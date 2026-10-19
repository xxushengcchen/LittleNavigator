# 首页标识素材

使用项目维护者提供的品牌图。全部展示 PNG 均含透明通道，没有白色或深色底板；可编辑 SVG 排版源位于 source/，内嵌输入图片，不依赖外部资源。

| 文件 | 身份 | 显示处理 |
| --- | --- | --- |
| ifp-wargame.png | 赞助支持：IFP Wargame | 去除原图实心白底，裁去周围留白 |
| moondrop.png | 赞助支持：水月雨 MoonDrop | 横向字标 |
| moondrop-racing.png | MoonDrop | Racing 字标 |
| moondrop-signature.png | MoonDrop | 手写签名 |
| moondrop-headphones.png | MoonDrop | 粉色耳机人物标 |
| moondrop-symbol.png | MoonDrop | 黑色图形标 |
| drag-on-xtt.png | 车队：Drag-On XTT racing team | 使用 3053.PNG 新图去除黑底，保留白色龙形与彩色字标 |

共七款标识。IFP 与 MoonDrop 的四款单色标识另有 `-dark.png` 浅色墨迹版本，共十二张 PNG。README 使用 GitHub 支持的 `<picture>` 按主题切换，彩色标识沿用原配色，品牌名和身份同时以文字保留。裁切范围、尺寸与主题版本见 source/manifest.json；处理来源见 [PROVENANCE.md](source/PROVENANCE.md)。

重新导出：修改 `source/` 中的 SVG 排版源，在仓库根目录运行 `npm ci`、`npm run assets:readme`。复用已有 Playwright 开发依赖，Windows 默认使用本机 Edge；其他浏览器设置方式见 [开发指南](../../DEVELOPMENT.md)。导出直接复用已内嵌的素材，不需要再次调用图像生成服务。

品牌素材不自动适用项目 MIT 许可。原图和派生排版仅用于本项目署名，权利仍归原持有人。
