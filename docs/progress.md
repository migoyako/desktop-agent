# 项目进度与路线图

> 版本：v1.19
> 更新日期：2026-10-27
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
- 测试基线 34 → 66 → 80 → 93 → 98 → 109 → 124 → 136 → 145 → 148 → 151 → 173 → 202 → 240 → 271 → 291 → **312**（M1a +8、Qt 端到端 +1、M1b +14、M2 第一批 +9、M2 第二批 FTS 切片 +14、M2 第二批剩余 +12、Qt 证据台 +1、M2 收尾角色扮演/导入防护 +5、M2.5 第一切片 +11、M2.5 导入/世界书/设定 +15、M3 分层/预算 +12、M3 工具结果落盘/回读 +9、M3.5 复查修复 +3、M3.5 召回评测 +2、工具轮次收尾 +1、M4.0 主动底座 +22、M4 主体 +29、M5 任务卡 +37、Qt 任务面板 +1、M6 流式/浮窗/托盘/状态 +31、M7 技能包 +20、M8 维护/管理台 +21）。
- 下一阶段顺序调整为：**M4.0 主动底座 → M4 主体 → M5 任务卡 → M6 桌面形象与流式 → M7 技能包 → M8 管理与自检台（均已完成）**；接下来进入语音/Live2D/多角色与发布准备，M1b 的逻辑调用 ID、更多确认工具与 OpenClaw 复用随后续模块顺带完成。
- M3、M3.5、M4、M5、M6、M7 与 M8 已完成；语音、Live2D、多角色和社交渠道仍排在 M8 之后。

完成度是工程估算，用于判断趋势，不作为验收标准；验收以第 7 节指标和第 5 节各模块验收条件为准。

## 1. 阶段完成度

| 阶段 | 完成度 | 状态 | 已有能力 | 主要缺口 |
|---|---:|---|---|---|
| 0 架构基线 | 70% | 部分完成 | 四种模式、风险枚举、动作提案、SQLite 持久化、策略引擎 | 版本化迁移、系统钥匙串、统一工具循环 |
| 1 桌面形象 + 聊天 | 90% | 部分完成 | Qt/Tk 窗口、聊天、历史会话、确认卡、后台线程；M6 流式与停止、透明置顶浮窗、点击穿透、位置记忆、托盘、执行器回执状态气泡 | 形象素材与语音、Live2D 尚未接入 |
| 2 第一个真实任务 | 99% | 部分完成 | 搜索 → 笔记提案 → 确认 → 写入 → 回执 → 撤销；工作区快照、模式复核、`unknown`、幂等、崩溃恢复、快照撤销；按模式装配的工具注册表、LLM 原生工具循环、单工具超时与 function calling 降级；M5 工作任务卡、意图路由、计划状态机、审批与转发回执、Qt/Tk 任务面板；M7 本地技能包、元工具、模式/风险过滤、本轮工具范围约束与审计 | 逻辑调用 ID 与结果缓存、更多确认工具、多执行器路由、OpenClaw 复用同一内核 |
| 3 长期记忆 | 95% | M2 与 M2.5 已完成 | 用户原文证据、规范键、候选评分与晋升、冲突取代、纠正、遗忘墓碑、同键与派生级联、召回日志、迁移 v3；中文 2/3-gram + FTS5/BM25 召回（关键词降级、启动自愈、召回来源）；可暂停续跑的旧聊天导入与跨副本去重；证据/召回台；protected 人设入口；角色扮演/引用台词防污染；情绪快照；本地 persona pack、稳定前缀哈希、导入校验、世界书按需注入 | 向量/多模态召回、更多导出格式识别 |
| 4 情绪、人格与主动 | 75% | M4 已完成 | 情绪估计、用户纠正、历史快照；默认蕾米埃尔人设包、稳定人格/背景分层、离线角色语气；称呼/关系/边界编辑、人设包导入与来源展示；M4.0 提醒闭环、未完成话题、活动状态、纯函数门控；M4 主体状态持久化、反重复、错过未读、触发原因落库、Qt/Tk 主动面板与 60 秒轮询 | 更多主动来源与打扰阈值调优、更多人格模板 |
| 5 语音、Live2D 与发布 | 0% | 未开始 | 可选依赖已安装 | 全部 |

## 2. 已验证的现状

### 2.1 测试与环境

- 2026-10-27 运行 `.venv/Scripts/python.exe -m unittest discover -s tests -t tests`：**312 个测试通过，27.01 秒，0 跳过**（系统 Python 3.8.8 无 PySide6 且 Tcl 版本不匹配时跳过 8 个 Qt 测试与 1 个 Tk 测试；`tests/` 无 `__init__.py`，必须显式指定 `-t tests`）。
- 2026-10-27 M8 验证：新增 `tests/test_companion_maintenance.py`（18 个）与 `tests/test_companion_console.py`（3 个）；`doctor.py` 全项 OK（数据库 v8、FTS ready、无缺失索引、实例锁空闲；无备份时 `Backups` 为 WARN，首次迁移或手动备份后转为 OK）；离屏管理台 7 页均可加载并显示记忆/召回/失败记录，技能审计密文已在落库前脱敏，设置页可完成备份与导出。
- 2026-10-27 M7 验证：新增 `tests/test_companion_skills.py`（20 个）；`doctor.py` 全项 OK（数据库已迁移到 v8、FTS ready；`Skills` 无技能目录时提示可选）；真实库 `data/companion.db` 迁移后 14 条消息保留。
- 2026-10-27 M6 验证：新增 `tests/test_companion_streaming.py`（24 个）与 Qt 流式/浮窗/托盘 7 个用例；`doctor.py` 全项 OK（数据库仍为 v7、FTS ready；`Proactive state` 与 `Work state` 正常）；Qt 16 个界面测试全部通过（离屏平台）。
- 2026-10-27 M5 验证：`doctor.py` 全项 OK（新增 `Work state` 检查），真实库 `data/companion.db` 已迁移到 `user_version=7` 且 12 条消息保留；`work_tasks` / `work_receipts` 表已创建且为空。
- 2026-10-27 M4 主体验证：`doctor.py` 全项 OK（新增 `Proactive state` 检查），真实库 `data/companion.db` 已迁移到 `user_version=6` 且 12 条消息保留；`proactive_state` / `proactive_events` 表已创建且为空。
- 2026-10-27 M4.0 验证：`doctor.py` 全项 OK（含新增 `Activity probe`），真实库 `data/companion.db` 已迁移到 `user_version=5` 且 12 条消息保留；系统 Python 3.8.8 下当时 173 个测试同样通过（8 个 Qt 与 1 个 Tk 测试跳过）。
- 2026-10-27 召回评测：`python -m agent_framework.companion.eval_recall` 输出 `cases=26 limit=5 hit@1=1.0000 hit@5=1.0000 mrr=1.0000`。
- 2026-10-26 复查重跑：**145 个测试通过，14.68 秒**；`doctor.py` 全项 OK（数据库 `user_version=4`、FTS `ready`）；与本文档一致。随后 M3.5 修复 3 个代码项，全量升至 148 个。
- 基线为 2026-10-23 的 34 个测试；M1a 新增 8 个执行安全回归测试，Qt 端到端新增 1 个，M1b 新增 14 个工具循环测试，M2 第一批新增 9 个记忆生命周期测试，M2 第二批 FTS 切片新增 14 个（`tests/test_companion_memory_fts.py`），M2 第二批剩余新增 12 个（`tests/test_companion_memory_batch2.py`：派生级联、protected、证据台、导入、摘要墓碑），Qt 证据台新增 1 个，M2 收尾新增 5 个角色扮演/导入防护测试，M2.5 第一切片新增 11 个（`tests/test_companion_persona.py`：稳定哈希、路径校验、提示分层、隔离、删除 persona 不影响记忆），M2.5 第二切片新增 15 个（导入/预览/覆盖/大小/世界书 9 个，运行时设定/世界书/导入 4 个，Qt 人设面板 2 个），M3 第一切片新增 12 个（`tests/test_companion_context.py`：稳定哈希跨模式一致、分层顺序、工具排序、预算窗口、抽取式摘要、checkpoint 复用、迁移 v4），M3 工具结果守卫新增 9 个（`tests/test_companion_tool_results.py`：16KB 落盘、UTF-8 字节阈值、预览不含尾部、分段回读、会话隔离、越界/坏句柄拒绝、重启后可读、未回读提示），M3.5 新增 3 个（压缩轮次只记 1 条召回、回复引用记忆时 `used_in_reply=1`、中文单字查询走关键词兜底），M3.5 召回评测新增 2 个（用例形状覆盖、hit@5/MRR 基线），工具轮次收尾新增 1 个（末轮工具后仍能拿到最终回答，另拆出无回答时的兜底用例），M4.0 新增 22 个（`tests/test_companion_proactive.py`：提醒到点/完成/忽略、相对时间解析、未完成话题的未回复/失败回复/过期排除、活动状态分类、门控六条规则、空来源不发言、提醒与话题来源可追溯、运行时提醒入口与迁移 v4→v5），M4 主体新增 28 个服务/单元测试与 1 个 Qt 面板测试（`tests/test_companion_proactive_service.py`：设置持久化与校验、送达/宽限/错过、暂停不消费、跨天重置、未回复清零、各门控分支、半衰期与 URL 去重、提醒豁免、从重复回退、记忆来源 opt-in、事件未读/部分已读、运行时 API 往返），M5 新增 37 个（`tests/test_companion_work.py`：路由规则/注入/超时、任务卡与只追问一次、审批安全/超时/修改/拒绝、回执 unknown 终态、崩溃恢复、运行时 API）与 1 个 Qt 任务面板测试，M6 新增 31 个（`tests/test_companion_streaming.py`：标记过滤/跨分片、流式事件与 generation、工具事件、停止与迟到分片、错误事件、无流式降级、离线与搜索分支、`ui_state.json` 往返与损坏容错、位置夹取五类、桌面状态来源与文案；Qt 侧：透明置顶/点击穿透/命中检测、位置记忆与重启恢复、托盘不可用守卫与菜单同步、停止后不再追加、旧 generation 忽略），M7 新增 20 个（`tests/test_companion_skills.py`：frontmatter inline/块列表、字段/名称/effectKind/正文与参考大小校验、风险推导模式、未知工具/风险越界/模式冲突/参考缺失拒绝、参考文件分段与越界拒绝、无代码发现技能、模式过滤、元工具返回与审计、未知技能拒绝、本轮工具范围收窄与下一轮恢复、审计跨重启保留、迁移 v7→v8），M8 新增 18 个维护测试（`tests/test_companion_maintenance.py`：完整性报告/缺失索引、备份清单与校验、损坏/更新版本拒绝、恢复与恢复前安全备份、导出 zip 内容与排除密钥、按记忆树/会话/全量删除、迁移前快照、单实例锁同进程重入与跨进程互斥、脱敏、管理查询）与 3 个离屏管理台用例（`tests/test_companion_console.py`：7 页与记录、执行审计脱敏、设置页备份/导出/重启提示）。
- `.venv` 使用 **Python 3.12.6**；PySide6 6.12.0、openai 3.37.0 已安装；SQLite 3.45.3（含 FTS5/BM25）。
- `requirements-optional.txt` 中的 PyAutoGUI、pywinauto、pygame、Pillow、Playwright 已安装，但尚未接入执行器。
- 测试覆盖：Agent 冒烟、OpenAI 兼容服务、模式策略、文件动作、存储、运行时、失败路径、执行安全、工具循环（含末轮工具后的收尾回答）、Qt/Tk UI、FTS5 召回与降级、召回评测基线、派生遗忘传播、旧聊天导入与去重、证据台数据、角色扮演与引用台词防污染、persona pack 稳定哈希/导入校验/世界书检索与角色/用户事实隔离、分层上下文/预算/摘要 checkpoint、大工具结果落盘与分段回读、主动状态持久化/门控/反重复/错过未读/事件触发原因与 Qt 主动面板、工作任务卡/计划状态机/审批安全/回执终态/崩溃恢复与 Qt 任务面板、流式事件与停止/迟到分片、控制标记跨分片过滤、桌面浮窗点击穿透与位置记忆、托盘守卫与菜单同步、执行器回执状态气泡、技能清单校验/模式风险过滤/参考文件分段/本轮工具范围与审计、备份校验/恢复/导出/彻底删除/迁移前快照/单实例锁/日志脱敏与 Qt 管理台。
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
| 提案 → 确认 → 执行 → 回执 → 撤销 | `agent_framework/companion/runtime.py`、`file_actions.py`、`store.py` | `test_companion_runtime.py`、`test_companion_file_actions.py` |
| 执行账本、幂等确认、`unknown` 与崩溃恢复 | `store.py`（`execution_ledger`、`claim_execution`、`recover_incomplete_executions`）、`runtime.py` | `test_companion_execution.py` |
| 提案工作区快照、TTL 与确认复核 | `file_actions.py`、`runtime.py` | `test_companion_execution.py` |
| 工作区守卫、路径逃逸与链接检查 | `agent_framework/companion/file_actions.py` | `test_companion_file_actions.py` |
| 搜索（Bing RSS / DuckDuckGo HTML） | `agent_framework/companion/search.py` | `test_companion_runtime.py` |
| 本地记忆与情绪 | `agent_framework/companion/memory.py`、`affect.py` | `test_companion_store.py`、`test_companion_runtime.py` |
| Qt 桌面界面 | `agent_framework/companion/ui/qt_app.py` | `test_companion_qt_ui.py` |
| Tk 兼容界面 | `agent_framework/companion/ui/tk_app.py` | `test_companion_ui.py` |
| 旧版 ChatAgent 与工具列表 | `agent_framework/agent.py`、`tools.py`、`llm.py` | `test_agent.py` |
| OpenClaw / 微信桥 | `agent_framework/service.py`、`agent_service.py` | `test_service.py` |
| 失败路径 | 运行时与文件动作 | `test_companion_failures.py` |

### 2.3 真实数据状态

2026-10-27 检查 `data/companion.db`：

| 表 | 行数 |
|---|---:|
| sessions | 1 |
| messages | 12 |
| affect_states | 13 |
| action_proposals | 0 |
| action_receipts | 0 |
| execution_ledger | 0 |
| memories | 0 |
| memory_evidence / candidates / events / tombstones | 0 |
| memory_relations | 0 |
| recall_log | 2 |
| context_checkpoints | 0 |
| experiences | 0 |
| import_jobs / import_messages | 0 |
| workspace_grants | 1 |
| reminders | 0 |
| proactive_state / proactive_events | 0 / 0 |
| work_tasks / work_receipts | 0 / 0 |

`PRAGMA user_version = 7`、FTS 状态 `ready`（2026-10-27 复查，`doctor.py` 确认）；v3（派生关系、经历状态、导入账本）、v4（`context_checkpoints`）、v5（`reminders`）、v6（`proactive_state` / `proactive_events`）与 v7（`work_tasks` / `work_receipts`）迁移已在真实库完成，12 条消息保留。FTS 索引随写入自动维护，启动时自愈。

结论：真实 UI 端到端已由 Qt 自动化测试覆盖（临时数据库），但 `data/companion.db` 仍只验证过聊天与情绪路径，**搜索 → 确认 → 写入 → 回执 → 撤销尚未在真实用户数据中留下记录**。端到端能力已可回归，真实使用数据仍待日常运行积累。

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

顺序约束：

- M1a 已完成，M1b 核心已完成；M1b 收尾项必须先于 M5：任务卡依赖统一工具循环与回执状态。
- M2 必须先于 M4：主动发言的来源必须是可追溯的活跃记忆。
- **M3 复查结论（2026-10-26）：M3.5 必须先于 M4**——压缩轮次重复召回会污染 M4 的来源判断（“活跃记忆事件”本身来自召回日志），且 M4 的提醒/未完成话题来源在 M4.0 补齐前没有数据可读。
- M2.5 已完成：稳定前缀、persona pack 与导入/设定/世界书边界已确定；M3 可直接复用 `persona.py` 的稳定前缀与哈希。
- M6 已完成：状态气泡建立在 M1 的提案/回执与 M5 的任务回执之上，不再依赖模型自述。
- M7 已完成：技能只复用 M1 的工具注册表与策略，不新增执行通道；技能声明工具必须通过同一模式/风险判定，元工具本身为 `safe`。
- 阶段 5（语音/Live2D）排在 M1–M4 之后。

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

## 9. 风险

| 编号 | 风险 | 应对 |
|---|---|---|
| R1 | Windows 文件身份不稳定 | 已用 `realpath + st_dev/st_ino` 并执行前复核；部分文件系统 `st_ino` 可能为 0，M1b 前补 `GetFileInformationByHandle` 卷序列号/文件索引 |
| R2 | 中文 FTS5 分词效果差 | 已落地：`textseg.py` 中文 2/3-gram 预分词写入 FTS5，BM25 只做候选；关键词召回保留为降级路径。单字中文查询（如“茶”）现绕过 FTS 走关键词兜底，命中该字所在记忆；剩余边界：单字查询会退化为最多 200 条记忆的线性扫描，分词器升级后可再优化 |
| R3 | 模型不支持 function calling | M1b 已接入自动降级：首次工具调用失败后切换无工具对话；显式搜索仍走正则快路径 |
| R4 | 没有 git 历史，大改难回滚 | 已缓解：基线 `39151a2`，M1a/M1b/M2 第一批 `f6f4647`/`b6bdbe9`，FTS 切片 `f0ffa0a`，M2.5 `49f9fc6`，M3/M3.5 `23c7837`，M4/M5 `665d706`，M6 `1e3de1f`；M7 与 M8 尚未提交（等待用户确认）。未推送远端，仍无异地备份 |
| R5 | 文档漂移 | 每个模块完成时同步 README 与本文档状态 |
| R6 | 范围蔓延到语音/Live2D/社交 | 以 M1/M2 出口条件为闸门，未通过不启动 |
| R7 | 单元测试通过但真实 UI 未跑通 | 已增加 Qt 自动化端到端“搜索 → 确认 → 写入 → 撤销”；Tk 与真实模型仍需人工验证 |
| R8 | 两个实例同时使用同一 `data` 目录时，启动恢复可能把另一实例的在途执行误标为 `unknown` | 已缓解（2026-10-27）：启动入口默认获取跨进程文件锁，第二个实例提示占用后退出；`doctor.py` 显示锁状态；仍有 `--allow-multiple` 显式绕过，用于测试/维护 |
| R9 | 旧聊天角色扮演/引用台词被误存为用户事实 | 已缓解（2026-10-26）：D17 + 5 个回归测试；导入角色扮演会话只入消息、不入记忆。剩余边界：无标记、无引号的角色扮演仍无法可靠区分，可在记忆窗口手动纠正/忘记 |
| R10 | 直接引入第三方角色文本/素材带来 IP 与分发风险 | M2.5 已只内置原创摘要（`data/personas/remielle/`），不含官方原文、语音、图像、模型或 Live2D 素材；角色与世界观版权归 miHoYo，默认仅个人本地非商业使用。导入面板先展示来源、许可与大小，只复制文本文件；若要公开分发，应移除该角色包或替换为自有角色 |
| R12 | 稳定人设文件过长会挤占上下文预算 | 当前 persona + facts 约 4k 汉字量级；worldbook 不进入稳定前缀，只按查询注入最多 2 条、约 1200 字符；M3 预算不裁剪稳定层，只裁剪历史，并给召回记忆（1600 字符）与用户设定（2000 字符）设上限 |
| R11 | 角色扮演过滤器误伤真实引用 | 过滤只跳过记忆抽取、不删除消息；普通消息中的显式“请记住”仍生效；用户可在记忆窗口手动新增/纠正 |
| R13 | 落盘的工具结果可能包含网页原文或其他敏感内容 | 已缓解（2026-10-27）：文件只在 `data/tool_results/` 内、按会话句柄访问，单文件存储上限 4MB；提示词要求只引用实际回读片段；管理台可导出、按会话删除或全量清理；仍未实现按时间自动清理，长期使用会累积，需定期用管理台清理 |
| R14 | 透明置顶浮窗与系统托盘受桌面环境限制：部分远程桌面/无托盘环境无法显示托盘，个别显卡驱动下透明组合可能异常 | 托盘不可用时自动跳过且不影响主窗口；浮窗始终可关闭，主窗口提供“形象”开关；离屏 Qt 测试覆盖点击穿透与位置恢复，真实桌面仍需人工确认视觉合成 |
| R15 | 技能正文与参考文件是用户可编辑的本地内容，可能包含诱导模型越权或泄露上下文的指令 | 技能以工具结果注入，系统安全规则与工具策略仍优先；声明工具在加载与调用两层校验，本轮工具范围收窄；参考文件限制在技能目录内并有大小/单次字符上限；审计记录调用与拒绝，便于事后排查 |
| R16 | 备份、导出 zip 与 `tool_results/` 都是明文数据副本，删除数据库记录后仍可能残留 | 备份与导出仅在本地 `data/backups`、`data/exports`；全量删除提供“同时清理备份/导出”选项并提示不可撤销；导出不包含 `config/keys.json`，且日志与审计落库前脱敏；用户若把导出文件复制到别处，需自行管理 |

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
- [x] `proactive/reminders.py`：创建、到点查询、下一条到点、完成/忽略；相对时间解析支持“10 分钟/2 小时/三天后提醒我…”（阿拉伯数字与一至十、两），不调用模型。
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

接下来（M8 之后）：

1. 语音与 Live2D：先做可替换的 TTS/表情接口，再评估本地模型与素材授权；角色素材仍只允许用户本地导入。
2. 多角色与社交渠道：按会话隔离人设与记忆，渠道接入前先补齐出站消息确认与速率限制。
3. 任务卡可继续增加多执行器路由与转发结果回写；当前一次只绑定一个执行器。
4. （可选）真实模型压缩后继续多轮的冒烟，补齐 M3R-5 剩余部分；真实桌面下人工确认浮窗视觉合成与托盘行为。
5. 技能的可视化编辑/市场/远程安装与技能间编排，待管理台稳定后再评估。
6. 主动陪伴的更多来源（如共同经历、日程）与打扰阈值调优，待真实使用数据积累后再做。
7. 工具结果与备份的按时间自动清理策略；当前依赖管理台手动清理。

以上需用户逐项确认后启动；`eval_recall.py` 在分词或评分参数变更后应重跑并记录基线变化。

## 11. 变更记录

| 日期 | 变更 |
|---|---|
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
