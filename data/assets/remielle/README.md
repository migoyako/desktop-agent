# 把素材放进这个目录

目录名 = 人设 ID（对应 `data/personas/<id>/`）。本层可以有多个人设目录，程序只读取当前人设的那一份。

- `avatar.png` — 头像，支持 `png` · `webp` · `jpg`，用于主界面、设置与管理台的头像位；缺失时这些位置留空。多于一种格式时按 `avatar.png → avatar.webp → avatar.jpg` 取第一份可用的。
- `background.jpg` — 可选，窗口背景，支持 `png` · `webp` · `jpg`（主窗、设置、管理台；桌面浮窗保持透明）。文件名按 `background.png → background.webp → background.jpg` 取第一份可用的，上限 12 MiB。
- `live2d/model3.json` — Live2D 模型入口，**桌面浮窗（桌宠）的两个载体之一**；`.moc3`、贴图、物理与表情文件与它放在同一目录（M13.3 起生效）。
- `avatar.mavatar` — Mesh Avatar Studio 导出的归档，**桌面浮窗（桌宠）的另一个载体**（M13.4 起生效）；必须是 STORED 未加密 ZIP，内含 `avatar.json` / `rig.json` / `built/layers.json` / `built/base.png` / `built/hairmask.png` 与图层贴图，上限 128 MiB（单条 64 MiB、总量 512 MiB、≤200 条）。

四类素材各管一件事、互不兜底：两个桌宠载体里**存在 `live2d/model3.json` 时优先用 Live2D，否则用 `avatar.mavatar`**；两者都缺失、损坏或 WebGL 不可用时**桌宠功能失效**（浮窗不显示并在设置页与 `doctor.py` 说明原因），不会退回 `avatar.*`，不会退回内置形象，**也不会在 Live2D 与 `.mavatar` 之间静默切换**（`docs/progress.md` D58/D72）。

当前状态（2026-10-31）：**`avatar.*`、`background.*`、`live2d/` 与 `avatar.mavatar` 都已生效**（M13.1–M13.4）。头像放好后刷新界面，主界面顶栏、设置页人设头部与管理台设置页都会显示它；删掉即回到留空，上限 8 MiB，损坏或格式不符按"没有素材"处理。背景放好后到设置「数据与外观 → 背景图」点“使用背景”，主窗、设置与管理台会按当前主题叠加遮罩并显示这张图；点“清除背景”只关闭开关、不删除文件，删掉文件也会自动回到纯色。`live2d/` 放好 `model3.json` 与同目录资源后刷新浮窗即由 Live2D 渲染，模型经 token 门控的 `/api/live2d/<token>/<相对路径>` 读取（`model3.json` 上限 1 MiB、单文件 32 MiB、整个模型 96 MiB），损坏或超限按“没有可用模型”处理；表情按 `Expressions[].Name` 与情绪标签宽松匹配，“减少动态效果”会停掉待机动作与物理。没有 Live2D 时放好 `avatar.mavatar` 即改由 mesh 渲染器渲染，归档经 token 门控的 `GET /api/asset/mesh?v=<stamp>` 取回后在浮窗内解包（校验失败按“没有可用归档”处理，不会改用 Live2D）；表情按 `smile / sad / neutral` 三档映射情绪。**不可用即失效、不回退**，两个载体都不可用时浮窗直接不显示并在设置页与 `doctor.py` 说明原因。
本目录除说明文件外不进版本库，也不进安装包，并且**不会进入备份或导出包**（想保留请自行备份）。
