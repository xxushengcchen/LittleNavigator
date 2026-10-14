# 素材和第三方依赖

项目自己的程序源代码使用 MIT License。这个许可不自动授予 iRacing、MOONDROP 等第三方名称、标识、预训练模型和语音素材的权利。

- iRacing 遥测通过 pyirsdk 读取；SDK 字段解释以 iRacing 官方资料和实际遥测为准。
- 本地识别使用 SenseVoice 模型，由 sherpa-onnx 运行。模型从发行版取用，分发和训练用途须遵循模型提供方的许可。
- 桌面窗口使用 pywebview、pythonnet 和 Microsoft WebView2；安装包包含微软官方 WebView2 离线安装组件。
- 方向盘读取使用 PySDL2 / SDL2；音频、拼音和繁简转换依赖见 requirements-tested.txt，相关库保留各自许可。
- 中文语音使用豆包小何 2.0 音色预生成。播放器正常运行不需要语音服务凭据；调用语音生成服务需要自行取得权限并承担服务费用。
- 横幅及软件中的团队、品牌标识用于展示项目相关信息，不作为 MIT 许可素材重新授权。
- Inno Setup 简体中文语言文件来自 kira-96/Inno-Setup-Chinese-Simplified-Translation，文件内保留原维护者信息。

模型和大量音频放在 Release / 本地资源目录，源码仓库不收录这些二进制资源。
