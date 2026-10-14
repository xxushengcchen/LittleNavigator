"""驾驶员常用说法到明确问题；只做受限的整句归一，不把关键词当作回答依据。"""
import re

GROUPS=[]
def add(intent, canonical, nouns='', forms=''):
    variants=set(forms.split('|')) if forms else set()
    variants.add(canonical)
    for noun in nouns.split('|') if nouns else []:
        for ending in ('多少','是多少','有多少','读数是多少','读数是','是','是什么','怎么样'):
            variants.add(noun+ending)
        variants.add(noun)
    GROUPS.append((intent,canonical,sorted(variants)))

add('query_fuel','目前油量是多少升','油量|剩余油量|燃油量|剩油|剩余有量','还有多少油|目前还有多少油|我还有多少油|油还剩几升|油箱里还有多少油|剩余燃油有几升|剩多少油啊|目前剩余有量读数时')
add('query_fuel','每圈油耗多少','每圈油耗|平均油耗|单圈油耗','一圈用多少油|一圈烧几升油|跑一圈要多少油|平均每圈消耗多少油')
add('query_fuel','油还能跑几圈','燃油续航圈数','这些油能跑几圈|目前的油还能撑几圈|油能坚持几圈|油还可以跑几圈|还能用油跑多少圈')
add('query_fuel','油还能跑多久','燃油剩余时间','这些油还能撑多长时间|油还能坚持多久|剩下的油能开几分钟')
add('query_fuel','油够跑完吗','','油还够不够|油能坚持到终点吗|这箱油能完赛吗|需要补油吗|油够不够用|要不要省油|燃油够到结束吗')
add('query_weather','气温多少','气温|空气温度|环境气温','外面现在几度|空气现在多少度|气温读数是')
add('query_weather','路面温度多少','路温|地温|赛道温度|路面温度','赛道现在多少度|地面温度多少|沥青温度多少')
add('query_weather','空气湿度多少','湿度|空气湿度|相对湿度')
add('query_weather','风速多少','风速','现在风有多大|目前风速是|风吹得多快')
add('query_weather','降雨强度多少','降雨强度|降水强度|目前的降雨强度','雨下得大吗|现在雨有多大|雨大吗')
add('query_weather','路面湿不湿','','赛道现在干还是湿|地面是干的吗|路面有积水吗|赛道干湿情况|赛道湿不湿')
add('query_weather','下雨了吗','','现在在下雨吗|现在有没有雨|外面还有雨吗|雨还在下吗')
add('query_laps','比赛还剩几圈','剩余圈数|比赛剩余圈数','还有几圈结束|距离结束还有几圈|比赛还有多少圈|还要跑多少圈|还剩下多少圈')
add('query_laps','比赛还剩多久','比赛剩余时间|剩余比赛时间','还有多少时间结束|比赛还有几分钟|距离比赛结束多久|离结束还有多长时间')
add('query_laps','现在是第几圈','当前圈数','现在跑到第几圈了|我们跑第几圈了|我跑到哪一圈了|这会是第几圈')
add('query_laps','已经完成几圈','已完成圈数|完成圈数','我们一共跑了多少圈|已经跑完几圈了|我跑完多少圈了')
add('query_laptime','我上一圈多少','上一圈圈速|上圈圈速|上一圈成绩|上圈成绩','刚才那圈跑了多少|我刚才一圈多快|上一圈跑多长时间|刚跑完那圈成绩|报个上圈成绩')
add('query_laptime','最佳圈速多少','最佳圈速|最快圈速|个人最佳圈速|本场最佳圈速|这场的最佳圈速|最佳圈数','最快的一圈是多少|我最快跑了多少|这场的最最佳圈数是什么|这场最快一圈|我最好的一圈成绩')
add('query_laptime','当前圈已经跑了多久','当前圈已用时间','这一圈已经跑多久了|这圈目前用了多少秒')
add('query_laptime','上一圈比最佳慢多少','','上一圈和最快圈差几秒|我上圈比最佳差多少|上圈比最好成绩慢多少|刚才这圈差最佳几秒')
add('query_position','现在第几名','名次|排名|当前排名','我排第几|我们排第几名|目前排在第几位|现在第几位|我在什么位置|现在排名第几')
add('query_position','同组第几名','组别排名|同组排名|组别名次','我在这个组里排第几|同组目前排几位|本组第几名')
for side,other in [('前','后') ,('后','前')]:
    add('query_gap',side+'车多远',side+'车差距|和'+side+'车的差距',
        '|'.join([f'我们现在距离{side}面的车多远',f'距离{side}车还有几秒',f'和{side}面那辆车差多少',f'{side}面的车离我多远',f'{side}面那台离我们有多远',f'我们与{side}车之间差几秒']))
    add('query_gap',side+'车差距在缩小吗','',f'{side}车有没有追近|与{side}车的差距在变小吗|{side}车差距什么趋势|{side}车的距离在拉大吗')
add('query_pit','维修区限速多少','维修区限速|维修通道限速|维修去限速','进站限速是多少|pit限速多少|维修通道能开多快|维修区速度上限多少')
add('query_pit','维修区服务开放吗','','现在允许进站吗|现在能进维修区吗|pit开了吗|进站服务开了吗')
add('query_pit','还剩几次快修','快修次数|剩余快修次数','还有快修吗|快速维修还能用几次')
add('query_pit','必修还剩多久','必修剩余时间','必须修的还要多久|强制维修还要多长时间')
add('query_pit','选修还剩多久','选修剩余时间','可选维修还有多久|选修还要修几分钟')
add('query_pit','维修还要多久','维修剩余时间','修车还要等多久|维修什么时候结束|还要修几分钟')
add('query_pit','拖车还剩多久','拖车剩余时间','拖车还要等多久|拖车还有多少时间')
add('query_pit','设置进站加油量多少','进站加油设定','这次进站设定加几升油|目前设置进站加多少油')
add('query_damage','事故点多少','事故点|事故点数','我有几个x了|现在几x|我现在多少事故点|有没有扣事故点|被扣了几分')
add('query_damage','车有没有损伤','车损','车有没有坏的地方|车子哪里坏了|车子还好吗|车辆损伤情况|车哪里撞坏了|车受伤了吗')
add('query_flags','现在什么旗','旗号|旗语','现在是什么旗子|目前什么旗号|赛会出什么旗|现在旗子什么颜色')
add('query_speed','我现在车速多少','车速|当前速度','我开到多少了|我们现在有多快|现在多少公里每小时|当前时速是多少')
add('query_engine','引擎水温多少','水温|发动机水温','水现在多少度')
add('query_engine','引擎油温多少','油温|发动机油温','机油温度多少')
add('query_engine','现在引擎转速多少','转速|引擎转速|发动机转速','发动机多少转了|现在每分钟多少转|rpm是多少')
add('query_engine','现在电压多少','电压|电瓶电压','电池电压多少伏|电瓶现在几伏')
add('query_engine','我现在是几档','挡位|档位','现在挂几挡|我在几档|当前是什么档|几挡了|现在是不是空挡|现在是不是倒挡')
add('query_car_settings','前轮刹车比例多少','刹车平衡|刹车分配|前轮制动比例','bb是多少|现在的bb多少|制动平衡多少|刹车前后怎么分配')
add('query_car_settings','防抱死现在几档','abs档位|防抱死档位','abs多少|abs几档|防抱死设定多少')
add('query_car_settings','循迹控制现在几档','tc档位|循迹控制档位','tc多少|tc几档|牵引力控制多少')
for wheel in ('左前','右前','左后','右后'):
    add('query_tyre',wheel+'冷胎设定气压多少',wheel+'冷胎压|'+wheel+'冷胎气压',wheel+'轮冷胎压力多少')
    add('query_tyre',wheel+'胎温多少',wheel+'胎温|'+wheel+'轮胎温度',wheel+'轮胎热不热|上次进站'+wheel+'温度多少')
    add('query_tyre',wheel+'胎磨损多少',wheel+'胎磨损|'+wheel+'胎面剩余比例',wheel+'轮胎还剩多少|上次进站'+wheel+'胎磨损程度')
for who,subject in [('LEADER','第一名'),('AHEAD','前车'),('BEHIND','后车')]:
    nouns=[subject+'圈速',subject+'的圈速',subject+'上圈圈速',subject+'上一圈成绩']
    forms=[subject+'上一圈多快',subject+'刚才一圈跑多少',subject+'什么圈速']
    if who=='LEADER': forms+=['当前第一名的全速是什么','第一名的全速是多少','领跑者的圈速多少','第一名上一圈是多少']
    add('query_opponent',subject+'上一圈圈速多少','|'.join(nouns),'|'.join(forms))
    add('query_opponent',subject+'最佳圈速多少',subject+'最佳圈速|'+subject+'最快圈速',subject+'最快一圈是多少')
add('query_opponent','第一名已经完成几圈','第一名完成圈数','第一名什么圈数|第一名的圈数是|领跑者已经跑了几圈')

# 口语回放补充：保留油量/圈数、已完成/剩余、挡位/ABS 档位的区别。
EXTRA={
    '油还能跑几圈':'还能撑多少圈|油还能撑多少圈|这点油还能跑多少圈',
    '油够跑完吗':'我的油够不够跑到结束|剩下的油够完赛吗|现在油够不够到终点',
    '已经完成几圈':'我已经跑完几圈|我们已经跑完多少圈|目前跑完了几圈|已完成多少圈',
    '我上一圈多少':'我刚刚那圈多少秒|我刚那圈多少秒|刚刚那圈用了多久|我上一圈用了多少秒|我刚跑完那一圈多快',
    '上一圈比最佳慢多少':'我现在比最快圈慢几秒|我上一圈比最佳慢几秒|上一圈距离最佳差多少',
    '防抱死现在几档':'abs现在几档|abs当前是多少|防抱死现在是多少|abs设置几档',
    '循迹控制现在几档':'tc现在几档|tc当前是多少|牵引力控制现在几档|循迹现在几档',
}
for subject in ('第一名','前车','后车'):
    EXTRA[subject+'最佳圈速多少']=subject+'的最快圈速是多少|'+subject+'的最佳圈速是多少|'+subject+'最快一圈跑了多久'
    EXTRA[subject+'上一圈圈速多少']=subject+'上一圈跑了多少|'+subject+'刚才一圈跑多快|'+subject+'现在圈速多少|'+subject+'的上一圈是多少秒'
for i,(intent,canonical,forms) in enumerate(GROUPS):
    GROUPS[i]=(intent,canonical,sorted(set(forms+EXTRA.get(canonical,'').split('|'))-{''}))

ALIASES={}
for intent,canonical,forms in GROUPS:
    for form in forms:
        previous=ALIASES.get(form)
        if previous and previous!=(intent,canonical):raise ValueError('重复问法：'+form)
        ALIASES[form]=(intent,canonical)
_PREFIX=re.compile(r'^(?:你好小小领航员|你好小领航员|小小领航员|小领航员|领航员|麻烦你|麻烦|请问|请|帮我查一下|帮我看一下|帮我看看|帮我看|我想知道|告诉我|报一下|报告一下|报个|目前的|当前的|目前|当前|现在|我们现在|我们目前|我现在|这场的)')
_SUFFIX=re.compile(r'(?:谢谢你|谢谢|麻烦了|一下|呗|呀|啊|呢)$')
def canonicalize(q):
    # 优先保留完整短语，包括“当前圈”以及问句中的对象、否定和比较。
    if q in ALIASES:return ALIASES[q][1]
    stripped=q
    for _ in range(6):
        new=_SUFFIX.sub('',_PREFIX.sub('',stripped))
        if new==stripped:break
        stripped=new
        if stripped in ALIASES:return ALIASES[stripped][1]
    return q
