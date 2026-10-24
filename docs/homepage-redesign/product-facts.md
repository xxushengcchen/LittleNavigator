# 首页内容核实

核实日期：2026-10-19。源码基线：`57b96438f07b78c38e4a3288b6f00472574444cf`（v1.4.5）。仅制作本地草稿，不推送分支。

| 事实 | 依据 |
| --- | --- |
| 项目为 iRacing 中文语音助手与可独立安装的 Spotter 语音包 | 根 README；`voice-assistant/src/engine.py`；语音包安装文档 |
| 支持按住键盘/方向盘 PTT 说话、松开后回答 | `voice-assistant/config.yaml`；`src/listener.py`；使用指南 |
| 本地 SenseVoice 识别，读取 SDK 遥测，使用录音回答规则问题 | `src/engine.py`；`src/telemetry.py`；`THIRD_PARTY_NOTICES.md` |
| AI 分析、TTS 与语音进站控制为可选能力，默认关闭 | `config.yaml` 的 ai/car_control/tts 配置；AI 页面 |
| 换胎、加油、快修等通过确定性词表执行；加油只支持整数升数 | `src/car_control.py`；`src/command_match.py`；真机修复文档 |
| 只读控制台显示服务勾选，缺失/断线不伪造遥测 | `app/static/app.js`；`src/engine.py.snapshot_fields` |
| Windows 10 2004+ / Windows 11 x64 | `packaging/installer.iss`：MinVersion 10.0.19041、x64os |
| 普通用户安装 exe，无需 Python；`--sim` 为模拟模式参数 | `app/desktop.py`；开发指南；安装脚本 |
| 当前正式版 v1.4.5，安装附件与校验文件均存在 | GitHub 连接器读取 releases/latest，tag v1.4.5，draft=false，prerelease=false |
| 现有截图是真实软件截图，但数据为模拟夹具 | `docs/ui-preview/v1.4.5/`；版本说明与截图标注 |
| 源码 PolyForm Noncommercial（商业使用需授权）；品牌、模型、语音等不自动适用代码许可证 | `LICENSE`、`COMMERCIAL-LICENSE.md`、`THIRD_PARTY_NOTICES.md` |
| 没有多语言 README，仅有根 README 和源码目录说明 | 本地文件清单 |

已阅读根 README、DEVELOPMENT、CONTRIBUTING、版本说明、界面指南，以及入口、引擎、改车与配置相关代码。不宣称实测性能、用户数量、真实赛事验收或任意平台兼容。

远端依据：[仓库 README](https://github.com/xxushengcchen/LittleNavigator/blob/main/README.md)、[正式发布](https://github.com/xxushengcchen/LittleNavigator/releases/tag/v1.4.5)。

GitHub 展示约束：最终使用 Markdown、有限 HTML、相对图片路径和必要的 details；不用 JavaScript、iframe、自定义 CSS 或将整篇正文做成图片。

## API 教程补充核对（2026-10-20）

- DeepSeek 当前首次调用文档列出的基础地址为 `https://api.deepseek.com`、示例模型为 `deepseek-flash`；Key 入口指向开放平台 API keys。对照当前 v1.4.5 的前端预设仍为 `deepseek-chat`，因此教程明确要求手动覆盖。来源：https://api-docs.deepseek.com/zh-cn/
- 豆包语音新版控制台要求注册认证、服务开通、在 API Key 管理获取密钥，并在音色库复制音色 ID。来源：https://docs.volcengine.com/docs/DoubaoVoice/QuickStartNewConsole?lang=zh
- 当前软件豆包适配器使用 HTTP 单向流式接口、`X-Api-Key` 与默认资源 `seed-tts-2.0`，与官方接口说明匹配。教程使用 2.0 公版音色，不混用旧鉴权或声音复刻。来源：https://docs.volcengine.com/docs/DoubaoVoice/unidirectional-streaming-text-to-speech-http?lang=zh
- 已核对本地 app.js 表单与保存逻辑、config.py 默认值、llm_engine.py / tts_engine.py 请求实现。未登录用户账号或使用真实 Key 做付费调用；连接与完整分析的区别、时限及验证限制已写入教程。
- `app/server.py:ai_tts_test` 直接调用 `TtsEngine.test()`，后者仅合成并返回字节数、延迟，不调用播放器；因此文档明确 v1.4.5 的「试听」按钮不发声，耳机声音需通过实际 AI 回答验证。
