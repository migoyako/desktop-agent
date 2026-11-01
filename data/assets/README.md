# 角色素材槽位（用户本地导入）

这个目录存放**用户自己准备的角色美术素材**。它整体不进版本库、不进安装包：
模型、立绘和背景的版权与授权由素材提供者负责，仓库只保留本说明与目录骨架
（见 `docs/progress.md` 的 R10 与 D57）。

一层目录 = 一个人设，目录名必须与 `data/personas/<persona_id>/` 一致。
默认人设是 `remielle`。

```
data/assets/
  <persona_id>/
    avatar.png          # 头像：png · webp · jpg，用于主界面、设置、管理台的头像位
    background.jpg      # 可选：窗口背景：png · webp · jpg（主窗、设置、管理台；浮窗仍为透明）
    live2d/
      model3.json       # Live2D 模型入口 = 桌面浮窗（桌宠）载体之一，.moc3 / 贴图 / 物理 / 表情文件放同目录
    avatar.mavatar      # 可选：Mesh Avatar Studio 工程归档 = 另一类桌宠载体（没有 Live2D 时生效）
```

约定：

- 文件名固定，一个角色一份当前生效素材；换角色 = 切换人设，不引入索引清单文件。
- 只读，不写回：程序不会生成、改名或清理这里的文件，删除即彻底删除（与记忆导出/删除同一边界）。设置里的“清除背景”只关闭开关，不会删除 `background.*`。
- **四类素材各管一件事、互不兜底**（2026-10-30 决定，见 `docs/progress.md` 的 D58；2026-10-31 追加 D72）：`live2d/model3.json` 与 `avatar.mavatar` 是桌宠的两个并列载体——存在 Live2D 时优先，否则使用 `.mavatar`；**两者都缺失、损坏或 WebGL 不可用时桌宠功能直接失效**，浮窗不显示并在设置页与 `doctor.py` 说明原因，**不会**退回 `avatar.*`、不会退回任何内置形象，也不会在 Live2D 与 `.mavatar` 之间静默切换；`avatar.*` 缺失只让头像位留空；背景缺失才回退主题纯色（"没有背景图"是正常配置，不是故障）。
- 当前状态（2026-10-31，M13.1–M13.4 已交付）：**`avatar.*`、`background.*`、`live2d/` 与 `avatar.mavatar` 都已生效**。头像经令牌门控的 `GET /api/asset/avatar` 供主界面顶栏、设置页人设头部与管理台设置页使用（`png → webp → jpg` 取第一份可用者，上限 8 MiB，魔数不符/空文件/符号链接按"无素材"处理），缺失时这三处留空、不画替代形象。背景经同一套令牌门控的 `GET /api/asset/background` 读取（上限 12 MiB），放好图片后到设置「数据与外观 → 背景图」点“使用背景”：主窗、设置与管理台会铺满居中裁切的图片并叠加当前主题的遮罩，桌面浮窗始终保持透明；点“清除背景”只关闭开关、不删文件，把文件删掉也会自动回到纯色。`live2d/` 经 token 门控的 `GET /api/live2d/<token>/<相对路径>` 提供给浮窗：`model3.json` 上限 1 MiB、单个文件 32 MiB、整个模型 96 MiB，`.moc3` 魔数与贴图格式都会校验，损坏或超限按“没有可用模型”处理；换模型后刷新浮窗即更新。表情按 `Expressions[].Name` 与情绪标签宽松匹配，“减少动态效果”会停掉待机动作与物理。`avatar.mavatar` 是 Mesh Avatar Studio 的打包归档，经 token 门控的 `GET /api/asset/mesh` 提供给浮窗：**存在 `live2d/model3.json` 时 Live2D 优先，没有 Live2D 时才用它**；只接受 STORED、≤200 条目、单条 64 MiB、总量 512 MiB，路径/必需文件/PNG 魔数/`avatar.json` 与 `rig.json` 版本都会校验，损坏或超限按“没有可用归档”处理，同样不显示替代形象。换归档后刷新浮窗即更新。
- 备份只包含 SQLite 数据库、导出只打包表数据，**这个目录不会进入备份或导出包**（2026-10-31 决定，见 D69）；想保留素材请自行备份本目录。
- `doctor.py` 的 `Character assets` 会列出当前人设的素材状态：未开启桌面浮窗而缺素材只 WARN（正常状态），**已开启浮窗却没有可用 Live2D 模型或 `.mavatar` 时 FAIL**——那意味着一项声明开启的功能实际不可用。
