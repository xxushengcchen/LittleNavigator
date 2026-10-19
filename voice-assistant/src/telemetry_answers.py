"""有可靠数据的新问答；动态数值用原数字语音，新回复各六变体。"""
import math
from . import numbers
from .router import Decision

def refuse(reason,prompt=None):return Decision([],reason,prompt)
def state(code,reason):return Decision(['AST2_'+code],reason)
def valid(value,lo=0,hi=9999):return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and lo<=value<=hi
def integer_parts(value):
    if value<=9999:return numbers.int_to_msgids(value)
    tens,remainder=divmod(value,10000)
    return numbers.int_to_msgids(tens)+['AST2_UNIT_TEN_THOUSAND']+(['SPCC_MID_ZERO'] if 0<remainder<1000 else [])+(numbers.int_to_msgids(remainder) if remainder else [])
def duration(value):
    if not valid(value,0,604799):raise ValueError('无有效剩余时间')
    minutes,seconds=divmod(int(math.ceil(value)),60)
    return (integer_parts(minutes)+['SPCC_MINUTE'] if minutes else [])+numbers.int_to_msgids(seconds)+['SPCC_SECOND']
def numeric(code,value,unit=None,digits=1,lo=0,hi=9999,percent=False,integer=False):
    if not valid(value,lo,hi):return refuse('缺少有效的'+code+'遥测读数')
    signed=[]
    if value<0:signed=['AST2_UNIT_MINUS'];value=abs(value)
    if integer and (not float(value).is_integer()):return refuse('遥测整数读数无效')
    plan=['AST2_'+code+'_LEAD',*signed]
    if percent:plan+=['AST2_UNIT_PERCENT']
    plan+=integer_parts(int(value)) if integer else numbers.decimal_to_msgids(value,digits)
    if unit:plan+=['AST2_UNIT_'+unit]
    return Decision(plan,f'{code} = {value}，使用对应数值与单位')
def timed(code,value):
    if not valid(value,0,604799):return refuse('没有有效的时间读数')
    return Decision(['AST2_'+code+'_LEAD',*duration(value)],f'{code} = {value:.1f}s')

_CN={'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
def _cn_num(t):
    """中文或阿拉伯数字的名次。"""
    if t.isdigit():return int(t)
    try:
        if t=='十':return 10
        if t.startswith('十'):return 10+_CN[t[1]]
        if '十' in t:
            a,b=t.split('十');return _CN[a]*10+(_CN[b] if b else 0)
        return _CN[t]
    except (KeyError,IndexError):return None

def _interval(s,idx):
    """我与指定车辆索引的赛道间隔（秒）；跨线补一个圈周期，算法与前后车一致。"""
    me,dist,times=s.car_idx,s.car_lap_dist,s.car_est_times
    if me is None or not dist or not times:return None
    if not (0<=me<len(dist) and 0<=idx<len(dist)):return None
    if any(v is None or v<0 for v in (dist[me],dist[idx])):return None
    if times[me] is None or times[idx] is None:return None
    dd=(dist[idx]-dist[me])%1
    if dd<=0 or dd>0.5:return None
    gap=times[idx]-times[me]
    if dist[idx]<dist[me]:
        period=s.lap_best if s.lap_best and s.lap_best>0 else s.lap_last
        if not period or period<=0:return None
        gap+=period
    return gap if math.isfinite(gap) and 0<=gap<=300 else None

def _rubber(s):
    """SessionTrackRubberState 是英文串，按关键字分档；档位集齐后可再收紧。"""
    v=(s.track_rubber or '').strip()
    low=v.lower()
    if not low:return state('RUBBER_UNKNOWN','没有读到赛道橡胶状态')
    if 'moderate' in low:return state('RUBBER_MODERATE',f'橡胶状态 {v}')
    if any(w in low for w in ('low','little','light','new')):return state('RUBBER_LOW',f'橡胶状态 {v}')
    if any(w in low for w in ('max','extreme','very','extensive','heavy')):return state('RUBBER_MAX',f'橡胶状态 {v}')
    if 'high' in low:return state('RUBBER_HIGH',f'橡胶状态 {v}')
    return state('RUBBER_UNKNOWN',f'橡胶状态 {v} 未识别')

def answer(intent,s,q):
    if intent=='query_opponent':
        if any(w in q for w in ('这圈','当前圈','差多少','快多少','慢多少','剩几圈','还剩')):
            return refuse('对手目前支持上一完整圈、最佳圈及第一名已完成圈数，请明确查询项目')
        if any(w in q for w in ('组别','同组','第二','第三','p2','p3')):
            return refuse('当前可查询全场第一名或赛道前后邻车，请明确目标')
        targets=[(code,idx) for code,word,idx in [('AHEAD','前车',s.gap_ahead_idx),('BEHIND','后车',s.gap_behind_idx)] if word in q]
        if '第一名' in q or '领跑' in q:
            leaders=[i for i,pos in enumerate(s.car_positions or []) if pos==1 and i not in s.pace_car_indices]
            targets.append(('LEADER',leaders[0] if len(leaders)==1 else None))
        if len(targets)!=1 or targets[0][1] is None:return refuse('没有唯一且有效的目标车辆')
        code,idx=targets[0]
        completed=any(w in q for w in ('完成','圈数')) and '圈速' not in q
        best=any(w in q for w in ('最佳','最快'))
        values=s.car_completed_laps if completed else (s.car_best_laps if best else s.car_last_laps)
        value=values[idx] if values is not None and 0<=idx<len(values) else None
        if completed:
            if code!='LEADER':return refuse('邻车完成圈数尚未提供')
            result=numeric('LEADER_LAPS',value,integer=True)
            if result.ok:result.plan+=['AST_LAPS_TAIL']
            return result
        if not valid(value,.001,36000):return refuse('目标车辆尚无有效的完整圈速')
        kind='BEST_LAP' if best else 'LAST_LAP'
        return Decision(['AST2_'+code+'_'+kind+'_LEAD',*numbers.laptime_to_msgids(value)],f'{code} 车辆索引 {idx}，{kind} {value:.3f}s')
    if intent=='query_weather':
        if any(w in q for w in ('橡胶','鋪膠','铺胶','使用率','胶量','膠量','rubber')):
            return _rubber(s)
        if any(w in q for w in ('会','什么时候','预测','雨胎','要不要')):return refuse('当前遥测不提供未来天气或换胎建议')
        if any(w in q for w in ('地温','路温','赛道温','路面温')):return numeric('TRACK_TEMP',s.track_temp,'DEGREE',lo=-60,hi=100)
        if any(w in q for w in ('气温','外面多少度','温度')):return numeric('AIR_TEMP',s.air_temp,'DEGREE',lo=-60,hi=70)
        if '风向' in q:return refuse('尚未校验风向角度与地理方向的对应关系')
        if '风' in q:return numeric('WIND',s.wind_vel,'MPS',hi=100)
        if '湿度' in q:return numeric('HUMIDITY',s.relative_humidity*100 if valid(s.relative_humidity,0,1) else None,percent=True,hi=100)
        if any(w in q for w in ('雨大','雨量','降水','降雨强度')):return numeric('PRECIP',s.precip*100 if valid(s.precip,0,1) else None,percent=True,hi=100)
    if intent=='query_gap':
        from .router import asks_lap_gap
        if asks_lap_gap(q):
            return refuse('没有可靠的前后车圈差数据，不能用秒差代替圈差')
        # 「前车/后车是第几名」会被 L1 关键词送进差距意图，这里先转派成名次回答
        if any(w in q for w in ('第几','名次','排名')) and any(w in q for w in ('前车','前車','后车','後車')):
            ahead=any(w in q for w in ('前车','前車'))
            lead='AST2_AHEAD_POSITION_LEAD' if ahead else 'AST2_BEHIND_POSITION_LEAD'
            target=s.gap_ahead_idx if ahead else s.gap_behind_idx
            pos=s.car_positions[target] if (target is not None and s.car_positions
                                           and 0<=target<len(s.car_positions)) else None
            if not valid(pos,1,999):return refuse('那台车还没有有效名次')
            return Decision([lead,*numbers.int_to_msgids(int(pos)),'AST2_UNIT_POSITION_END'],
                            f'对手名次 {pos}')
        import re as _re
        m=_re.search(r'第\s*([0-9]+|[一二三四五六七八九十]+)\s*名',q)
        head=None;idx=None;n=None
        if any(w in q for w in ('组别第一','同组第一','分组第一','組別第一','同組第一')):
            if s.car_class_positions and s.car_idx is not None:
                idx=next((i for i,p in enumerate(s.car_class_positions)
                          if p==1 and i!=s.car_idx and i not in s.pace_car_indices),None)
            head='AST2_GAP_TO_CLASS_LEADER_LEAD'
        elif any(w in q for w in ('第一名','全场第一','領先','领先')):
            if s.car_positions and s.car_idx is not None:
                idx=next((i for i,p in enumerate(s.car_positions)
                          if p==1 and i!=s.car_idx and i not in s.pace_car_indices),None)
            head='AST2_GAP_TO_LEADER_LEAD'
        elif m:
            n=_cn_num(m.group(1))
            if n and s.car_positions and s.car_idx is not None:
                idx=next((i for i,p in enumerate(s.car_positions)
                          if p==n and i!=s.car_idx),None)
        if idx is None:
            if head or m:return refuse('没有找到那台车，或它还没有有效位置')
        else:
            gap=_interval(s,idx)
            if gap is None:return refuse('读不到与那台车的有效间隔，不能拿别人代替')
            if head:
                return Decision([head,'AST2_GAP_TO_POSITION_TAIL',
                                 *numbers.decimal_to_msgids(gap,1),'SPCC_GAPIS_PT2'],
                                f'与目标车 {gap:.1f}s')
            return Decision(['AST2_GAP_TO_POSITION_LEAD',*numbers.int_to_msgids(n),
                             'AST2_GAP_TO_POSITION_MID',
                             *numbers.decimal_to_msgids(gap,1),'SPCC_GAPIS_PT2'],
                            f'离第 {n} 名 {gap:.1f}s')
    if intent=='query_fuel':
        if '多久' in q:
            if not valid(s.laps_of_fuel,0,999) or not valid(s.lap_last,.001,36000):return refuse('尚无完整圈油耗及有效上一圈计时，无法估计燃油时间')
            return timed('FUEL_TIME',s.laps_of_fuel*s.lap_last)
        if '油耗' in q:
            if any(w in q for w in ('实际','即时','实时','现在的油耗','当前的油耗')):
                hour=s.fuel_use_per_hour
                if not valid(hour,0.1,500):return refuse('没有读到当前油耗')
                if any(w in q for w in ('差','差多少','差别')):
                    if not valid(s.fuel_per_lap,0.01,100) or not valid(s.lap_last,.001,36000):
                        return refuse('要比较每圈油耗，需要先有一个完整圈的圈速与油耗')
                    now=hour*s.lap_last/3600
                    delta=now-s.fuel_per_lap
                    lead='AST2_FUEL_VS_AVG_MORE_LEAD' if delta>=0 else 'AST2_FUEL_VS_AVG_LESS_LEAD'
                    return Decision([lead,*numbers.decimal_to_msgids(abs(delta),2),'AST2_UNIT_LITRE'],
                                    f'本圈即时 {now:.2f} 对平均 {s.fuel_per_lap:.2f} L/圈')
                return Decision(['AST2_FUEL_USE_HOUR_LEAD',*numbers.decimal_to_msgids(hour,1),
                                 'AST2_UNIT_LITRE_PER_HOUR'],f'即时油耗 {hour:.1f} L/h')
            return numeric('FUEL_USE',s.fuel_per_lap,'LITRE',digits=2,hi=100)
        if any(w in q for w in ('几圈','多少圈','跑多久','撑几','再撑','够跑')) and not '跑完' in q:
            result=numeric('FUEL_LAPS',s.laps_of_fuel,hi=999)
            if result.ok:result.plan+=['AST_LAPS_TAIL']
            return result
        if any(w in q for w in ('多少','几升','油量','剩油')):return numeric('FUEL',s.fuel_level,'LITRE',hi=1000)
        if s.laps_remain is None and valid(s.laps_of_fuel,0,999):
            result=numeric('FUEL_LAPS',s.laps_of_fuel);result.plan+=['AST_LAPS_TAIL']
            if valid(s.time_remain_s,0,604799):result.plan+=timed('TIME_REMAIN',s.time_remain_s).plan
            result.reason='按时间结束的比赛：分别播报估计燃油圈数与剩余时间，不猜能否跑完'
            return result
    if intent=='query_laps':
        if any(w in q for w in ('雨','天气','湿','放晴')):
            return refuse('这是天气问题，当前不提供降雨预测')
        if any(w in q for w in ('第几圈','当前第','这第','现在第')):
            result=numeric('LAP_NUMBER',s.lap,integer=True,lo=1)
            if result.ok:result.plan+=['AST_LAPS_TAIL']
            return result
        if any(w in q for w in ('完成几圈','完成了','跑了几圈','已跑','跑完多少')):
            result=numeric('LAPS_COMPLETED',s.lap_completed,integer=True)
            if result.ok:result.plan+=['AST_LAPS_TAIL']
            return result
        if s.laps_remain is None or any(w in q for w in ('时间','多久','分钟','几分')):return timed('TIME_REMAIN',s.time_remain_s)
    if intent=='query_position':
        lead=target=None
        if any(w in q for w in ('前车','前面','前車')):lead,target='AST2_AHEAD_POSITION_LEAD',s.gap_ahead_idx
        elif any(w in q for w in ('后车','后面','後車')):lead,target='AST2_BEHIND_POSITION_LEAD',s.gap_behind_idx
        if lead:
            pos=s.car_positions[target] if (target is not None and s.car_positions
                                            and 0<=target<len(s.car_positions)) else None
            if not valid(pos,1,999):return refuse('那台车还没有有效名次')
            return Decision([lead,*numbers.int_to_msgids(int(pos)),'AST2_UNIT_POSITION_END'],
                            f'对手名次 {pos}')
        if any(w in q for w in ('对手','第一名','领跑')):
            return refuse('这个目标的名次还没支持，可以说前车或后车')
        if any(w in q for w in ('组别','同组','组里','分组')):return numeric('CLASS_POSITION',s.class_position,'POSITION_END',integer=True,lo=1)
        if valid(s.position,6,999):return numeric('POSITION',s.position,'POSITION_END',integer=True,lo=1)
    if intent=='query_laptime':
        if (s.lap_completed is not None and s.lap_completed>=1
                and not valid(s.lap_last,.001,36000) and not valid(s.lap_best,.001,36000)
                and not any(w in q for w in ('当前圈','这一圈','这圈已'))):
            return state('NO_VALID_LAP','跑完了圈但没有有效成绩，不能说成没有数据')
        if (any(w in q for w in ('极速','最高速','最快速度','最高速度'))
                and not any(w in q for w in ('圈速','上一圈','上圈','最佳','最快圈','当前圈'))):
            return refuse('单圈最高速度还没有采集，不能拿当前速度或当前圈时间代替')
        if any(w in q for w in ('前车','后车','对手','前面','后面','第一名','领跑')):return refuse('当前圈速问答仅读取自己的车辆')
        if any(w in q for w in ('慢多少','快多少','差多少','差几秒')):
            if not valid(s.lap_last,.001,36000) or not valid(s.lap_best,.001,36000):return refuse('需要有效上一圈和最佳圈才能比较')
            delta=s.lap_last-s.lap_best
            result=numeric('LAP_LOSS' if delta>=0 else 'LAP_GAIN',abs(delta),digits=2)
            result.plan+=['SPCC_SECOND']
            return result
        if any(w in q for w in ('最快','最佳')):
            if not valid(s.lap_best,.001,36000):return refuse('尚无有效最佳圈')
            return Decision(['AST2_BEST_LAP_LEAD',*numbers.laptime_to_msgids(s.lap_best)],f'最佳圈 {s.lap_best:.3f}s')
        if any(w in q for w in ('这圈','当前圈')):
            if not valid(s.lap_current,.001,36000):return refuse('当前圈计时尚未开始')
            return Decision(['AST2_CURRENT_LAP_LEAD',*numbers.laptime_to_msgids(s.lap_current)],'当前圈的已用时间，不是最终成绩')
    if intent=='query_damage':
        if any(w in q for w in ('事故','扣分','几个点','几次')):return numeric('INCIDENT',s.incident_count,'POINT',integer=True)
        if 'repair' in s.flags or s.engine_warnings is not None and s.engine_warnings&0x80:return Decision(['SPCC_REPAIR'],'SDK 维修旗或必修警示')
        if s.engine_warnings is not None and s.engine_warnings&0x100:return state('OPTIONAL_REPAIR','SDK 选修警示；不推断具体部位')
        return refuse('没有可靠的车损部位数据；事故点不能替代车损','AST2_DAMAGE_UNKNOWN')
    if intent=='query_pit':
        if '拖车' in q:
            return timed('TOW_TIME',s.tow_time_s) if valid(s.tow_time_s,.001,604799) else refuse('目前没有有效拖车剩余计时')
        if any(w in q for w in ('限速','限多少','几公里')):return numeric('PIT_LIMIT',s.pit_speed_limit_kph,'KMH',hi=300)
        if any(w in q for w in ('快修','快速维修')):
            if s.fast_repairs_available==255:return state('FAST_UNLIMITED','SDK 255 表示快修无限')
            return numeric('FAST_REPAIR',s.fast_repairs_available,'TIMES',integer=True,hi=254)
        if any(w in q for w in ('修多久','维修还','必修','选修','修车时间','多久修')):
            if not s.pitstop_active:return refuse('维修尚未进行，维修剩余计时无效')
            if '必修' in q:return timed('REPAIR_REQUIRED',s.repair_required_s)
            if '选修' in q:return timed('REPAIR_OPTIONAL',s.repair_optional_s)
            total=s.repair_required_s+s.repair_optional_s if valid(s.repair_required_s) and valid(s.repair_optional_s) else None
            return timed('REPAIR_TOTAL',total)
        if any(w in q for w in ('开','开放','关闭','能进','允许')):
            if s.pits_open is None:return refuse('未读到维修区服务权限')
            return state('PITS_OPEN' if s.pits_open else 'PITS_CLOSED','SDK PitsOpen：允许接受维修区服务')
        if any(w in q for w in ('加多少','加几','设定加油','设置加油')):return numeric('FUEL_ADD',s.pit_fuel_add,'LITRE',hi=1000)
        if s.on_pit_road:return state('IN_PIT','当前已在维修区内')
    if intent=='query_flags':
        if not s.session_flags_known:return refuse('没有有效旗号读数，不能将缺字段当作无旗号')
        if 'red' in s.flags and not set(s.flags)&{'disqualify','repair'}:return state('RED_FLAG','SDK 红旗')
        if not s.flags and not s.session_flags & ~(0xF0000000|0x40000):return state('NO_FLAG','SDK 当前无旗号；灯光/服务许可不是旗号')
    if intent=='query_speed':
        if any(w in q for w in ('最高','极速','最快')):
            return refuse('单圈或本场最高速度还没有采集，不能拿当前速度代替')
        return numeric('SPEED',s.speed_kph,'KMH',hi=600)
    if intent=='query_engine':
        if any(w in q for w in ('abs','tc','防抱死','牵引','循迹')):return refuse('车辆辅助设置不能用当前挡位代替')
        if any(w in q for w in ('模式','map','混合比','增压','boost')):return refuse('引擎模式与燃油混合比尚未采集，不能拿变速箱挡位代替')
        if any(w in q for w in ('转速','rpm')):return numeric('RPM',round(s.rpm) if valid(s.rpm,0,30000) else None,'RPM',integer=True,hi=30000)
        if '电压' in q:return numeric('VOLTAGE',s.voltage,'VOLT',hi=100)
        if any(w in q for w in ('档','挡','gear')):
            if s.gear==0:return state('GEAR_NEUTRAL','SDK Gear = 0')
            if s.gear==-1:return state('GEAR_REVERSE','SDK Gear = -1')
            return numeric('GEAR',s.gear,'LEVEL',integer=True,lo=1,hi=20)
        if '油温' in q:return numeric('OIL_TEMP',s.oil_temp,'DEGREE',lo=-50,hi=250)
        if '水温' in q:return numeric('WATER_TEMP',s.water_temp,'DEGREE',lo=-50,hi=200)
        return refuse('请明确询问引擎水温、油温、转速、电压或挡位')
    if intent=='query_car_settings':
        if any(w in q for w in ('刹车','煞车','制动','前后比例')):return numeric('BRAKE_BIAS',s.brake_bias,percent=True,hi=100)
        if any(w in q for w in ('防抱死','防锁死','abs')):return numeric('ABS',s.abs_level,'LEVEL',integer=True,hi=30)
        if any(w in q for w in ('循迹','牵引','tc')):return numeric('TC',s.tc_level,'LEVEL',integer=True,hi=30)
        # 防倾杆 / 尾翼在 session YAML 的 CarSetup 里（实测：MX-5 是 AntiRollBar，保时捷是 ArbSetting），
        # 值可能是字符串也可能是数字；先把"这台车有没有"分开说清楚。
        if any(w in q for w in ('防倾','倾杆','arb')):
            value=s.setup_value('ArbSetting','AntiRollBar')
            return refuse(f'读到防倾杆设定是 {value}，但还没有对应语音' if value is not None else '这台车的调教数据里没有防倾杆')
        if any(w in q for w in ('尾翼','wing')):
            value=s.setup_value('WingAngle','Wing')
            return refuse(f'读到尾翼设定是 {value}，但还没有对应语音' if value is not None else '这台车的调教数据里没有尾翼')
        return refuse('这项车内设置尚未提供')
    if intent=='query_tyre':
        wheels=[w for w,words in [('LF',('左前',)),('RF',('右前',)),('LR',('左后',)),('RR',('右后',))] if any(word in q for word in words)]
        if len(wheels)!=1:return refuse('请说明左前、右前、左后或右后，并询问温度、冷胎气压或磨损')
        wheel=wheels[0]
        if any(w in q for w in ('冷胎','设定气压','设置气压')):
            if any(w in q for w in ('气压','胎压','压力')):
                return numeric(wheel+'_COLD_PRESSURE',s.tyre_cold_pressure.get(wheel),'KPA',hi=500)
            return refuse('冷胎设定仅提供气压，未提供冷胎温度')
        if any(w in q for w in ('气压','胎压')):return refuse('实时热胎气压未提供；可以询问冷胎设定气压')
        history=s.tyre_history.get(wheel,{})
        if not history:return refuse('没有观察到本次会话有效的进站轮胎检测','AST2_TYRE_UNAVAILABLE')
        if any(w in q for w in ('胎温','温度','过热')):return numeric(wheel+'_TEMP',history.get('temp'),'DEGREE',hi=250)
        if any(w in q for w in ('磨损','剩多少','剩余','胎面')):
            return numeric(wheel+'_WEAR',history.get('wear')*100 if valid(history.get('wear'),0,1) else None,percent=True,hi=100)
        return refuse('请具体询问胎温、冷胎气压或胎面剩余比例')
    return None
