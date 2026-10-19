# 首页改版交付说明

按用户选定的 A「阅读优先」完成。左对齐、GitHub 原生字体和间距，三个真实 logo 在宽屏同行展示，在手机自然换行。没有装饰图片、自定义 CSS、JavaScript、iframe、徽章堆叠或虚构的产品画面。

## 文件

- 根 `README.md`：定位、价值、三步开始、问法、真实界面、可选接口、独立语音包、边界、贡献与许可。
- `assets/readme/`：七款透明标识，其中 MoonDrop 五款齐全；含主题版本共十二张 PNG、自包含 SVG 排版源、导出命令与处理记录。PNG 总计 179,212 字节，约 175.0 KiB。
- `docs/guide/spotter-install.md`：从首页移出的完整独立语音包安装步骤。
- `docs/guide/capabilities-and-limits.md`：数据口径、AI 与操作边界、尚未实现的后续计划。
- `THIRD_PARTY_NOTICES.md`：补充三个品牌的来源和权利说明。
- `scripts/check_readme_links.py`：本地链接、标题锚点、图片 alt 与 GitHub 支持的标记检查。
- `package.json`：增加 `assets:readme` 导出命令，复用已有 Playwright 开发依赖，没有增加应用运行时依赖。

原有软件截图继续使用 `docs/ui-preview/v1.4.5/dashboard.png`，并在正文与 alt 中说明数据为模拟。没有真实操作录屏素材，本次不制作或冒充录屏 GIF。仓库没有多语言 README；源码目录 README 的现有链接与版本仍然有效。

## 验证

- GitHub 官方 Markdown API 成功渲染最终 README。
- 在真实 github.com 仓库页面 DOM/CSS 中预览官方 HTML；检查浅色、深色两套主题和 1440、768、375、320px 四种宽度，共 8 种组合。无页面或正文横向溢出、无缺图，三个标识均可读，折叠说明能打开。
- 发布前预览中，浏览器仅在本地拦截对应图片请求加载 PNG，没有推送草稿。API 未附带的标题 id 按 GitHub 锚点规则补齐后用于预览导航测试；以上截图与检查数据来自发布前预览。
- `python scripts/check_readme_links.py`：6 个文档、40 个本地链接/锚点通过；没有自定义样式或脚本，HTML 图片均有 alt。
- `npm run assets:readme` 对应脚本已实际运行，十二张 PNG 从自包含 SVG 成功导出。维护时先执行 `npm ci`；Windows 默认使用 Edge。
- 十二张 PNG 逐一检查 RGBA 和透明度范围，四角 alpha 全部为 0；详情见 [透明度检查](transparency-validation.json)。IFP 使用 imagegen 去除白底，其他图沿用原图透明通道；单色标识提供主题适配，来源和提示词保留在素材目录。
- 最新正式版 v1.4.5、v1.4.1 独立语音包 ZIP 已通过 GitHub 连接器核对存在；安装位置和游戏内选择方式依据 iRacing 官方帮助。
- `--sim` 已核对当前命令行入口。没有为文档检查启动用户安装的软件、游戏、麦克风或外部 AI/TTS；没有重新跑与文档修改无关的后端测试。
- `git diff --check` 通过。

浏览器检查数据见 [validation.json](validation.json)。预览：[桌面浅色](final-preview/readme-light-1440.png)、[手机深色](final-preview/readme-dark-375.png)、[五款标识浅色](final-preview/readme-brands-light.png)、[五款标识深色](final-preview/readme-brands-dark.png)。正文按用户要求加入六处 emoji，未改变标题与锚点。

## 本地状态与维护

工作分支为 `codex/readme-homepage-redesign`。初稿仅在本地审阅；用户确认透明标识、五款 MoonDrop 与 emoji 调整后，明确要求“推到git吧”，授权提交并更新 GitHub 默认分支 `main`。发布采用正常快进推送。

早期三版草稿和冗余截图保留在本机，由此目录的 .gitignore 排除；可发布素材与源文件均位于 assets/readme。修改 logo 排布时编辑对应 SVG，执行 `npm run assets:readme`。下一版发布时更新 README 的版本说明链接和产品截图，不必修改 latest 下载入口。
