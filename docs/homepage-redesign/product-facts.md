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
| 源码 MIT；品牌、模型、语音等不自动适用 MIT | `LICENSE`、`THIRD_PARTY_NOTICES.md` |
| 没有多语言 README，仅有根 README 和源码目录说明 | 本地文件清单 |

已阅读根 README、DEVELOPMENT、CONTRIBUTING、版本说明、界面指南，以及入口、引擎、改车与配置相关代码。不宣称实测性能、用户数量、真实赛事验收或任意平台兼容。

远端依据：[仓库 README](https://github.com/xxushengcchen/LittleNavigator/blob/main/README.md)、[正式发布](https://github.com/xxushengcchen/LittleNavigator/releases/tag/v1.4.5)。

GitHub 展示约束：最终使用 Markdown、有限 HTML、相对图片路径和必要的 details；不用 JavaScript、iframe、自定义 CSS 或将整篇正文做成图片。
