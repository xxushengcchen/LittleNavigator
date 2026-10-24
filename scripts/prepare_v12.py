"""生成 v1.2 新增文案（六变体）与审阅稿，供 scripts/synthesize_telemetry.py 合成。

只写 content/ 与 audio-content/ 两个文件，不碰任何已有包。
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# ---- 整句家族：每条 6 个不同说法 ----
SENTENCES = {
    'AST2_PENALTY_SERVE': [                      # black：必须进站受罚
        '先按指示回來受罰。', '進站，把處罰處理掉。', '馬上回來受罰。',
        '◆ 搞什麼，回來受罰。', '按賽會指示回去受罰。', '◆ 你他媽的先回來把處罰清掉。',
    ],
    'AST2_PENALTY_NO_REASON': [                  # 不解释原因
        '詳細狀況我在跟賽事幹事確認。', '原因我不多說，先處理處罰。', '這件事我來跟賽會確認。',
        '我在跟幹事溝通，你照指示做。', '別問原因，先按指示受罰。', '狀況我處理，你專心跑。',
    ],
    'AST2_GIVEBACK_ADVANTAGE': [                 # furled：切彎後要減速還回優勢
        '減速，把優勢還回去。', '剛剛賺到的，還回去。', '優勢要還，現在放慢。',
        '慢下來，還掉剛才那點。', '這一下賺的，還回去。', '收油，把優勢還掉。',
    ],
    'AST2_DISQUALIFY_OUT': [                     # disqualify
        '資格沒了，把這圈跑完。', '被取消資格，跑完收工。', '取消資格生效，穩穩跑完。',
        '這場沒了，跑完剩下的。', '◆ 別再出事了，資格已經沒了。', '已經取消資格，跑完就好。',
    ],
    'AST2_NO_VALID_LAP': [                       # 完成圈數但沒有有效圈速
        '目前還沒有有效圈。', '這場還沒做出有效圈。', '還沒有算數的圈速。',
        '到現在都沒有有效圈。', '目前沒有有效成績。', '還沒做出有效的一圈。',
    ],
    'AST2_SETTING_ABSENT': [                     # 這台車沒有這項設定
        '你傻啦，這台車沒這個設定。', '哎唷，這台車就沒這個設定。', '這台車沒這項設定，不用找。',
        '哈哈，這台車沒這個可以調。', '這台車沒這個設定，換一個問。', '這台車不給調這個，沒這項設定。',
    ],
    'AST2_RUBBER_LOW': [
        '路面還沒什麼膠，抓地力一般。', '賽道橡膠還不多，小心點。', '目前膠量偏低，別太激進。',
        '賽道還沒磨開，抓地力有限。', '橡膠量還是很低。', '路面還新，膠還沒鋪上去。',
    ],
    'AST2_RUBBER_MODERATE': [
        '路面橡膠中等，夠用。', '膠量一般，正常跑。', '鋪膠程度中等。',
        '賽道橡膠差不多，可以正常跑。', '膠量中等，不算滑。', '橡膠狀態是中等。',
    ],
    'AST2_RUBBER_HIGH': [
        '路面已經鋪了不少膠，抓地力不錯。', '膠量偏高，可以放心推。', '賽道橡膠很好，抓地力夠。',
        '路面挺黏的，正常發揮。', '膠量上來了，跑起來會比較穩。', '橡膠量偏高。',
    ],
    'AST2_RUBBER_MAX': [
        '路面膠量很足，抓地力最好。', '賽道完全磨開，放心跑。', '膠量非常夠，可以全力跑。',
        '路面跑開了，抓地力是這場最好。', '橡膠鋪得很滿，抓地力到位。', '膠量非常充足。',
    ],
    'AST2_RUBBER_UNKNOWN': [
        '橡膠狀態我讀不到。', '賽道橡膠資料還沒拿到。', '這個狀態讀不出來，我不亂猜。',
        '膠量沒有有效讀數。', '賽道橡膠還沒讀到。', '這項讀不到，先專心跑。',
    ],
}

# ---- 拼接前綴家族：frag=True，配數字或其他片段用 ----
FRAGS = {
    'AST2_FUEL_USE_HOUR_LEAD': ['目前的實際油耗是', '現在的即時油耗是', '這邊讀到即時油耗是',
                                '現在的每小時油耗是', '現在燒油的速度是', '即時油耗讀數是'],
    'AST2_FUEL_VS_AVG_MORE_LEAD': ['比每圈平均多', '比平均油耗多', '比平均每圈多',
                                   '比平常每圈多', '比平均多燒', '跟平均比起來多'],
    'AST2_FUEL_VS_AVG_LESS_LEAD': ['比每圈平均少', '比平均油耗少', '比平均每圈少',
                                   '比平常每圈少', '比平均少燒', '跟平均比起來少'],
    # 離第 N 名：LEAD + 名次 + POSITION_END + MID + 秒數 + SPCC_GAPIS_PT2
    'AST2_GAP_TO_POSITION_LEAD': ['離第', '跟第', '跟全場第', '距離第', '離全場第', '和第'],
    'AST2_GAP_TO_POSITION_MID': ['名大約差', '名估計差', '名差不多差', '名還差',
                                 '名差了大概', '名大概差'],
    'AST2_GAP_TO_CLASS_LEADER_LEAD': ['離組別第一', '跟組別第一', '和組別第一',
                                      '距離組別第一', '跟同組第一', '離同組第一'],
    'AST2_GAP_TO_LEADER_LEAD': ['離第一名', '跟第一名', '和第一名',
                                '距離第一名', '跟全場第一', '離領先車'],
    'AST2_GAP_TO_POSITION_TAIL': ['大約差', '估計差', '差不多差', '還差', '差了大概', '大概差'],
    # 前車 / 後車是第幾名：LEAD + 名次 + AST2_UNIT_POSITION_END
    'AST2_AHEAD_POSITION_LEAD': ['前車目前第', '前面那台第', '前車在第', '前面那台目前第',
                                 '前車名次是第', '前面那台排第'],
    'AST2_BEHIND_POSITION_LEAD': ['後車目前第', '後面那台第', '後車在第', '後面那台目前第',
                                  '後車名次是第', '後面那台排第'],
    'AST2_LAP_MAXSPEED_LOSS_LEAD': ['本圈極速比上圈慢', '這圈最高速度比上圈慢', '極速比上圈掉了',
                                    '本圈極速少了', '最高速度比上圈慢', '這圈極速差'],
}

# ---- 補到 6 條的既有家族（原包已有 3 條，這裡補 3 條） ----
SUPPLEMENT = {
    'AST_REFUSE_HEARD': ['蛤？沒聽清楚，再說一遍。', '咦？剛剛沒聽清，再講一次。', '嗯？沒聽明白，再說一次。'],
    'AST_REFUSE_CANT': ['這項遙測裡沒有，我不亂編。', '這個我讀不到，不敢亂猜。', '這項查不到資料，我不亂講。'],
}

UNITS = {'AST2_UNIT_LITRE_PER_HOUR': ['公升每小時。']}

FRAG_INSTRUCT = ('用台灣腔年輕女生的語氣，清楚輕快地唸出這個拼接片段，'
                 '保留自然甜美音色，不要拖長音，不要自行添加文字')
URGENT_INSTRUCT = ('用台灣腔年輕女生的語氣，短促、平穩、帶一點嚴肅，'
                   '像賽事無線電那樣直接，不要拖長音，不要撒嬌')


def main() -> int:
    items = []
    for msg_id, variants in SENTENCES.items():
        item = {'msgId': msg_id, 'cat': 'assistant', 'prio': 'normal', 'variants': variants}
        if msg_id.startswith(('AST2_PENALTY', 'AST2_DISQUALIFY', 'AST2_GIVEBACK')):
            item['instruct'] = URGENT_INSTRUCT          # 處罰類不撒嬌
        items.append(item)
    for msg_id, variants in FRAGS.items():
        items.append({'msgId': msg_id, 'cat': 'assistant', 'prio': 'normal',
                      'frag': True, 'speechRate': 30, 'variants': variants,
                      'instruct': FRAG_INSTRUCT})
    for msg_id, variants in UNITS.items():
        items.append({'msgId': msg_id, 'cat': 'assistant', 'prio': 'normal',
                      'frag': True, 'speechRate': 30, 'variants': variants})
    # ② 層「專心比賽」話術：放進 telemetry 包即可，engine 會把多個包合併查找
    items.append({'msgId': 'AST_REFUSE_LATER', 'cat': 'assistant', 'prio': 'normal',
                  'variants': ['這個先別管，專心跑就好。', '先專心比賽，其他我幫你顧著。',
                               '專心開車，雜事交給我就好。', '這個之後再講，先專心跑。',
                               '別分心，這種小事我來想。', '先顧好比賽，其他事情我們處理。']})

    meta = {
        'name': '小小領航員 v1.2 補充：處罰、橡膠、油耗與名次差距',
        'voice': 'zh_female_xiaohe_uranus_bigtts', 'resource': 'seed-tts-2.0',
        'instructs': {'normal': '用台灣腔年輕女生超級撒嬌的語氣，聲音要嗲要甜，'
                                '語尾上揚帶一點鼻音，語速輕快有跳躍感，台灣國語咬字要明顯'},
    }
    out = ROOT / 'content' / 'assistant-telemetry-v1.2.json'
    out.write_text(json.dumps({'meta': meta, 'items': items}, ensure_ascii=False, indent=2),
                   encoding='utf-8')

    review = ['# v1.2 新增回覆（每類六種）', '',
              '數字與單位沿用原包拼裝；◆ 標記的是帶髒話的變體。', '']
    for item in items:
        review.append('## ' + item['msgId'])
        review.append('')
        review += [f'{i}. {v}' for i, v in enumerate(item['variants'], 1)]
        review.append('')
    (ROOT / 'voice-assistant' / 'audio-content' / 'v1.2新增回覆審閱.md').write_text(
        '\n'.join(review), encoding='utf-8')

    clip = sum(len(i['variants']) for i in items)
    print(f'msgId {len(items)} 個，音檔 {clip} 條')
    print('待辦：AST_REFUSE_HEARD / AST_REFUSE_CANT 各補 3 條變體需要重建 assistant-extra 包，'
          '本輪先不動（見 CHANGELOG 待辦）')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
