"""先生成每类六种回复模板，再交给原豆包合成/打包流程。"""
import json,pathlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
labels={
 'AIR_TEMP':'氣溫','TRACK_TEMP':'路面溫度','WIND':'風速','HUMIDITY':'空氣濕度',
 'PRECIP':'目前降雨強度','FUEL':'目前剩餘油量','FUEL_LAPS':'依目前油耗估計還能跑',
 'FUEL_USE':'每圈平均油耗','SPEED':'目前車速','INCIDENT':'目前事故點',
 'BEST_LAP':'這場的最佳圈速','CURRENT_LAP':'目前這圈已用時間','TIME_REMAIN':'這場剩餘時間',
 'WATER_TEMP':'引擎水溫','OIL_TEMP':'引擎油溫','REPAIR_REQUIRED':'必修項目剩餘時間',
 'REPAIR_OPTIONAL':'選修項目剩餘時間','REPAIR_TOTAL':'全部維修剩餘時間',
 'PIT_LIMIT':'維修區限速','FAST_REPAIR':'剩餘快修次數','BRAKE_BIAS':'前輪煞車比例',
 'ABS':'目前防鎖死檔位','TC':'目前循跡控制檔位','FUEL_ADD':'設定進站加油量',
 'TOW_TIME':'拖車剩餘時間',
 'FUEL_TIME':'按目前油耗和上圈圈速估計燃油還能支持的時間',
 'LAP_LOSS':'比最佳圈慢的時間','LAP_GAIN':'比先前最佳圈快的時間',
 'LAPS_COMPLETED':'已經完成的圈數',
 'LEADER_LAST_LAP':'全場第一名的上一圈圈速','LEADER_BEST_LAP':'全場第一名的最佳圈速','LEADER_LAPS':'全場第一名已完成圈數',
 'AHEAD_LAST_LAP':'賽道前車的上一圈圈速','AHEAD_BEST_LAP':'賽道前車的最佳圈速',
 'BEHIND_LAST_LAP':'賽道後車的上一圈圈速','BEHIND_BEST_LAP':'賽道後車的最佳圈速',
 'RPM':'目前引擎轉速','GEAR':'目前使用的前進檔位','VOLTAGE':'目前電壓',
}
units={'LITRE':'公升。','DEGREE':'度。','MPS':'公尺每秒。','KMH':'公里每小時。',
       'PERCENT':'百分之','POINT':'點。','TIMES':'次。','KPA':'千帕。','MINUS':'零下',
       'POSITION_END':'名。','LEVEL':'檔。','RPM':'轉每分鐘。','VOLT':'伏特。','TEN_THOUSAND':'萬'}
states={
 'GEAR_NEUTRAL':['現在是空檔喔。','目前掛著空檔。','這邊顯示空檔。','現在檔位是空檔。','你目前在空檔喔。','讀到的檔位是空檔。'],
 'GEAR_REVERSE':['現在是倒檔喔。','目前掛著倒檔。','這邊顯示倒檔。','現在檔位是倒檔。','你目前在倒檔喔。','讀到的檔位是倒檔。'],
 'IN_PIT':['現在已經在維修區裡囉。','你現在人在維修區裡喔。','已經進維修區了。','目前在維修區內。','這邊顯示已在維修區。','現在就在維修區裡啦。'],
 'PITS_OPEN':['現在可以進站接受服務。','維修區目前允許服務喔。','目前進站服務是開放的。','現在可以在維修格接受服務。','這邊顯示允許進站服務。','維修区服務現在開放喔。'],
 'PITS_CLOSED':['現在還不能接受進站服務。','維修區目前不允許服務喔。','進站服務現在沒開放。','現在維修區服務是關閉的。','這邊顯示還不允許進站服務。','目前不能在維修格接受服務。'],
 'FAST_UNLIMITED':['快修次數沒有上限。','目前快修可以不限次數。','這場快修次數不限。','快修目前沒有次數限制。','這邊顯示快修次數無限。','本場的快修次數沒有限制喔。'],
 'RED_FLAG':['現在是紅旗。','紅旗狀態，注意一下。','賽會現在出紅旗了。','目前紅旗喔。','這邊顯示紅旗狀態。','現在正在紅旗期間。'],
 'NO_FLAG':['目前沒有顯示旗號。','這邊沒有看到旗號。','現在沒顯示任何旗號喔。','目前旗號欄是空的。','現在沒有旗號訊息。','沒有讀到當前旗號喔。'],
 'OPTIONAL_REPAIR':['這邊有亮選修維修警示。','目前顯示有選修維修項目。','選修維修警示亮著喔。','目前有可選的維修項目。','系統顯示可以做選修維修。','這邊讀到選修維修警示。'],
 'DAMAGE_UNKNOWN':['沒有讀到具體車損資料，不能判斷部位。','車損部位這邊看不到，不能亂猜。','沒有具體車損讀數，事故點也不能當車損。','目前無法確認車損部位和程度。','這邊沒有可靠的車損細節。','車損細節沒有提供，現在沒辦法確認。'],
 'GAP_STABLE':['差距目前大致穩定。','現在差距沒有明顯改變。','目前看起來差距差不多。','這段時間差距算穩定喔。','差距沒有明顯增加或縮短。','這邊看到的差距暫時持平。'],
 'TYRE_UNAVAILABLE':['這邊沒有最近進站的輪胎檢測資料。','尚未拿到有效的輪胎檢測紀錄。','目前沒有可靠的輪胎歷史檢測值。','還沒收到可用的輪胎檢測資料。','沒有最近的輪胎檢測紀錄，不能猜。','這邊暫時拿不到有效輪胎檢測值。'],
}
items=[]
for code,label in labels.items():
    if code=='FUEL_LAPS':
        variants=['估計這些油還能跑','依目前油耗估計能跑','這邊算油還能跑','目前估計油量可跑','以平均油耗估算還能跑','油量估計還可以跑']
    else:
        variants=[f'{label}是',f'{label}現在是',f'現在的{label}是',f'這邊讀到{label}是',f'幫你確認，{label}是',f'{label}讀數是']
    items.append({'msgId':f'AST2_{code}_LEAD','cat':'assistant','prio':'normal','frag':True,'speechRate':30,'variants':variants})
for code,label in [('POSITION','名次'),('CLASS_POSITION','組別名次'),('LAP_NUMBER','圈數')]:
    items.append({'msgId':f'AST2_{code}_LEAD','cat':'assistant','prio':'normal','frag':True,'speechRate':30,
                  'variants':[f'目前{label}是第',f'現在{label}排第',f'你目前的{label}是第',f'這邊顯示{label}第',f'{label}目前在第',f'現在你是{label}第']})
for code,label in [('GAP_AHEAD','和前車'),('GAP_BEHIND','和後車')]:
    items.append({'msgId':f'AST2_{code}_EST_LEAD','cat':'assistant','prio':'normal','frag':True,'speechRate':30,
                  'variants':[f'估計{label}差',f'這邊估算{label}差',f'目前估計{label}差',f'按現在位置估算{label}差',f'{label}的估計差距是',f'{label}大約差']})
for wheel,name in [('LF','左前'),('RF','右前'),('LR','左後'),('RR','右後')]:
    for kind,label in [('TEMP','胎溫'),('WEAR','胎面剩餘比例'),('COLD_PRESSURE','冷胎設定氣壓')]:
        prefix=(f'上次進站量到{name}{label}' if kind!='COLD_PRESSURE' else f'{name}{label}')
        items.append({'msgId':f'AST2_{wheel}_{kind}_LEAD','cat':'assistant','prio':'normal','frag':True,'speechRate':30,
                      'variants':[f'{prefix}是',f'{prefix}讀數是',f'這邊記錄的{prefix}是',f'幫你查到{prefix}是',f'目前可讀的{prefix}是',f'{prefix}的資料是']})
for code,text in units.items():items.append({'msgId':f'AST2_UNIT_{code}','cat':'assistant','prio':'normal','frag':True,'speechRate':30,'variants':[text]})
for code,variants in states.items():items.append({'msgId':f'AST2_{code}','cat':'assistant','prio':'normal','variants':variants})
meta={'name':'小小領航員日常遙測補充','voice':'zh_female_xiaohe_uranus_bigtts','resource':'seed-tts-2.0',
      'instructs':{'normal':'用台灣腔年輕女生超級撒嬌的語氣，聲音要嗲要甜，語尾上揚帶一點鼻音，語速輕快有跳躍感，台灣國語咬字要明顯，像在跟男朋友撒嬌那樣'}}
for item in items:
    if item.get('frag'):item['instruct']='用台灣腔年輕女生的語氣，清楚輕快地唸出這個拼接片段，保留自然甜美音色，不要拖長音，不要自行添加文字'
    if len(item['variants'])>1:assert len(item['variants'])==len(set(item['variants']))==6
path=ROOT/'content'/'assistant-telemetry-v1.1.json'
path.write_text(json.dumps({'meta':meta,'items':items},ensure_ascii=False,indent=2),encoding='utf-8')
review=['# 日常遥测回复模板（每类六种）','','数值在运行时用原数字音频拼入。单位是共享连接件，不重复录六次；每类事实回答有六个不同前缀或完整回复。','']
for item in items:
    if len(item['variants'])==6:
        review+=['## '+item['msgId'],'']+[f'{i}. {text}'+('〈遥测数值〉〈单位〉' if item.get('frag') else '') for i,text in enumerate(item['variants'],1)]+['']
(ROOT/'voice-assistant'/'audio-content'/'六变体回复审阅.md').write_text('\n'.join(review),encoding='utf-8')
print('reply families',sum(len(i['variants'])==6 for i in items),'new audio',sum(len(i['variants']) for i in items))
