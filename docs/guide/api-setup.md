# AI 与语音 API 接入

面向 v1.6.0。先按 [首页完整步骤](../../README.md#获取-api以-deepseek-和豆包语音为例) 申请 DeepSeek 与豆包语音密钥，再分别保存模型与语音设置。官方网页和参数核对日期：2026-10-20。

## 三个环节分别做什么

| 环节 | 当前实现 | 是否需要外部 Key |
| --- | --- | --- |
| 听懂语音、日常事实问答 | 本地识别、规则与录音播报 | 不需要 |
| AI 文字分析 | 兼容 Chat Completions 的模型接口；首页以 DeepSeek 为例 | 模型服务商的 Key |
| 把 AI 回答念出来 | 独立 TTS 适配器；首页以豆包语音为例 | 语音服务商的 Key |

配置模型后仍需单独配置 TTS，才会为 AI 答案合成语音。规则录音不依赖 TTS。模型、语音、分析键三组分别保存，测试按钮使用已保存配置。

## 参数怎样对应代码

模型接口地址对应 `ai.base_url`，模型名对应 `ai.model`，模型 Key 对应 `ai.api_key`。软件在基础地址后补 `/chat/completions`，发送 Bearer 鉴权的非流式请求。v1.6.0 起，保存接口地址与 Key 后可以拉取服务商返回的模型列表并选择；拉取失败时可手动填写模型名。旧版 DeepSeek 预设可能包含旧模型名，按首页提示手动修改。

豆包模式对应 `ai.tts.adapter: doubao`，音色 ID 对应 `ai.tts.speaker`，语音 Key 对应 `ai.tts.api_key`。软件内置 HTTP 单向流式地址 `https://openspeech.bytedance.com/api/v3/tts/unidirectional`，使用 `X-Api-Key` 和 `X-Api-Resource-Id` 鉴权；默认资源 `seed-tts-2.0`。当前界面不提供资源 ID 选择，因此首页步骤使用语音合成 2.0 公版音色。不要照抄旧版 AppID/Access Token 教程到此界面。

## 如何验证

1. 保存模型设置后点「测试连接」，检查返回结果。该检查只验证接口连通，不代表完整分析一定成功。
2. 保存语音合成后点「测试」，v1.5.2 起会合成并**实际播放**试听音；检查是否听到声音、字节数与耗时。未听到声音时检查输出设备。
3. 在模拟模式或练习赛提出分析问题，检查实际 AI 答案与耳机声音。普通油量、名次等事实问题可能由本地规则直接回答，不能用来证明 AI 接入成功；在问答记录中确认回答路径。
4. 使用文字试问时，先「预览解析」，再点「执行并播报」。仅预览不会调用外部服务。

本版本模型请求上限 3 秒、TTS 上限 2.5 秒，单次提问总预算 6 秒；连续超时会影响 AI 可用状态。接口虽能连通，也可能因延迟或回答格式不符合要求而失败。模型接入区提供「关闭思考模式」：默认关闭；不关闭时 DeepSeek 使用 `thinking` 开启加 `reasoning_effort: low`，OpenAI 系列使用 `reasoning_effort: low`，其他兼容服务商是否支持由对方决定。本文已核对字段映射与官方接口，未使用用户密钥进行付费端到端测试。

## 常见问题

| 现象 | 排查顺序 |
| --- | --- |
| 鉴权失败 | 核对服务商与 Key，去掉粘贴的首尾空格；豆包必须使用豆包语音新版控制台 Key |
| 模型不可用 | 核对当前模型名称和账号权限，重新保存；不要继续使用过期预设名 |
| 余额、额度或服务错误 | 在相应服务商控制台核对余额、试用额度、服务开通与限额；模型和语音分别计费 |
| 豆包音色或资源错误 | 同一项目开通语音合成 2.0，选择该模型支持的公版音色并复制 ID；不要填昵称或复刻 ID |
| AI 有文字没有声音 | 检查合成服务是否为豆包、已单独保存并试听，再检查音量与音频路由 |
| 连通但分析失败或偏慢 | 检查问答记录中的 AI 错误、延迟及连续超时状态；赛前完成实际问题验证 |

安装版设置保存在本机 `%LOCALAPPDATA%\LittleNavigator`。不要公开完整配置、真实密钥或私人日志。更换泄露的密钥应在服务商控制台撤销旧 Key 并重新保存。

## 官方入口

- [DeepSeek 开放平台](https://platform.deepseek.com/) · [首次调用 API](https://api-docs.deepseek.com/zh-cn/)
- [豆包语音新版控制台入门](https://docs.volcengine.com/docs/DoubaoVoice/QuickStartNewConsole?lang=zh) · [HTTP 单向流式接口](https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-text-to-speech-http?lang=zh)

软件侧依据：`voice-assistant/app/static/app.js` 的 AI/TTS 表单与保存处理、`voice-assistant/src/config.py` 的默认参数、`voice-assistant/src/llm_engine.py` 与 `voice-assistant/src/tts_engine.py` 的请求实现。
