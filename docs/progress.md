# 项目进度与路线图

> 版本：v1.38
> 更新日期：2026-10-30
> 对应设计：[数字分身技术方案](technical-plan.md)  
> 范围：当前仓库本体；`research/` 仅作参考项目，不计入完成度。

## 0. 结论

- 横向功能覆盖约 **92–95%**，纵向可靠性、记忆深度约 **72–77%**。
- 当前主要矛盾已从“执行不可对账”转为 **记忆召回质量与可解释性**。
- **M1a（执行可信度）已完成**：5 个 P0 全部关闭。
- **M1b（原生工具循环）核心与收尾已完成**：模型可原生调用 `search_web` / `write_note`，写入仍只生成提案；单工具超时与 function calling 自动降级已接入。
- **M2 第一批已完成**：版本化迁移、用户原文证据、规范键、候选评分、纠正取代、遗忘墓碑与召回日志。
- **M2 第二批已完成（2026-10-26）**：中文 2/3-gram 预分词 + FTS5/BM25 候选召回、关键词降级、索引启动自愈、`recall_log.source`；旧聊天可暂停续跑导入、跨副本去重、成本预估与遗忘过滤；派生记忆跨类型级联遗忘；证据/召回台与 protected 人设入口；`doctor.py` 增加数据库完整性/迁移版本/FTS 状态检查。
- **M2 收尾加固（2026-10-26）**：角色扮演、引用台词与假设句不再进入用户事实抽取；旧聊天导入按会话标题与“开始/结束扮演”标记维护角色扮演状态，只导入消息、不抽取记忆。
- **M2.5 人设层已完成（2026-10-26）**：本地 persona pack（`persona.md` / `facts.md` / `worldbook.md` / `persona.json`）、默认角色“蕾米埃尔·丹”、稳定前缀 SHA-256、系统提示分层、Qt/Tk 人设面板；支持来源/许可/大小/版本预览与安全的本地导入；用户称呼/关系/边界以 `protected` persona 记忆保存；世界书按查询触发，不进入稳定前缀。
- **M3 上下文编排已完成（2026-10-26）**：`context.py` 分层提示（稳定层 / 会话前缀 / 运行时上下文 / 会话历史）；稳定层 SHA-256 跨模式一致；历史按字符预算保留最近完整消息，82% 触发抽取式 checkpoint 摘要并复用；工具输出超过 16KB 落盘到 `data/tool_results/`，只回预览与句柄，`read_tool_result` 按会话分段回读；提示词明确“未回读不算已读”。
- **M3 复查已完成（2026-10-26）**：实测 145 测试通过、`doctor.py` 全项 OK、数据库已迁移到 v4 且 FTS ready，与文档一致；复查发现的 3 个代码项已在 M3.5 修复（压缩轮次只写 1 条召回、`used_in_reply` 接线、中文单字查询关键词兜底）；4 个前置缺口均已关闭：真实模型链路已于 2026-10-27 冒烟，提醒/未完成话题底座与活动探测于 M4.0 补齐（改用 `ctypes`，不引入 `psutil`），`artifacts/` 临时目录已不在工作区。M3 + M3.5 已提交（`23c7837`）。
- **M3.5 评测与冒烟已完成（2026-10-27）**：新增确定性召回评测集 `eval_recall.py`（26 条中文查询，覆盖单字/短词/长句/ASCII），基线 `hit@1=1.0000 hit@5=1.0000 mrr=1.0000`；真实模型 `sensenova-6.8-flash-lite` 冒烟两项通过——`current_time` 原生 function calling（2 轮）与“搜索 → 30KB 结果落盘 → `read_tool_result` 回读 → 最终回答”（3 轮，含 result_id 与 0–2000 读取区间）。冒烟还暴露工具轮次上限会吞掉最后一轮结果，已修复为末轮后再发一次无工具收尾请求。
- **M4.0 主动底座已完成（2026-10-27）**：迁移 v5 新增 `reminders` 表与创建/到点/完成/忽略闭环；`proactive/topics.py` 从未回复或失败回复的最近提问派生可追溯的未完成话题；`proactive/activity.py` 用 `ctypes`（`GetLastInputInfo` / `GetForegroundWindow` / 全屏检测）实现 `active/idle/away/private/focus/game/unknown`，不引入 `psutil`；`proactive/gate.py` 纯函数门控（Quiet → 免打扰 → 每日上限 → 间隔 → 未回复 → 活动/隐私）；`proactive/engine.py` 汇总来源，**空来源直接返回不发言**，不生成随机话题。真实库已迁移到 v5。
- **M4 主体已完成（2026-10-27）**：迁移 v6 新增 `proactive_state` 与 `proactive_events`；`ProactiveService` 负责状态持久化、每日计数/跨天重置、未回复计数、错过提醒标记与送达；来源加入“近期被回复采用的记忆”（显式 opt-in）；反重复按来源半衰期衰减（话题 24h、记忆 72h）并用内容/URL 归一化哈希匹配，提醒因用户明确请求而豁免；主动消息由本地模板生成并落为助手消息，事件记录 `trigger_reason`、状态、`message_id` 与未读标记；Qt 每 60 秒轮询并提供“主动”面板（开关、免打扰、每日上限、间隔、未回复阈值、记忆来源、事件表、未读与手动检查），Tk 提供对应入口。
- **M5 工作任务卡已完成（2026-10-27）**：迁移 v7 新增 `work_tasks` / `work_receipts`；新增 `work/` 包，意图路由（`companion/work/clarify`，规则优先、可选模型分类、12 秒硬上限、注入守卫）、任务卡六要素、计划状态机（`NORMAL/PLAN_DISCUSSING/PLAN_REVIEW/EXECUTING`）、审批（approve/revise/reject，30 分钟 TTL，超时不批准）、转发回执（`unknown` 终态不可重发）与启动恢复齐备；Qt/Tk 新增“任务”面板；运行时只对外部副作用请求自动建卡，既有本地流程不变。
- **M6 桌面形象与流式交互已完成（2026-10-27）**：LLM 客户端新增 `stream_chat`（OpenAI `stream=True`、工具调用增量拼装、错误转事件），运行时统一为 `delta / reset / tool / done / error` 事件并透传 generation id；停止通过 `threading.Event` 贯穿流读取与工具轮次，已显示部分入库并追加“（已停止生成）”，迟到分片按 generation 丢弃；`streaming.py` 增量剥离 `[[mood/action/sticker]]`，跨分片标记不漏进正文或数据库；Qt 新增透明无边框置顶形象浮窗（默认点击穿透、头像命中才可拖拽，位置存 `ui_state.json` 并按最近屏幕夹取）、`QSystemTrayIcon` 托盘与执行器回执状态气泡。
- **M7 技能包已完成（2026-10-27）**：新增 `skills/` 包，扫描 `data/skills/<id>/SKILL.md` 并严格解析 `name / description / tools / effectKind / modes / references`；按 `effectKind` 与工具风险推导可用模式，非法清单、未知工具、工具风险越界、模式冲突、参考文件缺失/越界均在加载时拒绝；`invoke_skill` 按需加载正文，`read_skill_reference` 按 offset/limit 分段读取声明文件（单次 ≤ 8000 字符）；技能加载后本轮工具范围收窄为声明工具（元工具除外）；迁移 v8 新增 `skill_audit`，记录调用、参考读取与越权拒绝；`doctor.py` 新增 `Skills` 检查。
- **M8 管理与自检台已完成（2026-10-27）**：新增 `maintenance.py`（在线备份/校验/恢复、zip 导出、按记忆树与会话/全量彻底删除、完整性报告、单实例锁）与 `redaction.py`（密钥脱敏）；迁移前自动快照；Qt 新增 7 页管理台（记忆、召回、候选、执行与审计、情绪、失败、设置与数据）并从主窗口与托盘进入；`doctor.py` 增加 schema 版本、缺失索引、FTS 行数、备份与实例锁状态；日志与审计落库前统一脱敏，导出不含 `config/keys.json`；新增 21 个测试，全量 312 个通过。
- **M8.5 桌面界面改版（方案 A）已完成（2026-10-27）**：新增 `ui/theme.py`（深色/珍珠白两套语义色 token 与 QSS 构建器，强调色 `#ff5b8a`）、`ui/chat_view.py`（气泡记录、工具回执卡、流式光标、按签名增量重建）与 `ui/inspector.py`（右栏只读详情，全部取自已落库的运行时状态）；主窗口改为“左栏 / 顶部模式切换 / 中聊天 / 右可折叠面板”，窗口几何、主题与两栏展开状态写入 `ui_state.json`。不引入 Web 运行时，Tk 兼容界面不变。
- **M8.5 复查修复：窗口找回（2026-10-27）**：原顶部“收起”把主窗口 `hide()` 进托盘，而主窗口是 `Qt.Tool`（不占任务栏），托盘缺失或被折叠时用户再也找不回窗口。改为普通窗口类型 + `Qt.WindowMinimizeButtonHint` 与头像窗口图标，按钮更名“最小化”并走 `showMinimized()`，`changeEvent` 在还原时回填最小化前的几何（无边框窗口在 Windows 上还原尺寸不可靠），`toggle_window` 能识别最小态，托盘文案增加“还原主窗口”。
- **M8.5 复查修复：提醒能力与窗口层级（2026-10-27）**：`reminders.py` 的时间解析补齐“秒/半小时/中文多位数字/今天·明天·后天 + 凌晨…夜里 的钟点”，并抽出可复用的 `resolve_when()`；新增 `set_reminder` 低风险工具，模型可在陪伴/安静模式把“等我忙完这阵提醒我”这类说法落成提醒，识别不出时间时返回失败让模型追问。主窗口与侧边面板去掉 `Qt.WindowStaysOnTopHint`（Tk 同步 `-topmost False`），只有桌面形象浮窗仍置顶。
- **M8.5 信息架构调整（2026-10-28）**：左栏不再常驻六个面板入口，只保留形象/状态、工作目录与会话摘要；记忆、情绪、人设、主动、任务、管理以及主题、桌面浮窗统一收进左下角“设置”中枢（新增 `ui/settings.py`）。会话摘要从最近消息派生话题、条数/最后时间与最新一句，随 `refresh_history()` 更新；设置项选中后先关闭设置再打开侧边面板，避免两个侧窗重叠。
- **M8.5 左栏会话树（2026-10-28）**：左栏按参考图改为“新的任务 / 搜索 / 工作区（目录行 + `…` / `+`）/ 缩进会话列表 / 底部设置”；形象、状态与人设名移到顶部标题栏，左栏不再放摘要卡。新增 `store.list_session_summaries()`（会话首条用户消息作标题、末条内容与条数作提示）、`runtime.session_summaries()` 与 `runtime.switch_session()`（同时迁移 `proactive_service` / `work` 的会话作用域并清空上下文），`ui_state.json` 记录 `active_session` 跨重启恢复；新建会话复用空会话、生成中禁止切换，搜索按标题/末条内容过滤会话行，空会话仅在当前会话时显示。
- **M9 应用层契约与 Qt 适配器已完成（2026-10-28）**：新增进程内 `CompanionApplicationService`，统一 `StartTurnCommand / RunContext / ApplicationEvent / ApprovalView / WorkspaceSnapshot` 五类契约；Qt 的流式启动、模式、工作区、会话、确认、取消与撤销均改走应用服务，`CompanionWindow` 同时兼容传入 runtime 或 service。run id 直接作为 generation id，事件统一携带 `run_id / generation_id / session_id / sequence`；生成期间禁止切模式、工作区或会话，`done` 只有在最终助手消息落库确认后才对外发出。FastAPI/SSE 与 Web/手机界面不在本轮范围，后续作为同一服务的 transport adapter 接入。
- **M10.1 运行可观察与消息表达已完成（2026-10-28）**：应用服务新增只读 `ui_projection()`（会话消息、当前 run、待审批摘要与右栏分节），Qt 主聊天改从投影取数；输入框上方新增统一运行条，按 `ApplicationEvent.stage / sequence` 显示思考、调用工具、生成回答、等待审批、完成、取消与失败，并展示当前工具、耗时与停止动作，陈旧事件按 run id 与 sequence 丢弃；消息支持安全 Markdown 子集（先转义再渲染，链接仅 http/https，代码块按纯文本）、原文复制、日期分隔与角色/时间元信息；流式滚动改为仅在底部时跟随，上滚后保留阅读位置并显示“有新内容 / 回到最新消息”。后续 M10.2、M10.3 均已完成。
- **M10.2 审批、证据与上下文抽屉已完成（2026-10-29）**：确认区改为聊天流内的内联事务卡（`ApprovalCard`），展示目标、工作区、模式、风险、有效期、可撤销性、计划与只读 Diff 预览；`NoteActionService.preview()` 只读计算 `create/update`、增删行数与截断提示，绝不写入，审批数据只经 `ApprovalView`（含 `ApprovalPreview`）读取，确认/取消/结果/撤销都在同一张卡内闭环；过期提案由服务层拒绝并禁用确认。工具回执升级为可展开 `ToolCard`（默认名称/状态/摘要，展开显示参数、结果预览、错误码与原因、来源列表与 `tool_result` 句柄），展开状态按消息 ID 记忆；右栏改为“执行 / 文件 / 来源 / 上下文 / 任务”标签式抽屉，文件与来源节由投影派生，可关闭、拖动宽度并通过 `ui_state.json` 恢复；窗口宽度 <940px 先收起右栏，<860px 把左栏改为画布抽屉，780px 最小尺寸下中部阅读区不被挤压。全量测试 383 → **404**。
- **M10.3 会话、输入与设置体验已完成（2026-10-29）**：迁移 v9 为会话增加自定义标题、置顶时间、未读标记与按会话草稿表（`session_drafts`）；左栏按“今天 / 昨天 / 最近 7 天 / 更早”分组，行内显示相对时间、置顶/未读/当前状态与可读名称，`⋯`、右键与键盘 `Return/Space/Menu` 均可管理会话，重命名、删除、置顶与标记未读在失败时给出反馈，生成中拒绝跨会话写入；新增 `Ctrl+N` 新建、`Ctrl+K` 搜索与 `Ctrl+,` 设置快捷键。输入区按 2–5 行自动增长，按会话保存草稿（运行中也可保存），显示字数与超长提示，并在上方展示模式、工作区与上下文胶囊；发送与停止复用同一固定宽度主按钮，停止时切换文案与状态色。空会话按陪伴/工作模式显示角色化欢迎、快捷开场与最近任务。设置中枢改为左侧分类 + 右侧内容的双栏结构，支持搜索、状态摘要与“更多”渐进展开，主题与浮窗保留底部快速入口，并新增字体缩放（100/125/150/200%）与“减少动态效果”。视觉层新增 `TYPE_TOKENS / SPACE_TOKENS / FONT_SCALES` 契约、统一焦点样式、图标工具提示与可访问名称；全量测试 404 → **431**。
- **M11 Pixso Web UI 迁移已完成（2026-10-29）**：以 Pixso 为唯一设计源，把 `theme.py` 的颜色/字号/间距 token 登记为 Pixso 变量与文字样式，并重建 6 个画板（深色三栏主界面、珍珠白空状态、设置、管理台、桌面浮窗、780×560 窄窗）。新增 `agent_framework/companion/web/`：本地 `ThreadingHTTPServer` + chunked SSE 适配器只监听 `127.0.0.1`，随机端口 + `secrets` 令牌（`?t=` 或 `X-Companion-Token`，`compare_digest` 比较），提供 `/`、`/settings`、`/console`、`/overlay` 四个页面与 `/api/*` 命令/查询；事件 `delta / tool / done / error / reset / ui / shutdown` 经订阅队列扇出，路径访问做 resolve 包含检查。前端由 Pixso 画板派生为手写 HTML/CSS/JS（`web/assets/`），覆盖三栏主界面、内联审批/工具卡、标签抽屉、设置双栏、7 页管理台与 300×250 透明浮窗；`web/shell.py` 用 QtWebEngine + QWebChannel 托管无边框主窗口（1000×700）、设置（680×560）、管理台（1040×720）与置顶透明浮窗，宿主桥接最小化/最大化/移动/打开设置与管理台/浮窗显隐；未安装 WebEngine 时回退默认浏览器。`companion.py` 默认 `--ui web`，`browser` 直接打开浏览器，`qt`/`tk` 保留为兼容入口；Windows 下设置 `QTWEBENGINE_DISABLE_SANDBOX=1` 规避 GLES 上下文导致的渲染进程退出。新增 8 个适配器回归测试（含管理台记忆清除与工作卡审批/拒绝），全量 431 → **439**。
- **M11.1 单实例唤回修复（2026-10-29）**：定位“第一次关闭后再次运行打不开”的根因——关闭主窗口只隐藏到托盘，进程继续持有数据目录锁，而 `pythonw` 启动的第二个实例只把提示写到不可见的 stderr 后以 3 退出。新增 `activation.py`：首个实例把回环端口与令牌写入 `data/.companion.endpoint`，第二个实例经 `/api/activate` 请求唤回主窗口（浏览器实例则打开指向同一服务的新标签页）；端点不可达时在 Windows 上按锁文件里的 PID 直接 `ShowWindow` 旧窗口；都失败才提示并以 3 退出，`pythonw` 下改用系统对话框。壳层通过 Qt 队列信号在 GUI 线程显示窗口；首次隐藏到托盘提示“仍在运行”，托盘不可用时关闭直接退出。新增 4 个适配器测试，全量 439 → **443**。
- **M12 Web UI 精细化（计划中，2026-10-29 登记）**：对照五个参考项目的 UI 层重新审计 `web/assets/`，登记 26 项差距（U1–U16 视觉与排布、C1–C10 信息表达与浮窗，见 4.1），拆为 M12.1 缩放正确性与视觉 token、M12.2 排版层级、M12.3 消息与运行表达、M12.4 桌面浮窗能力、M12.5 视觉纵深与密度五个切片。只动 CSS/token 与应用服务投影字段，不改执行链路与数据模型；语音、附件、Live2D 仍按 D49 的能力门控处理，不提前放控件。
- **M12.1 缩放正确性与视觉 token 已完成（2026-10-29）**：`tokens.css` 补齐 Qt `theme.py` 移植过来的间距与控制度量 token（`--sp-*`、`--ctl-*`、`--font-mono`、`--radius-pill/card`、行高与字重语义档），间距按 `--sp-scale = 0.5 + scale×0.5` 半速增长；`.pill / .segmented / .ghost / .field / .new-task / .table / .inspector-tabs / .send` 的固定 `height` 全部改为按字体度量的 `min-height`，100/125/150/200% 下不再切字；右栏与管理台标签条改为可横向滚动且下划线用 `inset` 阴影绘制不被裁掉；`@media` 不再隐藏 `[data-cmd]`，窄窗退化为图标按钮，同时响应式断点改按字号归一化（`responsiveWidth()`），200% 时 1000×700 自动收起两栏；浮窗 `--scale` 由 `/api/overlay` 下发并应用，形象 SVG 收进 `.pet` 盒内不再溢出；会话行改语义按钮并补 `role=tablist / aria-selected / aria-current / role=log / aria-live`、统一 `:focus-visible` 焦点环与管理台表格行的键盘可达。新增 `web/layout_audit.py`（Playwright 真实浏览器逐档截图 + 溢出扫描）与 17 个静态/结构断言、1 个 `/api/overlay` 载荷断言、2 个真实浏览器回归用例（无裁切溢出 + 窗口控件可达并退化为图标），全量 443 → **463**。
- **M12.2 排版层级已完成（2026-10-29）**：两端共用一份 7 档字阶（`caption 11 / meta 12 / ui 13 / body 14 / large 16 / subtitle 18 / display 24`；web 从未引用的 `--fs-title` 与 Qt 侧的 `heading` 档被这组顶部阶梯取代），Qt 侧 `TYPE_TOKENS` 同步换值并把 `stylesheet()` 里 41 处裸 `font-size` 全部改走 `_type(token)`，字典成为唯一来源（`font_scale` 后处理正则照常生效）；`SPACE_TOKENS` 补 `xl: 18` 对齐 `--sp-xl`；`ui/markdown.py` 的标题字号与浮窗气泡字号也接回字阶。Web 端把登记时计 39 处的最高频 `--fs-caption` 按角色拆散，现在四个页面样式表里是 caption 17 / meta 25 / ui 26 / body 12，正文升到 14px；行高收敛为 `--lh-body 1.7 / --lh-meta 1.5 / --lh-control 1` 三档并清零裸数值；阅读列改为按字符计量的 `--read-measure: calc(var(--fs-body) * 42)` 与派生的 `--col-measure`（`rem` 方案经核实不可用：`html` 无 `font-size`，`rem` 会解析到 UA 16px 而无视 `--scale`），实测每行 18–37 个中文字（整改前上限约 44）。新增 7 个排版静态断言（两端字阶相等、每档都被使用、行高仅三档、列宽由字号派生、几何宽度仍走 `--sp-scale`、控制度量不得引用 display 档）与 2 个真实浏览器断言（每行字数区间、聊天区占屏比例），`layout_audit` 的探针新增每行字数与顶栏/聊天区高度上报；全量 463 → **472**。
- **角色素材槽位已预留，桌宠改为 Live2D 唯一载体（2026-10-30 登记，同日按"完全替换"语义修订）**：用户自备头像/立绘与 Live2D 模型，仓库为其预留 `data/assets/<persona_id>/`（`avatar.png|webp|jpg`、可选 `background.jpg`、`live2d/model3.json`），目录名与人设包 `data/personas/<id>/` 一一对应；除两份 README 外整体 gitignore，不进版本库也不进安装包（延续 R10「角色素材仅允许用户本地导入」与技术方案阶段 5「安装包不含未授权素材」）。新增 **M13 角色素材接入**三切片：M13.1 素材槽位与静态头像、M13.2 可替换窗口背景（主窗/设置/管理台 + 主题派生自动遮罩，浮窗保持透明）、M13.3 Live2D 桌宠运行时与旧桌宠下线。**2026-10-30 用户确认的三项决定构成"完全替换"的语义**：① **不设任何回退档**——Live2D 不可用（缺素材、缺 Cubism Core、WebGL 不可用、模型损坏）时**桌宠功能即失效**，浮窗不启动并给出原因，既不退回旧程序化形象，也不放中性占位图（D58）；② **M12 先全部做完**，顺序为 M12.3 → M12.4 → M12.5 → M13.1 → M13.2 → M13.3（撤销此前"M13.1/M13.2 插在 M12.4 与 M12.5 之间"的细化）；③ **Web 透明置顶浮窗是桌面形象的唯一载体**，Qt 原生 `AvatarOverlay`、`AvatarWidget` 的 QPainter 程序化绘制、`app.js`/`overlay.js` 两份 `avatarSvg`、`build_avatar_icon()` 与 `avatar_palette()` 在 M13.3 一并删除（见 D58 与 R25）。**原桌宠形象不再做动画**：M12.4 里"纯 CSS 呼吸/眨眼待机动画"取消（D56 相应修订），M12.4 只保留把形象挂载点从两份 `avatarSvg` 抽成单一模块这一项——它是 M13.3 换掉整个载体的前提，而不是给旧形象补动画。本轮**只登记与预留目录，未改任何代码**，472 个测试基线不变。
- 测试基线 34 → 66 → 80 → 93 → 98 → 109 → 124 → 136 → 145 → 148 → 151 → 173 → 202 → 240 → 271 → 291 → 312 → 336 → 337 → 341 → 343 → 350 → 359 → 383 → 404 → 431 → 439 → 443 → 463 → 472 → 482 → 494 → **510**（M1a +8、Qt 端到端 +1、M1b +14、M2 第一批 +9、M2 第二批 FTS 切片 +14、M2 第二批剩余 +12、Qt 证据台 +1、M2 收尾角色扮演/导入防护 +5、M2.5 第一切片 +11、M2.5 导入/世界书/设定 +15、M3 分层/预算 +12、M3 工具结果落盘/回读 +9、M3.5 复查修复 +3、M3.5 召回评测 +2、工具轮次收尾 +1、M4.0 主动底座 +22、M4 主体 +29、M5 任务卡 +37、Qt 任务面板 +1、M6 流式/浮窗/托盘/状态 +31、M7 技能包 +20、M8 维护/管理台 +21、M8.5 界面改版 +24、M8.5 窗口找回 +1、提醒时间与工具 +4、界面信息架构 +2、左栏会话树 +7、M9 应用层契约 +9、M10.1 UI 投影/运行条/Markdown/滚动 +24、M10.2 审批预览/事务卡/工具展开/标签抽屉/响应式 +21、M10.3 会话字段/草稿/分组快捷键/Composer/空状态/双栏设置/可访问性/token +27、M11 Web 适配器/页面/壳层/管理台清除/工作卡 +8、M11.1 单实例唤回 +4、M12.1 token/缩放/可访问性静态断言 +18、真实浏览器缩放回归 +2、M12.2 排版契约静态断言 +7、浏览器行长与版面占比断言 +2、M12.3 C2 上下文投影数字与环形仪表 +10、C4 运行步骤时间线与 SSE 分帧 +12、C1 Markdown 子集对齐与共享样本 +16）。
- 下一阶段顺序调整为：**M4.0 主动底座 → M4 主体 → M5 任务卡 → M6 桌面形象与流式 → M7 技能包 → M8 管理与自检台 → M8.5 桌面界面改版 → M9 应用层契约 → M10.1 运行与消息 → M10.2 审批与抽屉 → M10.3 会话与设置 → M11 Pixso Web UI（均已完成）→ M12 Web UI 精细化（M12.1、M12.2 已完成，M12.3–M12.5 计划中）**；**M12 三个剩余切片全部做完后**才进入 M13 角色素材接入（桌面形象替换为 Live2D 唯一载体），再评估语音/多角色、发布准备与远程/手机端 transport，M1b 的逻辑调用 ID、更多确认工具与 OpenClaw 复用随后续模块顺带完成。
- M3、M3.5、M4、M5、M6、M7、M8、M8.5、M9、M10.1、M10.2、M10.3、M11、M12.1 与 M12.2 已完成；**M12.3 进行中（C1 Markdown 子集对齐、C2 上下文投影字段与环形仪表、C4 运行步骤时间线已于 2026-10-30 交付，剩 C3/C5/C8）**、M12.4–M12.5（浮窗能力、视觉纵深）尚未动工；语音、Live2D、多角色、社交渠道和远程/手机 transport 尚未开始。C4 顺带发现并修复了 Web 事件通道的 SSE 分块编码缺陷（D60）——修复前浏览器里的 `/api/events` 从未成功连上过。

完成度是工程估算，用于判断趋势，不作为验收标准；验收以第 7 节指标和第 5 节各模块验收条件为准。

## 1. 阶段完成度

| 阶段 | 完成度 | 状态 | 已有能力 | 主要缺口 |
|---|---:|---|---|---|
| 0 架构基线 | 85% | 部分完成 | 四种模式、风险枚举、动作提案、SQLite 持久化、策略引擎；M9 进程内应用服务、运行/事件/审批/工作区快照契约与 Qt 适配器；M11 本地 HTTP + SSE Web 适配器（回环监听、令牌、路径守卫、事件扇出） | system keychain、远程/多客户端 transport 与持久化 run |
| 1 桌面形象 + 聊天 | 97% | 部分完成 | M11 Pixso Web UI 为默认界面（三栏主界面、设置、管理台、透明浮窗、780×560 窄窗），QtWebEngine 壳层 + 浏览器回退，Qt/Tk 保留兼容入口；聊天、历史会话、确认卡、后台线程；M6 流式与停止、透明置顶浮窗、点击穿透、位置记忆、托盘、执行器回执状态气泡；M8.5 主题 token、气泡记录、工具回执卡与右栏详情面板、主窗口不置顶、左栏会话树与设置中枢；M10.1 运行条、安全 Markdown 与滚动跟随；M10.2 内联审批事务卡与只读 Diff、可展开工具回执、标签式上下文抽屉、宽度持久化与响应式收起；M10.3 会话置顶/重命名/删除/未读与日期分组、快捷键、自适应输入与按会话草稿、单一发送/停止主按钮、角色化空状态、可搜索设置、字体缩放与减少动态效果；M12.1 间距/控制度量 token、四档字号无裁切、标签条滚动、窄窗图标化窗口控制、语义按钮与焦点环、真实浏览器缩放回归；M12.2 两端共用 7 档字阶与三档行高、正文 14px、元信息按角色分档、阅读列按字符计量；M12.3 起上下文用量由投影数字渲染为环形仪表、运行条可展开为投影单点提供的步骤时间线，两端共用同一套 Markdown 语法（表格/嵌套列表/删除线/代码语言标签，Web 代码块带复制按钮） | 形象素材与语音、Live2D 尚未接入（现行程序化 SVG 形象是过渡态，M13.3 起由 Live2D 独占桌宠且不保留回退）；Web 前端的消息表达（整列表重建）与浮窗交互、视觉纵深缺失已登记为 M12.3–M12.5，M12.3 进行中（C1/C2/C4 已关闭，剩 C3/C5/C8），M12.4/M12.5 未开始 |
| 2 第一个真实任务 | 99% | 部分完成 | 搜索 → 笔记提案 → 确认 → 写入 → 回执 → 撤销；工作区快照、模式复核、`unknown`、幂等、崩溃恢复、快照撤销；按模式装配的工具注册表、LLM 原生工具循环、单工具超时与 function calling 降级；M5 工作任务卡、意图路由、计划状态机、审批与转发回执、Qt/Tk 任务面板；M7 本地技能包、元工具、模式/风险过滤、本轮工具范围约束与审计 | 逻辑调用 ID 与结果缓存、更多确认工具、多执行器路由、OpenClaw 复用同一内核 |
| 3 长期记忆 | 95% | M2 与 M2.5 已完成 | 用户原文证据、规范键、候选评分与晋升、冲突取代、纠正、遗忘墓碑、同键与派生级联、召回日志、迁移 v3；中文 2/3-gram + FTS5/BM25 召回（关键词降级、启动自愈、召回来源）；可暂停续跑的旧聊天导入与跨副本去重；证据/召回台；protected 人设入口；角色扮演/引用台词防污染；情绪快照；本地 persona pack、稳定前缀哈希、导入校验、世界书按需注入 | 向量/多模态召回、更多导出格式识别 |
| 4 情绪、人格与主动 | 75% | M4 已完成 | 情绪估计、用户纠正、历史快照；默认蕾米埃尔人设包、稳定人格/背景分层、离线角色语气；称呼/关系/边界编辑、人设包导入与来源展示；M4.0 提醒闭环、未完成话题、活动状态、纯函数门控；M4 主体状态持久化、反重复、错过未读、触发原因落库、Qt/Tk 主动面板与 60 秒轮询 | 更多主动来源与打扰阈值调优、更多人格模板 |
| 5 语音、Live2D 与发布 | 5% | 计划中（2026-10-30 登记 M13，同日确认为"完全替换"语义） | 可选依赖已安装；角色素材槽位 `data/assets/<persona_id>/` 已预留（仅目录与说明，代码未读取） | Live2D 桌宠（Web 浮窗唯一载体，不可用即失效、无回退）、静态头像与可替换窗口背景接入、旧程序化桌宠代码下线、本地 ASR/TTS、打包签名与崩溃日志 |

## 2. 已验证的现状

### 2.1 测试与环境

- 2026-10-30 运行 `.venv/Scripts/python.exe -m unittest discover -s tests -t tests`：**510 个测试通过，73.285 秒，1 个 Tk 环境跳过**（`tests/` 无 `__init__.py`，必须显式指定 `-t tests`；上一轮 494 个为 M12.3 第二子片 C4 交付后）。
- 2026-10-30 M12.3-c（C1 Markdown 子集对齐与扩展）验证：新增 16 个用例——`tests/test_companion_markdown.py` 重写为 18 个（安全 URL 三类、原始 HTML 转义、行内代码压过标记、删除线与加粗共存、链接/裸链尾部标点截断、标题 class、嵌套列表结构、引用合并、表格对齐与补列、表格误判回退为段落、代码块语言标签、空白输入、长混合回复、Qt 样式块随字号 16/24/32px 与 document 前缀、node 驱动的 21 个共享样本逐字节相等），`tests/test_companion_web_style.py` 新增 5 个静态契约（`markdown.js` 在 `app.js` 之前加载且不再有第二套语法、代码块复制按钮与语言标签接在同一次装饰里、表格/引用/列表只引用 token、转义先于渲染），`tests/test_companion_ui_theme.py` 改 2 个（QSS 的 `QLabel#bubbleBody` 基准 14px → 200% 28px；气泡正文带共享样式块，200% 下标题 32px、125% 下 20px）。真实 Chromium 在 100/150/200% 下核对同一段含表格/嵌套列表/代码/删除线的长回复：正文 14/21/28px、标题 16/24/32px、三列表格列对齐与 `table.md-table` 命中、嵌套缩进 20/25/30px、删除线 `line-through`、代码块语言标签与复制按钮（点击后 toast "已复制"、剪贴板内容等于代码原文），无溢出、无 console 报错；Qt 离屏同三档：正文 14/21/28px、标题 16/24/32px、气泡沫高 413/593/839px、表格三列与代码块样式生效。`python -m agent_framework.companion.web.layout_audit` 仍为 `Layout audit passed.`，`doctor.py` 为 `Environment check passed.`。顺带修掉一个 M12.2 残留缺陷：QSS 阻断字体继承导致气泡正文在任何字号档下都停在 9pt（`QLabel#bubbleBody` 补 `font-size: _type("body")` 后才随缩放变化）。
- 2026-10-30 M12.3-b（C4 运行步骤时间线）验证：新增 12 个用例——`tests/test_companion_app.py` 2 个（相邻同阶段折叠、失败终态记录原因）、`tests/test_companion_web.py` 2 个（原始 socket 断言每一帧 SSE 都正确分块、`/api/bootstrap` 的 `run.steps` 携带折叠后的时间线与字段名）、`tests/test_companion_web_style.py` 5 个静态契约（转义与共享阶段文案、前端不再自造"已完成"、步骤列表在 `role=status` 活性区之外、折叠按钮从缓存行重绘、尺寸全部来自 token）、`tests/test_companion_ui_theme.py` 2 个（不足两步不显示按钮与展开/收起、新一轮清空旧时间线）、`tests/test_companion_qt_ui.py` 1 个（真实离屏窗口端到端：时间线行数与投影 `steps` 相等）。浏览器侧用真实 Chromium 在 100/150/200% 下各跑一轮带工具的回合，实测 5 行、`aria-expanded=true`、列表字号 12/18/24px、行高 18/27/36px、展开上限 144/210/276px，无 console 报错、run 结束后运行条正常收束；Qt 侧离屏窗口同三档实测折叠 21/29/36px → 展开 67/75/112px、"步骤 5"、输入框不被挤出窗口。`python -m agent_framework.companion.web.layout_audit` 仍为 `Layout audit passed.`（0 problems），`doctor.py` 为 `Environment check passed.`。修复的产品缺陷见 D60。
- 2026-10-29 M12.2 验证：`layout_audit` 探针在原有溢出/控件检查之上上报 `.bubble .body` 的每行字符容量与顶栏/聊天区高度，实测 100/125/150/200% × 1000×700 为每行 23/36/33/24 字、聊天区占屏 68/62/55/42%，780×560 为 37/30/25/18 字、占屏 60/53/44/27%，20 个组合仍零裁切零溢出、28 个窗口控件全部可达；`tests/test_companion_web_style.py` 新增 7 个排版断言（Web `--fs-*` 字阶与 `theme.TYPE_TOKENS` 逐档相等、每一档至少被引用一次、行高只有 control/meta/body 三档且样式表零裸 `line-height` 数值、`--read-measure` 按 `--fs-body` 计量且 `.chat-inner`/`.composer-wrap` 引用 `--col-measure`、四个样式表零 `max-width: calc(Npx`、rail 与滚动条这类家具宽度仍走 `--sp-scale`、`--ctl-*` 不得引用 display 档），`tests/test_companion_web_layout.py` 新增每行 16–45 字与聊天区 ≥25% 两个断言，并把"宽窗必留文字标签"的旧断言改为"原始宽度与字号归一宽度都要够"（200% 时 1000px 只有 500% 等效空间，退化为图标是正确行为）。Qt 侧 `tests/test_companion_ui_theme.py` 的 `font_px("display", 200) == "48px"`、`spacing("xl") == 18` 与"QSS 字号集合 == TYPE_TOKENS 值集合"三条契约在字面量全部改走 `_type()` 后仍成立；`tests/test_companion_qt_ui.py` 41 个离屏用例（含 200% 档位下的 `font-size: 28px`、发送按钮宽度与输入框行高断言）全部通过。
- 2026-10-29 M12.1 验证：新增 `agent_framework/companion/web/layout_audit.py`，用 Playwright 驱动真实 Chromium 以 100/125/150/200% × 主界面 1000×700 与 780×560、设置 680×560、管理台 1040×720、浮窗 300×250 共 20 个组合逐页截图并扫描“元素自身溢出/超出视口/窗口控件命中区不足”，`python -m agent_framework.companion.web.layout_audit --out data/layout_audit` 输出 `Layout audit passed.`（截图落在被 gitignore 的 `data/layout_audit/`）。`tests/test_companion_web_layout.py` 把该矩阵纳入回归（未安装 playwright 时跳过），并断言 780px 下最小化/关闭退化为图标、1000px 下保留文字标签且四档全部可达（后一条在 M12.2 改为"原始宽度与字号归一宽度都要够"）；`tests/test_companion_web_style.py` 17 个静态断言守住 token 契约（`--sp-*`/`--ctl-*` 存在且由 `--fs-*` 推导、四个页面样式表零固定 `height` 与裸像素间距、tab 条可滚动、`@media` 不再隐藏 `[data-cmd]`、响应式断点按字号归一化、焦点环未被 `outline:none` 抹掉、HTML/JS 的 `role/aria` 语义）；`tests/test_companion_web.py` 新增 `/api/overlay` 载荷携带 `font_scale` 的断言。`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe doctor.py` 仍为 `Environment check passed.`（`Instance lock`/`Skills`/`Tk fallback` 为既有环境 WARN）。
- 2026-10-29 M11 验证：新增 `tests/test_companion_web.py` 8 个用例（页面与令牌、`/api/ui` 持久化与广播、9 个管理台查询接口形状、中文情绪纠正 `开心 → positive` 且回显 `mood_text`、回合经 SSE 订阅队列流式返回并落库、静态资源可达且 `..` 路径逃逸被拒、管理台 `purge` 真正删除记忆、工作卡在启动快照暴露审批字段且拒绝后回到 `NORMAL`）；全部使用真实离线运行时与临时数据目录。浏览器端逐页核对主界面、设置、管理台与浮窗；Qt 壳层在 1000×700 下核对三栏布局、窗口按钮、模式切换、右栏开关与宿主驱动的设置窗口。`companion.py --ui web` 默认启动，`--ui browser` 直接打开浏览器，`--ui qt` 仍可用。
- 2026-10-29 M11.1 验证：在真实壳层实例上端到端跑通“第二次启动 → `/api/activate` → 窗口重新出现在前台”（端点文件返回 `shown=true`）；`tests/test_companion_web.py` 新增 4 个用例覆盖令牌校验、无壳层时返回页面 URL、端点文件驱动唤回与失效端点快速放弃；同时确认系统 `Temp` 目录受沙箱 ACL 限制导致锁文件创建失败，与产品逻辑无关，验证改用仓库内临时目录。
- 2026-10-29 M10.3 验证：`tests/test_companion_store.py` 新增重命名/置顶/未读往返、草稿生命周期、删除会话连带消息/草稿/记忆与 v8→v9 迁移 5 个用例；`tests/test_companion_runtime.py` 新增切换清除目标未读、重命名/置顶/草稿按会话隔离、删除当前/最后会话后的回退用例；`tests/test_companion_ui_theme.py` 42 → 50（相对时间与日期分组、会话行自定义标题/置顶/未读/可读名称、菜单与键盘触发、反向操作、模式化空状态与最近任务、减少动态效果不抢滚动、类型/间距 token 契约、字体缩放覆盖全部字号）；Qt 用例 30 → 41（会话操作与 `Ctrl+N`/`Ctrl+K`、Composer 2–5 行增长与字数提示、按会话草稿、Shift+Enter/输入法不误发、模式/工作区/上下文胶囊、空状态快捷开场与最近任务、设置分类/搜索/摘要、字体缩放与减少动态效果持久化、主控件可访问名称/工具提示/快捷键、1366×768 与 1920×1080 离屏截图）。
- 2026-10-29 M10.2 验证：`tests/test_companion_file_actions.py` 4 → 8（新建/更新预览只读、Diff 增删与上下文行、长内容截断行数与单行字符上限、工作区身份失效报错）；`tests/test_companion_app.py` 12 → 16（`ApprovalView` 预览字段与序列化、过期提案服务层拒绝确认、跨会话审批不可见/不可确认）；`tests/test_companion_tools.py` 17 → 19（工具 running/done 事件携带参数摘要、来源与结果句柄，失败事件携带错误码与原因）；`tests/test_companion_ui_theme.py` 33 → 42（工具卡参数/来源/句柄展开与折叠记忆、失败卡错误码、审批卡 Diff/元数据/过期禁用/结果与撤销闭环、右栏五个标签与焦点、文件/来源节派生、抽屉宽度存取与夹取）；Qt 用例 28 → 30（窄窗口先收右栏再收左栏、左栏抽屉开合与恢复、过期提案在卡内禁用确认且不写入、端到端确认/撤销在事务卡内闭环）。
- 2026-10-28 M10.1 验证：`tests/test_companion_app.py` 9 → 12（事件 stage/sequence 序列、投影消息与活跃 run、待审批投影），新增 `tests/test_companion_markdown.py` 8 个（原始 HTML 转义、行内代码/加粗、链接协议与引号转义、代码块与未闭合围栏、列表/标题/引用、空输入）；`tests/test_companion_ui_theme.py` 21 → 33（Markdown 安全渲染、复制原文、日期分隔、上滚后不跟随与“有新内容”跳转按钮、运行条开始/乱序丢弃/完成/失败/等待审批/耗时/重置、样式表新选择器）；Qt 用例 26 → 28（运行条随流式进入“生成回答”并在完成后收束、停止后标记“已取消”）。
- 2026-10-28 M9 验证：新增 `tests/test_companion_app.py` 9 个用例（事件顺序与序号、run id/generation id、同会话 active-run 守卫、生成中变更拒绝、停止传播、无 done 拒绝完成、done 前未落库拒绝完成、启动时工作区快照、审批 digest/TTL 与已处理审批不可重复确认）；Qt 26 个用例通过，其中端到端用例改为显式传入 application service，其余 smoke 用例继续传入 runtime 以验证兼容；`compileall` 通过。`PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe doctor.py` 输出 `Environment check passed.`，数据库 `user_version=8/8`、FTS/锁/主动/任务/活动探测正常；`Backups` 与 `Skills` 为 WARN，Tk fallback 因本机 Tcl 8.6.15 初始化脚本要求 8.6.13 而告警，Qt 主界面不受影响。
- 2026-10-27 提醒能力与窗口层级验证：`tests/test_companion_proactive.py` 新增钟点解析与 `resolve_when` 两个用例（相对时长含秒与“半小时”，钟点按本地时区、已过今日则顺延次日，ISO 字面量优先于钟点匹配）；`tests/test_companion_tools.py` 新增 `set_reminder` 脚本化调用（落库 1 条待办、工具消息回读“已创建提醒”）与无法识别时间时不落库两个用例，四种模式的工具范围断言同步更新；Qt 用例新增主窗口不含 `WindowStaysOnTopHint` 断言。真机复核：重启实例后主窗口 `WS_EX_STYLE` 为 `0x0`（无 `WS_EX_TOPMOST`、无 `WS_EX_TOOLWINDOW`）、仍保留 `WS_MINIMIZEBOX`，全屏前台程序可以盖住主窗口，`doctor.py` 全项通过。真实模型 `sensenova-6.8-flash-lite` 冒烟：本地规则解析不出的“20 分钟之后我可能有空，到时候提醒我拿快递”由模型调用 `set_reminder` 落库（`拿快递 / 20 分钟后 / pending`），回复与库中时间一致；完全没给时间的“散会以后大概一个钟头再提醒我…”模型选择先追问会议时间，未落库。
- 2026-10-27 M8.5 复查（窗口找回）：Qt 界面用例 19 → 20，新增 `test_header_minimize_keeps_window_recoverable`（主窗口不是 `Qt.Tool`、带最小化按钮提示与头像窗口图标、点“最小化”只最小化不退出、最小/隐藏/还原三态下托盘文案分别为“还原主窗口 / 隐藏主窗口 / 显示主窗口”）；离屏平台确认 `showMinimized()` 后 `isMinimized()` 为真且几何不变、`showNormal()` 可恢复隐藏态，`changeEvent` 收/发状态变更顺序正确。任务栏按钮与真实还原尺寸仍需人工在桌面确认。
- 2026-10-27 M8.5 验证：Qt 界面用例 16 → 19（默认尺寸与三栏结构、左栏/右栏折叠持久化、主题切换与样式表、侧边对话框几何规则单测），新增 `tests/test_companion_ui_theme.py` 21 个（token 契约、QSS 选择器、气泡与回执卡、流式光标、签名相同不重建、文本变短后高度回收、详情面板各节派生与读取失败降级、`ui_state.json` 新字段校验、`clamp_position`）。离屏截图核对深色/珍珠白两套布局一致；离屏平台无字体数据库，中文以方框显示，只用于核对几何与配色。
- 2026-10-28 M8.5 左栏会话树验证：Qt 用例 22 → 26（左栏目录名与活动会话行、空会话去重的新建、行点击切换并写入 `active_session`、搜索过滤与“没有匹配”空态、生成中拒绝切换、新窗口恢复上次会话）；`test_companion_store.py` +1（首条用户消息作标题、末条角色/内容、空会话计数）；`test_companion_runtime.py` +1（切换会话同步历史、模式与 `proactive_service` / `work` 作用域，未知/相同会话返回 False）；`test_companion_ui_theme.py` +1（`active_session` 往返与空白回退），样式表契约改为 `railAction` / `railSearch` / `workspaceRow` / `railMini` / `sessionRow` / `sessionActive`；全量 350 个通过；离屏截图核对左栏（新的任务、搜索框、工作区行与 `…`/`+`、缩进会话列表、活动行高亮、底部设置）与顶部形象/状态布局。
- 2026-10-28 M8.5 信息架构验证：Qt 用例 20 → 22，新增左栏工作目录/会话摘要用例（摘要话题、条数、最新一句，且六个面板入口不再挂在主窗口）与设置中枢用例（六项入口、主题切换就地刷新、浮窗开关、点击记忆行后设置关闭并打开记忆侧栏）；样式表契约补 `sessionCard` / `settingsRow` / `settingsButton` 三个选择器；全量 343 个通过；离屏截图核对左栏与设置页几何。
- 2026-10-27 M8 验证：新增 `tests/test_companion_maintenance.py`（18 个）与 `tests/test_companion_console.py`（3 个）；`doctor.py` 全项 OK（数据库 v8、FTS ready、无缺失索引、实例锁空闲；无备份时 `Backups` 为 WARN，首次迁移或手动备份后转为 OK）；离屏管理台 7 页均可加载并显示记忆/召回/失败记录，技能审计密文已在落库前脱敏，设置页可完成备份与导出。
- 2026-10-27 M7 验证：新增 `tests/test_companion_skills.py`（20 个）；`doctor.py` 全项 OK（数据库已迁移到 v8、FTS ready；`Skills` 无技能目录时提示可选）；真实库 `data/companion.db` 迁移后 14 条消息保留。
- 2026-10-27 M6 验证：新增 `tests/test_companion_streaming.py`（24 个）与 Qt 流式/浮窗/托盘 7 个用例；`doctor.py` 全项 OK（数据库仍为 v7、FTS ready；`Proactive state` 与 `Work state` 正常）；Qt 16 个界面测试全部通过（离屏平台）。
- 2026-10-27 M5 验证：`doctor.py` 全项 OK（新增 `Work state` 检查），真实库 `data/companion.db` 已迁移到 `user_version=7` 且 12 条消息保留；`work_tasks` / `work_receipts` 表已创建且为空。
- 2026-10-27 M4 主体验证：`doctor.py` 全项 OK（新增 `Proactive state` 检查），真实库 `data/companion.db` 已迁移到 `user_version=6` 且 12 条消息保留；`proactive_state` / `proactive_events` 表已创建且为空。
- 2026-10-27 M4.0 验证：`doctor.py` 全项 OK（含新增 `Activity probe`），真实库 `data/companion.db` 已迁移到 `user_version=5` 且 12 条消息保留；系统 Python 3.8.8 下当时 173 个测试同样通过（8 个 Qt 与 1 个 Tk 测试跳过）。
- 2026-10-27 召回评测：`python -m agent_framework.companion.eval_recall` 输出 `cases=26 limit=5 hit@1=1.0000 hit@5=1.0000 mrr=1.0000`。
- 2026-10-26 复查重跑：**145 个测试通过，14.68 秒**；`doctor.py` 全项 OK（数据库 `user_version=4`、FTS `ready`）；与本文档一致。随后 M3.5 修复 3 个代码项，全量升至 148 个。
- 基线为 2026-10-23 的 34 个测试；M1a 新增 8 个执行安全回归测试，Qt 端到端新增 1 个，M1b 新增 14 个工具循环测试，M2 第一批新增 9 个记忆生命周期测试，M2 第二批 FTS 切片新增 14 个（`tests/test_companion_memory_fts.py`），M2 第二批剩余新增 12 个（`tests/test_companion_memory_batch2.py`：派生级联、protected、证据台、导入、摘要墓碑），Qt 证据台新增 1 个，M2 收尾新增 5 个角色扮演/导入防护测试，M2.5 第一切片新增 11 个（`tests/test_companion_persona.py`：稳定哈希、路径校验、提示分层、隔离、删除 persona 不影响记忆），M2.5 第二切片新增 15 个（导入/预览/覆盖/大小/世界书 9 个，运行时设定/世界书/导入 4 个，Qt 人设面板 2 个），M3 第一切片新增 12 个（`tests/test_companion_context.py`：稳定哈希跨模式一致、分层顺序、工具排序、预算窗口、抽取式摘要、checkpoint 复用、迁移 v4），M3 工具结果守卫新增 9 个（`tests/test_companion_tool_results.py`：16KB 落盘、UTF-8 字节阈值、预览不含尾部、分段回读、会话隔离、越界/坏句柄拒绝、重启后可读、未回读提示），M3.5 新增 3 个（压缩轮次只记 1 条召回、回复引用记忆时 `used_in_reply=1`、中文单字查询走关键词兜底），M3.5 召回评测新增 2 个（用例形状覆盖、hit@5/MRR 基线），工具轮次收尾新增 1 个（末轮工具后仍能拿到最终回答，另拆出无回答时的兜底用例），M4.0 新增 22 个（`tests/test_companion_proactive.py`：提醒到点/完成/忽略、相对时间解析、未完成话题的未回复/失败回复/过期排除、活动状态分类、门控六条规则、空来源不发言、提醒与话题来源可追溯、运行时提醒入口与迁移 v4→v5），M4 主体新增 28 个服务/单元测试与 1 个 Qt 面板测试（`tests/test_companion_proactive_service.py`：设置持久化与校验、送达/宽限/错过、暂停不消费、跨天重置、未回复清零、各门控分支、半衰期与 URL 去重、提醒豁免、从重复回退、记忆来源 opt-in、事件未读/部分已读、运行时 API 往返），M5 新增 37 个（`tests/test_companion_work.py`：路由规则/注入/超时、任务卡与只追问一次、审批安全/超时/修改/拒绝、回执 unknown 终态、崩溃恢复、运行时 API）与 1 个 Qt 任务面板测试，M6 新增 31 个（`tests/test_companion_streaming.py`：标记过滤/跨分片、流式事件与 generation、工具事件、停止与迟到分片、错误事件、无流式降级、离线与搜索分支、`ui_state.json` 往返与损坏容错、位置夹取五类、桌面状态来源与文案；Qt 侧：透明置顶/点击穿透/命中检测、位置记忆与重启恢复、托盘不可用守卫与菜单同步、停止后不再追加、旧 generation 忽略），M7 新增 20 个（`tests/test_companion_skills.py`：frontmatter inline/块列表、字段/名称/effectKind/正文与参考大小校验、风险推导模式、未知工具/风险越界/模式冲突/参考缺失拒绝、参考文件分段与越界拒绝、无代码发现技能、模式过滤、元工具返回与审计、未知技能拒绝、本轮工具范围收窄与下一轮恢复、审计跨重启保留、迁移 v7→v8），M8 新增 18 个维护测试（`tests/test_companion_maintenance.py`：完整性报告/缺失索引、备份清单与校验、损坏/更新版本拒绝、恢复与恢复前安全备份、导出 zip 内容与排除密钥、按记忆树/会话/全量删除、迁移前快照、单实例锁同进程重入与跨进程互斥、脱敏、管理查询）与 3 个离屏管理台用例（`tests/test_companion_console.py`：7 页与记录、执行审计脱敏、设置页备份/导出/重启提示），M8.5 界面改版新增 21 个（`tests/test_companion_ui_theme.py`）与 5 个 Qt 用例（三栏结构、折叠持久化、主题切换、侧边对话框几何、最小化找回），提醒能力补齐新增 4 个（`test_companion_proactive.py` 的钟点解析与 `resolve_when`、`test_companion_tools.py` 的 `set_reminder` 成功落库与时间不可识别拒绝）。
- `.venv` 使用 **Python 3.12.10**；PySide6 6.12.0、openai 3.37.0 已安装；SQLite 3.45.3（含 FTS5/BM25）。
- `requirements-optional.txt` 中的 PyAutoGUI、pywinauto、pygame、Pillow、Playwright 已安装，但尚未接入执行器。
- 测试覆盖：Agent 冒烟、OpenAI 兼容服务、模式策略、文件动作、存储、运行时、失败路径、执行安全、工具循环（含末轮工具后的收尾回答）、Qt/Tk UI、FTS5 召回与降级、召回评测基线、派生遗忘传播、旧聊天导入与去重、证据台数据、角色扮演与引用台词防污染、persona pack 稳定哈希/导入校验/世界书检索与角色/用户事实隔离、分层上下文/预算/摘要 checkpoint、大工具结果落盘与分段回读、主动状态持久化/门控/反重复/错过未读/事件触发原因与 Qt 主动面板、工作任务卡/计划状态机/审批安全/回执终态/崩溃恢复与 Qt 任务面板、流式事件与停止/迟到分片、控制标记跨分片过滤、桌面浮窗点击穿透与位置记忆、托盘守卫与菜单同步、主窗口最小化与隐藏/最小化/还原三态找回、执行器回执状态气泡、技能清单校验/模式风险过滤/参考文件分段/本轮工具范围与审计、备份校验/恢复/导出/彻底删除/迁移前快照/单实例锁/日志脱敏与 Qt 管理台、会话摘要查询/切换服务作用域/左栏会话树与搜索过滤、应用层事件顺序与落库完成契约。
- **已知环境限制（DSH 沙箱）**：在 DSH workspace-write 沙箱内无法运行测试套件——Python 3.12 在 Windows 上会为 `tempfile.mkdtemp()`（0o700）生成仅所有者的私有 DACL，沙箱受限令牌的第二次访问检查必然失败（WinError 5）。这是沙箱与 Python 的交互问题，不是项目回归；测试需在沙箱外（普通终端）运行。`doctor.py` 的 Write access/Playwright 项在沙箱内同样会误报。

### 2.2 代码能力

| 能力 | 主要位置 | 验证 |
|---|---|---|
| 四种模式与风险策略 | `agent_framework/companion/policy.py` | `tests/test_companion_policy.py` |
| 模式化工具注册表与原生工具循环 | `agent_framework/companion/tools.py`、`runtime.py` | `tests/test_companion_tools.py` |
| 证据链记忆、候选晋升、纠正、遗忘墓碑、派生级联与角色扮演防污染 | `agent_framework/companion/memory.py`、`store.py` | `tests/test_companion_memory.py`、`tests/test_companion_memory_batch2.py` |
| 中文 2/3-gram 预分词 + FTS5/BM25 召回、关键词降级、索引自愈、单字查询兜底 | `agent_framework/companion/textseg.py`、`memory.py`、`store.py` | `tests/test_companion_memory_fts.py` |
| 旧聊天导入：成本预估、暂停续跑、跨副本去重、遗忘过滤、角色扮演会话跳过抽取 | `agent_framework/companion/importer.py`、`runtime.py` | `tests/test_companion_memory_batch2.py` |
| 证据/召回台与 protected 人设入口 | `memory.py`、`ui/qt_app.py`、`ui/tk_app.py` | `tests/test_companion_memory_batch2.py`、Qt/Tk UI 测试 |
| 本地 persona pack、稳定前缀 SHA-256、导入校验、世界书按需注入、角色/用户事实隔离 | `agent_framework/companion/persona.py`、`runtime.py`、`ui/qt_app.py`、`ui/tk_app.py`、`data/personas/remielle/` | `tests/test_companion_persona.py`、`tests/test_companion_persona_pack.py` |
| 分层上下文、字符预算、抽取式 checkpoint 压缩 | `agent_framework/companion/context.py`、`runtime.py`、`store.py` | `tests/test_companion_context.py` |
| 大工具结果落盘、会话隔离句柄、分段回读与“未回读”守卫 | `agent_framework/companion/tool_results.py`、`runtime.py` | `tests/test_companion_tool_results.py` |
| 提醒闭环、未完成话题派生、ctypes 活动状态机、纯函数门控与空来源退出 | `agent_framework/companion/proactive/`、`runtime.py` | `tests/test_companion_proactive.py` |
| 主动状态持久化、送达/错过、反重复、记忆来源、事件触发原因与 Qt/Tk 面板 | `agent_framework/companion/proactive/service.py`、`repeat.py`、`compose.py`、`runtime.py`、`ui/qt_app.py`、`ui/tk_app.py` | `tests/test_companion_proactive_service.py`、`tests/test_companion_qt_ui.py` |
| 意图路由（规则 + 可选模型、12 秒上限、注入守卫）、任务卡、计划状态机、审批与转发回执 | `agent_framework/companion/work/router.py`、`service.py`、`runtime.py`、`ui/qt_app.py`、`ui/tk_app.py` | `tests/test_companion_work.py`、`tests/test_companion_qt_ui.py` |
| 数据库完整性/迁移版本/索引/FTS/备份/实例锁自检 | `doctor.py`、`maintenance.py` | 手动运行验证、`tests/test_companion_maintenance.py` |
| 备份/校验/恢复、zip 导出、记忆树/会话/全量彻底删除、迁移前快照与单实例锁 | `agent_framework/companion/maintenance.py`、`companion.py` | `tests/test_companion_maintenance.py` |
| 凭据脱敏（技能审计、执行失败、工作回执） | `agent_framework/companion/redaction.py`、`store.py`、`runtime.py` | `tests/test_companion_maintenance.py` |
| Qt 管理台（记忆/召回/候选/执行审计/情绪/失败/设置 7 页） | `agent_framework/companion/ui/console/`、`ui/qt_app.py`、`ui/tray.py` | `tests/test_companion_console.py` |
| Web UI 适配器（本地 HTTP、SSE、令牌、页面与 `/api/*`） | `agent_framework/companion/web/server.py`、`web/assets/`、`web/shell.py` | `tests/test_companion_web.py`、浏览器与 Qt 壳层冒烟 |
| 单实例锁与第二次启动唤回（端点文件、`/api/activate`、Windows 按 PID 显示窗口） | `agent_framework/companion/activation.py`、`maintenance.InstanceLock`、`companion.py`、`web/shell.py` | `tests/test_companion_web.py`（4 个唤回用例）与真实壳层端到端 |
| 提案 → 确认 → 执行 → 回执 → 撤销 | `agent_framework/companion/runtime.py`、`file_actions.py`、`store.py` | `test_companion_runtime.py`、`test_companion_file_actions.py` |
| 执行账本、幂等确认、`unknown` 与崩溃恢复 | `store.py`（`execution_ledger`、`claim_execution`、`recover_incomplete_executions`）、`runtime.py` | `test_companion_execution.py` |
| 提案工作区快照、TTL 与确认复核 | `file_actions.py`、`runtime.py` | `test_companion_execution.py` |
| 工作区守卫、路径逃逸与链接检查 | `agent_framework/companion/file_actions.py` | `test_companion_file_actions.py` |
| 搜索（Bing RSS / DuckDuckGo HTML） | `agent_framework/companion/search.py` | `test_companion_runtime.py` |
| 本地记忆与情绪 | `agent_framework/companion/memory.py`、`affect.py` | `test_companion_store.py`、`test_companion_runtime.py` |
| Qt 兼容桌面界面（`--ui qt`） | `agent_framework/companion/ui/qt_app.py` | `test_companion_qt_ui.py` |
| 应用服务命令/运行/事件/审批契约与 Qt 适配 | `agent_framework/companion/app/`、`ui/stream.py`、`ui/qt_app.py` | `tests/test_companion_app.py`、`tests/test_companion_qt_ui.py` |
| 主题 token 与 QSS、气泡/工具回执卡、右栏只读详情面板 | `agent_framework/companion/ui/theme.py`、`ui/chat_view.py`、`ui/inspector.py`、`ui/state.py` | `tests/test_companion_ui_theme.py`、`tests/test_companion_qt_ui.py` |
| Tk 兼容界面 | `agent_framework/companion/ui/tk_app.py` | `test_companion_ui.py` |
| 旧版 ChatAgent 与工具列表 | `agent_framework/agent.py`、`tools.py`、`llm.py` | `test_agent.py` |
| OpenClaw / 微信桥 | `agent_framework/service.py`、`agent_service.py` | `test_service.py` |
| 失败路径 | 运行时与文件动作 | `test_companion_failures.py` |

### 2.3 真实数据状态

2026-10-29 检查 `data/companion.db`：

| 表 | 行数 |
|---|---:|
| sessions | 1 |
| messages | 2 |
| affect_states | 0 |
| action_proposals | 0 |
| action_receipts | 0 |
| execution_ledger | 0 |
| memories | 0 |
| memory_evidence / candidates / events / tombstones | 0 |
| memory_relations | 0 |
| recall_log | 1 |
| context_checkpoints | 0 |
| experiences | 0 |
| import_jobs / import_messages | 0 |
| workspace_grants | 1 |
| reminders | 0 |
| proactive_state / proactive_events | 1 / 0 |
| work_tasks / work_receipts | 0 / 0 |
| skill_audit / session_drafts | 0 / 0 |

`PRAGMA user_version = 9`，v3–v9 迁移均已在真实库完成（v5 `reminders`、v6 主动状态/事件、v7 任务/回执、v8 `skill_audit`、v9 会话标题/置顶/未读与 `session_drafts`）；`memory_fts` 行数为 0 与 `memories` 为空一致，索引随写入自动维护、启动时自愈。

结论：真实 UI 端到端已由 Qt 自动化测试与 `tests/test_companion_web.py` 覆盖（临时数据库），但 `data/companion.db` 仍只验证过聊天与情绪路径，**搜索 → 确认 → 写入 → 回执 → 撤销尚未在真实用户数据中留下记录**。端到端能力已可回归，真实使用数据仍待日常运行积累。

## 3. 缺口清单

### 3.1 P0：执行可信度（M1a 已全部关闭）

| 编号 | 问题 | 影响 | 修复模块 | 状态 |
|---|---|---|---|---|
| P0-1 | 提案创建时记录工作区，执行时重新读取“当前工作区” | 确认后切换工作区，写入可能落到另一个目录 | M1a | 已关闭：确认时比对当前工作区与快照根路径，执行只读快照 |
| P0-2 | 确认时校验 `proposal.mode`，不校验当前模式 | 用户已切回 Chat/Quiet，旧提案仍可按高权限执行 | M1a | 已关闭：确认时按当前模式重新运行 `decide_tool_policy` |
| P0-3 | 执行状态机不会产生 `unknown` | 超时、崩溃、进程被杀后无法对账，只能猜成功或失败 | M1a | 已关闭：`ActionResult.status` 支持 `unknown`，恢复与不确定副作用均映射 |
| P0-4 | 撤销绑定当前工作区，而不是回执快照 | 切换工作区后无法撤销，或撤销到错误位置 | M1a | 已关闭：撤销使用回执内的工作区身份与目标路径 |
| P0-5 | `idempotency_key` 已建模但未参与去重 | 重复确认、UI 重试或重启后可能重复写入 | M1a | 已关闭：`execution_ledger` UNIQUE + 回执按 `action_id` 唯一，重复确认返回同一回执 |

### 3.2 P1：能力深度

| 问题 | 影响 | 修复模块 |
|---|---|---|
| Companion 的 LLM 没有工具列表，搜索/笔记靠正则触发 | 模型不能组合工具，稍复杂的任务就退化 | M1b（核心已关闭：模型可原生调用 `search_web` / `write_note`；显式搜索保留正则快路径） |
| 工具无统一超时；不支持 function calling 的模型没有自动降级 | 本地工具可能阻塞；部分兼容模型只能报错 | 已关闭：工具级超时 + 首次工具调用失败自动降级（M1b 收尾） |
| 记忆是正则抽取 + 关键词重叠召回，无证据、无候选、无召回日志 | 无法解释“为什么记得”，用户纠正和遗忘不彻底 | M2 第一批已关闭：用户原文证据、候选评分、纠正取代、召回日志、遗忘墓碑 |
| 无 FTS5/BM25，中文短记忆召回质量有限 | 长期使用后“记得但不准” | 已关闭（M2 第二批第一切片，2026-10-26）：中文 2/3-gram 预分词 + FTS5/BM25 候选召回，异常自动降级关键词 |
| 遗忘只处理单条，不向派生经历/反思传播 | 用户忘记后仍可能被间接召回 | 已关闭（M2 第二批，2026-10-26）：`memory_relations` 派生图 + 递归遗忘，经历同步失效 |
| 旧聊天无法导入，遗忘后重新导入可能复活 | 历史记忆迁移成本高，遗忘不可信 | 已关闭（M2 第二批，2026-10-26）：成本预估、暂停续跑、跨副本去重、墓碑过滤 |
| 记忆不可解释，无 UI 查看证据/召回 | 用户无法判断“为什么记得” | 已关闭（M2 第二批，2026-10-26）：证据/召回台与 protected 人设入口 |
| 旧聊天中的角色扮演或引用台词可能被误存为用户事实 | 角色设定污染真实用户画像，且难以解释来源 | 已关闭（2026-10-26）：显式角色扮演标记、引用台词与假设句不入库；导入按标题与开始/结束标记维护状态 |
| 人格、背景与用户记忆混在同一层 | 角色资料挤占召回预算，且容易把角色经历当成共同经历 | 已关闭（M2.5，2026-10-26）：稳定前缀与 persona pack、角色/用户事实隔离 |
| 没有主动调度器，只有被动回复 | “陪伴”缺少时间感与主动性 | 已关闭（M4 主体，2026-10-27）：状态持久化、反重复、错过未读、触发原因与 Qt/Tk 面板已接入；剩余为更多来源与阈值调优 |
| UI 无托盘、流式、停止、位置记忆 | 长时间使用体验不完整 | 已关闭（M6，2026-10-27）：流式与停止、透明浮窗、位置记忆、托盘与状态气泡 |
| 文档把部分规划写成已实现 | 后续开发和验收容易误判 | 已处理（M8，2026-10-27）：同步 README、使用说明与进度文档至 v1.19，并回填 M2.5/M6/M8 状态 |
| OpenClaw 桥仍走旧版 ChatAgent，与 Companion 不共享状态 | 微信端和桌面端像两个产品 | M1b 后期 |
| 自动化依赖已安装但未接入 | 依赖体积与安全面扩大，能力却未兑现 | 阶段 5 前清理或接入 |

### 3.3 P2：产品面扩展

- 多执行器路由与并行任务（M5 已完成单执行器任务卡，2026-10-27）。
- `SKILL.md` 技能包（M7）。
- 统一管理台、迁移与自检（M8 已完成，2026-10-27）。
- Web 前端的排版层级、消息表达与浮窗交互整改（M12.3–M12.5 进行中，2026-10-29 登记，见 4.1；M12.1 缩放正确性与间距/度量 token、M12.2 两端共用字阶与阅读列宽已完成，2026-10-29；M12.3 的 C2 上下文投影数字与 C4 运行步骤时间线已完成，2026-10-30）。
- 语音、Live2D、发布与签名（阶段 5）。

### 3.4 清理项

- ~~根目录存在误生成的 `$null` 文件~~ 已确认为 GBK 编码的 cmd 报错残留并删除（2026-10-26）。
- README 中 Python 版本表述不一致，已随 v1.3 更新修正。
- 每次功能落地后同步 README 的“已实现/规划中”标记，避免文档漂移。
- M1a/M1b/M2 第一批成果已于 2026-10-26 分组提交（`f6f4647` 代码、`b6bdbe9` 文档），git 可回滚承诺兑现。
- M2.5 已提交（`49f9fc6`）；**M3 与 M3.5 全部改动已于 2026-10-27 提交（`23c7837`）**：新增 `context.py`、`tool_results.py`、`eval_recall.py` 与 3 个测试文件，修改 `runtime.py`、`store.py`、`memory.py`、UI 与文档。工作区仅剩未跟踪的 `seethrough_output.psd`。
- `artifacts/` 下 11 张图片（约 8MB）未被 `.gitignore` 覆盖，且与代码无引用关系，需归入忽略规则或迁移到素材目录。

### 3.5 M3 复查发现（2026-10-26，待办）

| 编号 | 问题 | 影响 | 归属 | 状态 |
|---|---|---|---|---|
| M3R-1 | 触发压缩时 `_build_prompt` 调用 `_compose_system` 两次，`memory.context_lines` 每次都会写 `recall_log` | 每轮召回证据翻倍，污染“召回可解释性”指标 | M3.5 | 已修复（2026-10-26）：召回在 `_build_prompt` 只取一次，压缩重排复用 |
| M3R-2 | `recall_log.used_in_reply` 从写入到读取恒为 0，测试还以 0 断言 | “召回是否被回复采纳”不可度量 | M3.5 | 已修复（2026-10-26）：回复生成后按记忆原文/2-gram 重合更新 |
| M3R-3 | `textseg.py` 只切 2/3-gram，中文单字查询（如“茶”“猫”）不命中 | 短查询召回为空（R2 已知边界的延伸） | M3.5 | 已修复（2026-10-26）：单字查询绕过 FTS，关键词兜底支持单字子串 |
| M3R-4 | M4 计划的来源“提醒、未完成话题”没有任何数据底座：无提醒表、无话题追踪，记忆表为 0 | 主动引擎按现设计可能永远不触发 | M4.0 | 已补齐（2026-10-27）：迁移 v5 `reminders` 表与闭环；未完成话题从消息派生；M4 主体已接入 opt-in 记忆来源（48h 内被回复采用的已确认记忆） |
| M3R-5 | 145 个测试全部使用假 LLM/离线客户端，真实模型（sensenova-6.8-flash-lite）链路未验证 | function calling、落盘回读、压缩续聊只在离线路径验证 | M3.5 后手动冒烟 | 已冒烟（2026-10-27）：function calling 与“搜索→落盘→回读→回答”通过；压缩后真实模型续聊未覆盖 |
| M3R-6 | M4 计划依赖 `psutil`，当前环境未安装 | 活动状态机无法按原方案实现 | M4.0 | 已决（2026-10-27）：采用 `ctypes`，不新增依赖；`doctor.py` 显示活动探测 |
| M3R-7 | 单实例假设（R8）未加运行锁 | 同 `data` 目录双开会把在途执行误标 `unknown` | M8 | 已修复（2026-10-27）：启动入口默认获取跨进程文件锁，第二个实例提示占用并以退出码 3 结束；`--allow-multiple` 可显式跳过；`doctor.py` 显示锁状态 |
| M3R-8 | `_tool_loop` 用满 `MAX_TOOL_ROUNDS` 后直接返回“轮次达到上限”，最后一轮工具结果从未交给模型 | 真实模型在第 3 轮拿到回读结果却被截断，用户只看到失败提示 | M3.5 | 已修复（2026-10-27）：末轮后再发一次无工具收尾请求；仍为空才回退上限提示 |

## 4. 参考项目整合结论

| 项目 | 最值得吸收的长板 | 复用边界 |
|---|---|---|
| AAAAGENT | 工作任务卡、转发回执阶段、O/U/M 情绪分层、记忆管理台 | 非商业许可；Electron/TS，只借鉴设计 |
| Coopanion | 工具即说话、四档权限、世界版本校验、闹钟、透明点击穿透桌宠 | AGPL-3.0；核心子模块缺失，只借鉴设计 |
| Cyrene-Agent | 工具四态、`uncertainEffects`、执行账本、工作区冻结、计划模式、证据记忆、技能层；`soul.md` + 世界书触发的人设分层 | MIT 代码可复用；角色 IP 与 Live2D 素材除外 |
| N.E.K.O | 读时衰减证据、反思晋升、BM25 + RRF、outbox/幂等/tombstone、主动门链、工具注册表 | Apache-2.0；三进程架构过重，只取单进程可移植部分 |
| shunguang-companion | 多轮工具循环、证据事实库、文件身份 + 每步指纹、持久化提醒、SSE 流式/停止；`character.md` + `profile.md` 常驻角色事实与角色扮演过滤 | 非商业许可；纯 Web，无桌宠代码；角色文本与素材不可直接分发 |

五个项目独立收敛出的四条共识：

1. 工具不是函数，而是带风险、状态、回执的事务。
2. 记忆不是向量库，而是“证据 → 候选 → 晋升 → 遗忘”的生命周期。
3. 主动不是定时器，而是“活动状态 → 门控 → 来源 → 反重复”的决策链。
4. 确认不是弹窗，而是绑定世界快照的一次性令牌。

### 4.1 UI 层对比结论（2026-10-29，M12 依据）

上一轮整合只覆盖执行/记忆/主动四条共识，没有单独审计 UI。本次以五个参考项目的界面层为基准，重新逐项核对 `agent_framework/companion/web/assets/`（M11 的默认前端）。

| 项目 | 界面技术栈 | 最值得对标之处 |
|---|---|---|
| Cyrene-Agent | Electron + React19 + Ant Design X + pixi-live2d + streamdown/shiki/katex/mermaid | 双层 theme token、可调整右栏承载多种 inspector、富 Markdown、上下文用量环、忙时消息排队 dock、托盘菜单、Live2D 口型/眨眼 |
| Coopanion | Electron 双进程 + 自研控制台（Tailwind4 + CodeMirror） | 鼠标租约与点击穿透、气泡队列与逐条节奏、按工具类型分关键帧的状态气泡、248→72px 折叠栏、换装联动全局配色、15px/1.72 正文 |
| AAAAGENT | WKWebView / Electron 浮窗 + 手写 CSS 抽屉 | 折叠成小球、整窗拖拽换图、推麦说话热键 + 实时波形、快捷键条、不对称圆角气泡 |
| N.E.K.O | FastAPI + Jinja / React 悬浮胶囊 | 可拖拽可缩放的胶囊浮窗、结构化消息块 schema、消息表情回应、边缘吸附、手写字体氛围 |
| shunguang-companion | React19 + Tailwind4 + assistant-ui | 氛围光晕 + 点阵背景、接收→思考→回应三段状态、人格贴纸、页面转场、`clamp()` 响应式 |

信息架构层面本项目已不落后（三栏、会话树、内联审批卡、运行条、标签抽屉、字体缩放、减少动态效果均有对应实现），差距集中在视觉纵深、消息表达力与桌宠浮窗能力，共 26 项，编号 U/C 供 M12 切片引用；M12.1 已于 2026-10-29 关闭其中 8 项（U3/U8/U9/U10/U11/U12/U15/C9），M12.2 同日关闭 4 项（U1/U2/U4/U5），M12.3 已关闭 C1（2026-10-30）、C2（2026-10-30）与 C4（2026-10-30），累计 15 项，其余 11 项仍开放。

**视觉与排版**

| 编号 | 现状（证据） | 参考基准 | 归属 |
|---|---|---|---|
| U1 | 正文 `--fs-body` 13px、`body` 默认 12px、`--fs-caption` 10px 却使用 39 次为全站最高频（会话时间、工具详情、审批元信息、运行条、状态栏全靠它）—— `tokens.css:3-11` | Coopanion 15px/1.72、shunguang 14px、AAAAGENT 14px、Cyrene 正文 14px + 12–80 完整字阶 | M12.2 已关闭（字阶整档上调：body 14 / ui 13 / meta 12 / caption 11，与 `theme.TYPE_TOKENS` 逐档相等；会话时间、工具详情、审批元信息、运行条、状态栏按角色重新分档，四个页面样式表里 `--fs-caption` 从 39 处降到 17 处，正文/次级文本分别走 body 12 处、ui 26 处） |
| U2 | 字阶底部挤（10/11/12/13 只差 1px）、顶部缺：`--fs-title` 在 web 从未引用、`--fs-large` 仅 2 次，空状态标题只有 17px —— `app.css:474` | 参考项目品牌位 23px 衬线 + 3px 字距 | M12.2 已关闭（补出顶部阶梯 large 16 / subtitle 18 / display 24：空状态标题 17px → display 24px、人格名 → large、窗口标题与管理台情绪标题 → subtitle；未用衬线与字距装饰，属参考项目品牌风格而非层级 necessity） |
| U3 | 字体栈只有雅黑系，无 `--font-mono`/display token：`Consolas, "Cascadia Mono"` 在行内 code、代码块、diff 三处各写一遍；`font-weight` 只有裸 600×11、500、400 | 参考项目普遍有 `JetBrains Mono` 等独立 mono token | M12.1 已关闭（`--font-mono`/`--fw-*`，行内 code、代码块、diff 共用） |
| U4 | `line-height` 出现 6 个取值（1.5/1.55/1.6/1.65/1.7/1），同层内容节奏不齐 | 收敛为正文/元信息/控件三档 | M12.2 已关闭（`--lh-control` 1 / `--lh-meta` 1.5 / `--lh-body` 1.7 三档，五个样式表零裸 `line-height` 数值；`.new-task .plus` 这类图标字形例外，`--lh-control` 会把 "+" 裁掉 2px，实测后改走 `--lh-meta`） |
| U5 | 阅读列固定 780px（`app.css:288`），13px 下每行约 58–60 个中文字 | CJK 舒适行长 35–45 字；参考项目用 rem/max 宽度约束 | M12.2 已关闭（`--read-measure = --fs-body × 42`、`--col-measure` 按气泡 74% 反算，`.chat-inner`/`.composer-wrap`/`.approval` 全部引用；审计实测每行 18–37 字（回归下限 16），最宽的 100% 档 37 字已落进舒适区。原"58–60 字"是按 780px 整列估的，气泡实为 74% 列宽，基准一并修正） |
| U6 | 无纵深：5 个 CSS 文件里 `backdrop-filter`、`gradient`、`clamp()` 均出现 0 次，阴影只有 `--shadow` 一档（`tokens.css:15`），`pearl-white` 还沿用 0.28 黑阴影 | Cyrene 3–12px 玻璃模糊 + 渐变 + glow shadow；shunguang `blur(80px)` 光晕 + 点阵背景 + 渐变侧栏 | M12.5 |
| U7 | 气泡两侧都是对称 10px 圆角（`app.css:313`），不靠 meta 行就分不出谁在说 | AAAAGENT `15px 15px 15px 5px`、N.E.K.O `8px 16px 16px 16px`、Coopanion 镜像圆角 + 尾巴 | M12.5 |

**组件排布**

| 编号 | 现状（证据） | 参考基准 | 归属 |
|---|---|---|---|
| U8 | Web 端没有间距 token——Qt 侧已有 `SPACE_TOKENS`（`ui/theme.py:28`）但移植时丢失；裸像素 padding 有 20+ 组合（`0 18px`、`10px 12px`、`7px 9px`、`8px 10px 9px`），gap 出现 2/3/5/6/7/8/9/10/12/16/18，相邻面板互不对齐 | Coopanion `--page-gutter clamp()` + `--section-gap`；shunguang Tailwind 间距阶 | M12.1 已关闭（`--sp-*` 按 `--sp-scale` 半速增长，四个样式表零裸像素 padding/gap） |
| U9 | `--scale` 只乘在 font-size 上，控件高度全写死：`.pill` 18px、`.segmented button` 22px、`.ghost` / `.inspector-tabs button` 26px、`.new-task` / `.field` 30px、`.table td` 34px；200% 时 20px 字进 18px 盒直接切顶。全站唯一正确写法是 `.composer textarea` 的 `calc(var(--fs-body) * 1.55 * 2)`（`app.css:713`） | Qt 靠控件字体度量自然长高，无此问题 | M12.1 已关闭（`--ctl-*` 由 `--fs-*` 推导的 `min-height`，四个样式表不再有固定 `height`） |
| U10 | 右栏 5 个标签是 nowrap flex 且没有 `overflow-x`/`flex-wrap`（`app.css:844`），面板默认 268px，125% 起顶边、150%+ 溢出面板边界 | 参考项目标签栏可滚动或图标化 | M12.1 已关闭（`.inspector-tabs`/管理台 `.tabs` 横向滚动、按钮 `flex:none`，下划线改 `inset` 阴影不被滚动容器裁掉） |
| U11 | `@media (max-width:880px) .win-actions [data-cmd]{display:none}`（`app.css:1005`，与 720px 那条重复）而主窗最小宽度 780（`web/shell.py:28`）——无边框窗口在最窄可达尺寸只剩"面板"，最小化/关闭都不可用 | 窄屏退化为图标按钮 | M12.1 已关闭（`@media` 只切换 `.lbl`/`.ico`，按钮永不 `display:none`；断点另按字号归一化为 `responsiveWidth()`） |
| U12 | `.send` 固定 `width:78px`（`app.css:741`），150/200% 与"停止"文案切换时挤压 | 按字号计算最小宽度 | M12.1 已关闭（`.send` 用 `min-width: calc(6em + var(--sp-xl))` 与 `min-height: var(--ctl-primary)`） |
| U13 | 模式在顶部 `modeSwitch` 与 composer 的 `data-pill="mode"` 各一份，工作区在 rail `ws-card` 与 pills 各一份，两套样式两个入口 | 单一状态源 + 单一控件 | M12.5 |
| U14 | 同一列三种宽度基准：气泡/工具卡 74%、审批卡 `min(100%,600px)`（`app.css:512`），780px 列里右边缘参差 | 统一内容列宽 | M12.5 |
| U15 | 圆角失控：`--radius-sm/md/lg` 只被引用 7 次，实际出现 11 种硬编码值，最常用的 `8px` 用了 19 次却没有 token | 参考项目 4/8/12/16/24 阶 + pill | M12.1 已关闭（新增 `--radius-pill/card`，硬编码圆角收进 token 阶） |
| U16 | rail 固定 208px 无图标折叠态，窄屏直接跳覆盖抽屉，缺中间态 | Coopanion 248→72px、260ms 过渡并淡出标签 | M12.5 |

**功能与信息表达**

| 编号 | 现状（证据） | 参考基准 | 归属 |
|---|---|---|---|
| C1 | ~~Web 的 `markdown()` 比 Qt 还弱：无引用、表格、嵌套列表、删除线，代码块无语言标签与复制按钮（`app.js:226`），而 `ui/markdown.py` 已支持 quote；工作模式输出表格会散架~~ **已关闭（M12.3，2026-10-30）**：语法收敛为一份共享实现（`ui/markdown.py` + `web/assets/markdown.js`），两端用 21 个样本做逐字节相等断言；新增表格（含对齐与误判回退）、层级列表、删除线、代码语言标签，Web 代码块加复制按钮；高亮未引入（本地 vendored 的成本高于收益，见 D61） | Cyrene streamdown + shiki + katex + mermaid | M12.3 |
| C2 | ~~上下文用量由前端正则从投影文案里抽 `(\d+)\s*/\s*(\d+)`（`app.js:569`），上游改一个字即静默失效；显示为文本胶囊~~ **已关闭（M12.3，2026-10-30）**：投影新增 `ContextView` 数字字段，前端改环形仪表 + 阈值变色，正则抽取删除 | Cyrene / Coopanion 用 conic-gradient 环 + 阈值变色 | M12.3 |
| C3 | 消息级操作只有"复制"，无重新生成、存为记忆、来源跳转、表情回应；重试/再生成自 M10.1 起就以"无应用服务命令"为由缺席 | N.E.K.O 消息块 schema 与表情回应 | M12.3 |
| C4 | ~~运行条只有 label/detail/耗时/停止，事件里已有的 `sequence / stage / detail` 未展开为步骤时间线~~ **已关闭（M12.3，2026-10-30）**：投影新增 `RunView.steps`（相邻同阶段同行折叠），两端展开为可折叠步骤时间线，终态文案仍单点来自投影；顺带修掉阻断 Web 事件通道的 SSE 分块编码缺陷（D60） | Coopanion 可折叠 activity step 列表、Cyrene RunStageIndicator | M12.3 |
| C5 | 生成中拒绝一切输入变更（M9 active-run 守卫），用户只能等待或停止 | Cyrene / Coopanion 的 pending queue dock | M12.3 |
| C6 | 浮窗把 `contextmenu` 直接 `preventDefault()` 吞掉（`overlay.js:106`），无 hover 快捷入口，主动气泡只有一条且新的覆盖旧的（`overlay.css:32`），不可折叠成球、无全屏自动隐藏与边缘吸附 | Coopanion hover 按钮 + 气泡队列、AAAAGENT 折叠小球与快捷键条、N.E.K.O 边缘吸附 | M12.4 |
| C7 | 形象是 3 帧静态 SVG，且 `avatarSvg` 在 `app.js:174` 与 `overlay.js` 各存一份；无呼吸/眨眼/口型 | Cyrene Live2D 命中区与表情、Coopanion canvas figure pack 与分关键帧动画 | 拆分归属（2026-10-30 二次调整）：M12.4 只做"把两份 `avatarSvg` 抽成单一挂载点"，它是 M13.3 **删除**整个程序化形象的前提，不是给旧形象补动画的前提；M13.3 起桌宠只有 Live2D 一个载体，呼吸/眨眼/口型/表情全部由模型承担，程序化 SVG 的 CSS 待机动画与其回退档一起取消（不可用即失效，见 D58） |
| C8 | 每次渲染整列表全量重建：`renderChat` 用 `chatInner.innerHTML = parts.join("")`（`app.js:443`），流式期间每个 delta 重建全部气泡 | Cyrene `@tanstack/react-virtual`；Qt 侧已有"按签名增量重建"经验 | M12.3 |
| C9 | 可访问性近乎为零：`index.html` 的 aria/role/tabindex 计数为 0，会话行是非语义 `div`（`app.js:396`），chat 区无 `role=log`/`aria-live`，焦点样式只有 `.field:focus` | 参考项目均有 tablist/焦点环与语义按钮 | M12.1 已关闭（会话行改 `<button>`、`role=tablist/tab` + `aria-selected`、chat `role=log` + `aria-live`、管理台表格行键盘可达、统一 `:focus-visible` 焦点环） |
| C10 | 浮窗用 `setInterval(refresh, 8000)` 轮询状态，未复用主界面已有的 SSE 通道 | 参考项目事件驱动 | M12.4 |

结论：M11 把 Qt 的信息层级搬到了 Web，但**没有搬 Qt 的 token 契约**——`theme.py` 的 `SPACE_TOKENS`、字体度量自然增长、`ui/markdown.py` 的引用支持在 Web 端都缺失，因此 150/200% 字体缩放和 780px 窄窗这两条 M10.3 已验收的能力在默认界面上实际是坏的。M12 按 U/C 编号整改，顺序为 M12.1（缩放正确性）→ M12.2（排版层级）→ M12.3（消息与运行表达）→ M12.4（浮窗）→ M12.5（视觉纵深与密度）。
- 2026-10-29 M12.1 复核：token 契约已移植，四档字号 × 5 个窗口尺寸共 20 个组合在真实 Chromium 下零裁切/零溢出，780px 最小尺寸下最小化与关闭以图标态可用，M10.3 承诺的字体缩放在默认界面恢复可用；剩余 U/C 项仍按上述顺序处理。

## 5. 模块路线图

### M1 可审计执行内核

- **优先级**：P0
- **状态**：M1a 已完成；M1b 核心与收尾已完成（2026-10-24），其余增强项随后续模块处理
- **依赖**：无
- **预计**：已完成；剩余增强不阻塞 M2
- **目标**：让每一次工具调用都可授权、可去重、可对账、可恢复，并最终由模型原生发起。

#### M1a 执行可信度（已完成）

工作项：

- [x] 工具四态：`success / failure / unknown / not_executed`。
- [x] 不确定副作用：替换文件后无法确认终态时返回 `unknown`，禁止自动重放。
- [x] 执行账本：`execution_ledger` 以 `action_id` 为主键、`idempotency_key` 唯一；回执按 `action_id` 唯一。
- [x] 提案快照：工作区真实路径、`st_dev`/`st_ino` 身份、目标指纹、内容哈希、TTL。
- [x] 确认复核：重新校验当前模式、策略、工作区路径与身份、TTL；不一致返回 `not_executed`，不写文件。
- [x] 执行与撤销只读提案/回执快照，不读当前 runtime 工作区。
- [x] 重启恢复：`running / dispatching / replaced` 统一标为 `unknown`，绝不自动重放；兼容旧版卡在 `RUNNING` 的提案。
- [x] UI：模式切换写入会话；确认时传入当前模式；仅对可撤销回执显示撤销按钮。

验收（2026-10-24 全部通过）：

- [x] 确认前切换工作区 → 写入被拒绝，回执为 `not_executed`。
- [x] 当前模式已降级 → 旧提案不能越权执行。
- [x] 重复确认/重复请求 → 只产生一次写入，返回同一回执。
- [x] 模拟超时或崩溃 → 状态为 `unknown`，可人工对账，不自动重试。
- [x] 撤销不受当前工作区影响。
- [x] 新增 8 个回归测试（`tests/test_companion_execution.py`），42 个测试全部通过。

落点（实际）：`store.py`（执行账本与恢复）、`file_actions.py`（快照、TTL、不确定副作用、快照撤销）、`runtime.py`（确认复核与状态映射）、`models.py`（`UNKNOWN`）、Qt/Tk UI。

#### M1b 原生工具循环（核心与收尾已完成）

已完成：

- [x] 统一 `CompanionToolRegistry`：JSON Schema、按模式装配、结构化 `ToolResult`；`ASK` 级工具必须生成提案，否则拒绝并回填错误。
- [x] LLM 原生工具循环：每轮传入工具列表，最多 **4 次调用 / 3 轮**；工具结果回填模型继续推理。
- [x] 写入工具只创建提案，不直接写文件；确认后仍走 M1a 的模式复核、工作区快照与执行账本。
- [x] 显式“搜索…并保存为笔记”保留正则快路径；离线模式保持原有路径，无模型时不退化。
- [x] 工具失败、未知工具、模式拒绝都作为工具结果回填，模型可向用户解释失败原因。
- [x] 单工具超时：`CompanionTool.timeout` 在工作线程中执行，超时映射为 `tool_timeout`；搜索 30 秒、本地写入 10 秒、时间查询 5 秒。
- [x] function calling 降级：首次工具调用因模型不支持而失败时，切换为无工具对话并提示用户；显式搜索正则路径不受影响；非工具类错误不会被误判降级。
- [x] 新增 14 个测试（`tests/test_companion_tools.py`），全量 57 个测试通过。

待完成（不阻塞 M2）：

- [ ] 逻辑调用 ID：扩展为 `proposal_id:step_id` / `run_id:tool_call_id`，终态成功结果缓存 10 分钟。
- [ ] 更多工具接入统一确认与回执路径（当前仅 `write_note` 需要确认）。
- [ ] OpenClaw 桥复用同一执行内核。

落点（实际）：新增 `agent_framework/companion/tools.py`，修改 `runtime.py`、`file_actions.py`。

### M2 证据链记忆中心

- **优先级**：P1
- **状态**：已完成（第一批 2026-10-24；第二批 2026-10-26）
- **依赖**：M1 的稳定消息 ID 与回执 ID
- **预计**：已完成
- **目标**：每条长期记忆都能回答“从哪来、为什么信、何时被用过、忘记后去了哪”。

#### M2 第一批（已完成）

已完成：

- [x] 版本化迁移：`store.py` 引入 `SCHEMA_VERSION` 与 `PRAGMA user_version`，旧库启动时自动补列、建表。
- [x] 表结构：以现有 `memories` 作为事实表扩展 `canonical_key / evidence_count / protected / merged_from_ids / forget_epoch / first_seen_at / last_seen_at`；新增 `memory_evidence`、`memory_candidates`、`memory_events`、`recall_log`、`memory_tombstones`。
- [x] 抽取：只处理用户消息；证据必须是对应消息原文的精确子串；助手消息即使被误传也不会生成记忆。
- [x] 规范键：偏好以“对象”拆键（`preference:<对象>`），身份按字段拆键（`identity:name/birthday/job`），普通事实使用 `fact:user:<规范化内容>`。
- [x] 冲突与晋升：加权评分（显式记忆、结构化模式、用户证据、近期观察），≥0.70 自动确认，0.45–0.70 进入候选；同键新值会取代旧值并归档，低分冲突只记录候选。
- [x] 召回：沿用单一读时衰减公式；召回结果带来源消息、证据数量与原因；召回日志记录 `query_hash / fact_ids / scores / used_in_reply / latency`，不存原文。
- [x] 遗忘：`forget_epoch` + `through_seq` 墓碑；同规范键记忆级联遗忘；相同内容不会被同一条用户输入复活。
- [x] 新增 9 个测试（`tests/test_companion_memory.py`），全量 66 个测试通过。

验收（第一批，2026-10-24 通过）：

- [x] 仅由助手回复推导的“用户事实”被拒绝。
- [x] 召回结果带来源和原因；`recall_log` 有记录且不含原查询。
- [x] 用户纠正后，旧值不再被召回。
- [x] 遗忘后，直接召回与相同内容重放都不会复活。

#### M2 第二批（已完成，2026-10-26）

已完成（FTS5/BM25 召回切片）：

- [x] 中文 2/3-gram 预分词：`textseg.py` 统一索引与查询切分（英数整词 + 中文 2/3-gram，去重、casefold）。
- [x] FTS5 虚表 `memory_fts` + BM25 候选检索（top 30），仅命中 `confirmed`；最终排序仍用单一读时衰减公式（决策 D2/D12）。
- [x] 版本化迁移 v2：`user_version` 1→2，`recall_log.source` 列，FTS 全量回填；旧库启动自动升级。
- [x] 索引同步：写入记忆即索引；纠正（内容变更）后重建该行索引；遗忘靠 JOIN 状态过滤与墓碑双重排除。
- [x] 降级与自愈：FTS5 不可用/索引损坏/查询异常 → 自动降级关键词召回，聊天不阻塞；重启时缺表建表、行数不符重建（`_ensure_memory_fts`），另有 `rebuild_memory_fts()` 管理入口。
- [x] 召回可解释：`recall_log.source` 记录 `fts`/`keyword`；`context_lines` 原因文案区分 BM25 与关键词。
- [x] `doctor.py`：数据库完整性（`PRAGMA integrity_check`）、`user_version`、FTS 状态只读检查。
- [x] 新增 14 个测试（`tests/test_companion_memory_fts.py`），全量 80 个测试通过。

已完成（第二批剩余，2026-10-26）：

- [x] 迁移 v3：`memory_relations` 派生关系表、`experiences.memory_id/status`、`import_jobs`/`import_messages` 导入账本；旧库自动升级。
- [x] 跨类型遗忘传播：遗忘事实时沿派生图递归失效经历、反思与摘要；每条派生记忆写入事件与墓碑，经历列表同步隐藏。
- [x] 旧聊天导入：支持消息数组、含 `messages` 的会话对象、JSONL；导入前返回成本预估并强制确认；`max_messages` 分段暂停、数据库游标续跑；消息内容哈希跨副本去重；墓碑阻止已遗忘内容复活。
- [x] 证据与召回台：记忆窗口展示来源原文、候选评分、生命周期事件、最近召回及 `source`；运行时提供 `memory_details`。
- [x] `protected` 人设入口：`remember_persona` 写入受保护记忆，冲突观察只记候选、不自动取代；窗口可切换保护状态。
- [x] 角色扮演防污染：显式角色扮演/引用台词/假设句不进入事实抽取；导入按会话标题与开始/结束标记维护状态，只导入消息、不抽取记忆。
- [x] 新增 12 个数据层测试（`tests/test_companion_memory_batch2.py`）、1 个 Qt 证据台测试与 5 个角色扮演/导入防护测试，全量 98 个测试通过（6 个环境跳过）。

验收（第二批）：

- [x] 派生记忆来源覆盖率 100%：受测试的派生记忆都能通过事件/关系追溯到来源事实。
- [x] 遗忘后，摘要和导入都不会复活（含派生记忆内容墓碑与导入过滤）。
- [x] FTS5 损坏时召回降级为关键词，聊天不阻塞（含损坏降级与重启自愈回归测试）。
- [x] 导入前提示消息数、用户消息数、预计记忆数与耗时；暂停后可跨进程续跑。
- [x] 受保护人设不会被冲突观察或自动归档淘汰。
- [x] 角色扮演、引用台词与假设句不生成用户事实；导入角色扮演会话后记忆表仍为空。

落点（实际）：新增 `agent_framework/companion/textseg.py`、`agent_framework/companion/importer.py`；扩展 `memory.py`（双路召回、派生遗忘、证据详情、protected、角色扮演防护）、`store.py`（迁移 v2/v3、FTS 索引与检索、派生关系、导入账本）、`runtime.py`、`ui/qt_app.py`、`ui/tk_app.py`、`doctor.py`。

### M2.5 人格与角色背景层（已完成）

- **优先级**：P1
- **状态**：已完成（2026-10-26）：本地 persona pack + 稳定前缀 + 默认“蕾米埃尔·丹” + 导入校验 + 用户设定 + 世界书
- **依赖**：M2 证据记忆；为 M3 稳定前缀确定边界
- **预计**：2–3 天（实际约 1.5 天）
- **目标**：把“角色是谁”与“用户经历过什么”分开：稳定人格/背景/性格走文件与系统前缀，用户事实继续走证据记忆。

工作项：

- [x] `data/personas/<id>/persona.md`：身份、性格、说话方式、边界、关系姿态；由开发者或用户本地维护。
- [x] `facts.md`：少量常驻角色背景事实；大段世界观走独立 `worldbook.md`，按查询触发，不写入 `memories`。
- [x] 稳定前缀：基础规则 + persona + facts；记录内容哈希，供 M3 复用。世界书不参与稳定哈希；M3 已将模式移入会话前缀，稳定层不再随模式变化。
- [x] persona pack 元数据：`persona.json` 记录名称、版本、来源、许可、canon cutoff、剧透与关系边界。
- [x] persona pack 导入：展示来源、许可、大小与版本；只复制人设文本文件，第三方素材不随仓库分发；同 ID 覆盖需显式确认。
- [x] 用户自定义称呼/关系/边界继续走 `protected` persona 记忆，并在 Qt/Tk 人设面板可编辑。
- [x] 角色扮演/引用台词防污染（2026-10-26）。
- [x] 明确角色事实不是用户事实或共同经历；提示词分栏标注，角色资料不写入 `memories`。
- [x] Qt/Tk 标题、输入提示与离线问候使用人设名称/默认称呼。
- [x] `--persona <id>` 选择本地人设包；缺失或损坏时回退到朴素助手人格，不阻塞启动。

验收（第一切片）：

- [x] 同一 persona 的稳定前缀哈希一致（`tests/test_companion_persona.py`）。
- [x] 角色资料不进入用户事实召回；persona 加载后 `list_memories` 仍为空。
- [x] 删除/替换 persona 不影响已有用户记忆。
- [x] 导入角色扮演会话不产生事实（沿用 D17 回归测试）。
- [x] persona 导入/编辑与世界书完成：来源/许可/大小/版本先预览；非法 ID、超大文件、坏 JSON 与未显式覆盖均被拒绝；世界书只注入相关条目。

落点（实际）：新增 `agent_framework/companion/persona.py`、`data/personas/remielle/`（`persona.md`、`facts.md`、`worldbook.md`、`persona.json`）；扩展 `runtime.py` 的提示组装、离线回答、`persona_info()`、设定与导入 API，`memory.py` 的 keyed protected persona 设定，`ui/qt_app.py` / `ui/tk_app.py` 的人设面板；M3 的 `context.py` 复用稳定前缀。

### M3 上下文编排器

- **优先级**：P1
- **状态**：已完成（2026-10-26）
- **依赖**：M1、M2、M2.5 的稳定人设层（已提供稳定前缀与哈希）
- **预计**：3–5 天
- **目标**：在固定预算内稳定组装“人设 + 近期完整轮次 + 摘要 + 召回记忆”。

工作项：

- [x] 提示词分层：稳定层（安全规则 + persona）、会话前缀（模式 + 排序后的工具）、运行时上下文（情绪/世界书/摘要）、相关记忆。
- [x] 稳定层 SHA-256 指纹；模式与工具变化只进入会话前缀，不改变稳定哈希。
- [x] 工具 Schema 按名称排序，保持前缀稳定。
- [x] 上下文预算：按字符预算保留最近完整消息；召回记忆与用户设定各自行数/字符上限。
- [x] 压缩：历史超过可用预算 82% 时触发，只丢最旧完整消息，写抽取式 checkpoint 摘要（每会话保留最新一条），后续轮次复用。
- [x] 工具输出：超过 16KB（UTF-8 字节）落盘到 `data/tool_results/`，只回预览、字符数与句柄；`read_tool_result` 支持 offset/limit 分段回读，句柄校验、会话隔离、重启后可读。
- [x] 明确“未回读不算已读”：稳定层提示、落盘通知与回读头部都写明未返回部分不得假设或声称已读。
- [x] 单条存储上限 4MB；超限时存储副本标记截断；存储不可用时降级为“仅预览、不可回读”并明确告知。

验收：预算内不超限；压缩后工具调用对完整；大输出可回读；相同稳定前缀哈希一致。

落点（实际）：新增 `agent_framework/companion/context.py`、`tool_results.py`；`runtime.py` 以 `_build_prompt()` 统一装配并暴露 `context_info()`，在工具循环进入上下文前执行 `_maybe_spill()` 并注册 `read_tool_result`；`store.py` 迁移 v4 新增 `context_checkpoints`。当前历史只落库 user/assistant 消息，窗口边界天然落在完整轮次之间。

### M4 主动陪伴引擎

- **优先级**：P1
- **状态**：已完成（M4.0 数据底座 2026-10-27；M4 主体 2026-10-27）
- **依赖**：M2 的活跃记忆、M3 的上下文与稳定召回日志、M4.0 的提醒/话题底座
- **预计**：M4.0 约 2–3 天 + 主体约 1 周
- **目标**：有理由、有预算、可关闭地主动说话。

#### M4.0 前置底座（2026-10-27 完成）

- [x] `reminders` 表与最小提醒闭环：创建、到点、已读/忽略；`next_due` 可被门控查询（迁移 v5，`proactive/reminders.py`；运行时支持“提醒我 10 分钟后喝水”）。
- [x] “未完成话题”派生：从最近消息中识别“用户提问但助手未回答/未收尾”的会话，给出可追溯的 `source_message_id`；不额外调用 LLM（`proactive/topics.py`，识别未回复、连续用户消息与已知失败回复，默认 24 小时内、最多 3 条）。
- [x] 活动状态机依赖决策：采用 `ctypes`（`GetLastInputInfo` / `GetForegroundWindow` / `QueryFullProcessImageNameW` / 全屏检测），不引入 `psutil`；`doctor.py` 显示 `Activity probe`（`proactive/activity.py`）。
- [x] 门控链可单测：Quiet → 忙 → 免打扰 → 每日上限 → 间隔 → 未回复阻断 → 活动/隐私门，纯函数实现，不依赖 UI（`proactive/gate.py`）。
- [x] 空来源安全退出：没有任何可追溯来源时直接返回 `no_source`，禁止生成随机话题（`proactive/engine.py`）。

工作项：

- [x] 活动状态机：`active / idle / away / private / focus / game / unknown`；Windows 使用 `GetLastInputInfo`、`GetForegroundWindow`，锁屏或前台窗口为空按离开处理。
- [x] 门控链：Quiet 模式 → 免打扰时段（22:00–08:00，可关闭） → 每日最多 2 次 → 间隔至少 3 小时 → 有未回复则阻断（连续 2 次） → 隐私门。
- [x] 来源：提醒、未完成话题已接入；M4 主体新增“近期被回复采用的已确认记忆”，默认关闭、面板显式开启；不使用随机话题。
- [x] 反重复：按来源类型半衰期 + 内容/URL 哈希去重，重复则丢弃并尝试下一条来源；提醒因用户明确请求而豁免。
- [x] 持久化：`next_due / last_event_id / quiet_hours` 与每日计数、未回复计数、`last_spoken_at` 写入 `proactive_state`；错过的主动消息不补发，只标未读。
- [x] 记录每次主动发言的触发原因，并可在 UI 查看（`proactive_events.trigger_reason` 与“主动”面板事件表）。

验收（2026-10-27 全部通过）：

- [x] Quiet/免打扰不发言；每日与间隔上限生效；连续未回复后停止（`test_companion_proactive_service.py` 门控分支）。
- [x] 错过不补发：超过宽限 30 分钟的提醒记为 `missed`、关闭提醒并保持未读，后续 tick 不再生成事件。
- [x] 触发原因可追溯：每条送达/错过事件都带来源类型、来源 ID 与 `trigger_reason`。

#### M4 主体实现（2026-10-27 完成）

- [x] 迁移 v6：`proactive_state`（开关、免打扰、每日/间隔/未回复阈值、每日计数、`last_spoken_at`、`last_event_id`）与 `proactive_events`（来源、触发原因、内容哈希、URL 哈希、状态、消息 ID、`read_at`）。
- [x] `ProactiveService`：`tick()` 依次标记错过 → 构建门控状态 → 获取来源 → 本地模板生成 → 写入助手消息与事件 → 更新计数；`preview()` 只读。
- [x] 反重复：`repeat.py` 用归一化内容哈希与 URL 哈希匹配最近 100 条送达/错过事件；半衰期为话题 24h、记忆 72h，提醒为 0（允许重复）。
- [x] 错过语义：`MISSED_GRACE_MINUTES=30`；超窗提醒创建 `missed` 事件（`read_at=None`）并 `dismiss`，不写入会话历史。
- [x] 状态语义：用户消息清零未回复并标记全部事件已读；跨天每日计数归零；发送/生成期间轮询跳过。
- [x] 界面：Qt 每 60 秒 `QTimer` 轮询，新增“主动”面板（设置、状态、事件表、未读、现在检查）；Tk 提供对应按钮与检查入口。
- [x] `doctor.py` 新增 `Proactive state`（开关、今日次数、未回复、未读）；真实库迁移 v6 后 12 条消息保留。
- [x] 新增 28 个服务/单元测试与 1 个 Qt 面板测试；全量 202 个测试通过（3.12.6 无跳过）。

落点（实际）：新增 `agent_framework/companion/proactive/service.py`、`repeat.py`、`compose.py` 与 `tests/test_companion_proactive_service.py`；扩展 `proactive/engine.py`、`__init__.py`、`models.py`、`store.py`（迁移 v6）、`runtime.py`、`ui/qt_app.py`、`ui/tk_app.py`、`doctor.py`、README 与使用说明。

### M5 工作任务卡与计划模式

- **优先级**：P2
- **状态**：已完成（2026-10-27；迁移 v7、`work/` 包、Qt/Tk 任务面板）
- **依赖**：M1
- **预计**：1–2 周
- **目标**：把开放式工作请求变成可审阅、可修改、可拒绝的任务卡。

工作项：

- [x] 意图路由：`companion / work / clarify`，12 秒超时，抗提示注入。
- [x] 任务卡：标题、理由、口语播报、目标、执行器、项目。
- [x] 计划状态机：`NORMAL / PLAN_DISCUSSING / PLAN_REVIEW / EXECUTING`；先落库再等待。
- [x] 审批卡：approve / revise / reject；超时绝不默认通过；崩溃恢复为 discussing。
- [x] 转发回执：`awaiting_confirmation / forwarding / accepted / completed / unknown / unavailable`；`unknown` 不自动重发。

验收：信息不足只追问一次；未确认不产生外部副作用；超时不自动通过；崩溃可恢复；`unknown` 不重复发送。

落点（实际）：新增 `agent_framework/companion/work/router.py`、`service.py`、`__init__.py` 与 `tests/test_companion_work.py`；扩展 `models.py`、`store.py`（迁移 v7）、`runtime.py`、`ui/qt_app.py`、`ui/tk_app.py`、`tests/test_companion_qt_ui.py`、`doctor.py`、README 与使用说明。

实现要点：

- 路由规则优先，外部副作用请求（`EXTERNAL_RE`）与“计划/安排”类请求进入 `work`；本地搜索/笔记/时间查询保持既有流程；短句与不确定输入走 `clarify`。模型分类器只接收不可信 JSON 文本，12 秒硬上限，超时/非法输出降级为追问。
- 注入守卫先于分类，命中即返回 `clarify` 且不创建任务。
- 任务卡六要素与计划状态先写 `work_tasks` 再等待；`PLAN_DISCUSSING` 只追问一次，下一条消息作为补充信息合并进目标后进入 `PLAN_REVIEW`。
- 批准只在 `PLAN_REVIEW` 生效，先把任务置为 `EXECUTING`、回执置为 `forwarding` 并通过 `claim` 再调用执行器；无执行器映射 `unavailable`、异常映射 `unknown`；`unknown` 为终态，拒绝改写与重发。
- 审批 TTL 默认 30 分钟，过期回到 `PLAN_DISCUSSING` 并标 `expired`，不批准任何动作；启动恢复把审阅/执行任务退回讨论，未完成的转发标 `unknown`。
- Qt“任务”面板与 Tk 任务窗口提供列表、详情、批准/修改/拒绝/刷新；生成任务后自动打开。

### M6 桌面形象与流式交互

- **优先级**：P2
- **状态**：已完成（2026-10-27；流式/停止、透明浮窗、托盘、位置记忆与状态气泡）
- **依赖**：M1 的工具状态事件
- **预计**：1–2 周
- **目标**：让桌宠状态来自真实执行器，而不是模型自述。

工作项：

- [x] 托盘：`QSystemTrayIcon`，显示当前模式、暂停主动、退出；系统托盘不可用时自动跳过。
- [x] 透明置顶窗口：`FramelessWindowHint | Tool | WindowStaysOnTopHint` + `WA_TranslucentBackground`。
- [x] 点击穿透：默认 `WA_TransparentForMouseEvents`，指针进入形象区域再申请点击。
- [x] 位置记忆与多显示器边界修正（`ui_state.json` + 最近屏幕夹取）。
- [x] 流式事件：`delta / reset / tool / done / error`；停止贯穿 LLM 流读取与工具轮次；generation id 丢弃迟到分片。HTTP 取消在下一分片到达时生效（客户端 60 秒超时兜底）；TTS 尚未接入，接入时复用同一取消语义。
- [x] 行内控制标记：`[[mood:...]] / [[action:...]] / [[sticker:...]]` 在显示与入库前剥离（跨分片安全；TTS 接入后复用同一过滤器）。
- [x] 状态气泡只读执行器回执：等待确认、等待审批、等待补充、正在转发、unknown、已写入、已撤销、写入失败。

验收：托盘可用；透明与点击穿透正常；位置重启后恢复；停止后不再追加内容；状态与回执一致。

落点（实际）：新增 `agent_framework/companion/streaming.py`、`ui/stream.py`、`ui/state.py`、`ui/tray.py` 与 `tests/test_companion_streaming.py`；扩展 `llm.py`（`StreamEvent` / `stream_chat`）、`runtime.py`（`stream_message`、事件生成、`desktop_status()`）、`ui/qt_app.py`（`AvatarOverlay`、托盘接线、停止按钮、generation 过滤）、`tests/test_companion_qt_ui.py`、`doctor.py`（无需新增检查，沿用 v7 状态）、README 与使用说明。

实现要点：

- `LLMClient.stream_chat` 提供默认实现（从 `chat` 生成 delta + done），远程客户端用 OpenAI `stream=True` 并按 `index` 拼装工具调用增量；任何异常转成 `error` 事件，`done` 携带完整 `LLMResponse`。
- 运行时 `stream_message()` 在每个事件上附加 `generation_id`；模型分支与非模型分支（搜索、任务卡、提醒、离线）统一走同一事件出口，界面不需要两套路径。
- 停止使用 `threading.Event`：流读取、工具执行后与 chunk 循环都检查取消；已显示部分与“（已停止生成）”入库，迟到分片在 Qt 侧按 generation 丢弃；工具前导文本触发 `reset`，避免把已废弃内容拼进最终回答。
- `MarkerFilter` 增量解析控制标记，跨分片保留未完成的 `[[`；已知标记送入 `markers`，未知或未闭合标记原样保留，避免吞掉正文。
- `AvatarOverlay` 为独立顶层窗口，120ms 命中检测切换 `WA_TransparentForMouseEvents`；拖动释放与关闭时保存坐标；启动按最近屏幕恢复并夹取，显示器变化不会把浮窗留在屏幕外。
- `desktop_status()` 只读已落库状态：待确认提案 → 任务状态 → 工作回执 → 最近动作回执，依次降级；待机时气泡隐藏。
- 托盘菜单与主窗口状态双向同步：窗口显隐、模式勾选、主动暂停勾选、浮窗显隐；`closeEvent` 停止流、关闭浮窗与托盘。

### M7 技能包

- **优先级**：P3
- **状态**：已完成（2026-10-27）
- **依赖**：M1
- **预计**：3–5 天
- **目标**：不修改主循环即可增加流程型能力。

工作项：

- [x] `SKILL.md` frontmatter：`name / description / tools / effectKind / modes / references`。
- [x] 元工具：`invoke_skill`、`read_skill_reference`，按需读取，限制单次字符数。
- [x] 按模式和风险过滤技能；非法技能拒绝加载。
- [x] 技能调用写入审计日志。

验收：新增一个技能无需改代码；技能只能调用已授权工具；越权技能被拒绝。

落点（实际）：新增 `agent_framework/companion/skills/`（`manifest.py` 解析、`library.py` 扫描/校验/读取）、`tests/test_companion_skills.py`；扩展 `store.py`（迁移 v8 `skill_audit`）、`tools.py`（`ToolResult.data`、`risks()`）、`runtime.py`（技能发现、元工具、本轮工具范围、`skills_status()` / `skill_audits()`）、`doctor.py`（`Skills` 检查）、README 与使用说明。

实现要点：

- frontmatter 只接受一层键值与列表，拒绝未知/重复字段、嵌套结构和非 UTF-8；`name` 必须与目录名一致，参考文件只能是 `references/` 下的单个 `.md`/`.txt`。
- `effectKind` 映射风险上限（read→fs-read、network→network、write→fs-write、external→input-control）；技能风险取效果与所有声明工具的最大值，省略 `modes` 时按 `decide_tool_policy` 推导，显式 `modes` 与风险冲突即拒绝。
- 元工具只在存在当前模式可用技能时注册；`invoke_skill` 成功返回技能正文与工具清单，`read_skill_reference` 用 offset/limit 分段读取并带字符范围提示。
- 调用成功后本轮工具范围收窄到声明工具 + 元工具；越权调用在 `registry.execute` 之前拦截，记 `skill_tool_denied`，下一轮恢复完整工具集。
- 审计写 `skill_audit`（session、skill、action、mode、outcome、detail、created_at）；审计写入失败时拒绝调用，避免“执行了但没有记录”。
- 技能正文与参考文件都按工具输出处理并附带“不能覆盖安全规则/确认流程”的约束；技能加载失败只影响该技能，不阻塞启动，问题通过 `skills_status()` 与 `doctor.py` 暴露。

### M8 管理与自检台

- **优先级**：P3
- **状态**：已完成（2026-10-27）
- **依赖**：M1、M2
- **预计**：1 周
- **目标**：让用户能看见、纠正、导出和删除系统记住的一切。

工作项：

- [x] 页面：记忆、召回日志、候选、提案/回执、情绪、失败记录、设置。
- [x] 版本化迁移与备份/恢复。
- [x] 数据导出与彻底删除。
- [x] 日志脱敏，密钥不进入日志和模型上下文。
- [x] `doctor.py` 增加数据库完整性、迁移版本、索引状态检查。

验收：每个页面可追溯到原始记录；导出/删除可用；迁移可回滚；日志无密钥。

落点（实际）：新增 `agent_framework/companion/maintenance.py`（备份/恢复/导出/删除/完整性/单实例锁）、`redaction.py`、`ui/console/`（7 页管理台）与 `tests/test_companion_maintenance.py`、`tests/test_companion_console.py`；扩展 `store.py`（迁移前快照、`list_sessions/list_proposals/list_affect_states`、审计与回执脱敏）、`runtime.py`（失败原因脱敏）、`ui/qt_app.py` 与 `ui/tray.py`（入口）、`companion.py`（默认单实例）、`doctor.py`（自检项）、README 与使用说明。

实现要点：

- 备份使用 SQLite 在线备份 API 写入临时文件后原子替换，同名时间戳自动加序号；清单记录 schema 版本、原因、SHA-256 与消息/记忆/会话数。校验检查文件可读、核心表存在、版本不高于当前、完整性与外键；恢复前自动再备份当前状态，恢复后执行 WAL checkpoint。
- 迁移回滚采用“迁移前自动快照 + 从备份显式恢复”，不实现 schema 降级；仅当检测到旧版本且已有数据时创建快照，空库首次初始化不产生备份。
- 导出为 zip：`export.json`（24 张业务表）、`manifest.json` 与 `tool_results/tr_*.json`；`config/keys.json` 不在数据目录且不在导出范围，测试断言 zip 中不含密钥文件或密文。彻底删除记忆会连带证据、候选、事件、经历、派生树、FTS 行，并重写或删除引用它的召回日志；会话/全量删除可选清理备份与导出。
- 单实例锁位于启动入口：Windows `msvcrt.locking`、POSIX `flock` 锁首字节，持有者信息写独立 `.owner` 文件以便在锁定时查询；同进程重入按引用计数；`--allow-multiple` 显式跳过。`doctor.py` 将“被占用”显示为 WARN（说明有实例在运行），空闲为 OK。
- 脱敏在落库前执行：`sk-*`、Bearer、`api_key/token/secret/password/authorization` 赋值与 GitHub token 统一替换为 `[REDACTED]`；覆盖技能审计 detail、执行失败原因、工作回执摘要与详情。
- 管理台只在 Qt 提供（Tk 保留原有对话框）：7 页分别为记忆、召回、候选、执行与审计、情绪、失败、设置与数据；失败页是只读派生页，不新增失败表，来源为失败/未执行提案、失败/unknown 回执、未知/不可用任务回执、非 ok 审计、错过提醒与错过主动事件；设置页提供完整性检查、备份/校验/恢复、导出、FTS 重建、删除当前会话与全量删除（需输入“删除全部”并选择是否清理备份/导出），数据变更后提示重启。
- 控制台入口放在主窗口顶部按钮行与托盘菜单；该按钮最初加入模式行导致窗口最小宽度从 500 增至 508，破坏既有 380 宽侧边对话框布局，已移到可收缩的顶部行，Qt 几何用例恢复通过。

### M8.5 桌面界面改版（方案 A）

- **优先级**：P2
- **状态**：已完成（2026-10-27；2026-10-28 信息架构调整与左栏会话树）
- **依赖**：M6、M8
- **目标**：在不引入 Web/Electron 运行时的前提下，把 Qt 主窗口收敛到参考项目（Cyrene-Agent）的桌面布局与配色。

工作项：

- [x] `ui/theme.py`：语义色 token（经典深色 / 珍珠白）+ QSS 构建器 + 头像配色派生。
- [x] `ui/chat_view.py`：气泡记录、工具回执卡三态、流式光标、按签名增量重建、内容驱动宽度。
- [x] `ui/inspector.py`：右栏只读详情面板，各节由运行时状态派生，签名不变不重建。
- [x] 主窗口三栏结构、左栏/右栏折叠、主题切换、窗口几何持久化。
- [x] Qt 用例更新与新增 24 个测试。
- [x] 复查修复：顶部“收起”改为“最小化”，主窗口进任务栏并可从任务栏/托盘还原（+1 用例）。
- [x] 信息架构调整：左栏只留形象/状态、工作目录与会话摘要，六个面板入口与外观收进左下角设置中枢（+2 用例；左栏在随后的会话树中改为列表形态，见下一项）。
- [x] 左栏会话树：`新的任务` / `搜索` / `工作区`（目录行 + `…` / `+`）/ 缩进会话列表 / 底部设置；形象与状态移到标题栏；会话切换、新建、搜索过滤与上次会话恢复（+7 用例）。

落点（实际）：新增 `ui/theme.py`、`ui/chat_view.py`、`ui/inspector.py`、`ui/settings.py`；重写 `ui/qt_app.py` 的 `CompanionWindow` 布局与 `_build_rail()`，扩展 `ui/state.py`（`theme` / `inspector_visible` / `rail_visible` / `window_geometry` / `active_session`）；`store.list_session_summaries()` 与 `runtime.session_summaries()` / `switch_session()`；`tests/test_companion_qt_ui.py` 与新增 `tests/test_companion_ui_theme.py`。

实现要点：

- 配色只做 token 与 QSS，不复制参考项目的角色素材与 Live2D 模型（其形象为米哈游衍生作品、非商业授权）。
- 保留全部旧控件属性名（`chat_button` / `work_button` / `input` / `send_button` / `confirm_frame` / `status` 等），托盘、对话框与既有用例不受布局重写影响。
- 详情面板读 `runtime.last_context` 而不是 `context_info()`，避免每次刷新触发一次召回与预算构建；刷新走签名比较 + 显式钩子 + 30 秒兜底定时器，不挂在每个 `delta` 上。
- `QLabel` 换行高度不用 `heightForWidth()`：设置固定高度后它会持续返回上一次的高度，导致文本变短时气泡留白；改为按 `QFontMetrics.boundingRect()` 逐行测量。
- 侧边对话框定位抽成纯函数 `side_dialog_geometry()`，可在 800×800 离屏屏外验证“右侧优先、左侧回退、无空间时不出屏”，不再依赖把主窗口缩到 380 宽。
- 主窗口去掉 `Qt.Tool`（普通窗口 + `Qt.WindowMinimizeButtonHint` + 头像 `windowIcon`），透明浮窗仍是 `Tool` 窗口以继续不占任务栏；`minimize_to_taskbar()` 先写 `ui_state.json` 再记录最小化前几何，`changeEvent` 在退出最小态时回填该几何，`toggle_window()` 与托盘文案把“隐藏 / 最小化 / 正常”三态分开处理。
- 信息架构第一版把左栏收敛为“形象与状态 → 工作目录 → 会话摘要”；2026-10-28 的会话树把身份信息移到标题栏，左栏改为导航列表（会话摘要卡移除，标题/条数/末条改为会话行悬停提示）。
- 设置中枢点选后先 `close()` 再用 `QTimer.singleShot(0, ...)` 打开目标面板，两个侧窗都在主窗口右侧定位，不先关会完全重叠。
- 左栏会话行以 `SessionRow` 承载：`ElidedLabel` 保存完整标题并在 `resizeEvent` 用 `QFontMetrics.elidedText` 截断，行内不换行；标题取首条用户消息，悬停提示带条数、最后时间与末条预览。
- 会话切换走 `runtime.switch_session()`：除 `session_id` 外同步 `proactive_service` / `work` 的会话作用域并丢弃 `last_context` / 本轮召回，避免提醒、任务卡或记忆跨会话串台；生成中（`active_generation` 或 `stream_worker` 存在）拒绝切换与新建。
- 新建会话只在当前会话已有消息时创建 `chat_<hex>`，空会话直接复用并聚焦输入框；空会话仅在它是当前会话时出现在列表里，避免“点了新建却没说话”堆出一串空行。
- 顶部图标（新的任务/搜索/工作区/更多/加号/设置）由 `rail_icon()` 按主题 `muted` 色绘制，切换主题时随 `_apply_stylesheet()` 重绘，不依赖系统图标主题。

未做（明确排除）：会话重命名/置顶/删除入口（删除仍走管理台）、拖拽调整分栏宽度、Tk 界面的同等改版、参考项目的语音输入与 Live2D 形象层。

### M9 应用层契约与 Qt 适配器

- **优先级**：P1
- **状态**：已完成（2026-10-28）
- **依赖**：M1a、M6、M8.5
- **目标**：参考 Cyrene-Agent 的启动编排/IPC 契约分层与 shunguang-companion 的流式响应边界，在 Qt 与 `CompanionRuntime` 之间建立稳定应用服务层，为后续 Web/手机 transport adapter 保留同一业务入口。

工作项：

- [x] `app/contracts.py`：命令、运行上下文、运行状态、事件、审批视图与工作区快照契约。
- [x] `app/ports.py`：运行时端口定义，明确应用层依赖的是对话、模式、工作区与会话能力。
- [x] `app/service.py`：turn/run 生命周期、同会话 active-run 守卫、生成中变更拒绝、取消、同步运行、审批与撤销用例。
- [x] `app/bootstrap.py`：`create_application_service(runtime)` 组合入口。
- [x] `ui/stream.py`：Qt worker 改为消费 `ApplicationEvent`；停止只设置取消标志，继续读到 runtime 的 `done`。
- [x] `ui/qt_app.py`：核心操作改走 application service；`CompanionWindow` 同时接受 runtime 与 service；launch 支持两者。
- [x] `companion.py`：Qt 启动时显式构造 application service；CLI/Tk 暂时保持 runtime 入口。
- [x] `tests/test_companion_app.py`：新增 9 个应用层契约回归；全量 359 个通过。

落点（实际）：新增 `agent_framework/companion/app/{__init__,bootstrap,contracts,ports,service}.py` 与 `tests/test_companion_app.py`；修改 `agent_framework/companion/ui/stream.py`、`ui/qt_app.py`、`companion.py` 和 Qt 端到端测试。

实现要点：

- 采用进程内应用服务，不引入 Web 运行时。Qt 是第一个 adapter；后续 FastAPI/SSE 只负责把 HTTP 请求与事件流映射到同一 `CompanionApplicationService`，不重写记忆、工具、审批与执行内核。
- `begin_turn()` 固定 `run_id / session_id / mode / previous_message_id`，并在 run 启动时冻结工作区 `root + device + file_id`。该快照用于本轮上下文与审计；实际文件执行权限仍由提案 `expected_state.workspace` 的既有安全复核控制。
- 同一会话同时只允许一个 run；任何 run 活跃期间，模式、工作区、会话、审批确认/取消和撤销都被应用层拒绝，避免“生成中切走了上下文，分片却落到新会话/新目录”。
- `ApplicationEvent` 统一携带 `run_id / generation_id / session_id / sequence`，其中 run id 就是 generation id；Qt 继续按 generation 丢弃停止后的迟到事件。
- `done` 不以 runtime 自报为最终依据：应用层按 `previous_message_id` 与最终 `answer` 查询会话表，确认对应 assistant 消息已经落库后才补 `message_id / persisted=true` 并发送完成事件。没有 `done` 或落库未确认时，run 标记失败。
- `ApprovalView` 只暴露审批所需的只读摘要、风险、确认指纹与有效期；已处理提案不能再次确认。真实执行仍走 M1a 的 TTL、工作区身份、模式、idempotency 与回执账本。
- `StreamWorker.cancel()` 调用 `application.request_cancel(run_id)`，但不提前关闭生成器；只有 runtime 收尾并写入“已停止生成”后，应用层才确认 done。Qt 在停止态抑制迟到分片，保留已落库的停止结果。
- `CompanionWindow(runtime)` 自动包一层 service，保证既有调用与测试兼容；传入 `CompanionApplicationService` 时直接使用。Qt 端到端用例显式走 service，其余 smoke 用例走 runtime 兼容路径。
- 本轮不改 Tk/CLI 的调用边界，也不把管理台、人设导入等所有面板操作一次性迁入 service；它们继续作为 Qt 本地能力直接读取 runtime。M9 只收敛会话主链路与高风险副作用入口，避免扩大回归面。

未做（明确排除）：FastAPI/SSE 服务、Web/手机前端、跨进程事件总线、持久化 run 表、Tk/CLI 的 service 迁移、全部面板 API 化。

### M10 桌面 UI 体验优化（M10.1 / M10.2 / M10.3 已完成）

- **优先级**：P0
- **状态**：M10.1 已完成（2026-10-28）；M10.2 已完成（2026-10-29）；M10.3 已完成（2026-10-29）
- **依赖**：M8.5、M9
- **目标**：把当前“三栏聊天窗口”升级为“可观察、可审批、可追踪的陪伴工作台”。Cyrene-Agent 主要提供运行状态、工具/文件 Diff、右栏标签页与会话管理参考；shunguang-companion 主要提供消息层级、欢迎/空状态、工具与来源折叠、输入区和可访问性参考。
- **共同约束**：Qt 继续作为首个 adapter；新增查询与命令经 `CompanionApplicationService`，新代码不得在 Qt 中复制 runtime 状态源；不引入 Web/Electron；附件、语音、截屏、Live2D 等控件只在对应能力接通后显示；Tk 界面不在本轮同步范围。

#### M10.1 运行可观察与消息表达

- [x] 在应用服务中补充主聊天所需的只读 UI 投影，至少覆盖会话消息、当前 run、待审批摘要和右栏快照；Qt 新渲染路径不再直接拼装 runtime 私有状态。
- [x] 新增输入框上方的统一运行条，依据 `ApplicationEvent.sequence` 显示“思考 / 调用工具 / 生成回答 / 等待审批 / 完成 / 失败”、当前工具、耗时与停止动作；失败和取消必须有明确终态。
- [x] 消息支持安全 Markdown 子集、代码块与消息复制、日期分隔、头像/角色区分和弱化元信息；禁止执行原始 HTML，链接与代码块按不可信内容处理。
- [x] 重试/重新生成入口必须复用应用服务的新命令，不允许由 Qt 直接调用 runtime；若对应命令尚未实现，则不展示该入口，不伪造成功。
- [x] 修复流式自动滚动：用户停留底部时跟随新内容；用户主动上滚后保持阅读位置，并提供“回到最新消息 / 有新内容”提示。

落点（实际）：新增 `agent_framework/companion/app/projection.py`、`ui/markdown.py`、`ui/run_bar.py` 与 `tests/test_companion_markdown.py`；修改 `app/contracts.py`、`app/service.py`、`app/__init__.py`、`ui/inspector.py`、`ui/chat_view.py`、`ui/qt_app.py`、`ui/theme.py`、`tests/test_companion_app.py`、`tests/test_companion_ui_theme.py`、`tests/test_companion_qt_ui.py`。

实现要点：

- `CompanionApplicationService.ui_projection()` 返回 `UiProjection`：`TranscriptMessage`（消息 ID / 角色 / 正文 / 时间 / 模式）、`RunView`（run 状态、stage、sequence、detail、tool）、`ApprovalView` 元组与右栏 `ProjectionSection` 元组。Qt 的 `_transcript_blocks()` 与 `InspectorPanel` 只读投影，不再直接拼装 runtime 状态；面板仍保留无 `ui_projection()` 时的兼容回退。
- 运行阶段由应用层 `stage_for_event()` 单点推导，`started → thinking`、`delta → answering`、`tool → tool`、`reset → thinking`、`done → completed/cancelled`（已见 error 则为 failed）、`error → failed`；阶段与 `sequence` 同时写入 `RunContext` 与 `ApplicationEvent`，Qt 只做展示，不复制状态机。
- `RunBar` 按 run id 与严格递增 sequence 过滤迟到/乱序事件；开始显示“思考中”、调用工具时显示工具名与完成摘要、`done` 显示“已完成”并在 4 秒后自动隐藏，取消 3 秒后隐藏，失败保持可见并展示原因；等待审批单独进入“等待审批”并隐藏停止按钮。原“停止”按钮移入运行条，`window.stop_button` 保留为同一控件的别名。
- `ui/markdown.py` 先 `html.escape` 再做有限的 Markdown 替换：行内代码、加粗、http/https 链接、标题、无序/有序列表、引用与围栏代码块；未闭合代码围栏按流式半成品处理，原始 HTML 只会以转义文本出现。链接点击统一走 `ChatView.link_activated` → 二次确认后再 `QDesktopServices.openUrl`，不安全协议直接忽略。
- `MarkdownLabel` 用 `QTextDocument` 按实际宽度测量高度，流式尾部仍带 `▍` 光标；气泡标题行显示角色名、时间与“复制”按钮（复制未渲染的原文）；日期变化时插入分隔条，同一天只出现一次。
- 滚动跟随改为“粘底”策略：仅当滚动条位于底部附近时跟随新内容；用户上滚后保留位置，新内容到达时浮出“有新内容 · 回到最新”，点击后回到底部并恢复跟随。`verticalScrollBar().rangeChanged` 负责在换行高度完成布局后再滚到新底部。
- 重试/重新生成暂无对应的应用服务命令，因此本轮不展示该入口，Qt 也不直接调用 runtime。
- 本轮不开始内联审批/Diff、工具与来源展开、标签式右栏、响应式阈值、会话管理与双栏设置；这些留给 M10.2 / M10.3。

验收：

- 运行条各阶段与应用事件顺序一致，停止、失败、取消和迟到分片均有确定性 UI 状态。
- 消息中的 Markdown、代码、链接与超长文本不破坏布局，不执行脚本或原始 HTML。
- 用户上滚后流式输出不再强制拉回底部；回到底部后恢复自动跟随。
- 应用层 UI 投影有单元测试，Qt 端不重复实现 run 状态机。

#### M10.2 审批、证据与上下文抽屉

- [x] 将当前只含计划文本和按钮的确认区改为内联事务卡，展示目标、工作区、模式、风险、有效期、可撤销性与变更预览；已有文件优先显示 before/after Diff，新文件显示新增内容摘要，敏感内容截断。
- [x] 确认、取消、执行结果与撤销都在同一事务卡内闭环；审批数据只从 `ApprovalView` 和既有回执获取，不绕过 digest、TTL、模式、工作区、幂等与执行账本。
- [x] 工具回执改为可展开卡片，默认显示名称、状态与摘要，展开后显示参数/结果摘要、错误原因和来源；长结果通过既有 `tool_result` 句柄分段查看。
- [x] 右栏从固定卡片列表改为标签式上下文抽屉，至少提供“执行 / 文件 / 来源 / 上下文 / 任务”；仅在相关事件发生时自动打开，并支持关闭、拖动宽度和恢复宽度。
- [x] 建立响应式阈值：宽度不足时优先收起右栏，进一步收窄时把左栏改为抽屉；中部聊天区保持稳定阅读宽度。

落点（实际）：新增 `agent_framework/companion/ui/approval_card.py`；修改 `file_actions.py`、`runtime.py`、`app/contracts.py`、`app/service.py`、`app/ports.py`、`app/projection.py`、`app/__init__.py`、`ui/chat_view.py`、`ui/inspector.py`、`ui/state.py`、`ui/qt_app.py`、`ui/theme.py` 与对应测试。

实现要点：

- `NoteActionService.preview()` 是纯读路径：只校验工具、工作区快照与 `WorkspaceGuard` 路径，读取既有目标并计算 `difflib` 行差异，不创建目录、不写文件、不消耗提案。输出 `change / workspace / relative_path / diff / added / removed / truncated / omitted_lines / error`；Diff 最多 80 行、单行最多 180 字符，超出显示“另有 N 行未显示，确认后才会写入完整内容”。
- `runtime.preview_proposal()` 只返回当前会话的提案，附带 `proposal_expired()` 结果；`ApprovalView` 新增 `tool / workspace / expired / preview`，由应用服务统一填充，Qt 不直接读取提案或文件系统。
- 内联 `ApprovalCard` 替代原确认区并插入聊天流；展示目标、工作区、模式、风险、有效期、可撤销性与计划，已有文件显示 `- / + / 空格` 三类 Diff 行，新建文件显示新增摘要，敏感内容只截断展示。确认后卡片进入“正在执行”，成功显示回执摘要与撤销按钮，取消、失败、`unknown` 也都在同一张卡内给出明确终态。
- 过期提案在 `ApprovalCard` 中禁用确认并提示原因；应用服务 `confirm_action()` 也会拒绝过期、已处理和跨会话审批，卡片只是展示层，安全边界仍在服务层。执行仍走原有 digest、TTL、模式、工作区快照、幂等键与执行账本。
- 工具事件新增参数摘要（每个值最多 240 字符）、`ok / code / error / preview / sources / result_id / result_chars / result_truncated`，大结果仍落盘为 `tool_result` 句柄。`ToolCard` 默认显示名称、状态与摘要，展开后显示参数、结果预览、错误码与原因、前 5 条来源和完整结果句柄；展开状态按消息 ID 记忆，重渲染不丢失。
- 右栏改为 `QTabWidget` 五个标签：执行（当前状态/待确认提案/最近回执/详情）、文件（工作区、待写入目标、已写入路径）、来源（本轮工具来源）、上下文（上下文/技能/主动陪伴）、任务；收到工具来源或审批事件时自动切到相关标签，关闭按钮折叠右栏。
- 右栏与主区之间使用 `QSplitter`（handle 4px，宽度 220–420）；拖动结束写入 `ui_state.json` 的 `inspector_width`，重新打开右栏或重启时恢复。`resizeEvent` 中执行响应式：宽度 <940 收起右栏，<860 把左栏改为覆盖式抽屉（`rail_host` 宽度归零，抽屉按 `RAIL_WIDTH` 覆盖在画布上），窗口重新变宽时恢复用户原先的展开状态与宽度；780px 最小窗口下中部聊天区保持可用宽度。

验收（本轮结果）：

- 未确认动作不产生副作用：预览与提案只读测试覆盖新建、覆盖与截断，确认前工作区无文件。
- 过期、已处理、跨会话与生成中审批均不可误确认：服务层抛出 `ApprovalUnavailableError`，Qt 卡显示禁用与错误原因；生成中变更仍由 M9 的 idle 守卫拒绝。
- 审批卡与回执可追溯：`ApprovalView` 保留 action id、digest 与过期时间，工具卡保留 `result_id` 与来源，失败事件带错误码。
- 780px 下不出现三栏挤压：离屏几何测试覆盖 1100 → 900 → 780 → 1100 的收起与恢复顺序、左栏抽屉开合，确认/撤销与工具展开不影响滚动和文本选择。

#### M10.3 会话、输入与设置体验

- [x] 会话行补充置顶、重命名、删除、未读、相对时间和按日期分组，并提供 `Ctrl+N` 新建、`Ctrl+K` 搜索；如有持久字段需求，通过独立 schema 迁移与回归测试落地。
- [x] 输入区支持 2–5 行自动增长、每会话草稿、长度提示、模式/工作区/上下文胶囊；发送与停止使用固定位置的单一主按钮，避免控件跳动。
- [x] 按陪伴/工作模式提供有角色感的空状态、快捷开场或最近任务；空会话不再只显示“还没有对话”。
- [x] 设置中枢改为左侧分类、右侧内容的双栏结构，支持搜索、状态摘要和渐进展开；高频主题与浮窗设置保留快速入口。
- [x] 补齐键盘焦点、回车/空格操作、图标工具提示、屏幕阅读器可读名称、非颜色状态提示、字体缩放与减少动态效果；统一正文、界面文字、元信息和间距 token。

落点（实际）：`store.py`（迁移 v9、会话字段与草稿表、删除会话级联）、`runtime.py`、`ui/state.py`、`ui/theme.py`、`ui/chat_view.py`、`ui/settings.py`、`ui/inspector.py`、`ui/qt_app.py` 与对应测试。

实现要点：

- 迁移 v9：`sessions` 增加 `title / pinned_at / unread`，新增 `session_drafts(session_id, content, updated_at)`；旧库升级保留既有会话与消息，空库直接建到 v9。`store.rename_session()`（空白标题拒绝）、`set_session_pinned()`、`set_session_unread()`、`get_draft()/set_draft()`、`delete_session()`（连带消息、提案、回执、记忆、任务、提醒、草稿与会话级工具结果）均返回布尔结果供 UI 反馈。
- 会话列表按 `last_at` 分为“今天 / 昨天 / 最近 7 天 / 更早”，置顶会话仍排在最前；行内元信息显示置顶、相对时间（刚刚/N 分钟前/N 小时前/昨天/N 天前/日期）、未读与“当前”，并汇总进可访问名称与完整工具提示。`⋯` 菜单、右键菜单与键盘 `Return/Enter/Space`（切换）及 `Menu`（管理）共用同一组动作；重命名空标题、对已删除会话操作、生成中新建/切换都会在状态栏给出原因而不写入。
- `Ctrl+N` 新建会话（空会话直接聚焦输入框）、`Ctrl+K` 聚焦并全选搜索、`Ctrl+,` 打开设置；搜索按标题/话题/末条内容过滤并隐藏空分组。
- Composer 按字体行高在 2–5 行间自动增长，输入变化时重新测量文档宽度（隐藏窗口也能得到正确高度）；每会话草稿在切换、发送、停止与窗口关闭时持久化，运行中允许保存草稿但不允许切换会话。字数提示在超过 4000 字警告、超过 8000 字阻止发送并提示拆分；模式、工作区与上下文胶囊随模式/工作区/最近一次上下文构建刷新，并提供可访问名称。
- 发送与停止共用右下角同一主按钮：空闲时“发送（Enter）”，生成中变为“停止（Esc）”并切换 `mode` 状态色，宽度随字体缩放调整，避免布局跳动；停止后按钮显示“停止中”并在收到终态后恢复。
- `EmptyState` 按模式显示角色化标题、说明与三条快捷开场（点击填入输入框并聚焦），工作模式额外显示最近任务标题与状态；陪伴模式标题为“今天想聊点什么？”，工作模式为“把一件事交给我”，空会话不再只显示“还没有对话”。
- 设置中枢改为左栏分类（陪伴 / 工作 / 数据）+ 右侧页面栈：顶部搜索框（`Ctrl+F` 聚焦、`Esc` 清空）可过滤入口并按需生成结果行；每类顶部有状态摘要卡（人设/记忆数/情绪、任务/主动、主题/浮窗/数据库大小），默认只显示摘要，点“更多”渐进展开细节；底部保留“主题：…”与“桌面浮窗：开/关”快速入口。数据页新增字体缩放下拉（100/125/150/200%）与“减少动态效果”。
- `theme.py` 建立 `TYPE_TOKENS`（正文 13px、界面 12px、元信息 11px、标题 17px 等）、`SPACE_TOKENS`（xs/sm/md/lg）与 `FONT_SCALES`；`stylesheet(name, font_scale)` 以同一契约生成 QSS，并在缩放时统一放大所有 `font-size`。焦点样式覆盖主按钮、胶囊、会话行、设置导航与搜索、抽屉关闭等交互控件；图标按钮补齐工具提示与可访问名称，当前/置顶/未读等状态同时以文字表达，不只依赖颜色。
- 减少动态效果开启后，流式期间不再自动粘底，改用“回到最新消息”提示，避免长回复持续滚动；该设置写入 `ui_state.json` 并与字体缩放一样在新窗口恢复。

验收（本轮结果）：

- 置顶/重命名/未读/删除均有成功文案与失败原因；删除前二次确认并说明不可撤销；生成中新建、切换、删除被拒绝，草稿切换与关闭窗口时保存，不出现跨会话写入。
- 长文本按 2–5 行增长并在超长时阻止发送；`Shift+Enter` 换行、输入法组词期间 Enter 不触发发送；停止后迟到分片与按钮状态一致。
- 键盘可完成新建、搜索、会话切换与管理、发送/停止、设置导航与条目操作；空状态快捷按钮可 Tab 聚焦并以 Return/Space 触发；200% 字体下按钮宽度、输入行高、设置导航宽度与正文均随 token 放大，离屏截图覆盖 1366×768 与 1920×1080。
- 主题、字号与间距统一来自 `theme.py` token；正文/界面文字/元信息不再散落魔法字号。

明确排除：Tk 同等改版、Web/手机前端、常驻后端、语音输入、附件/截屏、Live2D、多角色视觉系统，以及在能力未实现时展示不可用控件。

### M11 Pixso Web UI 迁移

- **优先级**：P1
- **状态**：已完成（2026-10-29）
- **依赖**：M9 应用服务、M10 的全部界面契约
- **目标**：把 Pixso 作为唯一设计源，用 Web 前端取代 Qt 作为默认界面；Qt/Tk 仅作兼容降级保留，业务仍只经 `CompanionApplicationService`。

工作项：

- [x] 把 `ui/theme.py` 的语义色、字号、间距登记为 Pixso 变量与文字样式，重建 6 个画板：深色三栏主界面、珍珠白空状态、设置、管理台、桌面浮窗、780×560 窄窗。
- [x] 新增 `agent_framework/companion/web/`：本地 HTTP 适配器把启动快照、回合、停止、模式、工作区、会话、草稿、界面设置、人设、审批、撤销、主动与 7 页管理台查询映射为 `/api/*`；事件经 SSE 推送。
- [x] 新增 `web/assets/`：由 Pixso 画板派生的 HTML/CSS/JS，覆盖主界面、审批卡/工具卡、标签抽屉、设置双栏、管理台与浮窗；浏览器模式隐藏仅桌面壳层可用的窗口按钮。
- [x] 新增 `web/shell.py`：QtWebEngine 无边框主窗口、设置/管理台子窗口、透明置顶浮窗、几何持久化与 QWebChannel 宿主桥接；缺 WebEngine 时回退默认浏览器。
- [x] `companion.py` 默认 `--ui web`，新增 `--ui browser`，`qt`/`tk` 保留兼容；Windows 下设置 `QTWEBENGINE_DISABLE_SANDBOX=1`。
- [x] 新增 8 个适配器回归测试；全量 431 → 439。
- [x] M11.1 修复单实例唤回：`activation.py` 端点文件 + `/api/activate` + Windows 按 PID 显示窗口兜底；壳层注册队列信号唤回主窗口；无托盘时关闭即退出；新增 4 个测试，全量 439 → 443。

实现要点：

- 适配器只绑定 `127.0.0.1`、端口 0 随机分配，令牌由 `secrets.token_urlsafe(24)` 生成；页面通过 `?t=` 引导，API 同时接受 `X-Companion-Token`，校验用 `secrets.compare_digest`。静态资源按 `resolve()` 后 `relative_to` 检查，`..` 与绝对路径逃逸返回 403。
- `WebAdapter` 持有 `UiStateStore` 与订阅队列：`/api/events` 以 HTTP/1.1 chunked 持续输出 `data: {...}`，15 秒无事件发注释心跳；`ui`、`shutdown` 与回合事件共用同一扇出，`daemon_threads` 保证退出不被 SSE 连接拖住，连接中断按预期关闭。
- 页面路由为 `/`（主界面）、`/settings`、`/console`、`/overlay`；`/api/bootstrap` 一次返回会话、消息、提案、任务、主动、人设、技能、界面状态与情绪快照。中文情绪标签在适配器内向 `AffectService` 的内部值映射（平静/开心/低落/疲惫），回显统一使用 `mood_text`。
- 回合提交立即返回 `run_id`，后台线程消费应用服务事件并广播；前端只消费 `delta / tool / done / error / reset`，不复制 run 状态机。停止、审批、撤销仍走服务层命令，没有新增旁路。
- `web/shell.py` 的主窗口为无边框 1000×700（最小 780×560），设置 680×560、管理台 1040×720、浮窗 300×250 且透明置顶；位置与尺寸写入 `ui_state.json`，浮窗位置按最近屏幕夹取。`ShellHost` 暴露 `ready / minimize / hide / toggleMaximize / moveWindow / openSettings / openConsole / openMain / moveOverlay / hideOverlay / setOverlayVisible`，页面按钮不再自己拼窗口逻辑。
- 单实例与唤回（M11.1）：`launch_web` 启动后把 `port/token` 写入 `data/.companion.endpoint`，退出时删除；`/api/activate` 只接受同一令牌，命中壳层注册的回调时返回 `shown=true`，纯浏览器实例返回 `shown=false` 与页面 URL 由第二个进程直接打开。`companion.py` 抢锁失败后依次尝试端点唤回 → Windows `EnumWindows` 按锁文件 PID 显示该进程的非工具窗口（标题“数字分身”优先）→ 才提示并以 3 退出（`pythonw` 无 stderr 时改用 `MessageBoxW`）。壳层用 `QObject.Signal + Qt.QueuedConnection` 把 HTTP 线程的请求安全投递回 GUI 线程。
- QtWebEngine 在 http 页面无法加载 `qrc:///qtwebchannel/qwebchannel.js`，因此把同版本脚本落到 `/assets/qwebchannel.js` 由适配器提供；Windows 上若不禁用渲染器沙箱，WebEngine 会因 GLES 上下文创建失败直接退出（TerminationStatus 18），入口统一设置 `QTWEBENGINE_DISABLE_SANDBOX=1`。
- 浏览器模式没有宿主桥：样式隐藏最小化/关闭按钮，设置与管理台的“返回”改为 `/?t=...`，浮窗改为居中卡片；这些分支只为本地调试与降级，不影响壳层路径。

验收：

- 四个页面与全部 `/api/*` 在临时数据目录下可用；令牌缺失或错误返回 403，路径逃逸被拒绝。
- 通过 SSE 完成一次完整回合：`delta` 增量、`done` 收束，用户与助手消息均落库；`/api/ui` 更新写入 `ui_state.json` 并向订阅者广播。
- 设置页中文情绪纠正写入 `positive` 且回显“开心”；管理台 7 页均可返回记录。
- 浏览器与 Qt 壳层目视核对：主界面三栏、窗口按钮、模式切换、右栏开关、设置子窗口与浮窗均与 Pixso 画板一致；`--ui qt` 仍可启动旧界面。
- 关闭主窗口只隐藏到托盘并给出一次提示；隐藏后再次运行 `python companion.py` 会唤回正在运行的实例并返回 0，不再出现“第二次打不开”；无托盘时关闭直接退出。

明确排除：远程监听与多客户端、HTTPS/账号体系、移动端布局、把管理台的全部写操作 API 化、Electron、删除 Qt/Tk 兼容入口；Qt/Tk 的移除需另立里程碑并确认兜底方案。

### M12 Web UI 精细化（参考项目对比整改）

- **优先级**：P1（排在阶段 5 语音/Live2D 之前）
- **状态**：进行中（2026-10-29 登记；M12.1、M12.2 已完成，M12.3 进行中（C1/C2/C4 已交付，剩 C3/C5/C8），M12.4–M12.5 未开始）
- **依赖**：M10 的界面契约与 M11 的 Web 前端/投影；M12.3 的重新生成需要先新增应用服务命令
- **目标**：把 M10.3 已承诺的字体缩放与窄窗可用性在默认 Web 界面上真正兑现，并补齐与参考项目差距最大的消息表达、浮窗能力与视觉纵深。不改执行链路、不新增数据表、不引入前端框架或构建工具链。

#### M12.1 缩放正确性与视觉 token（U3 / U8 / U9 / U10 / U11 / U12 / U15 / C9）— 已完成（2026-10-29）

- [x] `tokens.css` 移植 `SPACE_TOKENS`，按 `--scale` 输出 `--sp-*`；把 app/settings/console/overlay 的裸像素 padding/gap 收敛到 token 档。实际取 5 档（`--sp-xs..--sp-xl` = 2/4/8/12/18px），并按 `--sp-scale = 0.5 + scale×0.5` 半速增长——字号翻倍时留白翻倍会把窗口掏空。
- [x] 固定 `height` 的控件改 `min-height: calc(字体度量)`：`.pill`、`.segmented button`、`.ghost`、`.inspector-tabs button`、`.new-task`、`.field`、`.table` 行一律允许随字号长高。表格行保留 `height`（单元格上它就是最小值，`min-height` 会被忽略）；`button` 补 `inline-flex + align-items` 才能在 min-height 下垂直居中。
- [x] `.inspector-tabs` 允许横向滚动并加边缘淡出；窄窗把 `data-cmd` 窗口按钮退化为图标而不是 `display:none`，保证 780px 下最小化/关闭仍可用。滚动容器会裁掉负偏移的 `::after` 下划线，改用 `box-shadow: inset 0 -2px 0`；边缘淡出未做（会遮挡末位标签可读性），改为标签 `flex:none` + 隐藏滚动条。断点另按字号归一化为 `responsiveWidth()`，200% 时 1000×700 会像 500px 一样收起两栏，否则中栏被挤到 420px 仍然溢出。
- [x] `.send` 与 composer 胶囊按字号计算最小宽度；补 `--font-mono`、`--radius-pill`、`--radius-card`，把使用 19 次的 `8px` 收进 token；行高与字重收敛为语义 token（`--lh-tight/body/relaxed`、`--fw-medium/semibold`）。
- [x] 可访问性：会话行与标签改为语义按钮，补 `role=tablist` / `aria-selected`、chat 区 `role=log aria-live=polite`、可见焦点环与键盘可达路径，对齐 Qt 在 M10.3 已建立的契约。管理台表格行补 `tabindex=0` 与 Enter/Space 触发；删除全部 `outline:none` 让 `:focus-visible` 生效。
- [x] 浮窗跟随字号：`/api/overlay` 载荷新增 `font_scale`，`overlay.js` 写入 `--scale`；`.pet` 改 flex 容器并让 SVG `display:block`，消除基线留白导致的 6–10px 纵向溢出。
- **出口**（已达成）：100/125/150/200% 四档与 780×560 下无文字裁切、无溢出、窗口控制可用；离屏截图进入回归——由 `web/layout_audit.py` 用真实 Chromium 跑 20 个页面/尺寸/档位组合，`tests/test_companion_web_layout.py` 断言零溢出与图标退化，截图输出到 `data/layout_audit/`（gitignore）。

#### M12.2 排版层级（U1 / U2 / U4 / U5）— 已完成（2026-10-29）

- [x] 字阶整档上调并补齐顶部：`--fs-caption` 11 / `--fs-meta` 12 / `--fs-ui` 13 / `--fs-body` 14 / `--fs-large` 16 / `--fs-subtitle` 18 / `--fs-display` 24，七档全部由 `calc(Npx * var(--scale))` 推导；Qt 侧 `TYPE_TOKENS` 取同一组数值，两端一致由 `tests/test_companion_web_style.py` 逐档断言。**两端同步**：`ui/theme.py` 的 41 处硬编码 QSS `font-size` 全部改走 `_type("<token>")`，保持 `stylesheet()` 后置 `re.sub` 缩放通道不变；`ui/markdown.py` 的标题按 level 走 large/body（原来的 `heading` token 并入 display）；`ui/qt_app.py` 浮窗气泡改用 `font_px("ui", self._font_scale)`，不再写死 12px。Web 从未引用的 `--fs-title` 由 subtitle/display 取代。
- [x] 拉开标题阶梯：空状态标题 17px → `--fs-display` 24px、主窗人设名 → `--fs-large`、设置/管理台窗口标题与管理台情绪标题 → `--fs-subtitle`。偏离：`.mood-card .mood` 用 subtitle 而非 display（1040px 面板里 24px 会挤掉情绪条），人设名取 16 而非计划的 15（16 是阶梯上的真实一档）。
- [x] 行高收敛为三档：`--lh-control` 1 / `--lh-meta` 1.5 / `--lh-body` 1.7，五个样式表零裸 `line-height` 数值（静态断言守住）。例外：`.new-task .plus` 与 `.close` 这类图标字形用 `--lh-control` 会把 "+" 的墨迹裁掉 2px（em 盒小于字形），实测后改走 `--lh-meta` 并在样式表留注释。
- [x] 阅读列改为按字号计量：`--read-measure = --fs-body × 42`（每行 42 个正文宽度）、`--col-measure = read-measure / 0.74`（还原气泡的 74% 列宽），`.chat-inner`/`.composer-wrap`/`.approval` 按这两档取宽；`.diff` 的可见高度与 pills 的行数上限改为"字号 × 行高 × 行数"换算，不再写死像素。四个页面样式表零 `max-width: calc(Npx`。**偏离**：计划里的 `rem` 在此不可用——项目没有 `html { font-size }`，`rem` 只会解析到 UA 的 16px 且完全不随 `--scale` 变，等于把缩放通道关掉，故改用 `--read-measure` 承担同一职责。U5 原来的"每行 58–60 字"也是按 780px 整列估的，气泡实际 74% 列宽，基准已修正；审计实测每行 18–37 字。
- [x] 控件度量随字阶重导：`--ctl-*` 一律由 `--fs-*` 推导，`--ctl-bar` 改按栏内最大的控件标签（`--fs-large × 2.5`）而非页面标题测量；开关的宽高/滑块位置也改为 `--ctl-switch-*`，200% 下不再压住文字。静态断言禁止任何 `--ctl-*` 引用 display 档。
- [x] 200% 下的顶栏退化：新增 `body.compact` 规则，把窗口操作与模式段的文字标签收起、只留图标，并隐藏与运行条重复的人设状态文本。
- **出口**（已达成）：同屏正文（body 14 / ui 13）与元信息（meta 12 / caption 11）层级可辨，`layout_audit` 20 个组合零裁切零溢出；字号放大时行长同步收窄——1000×700 下每行 23/36/33/24 字、780×560 下 37/30/25/18 字（100/125/150/200%），无超宽行。**未做**：Pixso 画板与 `theme.py` 的视觉同步仍需设计侧手工对齐（R21 的代码侧残留已清零，画板侧未清零）。

#### M12.3 消息与运行表达（C1 / C2 / C3 / C4 / C5 / C8）

- [x] Web Markdown 子集先对齐 `ui/markdown.py`，再补表格、嵌套列表、删除线与代码块语言标签 + 复制按钮；仍是"先转义再渲染"，高亮脚本本地 vendored（遵守 R20）；Qt/Web 用同一组样本做渲染断言。**2026-10-30 交付**：`ui/markdown.py` 与新增的 `web/assets/markdown.js` 是同一套语法的两个实现，输出**逐字节相等**（`tests/fixtures/markdown_samples.json` 的 21 个样本由 node 逐条比对），渲染层不再各自演化；两端只保留各自的呈现（Qt 由 `markdown.qt_style(font_scale)` 生成 `<style>` 块 + QSS，Web 由 `app.css` 的 `.md-*` 规则）。新增表格（`table.md-table`，按分隔行取 left/center/right 对齐，列数不符时回退为普通段落而不是画出半截表）、层级列表（`<li>` 内嵌套，深度夹在 0–3）、删除线、代码块语言标签（`div.md-lang`）。Web 的复制按钮由渲染后的 DOM 装饰（`decorateCode()` 把 `pre.md-code` 包进 `.code-block` 并追加 `button.code-copy`，`data-copy` 复用已有委托），**renderer 输出因此仍与 Qt 完全一致**。标题改用 `<div class="md-h md-hN"><b>`：Qt 的 `h1..h3` 会强制约 1.5× 相对字号且忽略 `font-size`，类选择器才能让标题跟着字号档走（16/24/32px）。高亮未引入：shiki 一类需要构建链或数 MB 运行时，超出 D56 的零构建边界，且 Qt 端无法复用（见 D61）。
- [x] 上下文用量改由投影提供 `context_used / context_limit` 数值字段，前端不再正则解析展示文案；显示为环形仪表 + 阈值变色 + "查看上下文"入口。**2026-10-30 交付**：新增 `ContextView`（`app/contracts.py`）与单点推导 `build_context_view()`（`app/projection.py`），详情面板文案与两端数字共用同一份推导；`/api/bootstrap` 的 `context` 字段随投影走（`plain()` 只序列化字段，`percent` 由前端用两个整数算出，因此不新增端点）；Web 胶囊改为 conic-gradient 环 + 70/90% 阈值变色，点击仍打开上下文标签；Qt 端 `refresh_composer_hint()` 不再直读 `runtime.last_context`，改取同一投影（关闭 R22 的一半漂移）。前端 `contextPercent()` 正则抽取已删除。
- [x] 运行条按已有 `sequence / stage / detail` 展开为可折叠步骤时间线，终态仍单点来自投影。**2026-10-30 交付**：契约新增 `RunStep(sequence, stage, at, detail, tool)` 与 `RunView.steps`，由 `RunContext.steps` 在服务层单点记录——`_record_step()` 把相邻的 `(stage, detail, tool)` 相同行折叠成一行（流式 20 个 delta 只留一条"生成回答"），工具运行/完成行携带工具名与摘要，失败与取消终态也各记一行；`done` 后 run 释放、`run_view()` 归 `None`，因此时间线只描述进行中的这一轮，不需要额外截断。Web 端 `#runSteps` 是 `<ol>` 且**在 `role=status` 之外**（避免每步都读屏播报），折叠按钮带 `aria-expanded/aria-controls`，展开高度按 `--ctl-bar × 3` 封顶、内部滚动并自动跟到底；Qt 端 `RunBar` 同一套语义（`runStepsToggle` + `QScrollArea#runStepsArea`，可见 3 行按 `fontMetrics().height()` 计算）。两端都只消费投影里的 `steps`，不自行推导阶段，也不新增"已完成"文案；时间格式由新增的 `ui/timefmt.py::clock()` 与前端各自渲染 `at`。**修复的产品缺陷（D60）**：`web/server.py::_stream()` 在 `Transfer-Encoding: chunked` 下把首帧 `retry: 1000\n\n` 未经分帧直接写出，Chromium 立即判 `net::ERR_INVALID_CHUNKED_ENCODING` 并断开，`EventSource` 每次重连都死在同一处——即浏览器/壳层里 `/api/events` 从未真正工作过，发送一轮后界面不更新、运行条不出现，跨窗口广播同样失效；改为 `_write_chunk()` 统一分帧并在断开时补终止块，另修一个前端缺陷（折叠按钮原从 `state.data.run` 重绘，快照为空时点击无反应，改为缓存 `state.steps`）。
- [ ] 气泡 hover 提供 重新生成 / 存为记忆 / 跳转来源；重新生成必须先有 `CompanionApplicationService` 命令，服务层没有命令前不放按钮（沿用 D49 能力门控）。
- [ ] 生成中的输入进入前端待发队列（pending dock），run 结束按序提交；队列项可撤回与编辑，切换会话时清空并提示。
- [ ] 消息列表按 message id 键控增量更新，流式只更新当前气泡，历史不整列表重建。
- **出口**：一次带表格与代码的长回复在两端渲染一致；上下文与运行阶段不再依赖前端字符串解析；20 条以上历史流式无整列表重绘。**进度**：第一项已达成（C1：两端渲染逐字节相等，2026-10-30），第二项已达成（C2 环形仪表 + C4 投影步骤时间线，2026-10-30），第三项待 C8。
- **子片顺序（2026-10-30 定）**：C2（已完成）→ C4 运行步骤时间线（已完成）→ C1 Markdown 子集对齐（已完成）→ C8 消息列表键控增量更新 → C5 忙时待发队列 → C3 消息级操作。C3 排最后是因为"重新生成"要新增会删改已存消息的应用服务命令，属数据变更而非界面表达，**开工前需用户确认**。

#### M12.4 桌面浮窗能力（C6 / C7 / C10）

- [ ] 抽 `assets/avatar.js` 供主界面与浮窗共用，把 `app.js:174` 与 `overlay.js:20` 两份 `avatarSvg` 收成一个挂载点，情绪档位与 `AffectService` 标签一致。**2026-10-30 二次修订**：本条只剩"归一挂载点"这一项价值——原计划的"纯 CSS 呼吸/眨眼待机动画"取消，且这两份程序化 SVG 将在 M13.3 **整体删除**（Live2D 是桌宠的唯一载体，不设回退档，见 D58）；因此这里的目标是"换载体时只改一处"，不是"让旧形象更好看"。同一挂载点还要承接 M13.1 的静态头像（主界面头像位）。
- [ ] 恢复浮窗右键菜单（打开聊天 / 显示隐藏主窗 / 勿扰 / 收起浮窗）并增加 hover 快捷按钮；主动消息改为气泡队列 + 逐条节奏，点击或超时后出队。
- [ ] 支持折叠成小球、拖拽边缘吸附与全屏应用自动隐藏；位置仍写 `ui_state.json`，保留最近屏幕夹取。
- [ ] 浮窗改订阅 `/api/events`，轮询降级为断线兜底。
- **出口**：真实桌面人工确认（R14）"不说话时不打扰、说话时找得到入口"；连续三条主动消息不互相覆盖。

#### M12.5 视觉纵深与密度（U6 / U7 / U13 / U14 / U16）

- [ ] 阴影分三档并加 accent glow；`--shadow` / `--scrim` / blur 强度按主题定义，`pearl-white` 不再沿用深色的 0.28 黑阴影。
- [ ] 顶栏、composer 与浮窗气泡引入半透明 surface + `backdrop-filter`，rail 用主题渐变，空状态加低透明度氛围层；取值全部来自 token。**顺序反转后的新增约束（2026-10-30）**：M12.5 现在跑在 M13.2（可替换背景）之前，因此叠加层必须拆成"内容表面"与"背景遮罩"两个独立 token——遮罩本切片只给主题默认值并留 `--bg-scrim` 这一处可覆盖的缝，M13.2 只需在有背景图时改这一个值，不得回头改阴影/模糊/氛围层取值。
- [ ] 气泡改不对称圆角 + 小尾巴并按角色镜像；复制等消息操作默认隐藏、hover 或焦点时出现（不牺牲键盘可达）。
- [ ] 模式与工作区去掉重复控件（保留单一状态源），统一内容列宽，rail 支持折叠到图标态（含过渡与 reduce-motion 回退）。
- **出口**：两套主题在浅色不发灰、深色不脏；新增模糊层不引入 R19 的布局膨胀。

实现要点（沿用现有边界）：

- 改动面限于 `web/assets/*.css|js`、`app/projection.py` 的只读字段、`ui/theme.py` 与 Pixso token 表；除 M12.3 的重新生成命令外不新增 `/api/*`，不新增数据库迁移。
- 不引入 Tailwind/React/Electron：`clamp()`、`min-height` 与 token 计算已能覆盖参考项目的观感长板，引入框架会把一次视觉整改变成构建与状态同步工程（见 D56）。
- 语音、附件、Live2D/VRM 仍按 D49 能力门控，后端未实现前不放控件。

验收：

- 443 个测试基线不回退（当前 **510**）；新增 token/投影层断言（spacing token 覆盖、`context_used` 字段存在、控件不再使用固定 `height`）。M12.1 已交付 spacing/度量/圆角/等宽 token 与“控件不得使用固定 height”的静态断言；M12.2 已交付两端共用字阶（Web `--fs-*` == `theme.TYPE_TOKENS`）、三档行高、`--read-measure` 按字号计量与 `--ctl-*` 不得引用 display 档的静态断言，浏览器侧另加每行 16–45 字与聊天区 ≥25% 两条；`context_used` 投影字段随 M12.3 交付（2026-10-30 已完成：投影/`/api/bootstrap`/两端各 1 组用例）；C4 的 `RunView.steps` 与两端时间线于同日交付（服务层折叠 2 + Web 端含 SSE 原始 socket 分帧 2 + 静态契约 5 + Qt 主题/离屏端到端 3 = 12 个用例）；C1 的 21 个共享 Markdown 样本与两端样式契约于同日交付（16 个用例）。
- 100/125/150/200% × 1000×700 与 780×560 共 8 张离屏截图核对无裁切/溢出/丢失窗口控制。M12.1 起由 `python -m agent_framework.companion.web.layout_audit --out data/layout_audit` 生成（另含设置/管理台/浮窗共 20 张），并由 `tests/test_companion_web_layout.py` 常驻回归。
- 两端 Markdown 同一组样本渲染一致；`--ui qt` 同步 token 后观感不劣于改版前。M12.2 已把字阶同步进 `ui/theme.py`/`ui/markdown.py`/浮窗气泡，Qt 侧 41 个离屏用例（含 200% 档 `font-size: 28px`）通过；M12.3 的 C1 于 2026-10-30 关闭后，两端由 `tests/fixtures/markdown_samples.json` 的 21 个样本断言输出逐字节相等（node 驱动），Qt 气泡正文也随字号档缩放。

明确排除：Live2D/VRM 模型与素材接入、角色贴纸与素材分发（R10 仍适用）、远程/移动端布局、管理台除虚拟列表外的重构、CSS-in-JS 或构建工具链、把 Qt 界面重新设计一遍。上述"排除"不等于不做，而是自 2026-10-30 起改由下面的 M13 承接。

### M13 角色素材接入（Live2D 桌宠 / 静态头像 / 可替换背景）

- **优先级**：P2（阶段 5 的首个切片）
- **状态**：计划中（2026-10-30 登记，同日按用户确认的"完全替换"语义修订；素材槽位目录与说明文件已预留，**代码尚未读取**，M13 自身未动测试基线——登记时为 472，M12.3 已推进到 494）
- **依赖**：**M12 全部收口**（M12.3 → M12.4 → M12.5 完成后才开工，2026-10-30 决定，撤销此前把 M13.1/M13.2 插在 M12.4 与 M12.5 之间的细化）；M12.4 归一出来的 `assets/avatar.js` 挂载点；M9 应用服务与 M11 的 `/api/ui` 白名单 + SSE 广播通道
- **目标**：把**桌面形象（桌宠）完全替换为用户自备的 Live2D 模型**，同时让静态头像与窗口背景可替换。四条边界——不引入构建工具链（D56）、不把任何第三方素材打进安装包（技术方案阶段 5 验收）、不改变权限与记忆边界（素材只是展示层资源，不进上下文）、**替换后不存在"程序化桌宠"这条退路**。

三项已由用户确认的决定（2026-10-30），它们就是"完全替换"的含义，后续切片不得自行软化：

1. **不设回退档**：Live2D 不可用时**桌宠功能即失效**——浮窗不显示，原因在设置页与 `doctor.py` 可读；既不退回旧程序化 SVG 形象，也不放中性占位图。
2. **M12 先全部做完**：顺序 M12.3 → M12.4 → M12.5 → M13.1 → M13.2 → M13.3，M13 不与 M12 剩余切片交错。
3. **Web 透明置顶浮窗是桌面形象的唯一载体**：Qt 原生 `AvatarOverlay` 与 QPainter 程序化形象随 M13.3 一起删除，桌宠不再有两套实现。

素材槽位（本轮已预留，一层目录 = 一个人设，目录名与 `data/personas/<persona_id>/` 一致）：

```
data/assets/<persona_id>/
  avatar.png | avatar.webp | avatar.jpg   # 静态头像：主界面/设置/管理台的头像位与托盘图标
  background.jpg                          # 可选：该角色的窗口背景
  live2d/model3.json                      # 桌宠唯一载体入口，.moc3/贴图/物理/表情放同目录
```

- 程序**只读不写**：不生成、不改名、不清理这里的文件；删除素材即彻底删除，与记忆导出/删除同一边界。
- `.gitignore` 只放行 `data/assets/README.md` 与 `data/assets/*/README.md`，素材文件与 `live2d/` 子目录整体忽略，因此素材既不进版本库也不进安装包（R10）。
- **没有回退链**（2026-10-30 修订，原登记的"Live2D → 静态头像 → 程序化 SVG"作废）：`live2d/` 决定桌宠是否存在，`avatar.*` 只决定头像位与托盘图标是否有图，两者互不兜底；背景缺失回退主题纯色是唯一的降级，因为"没有背景图"本来就是一种正常配置而不是故障。

#### M13.1 素材槽位与静态头像

- [ ] 新增只读解析（`persona.py` 侧或新的 `assets.py`）：按当前人设解析出 `AvatarAsset(kind, path, size)` 与 `Live2dEntry(path, size)`，扩展名白名单、单文件体积上限与"目录不存在/文件损坏即无素材"三类判定都收敛在这一处。
- [ ] 新增 **token 门控**的 `GET /api/asset/<slot>`：`web/server.py` 的 `_asset(name, base=…)` 已支持换根目录，但 `/assets/` 前缀不校验令牌，直接复用会把数据目录变成任意文件读取面——必须走 `/api/*` 的 `_authorized` 通道，并补扩展名白名单与 `Content-Length` 上限。
- [ ] 状态只有"文件在不在"这一档，**不加 `avatar_enabled` 开关**（2026-10-30 修订，撤销登记时的同名计划）：多一个开关就多一个"开关开着但没素材"的状态，而素材目录本身就是用户可见的唯一控制面；需要的下发通道仍照旧——`bootstrap()["ui"]`、`overlay()`、`console_page()` 与 SSE `{"type":"ui"}` 共用 `UiStateStore` 同一份状态。
- [ ] 主界面/设置/管理台的头像位改渲染 `GET /api/asset/avatar`；**无素材时该位置空**，不再绘制程序化 SVG，也不放首字母或占位图。`overlay.js`/`app.js` 的 `avatarSvg` 在本切片仍保留（浮窗尚未换载体），删除在 M13.3，过渡期明确为"M13.1～M13.2 之间桌宠仍是旧形象"。
- [ ] `doctor.py` 新增 `Character assets` 检查：槽位目录是否存在、当前人设有无 `avatar.*` 与 `live2d/model3.json`、单文件是否超体积阈值。未开启浮窗时缺素材只给 WARN（正常状态）；**用户已开启桌面浮窗却没有可用 Live2D 时给 FAIL**——因为按决定 ①，那意味着一项已声明开启的功能实际不可用，静默 WARN 会把功能缺失伪装成正常（2026-10-30 修订，原"一律 WARN 不 FAIL"作废）。
- **出口**：放入 `avatar.png` 后主界面、设置与管理台的头像位同步更新，删除后位置留空且不报错；`/api/asset` 读取 `data/assets/` 之外的任何路径（含 `..`、绝对路径、符号链接）必须被拒；`layout_audit` 的 20 组合在"无头像"状态下仍零裁切（空位不得撑出或挤压顶栏）。

#### M13.2 可替换窗口背景

- [ ] 背景来源即 `data/assets/<persona_id>/background.*`；设置页"数据与外观"增加选择与清除，状态走 `ui_state.json` + `/api/ui` + SSE 广播（与主题、字体缩放同一条链路）。
- [ ] 作用范围：**主窗、设置窗、管理台窗**；桌面浮窗保持透明置顶不变——给浮窗加背景图会破坏"桌宠贴在桌面上"的观感，也影响点击穿透判定。
- [ ] 可读性由主题派生的自动遮罩承担：直接使用 M12.5 已经预留的 `--bg-scrim` 缝（本切片只负责"有背景图时给它赋值"，不回头改阴影/模糊/氛围层——顺序在 2026-10-30 由"M13.2 先于 M12.5"改为"M12.5 先于 M13.2"，这条缝就是为了让反转不产生返工），正文/元信息/控件三档文字在任意图片上都保持对比；Qt 侧 `stylesheet()` 同步 `background-image` 与遮罩色。用户只选图，不调节遮罩、模糊与平铺参数。
- [ ] 壳层 `set_theme_background()` 继续只负责 WebEngine 的打底色（避免白屏闪烁），背景图由页面 CSS 承担，不新增 Qt 侧图片通道。
- **出口**：`layout_audit` 增加"带背景图"的组合或人工截图核对，三档文字与审批卡、运行条在背景上仍清晰；未设背景时观感与 M12.5 之前完全一致。

#### M13.3 Live2D 桌宠运行时与旧桌宠下线

本切片是"完全替换"落地的地方：Live2D 成为桌宠的唯一载体，程序化形象连同它的第二套实现一起删除，不设回退档。

- [ ] **前置门槛（先于任何代码）**：① 确认 Cubism Core 的再分发许可、模型文件的来源与授权，结论写进技术方案阶段 5 与本节；② 技术验证（R25）——在 `web/shell.py` 现有的 OVERLAY 窗（300×250、无边框、透明、置顶、点击穿透）里跑通 WebGL：透明合成是否保留、`QWebEngineView` 的 GL 上下文在置顶层叠窗口下是否稳定、命中区与穿透如何共存、待机帧率与 CPU 量级。任一不成立就先解决或改壳层方案，不要把结论留给接入阶段。
- [ ] 接入路线沿用技术方案阶段 5：`QWebEngineView` + PixiJS + `pixi-live2d-display`，脚本与运行时作为本地静态资源随包（不含任何角色素材），仍是无构建链的手写前端（D56）。
- [ ] **不可用即失效**：缺 `live2d/model3.json`、缺 Cubism Core、模型损坏、WebGL 不可用——四种情况一律**不启动浮窗形象**，设置页"桌面浮窗"开关置灰并在同处显示原因，`doctor.py` 的 `Character assets` 按 M13.1 的规则给 FAIL（已开启却没有可用模型）或 WARN（未开启）；不弹打断式对话框，也**不加载任何替代形象**。
- [ ] 主动与回执消息不随形象失效而丢失：气泡原本是画在浮窗上的，浮窗不显示时改为主窗口的未读/主动面板入口 + 托盘提示，消息本身仍走既有的 `proactive`/`work` 回执链路落库。即"形象的缺失不影响消息的可达"，不静默丢弃。
- [ ] 表情/动作由既有 `AffectService` 情绪标签驱动：按标签名与 `model3.json` 里 `Expressions[].Name` 做宽松同名匹配（`calm` / `warm` / `low` 这类），匹配不到就保持模型默认姿态——这是表情级的降级，不是形象级的回退。**不引入用户侧映射文件**：素材目录里再放一份配置就是第二个状态源，与 D57「不引入清单文件」同一条理由；确有需要的角色可以走人设包已有的文本字段，由 M13.3 实施时评估。口型随 TTS 播放进度，属阶段 5 后续语音切片，本切片只接情绪。
- [ ] 既有约定要一并跟上：`reduce_motion` 关闭 idle 动作与物理、只保留静态帧与表情切换；`--scale`（100/125/150/200%）与浮窗尺寸缩放作用于舞台容器而非模型内部字号，气泡文字仍走 `--fs-*` 字阶；浮窗位置、`overlay_visible` 与拖拽吸附继续读写 `ui_state.json` 同一批键，不因换载体新增状态源。
- [ ] **删除清单**（与 Live2D 同一切片完成，避免出现"没有形象"的空窗期）：`ui/qt_app.py:449 AvatarWidget` 的 QPainter 程序化绘制与 `:536 AvatarOverlay`（Qt 原生浮窗整体下线）、`ui/tray.py:13 build_avatar_icon()`、`ui/theme.py:143 avatar_palette()` 及其 `avatar_mood_*` token、M12.4 归一出来的 `assets/avatar.js` 里的程序化 SVG 分支（`app.js:174`、`overlay.js:20` 两份拷贝此时已并为一个模块）。托盘与窗口图标改为"有 `avatar.*` 用之，否则用不含角色的应用通用图标"——图标不是桌宠，不需要角色形象。
- [ ] **连带后果要如实写进文档**：`--ui qt` / `--ui tk` 兼容入口不再提供桌面浮窗（桌宠只有 Web 壳层一个载体）。在这两个入口下开启浮窗开关时给出明确提示"桌面浮窗需要 Web 界面（`--ui web`）"，不静默无反应。
- [ ] 测试随删除收敛：`tests/test_companion_qt_ui.py:1038/1048` 的 `AvatarOverlay` 位置恢复与屏幕夹取用例改测 Web 壳层的浮窗状态持久化与夹取（或按 `--ui qt` 不再有此能力删除），`tests/test_companion_ui_theme.py:94-102` 的 `avatar_palette` 断言随函数删除；新增 `/api/asset` 读取面、失效原因下发与 SSE 广播的适配器用例。**基线数会因删除而下降，必须在变更记录里写清删了什么，不能让人误读成回归。**
- **出口**：真实桌面人工确认（R14/R23）模型可显示、命中区可点击、点击穿透与拖拽共存、待机 CPU 与内存可接受；移除 `live2d/` 后桌宠入口置灰且原因可读，主窗口、提醒与回执不受影响；关闭"显示浮窗"再打开，位置与吸附状态不丢。

明确排除：素材市场与在线下载、多角色同屏、把素材打进安装包、VRM/3D、模型编辑与物理调参、语音（另立切片）、为旧程序化形象保留任何形式的兜底显示。

实现要点：**M12.3 → M12.4 → M12.5 全部完成后**才进入 M13，M13 内部按 **M13.1 → M13.2 → M13.3** 推进（2026-10-30 决定，撤销登记时"M13.1/M13.2 插在 M12.4 与 M12.5 之间"的交错顺序）。M13.3 是"接入 + 删除"的单一切片——旧形象的删除与 Live2D 的上路在同一次改动里完成，因此中间不存在"桌宠消失"的版本；语音与打包仍排在 M13 之后。

## 6. 里程碑与顺序

以 2026-10-23 为第 1 周，按单人开发估算；周数是量级，不是承诺。

| 里程碑 | 周次 | 内容 | 出口条件 |
|---|---|---|---|
| M0 基线 | 已完成 | 现状审计、五项目调研、路线图、git 基线 | 本文档；基线 34 个测试通过 |
| M1a 执行可信 | 2026-10-24 完成 | P0-1 至 P0-5 | 5 个 P0 全部关闭；43 个测试通过 |
| M1b 原生工具 | 2026-10-24 核心与收尾完成 | 模式化工具注册表、函数调用循环、超时与降级 | 模型可原生调用搜索/写入工具；不支持工具调用时自动降级；57 个测试通过 |
| M2 记忆可信 | 2026-10-26 完成 | 证据链、候选晋升、召回日志、遗忘墓碑；FTS5/BM25 召回与降级自愈；导入、派生遗忘、证据台、protected 与角色扮演防污染 | 第二批验收全部通过；98 个测试通过（6 个环境跳过） |
| M2.5 人设分层 | 2026-10-26 完成 | 稳定人设/背景文件、persona pack、角色事实与用户事实隔离；导入校验、用户设定与世界书 | 124 个测试通过；稳定前缀哈希一致；角色资料不进入用户事实召回；删除 persona 不影响用户记忆 |
| M3 上下文编排 | 2026-10-26 完成 | 分层提示、稳定指纹、字符预算、抽取式 checkpoint；大工具结果落盘与 `read_tool_result` 分段回读 | 145 个测试通过；稳定哈希跨模式一致；预算内不超限；压缩后轮次完整；大输出可回读 |
| M3.5 收尾与评测 | 2026-10-27 完成 | 修复 M3R-1/2/3/8；召回评测集（26 条中文查询 → 期望记忆）；真实模型手动冒烟 | 151 个测试通过；压缩轮次只记 1 条召回；单字查询可召回；hit@5 基线 1.0；真实模型 function calling 与大结果落盘回读跑通 |
| M4.0 主动底座 | 2026-10-27 完成 | 迁移 v5 `reminders` 与提醒闭环、未完成话题派生、ctypes 活动状态机、纯函数门控、空来源退出 | 173 个测试通过；无来源不发言；六条门控规则可回归；活动状态可观测；真实库已迁移到 v5 |
| M4 主体 主动陪伴 | 2026-10-27 完成 | 迁移 v6 主动状态与事件、状态持久化、半衰期反重复、记忆来源 opt-in、错过未读、触发原因落库、Qt/Tk 面板与 60 秒轮询 | 202 个测试通过；送达/错过/暂停/跨天/未回复清零可回归；重复来源回退；真实库已迁移到 v6 |
| M5 工作任务卡 | 2026-10-27 完成 | 迁移 v7 任务/回执表、意图路由与注入守卫、任务卡与计划状态机、审批 TTL、转发回执终态、启动恢复、Qt/Tk 任务面板 | 240 个测试通过；未确认无副作用；超时不批准；崩溃可恢复；`unknown` 不重发；真实库已迁移到 v7 |
| M6 桌面形象/流式 | 2026-10-27 完成 | 流式事件与停止、控制标记过滤、透明置顶浮窗与点击穿透、位置记忆、托盘、回执状态气泡 | 271 个测试通过；停止后不再追加、迟到分片丢弃；浮窗位置跨重启恢复；托盘不可用可降级；状态与回执一致 |
| M7 技能包 | 2026-10-27 完成 | 本地 `SKILL.md`、元工具、模式/风险过滤、本轮工具范围、审计 v8 | 291 个测试通过；新增技能无需改代码；未声明工具被拒绝；非法/越权技能加载时拒绝；调用可审计 |
| M8 管理与自检台 | 2026-10-27 完成 | 备份/恢复/导出/彻底删除/完整性报告与单实例锁、迁移前快照、日志脱敏、Qt 7 页管理台、doctor 自检扩展 | 312 个测试通过；7 页可追溯到原始记录；导出/删除可用；迁移前快照可恢复；日志与审计无密钥 |
| M8.5 桌面界面改版 | 2026-10-28 完成 | 三栏布局、主题/QSS、气泡与回执卡、右栏详情、窗口找回、设置中枢、左栏会话树 | 350 个测试通过；Qt 离屏几何/主题/会话切换覆盖；主窗口可从任务栏与托盘找回 |
| M9 应用层契约 | 2026-10-28 完成 | ApplicationService、命令/run/event/审批/工作区快照契约、Qt adapter、停止到 done 的持久化确认 | 359 个测试通过；Qt runtime/service 两种构造路径可用；生成中变更被拒绝；无 done 或未落库不得完成 |
| M10.1 运行与消息 | 2026-10-28 完成 | 应用服务 UI 投影、运行状态条、安全 Markdown/消息操作、滚动跟随修复 | 383 个测试通过；运行阶段可回归；用户上滚不被抢滚动；新 UI 路径不绕应用服务 |
| M10.2 审批与抽屉 | 2026-10-29 完成 | 只读审批预览、内联事务卡、可展开工具回执、标签式上下文抽屉、可调宽度与响应式布局 | 404 个测试通过；确认前无副作用；过期/已处理/跨会话审批被拒绝；工具与来源可展开；780px 无三栏挤压 |
| M10.3 会话与设置 | 2026-10-29 完成 | 迁移 v9 会话标题/置顶/未读/草稿、日期分组与快捷键、2–5 行自适应 Composer、单一发送/停止主按钮、模式化空状态、双栏可搜索设置、字体缩放/减少动态、视觉 token | 431 个测试通过；会话操作有确认/失败反馈；生成中拒绝跨会话写入；草稿按会话保存；200% 字体与离屏截图覆盖主尺寸 |
| M11 Pixso Web UI | 2026-10-29 完成 | Pixso 变量/样式与 6 个画板；本地 HTTP + SSE 适配器；Pixso 派生前端；QtWebEngine 壳层与浏览器回退；默认 `--ui web`；M11.1 单实例唤回（端点文件 + `/api/activate` + Windows 按 PID 兜底） | 443 个测试通过；四页与 `/api/*` 可用；令牌与路径逃逸被拒；SSE 回合落库；Qt 壳层与浏览器目视一致；关闭隐藏后再次启动能唤回窗口；Qt/Tk 仍可显式启动 |
| M12 Web UI 精细化 | 进行中（2026-10-29 登记，M12.1、M12.2 已完成，M12.3 进行中：C1、C2、C4 已交付） | 参考项目 UI 对比整改五个切片：缩放正确性与视觉 token（已完成）→ 排版层级（已完成）→ 消息与运行表达（C1/C2/C4 已完成，剩 C3/C5/C8）→ 桌面浮窗能力 → 视觉纵深与密度（U1–U16 / C1–C10） | M12.1：200% 字体与 780×560 无裁切/溢出且窗口控制可用（真实浏览器 20 组合零问题 + 常驻回归）；M12.2：两端共用 7 档字阶（body 14）与三档行高，阅读列按字号计量、实测每行 18–37 字；M12.3 已达成"一次带表格与代码的长回复两端一致"（C1 共享语法 + 21 样本逐字节断言）与"上下文与运行阶段不再依赖前端字符串解析"（C2 环形仪表、C4 投影步骤时间线，且修复了阻断 Web 事件通道的 SSE 分帧缺陷）；其余出口（20 条以上历史流式无整列表重绘）与浮窗菜单、气泡队列待 C8/M12.4 |

| M13 角色素材接入 | 计划中（2026-10-30 登记，同日按"完全替换"语义修订；槽位已预留） | **M12 全部收口后开工**：M13.1 素材槽位与静态头像（`data/assets/<persona_id>/`、token 门控 `/api/asset`、无素材即留空、doctor `Character assets`）→ M13.2 可替换窗口背景（主窗/设置/管理台 + `--bg-scrim`，浮窗保持透明）→ M13.3 Live2D 桌宠运行时 + 旧桌宠下线（删除 Qt `AvatarOverlay`、QPainter 程序化形象、两份 `avatarSvg`、`build_avatar_icon()`、`avatar_palette()`） | 素材不进版本库也不进安装包；`/api/asset` 只能读 `data/assets/` 内白名单文件；**Live2D 不可用时桌宠功能失效**（入口置灰 + 原因可读，不显示任何替代形象）而消息仍可达；带背景图时三档文字仍可读；`--ui qt`/`tk` 明确不再提供浮窗；因删除导致的测试基线变化逐条写明 |

顺序约束：

- M1a 已完成，M1b 核心已完成；M1b 收尾项必须先于 M5：任务卡依赖统一工具循环与回执状态。
- M2 必须先于 M4：主动发言的来源必须是可追溯的活跃记忆。
- **M3 复查结论（2026-10-26）：M3.5 必须先于 M4**——压缩轮次重复召回会污染 M4 的来源判断（“活跃记忆事件”本身来自召回日志），且 M4 的提醒/未完成话题来源在 M4.0 补齐前没有数据可读。
- M2.5 已完成：稳定前缀、persona pack 与导入/设定/世界书边界已确定；M3 可直接复用 `persona.py` 的稳定前缀与哈希。
- M6 已完成：状态气泡建立在 M1 的提案/回执与 M5 的任务回执之上，不再依赖模型自述。
- M7 已完成：技能只复用 M1 的工具注册表与策略，不新增执行通道；技能声明工具必须通过同一模式/风险判定，元工具本身为 `safe`。
- M9 已完成：Qt 主链路先接应用服务；M11 的 Web 适配器只复用该层，不得复制 Qt 的线程、停止、审批或事件序号逻辑。
- M10 必须在 M9 之后、阶段 5 之前完成：先稳定桌面主界面的信息层级与可访问性，再在其上增加语音、Live2D 或多端适配；M10.1 → M10.2 → M10.3 按依赖顺序执行，不并行铺开全部视觉改造。
- M11 依赖 M10 的全部界面契约：Pixso 画板只决定视觉，状态、审批与运行阶段仍由应用服务投影提供；Web 默认后，Qt/Tk 只作为兼容入口，任何新面板先补应用服务命令再接 UI。
- 阶段 5（语音/Live2D）排在 M1–M4 之后。
- M12 必须先于阶段 5：语音、Live2D 与多角色都会在现有三栏与浮窗上追加控件，若先加能力，U9–U12 的缩放裁切与 C6 的浮窗交互缺失会被放大成返工；M12.1 修正缩放正确性、M12.2 修正排版、M12.3 修正消息表达、M12.4 补齐浮窗、M12.5 收视觉纵深，按序执行，不并行铺开（2026-10-29）。
- **2026-10-30 修订**：M13 不插进 M12——用户确认 **M12 先全部做完**，完整顺序为 **M12.3 → M12.4 → M12.5 → M13.1 → M13.2 → M13.3 → 语音/打包**。登记时那版"M13.1/M13.2 插在 M12.4 与 M12.5 之间"的理由是"M12.5 的遮罩取值要先知道背景是不是用户图片"；这个前提改用 token 缝解决：M12.5 把"内容表面"与"背景遮罩"拆成两个独立值并预留 `--bg-scrim`，M13.2 只赋值不改值，交错带来的并行心智负担就没了。另一条硬约束不变：桌宠载体的替换必须一次做完（M13.3 同时接入 Live2D 与删除旧形象），中间不留"没有形象"的版本。

## 7. 验收指标

| 指标 | 含义 | 目标 |
|---|---|---|
| 越权执行次数 | 未授权写入、越界路径、未确认高风险动作 | 0 |
| 回执完整率 | 执行动作有结构化回执的比例 | 100% |
| `unknown` 对账率 | 不确定状态最终有结论的比例 | 100% |
| 重复执行次数 | 同一幂等键产生多次副作用 | 0 |
| 记忆来源覆盖率 | 派生记忆可追溯到来源的比例 | 100% |
| 召回可解释率 | 召回结果带来源与原因的比例 | 100% |
| 遗忘传播完整率 | 原文、记忆、索引、派生状态同步处理 | 100% |
| 角色扮演污染次数 | 角色扮演/引用台词被存为用户事实的次数 | 0 |
| 人设与用户事实隔离率 | 角色资料进入用户事实召回的比例（M2.5 已完成） | 0 |
| 重启恢复率 | 重启后可查看/撤销的任务比例 | 可恢复任务 100% |
| 主动打扰拒绝率 | 用户关闭或忽略主动发言的比例 | 持续观测并调阈值 |
| 测试基线 | 每次合并前的单元测试 | 全绿，且新增模块有回归用例 |
| 本地 Web 暴露面 | 无令牌/错误令牌/路径逃逸访问 `/api/*` 与 `/assets/*` 的结果 | 100% 拒绝；仅回环监听 |

不把“聊天时长”作为核心成功指标。健康指标应包含用户控制感、任务有效性和打扰可接受度。

## 8. 决策记录

| 编号 | 决策 | 原因 |
|---|---|---|
| D1 | 先 M1/M2，后 M5/M6 | 执行与记忆是地基；先做界面会掩盖未完成的内核 |
| D2 | 只采用一套记忆衰减公式 | 同时实现两套会导致调参失控、行为不可解释 |
| D3 | SQLite 是权威数据源，FTS5 先行，向量可插拔 | 先保证来源与召回质量，避免过早引入大模型 |
| D4 | 保持单进程 PySide6，不引入 ZMQ/多服务 | N.E.K.O 的架构不适合当前单人桌面产品 |
| D5 | 参考项目按许可证分级复用 | 代码可复用与素材/IP 授权是两件事 |
| D6 | OpenClaw 桥后续复用同一执行内核 | 避免桌面端与微信端状态、权限、回执分裂 |
| D7 | M1 拆为 M1a / M1b，先做 M1a | 先以较小改动关闭 5 个 P0，再引入工具循环；便于验证与回滚（2026-10-24） |
| D8 | 显式“搜索…并保存为笔记”保留正则快路径 | 避免多一轮模型调用，保证无模型/弱模型时仍可用；开放式任务走原生工具循环（2026-10-24） |
| D9 | 写入工具只生成提案，不在工具循环内执行 | 工具循环不能绕过确认卡与 M1a 的执行复核、幂等和回执（2026-10-24） |
| D10 | 以现有 `memories` 作为事实表扩展，而不是新建 `memory_facts` 后双写 | 保持 UI、运行时与现有测试兼容，避免双写漂移；证据、候选、事件与墓碑使用独立表（2026-10-24） |
| D11 | 记忆证据必须是用户消息原文的精确子串 | 助手回复不能单独证明用户事实；这是可解释召回与遗忘的最低约束（2026-10-24） |
| D12 | BM25 只负责候选检索，最终排序仍用单一读时衰减公式 | 与 D2 一致：避免双评分体系调参失控；BM25 解决“找得到”，衰减公式解决“信多少”（2026-10-26） |
| D13 | FTS 索引作为独立虚表旁路维护，任何异常降级关键词、启动自愈 | 召回质量提升不得引入新的阻塞点；索引永远可以从 `memories` 全量重建，损坏无数据损失（2026-10-26） |
| D14 | 派生记忆用关系表 + 递归遗忘，而不是复制来源状态 | 反思、摘要和经历可能多层派生；关系表可逐层追溯且不污染事实本身。遗忘时每层写墓碑，确保导入不会绕过（2026-10-26） |
| D15 | 导入按角色+内容哈希去重，并在入库前检查内容墓碑 | 跨副本导出会重复消息，但内容哈希不受文件路径和时间戳影响；墓碑先于去重判断可保证“遗忘不复活”（2026-10-26） |
| D16 | 人设、背景、性格走稳定文件/系统前缀，不批量写入 `memories` | 角色事实没有用户证据，批量入库会污染用户画像、挤占召回预算，并带来第三方 IP 分发风险；只有用户自定义关系/边界进入 protected persona 记忆（2026-10-26） |
| D17 | 角色扮演、引用台词与假设句不进入事实抽取；导入按标题与开始/结束标记维护角色扮演状态 | 旧聊天里“我叫…”“我喜欢…”常是角色台词；宁可漏存，不可误存。状态从已导入单元重算，避免续跑丢失（2026-10-26） |
| D18 | 默认人设采用本地 persona pack（`persona.md` + `facts.md` + `worldbook.md` + `persona.json`），首版角色为蕾米埃尔；`persona.json` 记录来源、许可与 canon cutoff | 文件化便于版本化与稳定哈希；与证据记忆解耦；元数据让用户知道内容边界与 IP 归属。仓库只保留原创摘要，不含官方原文与素材（2026-10-26） |
| D19 | persona 导入只复制四类人设文本文件；worldbook 按查询触发，且不参与稳定前缀哈希 | 防止图片、语音或模型随包进入仓库；世界书是动态背景层，整包注入会膨胀上下文并破坏稳定前缀复用（2026-10-26） |
| D20 | 上下文预算以字符计；压缩摘要使用确定性抽取式，不额外调用 LLM 生成 | 无模型/离线/失败时仍可压缩，且摘要可追溯到原消息、不会引入新事实；字符预算无需绑定某个 tokenizer，跨模型稳定（2026-10-26） |
| D21 | 工具结果超过 16KB（UTF-8 字节）落盘，模型只看到预览与不透明句柄；回读仅限当前会话、按 offset/limit 分段 | 避免单次搜索结果撑爆上下文，也避免模型声称读过未返回的内容；会话隔离与句柄校验防止跨会话读取落盘文件；截断阈值以字节计，避免中文按字符低估体积（2026-10-26） |
| D22 | 召回日志每轮只写一次；`used_in_reply` 以“记忆原文或 ≥60% 的 2-gram 出现在回复中”判定；单字中文查询绕过 FTS 走关键词兜底 | 不额外调用模型即可度量召回是否被回复采纳；单字没有 2/3-gram 可命中，关键词兜底避免 FTS 空召回，且复用既有单一衰减评分（2026-10-26） |
| D23 | 活动状态机用 `ctypes` 读取空闲时间与前台窗口，不引入 `psutil`；游戏识别采用前台进程名启发式，全屏统一视为 `focus`；探测失败或非 Windows 一律 `unknown` | 少一个运行时依赖，且 `GetLastInputInfo` 不触发额外隐私采集；启发式只用于“是否打扰”，误判方向应是更安静；`unknown` 保守关门避免在锁屏或异常环境主动说话（2026-10-27） |
| D24 | 主动门控实现为纯函数 `evaluate_gate(GateState)`；提醒与未完成话题先形成带 `trigger_reason` 的来源，空来源直接返回 `no_source` | M4.0 不依赖 UI 即可回归六条规则；来源可追溯是主动发言的前提，空来源退出从结构上禁止“随机话题”（2026-10-27） |
| D25 | 反重复按来源类型半衰期衰减：未完成话题 24h、记忆 72h，`reminder` 半衰期为 0 表示豁免；内容与 URL 归一化哈希任一命中即视为重复 | 话题与记忆“说过一次”后应逐渐允许新的表达，提醒则是用户显式请求、必须按约重复；半衰期让去重强度可解释且可调（2026-10-27） |
| D26 | 提醒超过到点 30 分钟仍未送达即记为 `missed`、关闭提醒并保持未读；错过事件不写入会话历史，也永不补发 | “到点提醒”过晚送达会变成打扰；保留未读事件让用户可回看，但不假装刚刚说过；宽限窗口避免轮询抖动导致误判（2026-10-27） |
| D27 | 活跃记忆来源默认关闭，开启后只取 48h 内 `used_in_reply=1` 的召回日志中已确认记忆；主动消息由本地模板生成，不调用模型 | 记忆来源若默认开启容易把“记得”变成“翻旧账”；本地模板保证每句话都能追溯到来源且不引入新事实（2026-10-27） |
| D28 | Qt 用 60 秒 `QTimer` 轮询 `proactive_tick()`，发送/生成期间跳过，并提供“现在检查”；首版不做系统推送或托盘提醒 | 轮询足以覆盖分钟级提醒，避免引入后台服务与系统通知权限；手动检查便于用户立即验证（2026-10-27） |
| D29 | 意图路由规则优先，模型分类只作兜底；分类器只接受不可信 JSON 文本，12 秒硬上限，超时/非法输出降级为 `clarify` | 规则可解释、离线可用；超时与坏输出绝不能把消息误判为可执行工作，追问是安全方向（2026-10-27） |
| D30 | 只有外部副作用请求自动建任务卡；本地搜索/笔记/时间查询保持既有流程 | 任务卡是给“要确认的外部动作”用的；把本地只读流程也塞进计划模式会增加无谓确认并破坏现有体验（2026-10-27） |
| D31 | 计划状态与审批状态在等待用户前落库；批准时先把任务置为 `EXECUTING`、回执置为 `forwarding` 再调用执行器 | 崩溃后可从持久状态判断“审批过没有、转发过没有”；先写状态再产生副作用，才有 `unknown` 可对账（2026-10-27） |
| D32 | 审批 TTL 30 分钟，超时只回讨论并标 `expired`，绝不自动批准；`unknown` 为终态，禁止改写与自动重发 | 时间流逝不能代替用户同意；结果不确定时重发可能造成重复外部副作用，宁可让用户重新确认（2026-10-27） |
| D33 | 流式事件统一为 `delta / reset / tool / done / error`，非模型分支也走同一出口；每个事件携带 generation id | 界面只有一套消费路径，搜索/任务卡/离线不会退化成“等完整结果”；generation 让“停止后迟到分片”在 UI 层有确定归属（2026-10-27） |
| D34 | 停止用 `threading.Event` 在分片与工具边界检查；已显示部分入库并追加“（已停止生成）”，迟到分片既不入库也不显示 | 不猜测模型会输出什么；保留用户已经看到的内容，同时保证停止后不再增长。HTTP 请求本身受 60 秒客户端超时约束，取消在下一次分片到达时生效（2026-10-27） |
| D35 | 控制标记用增量 `MarkerFilter` 剥离，未完成 `[[` 跨分片保留，未知/未闭合标记原样输出 | 标记可能被任意切开；缓冲跨分片前缀才能既不漏标记又不吞正文。未知标记保留便于未来扩展与排查（2026-10-27） |
| D36 | 浮窗位置写 `ui_state.json`（临时文件 + `os.replace`），启动按最近屏幕夹取；点击穿透用命中检测动态切换 | UI 状态独立于业务数据库，损坏时安全回退；最近屏幕策略在多显示器变化后仍能把窗口拉回可见区域（2026-10-27） |
| D37 | 技能清单只支持单层键值与列表，拒绝未知/重复字段；`name` 必须等于目录名；参考文件限制为 `references/` 下单文件并做 resolve 包含检查 | 不引入 YAML 依赖，解析行为可预测；名称与目录一致避免同一技能多个身份；包含检查同时约束 `..` 与符号链接跳出（2026-10-27） |
| D38 | 技能风险取 `effectKind` 与声明工具风险的最大值；省略 `modes` 时按策略推导，显式模式若被拒绝则该技能判为非法 | 作者不能通过写 `read` 夹带高风险工具；模式声明必须是真实可用的，而不是只在提示里出现（2026-10-27） |
| D39 | `invoke_skill` 成功后本轮工具范围收窄为声明工具 + 元工具；越权调用在注册表执行前拦截；调用/读取/拒绝都写 `skill_audit` | 声明工具从“说明”变成可执行约束；在副作用发生前拦截比事后报错安全；审计失败时拒绝调用避免静默执行（2026-10-27） |
| D40 | 失败记录页从既有提案/回执/任务/审计/提醒/主动事件派生，不新增 `failure_log` 表；只读展示并保留原始 ID | 失败原因已经分散落在各自业务表，重复落一份日志会带来一致性与清理负担；派生页能直接追溯原始记录，也避免“日志写失败但业务已失败”的新故障点（2026-10-27） |
| D41 | 迁移回滚采用“迁移前自动快照 + 从备份显式恢复”，不实现 schema 降级；仅旧版本且已有数据时创建快照 | 降级 SQL 需要为每个版本维护反向脚本，风险高于收益；SQLite 在线备份+完整性校验已能恢复到迁移前状态，且空库初始化不产生无意义备份（2026-10-27） |
| D42 | 管理台只做 Qt，Tk 保留原有对话框；彻底删除可选清理备份与导出，单实例锁默认开启并可用 `--allow-multiple` 跳过 | 管理台以密集表格与多页签为主，Qt 是主界面；Tk 仅作降级不重复实现。删除必须覆盖“数据的一切副本”又不默认误删用户备份，因此把备份/导出清理做成显式选择。锁在入口层，测试与工具不受影响（2026-10-27） |
| D43 | 界面改版走“方案 A”：QSS + 原生控件复刻参考项目布局，不引入 Web/Electron 运行时，不复刻角色素材 | 参考项目的观感主要来自配色与三栏结构，QSS 即可覆盖；引入 Web 运行时要背打包体积、进程模型与渲染层状态同步，且现有 16 个 Qt 用例与托盘/对话框逻辑可全部保留。角色形象与 Live2D 模型属于米哈游衍生作品（非商业授权），只复用 MIT 代码思路与色值，不复制素材（2026-10-27） |
| D44 | 主窗口从 `Qt.Tool` 改为普通窗口（带 `Qt.WindowMinimizeButtonHint` 与头像窗口图标），顶部按钮由“收起→隐藏到托盘”改为“最小化到任务栏”；托盘“隐藏主窗口”保留但只在托盘可用时有意义 | 无边框 `Tool` 窗口不进任务栏，`hide()` 后唯一的入口是托盘图标，而托盘在部分环境不可用或被 Windows 折叠到溢出区，用户实测后“找不到窗口”。最小化是用户熟悉的等价动作，且系统本身就提供任务栏还原；还原时用最小化前几何回填，规避无边框窗口在 Windows 上还原尺寸不可靠的问题（2026-10-27） |
| D45 | 提醒做成 `RiskLevel.SAFE` 的 `set_reminder` 工具（四种模式可用，不产生提案），时间解析抽出 `resolve_when()` 供本地快路径与工具共用；主窗口与侧边面板不再 `WindowStaysOnTopHint` | 提醒只写本地 `reminders` 表、无外部副作用、到点仍需门控才发言，与 `current_time` 同级；若定为 ASK 就会撞上“ASK 必须带提案”的注册不变量，而提案机制是为文件写入设计的。解析复用避免两套时间规则漂移。桌宠的常驻可见已经由置顶浮窗承担，主窗口再置顶会挡住用户真正在用的程序（2026-10-27） |
| D46 | 六个面板入口与外观从常驻左栏移入左下角“设置”中枢（`ui/settings.py`），左栏只保留形象/状态、工作目录与会话摘要；选中设置项时先关设置再开侧边面板 | 左栏同时承担主导航、工作区与会话信息，入口越多越压缩“当前和谁在聊什么”的空间；参考图片的信息层级也是身份与工作区优先。设置项与面板都定位在主窗口右侧，不先关闭会完全重叠；把选择做成“关闭后导航”比并排堆叠更符合桌面向导习惯（2026-10-28） |
| D47 | 左栏改为会话树（新的任务/搜索/工作区行 + `…`/`+`/缩进会话列表/底部设置），形象与状态移到标题栏；会话标题不建列，取首条用户消息派生，切换由 `runtime.switch_session()` 统一迁移 `proactive_service` 与 `work` 的会话作用域；空会话只在当前时显示，生成中禁止切换/新建 | 参考图把左栏定义为“工作区 + 会话导航”，摘要卡与身份信息占掉的位置正是会话列表需要的空间；身份放在标题栏仍保持可见且不打断顶部模式切换。标题若新增列就要处理重命名/迁移/空标题回填，而首条用户消息已经是会话最稳定的语义摘要。两个服务在构造时缓存 `session_id`，若只改 `runtime.session_id` 会让提醒与任务卡仍落在旧会话；集中在 `switch_session()` 迁移可以避免各处遗漏。空会话行与生成中切换分别制造“空列表噪音”和“消息落到错误会话”，二者都在入口处拦截（2026-10-28） |
| D48 | M9 采用进程内 `CompanionApplicationService`：Qt 是第一个 adapter，FastAPI/SSE 作为后续 transport adapter；run 启动时冻结工作区，事件带稳定 sequence，`done` 必须通过最终 assistant 消息落库确认 | 当前产品仍是单进程桌面应用，引入独立后端会增加部署、认证、状态同步与离线成本。先把命令/运行/事件/审批边界从 Qt 中抽离，后续 Web/手机只替换 transport，不重写业务层。工作区冻结让本轮审计上下文不随 UI 切换漂移，但不会替代提案执行时的真实快照复核。`done` 若只信 runtime 事件，UI 可能展示未落库的回复；持久化确认是可见完成的最小可靠条件（2026-10-28） |
| D49 | M10 按 `运行与消息 → 审批与抽屉 → 会话与设置` 三批实施；主聊天新增只读 UI 投影，安全 Markdown 与工具内容按不可信输入处理；语音、附件、Live2D 等控件使用能力门控，后端未实现时不显示 | 运行状态、消息与审批决定用户是否信任当前 Agent，应先于视觉装饰与语音形象；分三批可以让每批都有独立回归和回滚边界。由应用服务输出投影能避免 Qt 继续扩散 runtime 直读，也能让未来 transport 复用同一视图模型。提前放置不可用控件会把路线图伪装成能力，并增加无意义的状态与错误路径（2026-10-28） |
| D50 | M10.1 的 UI 投影由应用服务返回冻结 dataclass，运行阶段由 `stage_for_event()` 在应用层单点推导；Markdown 采用“先转义再渲染有限子集”，链接仅 http/https 且点击需确认；滚动只在用户停留底部时跟随，上滚后显示跳转提示 | Qt 只做展示，stage/sequence 的权威在应用层，避免未来 transport 复制状态机；先转义能从根上排除模型输出中的原始 HTML/脚本，有限子集也足以覆盖代码、列表与链接。强制回底会打断用户阅读历史，而完全不提示会让新内容不可发现；粘底 + 显式提示同时保留两种意图（2026-10-28） |
| D51 | M10.2 的变更预览放在 `NoteActionService.preview()` 只读路径，经 `runtime.preview_proposal()` 与 `ApprovalView.preview` 暴露；Diff 最多 80 行、单行 180 字符；确认卡直接插入聊天流，右栏改为标签抽屉并用 `QSplitter` 持久化宽度；响应式阈值 940/860px，窄窗左栏改为覆盖式抽屉 | 预览若在 Qt 内自行读文件，会绕过工作区守卫并产生第二套路径规则；放在文件服务里可复用 `WorkspaceGuard`/指纹校验，且明确“只读、不建目录、不消耗提案”。容量上限避免敏感长文或超大 Diff 撑爆卡片。审批安全边界仍在服务层，卡只负责展示与禁用过期确认。标签抽屉让右栏按任务归类，避免所有节堆在一起；宽度写入 `ui_state.json` 保持用户习惯。先收右栏再收左栏符合“聊天优先”，覆盖式抽屉在 780px 下比压缩三栏更可读（2026-10-29） |
| D52 | M10.3 的会话持久字段落在独立迁移 v9（`sessions.title / pinned_at / unread` + `session_drafts` 表）；草稿在生成中允许保存，其余会话变更仍由 idle 守卫拒绝；输入区/设置/抽屉共用 `TYPE_TOKENS / SPACE_TOKENS / FONT_SCALES`，字体缩放统一放大 QSS 字号，减少动态效果只关闭流式自动粘底 | 标题、置顶、未读与草稿是用户可见的产品状态，放 `ui_state.json` 会在换数据目录或重装时丢失，也无法在管理台按会话删除；单独建草稿表可以高频写入而不触碰 `sessions.updated_at`，避免“打字把会话顶到列表最前”。生成中禁止切换是防止消息落到错误会话，但草稿属于本地编辑，允许保存才能避免停止/切窗时丢字。把字号与间距收敛到 token 才能让 200% 缩放一次覆盖全部控件；只缩放 QSS 字号而不改布局系数，能让按钮与行高按字体度量自然增长。减少动态效果针对的是流式强制滚动，而不是移除所有过渡，既满足偏好也保留状态反馈（2026-10-29） |
| D53 | M11 以 Pixso 为唯一设计源，前端改为本地 Web（HTML/CSS/JS），由 `ThreadingHTTPServer` + SSE 适配 `CompanionApplicationService`；默认 `--ui web`，Qt/Tk 保留兼容入口；只监听回环、随机端口、令牌校验，Windows 禁用 WebEngine 沙箱 | 用户明确要求舍弃原 Qt 界面、由 Pixso 接管视觉。静态画板无法完成发送、审批、会话与错误反馈，必须绑定真实应用服务；直接上 Electron/远程后端会放大打包、认证与状态同步成本。回环 + 随机端口 + 令牌把攻击面限制在本机浏览器/壳层，路径守卫阻止读出 `data/` 与密钥；QtWebEngine 禁用沙箱只针对本项目自带的本地静态资源，不加载远端内容。保留 Qt/Tk 是为了在 WebEngine 缺失或渲染异常时仍能打开数据与执行确认，删除入口需单独评估兜底（2026-10-29） |
| D54 | 关闭主窗口保留“隐藏到托盘 + 进程继续运行”，第二次启动改为唤回运行中的实例：`data/.companion.endpoint` 记录回环端口与令牌，`POST /api/activate` 让壳层在 GUI 线程显示主窗口（浏览器实例返回 URL 由新进程打开标签页），端点不可达时 Windows 上按锁文件 PID `ShowWindow` 兜底，全部失败才提示并返回 3；`pythonw` 无 stderr 时用系统对话框；托盘不可用时关闭即退出 | 用户反馈“第一次关闭后再运行打不开”：实际是进程仍在持锁运行而非崩溃。改成“关闭即退出”会丢掉托盘常驻与到点提醒；重复启动静默报错对 `pythonw` 用户完全不可见。唤回式启动既保住单实例账本，又保证用户总能回到窗口（2026-10-29） |
| D55 | M12.1 用"token + 字体度量"修复缩放：Web 端移植 `SPACE_TOKENS` 并按 `--scale` 换算，固定 `height` 一律改 `min-height: calc(字体度量)`；上下文用量等新增显示必须来自应用服务投影字段，禁止前端正则解析展示文案 | Qt 靠控件字体度量自然长高，Web 把高度写死而 `--scale` 只作用在 font-size 上，设置页已经开放的 150/200% 一选就切字（U9）；tab 溢出与 780px 隐藏窗口按钮（U10/U11）同属"可达尺寸下功能不可用"。前端正则从投影文案抽数字（C2）在任何文案改动时静默失效，把展示层变成数据源，违背 M10.1 建立的"权威在应用层投影"边界（2026-10-29） |
| D56 | M12 只做 CSS/token 与投影层整改，不引入 Tailwind/React/Electron/构建工具链；~~浮窗动画先用纯 CSS~~（2026-10-30 修订：取消），Live2D/VRM 与素材接入改由 M13 承接 | 前端现在的价值是零构建、随包静态资源与 R20 的回环边界，换成框架会把一次视觉整改变成构建与状态同步工程（延续 D43/D53）。参考项目的观感长板（玻璃模糊、渐变、不对称圆角、氛围层）用 token + `backdrop-filter` 即可覆盖；而 D49 的能力门控仍然有效——语音/附件/模型未接通前不放控件。**修订记录（2026-10-30）**：原计划"浮窗动画先用纯 CSS 呼吸/眨眼"取消——用户已自备头像与 Live2D 模型，为即将被删除的程序化 SVG 形象补动画是纯浪费；M12.4 只保留"把两份 `avatarSvg` 归一成一个挂载点"，它服务的是 M13.3 换掉整个载体（该模块的程序化分支在 M13.3 删除），不是给旧形象做动画（见 D57/D58）。Live2D 渲染仍走浏览器生态 + 本地静态资源，pixi-live2d-display 是运行时库而非构建链，不违反本决定 |
| D57 | 角色美术素材按人设分目录放在 `data/assets/<persona_id>/`（`avatar.*` / `background.*` / `live2d/model3.json`），程序只读不写、整体 gitignore、不进安装包；窗口背景替换只作用于主窗/设置/管理台，浮窗保持透明，可读性由主题派生的自动遮罩承担而非用户调参 | 素材是第三方授权物，与 `data/personas/`（文本、进版本库、原创摘要）性质相反，必须走另一条目录与忽略规则，否则一次 `git add` 就把未授权模型带进仓库（R10、技术方案阶段 5 验收"安装包不含未授权素材"）；按人设分目录让"换角色"是一次切换而不是三处各选一次，也不引入清单文件这种第二数据源。只读不写使删除语义与记忆导出/删除保持一致。浮窗的价值就是"透明贴在桌面上"，给它铺背景会同时破坏观感与点击穿透判定；遮罩交给主题派生而不是暴露强度/模糊/平铺参数，是为了把设置项控制在一个开关——参数越多，两端（Web CSS 与 Qt QSS）需要保持一致的取值就越多，正是 R22 刚收敛掉的那类漂移（2026-10-30）。**修订记录（同日）**：登记时的"`ui_state.json` 新增 `avatar_enabled`"取消——素材目录本身就是控制面，再加开关会造出"开关开着但没素材"的第二状态；`--bg-scrim` 的引入点从本切片挪到 M12.5（M12.5 先做，只留缝不赋值） |
| D58 | 桌宠的载体只有一个：Live2D 跑在 Web 透明置顶浮窗里；**不可用时桌宠功能失效**，不退回程序化 SVG、不放中性占位；旧形象（Qt `AvatarOverlay` + QPainter 绘制 + 两份 `avatarSvg` + `build_avatar_icon()` + `avatar_palette()`）在 M13.3 与接入同一次改动里删除 | 用户 2026-10-30 明确要求"原桌宠完全替换成 Live2D"，并选定"live2d 不可用时桌宠功能就失效"作为兜底策略。保留回退链会有三个后果：旧形象永远删不掉（回退档在真实使用中会因偶发 WebGL 失败而长期接管，替换变成"多了一套代码"）；两套渲染规则必须同时保持正确，正是 R22 刚收敛掉的漂移类型；测试与文档要长期描述一个用户没选的形象。"功能失效"是可诊断的——原因写进设置页与 `doctor.py`，而"悄悄换成别的脸"不是。代价如实记录：`--ui qt`/`tk` 兼容入口从此没有桌宠（要显式提示而非静默），WebGL/透明合成/点击穿透的可行性成为新的单点风险（R25），删除会让测试基线下降。**主动与回执消息不随形象失效**——浮窗不显示时改走主窗口未读/主动面板与托盘提示，因为丢消息是数据问题，不是外观问题；若后续要求连气泡一起停用，需另行确认 |
| D59 | 派生显示值一律由 `app/contracts.py` 的只读视图 + `app/projection.py` 的单点推导提供，契约里只放整数与布尔（`ContextView.context_used / context_limit / compressed`），比值、百分比、颜色档是使用方的展示计算；`/api/bootstrap` 的 `context` 直接来自 `ui_projection()`，Qt 胶囊也改取同一投影而不再直读 `runtime.last_context` | 关闭 C2 时暴露的两条边界（2026-10-30）：① `web/server.py` 的 `plain()` 按字段名序列化 dataclass，属性不下发——把 `percent` 做成服务端属性会造成"接口里没有但界面要用"，前端迟早回头拼字符串；② 详情面板的"用量：X / Y 字符"与胶囊必须同源，否则文案与仪表各说一套，正是 M10.1 之后仍残留的漂移点。代价是 `ContextView` 只有一个字段集，将来加运行阶段等视图要沿用同一写法（M12.3 后续的 `RunView.steps` 按此设计） |
| D60 | 传输层对 socket 的**每一处** `wfile` 写都必须经同一个分帧函数（`web/server.py::_write_chunk()`），SSE 首帧 `retry:` 与断线时的终止块也不例外；事件通道要有原始 socket 级帧格式回归，不能只测 `adapter.subscribe()` 的内存队列。运行步骤时间线的记录与折叠同样单点在服务层（`RunContext.steps` + `_record_step()` 按相邻 `(stage, detail, tool)` 折叠），两端只渲染 `RunView.steps`，都不自造终态文案 | 关闭 C4 时发现的真实缺陷：`_stream()` 声明了 `Transfer-Encoding: chunked` 却把首帧原样写出，Chromium 立即判 `net::ERR_INVALID_CHUNKED_ENCODING` 断开，`EventSource` 每次自动重连都死在同一处——**M11 起浏览器与 QtWebEngine 壳层里的 `/api/events` 从未真正工作过**（发送一轮后界面不更新、运行条不出现、跨窗口广播失效；浮窗因走 8 秒 `setInterval` 轮询而不受影响，正是它长期没被发现的原因），而全部 Web 用例都直接消费内存队列，结构上不可能发现它：界面"收不收得到事件"与"渲染对不对"是两个断言面。时间线若让两端各自从事件流推导阶段，就重演 R22 的两套文案；`done` 后 run 释放、`run_view()` 归 `None`，所以时间线只描述进行中的这一轮，无需步数上限（2026-10-30） |
| D61 | Markdown 保持"一份语法、两个实现、两套呈现"：`ui/markdown.py` 与 `web/assets/markdown.js` 输出**逐字节相等**的语义 HTML（`tests/fixtures/markdown_samples.json` 21 个样本由 node 逐条断言），表现层各归各端——Qt 由 `markdown.qt_style(font_scale)` 生成内联 `<style>` 块（类选择器 + 由字号档换算的 px 边距），Web 由 `app.css` 的 `.md-*` 规则承担；**纯装饰不得进入渲染器**（Web 复制按钮在渲染后由 `decorateCode()` 装饰 DOM，否则两端输出立刻分叉）。标题一律输出 `<div class="md-h md-hN"><b>` 而非 `h1..h3`；不做语法高亮 | 关闭 C1 时的三条发现（2026-10-30）：① 两端各写一套 grammar 必然漂移，正是 R22 描述的类型；把"语义"钉死成字符串后，测试只需断言一处，呈现差异留在 CSS/QSS 里也不会破坏一致性。② Qt 在 `QTextDocument` 里给 `h1..h3` 强制约 1.5× 相对字号并忽略 `font-size`，且 `em` 边距不随设置的字号档缩放，所以标题与间距只能用类选择器 + 按 `font_scale` 现算 px。③ QSS 会阻断字体继承：只要给 `QLabel` 设了任何字体属性，子文档就停在应用默认 9pt，且 `pixelSize()` 为 -1——`QLabel#bubbleBody` 必须显式给 `font-size: _type("body")`，气泡正文才会跟着 M12.2 的字阶缩放（这是 M12.2 留下、本轮才暴露的缺陷）。高亮不做的理由：shiki/Prism 需要构建链（违反 D56）或数 MB 运行时与第二份订阅逻辑，Qt 端还无法复用，而本项目输出以短片段为主；语言标签与复制按钮已覆盖实际使用场景。代价是 `markdown.js` 与 `markdown.py` 需同步手改，由样本断言兜住（2026-10-30） |

## 9. 风险

| 编号 | 风险 | 应对 |
|---|---|---|
| R1 | Windows 文件身份不稳定 | 已用 `realpath + st_dev/st_ino` 并执行前复核；部分文件系统 `st_ino` 可能为 0，M1b 前补 `GetFileInformationByHandle` 卷序列号/文件索引 |
| R2 | 中文 FTS5 分词效果差 | 已落地：`textseg.py` 中文 2/3-gram 预分词写入 FTS5，BM25 只做候选；关键词召回保留为降级路径。单字中文查询（如“茶”）现绕过 FTS 走关键词兜底，命中该字所在记忆；剩余边界：单字查询会退化为最多 200 条记忆的线性扫描，分词器升级后可再优化 |
| R3 | 模型不支持 function calling | M1b 已接入自动降级：首次工具调用失败后切换无工具对话；显式搜索仍走正则快路径 |
| R4 | 没有 git 历史，大改难回滚 | 已缓解：基线 `39151a2`，M1a/M1b/M2 第一批 `f6f4647`/`b6bdbe9`，FTS 切片 `f0ffa0a`，M2.5 `49f9fc6`，M3/M3.5 `23c7837`，M4/M5 `665d706`，M6 `1e3de1f`，M7/M8 `0967e9f`；M8.5、M9、M10.1、M10.2、M10.3 与 M11 尚未提交（等待用户确认）。未推送远端，仍无异地备份 |
| R5 | 文档漂移 | 每个模块完成时同步 README 与本文档状态 |
| R6 | 范围蔓延到语音/Live2D/社交 | 以 M1/M2 出口条件为闸门，未通过不启动 |
| R7 | 单元测试通过但真实 UI 未跑通 | 已增加 Qt 自动化端到端“搜索 → 确认 → 写入 → 撤销”；Tk 与真实模型仍需人工验证 |
| R8 | 两个实例同时使用同一 `data` 目录时，启动恢复可能把另一实例的在途执行误标为 `unknown` | 已缓解（2026-10-27）：启动入口默认获取跨进程文件锁，第二个实例提示占用后退出；`doctor.py` 显示锁状态；仍有 `--allow-multiple` 显式绕过，用于测试/维护 |
| R9 | 旧聊天角色扮演/引用台词被误存为用户事实 | 已缓解（2026-10-26）：D17 + 5 个回归测试；导入角色扮演会话只入消息、不入记忆。剩余边界：无标记、无引号的角色扮演仍无法可靠区分，可在记忆窗口手动纠正/忘记 |
| R10 | 直接引入第三方角色文本/素材带来 IP 与分发风险 | M2.5 已只内置原创摘要（`data/personas/remielle/`），不含官方原文、语音、图像、模型或 Live2D 素材；角色与世界观版权归 miHoYo，默认仅个人本地非商业使用。导入面板先展示来源、许可与大小，只复制文本文件；若要公开分发，应移除该角色包或替换为自有角色 |
| R12 | 稳定人设文件过长会挤占上下文预算 | 当前 persona + facts 约 4k 汉字量级；worldbook 不进入稳定前缀，只按查询注入最多 2 条、约 1200 字符；M3 预算不裁剪稳定层，只裁剪历史，并给召回记忆（1600 字符）与用户设定（2000 字符）设上限 |
| R11 | 角色扮演过滤器误伤真实引用 | 过滤只跳过记忆抽取、不删除消息；普通消息中的显式“请记住”仍生效；用户可在记忆窗口手动新增/纠正 |
| R13 | 落盘的工具结果可能包含网页原文或其他敏感内容 | 已缓解（2026-10-27）：文件只在 `data/tool_results/` 内、按会话句柄访问，单文件存储上限 4MB；提示词要求只引用实际回读片段；管理台可导出、按会话删除或全量清理；仍未实现按时间自动清理，长期使用会累积，需定期用管理台清理 |
| R14 | 透明置顶浮窗与系统托盘受桌面环境限制：部分远程桌面/无托盘环境无法显示托盘，个别显卡驱动下透明组合可能异常；托盘被折叠时“隐藏到托盘”的窗口难以找回 | 托盘不可用时自动跳过且不影响主窗口；浮窗始终可关闭，主窗口提供“形象”开关；主窗口改为占任务栏的普通窗口并以“最小化”收纳，托盘缺失也能从任务栏还原；主窗口与侧边面板不再置顶，常驻可见只由浮窗承担，避免挡住用户真正在用的程序；离屏 Qt 测试覆盖点击穿透、位置恢复与最小/隐藏/还原三态与窗口层级标志，真实桌面仍需人工确认视觉合成与任务栏还原尺寸 |
| R15 | 技能正文与参考文件是用户可编辑的本地内容，可能包含诱导模型越权或泄露上下文的指令 | 技能以工具结果注入，系统安全规则与工具策略仍优先；声明工具在加载与调用两层校验，本轮工具范围收窄；参考文件限制在技能目录内并有大小/单次字符上限；审计记录调用与拒绝，便于事后排查 |
| R16 | 备份、导出 zip 与 `tool_results/` 都是明文数据副本，删除数据库记录后仍可能残留 | 备份与导出仅在本地 `data/backups`、`data/exports`；全量删除提供“同时清理备份/导出”选项并提示不可撤销；导出不包含 `config/keys.json`，且日志与审计落库前脱敏；用户若把导出文件复制到别处，需自行管理 |
| R17 | 应用层若被多个 adapter 同时调用，进程内 run 注册表与 SQLite 会话写入可能竞争；远程进程也不能直接共享桌面内存状态 | M11 的 Web 适配器与壳层在同一进程内，仍受单实例锁与会话 active-run 守卫保护；远程/多客户端前需补持久化 run、事件游标、单写者模型与认证，不能把多个进程指向同一 SQLite 文件 |
| R18 | M10 同时改状态、消息、审批、右栏与会话操作，可能造成 UI 状态重复、审批入口回归或大范围视觉重写无法回滚 | 三批独立验收、独立提交；新增 UI 只消费应用服务投影；审批仍走既有 digest/TTL/模式/工作区/幂等链路；每批增加 Qt 端到端与离屏几何测试，不把视觉重构与执行内核改造混在同一切片 |
| R19 | Markdown、工具输出和来源链接均为不可信内容，富文本渲染可能引入 HTML 执行、危险链接或布局膨胀 | 只启用安全 Markdown 子集并禁用原始 HTML；外链需显式确认/安全协议白名单；代码、表格和长文本设折叠/截断边界；工具结果继续复用现有大小上限与会话隔离句柄 |
| R20 | 本地 Web 界面扩大攻击面：令牌可能出现在浏览器历史/进程参数，同机进程可尝试访问回环端口；WebEngine 关闭渲染器沙箱后若加载不可信内容风险更高 | 只绑定 `127.0.0.1`、每次启动随机端口与随机令牌、`compare_digest` 校验；静态资源只从 `web/assets/` 读取并做路径包含检查，不提供上传或目录浏览；页面不加载远端脚本，`qwebchannel.js` 使用随包固定副本；远程访问/HTTPS/账号体系不在当前范围，若开启需先加认证与 CSRF/Origin 校验。令牌与随机端口会写入 `data/.companion.endpoint`（供第二次启动唤回使用，退出时删除），该文件与其它本地数据同权限、只在回环地址使用，不写入数据库或日志 |
| R21 | ~~Web 前端的排版 token 与 Qt 不一致：`--scale` 只乘字号、控件写死高度、无间距 token，150/200% 字体缩放与 780px 窄窗下出现文字裁切、标签溢出与窗口按钮缺失~~ **已由 M12.1 关闭（2026-10-29）** | M12.1 按 D55 修复；`tokens.css` 承接 `theme.py` 的 spacing token，固定 `height` 改 `min-height: calc(字体度量)`；新增 100/125/150/200% × 1000×700/780×560 的离屏截图回归与“控件不得使用固定 height”的断言；Pixso 变量同步后设计源与代码不再各说一套。**残留**：代码侧的字号/间距契约已由 M12.2 收敛为同一套 7 档字阶（`tokens.css` 的 `--fs-*` 与 `theme.TYPE_TOKENS` 逐档相等，由静态断言守护），不再有两处定义；剩下的只是 Pixso 画板需要设计侧手工对齐，属设计源维护而非代码漂移。 |
| R22 | Qt 与 Web 两套前端各自实现 Markdown 子集与 token，已出现能力漂移（Web 无引用/表格，Qt 有引用；使用说明曾把 `SPACE_TOKENS` 记成两端通用） | ~~M12.3 对齐子集并用同一组样本对两套渲染做断言~~ **已由 M12.3 关闭（2026-10-30）**：token 漂移由 M12.2 收敛为同一套 7 档字阶（静态断言逐档相等），Markdown 漂移由 C1 收敛为“一份语法、两个实现、两套呈现”——`ui/markdown.py` 与 `web/assets/markdown.js` 对 21 个共享样本输出逐字节相等，呈现差异只留在 QSS/CSS（D61）。长期方向是只保留一套前端，Qt/Tk 移除需另立里程碑并确认兜底（R17 延伸，2026-10-29） |
| R23 | 桌面浮窗是"常驻可见"的唯一承担者，但 Web 浮窗右键菜单被吞、无 hover 入口、主动气泡单条覆盖、形象静态且以 8 秒轮询取状态；真实桌面的透明合成与托盘行为仍只经离屏测试验证（R14 延伸） | M12.4 补齐队列与入口、改订阅 SSE 并把轮询降为兜底、抽公共 avatar 模块；仍需在真实桌面人工确认浮窗命中、点击穿透与多显示器夹取，不以离屏测试代替 |
| R24 | 用户自备素材带来三类新风险：`/api/asset` 若沿用不校验令牌的 `/assets/` 通道会把数据目录变成任意文件读取面；大图/模型（几十 MB 的贴图与 `.moc3`）拖慢首屏并撑爆备份导出；Cubism Core 与模型的再分发许可未确认前，任何"随包提供"的写法都是合规事故 | M13.1 把素材读取收敛到 token 门控的 `/api/asset/<slot>` + 扩展名白名单 + 体积上限，`_asset()` 的 `base` 参数不对外暴露路径；素材目录整体 gitignore 且不进安装包（D57），备份/导出是否包含 `data/assets/` 由 M13.1 明确决定并写进文档，不默认打包；M13.3 开工前先确认 Cubism 许可与 WebGL 可用性（R25）。**同日修订（2026-10-30）**：末句原写"失败一律逐级回退"，按"完全替换"决定作废——桌宠不设回退档，Live2D 不可用即功能失效（D58）；本条的读取面、体积与许可三项边界不变 |
| R25 | 桌宠改为"Live2D 唯一载体、不可用即失效"（D58）后，三项此前不存在的单点风险同时成立：① `QWebEngineView` 的 WebGL 上下文能否在无边框 + 透明 + 置顶 + 点击穿透的浮窗里稳定合成，本机从未验证过——离屏测试跑不出真实桌面合成，正是 R14/R23 遗留的那条边界；② WebEngine 缺失或被禁用时桌宠没有任何替代路径（旧的 Qt 原生浮窗按计划删除），`--ui qt`/`tk` 兼容入口从此不提供桌宠；③ 模型把几十 MB 贴图与 Cubism 运行时拉进首屏，浮窗与主窗共用进程与 GPU 时，待机开销可能波及聊天与审批响应 | M13.3 拆成"前置验证 → 接入 + 删除"两步，验证不通过就不进入删除清单（本条是删除的闸门，不是删除后的兜底）；优先实测同进程 GPU 合成开销，必要时评估独立渲染层，但**不**因此引入常驻后端（守 D53/D55 边界）；`--ui qt`/`tk` 下开启浮窗给显式提示而非静默无反应；待机帧率、CPU 与内存进 M13.3 出口条件并按 R14 在真实桌面人工确认；失效原因经 `/api/overlay` 载荷 + SSE `{"type":"ui"}` 同一份状态下发，设置页与 `doctor.py` 同时可见，避免"功能关了但没人知道为什么"（2026-10-30） |

## 10. 下一步（本周）

已完成（2026-10-26）：

- [x] 提交 M1a/M1b/M2 第一批全部未入库成果（`f6f4647` 代码、`b6bdbe9` 文档），恢复可回滚状态。
- [x] 删除根目录 `$null` 垃圾文件（确认为 GBK 编码的 cmd 报错残留）。
- [x] M2 第二批第一切片：中文 2/3-gram 预分词 + FTS5/BM25 候选召回 + 关键词降级 + 索引启动自愈 + `recall_log.source`（14 个新测试，全量 80 个通过）。
- [x] `doctor.py` 数据库完整性/迁移版本/FTS 状态自检；真实 `data/companion.db` 已迁移到 `user_version=2`。
- [x] 记录 DSH 沙箱内测试不可运行的机制（tempfile 私有 DACL 与受限令牌冲突），避免误判为回归。

已完成（M2 第二批，2026-10-26）：

- [x] 旧聊天导入：成本预估、暂停续跑、跨副本去重、墓碑过滤。
- [x] 跨类型遗忘传播：派生经历/反思/摘要随来源事实失效。
- [x] 证据与召回台：来源原文、候选评分、最近召回与 `source`。
- [x] 人设类事实 `protected` 写入入口与验收。
- [x] 迁移 v3 与 13 个新回归测试；全量 93 个测试通过（收尾加固前）。
- [x] 角色扮演/引用台词防污染：单条消息守卫 + 导入会话状态识别；新增 5 个回归测试，全量 98 个测试通过（6 个环境跳过）。
- [x] 更新计划：新增 M2.5 人设层、决策 D16/D17 与风险 R9–R11。

已完成（M2.5 第一切片，2026-10-26）：

- [x] 新增 `data/personas/remielle/`：蕾米埃尔人格、背景事实、来源/许可/剧透边界元数据。
- [x] 新增 `persona.py`：路径校验、大小上限、稳定前缀与 SHA-256 指纹、缺失/损坏回退。
- [x] `runtime.py`：基础安全规则 + 稳定人设 + 模式/情绪/用户证据记忆分层；`persona_info()`；离线角色语气。
- [x] Qt/Tk 标题与输入提示使用人设名；`companion.py --persona` 支持切换本地人设包。
- [x] 新增 11 个回归测试；全量 109 个测试通过。

已完成（M2.5 第二切片，2026-10-26）：

- [x] `worldbook.md` 解析与触发式检索；最多注入 2 条、约 1200 字符，不进入稳定前缀哈希。
- [x] persona pack 预览与导入：来源/许可/大小/版本、忽略素材、同 ID 显式覆盖、坏包拒绝。
- [x] 用户称呼/关系/边界以 keyed `protected` persona 记忆保存，Qt/Tk 人设面板可编辑；离线称呼同步。
- [x] 新增 15 个回归测试；全量 124 个测试通过。

已完成（M3 第一切片，2026-10-26）：

- [x] `context.py`：稳定层（安全规则 + persona）、会话前缀（模式 + 排序后的工具）、运行时上下文（情绪/世界书/摘要）、相关记忆分层。
- [x] 稳定层 SHA-256；模式切换只改变会话前缀；工具 Schema 名称排序。
- [x] 字符预算窗口：保留最近完整消息，82% 触发压缩；抽取式摘要写入 `context_checkpoints`（迁移 v4）并复用。
- [x] 新增 12 个回归测试；全量 136 个测试通过。

已完成（M3 第二切片，2026-10-26）：

- [x] 工具输出超过 16KB（UTF-8 字节）落盘到 `data/tool_results/`，只回预览、字符数与 `result_id`；单条存储上限 4MB。
- [x] 新增 `read_tool_result` 分段回读：offset/limit、句柄格式校验、会话隔离、重启后可读。
- [x] 明确“未回读不算已读”：稳定层提示、落盘通知与回读头部三处一致；存储不可用时降级为“仅预览”。
- [x] 新增 9 个回归测试；全量 145 个测试通过。
- [x] 记录 D21 与 R13。

已完成（M3.5 代码收尾，2026-10-26）：

- [x] M3R-1：召回在 `_build_prompt` 每轮只取一次，压缩重排复用同一组记忆行，不再二次写 `recall_log`。
- [x] M3R-2：`log_recall` 返回 `recall_id`；回复生成后按记忆原文或 ≥60% 的 2-gram 重合更新 `used_in_reply`。
- [x] M3R-3：单字中文查询绕过 FTS，关键词兜底支持单字子串匹配；召回来源如实记为 `keyword`。
- [x] 新增 3 个回归测试；全量 148 个测试通过。

已完成（M3.5 评测与冒烟，2026-10-27）：

- [x] 新增 `eval_recall.py`：固定种子记忆 + 26 条中文查询（单字、短词、长句、ASCII），输出 hit@1 / hit@5 / MRR 与缺失用例；不调用模型。
- [x] 基线：`cases=26 limit=5 hit@1=1.0000 hit@5=1.0000 mrr=1.0000`；作为后续分词、评分与衰减调整的对照。
- [x] 真实模型冒烟一：`current_time` 原生 function calling，2 轮 1.31 秒，回答包含真实时间与日期。
- [x] 真实模型冒烟二：`search_web` → 30,857 字节结果落盘 → `read_tool_result` 回读 → 最终回答，3 轮 4.21 秒，回答含 `result_id` 与 `0–2000 / 10471` 读取区间。
- [x] M3R-8：修复末轮工具结果被丢弃；新增 2 个工具循环用例（有收尾回答 / 无收尾回答）。
- [x] 全量 151 个测试通过（8 个环境跳过）。
- [x] M3 + M3.5 已提交（`23c7837`，文档状态 `c099c94`）。

已完成（M4.0 主动底座，2026-10-27）：

- [x] 迁移 v5：`reminders` 表（pending/done/dismissed、`due_at` 索引、来源消息），真实库迁移后 12 条消息保留。
- [x] `proactive/reminders.py`：创建、到点查询、下一条到点、完成/忽略；相对时间解析支持“10 分钟/2 小时/三天后提醒我…”（阿拉伯数字与中文多位数字、“半小时”“30 秒”），以及“晚上 9 点/明天早上 7 点/18:30”这类本地钟点（`resolve_when()`），不调用模型；解析不出的说法由模型的 `set_reminder` 工具走同一条解析。
- [x] `proactive/topics.py`：未回复、连续用户消息、已知失败回复三类未完成话题；24 小时窗口、最多 3 条，附带 `source_message_id`。
- [x] `proactive/activity.py`：`ctypes` 读取空闲时间、前台标题、进程名与全屏状态；状态 `active/idle/away/private/focus/game/unknown`，探测失败或非 Windows 时保守关门。
- [x] `proactive/gate.py`：纯函数门控，按 Quiet → 忙 → 免打扰（22–08，可关闭）→ 每日 2 次 → 间隔 3 小时 → 连续 2 次未回复 → 活动/隐私门依次判定。
- [x] `proactive/engine.py`：提醒与未完成话题统一为带 `trigger_reason` 的来源；空来源返回 `no_source`，不生成随机话题；门控拒绝时保留来源但不发言。
- [x] `runtime.py`：提醒自然语言入口、`list/create/complete/dismiss_reminder`、`activity_state()`、`proactive_preview()`；`doctor.py` 新增 `Activity probe`。
- [x] 新增 22 个回归测试；全量 173 个测试通过（3.12.6 无跳过；3.8.8 跳过 8 个 Qt 与 1 个 Tk 测试）。

已完成（M4 主体，2026-10-27）：

- [x] 迁移 v6：`proactive_state`（开关/免打扰/每日/间隔/未回复阈值、计数、`last_spoken_at`、`last_event_id`）与 `proactive_events`（来源、触发原因、哈希、状态、消息 ID、未读）。
- [x] `ProactiveService`：送达、错过、暂停、跨天重置、未回复清零与事件已读；`tick()` 落库助手消息并更新状态。
- [x] 反重复：话题 24h / 记忆 72h 半衰期 + 内容/URL 哈希；提醒豁免；重复来源自动尝试下一条。
- [x] 记忆来源 opt-in（48h 内 `used_in_reply=1` 的已确认记忆）；主动消息本地模板生成，不调用模型。
- [x] Qt 每 60 秒轮询 + “主动”面板（设置、事件、未读、现在检查）；Tk 对应入口。
- [x] `doctor.py` 新增 `Proactive state`；真实库迁移到 v6，12 条消息保留。
- [x] 新增 28 个服务/单元测试与 1 个 Qt 面板测试；全量 202 个测试通过（3.12.6 无跳过）。

已完成（M5 工作任务卡，2026-10-27）：

- [x] 迁移 v7：`work_tasks`（状态、任务卡六要素、`info_asked`、`revision`、审批状态/原因、TTL、`approved_at`）与 `work_receipts`（状态、摘要、外部 ID、尝试次数）；真实库迁移后 12 条消息保留。
- [x] `work/router.py`：规则优先的 `companion/work/clarify` 路由、注入守卫、外部副作用/计划/本地流程分流、可选模型分类（不可信 JSON、12 秒硬上限、超时/非法降级）。
- [x] `work/service.py`：任务卡构建、`PLAN_DISCUSSING` 只追问一次、补充信息合并、审阅/执行状态变更、批准/修改/拒绝、TTL 过期、回执终态与启动恢复。
- [x] `runtime.py` 接入 `WorkService`；只有外部副作用请求自动建卡，本地搜索/笔记流程与陪伴模式不变；新增 `work_*` 运行时 API。
- [x] Qt“任务”面板与 Tk 任务窗口：任务列表、详情、批准/修改/拒绝/刷新，任务生成后自动打开。
- [x] `doctor.py` 新增 `Work state`；真实库迁移到 v7，12 条消息保留。
- [x] 新增 37 个任务卡测试与 1 个 Qt 面板测试；全量 240 个测试通过（3.12.6 无跳过）。

已完成（M6 桌面形象与流式交互，2026-10-27）：

- [x] `llm.py`：新增 `StreamEvent` 与 `stream_chat`；基础客户端从 `chat` 生成 delta + done；远程客户端用 `stream=True` 并按 index 拼装工具调用增量，异常转 `error`。
- [x] `runtime.py`：新增 `stream_message()` 与统一事件生成（含非模型分支），generation id 透传；停止保留已显示部分并写“（已停止生成）”；工具前导文本发 `reset`；新增 `desktop_status()`。
- [x] `streaming.py`：`strip_control_markers` 与增量 `MarkerFilter`，跨分片剥离 `[[mood/action/sticker]]`，未知/未闭合标记原样保留。
- [x] `ui/state.py`：`UiStateStore`（原子写入）与 `clamp_position`（最近屏幕、边界夹取）。
- [x] `ui/stream.py`：`StreamWorker`（QThread）消费运行时流，取消后抑制迟到事件并返回 `cancelled`。
- [x] `ui/qt_app.py`：透明置顶 `AvatarOverlay`（默认点击穿透、命中检测、拖动、位置保存/恢复）、停止按钮、generation 过滤、主窗口“形象”开关。
- [x] `ui/tray.py`：`TrayController` 提供窗口显隐、浮窗显隐、模式切换、暂停主动、现在检查与退出；托盘不可用时自动跳过。
- [x] 新增 24 个流式/状态单元测试与 7 个 Qt 用例；全量 271 个测试通过（3.12.6 无跳过）；`doctor.py` 全项 OK。

已完成（M7 技能包，2026-10-27）：

- [x] `skills/manifest.py`：严格 frontmatter 解析（单层键值/列表）、必填字段、名称与目录一致、effectKind/模式/参考文件名校验、正文 8000 字符与文件 128KB 上限。
- [x] `skills/library.py`：技能扫描、风险推导与模式过滤、未知/越权工具拒绝、参考文件存在性与大小校验、offset/limit 分段读取与目录包含检查。
- [x] `runtime.py`：仅有可用技能时注册 `invoke_skill` / `read_skill_reference`；技能正文按需注入；本轮工具范围收窄为声明工具 + 元工具；新增 `skills_status()` / `skill_audits()`。
- [x] 迁移 v8：`skill_audit` 记录调用、参考读取、越权工具拒绝；审计失败时拒绝技能调用。
- [x] `doctor.py` 新增 `Skills` 检查，与运行时共用 `CORE_TOOL_RISKS`；真实库迁移到 v8，14 条消息保留。
- [x] 新增 20 个技能测试；全量 291 个测试通过（3.12.6 无跳过）。

已完成（M8 管理与自检台，2026-10-27）：

- [x] `maintenance.py`：完整性报告（integrity/FK/schema/索引/FTS 行数/计数/大小）、在线备份与 manifest、备份校验、恢复与恢复前安全备份、zip 导出、按记忆树/会话/全量彻底删除、`InstanceLock` 与 `lock_info`。
- [x] `redaction.py`：`sk-*`、Bearer、密钥赋值与 GitHub token 脱敏；接入技能审计、执行失败原因与工作回执摘要/详情。
- [x] `store.py`：旧库有数据时迁移前自动快照（含失败原因字段）；新增 `list_sessions/list_proposals/list_affect_states` 供管理台查询。
- [x] `ui/console/` 7 页：记忆、召回、候选、执行与审计、情绪、失败（只读派生）、设置与数据；主窗口顶部与托盘新增“管理”入口。
- [x] `companion.py`：默认单实例锁，第二个实例提示占用并退出（码 3）；`--allow-multiple` 跳过。
- [x] `doctor.py`：新增 schema 版本/缺失索引/FTS 行数、`Backups`、`Instance lock` 检查。
- [x] 新增 18 个维护测试与 3 个离屏管理台用例；全量 312 个测试通过（3.12.6 无跳过）。

已完成（M9 应用层契约与 Qt 适配器，2026-10-28）：

- [x] 新增 `app/contracts.py` / `ports.py` / `service.py` / `bootstrap.py`：命令、run、事件、审批视图、工作区快照与应用服务组合入口。
- [x] 运行生命周期：同会话 active-run 守卫、生成中模式/工作区/会话/审批/撤销拒绝、取消传播、同步运行、run 状态与 sequence。
- [x] 完成边界：`done` 前按 run 起始消息 ID 与最终 answer 查询 assistant 消息；未落库则 `TurnPersistenceError`，不向 adapter 报完成。
- [x] Qt 接入：`StreamWorker(application, run)`、`CompanionWindow(runtime|service)`、核心操作改走 service、run id 作为 generation id；停止继续读到 runtime done，迟到事件由 UI 抑制。
- [x] `companion.py` 的 Qt 启动显式构造 application service；Tk/CLI 保持 runtime 入口，Qt 端到端显式使用 service，其余测试继续使用 runtime 兼容构造。
- [x] 新增 `tests/test_companion_app.py` 9 个用例；Qt 26 个通过；全量 359 个通过、1 个 Tk 环境跳过；`compileall` 通过；`doctor.py` 输出 `Environment check passed.`。
- [x] 记录 D48 与 R17；明确 FastAPI/SSE、跨进程/持久化 run、全面板 API 化不在本轮范围。

已完成（M10.1 运行可观察与消息表达，2026-10-28）：

- [x] `app/contracts.py`：新增 `RunStage`、`TranscriptMessage`、`RunView`、`ProjectionSection`、`UiProjection`；`RunContext` 增加 `sequence / stage / detail / tool`，`ApplicationEvent` 增加 `stage`。
- [x] `app/projection.py`：事件到阶段的单点映射、当前工具/错误摘要、右栏分节派生；读取失败与“没有待办”区分显示。
- [x] `app/service.py`：流式过程中按事件推进 run 的 stage/sequence，异常与未收尾分别落到 `failed / cancelled`；新增只读 `ui_projection()`。
- [x] `ui/inspector.py`：改为读取投影（无投影时兼容回退），不再直接读取 runtime 私有状态。
- [x] `ui/markdown.py`：安全 Markdown 子集与协议白名单；`ui/chat_view.py` 改为富文本气泡，新增复制、日期分隔、角色/时间元信息与粘底滚动。
- [x] `ui/run_bar.py` 与 `ui/qt_app.py`：输入框上方统一运行条，展示阶段、工具、耗时与停止；等待审批、完成、取消、失败均有终态；Markdown 链接点击需二次确认。
- [x] `tests/test_companion_markdown.py` 8 个；应用层 +3、UI 主题 +12、Qt +2；全量 383 个通过、1 个 Tk 环境跳过。
- [x] 记录 D50，更新 README 与使用说明；成果未提交。

已完成（M10.2 审批、证据与上下文抽屉，2026-10-29）：

- [x] `file_actions.py`：新增 `diff_lines()` 与 `NoteActionService.preview()`；预览只读，输出 create/update、增删行数、80 行/180 字符截断与工作区快照错误，不创建目录、不写入、不消耗提案。
- [x] `runtime.py`：新增 `preview_proposal()`（仅当前会话）；搜索/工具事件携带参数摘要、`ok / code / error / preview / sources / result_id / result_chars / result_truncated`，`last_tool_sources` 供右栏来源节使用。
- [x] 应用层：新增 `ApprovalPreview`，`ApprovalView` 增加 `tool / workspace / expired / preview`；`confirm_action()` 拒绝过期提案；投影新增“文件”与“来源”分节。
- [x] `ui/approval_card.py`：内联事务卡展示目标、工作区、模式、风险、有效期、可撤销性、计划与 Diff；确认、取消、执行结果、撤销与失败都在同一张卡内闭环，过期提案禁用确认。
- [x] `ui/chat_view.py`：工具回执改为可展开卡片，展示参数、结果预览、错误码与原因、前 5 条来源和 `tool_result` 句柄；展开状态按消息 ID 记忆，重渲染不丢失。
- [x] `ui/inspector.py` 与 `ui/qt_app.py`：右栏改为“执行 / 文件 / 来源 / 上下文 / 任务”标签抽屉，支持关闭、拖动宽度与 `ui_state.json` 恢复；940px 先收右栏、860px 把左栏改为覆盖抽屉，780px 下中部阅读区不被挤压。
- [x] 新增/更新测试 21 个：文件预览 +4、应用层 +4、工具事件 +2、UI 主题 +9、Qt +2；全量 404 个通过、1 个 Tk 环境跳过。
- [x] 记录 D51，更新 README 与使用说明；成果未提交。

已完成（M10.3 会话、输入与设置体验，2026-10-29）：

- [x] `store.py`：迁移 v9 增加 `sessions.title / pinned_at / unread` 与 `session_drafts`；新增重命名、置顶、未读、草稿读写与 `delete_session()` 级联删除，旧库升级与空库初始化均有回归。
- [x] `runtime.py`：会话切换清除目标未读、重命名/置顶/草稿按会话隔离；删除当前会话回退到最新剩余会话，删除最后一条时创建替代会话。
- [x] `ui/qt_app.py`：会话行分组与相对时间、`⋯`/右键/键盘管理、`Ctrl+N`/`Ctrl+K`/`Ctrl+,`、成功与失败反馈、生成中拒绝跨会话操作；Composer 2–5 行自适应、每会话草稿、字数提示、模式/工作区/上下文胶囊与单一发送/停止主按钮。
- [x] 空状态：陪伴/工作两套角色化文案、三条快捷开场与最近任务；快捷开场填入输入框并聚焦。
- [x] `ui/settings.py`：左分类 + 右内容双栏、`Ctrl+F` 搜索、分类状态摘要与“更多”渐进展开、主题/浮窗快速入口、字体缩放与减少动态效果。
- [x] `ui/theme.py` / `ui/chat_view.py` / `ui/state.py` / `ui/inspector.py`：`TYPE_TOKENS / SPACE_TOKENS / FONT_SCALES` 契约、统一焦点与可访问名称、图标工具提示、减少动态效果、字体缩放持久化与离屏尺寸截图。
- [x] 新增/更新 27 个测试；全量 431 个通过、1 个 Tk 环境跳过。
- [x] 记录 D52，更新 README 与使用说明；成果未提交。

已完成（M11 Pixso Web UI 迁移，2026-10-29）：

- [x] Pixso 变量与文字样式承接 `theme.py` token；重建深色主界面、珍珠白空状态、设置、管理台、桌面浮窗与 780×560 窄窗 6 个画板。
- [x] `web/server.py`：回环 HTTP 适配器、令牌校验、路径逃逸防护、SSE 事件扇出与 `/api/*` 命令/查询；回合、停止、模式、工作区、会话、草稿、界面、人设、审批、撤销、主动与管理台查询全部映射到应用服务。
- [x] `web/assets/`：由 Pixso 画板派生的主界面、设置、管理台与浮窗页面；浏览器模式隐藏窗口按钮并提供返回路径。
- [x] `web/shell.py`：QtWebEngine 无边框壳层、设置/管理台子窗口、透明置顶浮窗、几何持久化与 QWebChannel 宿主桥接；Windows 设置 `QTWEBENGINE_DISABLE_SANDBOX=1`，`qwebchannel.js` 随包提供。
- [x] `companion.py` 默认 `--ui web`，新增 `--ui browser`，`qt`/`tk` 保留兼容；README 与使用说明同步。
- [x] 新增 12 个适配器测试；全量 443 个通过、1 个 Tk 环境跳过；浏览器与 Qt 壳层目视核对完成；关闭隐藏后再次启动可唤回运行中的实例。
- [x] 记录 D53 与 R20；成果未提交。

已完成（M12.1 缩放正确性与视觉 token，2026-10-29）：

- [x] `tokens.css` 新增 `--sp-*`（半速跟随 `--scale`）、`--ctl-*`（由 `--fs-*` 推导的控制度量）、`--font-mono`、`--lh-*`、`--fw-*`、`--radius-pill/card`，与 Qt `ui/theme.py` 的 `SPACE_TOKENS` 契约对齐。
- [x] `app.css / settings.css / console.css / overlay.css` 去掉全部固定 `height` 与裸像素间距；标签条可横向滚动；`@media` 不再隐藏窗口按钮，退化为图标；`.send` 按字号取最小宽度；阅读列宽与 rail 宽度跟随 `--sp-scale`。
- [x] `app.js` 响应式断点改按字号归一化（`responsiveWidth()`），右栏开关按钮共用同一判定；会话行改语义 `<button>`，补 `aria-current/aria-pressed/aria-selected`、chat `role=log` 与流式 `aria-live`。
- [x] `index.html / settings.html / console.html / overlay.html` 补 `role=tablist/tab/tabpanel`、`aria-label` 与 `role=status`；管理台表格行键盘可达；删除 `outline:none`，统一 `:focus-visible` 焦点环。
- [x] `/api/overlay` 载荷新增 `font_scale`，浮窗与主界面共用同一缩放；`.pet` 容器化消除形象 SVG 溢出。
- [x] 新增 `web/layout_audit.py` 与 `tests/test_companion_web_layout.py`（真实浏览器 20 组合零溢出 + 图标退化断言）、`tests/test_companion_web_style.py` 17 个静态断言、`tests/test_companion_web.py` 浮窗载荷断言；全量 443 → 463 个通过、1 个 Tk 环境跳过；`doctor.py` 仍为 `Environment check passed.`；成果未提交。

已完成（M12.2 排版层级，2026-10-29）：

- [x] 两端共用 7 档字阶：`tokens.css` 的 `--fs-caption/meta/ui/body/large/subtitle/display` = 11/12/13/14/16/18/24，与 `ui/theme.py` 的 `TYPE_TOKENS` 逐档相等；正文 13→14、UI 文本 12→13、元信息 11→12、说明 10→11，并补出顶部阶梯（web 从未使用的 `--fs-title` 与 Qt 侧的 `heading` 档由 subtitle/display 取代）。
- [x] `ui/theme.py` 的 41 处 QSS 字号字面量改走 `_type("<token>")`，保持 `stylesheet()` 后置 `re.sub` 的 `font_scale` 通道；`ui/markdown.py` 标题按 level 取 large/body；`ui/qt_app.py` 浮窗气泡改用 `font_px("ui", scale)`，`SPACE_TOKENS` 补 `xl: 18`。
- [x] 按角色重新分档（会话时间/工具详情/审批元信息/状态栏不再全靠 caption），空状态标题 → display、人设名 → large、窗口标题与管理台情绪标题 → subtitle、rail 分组标题 → meta（与 Qt `#sessionGroup` 对齐）。
- [x] 行高收敛为 `--lh-control/meta/body` 三档，五个样式表零裸 `line-height`；图标字形（`.new-task .plus`、浮窗 `.close`）例外走 `--lh-meta`，因为 em 盒小于 "+" 的墨迹，200% 下会被裁 2px。
- [x] 阅读列改按字号计量：`--read-measure = --fs-body × 42`、`--col-measure = read-measure / 0.74`，`.chat-inner`/`.composer-wrap`/`.approval` 引用之；页面样式表零 `max-width: calc(Npx`。计划的 `rem` 方案在实测后放弃：项目没有 `html { font-size }`，`rem` 只解析到 UA 16px 且完全不随 `--scale` 变。
- [x] `--ctl-*` 随新字阶重导（`--ctl-bar` 按栏内最大的控件标签而非页面标题测量），开关尺寸/滑块位移改由 `--ctl-switch-*` 推导；新增 `body.compact` 在 200% 下收起窗口操作与模式段的文字标签。
- [x] `layout_audit` 探针扩展：上报 `.bubble .body` 每行字符容量与顶栏/聊天区高度。实测 100/125/150/200% 下 1000×700 每行 23/36/33/24 字、聊天区占屏 68/62/55/42%，780×560 每行 37/30/25/18 字、占屏 60/53/44/27%；20 组合仍零裁切零溢出。
- [x] `tests/test_companion_web_style.py` +7 排版契约、`tests/test_companion_web_layout.py` +2 浏览器度量并把图标退化判据改为"原始宽度与字号归一宽度都要够"；全量 463 → 472 个通过、1 个 Tk 环境跳过；`doctor.py` 仍为 `Environment check passed.`；成果未提交。

已完成（M12.3 第一子片 C2：上下文投影字段与环形仪表，2026-10-30）：

- [x] `app/contracts.py` 新增 `ContextView(context_used, context_limit, compressed)`（`percent` 只是属性，`plain()` 不序列化，因此契约里传的是两个整数，比值由使用方算）；`UiProjection` 加 `context` 字段。
- [x] `app/projection.py` 新增单点推导 `build_context_view(runtime)`：详情面板"上下文"文案与两端数字共用同一份推导，预算不可用（`budget_chars <= 0`）或尚未构建提示词时返回 `None`，两端一致地显示"待命"而不是 0%。
- [x] `/api/bootstrap` 新增 `context` 字段；Web 胶囊从文本改为 conic-gradient 环（环径 `calc(var(--fs-meta) * 1.1)`，随字号长高：实测 100% 13px、150% 20px），70%/90% 阈值切 `--warning`/`--danger`，点击仍把详情面板切到"上下文"标签；删除前端 `contextPercent()` 的文案正则。
- [x] Qt 端 `refresh_composer_hint()` 不再直读 `runtime.last_context`，改取同一投影（R22 的漂移少一处）；胶囊文本"上下文 38%"、tooltip 带 `1500 / 4000 字符` 与压缩标记。
- [x] 新增 10 个用例（投影 4 / Web 适配器 2 / 静态契约 3 / Qt 1）：全量 472 → **482 个通过、1 个 Tk 环境跳过**；`layout_audit` 4 项浏览器回归通过，另用一次性 Playwright 探针实测环径、控件高度与相邻胶囊一致（20/29px）、不溢出且点击后详情面板出现"用量：1500 / 4000 字符"；`doctor.py` 仍为 `Environment check passed.`；成果未提交。

已完成（M12.3 第二子片 C4：运行步骤时间线，2026-10-30）：

- [x] `app/contracts.py` 新增 `RunStep(sequence, stage, at, detail, tool)` 与 `RunView.steps`（`plain()` 按字段名递归序列化 dataclass，因此不需要新增端点，沿用 D59 的"契约只放原始值"写法）；`RunContext.steps` 是运行中的账本，`repr/compare` 排除以保持冻结语义不变。
- [x] `app/service.py::_record_step()` 在服务层单点记录：相邻 `(stage, detail, tool)` 相同的行折叠成一行（20 个流式 delta 只留一条"生成回答"），工具行携带工具名与 `名称：摘要`，`failed`/`cancelled` 终态也各记一行并带失败原因；`run_view()` 下发 `tuple(run.steps)`。`done` 后 run 释放、`run_view()` 归 `None`，时间线只描述进行中的这一轮，因此不需要步数上限。
- [x] Web：`#runStrip` 新增 `#runStepsToggle`（`aria-expanded/aria-controls`）与 `<ol id="runSteps">`——列表**在 `role="status"` 之外**，避免每步都读屏播报；每行是转义后的"阶段 / 内容 / HH:MM"，展开高度 `calc(var(--ctl-bar) * 3)`、内部滚动并自动跟到底，字号/行高/间距全部走 token。`state.steps` 缓存 + `state.stepsOpen`，SSE 事件只在 `stage` 变化时触发一次 `scheduleRefresh()`，前端不再自行拼"已完成"。
- [x] Qt：`ui/run_bar.py` 同一套语义——`runStepsToggle` + `QScrollArea#runStepsArea`，可见 3 行按 `fontMetrics().height()` 计算，`set_steps()` 只接收投影行、`begin()/reset()` 清空；新增 `ui/timefmt.py::clock()` 统一两端时间格式；`ui/qt_app.py` 在阶段变化时 `_sync_run_steps()`；`ui/theme.py` 补四条选择器（按钮、hover、滚动容器、行文本）。
- [x] 修掉两个真实缺陷：① **D60**——`web/server.py::_stream()` 在 `Transfer-Encoding: chunked` 下把首帧未经分帧直接写出，Chromium 判 `net::ERR_INVALID_CHUNKED_ENCODING` 断开，`/api/events` 在浏览器与壳层里从未真正工作过；改为 `_write_chunk()` 统一分帧并在断开时补终止块。② Web 折叠按钮原从 `state.data.run` 重绘，快照里没有 run 时点击无反应，改为从缓存的 `state.steps` 重绘。
- [x] 新增 12 个用例（应用层折叠与失败终态 2、Web 端含 SSE 原始 socket 分帧与 `/api/bootstrap` 时间线 2、静态契约 5、Qt 主题 2、Qt 离屏端到端 1）：全量 482 → **494 个通过、1 个 Tk 环境跳过**。真实 Chromium 在 100/150/200% 各跑一轮带工具的回合：5 行、`aria-expanded=true`、列表字号 12/18/24px、行高 18/27/36px、展开上限 144/210/276px、无 console 报错、结束后运行条收束；Qt 离屏同三档折叠 21/29/36px → 展开 67/75/112px、按钮"步骤 5"、输入框不被挤出窗口。`layout_audit` 仍 `Layout audit passed.`（0 problems），`doctor.py` 仍 `Environment check passed.`；成果未提交。

已完成（M12.3 第三子片 C1：Markdown 子集对齐与扩展，2026-10-30）：

- [x] 语法收敛为一份共享实现：`ui/markdown.py` 重写为语义渲染器（`_escape` 只做五个替换、行内代码先于其它标记、裸链截断尾部标点），新增 `web/assets/markdown.js` 为同一语法的 JS 实现；`tests/fixtures/markdown_samples.json` 21 个样本经 node 逐条比对**输出逐字节相等**（无 node 环境时该用例跳过）。
- [x] 新增语法：表格（`table.md-table`，按分隔行取 left/center/right，列数不符或只有一行时回退为普通段落）、层级列表（嵌套进未闭合的 `<li>`，深度 0–3）、删除线、代码块语言标签（`div.md-lang`）。标题改为 `<div class="md-h md-hN"><b>` —— Qt 的 `h1..h3` 强制约 1.5× 相对字号且忽略 `font-size`，类选择器才能跟字号档走。
- [x] Web：`app.js` 的 `markdown()` 与 `escapeHtml()` 删除，改走 `CompanionMarkdown`；`decorateCode()` 在渲染后把 `pre.md-code` 包进 `.code-block` 并追加复制按钮（`data-copy` 复用已有委托，点击提示"已复制"），**渲染器输出不受按钮影响**，两端一致性不被破坏；`index.html` 在 `app.js` 之前加载 `markdown.js`；`app.css` 补 `.md-h`/`blockquote.md-quote`/`table.md-table`/`.md-lang`/`pre`/`.code-block`/`.code-copy`，尺寸全部走 token。
- [x] Qt：`MarkdownLabel` 改用 `markdown.document(source, font_scale)`，字号档从 `font().pixelSize()` 反推最近的 `FONT_SCALES` 并在 `FontChange` 时重渲染；样式由 `qt_style()` 生成的内联 `<style>` 块承担（类选择器 + 按档换算的 px 边距）。**修掉 M12.2 残留缺陷**：QSS 阻断字体继承，气泡正文在任何字号档下都停在 9pt（`pixelSize() == -1`），补 `QLabel#bubbleBody { font-size: _type("body") }` 后正文才随缩放 14/21/28px。
- [x] 新增 16 个用例（`test_companion_markdown.py` 重写为 18 个含安全 URL/表格对齐/误判回退/嵌套结构/样式随字号/node 样本比对，`test_companion_web_style.py` +5 静态契约，`test_companion_ui_theme.py` 改 2 个改走样式表切换）：全量 494 → **510 个通过、1 个 Tk 环境跳过**。真实 Chromium 100/150/200% 核对同一段长回复：正文 14/21/28px、标题 16/24/32px、三列表格对齐、嵌套缩进 20/25/30px、删除线、语言标签与复制按钮（toast "已复制"、剪贴板等于代码原文），无溢出与 console 报错；Qt 离屏同三档正文 14/21/28px、标题 16/24/32px、气泡沫高 413/593/839px。`layout_audit` 与 `doctor.py` 均通过；成果未提交。

计划中（M12 Web UI 精细化，2026-10-29 登记；M12.1、M12.2 已完成，见上）：

- [ ] M12.3 消息与运行表达（C1/C2/C3/C4/C5/C8）：~~Markdown 子集对齐 Qt 并补表格/代码复制~~（**C1 已完成，2026-10-30**，见上）、~~上下文投影字段~~（**C2 已完成，2026-10-30**，见上）、~~运行步骤时间线~~（**C4 已完成，2026-10-30**，见上）、忙时待发队列、消息列表键控增量更新。
- [ ] M12.4 桌面浮窗能力（C6/C7/C10）：右键菜单与 hover 入口、主动气泡队列、折叠成小球与边缘吸附、把两份 `avatarSvg` 归一为单一挂载点、改订阅 SSE。**2026-10-30 修订**：原"纯 CSS 呼吸/眨眼待机动画"取消；该挂载点的程序化 SVG 分支将在 M13.3 随旧桌宠一并删除，本切片只负责"换载体时只改一处"（D56/D58 修订）。
- [ ] M12.5 视觉纵深与密度（U6/U7/U13/U14/U16）：阴影/模糊/渐变 token 分层、不对称气泡圆角与尾巴、去除重复控件与统一列宽、rail 图标折叠态。排在 M13 之前（2026-10-30 决定），但要把"内容表面"与"背景遮罩"拆成两个 token 并预留 `--bg-scrim` 的缝，让 M13.2 只需赋值不需改值。
- 差距清单、证据行号与参考基准见 4.1；出口条件见第 5 节 M12。本模块只动 CSS/token、投影只读字段与共享渲染层（除重新生成命令外不新增端点与迁移），M12.1、M12.2 已实施并通过真实浏览器回归，M12.3 进行中（C1、C2、C4 已关闭，剩 C3/C5/C8），M12.4、M12.5 未开始，成果均未提交。

已完成（角色素材槽位预留，2026-10-30，仅目录与文档）：

- [x] 预留 `data/assets/<persona_id>/`（`avatar.*` / `background.*` / `live2d/model3.json`），建好 `data/assets/remielle/live2d/` 骨架与两份放置说明，目录名与 `data/personas/<id>/` 一一对应。
- [x] `.gitignore` 精确放行 `data/assets/README.md` 与 `data/assets/*/README.md`，其余素材与 `live2d/` 子目录整体忽略（`git add -n data/assets` 只会加入两份 README）。
- [x] 登记 M13 三切片与 D57、R24，修订 D56 与 M12.4 的动画范围，更新 §0/§1/4.1 C7/§5/§6/§8/§10 与 README、使用说明。**未改任何运行代码，472 个测试基线不变，成果未提交。**

已完成（M13 计划修订，2026-10-30 同日第二笔，仅文档）：

- [x] 按用户确认的"完全替换"三项决定重写 M13：**不设回退档**（Live2D 不可用即桌宠功能失效，取消"Live2D → 静态头像 → 程序化 SVG"回退链）、**M12 先全部做完**（顺序改为 M12.3 → M12.4 → M12.5 → M13.1 → M13.2 → M13.3，撤销交错顺序）、**Web 透明浮窗为桌面形象唯一载体**（登记 Qt `AvatarOverlay` 与 QPainter 程序化形象、两份 `avatarSvg`、`build_avatar_icon()`、`avatar_palette()` 的删除清单及测试连带影响）。
- [x] 新增 D58（载体唯一 + 失效语义的理由与代价，含"消息不随形象失效"的边界）与 R25（WebGL×透明×置顶×穿透的合成可行性、WebEngine 缺失即无桌宠、模型体积与同进程 GPU 开销三项单点风险）；修订 R24 末句"逐级回退"、D56/D57 与 4.1 C7、M12.4/M12.5 的归属（M12.5 负责留出 `--bg-scrim` 缝，M13.2 只赋值）。
- [x] 同步 §0 结论、§1 阶段表、第 5 节 M13 三切片正文、第 6 节 M13 行与顺序约束、第 8 节决策表、第 9 节风险表、第 10 节待办与 §11 变更记录，以及 README、使用说明、技术方案阶段 5 与两份 `data/assets/` 放置说明。**仍未改任何运行代码，472 个测试基线不变，成果未提交。**

计划中（M13 角色素材接入，2026-10-30 登记并于同日按"完全替换"修订；排在 M12 全部收口之后）：

- [ ] M13.1 素材槽位与静态头像：只读解析 + 扩展名/体积白名单、token 门控的 `GET /api/asset/<slot>`、头像位"无素材即留空"（不再加 `avatar_enabled` 开关，也不绘制程序化形象）、doctor `Character assets`（已开启浮窗却无可用模型 → FAIL）。
- [ ] M13.2 可替换窗口背景：主窗/设置/管理台背景来自 `data/assets/<id>/background.*`，设置页选择与清除，复用 M12.5 留出的 `--bg-scrim` 缝，浮窗保持透明。
- [ ] M13.3 Live2D 桌宠运行时 + 旧桌宠下线：前置确认 Cubism 授权与"WebGL 在透明置顶可穿透浮窗里可用"（R25），再接 `QWebEngineView` + pixi-live2d-display，情绪标签驱动表情；不可用时桌宠失效并显示原因，**同一次改动**删除 Qt `AvatarOverlay`、QPainter 程序化绘制、`avatarSvg` 挂载点、`build_avatar_icon()`、`avatar_palette()`。

接下来（M12 之后，2026-10-30 按"完全替换"决定重排）：

1. 剩余切片按 **M12.3 → M12.4 → M12.5 → M13.1 → M13.2 → M13.3** 顺序实施，每片独立验收；**M12 三个切片全部做完之前不启动 M13**（用户 2026-10-30 决定，理由与撤销的交错顺序见第 6 节顺序约束、D56/D57/D58）。
2. 阶段 5 的首个里程碑就是 M13：**把桌宠完全替换为 Live2D**，不做兼容档——Live2D 不可用时桌宠功能失效，旧程序化形象与 Qt 原生浮窗在 M13.3 删除。在此之前不引入常驻后端。素材槽位 `data/assets/<persona_id>/` 已于 2026-10-30 预留，程序尚未读取。
3. 若开始远程/手机端，先补持久化 run/事件游标、身份认证与 HTTPS，再复用现有应用服务；本地 Web 适配器已就绪，但不应直接暴露到局域网或公网。
4. 语音与 Live2D：M13.3 先接模型显示与情绪表情（不做口型），TTS 播放进度驱动口型随语音切片再做；开工前先确认 Cubism Core 与模型授权，并在真实桌面验证"WebGL + 透明 + 置顶 + 点击穿透"能否共存（R25）——验证不通过就不进入旧形象删除那一步；角色素材仍只允许用户本地导入、不进仓库与安装包（R10/D57）。
5. 多角色与社交渠道：按会话隔离人设与记忆，渠道接入前先补齐出站消息确认与速率限制。
6. 任务卡可继续增加多执行器路由与转发结果回写；当前一次只绑定一个执行器。
7. （可选）真实模型压缩后继续多轮的冒烟，补齐 M3R-5 剩余部分；真实桌面下人工确认浮窗视觉合成与托盘行为（R23），M13.3 后同一批核对要覆盖 Live2D 的合成与穿透（R25）。
8. 技能的可视化编辑/市场/远程安装与技能间编排，待管理台稳定后再评估。
9. 主动陪伴的更多来源（如共同经历、日程）与打扰阈值调优，待真实使用数据积累后再做。
10. 工具结果与备份的按时间自动清理策略；当前依赖管理台手动清理。

以上需用户逐项确认后启动；`eval_recall.py` 在分词或评分参数变更后应重跑并记录基线变化。

## 11. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-30 | M12.3 第二子片 C4 运行步骤时间线（v1.38）：`app/contracts.py` 新增 `RunStep(sequence, stage, at, detail, tool)` 与 `RunView.steps`，`app/service.py::_record_step()` 在**服务层单点**记录并按相邻 `(stage, detail, tool)` 折叠（流式 20 个 delta 只留一条"生成回答"），工具行携带工具名与摘要，`failed`/`cancelled` 终态各记一行并带原因；`done` 后 run 释放、`run_view()` 归 `None`，所以时间线只描述进行中的一轮、无需步数上限。Web 端 `#runStepsToggle`（`aria-expanded/aria-controls`）+ `<ol id="runSteps">` 在 `role="status"` 活性区之外，展开高度 `--ctl-bar × 3`、内部滚动并自动跟到底，全部尺寸走 token；Qt 端 `RunBar` 同语义（`runStepsArea` + `fontMetrics().height()` 三行封顶），新增 `ui/timefmt.py::clock()` 统一时间格式，两端都只渲染投影的 `steps`、**不自造终态文案**。修掉两个真实缺陷（登记 **D60**）：① `web/server.py::_stream()` 声明 `Transfer-Encoding: chunked` 却把首帧 `retry: 1000\n\n` 未经分帧写出，Chromium 判 `net::ERR_INVALID_CHUNKED_ENCODING` 并每次重连都死在同一处——**浏览器与 QtWebEngine 壳层里的 `/api/events` 自 M11 起从未真正工作过**（发送后界面不更新、运行条不出现、跨窗口广播失效；浮窗因走 8 秒轮询不受影响，故长期未被发现），改为 `_write_chunk()` 统一分帧 + 断开时补终止块；② Web 折叠按钮从 `state.data.run` 重绘，快照无 run 时点击无反应，改缓存 `state.steps`。新增 12 个用例（应用层 2、Web 端 2 含原始 socket 帧格式、静态契约 5、Qt 主题 2、Qt 离屏端到端 1），全量 **482 → 494 个通过、1 个 Tk 环境跳过**；真实 Chromium 100/150/200% 与 Qt 离屏三档逐档实测行数、字号 12/18/24px、行高 18/27/36px、展开上限 144/210/276px 与运行条 21/29/36 → 67/75/112px，`layout_audit` 与 `doctor.py` 均通过。关闭 4.1 的 C4（累计关闭 14 项），同步 §0/§1/§2.1/§3.3/4.1/§5/§8/§10 与 README、使用说明。**成果未提交** |
| 2026-10-30 | M12.3 第三子片 C1 Markdown 子集对齐与扩展（v1.39）：`ui/markdown.py` 重写为语义渲染器，新增 `web/assets/markdown.js` 实现同一套语法，两端输出**逐字节相等**，由 `tests/fixtures/markdown_samples.json` 的 21 个样本经 node 逐条断言（共享语法单点、呈现各归各端，登记 **D61**）。新增表格（按分隔行取对齐，列数不符回退为段落）、层级列表（嵌套进未闭合 `<li>`，深度 0–3）、删除线、代码块语言标签；标题改用 `<div class="md-h md-hN"><b>`（Qt 的 `h1..h3` 强制约 1.5× 相对字号且忽略 `font-size`），Qt 侧样式由 `markdown.qt_style(font_scale)` 生成内联 `<style>` 块、边距按字号档换算 px（`em` 边距不随档缩放）。Web 端删除自带的 `markdown()`/`escapeHtml()`，改走 `CompanionMarkdown`，复制按钮由渲染后 `decorateCode()` 装饰 DOM 追加（`data-copy` 复用已有委托），因此**纯装饰不进入渲染器**；`index.html` 调整脚本顺序，`app.css` 补 `.md-*`/`table.md-table`/`.md-lang`/`.code-block`/`.code-copy` 规则且尺寸全走 token。**修掉 M12.2 残留缺陷**：QSS 阻断字体继承（`pixelSize() == -1`，气泡正文在任何字号档下停在 9pt），补 `QLabel#bubbleBody { font-size: _type("body") }` 后随缩放 14/21/28px。未引入语法高亮：shiki/Prism 需构建链或数 MB 运行时且 Qt 无法复用，违反 D56，语言标签 + 复制按钮已覆盖实际场景。新增 16 个用例（Markdown 18 个含安全 URL/表格对齐/误判回退/嵌套结构/样式随字号/node 样本比对、静态契约 5、Qt 主题 2 个改为样式表切换），全量 **494 → 510 个通过、1 个 Tk 环境跳过**；真实 Chromium 100/150/200% 与 Qt 离屏三档实测正文 14/21/28px、标题 16/24/32px、三列表格对齐、嵌套缩进 20/25/30px、删除线、复制按钮 toast "已复制" 且剪贴板等于代码原文、气泡沫高 413/593/839px，无溢出与 console 报错，`layout_audit` 与 `doctor.py` 均通过。关闭 4.1 的 C1（累计关闭 15 项，其余 11 项），同步 §0/§1/§2.1/4.1/§5/§6/§8/§10 与 README、使用说明。**成果未提交** |
| 2026-10-30 | M12.3 第一子片 C2 上下文投影字段与环形仪表（v1.37）：`app/contracts.py` 新增只读视图 `ContextView(context_used, context_limit, compressed)` 并挂到 `UiProjection.context`，`app/projection.py` 新增单点推导 `build_context_view()`——详情面板"用量：X / Y 字符"与两端仪表同源，预算不可用或尚未构建提示词时返回 `None`（界面显示"待命"而不是 0%，离线演示模式因不走提示词构建所以本就没有数字）。`/api/bootstrap` 新增 `context`；Web 文本胶囊改为 conic-gradient 环（环径按 `--fs-meta` 推导，实测 100%/150% 下 13px/20px，与相邻胶囊同高 20px/29px、不溢出），70%/90% 阈值切 `--warning`/`--danger`，点击仍把详情面板切到"上下文"标签，**删除前端 `contextPercent()` 的文案正则**；Qt 端 `refresh_composer_hint()` 不再直读 `runtime.last_context`，改取同一投影（R22 漂移少一处）。新增 10 个用例（应用层投影 4、Web 适配器 2、静态契约 3、Qt 1），全量 **472 → 482 个通过、1 个 Tk 环境跳过**，`layout_audit` 浏览器回归与 `doctor.py` 均通过。登记 **D59**（派生显示值只放整数与布尔、比值是使用方的展示计算，因 `plain()` 不序列化属性），关闭 4.1 的 C2，同步 §0/§1 阶段表、第 5 节 M12.3 与验收、第 10 节待办、README 与使用说明。**成果未提交** |
| 2026-10-30 | M13 计划按"完全替换"语义修订（v1.36，**仅文档，未改运行代码**）：用户确认桌宠要**完全**换成 Live2D，据此把 v1.35 登记的 M13 重写。三项决定进文档——① **取消回退链**（原"Live2D → 静态头像 → 程序化 SVG"作废；Live2D 不可用即**桌宠功能失效**，浮窗不显示、原因在设置页与 `doctor.py` 可见，不放中性占位；主动与回执消息不随形象失效，改走主窗未读/主动面板与托盘）；② **M12 先全部做完**，顺序改为 M12.3 → M12.4 → M12.5 → M13.1 → M13.2 → M13.3，撤销 v1.35 的交错排法，M12.5 改为"拆分表面与遮罩两个 token 并预留 `--bg-scrim` 缝"以免 M13.2 返工；③ **Web 透明置顶浮窗是桌面形象唯一载体**，登记 M13.3 的删除清单（`ui/qt_app.py:449 AvatarWidget` 程序化绘制与 `:536 AvatarOverlay`、`ui/tray.py:13 build_avatar_icon()`、`ui/theme.py:143 avatar_palette()` 及 `avatar_mood_*` token、`app.js:174` 与 `overlay.js:20` 两份 `avatarSvg`），接入与删除在同一切片完成以避免空窗期；连带如实记录 `--ui qt`/`tk` 从此不提供桌宠、`tests/test_companion_qt_ui.py:1038/1048` 与 `tests/test_companion_ui_theme.py:94-102` 需随删改、基线数会下降。**M13.1 取消 `avatar_enabled` 开关**（素材目录即控制面）、头像位无素材即留空、doctor 在"已开启浮窗却无可用模型"时由 WARN 升为 FAIL。新增 **D58**（载体唯一与失效语义的理由及代价）与 **R25**（WebGL×透明×置顶×点击穿透未经真实验证、WebEngine 缺失即无桌宠、模型体积与同进程 GPU 开销）；修订 R24 末句"失败一律逐级回退"、D56/D57、4.1 C7、M12.4 归属、§0 结论与阶段表、第 6 节 M13 行与顺序约束、第 10 节待办。同步 README、使用说明、技术方案阶段 5 与两份 `data/assets/` 放置说明。**472 个测试基线不变，成果未提交** |
| 2026-10-30 | 角色素材槽位预留与 M13 登记（v1.35，**仅目录与文档，未改运行代码**）：用户已自备头像与 Live2D 模型，为其预留 `data/assets/<persona_id>/`（`avatar.png|webp|jpg`、可选 `background.jpg`、`live2d/model3.json`），目录名与 `data/personas/<id>/` 一一对应，建好 `remielle/live2d/` 骨架与两份放置说明；`.gitignore` 精确放行 `data/assets/README.md` 与 `data/assets/*/README.md`，素材文件与 `live2d/` 子目录整体忽略（`git add -n data/assets` 只加入两份 README），素材不进版本库也不进安装包。新增 **M13 角色素材接入**三切片（M13.1 槽位与静态形象：只读解析 + 扩展名/体积白名单 + token 门控 `GET /api/asset/<slot>` + `ui_state.json` 的 `avatar_enabled` + doctor `Character assets`；M13.2 可替换窗口背景：主窗/设置/管理台 + 主题派生 `--bg-scrim` 自动遮罩，浮窗保持透明；M13.3 Live2D 运行时：先确认 Cubism 授权，失败逐级回退 Live2D → 静态头像 → 程序化 SVG），并据此把顺序细化为 M12.3 → M12.4 → M13.1 → M13.2 → M12.5 → M13.3。**原桌宠形象的纯 CSS 呼吸/眨眼待机动画取消**（D56 修订、M12.4 与 4.1 C7 同步收窄为"只抽 `avatar.js` 挂载点"）。记录 D57（素材按人设分目录、只读不写、整体忽略、背景只作用三窗且遮罩不暴露调参）与 R24（`/api/asset` 读取面、素材体积与备份边界、Cubism 再分发许可）。更新 §0/§1 阶段 5 行/§5/§6/§8/§10 与 README、使用说明。**472 个测试基线不变，成果未提交** |
| 2026-10-29 | M12.2 排版层级（v1.34，U1/U2/U4/U5）：`web/assets/tokens.css` 与 `ui/theme.py` 收敛为同一套 7 档字阶（caption 11 / meta 12 / ui 13 / body 14 / large 16 / subtitle 18 / display 24，全部 `calc(Npx * var(--scale))`），正文整档上调并把空状态标题、人设名、窗口标题、情绪标题接上顶部阶梯；`app/settings/console/overlay.css` 按角色重新分档，行高收敛为 `--lh-control/meta/body` 三档并清零裸 `line-height`（图标字形 `+` 例外，`--lh-control` 会裁掉 2px 墨迹）；阅读列从固定 780px 改为 `--read-measure = --fs-body × 42` 与 `--col-measure = read-measure / 0.74`，页面样式表零 `max-width: calc(Npx`——计划原写 `rem`，实测不可用（无 `html { font-size }`，`rem` 只解析到 UA 16px 且不随 `--scale` 变）；`--ctl-*` 随新字阶重导（`--ctl-bar` 按控件标签而非页面标题，开关改 `--ctl-switch-*`），新增 `body.compact` 在 200% 下退化窗口操作标签。Qt 端同步：`ui/theme.py` 41 处 QSS 字号字面量改走 `_type()`（保留 `stylesheet()` 后置 `re.sub` 缩放通道）、`SPACE_TOKENS` 补 `xl: 18`、`ui/markdown.py` 标题按 level 取 large/body、`ui/qt_app.py` 浮窗气泡改 `font_px("ui", scale)`。`web/layout_audit.py` 探针新增每行字符容量与顶栏/聊天区高度上报，实测 100/125/150/200% 下 1000×700 每行 23/36/33/24 字、780×560 每行 37/30/25/18 字，20 组合零裁切零溢出；`tests/test_companion_web_style.py` +7 排版契约、`tests/test_companion_web_layout.py` +2 度量断言并把图标退化判据改为"原始宽度与字号归一宽度都要够"，全量 463 → 472 个通过、1 个 Tk 环境跳过；更新 §0/§1/§2.1/§3.3/4.1/§5/§6/§8（R21 代码侧残留清零、R22 收窄）/§10 与 README、使用说明。**成果未提交** |
| 2026-10-29 | M12.1 缩放正确性与视觉 token（v1.33）：`web/assets/tokens.css` 移植 Qt `ui/theme.py` 的间距契约并新增 `--sp-*`（按 `--sp-scale = 0.5 + scale×0.5` 半速跟随 `--scale`）、`--ctl-*`（由 `--fs-*` 推导的控件度量）、`--font-mono`、`--lh-*`、`--fw-*`、`--radius-pill/card`；`app/settings/console/overlay.css` 去掉全部固定 `height`（表格行保留 `height`，单元格上它即最小值）与裸像素间距，`.send` 按 `em` 取最小宽度，`.inspector-tabs` 与管理台 `.tabs` 改横向滚动并把下划线换成 `inset` 阴影以免被滚动容器裁掉，`@media` 不再 `display:none` 窗口按钮而是退化为图标；`app.js` 新增 `responsiveWidth()` 让 860/940/780 断点按字号归一化，会话行改语义 `<button>` 并补 `aria-current/pressed/selected`、chat `role=log` 与流式 `aria-live`，四个页面补 `role=tablist/tab/tabpanel` 与 `aria-label`，删除全部 `outline:none` 使 `:focus-visible` 焦点环生效，管理台表格行支持 Enter/Space；`/api/overlay` 载荷新增 `font_scale`（`overlay.js` 应用），`.pet` 容器化并让形象 SVG `display:block` 消除基线溢出。新增 `web/layout_audit.py`（Playwright 真实 Chromium，100/125/150/200% × 主界面 1000×700 与 780×560、设置、管理台、浮窗共 20 组合逐页截图并扫描溢出/超出视口/命中区不足）与 `tests/test_companion_web_layout.py` 2 个回归用例、`tests/test_companion_web_style.py` 17 个静态断言、`tests/test_companion_web.py` 浮窗载荷断言 1 个，全量 443 → 463 个通过、1 个 Tk 环境跳过；`doctor.py` 输出 `Environment check passed.`；更新 §0/§1/§2.1/§3.3/4.1/§5/§6/§8（R21 关闭）/§10 与 README、使用说明。**成果未提交** |
| 2026-10-29 | M12 计划登记（v1.32，仅文档）：对照五个参考项目的界面层审计 `web/assets/`，新增 4.1 UI 层对比结论，登记 26 项差距（U1–U16 视觉/排布、C1–C10 消息表达/浮窗），每项附证据文件与行号；新增第 5 节 M12 与五个切片（缩放正确性与视觉 token → 排版层级 → 消息与运行表达 → 桌面浮窗能力 → 视觉纵深与密度）及其出口条件；第 6 节增 M12 行与"M12 先于阶段 5"的顺序约束；记录 D55（token + `min-height` 修复缩放、投影字段不得由前端正则解析）与 D56（不引入 Tailwind/React/Electron/构建链），新增 R21（150/200% 字体缩放与 780px 下的裁切/溢出/丢窗口按钮）、R22（Qt 与 Web 两套 Markdown 子集与 token 漂移）、R23（浮窗右键被吞、气泡无队列、8 秒轮询）；更新 §0 结论、§1 阶段 1 缺口与 §3.3/§10 待办排序。同步修正使用说明中"`SPACE_TOKENS` 两端通用"的漂移表述。**未改动任何代码，443 个测试基线不变，成果未提交** |
| 2026-10-29 | M11.1 单实例唤回修复（v1.31）：修复“关闭主窗口后再次启动打不开”——关闭只是隐藏到托盘，进程仍持有数据目录单实例锁，`pythonw` 下第二个实例的提示写入不可见的 stderr 后以 3 退出。新增 `agent_framework/companion/activation.py`（`data/.companion.endpoint` 端口/令牌落盘与清理、`activate()` 唤回、Windows `EnumWindows` 按 PID 显示非工具窗口兜底）；`web/server.py` 增加 `POST /api/activate`（同令牌校验、`port` 属性、`set_activate_handler`），`web/shell.py` 用 `QObject.Signal` + `Qt.QueuedConnection` 在 GUI 线程 `show_main()`，并在首次隐藏时发托盘提示、托盘不可用时关闭即退出；`companion.py` 抢锁失败改为先尝试唤回（成功返回 0），失败才提示（`pythonw` 用 `MessageBoxW`）返回 3；新增 4 个适配器测试，全量 439 → 443 个通过、1 个 Tk 环境跳过；更新使用说明的关闭窗口与单实例章节；成果未提交 |
| 2026-10-29 | M11 Pixso Web UI 迁移（v1.30）：以 Pixso 变量/样式承接 `theme.py` token，重建深色主界面、珍珠白空状态、设置、管理台、桌面浮窗与 780×560 窄窗 6 个画板；新增 `agent_framework/companion/web/`（`server.py` 回环 HTTP + SSE 适配器、令牌/路径守卫、`/` `/settings` `/console` `/overlay` 与 `/api/*` 命令查询；`assets/` 为 Pixso 派生前端；`shell.py` 为 QtWebEngine 无边框壳层、子窗口、透明置顶浮窗与 QWebChannel 宿主桥）；`companion.py` 默认 `--ui web` 并新增 `--ui browser`，Qt/Tk 保留兼容；Windows 设置 `QTWEBENGINE_DISABLE_SANDBOX=1`，`qwebchannel.js` 随包提供以避免 http 页面无法加载 qrc；新增 `tests/test_companion_web.py` 8 个用例（页面与令牌、`/api/ui` 持久化/广播、7 页管理台、中文情绪纠正、SSE 回合落库、资源与路径逃逸、管理台记忆清除、工作卡审批/拒绝），全量 431 → 439 个通过、1 个 Tk 环境跳过；记录 D53、R20，更新 R4/R17；成果未提交 |
| 2026-10-29 | M10.3 会话、输入与设置体验（v1.29）：迁移 v9 增加 `sessions.title / pinned_at / unread` 与 `session_drafts`，`store.delete_session()` 级联清理会话数据；左栏按“今天/昨天/最近 7 天/更早”分组，显示相对时间与置顶/未读/当前状态，`⋯`/右键/键盘均可管理，`Ctrl+N` 新建、`Ctrl+K` 搜索、`Ctrl+,` 设置；Composer 2–5 行自适应、按会话草稿、字数提示、模式/工作区/上下文胶囊与单一发送/停止主按钮；陪伴/工作模式化空状态与快捷开场/最近任务；设置中枢改为左分类右内容双栏，支持搜索、状态摘要、渐进展开、字体缩放（100/125/150/200%）与减少动态效果；`theme.py` 新增 `TYPE_TOKENS / SPACE_TOKENS / FONT_SCALES` 契约与统一焦点样式；新增/更新 27 个测试，全量 431 个通过、1 个 Tk 环境跳过；记录 D52；成果未提交 |
| 2026-10-29 | M10.2 审批、证据与上下文抽屉（v1.28）：`file_actions.py` 新增只读 `preview()`/`diff_lines()`（create/update、80 行与 180 字符截断、工作区快照校验）；应用层新增 `ApprovalPreview`，`ApprovalView` 增加 tool/workspace/expired/preview，`confirm_action()` 拒绝过期提案；新增内联 `ApprovalCard`（目标/工作区/模式/风险/有效期/可撤销性/计划/Diff，确认/取消/结果/撤销/失败同一卡闭环）；工具事件补参数摘要、错误码、来源与结果句柄，`ToolCard` 可展开并记忆状态；右栏改为“执行/文件/来源/上下文/任务”标签抽屉，`QSplitter` 宽度写入 `ui_state.json`；940/860px 响应式阈值，窄窗左栏改覆盖抽屉；新增/更新 21 个测试（文件预览 +4、应用层 +4、工具事件 +2、UI 主题 +9、Qt +2），全量 404 个通过、1 个 Tk 环境跳过；记录 D51；后续 M10.3 已完成；成果未提交 |
| 2026-10-28 | M10.1 运行可观察与消息表达（v1.27）：`app/projection.py` 新增应用层单点阶段映射与右栏分节派生，`CompanionApplicationService.ui_projection()` 输出 `TranscriptMessage / RunView / ApprovalView / ProjectionSection`，Qt 聊天与右栏改读投影；`RunContext / ApplicationEvent` 增加 `sequence / stage / detail / tool`；新增输入框上方 `RunBar`（思考/调用工具/生成回答/等待审批/完成/取消/失败、工具摘要、耗时、停止，按 run id 与 sequence 丢弃乱序/迟到事件）；新增安全 Markdown 子集（先转义再渲染、仅 http/https 链接、代码块纯文本、链接点击二次确认）、消息原文复制、日期分隔与角色/时间元信息；流式滚动改为粘底跟随，上滚后显示“有新内容 · 回到最新”；重试/重新生成因无应用层命令而不展示；新增 `tests/test_companion_markdown.py` 8 个，应用层 +3、UI 主题 +12、Qt +2，全量 383 个通过、1 个 Tk 环境跳过；记录 D50；未开始 M10.2/M10.3；成果未提交 |
| 2026-10-28 | M10 桌面 UI 体验优化计划（v1.26）：登记 `M10.1 运行与消息 → M10.2 审批与抽屉 → M10.3 会话与设置` 三个未开始切片；补充应用层 UI 投影、统一运行条、安全 Markdown、滚动跟随、内联审批/Diff/回执、工具与来源展开、标签式可调右栏、窄窗口响应式、会话管理、自适应 Composer、空状态、双栏设置、键盘可访问性与视觉 token 的范围和验收；记录 D49、R18/R19；明确 Qt 优先、Tk/Web/Electron、语音/附件/Live2D 不在本轮，后续控件仅在后端能力接通后出现；本次仅更新计划文档，未改代码、未改变 359 个测试基线 |
| 2026-10-28 | M9 应用层契约与 Qt 适配器（v1.25）：新增 `agent_framework/companion/app/`（contracts/ports/service/bootstrap），建立 `StartTurnCommand / RunContext / ApplicationEvent / ApprovalView / WorkspaceSnapshot`；Qt 的流式启动、模式、工作区、会话、确认、取消与撤销改走 `CompanionApplicationService`，`CompanionWindow(runtime|service)` 与 `launch_qt(runtime|service)` 兼容；`StreamWorker(application, run)` 的停止只发取消信号并继续读到 runtime `done`；run id 作为 generation id，事件带 session/sequence；同会话 active-run 与生成中变更守卫；`done` 前确认 assistant 消息落库，否则失败；run 启动时冻结工作区身份，审批视图暴露 digest/TTL；新增 9 个应用层测试，Qt 26 个通过，全量 359 个通过、1 个 Tk 环境跳过，`compileall` 通过，`PYTHONIOENCODING=utf-8 doctor.py` 输出 `Environment check passed.`（Tk/Tcl 本机告警，Qt 不受影响）；记录 D48、R17；未做 FastAPI/SSE、持久化 run/跨进程和全面板 API 化；成果未提交 |
| 2026-10-28 | M8.5 左栏会话树（v1.24）：左栏改为参考图的“新的任务 / 搜索 / 工作区（目录行 + `…`/`+`）/ 缩进会话列表 / 底部设置”，形象、状态与人设名移到标题栏；新增 `store.list_session_summaries()`（首条用户消息作标题，末条内容/角色/时间与条数作提示）、`runtime.session_summaries()` 与 `runtime.switch_session()`（同步 `proactive_service`/`work` 会话作用域、清空上下文），`ui_state.json` 新增 `active_session` 并在新窗口恢复；`SessionRow` 用 `ElidedLabel` 单行截断并保留完整标题提示，活动行高亮；`新的任务` 在已有消息时创建 `chat_<hex>`、空会话复用，生成中拒绝切换/新建；搜索按标题/末条内容过滤并显示空态；`rail_icon()` 按主题绘制左栏图标；样式表新增 `railAction`/`railSearch`/`workspaceRow`/`railMini`/`sessionRow`/`sessionActive`/`sessionLabel`/`brandSmall` 并移除 `sessionCard`/`nav` 系列；Qt 用例 22→26、store+1、runtime+1、样式/状态+1，全量 350 个通过；记录 D47；成果未提交 |
| 2026-10-28 | M8.5 信息架构调整（v1.23）：左栏只保留形象/状态、工作目录与会话摘要卡；记忆、情绪、人设、主动、任务、管理以及主题、桌面浮窗移入左下角“设置”中枢（新增 `ui/settings.py`），选择入口后设置先关闭再打开侧边面板；会话摘要由最近消息派生话题、条数/最后时间与最新一句，随 `refresh_history()` 更新；`theme.py` 增加 `sessionCard` / `settingsRow` / `settingsButton` / `link` 样式；Qt 用例 20→22，全量 343 个通过；记录 D46；成果未提交 |
| 2026-10-27 | M8.5 复查·提醒能力与窗口层级（v1.22）：`reminders.py` 时间解析补齐秒、“半小时”、中文多位数字与“今天/明天/后天 + 凌晨…夜里”的钟点（已过今日钟点顺延次日，ISO 字面量优先），抽出 `resolve_when()`；新增低风险 `set_reminder` 工具（四种模式可用、复用 `resolve_when`、时间识别失败返回原因不落库），本地快路径与模型工具共用同一套解析；真实模型 `sensenova-6.8-flash-lite` 冒烟：本地规则不命中的“20 分钟之后我可能有空，到时候提醒我拿快递”由模型调用工具落库（`拿快递 / pending / +20 分钟`），没有时间可推断的说法模型先追问、不落库；主窗口与 `open_side_dialog()` 面板去掉 `WindowStaysOnTopHint`（Tk `-topmost False`），置顶只留给桌面形象浮窗；新增 4 个测试，全量 341 个通过；真机确认主窗口 `WS_EX_STYLE=0x0` 且保留最小化框；记录 D45；成果未提交 |
| 2026-10-27 | M8.5 复查·窗口找回（v1.21）：主窗口去掉 `Qt.Tool`，改带 `Qt.WindowMinimizeButtonHint` 与头像窗口图标，顶部“收起”（`hide()` 进托盘，托盘缺失/折叠时找不回窗口）改为“最小化”走 `showMinimized()`；`changeEvent` 还原时回填最小化前几何，`toggle_window()`/托盘菜单区分隐藏·最小化·正常三态（新增“还原主窗口”）；`save_window_geometry()` 跳过最小态；Qt 用例 19→20，全量 337 个通过；记录 D44、更新 R14；成果未提交 |
| 2026-10-27 | M8.5 桌面界面改版·方案 A（v1.20）：新增 `ui/theme.py`（经典深色/珍珠白 token、QSS 构建器、头像配色派生）、`ui/chat_view.py`（气泡、工具回执卡三态、流式光标、签名增量重建、内容驱动宽度）、`ui/inspector.py`（右栏只读详情，全部派生自运行时状态）；`CompanionWindow` 重写为“左栏 / 顶部模式切换 / 中聊天 / 右可折叠面板”，`ui_state.json` 增 `theme`/`rail_visible`/`inspector_visible`/`window_geometry`；侧边对话框定位抽成纯函数 `side_dialog_geometry()`；气泡高度改按字体度量测量以消除换行留白；Qt 用例 16→19 并新增 `tests/test_companion_ui_theme.py` 21 个，全量 336 个通过；记录 D43；成果未提交 |
| 2026-10-27 | M8 管理与自检台（v1.19）：新增 `maintenance.py`（在线备份/manifest 校验/恢复与恢复前快照、zip 导出、记忆树/会话/全量彻底删除、完整性报告、单实例锁）与 `redaction.py`；旧库迁移前自动快照；`store.py` 增加管理查询并统一脱敏审计/回执；Qt 新增 7 页管理台（主窗口与托盘入口）；`companion.py` 默认单实例；`doctor.py` 增加 schema/索引/FTS/备份/锁检查；新增 18 个维护测试与 3 个离屏管理台用例，全量 312 个通过；记录 D40–D42 与 R16，更新 R4/R8/R13；修复管理按钮挤压窗口最小宽度导致的 Qt 几何回归 |
| 2026-10-27 | M7 技能包（v1.18）：新增 `skills/` 包与严格 `SKILL.md` frontmatter（name/description/tools/effectKind/modes/references）；按 effectKind 与工具风险推导模式，非法/越权技能加载时拒绝；`invoke_skill` 按需加载正文，`read_skill_reference` 分段读取声明文件（单次 ≤ 8000 字符）；技能加载后本轮工具范围收窄为声明工具 + 元工具；迁移 v8 `skill_audit` 记录调用/读取/拒绝；`doctor.py` 新增 `Skills`；新增 20 个测试，全量 291 个通过；真实库迁移到 v8；记录 D37–D39 与 R15 |
| 2026-10-27 | M6 桌面形象与流式交互（v1.17）：`stream_chat` / `StreamEvent`（OpenAI 流式与工具增量）、运行时 `delta/reset/tool/done/error` 与 generation id、停止与“（已停止生成）”、跨分片控制标记过滤；Qt 透明置顶浮窗、默认点击穿透与命中检测、`ui_state.json` 位置记忆与多显示器夹取、`QSystemTrayIcon` 托盘、`desktop_status()` 回执气泡；新增 24 个流式/状态单元测试与 7 个 Qt 用例，全量 271 个通过；`doctor.py` 全项 OK；记录 D33–D36 与 R14 |
| 2026-10-27 | M5 工作任务卡（v1.16）：迁移 v7 `work_tasks` / `work_receipts`；新增 `work/` 包实现规则优先意图路由（注入守卫、12 秒上限、模型分类降级）、任务卡六要素、计划状态机、审批 TTL 与修改/拒绝、转发回执 `unknown` 终态与启动恢复；运行时只对外部副作用请求自动建卡；Qt/Tk 新增“任务”面板；`doctor.py` 新增 `Work state`；新增 37 个任务卡测试与 1 个 Qt 面板测试，全量 240 个通过；真实库迁移到 v7；记录 D29–D32 |
| 2026-10-27 | M4 主体（v1.15）：迁移 v6 `proactive_state` / `proactive_events`；`ProactiveService` 实现状态持久化、每日/未回复计数、送达与错过；半衰期反重复（话题 24h / 记忆 72h，提醒豁免）与内容/URL 哈希；记忆来源 opt-in 与本地模板生成；事件触发原因、未读与部分已读；Qt 60 秒轮询与“主动”面板、Tk 入口；`doctor.py` 新增 `Proactive state`；新增 28 个服务/单元测试与 1 个 Qt 面板测试，全量 202 个通过；真实库迁移到 v6；记录 D25–D28 |
| 2026-10-23 | 创建进度文档；完成五个参考项目调研整合；确定 M1–M8 路线图；复核 34 个测试与数据库状态 |
| 2026-10-24 | 初始化 git 并提交基线；完成 M1a：执行账本、提案工作区快照/TTL、确认时模式与工作区复核、`unknown` 与崩溃恢复、快照撤销、UI 模式同步；新增 8 个回归测试，42 个测试通过；M1 拆分为 M1a/M1b |
| 2026-10-24 | 完成 Qt 自动化端到端（搜索 → 确认 → 写入 → 撤销），修复撤销按钮不可见；完成 M1b 核心：模式化工具注册表、原生工具循环（4 调用/3 轮）、写入只生成提案、工具失败回填、显式搜索正则快路径；新增 10 个测试，全量 53 个通过 |
| 2026-10-24 | 完成 M1b 收尾：工具级超时（搜索 30s / 写入 10s / 时间 5s）、function calling 首次失败自动降级、非工具错误不误判；新增 4 个测试，全量 57 个通过；下一阶段进入 M2 |
| 2026-10-24 | 完成 M2 第一批：`user_version` 迁移；`memories` 事实表扩展；证据、候选、事件、召回日志、墓碑表；仅用户原文可作证据；规范键与冲突取代；纠正后旧值不召回；遗忘墓碑阻止复活；新增 9 个测试，全量 66 个通过 |
| 2026-10-26 | 提交积压成果（`f6f4647`/`b6bdbe9`）；删除 `$null`；完成 M2 第二批第一切片：`textseg.py` 中文 2/3-gram 预分词、`memory_fts` FTS5 虚表与 BM25 候选召回、迁移 v2、纠正重索引、损坏降级与启动自愈、`recall_log.source`、`doctor.py` 数据库自检；新增 14 个测试，全量 80 个通过；记录 DSH 沙箱 tempfile 限制（决策 D12/D13） |
| 2026-10-26 | 完成 M2 第二批：迁移 v3（派生关系、经历状态、导入账本）、跨类型遗忘递归传播、可暂停续跑/跨副本去重/成本预估的旧聊天导入、证据与召回台 UI、protected 人设入口；新增 13 个测试，全量 93 个通过；M2 完成（决策 D14/D15） |
| 2026-10-26 | M2 收尾加固：角色扮演标记/引用台词/假设句不进入事实抽取；导入按会话标题与开始/结束标记维护状态；新增 5 个测试，全量 98 个通过（6 个环境跳过）；计划新增 M2.5 人设层，记录 D16/D17 与 R9–R11 |
| 2026-10-26 | M2.5 第一切片：新增本地 persona pack（默认蕾米埃尔·丹）、`persona.py` 稳定前缀/SHA-256、系统提示分层、Qt/Tk 人设名与离线语气、`--persona`；角色资料不入 `memories`；新增 11 个测试，全量 109 个通过；记录 D18 与 R12，更新 R10 |
| 2026-10-26 | M2.5 完成：`worldbook.md` 触发式检索、persona pack 来源/许可/大小/版本预览与安全导入（只复制文本、显式覆盖、坏包拒绝）、keyed protected 称呼/关系/边界与 Qt/Tk 人设面板；新增 15 个测试，全量 124 个通过；记录 D19，更新 R10/R12，下一阶段进入 M3 |
| 2026-10-26 | M3 第一切片：新增 `context.py` 分层提示与稳定指纹；字符预算窗口保留最近完整消息，82% 触发抽取式摘要并写入 `context_checkpoints`（迁移 v4）；工具 Schema 排序确认；召回与用户设定加字符上限；修复 `test_undo_restores_previous_content` 的跨秒时间戳抖动；新增 12 个测试，全量 136 个通过；记录 D20 |
| 2026-10-26 | M3 完成：新增 `tool_results.py`，工具输出超过 16KB（UTF-8）落盘并只回预览与 `result_id`；新增 `read_tool_result` 分段回读（句柄校验、会话隔离、重启可读）；稳定层/落盘通知/回读头部统一“未回读不算已读”；存储不可用时降级仅预览；新增 9 个测试，全量 145 个通过；记录 D21 与 R13 |
| 2026-10-26 | M3.5 代码收尾：压缩轮次召回只记 1 条；`recall_log.used_in_reply` 按回复采纳情况接线；单字中文查询绕过 FTS 走关键词兜底并支持单字子串评分；新增 3 个测试，全量 148 个通过；记录 D22，更新 R2 |
| 2026-10-27 | M3.5 评测与冒烟（v1.13）：新增 `eval_recall.py` 26 条中文查询评测集，基线 hit@1/hit@5/MRR 均为 1.0；真实模型 sensenova-6.8-flash-lite 冒烟通过 function calling 与大结果落盘回读；修复 M3R-8（工具轮次用满后最后一轮结果被丢弃，改为末轮后追加一次无工具收尾请求）；新增 3 个测试，全量 151 个通过（8 个环境跳过）；测试命令更正为 `discover -s tests -t tests` |
| 2026-10-27 | M4.0 主动底座（v1.14）：迁移 v5 `reminders` 表与创建/到点/完成/忽略闭环、自然语言相对时间提醒入口；未完成话题从消息派生；`ctypes` 活动状态机与 `doctor.py` Activity probe；纯函数门控（Quiet/忙/免打扰/每日/间隔/未回复/活动隐私）；空来源 `no_source` 安全退出；新增 22 个测试，全量 173 个通过（3.12.6 无跳过）；真实库迁移到 v5；记录 D23/D24，更新 M3R-4/M3R-6 |
| 2026-10-26 | M3 复查（v1.11）：重跑 145 个测试与 `doctor.py`，确认真实库 v4/FTS ready；记录 M3R-1~7（压缩轮次重复召回、`used_in_reply` 未接线、单字中文查询不召回、M4 来源无底座、真实模型未冒烟、`psutil` 未装、单实例锁）；路线图调整为 M3.5 → M4.0 → M4 主体；M3 成果仍未提交 |
