# 素材和第三方依赖

项目自己的程序源代码使用 [PolyForm Noncommercial License 1.0.0](LICENSE)：个人、非商业免费，商业使用需单独取得 [商业授权](COMMERCIAL-LICENSE.md)。这个许可不自动授予 iRacing、MOONDROP 等第三方名称、标识、预训练模型和语音素材的权利；项目自制素材的边界见 [ASSETS-LICENSE.md](ASSETS-LICENSE.md)。

- iRacing 遥测通过 pyirsdk 读取；SDK 字段解释以 iRacing 官方资料和实际遥测为准。
- 本地识别使用 SenseVoice 模型，由 sherpa-onnx 运行。模型从发行版取用，分发和训练用途须遵循模型提供方的许可。
- 桌面窗口使用 pywebview、pythonnet 和 Microsoft WebView2；安装包内含微软官方 WebView2 引导器，系统缺少运行时由它联网补装（全离线版安装包则改带微软官方离线运行时）。
- 方向盘读取使用 PySDL2 / SDL2；音频、拼音和繁简转换依赖见 requirements-tested.txt，相关库保留各自许可。
- 中文语音使用豆包小何 2.0 音色预生成。播放器正常运行不需要语音服务凭据；调用语音生成服务需要自行取得权限并承担服务费用。
- 横幅及软件中的团队、品牌标识用于展示项目相关信息，不作为项目许可证素材重新授权。
- 首页 IFP Wargame、MoonDrop 与 Drag-On XTT 标识由项目维护者提供。透明 PNG 与内嵌素材的 SVG 排版源位于 `assets/readme/`；IFP 与 3053.PNG 新车队图使用生成式去底派生图，单色标识另提供深色主题版本，处理记录见 [品牌图来源](assets/readme/source/PROVENANCE.md)，相关权利仍归原持有人。
- Inno Setup 简体中文语言文件来自 kira-96/Inno-Setup-Chinese-Simplified-Translation，文件内保留原维护者信息。

模型和大量音频放在 Release / 本地资源目录，源码仓库不收录这些二进制资源。

## 界面字体

- Public Sans，来自 [Public Sans](https://github.com/uswds/public-sans)，以 SIL Open Font License 1.1 分发。
- JetBrains Mono，来自 [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono)，以 SIL Open Font License 1.1 分发。

两种字体均本地加载，不依赖在线字体服务；字体与许可在 `voice-assistant/app/static/fonts/`，安装包保留对应 OFL 文件。中文由系统字体回退显示。
