# 数字分身第一版使用说明

## 已实现

- 桌面桌宠窗口，支持 PySide6 正式界面和 Tkinter 兼容界面。
- 陪伴与工作两种模式。
- 陪伴模式不允许网页搜索、文件写入和电脑执行工具。
- 工作模式支持“搜索关键词并保存为笔记”。
- 搜索后先生成写入提案，用户确认前不会修改文件。
- 确认内容与规范化动作参数通过 SHA-256 绑定，参数被篡改时拒绝执行。
- 文件只能写入用户授权目录下的 `notes/` 子目录。
- 支持路径逃逸检查、符号链接/目录联接检查、文件哈希冲突检测。
- 覆盖前创建备份，并支持撤销最近一次写入。
- SQLite 保存对话、动作提案、执行回执、记忆、共同经历和情绪快照，并按 `user_version` 自动迁移。
- 记忆以用户消息原文为证据，支持规范键、候选评分、纠正取代、查看和忘记。
- 召回优先使用中文 2/3-gram + FTS5/BM25；中文单字查询（如“茶”）自动走关键词兜底，召回日志记录来源与是否被回复采用。
- 遗忘会写入墓碑，阻止相同内容被重新召回或导入；由该事实派生的经历、反思和摘要同步失效。
- 记忆窗口展示来源原文、候选评分、生命周期事件与最近召回；人设事实可设为受保护，不会被自动取代。
- 旧聊天支持 JSON/JSONL 导入，导入前展示消息数、预计记忆数与耗时，支持暂停续跑和跨副本去重。
- 导入时，角色扮演会话（标题或开始/结束标记）只导入消息，不抽取用户事实；引用台词与假设句也不会被当作用户自述。
- 默认人设为《绝区零》蕾米埃尔·丹的本地 persona pack：稳定人格/背景走系统前缀，不写入用户记忆；角色事实不会被说成用户经历。
- 人设包可用 `--persona <id>` 切换；缺失或损坏时回退为朴素助手人格。
- “人设”面板可查看来源、许可、版本、大小与世界书条目，编辑称呼/关系/边界，或从本地目录导入人设包。
- 可选世界书按当前话题触发，只注入相关条目，不进入稳定前缀，也不写入用户记忆。
- 上下文分稳定层、会话前缀、运行时上下文与最近对话装配；稳定层哈希跨模式一致，模式与工具列表只进入会话前缀。
- 历史超过字符预算 82% 时保留最近完整轮次，较旧消息写成抽取式 checkpoint 摘要并复用；摘要可追溯到原消息，不生成新事实。
- 工具输出超过 16KB（UTF-8）时落盘到 `data/tool_results/`，模型只看到预览、字符数与句柄；`read_tool_result` 可按 offset/limit 分段回读，句柄仅限当前会话。
- 未回读的落盘内容不算已读：提示词、落盘通知与回读头部都禁止模型假设或声称读取未返回部分；存储不可用时退化为仅预览。
- 一次回复最多 3 轮工具调用、4 次调用；用满后仍会把最后一轮结果交给模型生成收尾回答，只有模型仍无输出时才提示“轮次达到上限”。
- 情绪状态分为用户估计和角色状态，并允许用户纠正。
- 关闭再打开后可以恢复历史会话和待确认动作。

## 启动

首次使用先双击项目根目录的 `setup.cmd`。安装脚本会创建 `.venv`、安装主依赖与自动化依赖、下载 Playwright Chromium，并运行环境诊断。

之后可以双击 `start_companion.cmd`，或运行：

```powershell
.\run.ps1
```

推荐 Python 3.11。当前代码兼容 Python 3.8。

```powershell
python companion.py
```

如果安装了 PySide6，会自动使用正式桌面界面；否则自动使用 Tkinter 兼容界面。

强制使用兼容界面：

```powershell
python companion.py --ui tk
```

强制离线演示：

```powershell
python companion.py --offline
```

切换本地人设包（默认 `remielle`）：

```powershell
python companion.py --persona remielle
```

命令行模式：

```powershell
python companion.py --ui cli --offline
```

命令行模式支持：

```text
/mode chat
/mode work
/workspace C:\path\to\notes
/memory
/affect
/undo
/quit
```

## 完成一次任务

1. 启动桌面界面。
2. 点击“工作”。
3. 点击“选择”，授权一个普通文件夹。
4. 输入：

```text
搜索 Python 桌面智能体并保存为笔记
```

5. 查看搜索结果和写入提案。
6. 点击“允许这次写入”。
7. 在授权目录的 `notes/` 下查看 Markdown 文件。
8. 点击“撤销最近写入”可以恢复写入前状态。

陪伴模式下提出同样任务时，分身会要求先切换到工作模式。

## 搜索与模型

网页搜索使用标准库请求 Bing RSS，失败后尝试 DuckDuckGo HTML。搜索和远程 LLM 都需要当前环境允许出站 HTTPS。

如果网络被拦截，界面会显示失败原因，不会创建文件提案。测试和离线开发可以使用注入的搜索后端，见 `tests/test_companion_runtime.py`。

远程 LLM 继续使用现有 `config/keys.json` 或环境变量：

```json
{
  "OPENAI_API_KEY": "你的 API Key",
  "OPENAI_BASE_URL": "https://your-provider.example/v1",
  "MODEL_NAME": "model-name"
}
```

第一版尚未把现有 `keys.json` 迁移到系统钥匙串。不要提交该文件，项目已在 `.gitignore` 中忽略它。

## 上下文预算

默认总预算为 **24,000 字符**，这是本项目的装配上限，不是模型原生窗口（qwen-plus 常见 32k/128k）。按中文粗估约 1.6 万–2.4 万 tokens。

预算内部分配：

- 稳定层（安全规则 + `persona.md`/`facts.md`）始终保留，不参与裁剪；不建议把人设文件写得很长，否则会挤掉对话历史。
- 用户称呼/关系/边界 ≤ 2,000 字符；世界书 ≤ 1,200；较早对话摘要 ≤ 1,200；召回记忆 ≤ 1,600。
- 剩余空间放最近完整对话，约占典型可用空间的 55%–100%。
- 历史占用可用预算 82% 时触发压缩，只丢最旧完整消息，摘要写入 `context_checkpoints`。

目前没有 CLI 或环境变量入口。以后需要调整时：

1. `CompanionRuntime.__init__` 已支持 `max_context_chars` 参数（下限 4,000），默认值在 `agent_framework/companion/context.py` 的 `DEFAULT_CONTEXT_CHARS`。
2. 启动入口 `companion.py` 走 `CompanionRuntime.from_config(...)`；需要给 `from_config` 增加同名参数并透传，或新增环境变量（例如 `COMPANION_CONTEXT_CHARS`）后再接进来。
3. 调整总预算时一并检查 `COMPRESS_TRIGGER`（默认 0.82）、`KEEP_RATIO`（默认 0.55）和 `MIN_RECENT_CHARS`（默认 4,000），避免压缩后保留窗口过小。
4. 预算按字符计算，不绑定具体 tokenizer；换模型后建议用实际文本抽查 token 占用。

工具结果落盘独立于对话预算：超过 16KB 的结果写入 `data/tool_results/`，模型上下文里只保留预览与句柄，因此不会因为一次大搜索直接撑爆窗口。落盘文件按会话隔离，单个存储副本上限 4MB；目前没有自动清理，需要时可手动删除该目录。

## 数据位置

默认数据目录：

```text
data/
├── companion.db
├── backups/
├── tool_results/   # 超过 16KB 的工具输出（JSON，按会话隔离）
└── personas/
    └── remielle/
        ├── persona.md
        ├── facts.md
        ├── worldbook.md
        └── persona.json
```

SQLite 使用 WAL 模式。备份只保存被覆盖文件的写入前内容，撤销时会再次校验哈希。人设包是稳定资料，不写入 `memories`；角色资料与用户事实分开保存。`tool_results/` 保存原始工具输出，可能包含网页正文等未校验内容，只用于当前会话回读。

可以通过参数指定独立数据目录：

```powershell
python companion.py --data-dir C:\path\to\companion-data
```

使用独立数据目录时，人设包也放在该目录的 `personas/<id>/` 下；找不到时回退为朴素助手人格。也可以复制 `data/personas/remielle/` 过去。

## 安全边界

- 工作目录不能是磁盘根目录或网络共享。
- 第一版只允许写 `.md` 和 `.txt` 文件。
- 第一版只允许写入授权目录下的 `notes/`。
- 禁止访问 `.git`、`.env`、凭据目录、符号链接和目录联接。
- 提案确认后只执行已经冻结的规范化参数。
- 同一条提案只能被一个窗口原子领取，避免重复执行。
- 文件在确认后被其他程序修改时停止写入。
- 撤销前会校验当前文件仍是执行器写入的版本。

## 尚未实现

- 任意 Shell 命令执行。
- 鼠标和键盘自动控制。
- Playwright 浏览器交互。
- 向量召回与多模态记忆。
- 旧聊天导出格式的自动识别（当前支持消息数组、含 `messages` 的会话对象和 JSONL）。
- 主动发言调度。
- Live2D、语音输入和语音合成。
- 操作系统钥匙串迁移。
- 打包安装程序。

这些能力应在现有动作状态机、记忆账本和权限策略上继续扩展。

## 验证

```powershell
python -m unittest discover -s tests -t tests -v
python -m compileall -q agent_framework companion.py tests
python -m agent_framework.companion.eval_recall
```

`tests/` 不是包，`-t tests` 不能省略（否则报 “Start directory is not importable”）。`eval_recall` 使用临时数据库和固定种子记忆，不调用模型；当前基线为 26 条用例 `hit@1=1.0000 hit@5=1.0000 mrr=1.0000`，调整分词、评分或衰减参数后应重跑并对比。

当前测试覆盖：

- 原有聊天与本地服务回归。
- 模式权限。
- 确认参数指纹。
- 确认前不写入。
- 取消任务。
- 文件冲突检测。
- 覆盖备份与撤销。
- 搜索笔记闭环。
- 共同经历和回执持久化。
- 记忆纠正与忘记。
- 情绪纠正。
- persona pack 稳定哈希、路径校验、提示分层、角色/用户事实隔离、删除人设不影响记忆。
- persona 导入预览/覆盖/超限拒绝、世界书按需检索、称呼/关系/边界 protected 设定与人设面板。
- 上下文分层与稳定哈希跨模式一致、字符预算、压缩 checkpoint 复用与迁移 v4。
- 大工具结果落盘（UTF-8 字节阈值）、预览不含尾部、分段回读、会话隔离与坏句柄拒绝、重启后可读、未回读守卫。
- 压缩轮次只写一条召回日志、回复引用记忆时 `used_in_reply` 置 1、中文单字查询关键词兜底。
- 召回评测集（26 条中文查询的 hit@5/MRR 基线）与用例形状覆盖。
- 工具轮次用满后仍能以最后一次工具结果收尾回答。
- Tkinter 界面创建冒烟测试。
