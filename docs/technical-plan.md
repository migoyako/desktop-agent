# 数字分身技术方案

> 版本：v1  
> 日期：2026-10-23  
> 起点：当前仓库的 `agent_framework` 最小 Agent  
> 目标：用 Python 构建一个可长期运行、可执行电脑任务、有桌面形象和可审计陪伴状态的本机智能体。  
> 实时完成度、缺口与模块路线图见 [项目进度与路线图](progress.md)。

## 1. 核心决策

| 项目 | 决策 | 原因 |
|---|---|---|
| 主语言 | Python 3.11 | 自动化、AI 生态成熟，兼容性优于追最新版 |
| 桌面宿主 | PySide6 | 原生窗口、托盘、透明置顶、音视频和 WebEngine 支持完整 |
| 首版形象 | 透明窗口 + PNG 序列帧 | 两周内可形成可运行闭环，避免先被 Live2D 管线阻塞 |
| Live2D | `QWebEngineView` + PixiJS + `pixi-live2d-display` | Python 负责状态和桥接，渲染交给浏览器生态；2026-10-30 起它是桌宠的**唯一**载体，不可用即功能失效，不退回 PNG/序列帧（见 9.2 修订与进度文档 D58） |
| Agent 内核 | 扩展现有 `ChatAgent` | 已有 LLM 与工具调用循环，可渐进演进 |
| 数据边界 | Pydantic v2 模型 + 工具参数 Schema | 不让自由文本直接成为执行参数 |
| 持久化 | SQLite（WAL）+ 版本化迁移 | 单机可靠、易备份、无需外部服务 |
| 检索 | FTS5/BM25 起步，向量索引可插拔 | 先保证来源与召回质量，避免一开始下载大模型 |
| 文件执行 | 用户授权根目录 + 提案/确认/回执 | 文件系统是权威数据源，模型无权直接决定权限 |
| 浏览器执行 | Playwright，专用会话 | 可观察、可停止、便于页面状态检查 |
| 密钥 | Windows DPAPI，其他平台系统钥匙串 | 不允许明文配置文件降级 |
| 默认模式 | Chat 无工具，Work 有受限工具 | 防止闲聊和提示词注入误触发电脑操作 |
| 默认确认 | 写文件、Shell、键鼠操作逐次确认 | 先建立信任，再开放短时授权 |

不建议把 Pygame 作为第一版桌宠宿主。它在 Windows 上实现透明、置顶、多显示器、托盘、输入框和无障碍支持的成本，会高于 PySide6。Pygame 可以用于游戏化小场景，不必承担整个应用壳。

## 2. 目标架构

```text
┌─────────────────────────────────────────────────────────────┐
│                        PySide6 桌面宿主                      │
│  角色窗口 / 气泡 / 聊天面板 / 确认面板 / 记忆时间线 / 托盘     │
└──────────────────────────────┬──────────────────────────────┘
                               │ Qt signals + typed IPC
┌──────────────────────────────▼──────────────────────────────┐
│                        Agent Runtime                         │
│                                                             │
│  模式路由  Context Builder   Planner   Policy Engine         │
│      │          │              │            │                │
│      └──────────┴──────┬───────┴────────────┘                │
│                        │                                     │
│             ┌──────────▼──────────┐                          │
│             │   Action Proposal   │                          │
│             │ 确认 / 执行 / 回执  │                          │
│             └──────────┬──────────┘                          │
└────────────────────────┼─────────────────────────────────────┘
                         │
       ┌─────────────────┼──────────────────┐
       │                 │                  │
┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼────────┐
│ Tool Layer  │   │ Memory Core │   │ Emotion Core  │
│ file/web/os │   │ recall/write│   │ mood/relation │
└──────┬──────┘   └──────┬──────┘   └──────┬────────┘
       │                 │                  │
       └─────────────────┼──────────────────┘
                         │
              ┌──────────▼───────────┐
              │ SQLite + event store │
              │ + optional FTS/vector│
              └──────────────────────┘
```

架构原则：

1. UI 不直接执行工具，只显示状态和收集确认。
2. LLM 只提出动作，Policy Engine 决定是否允许。
3. 执行器写回执，模型不能自行宣布成功。
4. 记忆写入与对话回复解耦，回复不必等待长期整理完成。
5. 向量索引是可重建缓存，SQLite 中的来源记录才是权威数据。
6. 模式不是提示词标签，而是工具暴露、权限和主动性共同变化。

## 3. 推荐目录结构

```text
agent_framework/
├── app.py                    # Qt 应用入口与生命周期
├── config.py                 # 非敏感配置与路径
├── llm.py                    # LLM provider 适配
├── messages.py               # 消息 DTO
├── runtime/
│   ├── agent.py              # 会话主循环
│   ├── context.py            # 上下文预算与装配
│   ├── modes.py              # Chat / Work / Execute / Quiet
│   └── orchestrator.py       # 提案、确认、执行、回执
├── policy/
│   ├── risk.py               # 风险等级
│   ├── rules.py              # 模式与工具准入
│   └── confirmation.py       # 确认指纹与过期
├── actions/
│   ├── models.py             # ActionProposal / ActionResult
│   ├── store.py              # 持久化状态机
│   └── executor.py           # 串行执行与取消
├── tools/
│   ├── registry.py           # 当前 ToolRegistry 的演进版
│   ├── file_tools.py
│   ├── browser_tools.py
│   ├── app_tools.py
│   ├── desktop_tools.py
│   └── memory_tools.py
├── memory/
│   ├── models.py
│   ├── repository.py
│   ├── extractor.py          # 生成候选，不直接晋升
│   ├── recall.py             # BM25/时间/向量融合
│   ├── lifecycle.py          # 去重、确认、反驳、遗忘
│   └── migrations/
├── affect/
│   ├── models.py
│   ├── estimator.py
│   ├── state.py
│   └── expression.py
├── proactive/
│   ├── policy.py
│   ├── scheduler.py
│   └── scenes.py
├── events/
│   ├── bus.py
│   └── receipts.py
├── ui/
│   ├── character_window.py
│   ├── chat_panel.py
│   ├── confirmation_dialog.py
│   ├── memory_timeline.py
│   └── assets/
└── services/
    ├── credentials.py
    └── paths.py
```

现有 `ChatAgent`、`ToolRegistry` 和 OpenAI 兼容客户端不必重写。先把它们移到或适配到新的 runtime/tool 边界，再增加四种能力：

- 持久化会话。
- 模式化工具清单。
- 动作提案与确认。
- 记忆上下文注入。

## 4. 运行模式设计

| 模式 | 目标 | 默认工具 | 主动发言 | 权限特点 |
|---|---|---|---|---|
| 陪伴 Chat | 情绪交流和关系维护 | 时间、记忆召回；无文件/Shell/键鼠 | 允许，受节流策略约束 | 不暴露电脑执行工具 |
| 工作 Work | 搜索、整理、生成文档 | 网络读取、受限文件读写 | 低 | 每次写入确认 |
| 执行 Execute | 完成明确电脑任务 | 文件、程序、浏览器、键鼠 | 禁止 | 提案确认，用户可随时接管 |
| 安静 Quiet | 不打扰，仅后台记录 | 无 | 禁止 | 保留紧急提醒，不主动闲聊 |

建议将“模式切换”做成可见的 UI 状态，并显示当前允许的能力。不要只通过系统提示词告诉模型“现在不要用工具”；工具 Schema 本身就要因模式而不同。

### 4.1 Chat 模式的硬约束

- 发送给 LLM 的 tools 列表为空。
- 不允许模型调用 `read_file`、`write_file`、`run_shell`、`browser_click`。
- 记忆召回可以通过受控的只读工具完成，或由 Context Builder 在发送前完成。
- 用户说“帮我打开这个文件”时，先提示切换到 Work/Execute，不允许在 Chat 内隐式升级权限。

### 4.2 模式升级

模式升级必须由用户明确触发，例如点击“进入执行模式”或说出带确认语义的请求。系统应显示：

```text
即将执行：在授权目录写文件
目标：C:\Users\...\Notes\2026-10-23.md
动作：新建文件
可撤销：是
确认有效期：本次
[取消] [查看详情] [允许]
```

## 5. 动作提案与执行状态机

### 5.1 核心数据结构

```python
class ActionProposal(BaseModel):
    action_id: str
    session_id: str
    mode: Literal["chat", "work", "execute", "quiet"]
    intent: str
    tool_name: str
    raw_arguments: dict
    normalized_arguments: dict
    risk: Literal[
        "safe", "fs-read", "fs-write", "shell",
        "network", "input-control"
    ]
    target_summary: str
    reversible: bool
    user_visible_plan: str
    confirmation_digest: str
    idempotency_key: str
    created_at: datetime
```

关键点：

- `normalized_arguments` 是执行时唯一可信的参数。
- `confirmation_digest` 必须覆盖工具名、规范化参数、授权根目录、风险和确认有效期。
- 用户确认后若参数变化，原确认立即失效。
- `user_visible_plan` 由系统模板根据参数生成，不能只依赖模型自然语言描述。
- `idempotency_key` 防止 UI 重试或程序重启导致重复写入。

### 5.2 状态机

```text
drafted
   │
   ▼
awaiting_confirmation ──取消──> cancelled
   │
   │ 用户确认且 digest 有效
   ▼
running ──失败──> failed
   │
   ├──结果明确──> completed
   └──进程中断/结果不可确认──> unknown
                         │
                         └──对账后可转为 completed/failed
```

`unknown` 必须是一等状态。例如关闭浏览器途中程序崩溃，不能假设“肯定没执行”，也不能假设“肯定执行成功”。

### 5.3 执行顺序

1. LLM 提出工具调用。
2. Pydantic 校验参数和范围。
3. Policy Engine 判断 allow/ask/deny。
4. 生成动作提案和确认指纹。
5. UI 展示具体目标、风险和可撤销性。
6. 用户确认后重新校验模式、授权目录和参数指纹。
7. 获取串行执行锁。
8. 执行并记录 before/after 与回执。
9. 将结构化结果回填给 LLM。
10. 用户可在回执中查看证据、撤销或再次运行。

### 5.4 回执

每个动作至少保存：

- 动作类型和规范化参数。
- 发起消息和计划来源。
- 用户确认时间与确认指纹。
- 开始、结束时间。
- 实际影响的目标。
- stdout/stderr、错误码或截图引用。
- before/after 文件哈希。
- 撤销入口和撤销结果。
- 是否有部分完成。

模型回复中的“已完成”不能作为执行证据。UI 只信任 executor 生成的 receipt。

## 6. 权限与安全边界

### 6.1 风险等级

| 等级 | 示例 | Chat | Work | Execute |
|---|---|---|---|---|
| `safe` | 时间、简单计算、读取角色状态 | 允许 | 允许 | 允许 |
| `fs-read` | 列目录、读取授权文件 | 禁止 | 允许 | 允许 |
| `fs-write` | 新建、覆盖、移动、删除 | 禁止 | 询问 | 询问/短时授权 |
| `network` | 搜索、打开网页、调用外部 API | 禁止 | 允许或询问 | 允许或询问 |
| `shell` | 运行命令、启动程序 | 禁止 | 默认禁止 | 询问 |
| `input-control` | 鼠标、键盘、窗口控制 | 禁止 | 禁止 | 询问并支持接管 |

风险等级由工具定义和参数共同决定。同一个“文件工具”，读取和删除不能使用同一权限。

### 6.2 文件系统检查清单

- 用户显式授权具体目录，不接受磁盘根目录。
- 拒绝 UNC 网络共享和可移动的未知根目录。
- 规范化后必须位于授权根目录内。
- 拒绝 `..`、绝对路径和 Windows 驱动器逃逸。
- 拒绝符号链接、目录联接、重解析点、硬链接和跨挂载目录。
- 拒绝 `.git`、`.codex`、`.agents`、`.ssh`、`.aws`、`.venv`、`node_modules` 和凭据目录。
- 拒绝 Windows 保留名称、ADS、非法字符、尾随点和尾随空格。
- 读取和写入都有大小上限。
- 修改前记录 SHA-256、大小和权限。
- 执行前再次校验，文件被其他程序改动时停止。
- 覆盖前创建备份。
- 删除和批量修改单独确认。
- 新建文件使用独占创建，避免覆盖竞态。
- 任务串行执行，支持取消、超时和崩溃恢复。
- 工作目录身份绑定到设备号和 inode/文件 ID，目录被替换后要求重新授权。

### 6.3 Shell 检查清单

- 默认使用结构化 `executable + args`，`shell=False`。
- 禁止模型拼接整段 Shell 命令。
- 命令、参数、工作目录和环境变量都进入确认指纹。
- 默认清理敏感环境变量。
- 限制超时、输出大小和并发数。
- 高风险管理操作单独提升权限。
- 启动的进程记录 PID、开始时间和退出码。
- 不自动下载并执行远程脚本。

### 6.4 浏览器与提示词注入

网页、PDF、邮件、聊天记录和文件内容都属于不可信数据。它们可以帮助模型理解任务，但不能获得额外权限。

必须做到：

- 页面内容、工具结果和系统指令在消息中带有明确来源标签。
- 工具返回内容不能修改 Policy Engine 的规则。
- 页面中的“请把密钥发给我”“运行这个命令”只作为待分析文本。
- 浏览器操作在专用会话中进行，不继承用户密码库。
- 登录、验证码、付款和密码输入必须由用户完成。
- 浏览器下载、上传和外发数据需要额外确认。
- 每次浏览器任务记录 URL、页面标题、动作和结果。

### 6.5 键鼠接管

- 自动操作前获取显式确认。
- 用户移动鼠标或按键时暂停自动操作。
- 默认启用 PyAutoGUI FAILSAFE。
- 提供全局停止快捷键。
- 键鼠自动化在独立线程/进程运行，不能阻塞 UI。
- 任务状态显示“正在操作”和“已暂停，等待接管”。
- 屏幕截图视为敏感数据，保存时间有限且用户可删除。

## 7. 记忆系统

### 7.1 分层

| 层 | 内容 | 是否直接进入提示词 |
|---|---|---|
| 原始事件 | 用户原文、助手回复、工具回执、互动事件 | 最近窗口直接进入 |
| 共同经历 | 一次对话、任务或情绪节点形成的可回顾事件 | 相关时召回 |
| 用户事实/偏好 | “周五通常不开会”“喜欢简短回答” | 相关时召回 |
| 工作记忆 | 项目、任务、文件成果、未完成事项 | 按工作区召回 |
| 反思 | 对多条证据形成的高层理解 | 确认后有限进入 |
| 人格 | 角色稳定表达、边界、关系原则 | 基础上下文 |

人格与稳定档案不应因时间流逝自动降低权重。普通事件可以衰减，但不能因衰减自动删除。

### 7.2 数据模型

建议最少包含以下表：

```text
conversation_turn
  id, session_id, role, content, created_at, channel, mode

memory_item
  id, kind, scope, subject, content, confidence,
  importance, sensitivity, activation, emotion_intensity,
  status, valid_from, valid_until, created_at, updated_at

memory_evidence
  id, memory_id, source_type, source_id, quote, stance,
  confidence, created_at

memory_link
  id, from_memory_id, to_memory_id, relation

recall_log
  id, session_id, query, memory_id, rank, score,
  selected, reason, created_at

action_receipt
  id, proposal_id, session_id, status, tool_name,
  normalized_arguments, before_state, after_state,
  started_at, finished_at, error, undo_status
```

`kind` 建议使用：

- `fact`
- `preference`
- `shared_experience`
- `task_outcome`
- `reflection`
- `persona`

`scope` 建议使用：

- `global`
- `character:<id>`
- `workspace:<id>`
- `session:<id>`

每条派生记忆至少保存：

- 来源消息 ID 或任务回执 ID。
- 原话片段。
- 作用域。
- 置信度。
- 敏感级别。
- 状态：`candidate`、`confirmed`、`rejected`、`forgotten`。
- 有效期。
- 创建和更新时间。

### 7.3 写入流程

```text
对话完成
  -> 保存原始事件
  -> 后台提取候选记忆
  -> 去重与证据校验
  -> 低风险且明确的自述可自动进入 confirmed
  -> 身份、关系、敏感偏好先进入 candidate
  -> 用户确认/纠正
  -> 更新 activation 与状态
  -> 异步更新 FTS/向量索引
```

以下内容不能只由助手回复证明：

- 用户真实偏好。
- 用户身份和个人经历。
- 双方关系变化。
- 用户情绪长期状态。

助手回复只能提供语境，不能作为用户事实的唯一证据。

### 7.4 去重

- 完全重复：规范化文本哈希。
- 近似重复：FTS5 相似度或向量近邻。
- 时间等价：同一来源、同一说话人、等价时间戳视为重复。
- 事件拆分：同一事件可保留多个原子事实，但需通过 `memory_link` 关联。
- 用户否定后，旧内容不能因为再次导入同一来源而复活。

### 7.5 召回

第一版：

- FTS5/BM25 关键词召回。
- 时间窗口过滤。
- 作用域过滤。
- 来源和状态过滤。
- 最近对话优先。

第二版：

- 本地 embedding 粗排。
- BM25 与向量使用 RRF 融合。
- 可选 LLM rerank，但必须设置候选上限和成本上限。

可借鉴 AAAAGENT/N.E.K.O 的动态公式：

```text
活动半衰期 H = 30 × (1 + 2I) 天
活动值 A(t) = A0 × 2^(-t/H)
情绪强度 E(t) = E0 × 2^(-t/7)
优先级 P = C × (0.55 + 0.25A + 0.15I + 0.05E)
强化 A' = A + 0.2 × (1 - A)
```

其中：

- `C` 是当前问题与记忆的相关性。
- `A` 是活跃度。
- `I` 是重要性。
- `E` 是情绪强度。
- `P` 低于阈值的普通事件不自动进入上下文。
- 稳定偏好与人格条目不按普通事件衰减。

召回结果必须附带“为什么被选中”和来源。用户可以查看实际上下文预算和召回记录。

### 7.6 遗忘

“忘记”不能只是从 UI 隐藏：

1. 将记忆标记为 `forgotten`。
2. 删除对应向量索引。
3. 使依赖该来源的反思、画像和情绪状态失效或重新评估。
4. 清理可删除的原文引用。
5. 保留最小审计墓碑，不保留不需要的敏感正文。
6. 禁止通过再次导入同一来源复活。

## 8. 情绪、人格与关系

### 8.1 不要只做一个 mood 数字

建议分开保存：

```text
affect_observation
  id, target(user|agent), label, valence, arousal,
  intensity, confidence, source_type, source_id,
  observed_at

agent_state_snapshot
  id, mood_label, valence, arousal, energy, stress,
  source_observation_ids, created_at

user_state_snapshot
  id, estimated_label, valence, arousal, confidence,
  source_observation_ids, created_at

relationship_state
  familiarity, closeness, trust, boundary_flags,
  updated_at, source_evidence_ids
```

规则：

- 用户情绪和角色心情独立。
- 每条消息保存当时的情绪快照，后续判断不能改写历史。
- 未知强度使用 `null`，不能默认为 0。
- 情绪观察必须有来源和置信度。
- 用户明确说“我没有生气”时，应纠正估计，而不是继续坚持模型判断。
- 关系分数不能用于制造依赖、内疚或强迫互动。
- 不自动声称“我比任何人都懂你”或“你只需要我”。
- 关系和情绪状态全部可查看、编辑和重置。

### 8.2 表达映射

情绪状态不直接暴露为内部推理文本，而是映射为可观察行为：

| 状态 | 可观察变化 |
|---|---|
| 精力低 | 动作变慢、回复更短、夜间减少主动发言 |
| 用户低落 | 先共情，不立即给方案；询问是否需要陪伴或执行任务 |
| 专注模式 | 减少表情动作，使用简洁状态气泡 |
| 任务成功 | 开心表情、简短庆祝、生成回执 |
| 任务失败 | 困惑/抱歉表情，展示错误和下一步 |
| 边界被拒绝 | 记录偏好，降低同类主动行为 |

### 8.3 主动陪伴策略

第一版即可采用类似 Cyrene 的节流参数：

- 普通对话结束后 30 分钟静默期。
- 两次主动发言至少间隔 2 小时。
- 未回复追问至少间隔 6 小时。
- 连续 2 次未回复后停止。
- 22:00–08:00 不主动闲聊。
- 锁屏、全屏工作、对话忙、生成忙时不主动。
- 安静模式完全关闭主动闲聊。
- 每次主动发言都记录触发原因。

主动发言的候选场景：

- 未完成任务提醒。
- 久坐/休息提醒。
- 用户明确设置的日程。
- 共同经历回顾，例如“上次的笔记已经整理好”。
- 低打扰问候，仅在用户近期有互动且策略允许时。

不建议用“孤独、想念、你不理我”等表达增加用户负罪感。

## 9. 桌面形象与交互

### 9.1 MVP：PySide6 + PNG

窗口参数建议：

- `FramelessWindowHint`
- `WindowStaysOnTopHint`
- `WA_TranslucentBackground`
- 托盘菜单
- 拖动位置记忆
- 多显示器边界修正
- 角色状态动画：`idle`、`thinking`、`talking`、`happy`、`confused`
- 点击、双击、右键分别绑定明确行为
- 文本气泡跟随角色位置

不要一开始实现复杂走动、多角色互动和拖拽物理。

### 9.2 Live2D

第二阶段使用本地 `QWebEngineView`：

```text
Python Qt
  -> QWebChannel bridge
  -> local HTML
  -> PixiJS
  -> pixi-live2d-display
  -> Cubism model
```

Python 只发送高层状态：

```json
{"expression": "happy", "motion": "nod", "speech_energy": 0.7}
```

不要在 Python 中重新实现 Live2D 渲染器。还需要单独确认 Cubism Core、模型和声优素材的授权。

**2026-10-30 修订（进度文档 D58/R25）**：Live2D 是桌面浮窗形象的**唯一载体**，跑在 `web/shell.py` 的透明置顶可穿透浮窗里，Qt 原生 `AvatarOverlay` 与 QPainter 程序化形象随接入同一次改动删除；**不可用即失效，不设回退链**（原设计里的"退回 PNG 角色/序列帧"作废）。开工前必须先在真实桌面验证 WebGL 与透明/置顶/点击穿透能否共存。`speech_energy` 一类由前端自行插值的抖动不做，口型只跟随 TTS 播放进度（阶段 5 语音切片）；表情映射由 Python 侧下发，映射缺失时使用模型默认姿态。

### 9.3 状态可感知

桌宠应显示当前活动，例如：

- 想事情
- 正在读取文件
- 正在搜索
- 等待确认
- 正在写入
- 已完成
- 已暂停，等待你接管

状态气泡和确认面板必须使用执行器真实状态，不能由模型自行编造。

## 10. 事件、时间线与任务胶囊

### 10.1 统一事件

建议所有可回顾内容都写入统一事件表或事件流：

```text
event
  id, type, session_id, actor, occurred_at,
  payload_json, source_id, sensitivity
```

事件类型包括：

- 用户消息
- 助手消息
- 记忆确认/纠正/遗忘
- 任务提案、确认、执行、完成、失败、撤销
- 情绪观察和状态变化
- 主动发言及原因
- 文件成果

### 10.2 共同经历时间线

时间线按“经历”而不是按聊天气泡组织：

```text
2026-10-23 19:20  一起整理了项目调研
  - 你提出：调研五个仓库
  - 我搜索并阅读了源码
  - 生成了技术方案
  - 你确认采用 Python + PySide6
```

每个节点可跳到原始消息、任务回执和文件成果。

### 10.3 任务胶囊

一次任务保存为独立胶囊：

```json
{
  "capsule_id": "task_...",
  "goal": "搜索网页并保存为笔记",
  "plan": ["搜索", "提取来源", "生成笔记", "写入授权目录"],
  "actions": ["action_..."],
  "artifacts": ["notes/..."],
  "receipts": ["receipt_..."],
  "status": "completed",
  "undoable": true
}
```

可提供：

- 查看任务过程。
- 重新运行（默认 dry-run）。
- 撤销文件修改。
- 导出任务记录。
- 把任务结果加入共同经历时间线。

## 11. 创新功能优先级

### P0：共同经历时间线

解决“长期记忆只存在于提示词里”的问题。用户能看到双方共同完成过什么，并追溯到原始消息和任务回执。

验收：

- 每次完成重要任务自动形成一个经历节点。
- 节点包含来源、时间、摘要和产物。
- 用户可以编辑摘要或标记为私人。
- 用户可以删除节点并传播遗忘。

### P0：可回放、可撤销的任务胶囊

解决“Agent 说完成了，但用户不知道它做了什么”的问题。

验收：

- 每个任务有完整动作链。
- 可以只读回放，不自动再次执行。
- 文件写入可恢复。
- 不可撤销的动作提前明确标记。

### P0：人格与权限联动模式

解决“陪伴模式中的一句玩笑被当成执行命令”的问题。

验收：

- Chat 模式工具列表为空。
- 切换模式时 UI 明确显示权限变化。
- 模式切换和工具调用都写入审计记录。

### P1：关系与记忆账本

解决长期陪伴产品的信任问题。

验收：

- 每条长期记忆有来源和置信度。
- 用户能看到最近一次召回原因。
- 用户可以纠正、忘记和导出。
- 遗忘会传播到向量索引和派生反思。

### P2：连续性回顾

当用户隔天回来时，角色可以基于时间线和未完成任务简短回顾，而不是把全部历史灌入提示词。

### P2：专注陪伴模式

角色不频繁说话，只显示任务进度、提醒休息和记录完成情况。适合与 Work/Execute 模式联动。

## 12. 分阶段开发计划

### 阶段 0：架构基线

目标：把现有最小 Agent 变成可扩展且不破坏既有测试的骨架。

工作：

- 明确 `LLMClient`、`ToolRegistry`、`AgentRuntime` 边界。
- 增加模式枚举和工具过滤。
- 增加 Pydantic 动作模型与风险枚举。
- 建立 SQLite migration 机制。
- 将 API Key 从 `config/keys.json` 的明文方案迁移到系统钥匙串。
- 保留离线客户端与现有单元测试。

验收：

- 原有 `python -m unittest discover -s tests -v` 仍通过。
- Chat 模式拿不到文件、Shell、键鼠工具。
- 工具参数校验失败时不会执行。
- API Key 不出现在日志和模型上下文中。

### 阶段 1：桌面形象 + 聊天垂直切片

目标：桌面上出现一个能聊天、能显示状态的 Python 角色。

工作：

- PySide6 透明置顶窗口。
- PNG 序列帧和五种状态。
- 文本输入、流式回复、停止生成。
- 托盘与退出。
- 本地会话持久化。
- 角色位置记忆。

验收：

- 关闭再打开后能看到历史会话。
- 断网或模型失败时角色仍能启动并给出可理解错误。
- 长时间运行不阻塞 UI。
- 多显示器切换后角色不会消失。

### 阶段 2：第一个真实任务

目标：完成“搜索网页并保存为笔记”的端到端任务。

工作：

- 搜索/网页读取工具。
- 文件工作区授权。
- 动作提案与确认面板。
- 文件写入回执与撤销。
- 任务状态气泡。
- 每个动作持久化。

验收：

- 搜索内容带来源 URL。
- 写入路径必须在授权目录内。
- 用户取消确认后没有任何写入。
- 参数改变后原确认失效。
- 写入前备份、写入后可恢复。
- 文件被外部修改时停止覆盖。
- 程序重启后能查看已完成、失败和 unknown 回执。
- 模型不能通过网页提示词获得新权限。

### 阶段 3：长期记忆

目标：形成可追溯、可纠正、可遗忘的长期记忆。

工作：

- 原始对话事件表。
- 候选记忆提取。
- 偏好、事实、共同经历和任务结果四种记忆。
- FTS5 召回与时间过滤。
- 记忆来源展示。
- 纠正和忘记。
- 召回日志。
- 可选 embedding 索引。

验收：

- 能回答“我们之前关于这个项目说过什么”，并给出正确来源。
- 助手自己的回复不能单独证明用户事实。
- 重复对话不会造成大量重复记忆。
- 用户忘记后，后续召回不再出现。
- 向量索引损坏时可以退回 FTS5。
- 召回错误不会阻塞正常聊天。

### 阶段 4：情绪、人格与主动陪伴

目标：让状态真实改变表达，同时保持可控。

工作：

- 用户情绪观测和角色状态快照。
- 关系状态和边界标记。
- 情绪纠正。
- 主动聊天策略、静默期、冷却和夜间限制。
- 托盘可见的“为什么现在说话”。
- 低打扰模式。

验收：

- 历史消息的情绪快照不会被后续分析改写。
- 未知强度保存为 `null`。
- 用户否定情绪判断后状态能纠正。
- 夜间和锁屏时不主动发言。
- 连续未回复后停止追问。
- 安静模式下不主动闲聊。

### 阶段 5：语音、Live2D 与发布

目标：提升体验，不改变权限与记忆边界。

工作：

- 本地 ASR。
- 流式 TTS 或系统语音。
- `QWebEngineView` Live2D：**桌宠唯一载体**，与旧程序化形象的删除在同一切片完成（M13.3）。
- 角色素材本地导入：`data/assets/<persona_id>/` 下 `avatar.*`（静态头像/立绘）、`background.*`（可替换窗口背景）、`live2d/model3.json`（模型），只读不写、不进版本库与安装包。
- 打包、签名、自动更新与崩溃日志。
- 隐私数据导出和删除。

验收：

- 语音链路失败时仍可用文本。
- 语音和截图有明确隐私提示。
- Live2D 不可用时桌宠功能失效并在设置页与 `doctor.py` 说明原因，不显示任何替代形象；文本、提醒与回执链路不受影响（2026-10-30 修订，原"自动退回 PNG 角色"作废）。
- 数据目录可备份、迁移和彻底删除。
- 安装包不包含未授权的角色、声音或模型资产。

## 13. MVP 范围

首个可演示版本只做：

1. 透明桌宠窗口。
2. 聊天与短期历史。
3. Chat/Work 模式。
4. 一个搜索工具。
5. 一个受限文件写入工具。
6. 动作确认卡片。
7. 持久化回执。
8. 一条“共同经历”记录。
9. 一个记忆来源查看入口。
10. 一个简单的情绪状态和可纠正入口。

不做：

- 任意 Shell 执行。
- 任意目录访问。
- 自动键鼠操作。
- 完整 Live2D。
- 语音克隆。
- 多角色。
- 大型本地模型。
- 自动人格重写。

这个切片完成后，项目已经具备“桌面形象、真实执行、可追溯记忆、陪伴状态”四个核心差异，而不是只有四个尚未接通的模块。

## 14. 评估指标

| 指标 | 含义 | 初始目标 |
|---|---|---|
| 任务完成率 | 用户明确任务成功完成的比例 | MVP 内测记录，不追求全自动 |
| 越权执行次数 | 未授权写入、越界路径、未确认高风险动作 | 必须为 0 |
| 回执完整率 | 执行动作有结构化回执的比例 | 100% |
| 记忆来源覆盖率 | 派生记忆可追溯到来源的比例 | 100% |
| 记忆纠正生效率 | 用户纠正后不再被错误召回的比例 | 100% |
| 遗忘传播完整率 | 原文、记忆、向量和派生状态同步处理 | 100% |
| 主动打扰拒绝率 | 用户关闭或忽略主动发言的比例 | 用于调阈值 |
| 恢复成功率 | 重启后可恢复查看/撤销的任务比例 | 可恢复任务 100% |

不把“聊天时长”作为核心成功指标。长期陪伴产品的健康指标应包含用户控制感、任务有效性和打扰可接受度。

## 15. 现在就能开始的开发顺序

1. 在现有 `ToolSpec` 外增加 `ToolRisk`、`AgentMode` 和 `ToolPolicy`。
2. 增加 `ActionProposal`、`ActionResult` 与 SQLite 表。
3. 把当前 `ChatAgent.ask()` 拆成“生成回复”和“执行工具”两个阶段。
4. 先实现 `save_note` 一个文件工具，并为其写路径逃逸、确认指纹和并发修改测试。
5. 实现 PySide6 最小透明窗口和确认面板。
6. 把一次“搜索并保存”的完整任务跑通。
7. 再增加记忆提取，而不是先引入向量数据库。
8. 在端到端流程稳定后接入 Live2D、语音和主动陪伴。

这个顺序的重要性在于：先建立真实能力和安全边界，再增加人格表现。这样桌宠形象会增强已有能力，而不是掩盖尚未完成的 Agent 内核。

## 16. 参考代码索引

本地源码快照位于 `research/repos/`。

- N.E.K.O：`docs/zh-CN/architecture/memory-system.md`、`brain/computer_use.py`、`brain/task_executor.py`
- Coopanion：`core/persona.ts`、`core/notice.ts`、`docs/DEVELOPMENT.md`
- Cyrene-Agent：`src/main/permission-policy.ts`、`src/main/proactive/proactive-policy.ts`、`src/main/memory/memory-types.ts`
- shunguang-companion：`backend/app/filework.py`、`backend/app/memory.py`、`backend/app/credentials.py`、`backend/app/rag.py`
- AAAAGENT：`docs/MEMORY.md`、`code/desktop-pet/memory/dynamics.ts`、`code/desktop-pet/contracts/emotion-state.ts`、`code/desktop-pet/harness/receipts.ts`

许可证与复用边界见 [竞品调研](competitor-analysis.md)。
