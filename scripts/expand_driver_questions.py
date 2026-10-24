"""可重复生成驾驶员问法库与随包速查表。"""
import pathlib,sys
import yaml
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'voice-assistant'))
from src.question_forms import GROUPS

path=ROOT/'voice-assistant/intents_examples.yaml'
data=yaml.safe_load(path.read_text(encoding='utf-8'))
data.setdefault('query_opponent',{'route':'state_layer','examples':[]})
for intent,entry in data.items():
    if intent=='query_opponent':continue
    moved=[q for q in entry.get('examples',[]) if any(w in q for w in ('前车','后车','第一名','领跑')) and any(w in q for w in ('圈速','上一圈','最佳圈'))]
    entry['examples']=[q for q in entry.get('examples',[]) if q not in moved]
    data['query_opponent']['examples'].extend(moved)
for intent,canonical,forms in GROUPS:
    entry=data.setdefault(intent,{'route':'state_layer','examples':[]})
    entry['examples']=list(dict.fromkeys(entry['examples']+[canonical]+forms))
path.write_text('# 驾驶员日常问法；受限整句归一与遥测语义检查并用\n'+yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
lines=['# 比赛常用问法','', '可加“请问”“目前”“帮我看看”等口语，不必照着念。一次询问一项数据。', '', '第一名指全场第一名；前后车指赛道前后邻车。普通圈速询问默认上一完整圈，最佳圈速请明确说“最佳”。胎温和磨损受 iRacing 遥测限制，不能当成实时胎面读数。缺少数据时会说明原因。', '', '| 查询内容 | 也可以这样问 |','|---|---|']
for _,canonical,forms in GROUPS:
    examples=[q for q in forms if q!=canonical]
    lines.append('| '+canonical+' | '+'；'.join(examples[:2])+' |')
(ROOT/'比赛常用问法.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('问法总数',sum(len(e.get('examples',[])) for e in data.values()),'意图',len(data),'问题分组',len(GROUPS))
