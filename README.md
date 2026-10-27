# Desktop Companion Agent

第一版桌面数字分身已加入 `companion.py`。它支持陪伴/工作模式、搜索后确认写入笔记、持久化回执、共同经历、记忆与情绪纠正。

## 快速启动

首次使用双击 `setup.cmd`。它会自动创建 `.venv`、安装 Python 依赖并运行环境检查。
安装完成后双击 `start_companion.cmd` 启动桌面程序，或在 PowerShell 中运行：

```powershell
.\run.ps1
```

环境或依赖异常时运行：

```powershell
.\run.ps1 --ui cli --offline
.venv\Scripts\python.exe doctor.py
```

环境包含 Python 3.12、PySide6/Qt WebEngine、OpenAI SDK、PyAutoGUI、pywinauto、pygame、Pillow 和 Playwright Chromium。依赖版本分别固定在 `requirements.txt` 与 `requirements-optional.txt`。

```powershell
python companion.py
```

缺少 PySide6 时会自动使用 Tkinter。完整功能和安全边界见 [第一版使用说明](docs/v1-usage.md)。

## 第一版能力

- 陪伴、工作、执行、安静四种模式；陪伴与安静模式不开放电脑执行工具。
- 工作/执行模式下模型可原生调用 `search_web`、`write_note`、`current_time`；工具结果会回填模型继续推理，写入只生成提案。
- 工具循环有上限：单轮最多 4 次调用、最多 3 轮；用满后仍会把最后一轮结果交给模型收尾，避免刚拿到数据就被截断。工具级超时（搜索 30 秒、写入 10 秒、时间查询 5 秒）。
- 模型不支持工具调用时自动降级为普通对话；显式“搜索…并保存为笔记”仍走正则快路径。
- 搜索后整理为 Markdown 笔记，确认前绝不写文件。
- 授权目录、路径逃逸检查、文件哈希冲突检测、覆盖备份和撤销。
- 提案绑定工作区快照与有效期；确认时复核当前模式、工作区和策略，不一致则不写入。
- 执行账本保证重复确认只写入一次；结果不确定时标记为 `unknown`，重启后不自动重放。
- SQLite 持久化对话、提案、执行回执、记忆、共同经历和情绪快照；数据库启动时按 `user_version` 自动迁移（当前为 v8，新增 `skill_audit` 技能审计表）。
- 用户偏好与事实记忆带用户原文证据、规范键与候选评分；同键新值会取代旧值，支持查看、纠正和忘记。
- 记忆召回采用中文 2/3-gram 预分词 + FTS5/BM25 候选检索，排序沿用单一读时衰减公式；索引损坏或不可用时自动降级为关键词召回，中文单字查询也走关键词兜底，重启时自愈，召回日志记录来源与是否被回复采用。
- 内置确定性召回评测集（`python -m agent_framework.companion.eval_recall`，26 条中文查询），输出 hit@1 / hit@5 / MRR；当前基线均为 1.0，可用来衡量分词与评分调整的回归。
- 遗忘使用墓碑与遗忘序号，阻止相同内容被重新导入或召回；派生经历、反思和摘要随来源事实级联失效。
- 记忆窗口可查看来源原文、候选评分、生命周期事件与最近召回；人设事实可标记为受保护，不会被自动取代。
- 旧聊天支持 JSON/JSONL 导入：先展示成本预估，可暂停续跑，跨副本按消息内容去重，已遗忘内容不会复活。
- 角色扮演、引用台词与假设句不会写入用户事实；导入角色扮演会话时只保留消息、不抽取记忆。
- 默认人设为《绝区零》的蕾米埃尔·丹（本地 persona pack）：人格、背景与说话方式走稳定系统前缀，不写入用户事实记忆；角色事实不会被说成用户经历或共同经历。
- 人设包可替换：`--persona <id>` 选择 `data/personas/<id>/`；缺失或损坏时回退为朴素助手人格，不影响启动。
- 人设面板可预览来源、许可、版本与文件大小，导入本地人设包，并编辑称呼、关系与边界；这些设定以受保护人设记忆保存。
- 可选世界书按当前话题触发，只注入相关条目，不占用稳定前缀，也不写入用户记忆。
- 上下文按“稳定层 → 会话前缀 → 运行时上下文 → 相关记忆 → 最近对话”分层装配；稳定层有独立 SHA-256，模式切换不会改变它。
- 历史超出字符预算 82% 时压缩：保留最近完整轮次，较旧消息生成抽取式摘要并写入 `context_checkpoints`；摘要不含模型臆测，后续轮次复用。
- 工具输出超过 16KB（UTF-8 字节）时落盘到 `data/tool_results/`，模型只收到预览、字符数与不透明句柄；可用 `read_tool_result` 按 offset/limit 分段回读，句柄仅限当前会话，重启后仍可读。
- 未回读的内容不算已读：稳定层提示、落盘通知与回读头部都要求模型不得假设、概括或声称读取未返回部分；存储不可用时降级为“仅预览、不可回读”。
- 情绪估计也可由用户纠正，并与角色状态分开保存。
- 主动底座（M4.0）：`reminders` 表支持提醒的创建、到点、完成与忽略，聊天里说“提醒我 10 分钟后喝水”即走本地解析，不调用模型。
- 未完成话题从最近消息派生（未回复的提问、回复失败的问题），保留 `source_message_id` 便于回溯，不额外调用模型。
- 活动状态用 `ctypes` 读取 Windows 前台窗口与空闲时间，区分 active/idle/away/private/focus/game/unknown；非 Windows 或读取失败时保守判为 unknown。`doctor.py` 可查看当前探测结果。
- 主动调度（M4 主体）：门控链为安静模式 → 忙碌 → 免打扰时段 → 每日上限 → 最小间隔 → 连续未回复 → 隐私窗口 → 活动状态；来源为提醒、未完成话题与（可选）近期被回复采用的记忆，来源为空时 `no_source` 安全退出，不编造话题。
- 主动状态持久化：`proactive_state` 保存开关、免打扰、每日上限、最小间隔、未回复阈值、每日计数、`last_spoken_at`、`last_event_id`；跨天自动重置每日计数，用户回复后清零未回复并标记事件已读。
- 反重复按来源类型半衰期衰减（未完成话题 24 小时、记忆 72 小时），提醒按用户明确请求允许重复；归一化内容哈希与 URL 哈希均参与匹配，重复来源会被跳过并尝试下一条来源。
- 错过的提醒不补发：超过送达宽限 30 分钟即记为 `missed`、关闭提醒并保持未读；事件 `proactive_events` 记录来源、`trigger_reason`、状态与消息 ID，界面“主动”面板可查看事件、设置与未读。
- 主动消息由本地模板生成（不调用模型），Qt 每 60 秒轮询一次，可手动“现在检查”；默认 22:00–08:00 免打扰、每日最多 2 次、间隔至少 3 小时、连续 2 次未回复即停止，第一版默认安静。
- 工作任务卡（M5）：对外部有副作用的请求（如“把总结发给项目群”）先做意图路由，只有 `work` 才建任务卡；任务卡包含标题、原因、口语摘要、目标、执行器与项目，保存后再等待用户。
- 计划状态机为 `NORMAL / PLAN_DISCUSSING / PLAN_REVIEW / EXECUTING`，状态与审批状态先落库再等待；信息不足只追问一次，补充信息合并进目标。
- 审批卡支持批准、修改、拒绝；有效期 30 分钟，超时回到讨论状态并标记 `expired`，**绝不会自动批准**；崩溃恢复把审阅/执行中的任务退回讨论并提示重新审阅。
- 转发回执为 `awaiting_confirmation / forwarding / accepted / completed / unknown / unavailable`；`unknown` 是终态，不可改写也不会自动重发。
- 意图路由有 12 秒硬上限，模型分类器超时或输出非法即降级为 `clarify`；提示注入先由守卫拦截，直接追问且不创建任务。
- Qt“任务”面板与 Tk 任务窗口可查看任务卡、计划状态与回执，并执行批准/修改/拒绝/刷新。
- 流式回复（M6）：模型按 `stream_chat` 逐段返回 `delta`，工具调用以 `tool` 事件显示 running/done，结束发 `done`，失败发 `error`；“停止”会取消生成并持久化“（已停止生成）”，generation id 丢弃停止后的迟到分片。
- 行内控制标记 `[[mood:...]] / [[action:...]] / [[sticker:...]]` 在显示、入库和后续语音前剥离；标记被拆到多个分片时也能完整过滤。
- Qt 桌面形象是透明无边框置顶浮窗（`FramelessWindowHint | Tool | WindowStaysOnTopHint` + `WA_TranslucentBackground`），默认鼠标穿透，指针移到头像上才可拖动；位置写入 `ui_state.json`，重启后恢复到原屏幕，显示器变化时自动修正边界。
- 系统托盘提供显示/隐藏主窗口、显示桌面形象、切换陪伴/工作模式、暂停主动、立即检查与退出；系统托盘不可用时自动跳过，不影响主窗口。
- 状态气泡只读已落库的提案、任务与回执：等待确认写入、等待批准计划、等待补充信息、正在转发、结果不确定、已写入、已撤销、写入失败；待机时自动隐藏。
- 技能包（M7）：把 `data/skills/<id>/SKILL.md` 放入数据目录即可新增流程型能力，无需改代码；frontmatter 声明 `name / description / tools / effectKind / modes / references`，正文为流程指令，`references/` 存放按需读取的说明文件。
- 技能按模式与风险过滤：`read / network / write / external` 映射到风险等级，陪伴/安静模式只显示安全技能；声明未知工具、越权工具或与风险冲突的 `modes` 会在加载时被拒绝并出现在 `doctor.py` 的 `Skills` 检查中。
- 元工具 `invoke_skill` 才把技能正文送入本轮上下文，`read_skill_reference` 按 offset/limit 分段读取声明过的参考文件（单次最多 8000 字符）；技能加载后本轮只能调用它声明的工具（元工具除外）。
- 每次技能调用、参考读取与越权工具拒绝都写入 `skill_audit`，可用 `skills_status()` / `skill_audits()` 查看；技能不能覆盖安全规则、权限或确认流程。
- 管理台（M8）：主窗口顶部“管理”或托盘“管理台”打开 7 页窗口——记忆、召回、候选、执行与审计、情绪、失败、设置与数据；每个页面都保留原始记录 ID，失败页由提案/回执/任务/审计/提醒/事件派生，不另建失败表。
- 备份与恢复：创建手动快照并生成 schema/计数/SHA-256 清单；校验备份完整性、外键与版本后，可恢复并自动先备份当前状态。数据库升级前若旧库已有数据，会自动生成 `pre-migration` 快照。
- 导出与删除：导出 zip 包含 24 张业务表、清单与工具结果，不读取 `config/keys.json`；可彻底删除单条记忆（含派生树、证据、召回引用与 FTS）或整个会话，也可在输入确认文字后删除全部数据并选择清理备份/导出。
- 日志脱敏：`sk-*`、Bearer、`api_key/token/secret/password/authorization` 赋值与 GitHub token 在写入技能审计、执行失败与工作回执前统一替换为 `[REDACTED]`。
- 单实例：默认同一数据目录只运行一个实例，第二个实例提示占用并以退出码 3 结束；调试可用 `--allow-multiple` 跳过。`doctor.py` 新增 schema 版本、缺失索引、FTS 行数、备份与实例锁检查。
- 关闭后重开可恢复历史会话、模式与待确认动作。

## 人设包

默认人设位于：

```text
data/personas/remielle/
├── persona.md    # 身份、性格、说话方式、关系姿态、安全边界
├── facts.md      # 少量常驻角色背景事实（不是用户记忆）
├── worldbook.md  # 可选世界书，按话题触发（不进入稳定前缀）
└── persona.json  # 名称、版本、来源、许可、剧透边界
```

启动时可切换：

```powershell
python companion.py --persona remielle
```

人设包只决定语气、偏好和叙事视角，不改变工具权限、执行确认、隐私与事实诚实规则。角色资料不会写入 `memories`；用户自定义的称呼、关系或边界仍作为受保护的用户人设记忆保存。`persona.py` 会为 `persona.md` + `facts.md` 计算稳定前缀 SHA-256；`context.py` 在此之上叠安全规则、会话前缀与运行时上下文，并记录稳定层指纹。

在 Qt/Tk 界面点击“人设”可以：

- 查看当前包的名称、版本、来源、许可、文件大小与世界书条目数；
- 修改称呼、关系与边界，留空即清除；
- 从本地目录导入人设包。导入前会展示来源、许可、大小与版本，只复制 `persona.json`、`persona.md`、`facts.md`、`worldbook.md` 四类文本文件，忽略图片、语音和模型；同 ID 覆盖需要再次确认。

`worldbook.md` 使用 `## 条目标题` 分段，按当前消息检索，最多注入 2 条、约 1200 字符，并且不参与稳定前缀哈希。

> 蕾米埃尔·丹及《绝区零》世界观版权归 miHoYo 所有。本仓库只包含本地、非商业使用的原创设定摘要，不包含官方原文、语音、图像、模型或 Live2D 素材；请勿将该角色包用于商业分发。

## 启动第一版

推荐 Python 3.11 及以上；当前验证环境为 Python 3.12.6。

```powershell
python -m pip install -r requirements.txt
python companion.py
```

强制使用 Tkinter 兼容界面：

```powershell
python companion.py --ui tk
```

完成一次任务：

1. 打开“工作”模式并授权一个普通文件夹。
2. 输入 `搜索 Python 桌面智能体并保存为笔记`。
3. 核对目标与内容，再点击“允许这次写入”。
4. 在授权目录的 `notes/` 下查看文件，或点击“撤销最近写入”。

网页搜索和远程 LLM 需要当前环境允许出站 HTTPS；网络失败时不会创建文件提案。

## 旧版命令行 Agent

下面保留了仓库原有的轻量命令行 Agent 说明，可用于单独测试基础 LLM 和工具循环。


## 旧版结构

```text
.
├── main.py                         # 命令行入口
├── agent_framework/
│   ├── __init__.py
│   ├── agent.py                    # Agent 主循环：模型回复 + 工具执行
│   ├── config.py                   # 环境变量和 keys.json 配置
│   ├── llm.py                      # 远程/离线 LLM 客户端
│   ├── messages.py                 # 消息与会话记忆
│   └── tools.py                    # 工具注册表和内置工具
├── config/
│   └── keys.template.json          # 密钥配置模板
└── tests/
    └── test_agent.py               # 离线冒烟测试
```

## 规划文档

- [项目进度与路线图](docs/progress.md)
- [五个参考项目调研](docs/competitor-analysis.md)
- [数字分身技术方案](docs/technical-plan.md)

## 旧版运行

```powershell
python main.py
```

也可以直接问一次：

```powershell
python main.py "现在几点"
python main.py "计算 12 * (3 + 4)"
python main.py --offline "你好"
```

交互模式支持：

- `:tools` 查看已注册工具
- `:history` 查看会话记忆
- `:clear` 清空会话记忆
- `:quit` 退出

没有 API Key 时会自动使用离线客户端，仍然可以演示时间查询和数学计算工具。

## 接入模型

复制配置模板并填入真实的兼容接口信息：

```powershell
Copy-Item config\keys.template.json config\keys.json
```

`config/keys.json` 示例：

```json
{
  "OPENAI_API_KEY": "你的 API Key",
  "OPENAI_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "MODEL_NAME": "qwen-plus"
}
```

远程模式需要：

```powershell
python -m pip install openai
```

如果没有创建 `config/keys.json`，也可以直接使用环境变量：

```powershell
$env:OPENAI_API_KEY = "你的 API Key"
$env:OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:AGENT_MODEL = "qwen-plus"
python main.py
```

## 扩展旧版工具

在 `agent_framework/tools.py` 的 `build_default_tools()` 中注册新的 `ToolSpec` 即可。工具处理器接收 JSON schema 中声明的参数，并返回字符串结果；Agent 会在模型提出调用请求后自动执行工具，再把结果交回模型。

## 旧版验证

```powershell
python -m unittest discover -s tests -t tests -v
python -m agent_framework.companion.eval_recall
```

`tests/` 不是包，`-t tests` 不能省略。召回评测使用临时数据库与固定种子，不调用模型。

## 接入个人微信 / ClawBot

项目提供 `agent_service.py`，把当前 `ChatAgent` 暴露为本地 OpenAI 兼容接口。OpenClaw 已配置为通过 `current-agent/python-agent` 调用它。

启动本地服务：

```powershell
python agent_service.py
```

详细配置和故障排查见 `integrations/openclaw/README.md`。
