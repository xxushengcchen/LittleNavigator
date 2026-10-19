# 品牌图处理记录

输入由项目维护者提供：`IMG_2633.PNG`（IFP Wargame）、`3051.PNG`（MoonDrop 五款主要标识）、`3052.PNG`（Drag-On XTT 车队）。品牌权利仍归原持有人。

MoonDrop 和车队原图已有透明通道。SVG 使用内嵌原图和 viewBox 裁切排版，没有生成替代标识。粉色耳机人物标使用 SVG 颜色矩阵隔离粉色墨迹，排除原图中混入裁切区域的相邻白色标识，镂空部分保持透明。单色深色主题版本通过 SVG 颜色矩阵改为白色墨迹，保持透明通道；彩色标识保留原配色。裁切参数见 manifest.json。

IFP 输入为实心白底，使用 imagegen 的图片编辑模式去底，设置 `transparent_background: true`。该派生结果已内嵌 SVG，日常重新导出不需要调用服务。生成式去底可能产生细微像素差异，不能视作品牌原始矢量文件。

去底提示词原文：

```text
Edit target: the attached original IFP Wargame logo. Background-extraction only: remove the solid white background and make it genuinely transparent alpha, including white background visible inside letter counters. Preserve the existing black and gray printed logo artwork as faithfully as possible: exact italic letter shapes, wording, placement, proportions, existing small gray details and the tiny Chinese tagline. Do not redraw, redesign, restyle, add any elements, add a shadow, add an outline, invent text, or change the brand. Transparent background, no checkerboard baked into the image. This is a faithful cutout of the supplied original, not a new logo.
```

展示 PNG 使用 Playwright/Edge 从自包含 SVG 导出，设置 `omitBackground: true`。透明度检查与主题预览结果保存在 docs/homepage-redesign；所有展示 PNG 均含透明像素，四角 alpha 为 0。
