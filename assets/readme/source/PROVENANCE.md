# 品牌图处理记录

输入由项目维护者提供：`IMG_2633.PNG`（IFP Wargame）、`3051.PNG`（MoonDrop 五款主要标识）、`3053.PNG`（维护者于 2026-10-20 指定替换的 Drag-On XTT 车队新图，替代 3052.PNG）。品牌权利仍归原持有人。

MoonDrop 原图已有透明通道。SVG 使用内嵌原图和 viewBox 裁切排版。粉色耳机人物标使用 SVG 颜色矩阵隔离粉色墨迹，排除原图中混入裁切区域的相邻白色标识，镂空部分保持透明。单色深色主题版本通过 SVG 颜色矩阵改为白色墨迹，保持透明通道；彩色标识保留原配色。裁切参数见 manifest.json。

IFP 输入为实心白底，使用 imagegen 的图片编辑模式去底，设置 `transparent_background: true`。该派生结果已内嵌 SVG，日常重新导出不需要调用服务。生成式去底可能产生细微像素差异，不能视作品牌原始矢量文件。

去底提示词原文：

```text
Edit target: the attached original IFP Wargame logo. Background-extraction only: remove the solid white background and make it genuinely transparent alpha, including white background visible inside letter counters. Preserve the existing black and gray printed logo artwork as faithfully as possible: exact italic letter shapes, wording, placement, proportions, existing small gray details and the tiny Chinese tagline. Do not redraw, redesign, restyle, add any elements, add a shadow, add an outline, invent text, or change the brand. Transparent background, no checkerboard baked into the image. This is a faithful cutout of the supplied original, not a new logo.
```

展示 PNG 使用 Playwright/Edge 从自包含 SVG 导出，设置 `omitBackground: true`。透明度检查与主题预览结果保存在 docs/homepage-redesign；所有展示 PNG 均含透明像素，四角 alpha 为 0。

## 车队新图去底

3053.PNG 为 RGB 黑底图，使用内置 imagegen 编辑模式去底，设置 `transparent_background: true`，结果内嵌到 drag-on-xtt.svg，再通过相同导出脚本生成 PNG。与 IFP 一样，该结果属于生成式去底派生图，不承诺逐像素保真，不应当作原始品牌矢量文件。

提示词原文：

```text
Edit target: the supplied Drag-On XTT racing team logo. Only extract the existing logo from its solid black background and make the background genuinely transparent, including black negative-space gaps between the letters and around the dragon. Preserve the exact white dragon, white Drag-On lettering, original cyan/magenta gradient outlines, separator brush stroke, and racing team text. Keep the exact composition, proportions, letter shapes, wording, and colors. Do not redesign or redraw the brand, add shadows, add any new outline, invent lettering, or add a backdrop. Retain deliberate internal black line detail within the white dragon where needed, but remove the large surrounding black canvas. Return a faithful transparent-background cutout with alpha and no baked-in checkerboard.
```
