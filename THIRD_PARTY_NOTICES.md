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

## pyirsdk（iRacing SDK 封装）

- 版本：`voice-assistant/requirements-tested.txt` 锁定的 `pyirsdk==1.3.7`
- 版权：Copyright (c) 2014 Mihail Latyshov, kutu182@gmail.com
- 许可：MIT License；全文如下。打包时 `xiaoxiao.spec` 还会把该分发自带的
  `LICENSE` 收集到发行目录的 `licenses/pyirsdk/`，安装包随 `{app}` 一起携带。

```text
The MIT License (MIT)

Copyright (c) 2014 Mihail Latyshov, kutu182@gmail.com

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```
