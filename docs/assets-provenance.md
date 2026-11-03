# 随包素材来源与版本追溯清单

本文件是审查项 **Q22** 的可核实部分：把安装包里实际分发的模型与音频资产逐项列出，
给出**本仓库可以复现的指纹**（文件大小与 SHA-256），并明确区分
「已核实的来源声明」与「仍需维护者提供授权凭据的部分」。

它不替代 [ASSETS-LICENSE.md](../ASSETS-LICENSE.md) 与
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)：许可证条款以那两份为准，
这里只回答「这份字节是从哪来的、是不是当前发布的那一份」。

## 一、指纹（v1.6.4 安装包内，可复现）

以下数值来自构建产物 `build/dist-v1.6.4/小小领航员`，可用相同脚本重新计算。

| 资产 | 包内路径 | 大小（字节） | SHA-256 |
| --- | --- | ---: | --- |
| ASR 模型 SenseVoice int8 ONNX | `models/sense-voice/model.int8.onnx` | 237,115,547 | `12ca1a2ae7ecf3e0019ef2822307ee0b5cadc9196569e379b4c4026f8205276d` |
| SenseVoice 词表 | `models/sense-voice/tokens.txt` | 315,894 | `f449eb28dc567533d7fa59be34e2abca8784f771850c78a47fb731a31429a1dc` |
| Spotter 语音包索引 | `build/pack/Mandarin-CNTW-Xusheng_Chen/spmsg.txt` | 172,952 | `9885d8ef469f1ef14e5d7c7051c4f1fefb0d4f2dd39652bffd1a7eea8d1c4732` |
| 随包使用说明 | `使用说明.txt` | 4,766 | `854cd925ed0e419f9d641a9fcc9437a419c05046a9de9ab2c007bca824937777` |

目录级摘要（文件数 + 路径排序后的内容哈希汇总）：

| 目录 | 文件数 | 总大小（字节） | 目录摘要 SHA-256 |
| --- | ---: | ---: | --- |
| `models/sense-voice/` | 2 | 237,431,441 | `11e09ebc7b3e7032d1a3de69fef234da4cc1d565def8f39ac0b0f08b7efd210e` |
| `build/pack/Mandarin-CNTW-Xusheng_Chen/` | 2,497 | 207,343,828 | `66ca313308ceeea36e4f9fca163967efc098c3e787e526cee2d03d2a30e9b4d2` |

安装包本身的大小与 SHA-256 以对应 Release 页的 `SHA256SUMS.txt` 为准。

## 二、来源声明（已能在仓库内核实）

- **SenseVoice 模型**：本地识别由 sherpa-onnx 运行，模型文件取自发行版；
  许可证与再分发条件遵循模型提供方，见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。
  仓库内可核实项：文件指纹（上表）、打包路径、`models/sense-voice` 目录内容。
- **Spotter 语音包**：包内 `spmsg.txt` 头部自带版本与构建信息——
  `version: 1.4.1`、`build: 2026-10-15 09:26`、2496 条音频 / 738 个 msgId /
  35 个语速 / 24000 Hz。发布说明（[v1.6.0](releases/v1.6.0.md)）记录其源为
  「官方 1015 版」，并按新源重新生成。
- **助手补充语音**：由豆包小何 2.0 音色预生成（见 [ASSETS-LICENSE.md](../ASSETS-LICENSE.md)）；
  生成结果随包分发，不调用在线服务。
- **字体**：Public Sans、JetBrains Mono 以 SIL OFL 1.1 分发，安装包保留 OFL 文本。
- **品牌与赞助商标识**：IFP Wargame、MoonDrop、Drag-On XTT 由维护者提供原图，
  处理记录与去底提示词见 [assets/readme/source/PROVENANCE.md](../assets/readme/source/PROVENANCE.md)；
  商标权归各自权利人，不随代码许可证转授权。

## 三、仍需维护者提供的外部证据（Q22 未闭环部分）

以下内容**无法从本仓库推导**，因此没有写进上述声明，也不做推测：

1. **SenseVoice 上游精确版本**：模型来源的仓库/发行页地址、发布 tag 或 commit、
   以及该版本对应的许可证全文（当前只有「模型提供方许可」的表述，没有版本锚点）。
2. **语音服务的再分发授权**：豆包语音合成服务条款中，是否允许把生成音频随
   客户端软件分发、以及是否需要署名/限制用途的书面确认。
3. **Spotter 语音包授权凭据**：「官方 1015 版」的原始发布页、版本号与允许再分发的
   条款（是否允许打包进第三方安装程序）。
4. **品牌标识使用许可**：三位权利人（IFP Wargame / MoonDrop / Drag-On XTT）对
   「在开源项目 README 与安装包中展示其标识」的许可记录。

补齐上面四项后，本文件会升级为完整清单，并在
[ASSETS-LICENSE.md](../ASSETS-LICENSE.md) 与 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)
中引用具体版本锚点；在此之前，本项目的对外表述保持「来源已声明、授权凭据待补」。
