# 单独安装中文 Spotter 语音包

只想使用 iRacing 自带事件触发的中文播报，可以只装语音包，不启动小小领航员助手。

## 取得语音包

- 从 [v1.4.1 发布附件](https://github.com/xxushengcchen/LittleNavigator/releases/tag/v1.4.1) 下载 `LittleNavigator-iRacing-VoicePack-v1.4.1.zip`，这是 24 kHz 母带版本。
- 或使用助手安装目录中的 `build\pack\Mandarin-CNTW-Xusheng_Chen`，这是同一批主包素材的 16 kHz 派生版，也可安装进游戏。

## 安装

1. 退出 iRacing 模拟器。下载 ZIP 时，先解压得到 `Mandarin-CNTW-Xusheng_Chen` 文件夹。
2. 找到 **iRacing 游戏安装目录**的 `sound\spcc`。默认通常为 `C:\Program Files (x86)\iRacing\sound\spcc`，Steam 或自选安装位置应以实际目录为准。
3. 复制整个语音包文件夹，确保 `spmsg.txt` 直接位于其中，不要重复嵌套同名文件夹。

```text
iRacing/
└─ sound/
   └─ spcc/
      └─ Mandarin-CNTW-Xusheng_Chen/
         ├─ spmsg.txt
         ├─ SPCC_....WAV
         └─ ...
```

4. 在模拟器 **Options → Sound → Voice Pack** 中选择 `Mandarin-CNTW-Xusheng_Chen`，并确认 Spotter 已开启。
5. 重新加载模拟器，让语音包生效。列表里没有时，先检查路径与 `spmsg.txt`。

这里使用游戏安装目录，通常不是「文档\iRacing」。`assistant-extra`、`assistant-telemetry` 是助手专用素材，不需要复制进游戏。

只装语音包不会获得按键提问功能；需要语音问答时仍须运行助手。

安装路径与选择方式参考 iRacing 官方说明：[第三方语音包](https://support.iracing.com/support/solutions/articles/31000133521-using-third-party-spotter-packs)、[自定义语音包](https://support.iracing.com/support/solutions/articles/31000157884-creating-custom-spotter-packs)。

[返回项目首页](../../README.md) · [助手使用指南](v1.4.5-interface.md)
