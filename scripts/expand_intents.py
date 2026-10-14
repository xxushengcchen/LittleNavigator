"""扩展日常只读问法；不添加分析、操作或预测请求。"""
import pathlib,yaml
ROOT=pathlib.Path(__file__).resolve().parents[1]
path=ROOT/'voice-assistant/intents_examples.yaml'
data=yaml.safe_load(path.read_text(encoding='utf-8'))
extra={
 'query_weather':['空气湿度多少','现在湿度是多少','路面温度多少','目前的风速多少','降雨强度多少','降水百分比多少'],
 'query_fuel':['平均每圈油耗多少','这些油还能跑多久','目前油量是多少升','燃油剩余时间多久','按油耗估计能跑几圈'],
 'query_position':['我在组别第几名','同组名次是多少','现在组别排名多少','我在同组排第几'],
 'query_laps':['现在是第几圈','当前跑到第几圈','已经完成几圈','跑了几圈了','比赛还剩多少时间'],
 'query_laptime':['上一圈比最佳慢多少','上圈比最快差多少','我上圈差最佳几秒','当前圈已经跑了多久'],
 'query_pit':['维修区限速多少','维修区服务开了吗','还剩几次快修','快修还有多少次','必修还剩多久','选修还剩多久','维修还要多久','设置进站加油量多少'],
 'query_tyre':['左前冷胎设定气压多少','右前冷胎设定气压多少','左后冷胎设定气压多少','右后冷胎设定气压多少','上次进站左前胎面还剩多少','上次进站右前胎温多少'],
 'query_speed':['我现在车速多少','当前速度是多少','车速有多少','现在开到多少公里','目前车速几公里','速度是多少公里每小时','我车速有多快','现在的速度多少','这会儿车速是多少','现在跑多快'],
 'query_engine':['现在引擎转速多少','发动机转速是多少','目前转速多少转','电瓶电压是多少','现在电压多少','当前挂在几挡','我现在是几档','目前是空挡吗','现在是不是倒挡','引擎水温多少','发动机水温多少','水温是多少','目前水温几度','现在水温高不高','引擎油温多少','发动机油温多少','油温是多少','目前油温几度','现在油温高不高'],
 'query_car_settings':['前轮刹车比例多少','刹车前后比例多少','制动分配是多少','前轮制动百分比多少','防抱死现在几档','防锁死设在几档','防抱死档位多少','循迹控制现在几档','牵引力控制几档','循迹档位多少'],
}
for intent,examples in extra.items():
    spec=data.setdefault(intent,{'route':'state_layer','examples':[]})
    for phrase in examples:
        if phrase not in spec['examples']:spec['examples'].append(phrase)
path.write_text('# 日常只读遥测问法；修改后请运行 tools/check_library.py\n'+yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
print('short phrases',sum(len(spec.get('examples',[])) for spec in data.values() if isinstance(spec,dict)))
