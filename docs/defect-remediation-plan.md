# 缺陷工程整改计划书（Defect Remediation Plan）

- 日期：2026-11-02
- 最近复核：2026-11-09（新增 F17–F20、Q10–Q14；实机复现写入卡死导致会话/提案互斥阻塞）
- 取证基线：`v1.57`（M15 交付后）、`data/companion.db` = `PRAGMA user_version 10`、HEAD = `c2e63c9`
- 范围：仓库本体（`agent_framework/`、`agent_service.py`、`doctor.py`、`tests/`）。`research/` 为参考项目，不在范围内。
- 状态：**旧 F1–F16/Q0–Q9 仍是历史待审计划；2026-11-09 的 F17–F20、F24、F26 已完成代码修复、回归和真实重启验证，F21–F23、F25 仍未完成。**（F1–F15 与 Q0–Q8 整体仍待审核；§1 的 820 基线与 §5 的 v10/v11 前提已被后续 M16/M17 改动，见 §11。）
- 编号说明：缺陷编号用 `F*`（finding），整改切片用 `Q*`。**编号校正（2026-11-03）**：本计划起草时建议的"里程碑 M16 = 缺陷整改轮、RAG+MCP 顺延 M17"**与实际相反**——RAG+MCP 已作为 **M16** 开工并交付了切片 1–3，其决策占用 **D82–D84**、风险最大到 **R28**。因此本计划的里程碑改称 **M17 缺陷整改轮**，新增决策从 **D85** 起、风险仍从 **R29** 起（详见 §8 的逐项映射）。**该编号安排需用户确认。**
- 2026-11-03 追加：**F16**（全量测试的验收命令被环境缺陷绑架）与 **Q9**（测试基建收尾），由 M16 切片 3 收口时的崩溃取证产生；用户决定「先写入缺陷工程计划，完成切片 4 后再考虑」。

## 0. 摘要

| 编号 | 优先级 | 用户/系统现象 | 根因（一句话） | 修复方向 | 迁移 |
| --- | --- | --- | --- | --- | --- |
| F1 | P0 | 遗忘一条事实后，再手动告诉它第二遍也永远记不住 | 墓碑命中只看 `session_id + content_hash`，`forget_epoch/through_seq` 存了但没人读 | 抽取门槛按"来源消息 seq 是否晚于 `through_seq`"判定，允许遗忘后重新学习并记 `relearned_after_forget` 事件 | 否 |
| F2 | P0 | 遗忘只在当前会话生效，切会话同一条事实复活 | 墓碑与级联都按会话作用域；`forget()` 的 canonical_key 级联只扫本会话 | 引入遗忘作用域（会话内 / 全局），全局时跨会话批量置 `forgotten` 并写会话无关墓碑 | 是（v11） |
| F3 | P1 | 中文场景上下文档位实际用量约为设定值的 2 倍 | char↔token 固定 `2.0`，而 `estimate_tokens` 对汉字按 1 token/字；后者在生产代码里**无人调用** | 预算与占用条统一改用 `estimate_tokens` 实测内容，删除固定换算因子的唯一真理地位 | 否 |
| F4 | P1 | 问"我不喜欢喝咖啡吗"高分命中"我喜欢喝咖啡"；自然的长句召回被阈值挡死 | `recall_score` 是 bag-of-n-gram 且按查询长度归一，无极性、无 IDF | 打分改为 BM25 相关度为主 + 长度不敏感归一；阈值默认值随分布重标 | 否 |
| F5 | P1 | `Ａ０１`、`１５分钟后`、`café` 类内容完全不可召回 | 分词正则只认 ASCII 词与 BMP 汉字，全角/扩展 A 区/带音拉丁被丢弃或截断 | 索引与查询共用同一分词器，先做 NFKC 归一 + casefold | 是（重建 FTS） |
| F6 | P1 | `晚上12点` 的提醒在中午 12 点响 | 12 点分支只在"凌晨/夜里"前缀下归 0 点 | 把"晚上/下午/傍晚 + 12 点"归到次日 00:00，补 11/12/1 三点边界 | 否 |
| F7 | P1 | 单条超长消息把上下文预算击穿 | `select_history` 反向遍历时 `kept` 为空不判预算，最新一条无条件保留 | 允许最新一条被截断为分段或整条入 `dropped`，并暴露 `over_budget` 标记 | 否 |
| F8 | P1 | `calculate` 一句 `9**9**8` 永久占住线程 | AST 白名单放开 `ast.Pow` 且不限指数位宽；原生 agent 循环调工具**没有超时** | 幂运算限制操作数/结果位宽，`ToolRegistry.execute` 接超时 | 否 |
| F9 | P1 | 工具超时后仍可能重复落库（重复提醒/重复笔记） | `queue.Empty` 时直接抛 `ToolTimeout`，daemon 线程既不取消也不 join，稍后照样提交副作用 | 带取消标志传入 handler、超时后 join 并把线程结果登记为"迟到提交"审计 | 否 |
| F10 | P1 | 本地任意网页可驱动 OpenAI 桥接服务 | `agent_service.py` 无令牌、无 `Origin`/`Host` 校验 | 复用 companion 已有的启动令牌 + 同源校验；默认 `--require-token` | 否 |
| F11 | P1 | 会话内两次压缩之间超过 400 条消息时，中间段静默永久丢失 | `list_messages_after` 默认 `limit=400` 且 `ORDER BY id DESC`，取最新 400 | 按 `dropped` 需求分页读全量，或让 checkpoint 触发条件与行数下限取严 | 否 |
| F12 | P1 | 重新生成中途失败/取消后，原回复与新回复同时不存在 | 模型调用**之前**就 `delete_message`，回滚只在 `except Exception` 且条件为"新流还没有 assistant" | 改为先生成、落库成功后再原子替换；失败保留原消息 | 否 |
| F13 | P2 | `hit@1=1.0000` 与实际体感不符 | 评测把用例自己的 seed 写进临时库再查它；生产库 `memories/messages` 均为 0 行 | 评测集加入干扰项与真实查询形状；补一条"真实库为空"的前置声明 | 否 |
| F14 | P2 | 820 个用例中约 13% 不执行行为 | 静态契约测试读取源码/CSS/JS 断子串 | 保留少数作为文案防漏，其余下沉为行为断言 | 否 |
| F15 | P0（流程） | 整个 v1.57/M15 只在工作区，一次磁盘事故即丢失 M13.3–M15 | 50 个脏路径，含 4 个全新模块与 3 个全新测试文件未跟踪 | 先建基线提交，再开始本计划（切片前置条件） | 否 |
| F16 | P1（验收面） | 普通 `unittest discover` 全量跑必崩（退出码 139），只有带 `gc.disable()` 的命令可用；`WinError 32` 随机毁掉临时目录清理 | Qt 侧：QObject 处在引用环里、被分代回收时撞上拆除中的 Qt 状态，关 GC 等于让析构永不发生；句柄侧：SSE/请求线程在 `adapter.stop()` 后可能仍在跑（`CompanionStore` 本身不持长连接，故"加 `store.close()`"的猜测已证伪） | 先取证再动测试基建：Qt 用例受控析构、Web 服务器/流线程确实退出；判据是**两种命令都全绿且数字相同** | 否 |

结论：横向功能没有虚报——全仓 `TODO/FIXME/NotImplementedError` 只有 `llm.py:48` 一处合法抽象基类；M15 的消费者（`apply_settings`、`memory_enabled`、`decide_tool_policy`、`restore_backup`、`purge_memories`）都真实存在并被行为用例覆盖。真正的问题集中在**记忆遗忘的语义闭环（F1/F2）、语言层打分与分词（F4/F5）、预算与历史完整性（F3/F7/F11）、执行边界（F8/F9）**四条线上。**2026-11-03 追加第五条线：验收面自身（F16）**——它不影响用户看到的行为，但决定"我们说测试全绿"这句话还能不能被复现。

## 1. 取证基线（可复现）

- 解释器：`.venv/Scripts/python.exe`（3.12.10）；系统 `python` 是 3.8.8 且无 PySide6，**必须带 `PYTHONPATH=.` 并用 venv**。
- 测试基线复核：`PYTHONPATH=. .venv/Scripts/python.exe -m unittest discover -s tests -t tests` → **Ran 820 tests, OK (skipped=3)**，175.4s。文档记录与实测一致，本计划不质疑该数字本身（但对它的构成见 F14）。
- 生产库取证：

```sql
SELECT count(*) FROM messages;          -- 0
SELECT count(*) FROM memories;          -- 0
SELECT count(*) FROM memory_fts;        -- 0
SELECT count(*) FROM recall_log;        -- 0
SELECT count(*) FROM memory_tombstones; -- 0
PRAGMA user_version;                    -- 10
```

- 静态契约占比统计（AST，判定为"测试方法调用了本文件内读取源码文本的辅助函数"）：**106 / 820**，集中在 `test_companion_web_style.py` 79/87、`test_companion_web_depth.py` 14/22、`test_companion_context_bar.py` 7/23、`test_companion_web_dialog.py` 3/8、`test_companion_web_window.py` 3/12。
- 本计划的取证脚本与临时文件已删除，未改动任何项目文件，未新增常驻进程。
- **2026-11-03 复校（F16 之后本行的效力）**：上面的 `Ran 820, OK` 与 `discover` 命令在当时的用例总量下确实成立；到 M16 切片 3 的 **997** 个用例，同一条命令在本机改为**必现崩溃（exit 139）**，权威验收命令变成 `PYTHONPATH=. QT_QPA_PLATFORM=offscreen python -c "import gc,unittest; gc.disable(); unittest.main(module=None, argv=['x','discover','-s','tests','-t','tests'])"` → `Ran 997 tests / OK (skipped=3) / exit 0`。详见 F16 与 Q9。

## 2. 根因分析

### F1 遗忘后永久拉黑同一事实

**命中代码**：`agent_framework/companion/memory.py:248-252`、`agent_framework/companion/store.py:2521`

```python
if self.store.content_tombstoned(
    session_id,
    _content_hash(item["content"]),
):
    continue
```

`forget()`（`memory.py:528`）在写入时算好了 `epoch = 当前毫秒` 与 `through_seq = max_message_id(session)`（`memory.py:532-533`），语义正是"截至该消息序号为止的这条内容是遗忘的"。但读侧只传两个参数，命中即拦，`through_seq` 形同虚设。

**实测**：`请记住：我喜欢喝咖啡。` → 抽得 1 条 → `forget()` → 再说同一句 → 新建 **0** 条，`recall('咖啡')` → `[]`。此后无论用户重复多少次都无效，且 `remember_experience` 走同一门槛。

**修复**：
1. `content_tombstoned(session_id, content_hash, source_message_id)`，命中条件收紧为 `tombstone.through_seq >= source_message_id`。
2. 重新学习成功时写 `memory_events(type='relearned_after_forget', {tombstone_epoch})`，管理台可见。
3. 同一改动自然覆盖 `observe_user_message` 与 `remember_experience` 两个入口，不改抽取规则本身。

**边界**：若用户在遗忘前发的消息被重新导入（`importer.py` 续跑），`source_message_id` 可能小于 `through_seq` 而被正确拦下——这是期望行为，需在用例里显式断言。

### F2 遗忘跨会话失效

**命中代码**：`memory.py:391`（`list_memory_tombstones(session_id)`）、`memory.py:566-595`（canonical_key 级联只 `list_memories(memory.session_id, limit=500)`）、`store.py:2533`。

**实测**：同一句在 `sA`/`sB` 各存一条 → 在 `sA` 遗忘 → `sA 召回 0 / sB 召回 1`。

**修复**：`forget(memory_id, scope="session" | "global")`。
- `global`：遍历所有会话中 `content_hash`/`canonical_key` 命中的 `confirmed/candidate` 行，逐行 `status='forgotten'` + `forget_experience_by_memory` + `add_memory_event` + 写墓碑；墓碑表新增作用域列（`scope` 或允许 `session_id IS NULL` 表示全库），迁移 **v11**。
- 读侧 `list_memory_tombstones(session_id)` 返回"本会话 + 全局"并集。
- UI/管理台必须显式选择作用域，默认仍为 `session`，避免一次误点清库。

### F3 char↔token 换算：准确估算器是死代码

**命中代码**：`tokens.py:15`（`CHARS_PER_TOKEN = 2.0`）、`tokens.py:38-46`（`estimate_tokens`：CJK 1 token/字，ASCII 4 字符/1 token）、`tokens.py:49-54`；消费方 `runtime.py:327-329`（`max_context_chars = max(4000, tokens_to_chars(context_tokens))`）、`app/projection.py:66`（占用条 `chars_to_tokens(rounded)`）。

全仓 grep 确认 **`estimate_tokens` 没有任何生产调用**，只有 `tokens_to_chars/chars_to_tokens` 这对固定因子被用。

**实测**：`tokens_to_chars(4096) = 8192`；`estimate_tokens("咖"*4096) = 4096`。即 8192 个汉字的真实规模约 8192 token，而档位设定是 4096。占用条与预算**彼此自洽**（都除以 2.0），因此界面看起来正常，但两者相对上游真实 token 数一致低估约 2 倍。

**修复**：`apply_settings` 保留用户语义为"token 档位"，但把内容计量改为对实际串跑 `estimate_tokens`；`projection.py` 的占用上报同式。固定因子退化为 `estimate_tokens` 缺失时的兜底。风险是既有会话的历史预算表现变化，需在 §5 里做灰度对照。

### F4 召回打分无极性、按查询长度归一

**命中代码**：`memory.py:115-132`。`_recall_via_fts`（`memory.py:415-453`）拿 BM25 命中集后**丢弃 BM25 分数**，用 `recall_score` 重排，因此排序与索引相关度脱钩。

**实测**：`recall_score('我不喜欢喝咖啡吗？','我喜欢喝咖啡',1.0) = 0.571`；`recall_score('我今天想喝点什么，咖啡还是茶','我喜欢喝咖啡',1.0) = 0.091`。后者低于 `runtime.py:2325` 的阈值即被完全过滤。

**修复**：以 BM25 为主键、`recall_score` 仅作衰减/重要度微调；归一分母改用 `min(查询词数, 命中词数)` 而非查询长度；`memory_score_threshold` 依新分布重标默认值，并做一次真实语料抽样校准（不能用 F13 的合成集）。

### F5 分词字符覆盖

**命中代码**：`textseg.py:20`（`TOKENS` 正则只认 `[a-zA-Z0-9_]` 与 `一-鿿`）、`textseg.py:40-46`（`_chinese_ngrams` 产 2/3-gram）、`memory.py:84-87`（打分侧 `tokenize` 只产 2-gram，两侧词形不一致）。

**实测**：`segment_tokens('Ａ０１') = []`、`segment_tokens('café') = ['caf']`、`segment_tokens('我㐀是') = ['我','是']`、`fts_segment('１５分钟后') = '分钟 钟后 分钟后'`（数字丢失）。另需注意：索引端产 3-gram、`recall_score` 端不产，两侧词形不一致是排序抖动的来源之一。

**修复**：`segment_tokens`/`tokenize` 共用一个函数；入口先 `unicodedata.normalize('NFKC', ...)`（顺带把全角 `Ａ０１` 归一成 `A01`），再 casefold；CJK 正则扩到 CJK Ext-A（`U+3400–4DBF` 已在 `tokens._CJK_RANGES` 里，分词侧却没有）；带音拉丁改用 `NFD` 去声符或整体按 word 切，不再截断。改动会使既有索引失配 → 走 `rebuild_memory_fts` 一次性重建（§5）。

### F6 `晚上12点` 解析成正午

**命中代码**：`proactive/reminders.py:132-137`。`:132` 的时段位移只覆盖 `1 <= hour <= 11`，`:136` 的 12 点归零又只挂在"凌晨/夜里"上，`晚上 + 12` 两条都不接。

**实测**：`resolve_when('晚上12点', now=2026-11-02 09:00) → 2026-11-02 12:00`（应为次日 00:00）；`凌晨12点 → 11-03 00:00`、`中午12点 → 11-02 12:00` 正确。

**修复**：`{晚上,夜里,傍晚,深夜,下午} + 12` → 次日 00:00；补 `1` 点的上下午歧义用例。次级问题：`_clock_due`（`reminders.py:138-142`）把 `astimezone()` 得到的固定偏移跨 `timedelta(days=1)` 携带，跨夏令时边界会差一小时——中国无 DST，但对海外时区用户是真实缺陷，修复时改为"按本地日历日构造再 astimezone"。

### F7 上下文预算被单条消息击穿

**命中代码**：`context.py:160-166`

```python
for item in reversed(items):
    size = message_chars(item)
    if kept and used + size > target:
        break
    kept.append(item)
```

`kept` 为空时不判预算，最新一条无条件入窗口。

**修复**：保留"至少一条"以便对话不断裂，但超预算的那条应走分段（复用 `tool_results` 的落盘+预览机制）或整条计入 `dropped` 并在投影里给出 `over_budget=True` 提示；`min_recent` 语义同步收紧。

### F8 `calculate` 无界幂 + 工具调用无超时

**命中代码**：`agent_framework/tools.py:59-96`（`ast.Pow` 在 `_ALLOWED_BINARY_OPERATORS` 白名单中，`:66`，且 `_eval_expression` 不限指数位宽）、`agent_framework/agent.py:59`（`self.tools.execute(...)` 无超时包裹）、`llm.py:75-86`（离线分类器会把用户串里的算式直接变成 `calculate` 工具调用，因此模型可控）。

**实测**：经 `ToolRegistry.execute` 调用，`9**9**7` 耗时 2.35s；`9**9**8` 在 40s 硬超时下**未返回**（被外部 kill）。`2**100000` 与 `2**9999999` 只在 `str()` 阶段撞上 Python 的 4300 位整转字符串限制，计算本身已完成——所以真正的 DoS 面是大指数幂的**计算与内存增长**，不是格式化。

**修复**：`_eval_expression` 对 `Pow` 加约束——底数与指数的绝对值上限（如 `|exp| <= 64`、`|base| <= 10^6`），并对结果位数设 `sys.int_info.default_max_str_digits` 一致的上限；超限返回"表达式超出可计算范围"而非抛异常。同时给 `ToolRegistry.execute` 加可配置超时（复用 F9 的机制），保证即使有漏网算式也不钉死线程。

### F9 工具超时后孤儿线程照样提交副作用

**命中代码**：`agent_framework/companion/tools.py:28-42`

```python
thread = threading.Thread(target=worker, name="companion-tool", daemon=True)
thread.start()
try:
    ok, value = outcome.get(timeout=timeout)
except queue.Empty:
    raise ToolTimeout()
```

超时后不取消、不 join。

**失效场景**：`set_reminder` 或 `write_note` 超过预算 → 模型收到 `ToolTimeout` 并重试 → 第一次那个线程仍在跑，随后把 DB 行写第二遍。表现为重复提醒/重复笔记，而 `execution_ledger` 里只有一次记录，无从对账。

**修复**：
1. handler 签名接收 `cancel_event`，长任务协作式检查。
2. 超时后置位取消标志并**有界 join**；join 失败则该工具结果标记为 `unknown`，写 `skill_audit`/`execution_ledger` 一条"迟到提交待确认"记录。
3. 对**非幂等**写类工具（`set_reminder`/`write_note`）超时后不自动重试，回执明确告知用户"可能已生效，请在管理台确认"。幂等读类工具可自由重试。

### F10 `agent_service.py` 无鉴权

**命中代码**：`agent_service.py:35-66`（无令牌、无 `Origin`/`Host` 校验）、`:83`（`--host` 默认 `127.0.0.1`，但可被改）。对照 `web/server.py:1151,1173` 已有 `secrets.compare_digest` 令牌校验、`web/__init__.py` + `activation.py:15-24` 已有端点令牌落盘与到期删除机制。

**失效场景**：用户本地任意网页一个 `<form enctype="text/plain" action="http://127.0.0.1:8765/v1/chat/completions">` 即可驱动这条链路——消耗 API 额度、往 `ConversationMemory`/存储会话里投毒。若页面还能读到响应（DNS rebinding 后同源），则直接读到助手输出。

**修复**：抽出 `web/server.py` 里的同源+令牌校验为共用中间件，`agent_service.py` 默认启用；无令牌启动必须显式 `--insecure` 且打印警告。顺带把 `--host` 非回环值变为需要显式确认。

### F11 400 行历史窗口静默截断

**命中代码**：`store.py:1008-1030`（`list_messages_after(..., limit=400)`，`ORDER BY id DESC LIMIT ?`，再 `reversed`）、`runtime.py:2256`（`_build_prompt` 用默认 limit 调用）。

**失效场景**：两次 checkpoint 之间累积超过 400 条消息（`COMPRESS_TRIGGER` 是按字符触发的，短消息场景下 400 行会先到），最旧的未摘要部分既不进 prompt，也进不了 `summarize_dropped`（`runtime.py:2281-2284`）——与字符预算无关的静默永久丢失。

**修复**：`_build_prompt` 显式传 `limit`，并按 `after_id` 游标分页读全量后在内存里做窗口选择；或让触发条件取 `max(字符阈值, 行数阈值)`，保证行数不超过窗口。二者都做，并加一条"400+ 条短消息会话不丢历史"的行为用例。

### F12 重新生成先删后生成

**命中代码**：`app/service.py:268-279`（发起流之前 `delete_message`）、`:337-339`（回滚仅在 `except Exception`，且条件 `not self._has_assistant_after(run)`）、`:599-604`。

**判定说明**：`_has_assistant_after` 一旦为真就跳过回滚，此时原回复已删、新回复是残缺的；`except Exception` 也接不住 `GeneratorExit`/进程被杀。综合定级 P1 而非 P0，因为常规取消路径（`cancel_event` → 抛 `Exception` 子类）仍能回滚。

**修复**：改为"先生成到临时草稿，流成功后在同一事务里替换原消息"。保留旧行到 `data/backups` 或一张 `message_replacements` 表，失败即回指。这条与 M15 的 `session_drafts` 已有底座相邻，应复用而不是新建机制。

### F13 评测的闭环自证

`eval_recall.py:103-112` 把每个用例自己的 `seed` 写进 `TemporaryDirectory` 库再查它。26 条查询彼此不竞争，因此 `hit@1=1.0000` 只证明"检索链路通"，不证明"排序好"。叠加生产库 0 条记忆（§1），当前所有召回指标都没有真实数据支撑。

**修复**：评测集加干扰项密度（建议同主题 50–200 条）、加入真实查询形状（整句、含否定、含全角），并把指标拆成两栏对外表述：**合成链路指标** vs **真实语料排序指标**（后者在 F4/F5 修复前标为"未测"）。文档 §0 与 README 的"1.0" headline 需按此改写。

### F14 静态契约测试占比

**量化**：106/820（约 13%）。这类用例断言"源码里出现过某段文本"，改实现不改文案会红、改坏了行为也不会红。

**修复方向**：不一刀切删除。分层为（a）确属防漏的少数（如"全部页面脚本必须调用 `beginWindowDrag`"）保留并集中登记；（b）其余逐步替换为对渲染结果/返回值的断言。本计划只做**指标口径**修正：文档中的测试数按"行为用例 / 静态契约 / 环境跳过"三栏披露。

### F15 未提交的 v1.57/M15 工作

`git status` 50 个脏路径，其中**未跟踪的 4 个生产模块**：`companion/settings.py`、`companion/tokens.py`、`web/upstream.py`、`web/assets/context_bar.js`，以及 3 个新测试文件（`test_companion_settings.py`、`test_companion_context_bar.py`、`test_companion_web_zones.py`）；`runtime.py`/`store.py`/`memory.py` 等均为 M。`data/companion.db` 已正确 gitignore。

**约定冲突提示**：用户规则是"未经明确要求不得 `git commit`"。因此本计划**不自行提交**，只把"建立基线提交"列为所有切片的**前置动作**，由用户执行或明确授权后执行。

### F16 全量测试的验收命令被环境缺陷绑架

**现象（2026-11-03，M16 切片 3 起）**：用例总量到 997 后，`PYTHONPATH=. QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m unittest discover -s tests -t tests` 在本机 PySide6 6.12 + Python 3.12.6 下**必现**崩溃、退出码 139；两次分别是 `Fatal Python error: Aborted` 与 `Windows fatal exception: access violation`，落点用例都是 `test_companion_context_bar.BudgetHttpTests.test_settings_carries_the_bands_and_the_conversion_rate`——**按字母序还没轮到 MCP 用例**。当前唯一的可用验收命令是关 GC 的 `python -c "import gc,unittest; gc.disable(); unittest.main(...)"`（997 全绿）。

> 取证诚实度：**本轮两次的完整栈未逐帧留存**（全量日志用后即删），"顶部帧是 `chat_view.resizeEvent`"这句来自 M13.3 已登记的同形崩溃记录，不是对 11-03 那两帧的直接引用。Q9 重做取证时必须把 faulthandler 原文落到可回看的文件里，否则这条根因判断永远只是推定。

**取证链（已复核）**：单跑该模块 27 个通过；前六个模块 100 个通过；**排除 `tests/test_companion_mcp.py` 的其余 916 个稳定通过**；`gc.disable()` 后 997 个全绿；崩溃栈里没有任何 `mcp` 帧。与 M13.3 登记的"完整 discover 下偶发 shiboken/Qt GC abort"同形——**用例总量一变、GC 时机一变就换个地点炸**，所以它不是切片 3 引入的缺陷，但被切片 3 从"偶发"推成"必现"。

**根因的两条腿（一条已核实，一条待取证）**：
- **Qt 侧（机制已明确）**：`gc.disable()` 只停止**循环垃圾**回收，引用计数照常。因此崩溃的触发条件是"某个 QObject 处在引用环里、被分代回收时撞上 Qt 正在拆除的状态"，关掉 GC 等于让那批对象活到进程退出、析构器永不运行。**这不是"测试写错了"，而是产品侧的对象生命周期事实**：`ui/chat_view.py` 的 `resizeEvent` 说明被回收的是仍在事件链上的窗口/视图对象。
- **句柄侧（此前的一条推断已被证伪，另一条待取证）**：`CompanionStore` **不持有长连接**——`store.py:719-728` 的 `_connection()` 是每次操作 `sqlite3.connect` + `finally: close()`。因此切片 3 收口报告里"修法是给 fixture 显式 `store.close()`"**不成立**，那条建议作废。偶发的 `PermissionError [WinError 32]`（M16 切片 1 记在 `test_companion_web_queue` 的 tearDown、M13.1 也记过一次同名噪声）只能来自**别的线程正好在查询中途**（`ThreadingHTTPServer` 是每连接一线程，`adapter.stop()` 只保证不再接新请求，不保证 daemon 处理线程已退出；SSE 的 `_stream()` 那条尤其会阻塞到客户端断开），或来自 Windows 对刚关闭句柄/`-wal` 的删除延迟。**当前没有证据区分这两者**，所以 Q9 的第一步是取证而不是改代码。

**影响**：① 权威验收信号变弱——关掉 GC 会**同时**关掉这一类崩溃的报警，产品里真实的"对象不析构"缺陷（内存缓慢增长、Qt 对象滞留）从此不会以崩溃暴露；② 基线数 `997 / 3 skip` 变成"特定命令 + 特定机器"的函数，换机可能 `gc.disable()` 也崩（那就完全没有可用命令），也可能普通 discover 常绿（失去可比性）；③ 任何把退出码当门控的自动化（pre-commit、发布自检）不可用；④ 崩溃点随用例总数漂移，每加一批用例就诱发一次"以为是新模块回归"的重复取证；⑤ 遮蔽面随切片累积——本轮 81 条 MCP 用例在普通跑法下**一条都没执行**就被跳过。

**修复方向（先取证，再动测试基建；不在功能切片里顺手做）**：
1. 先复现并定位：`-k` 逐段缩小 + faulthandler 落到文件，确认崩溃是否只在"窗口类用例跑完后的一次 GC"发生；同时给 `WinError 32` 抓到持有句柄的线程名。
2. Qt 用例统一收尾：`tearDown` 里 `window.close()` + `processEvents()`，让析构在受控时刻发生，而不是留给 GC；M14 S4 已实测"清理里调 `deleteLater()` 会在解释器退出时段错误"，收尾顺序要按这条既有事实设计。
3. Web 用例的服务器与流：`adapter.stop()` 需保证 SSE 线程退出（加一个 join/超时），临时目录改 `ignore_cleanup_errors=True` 只作为兜底、不作为答案。
4. 判据不是"跑完不崩"，而是**普通 `discover` 全绿 + `gc.disable()` 也全绿**，两者数字相同；否则视为未修。

**排期**：用户 2026-11-03 决定**先入档，等 M16 切片 4 完成后再考虑**；期间文档必须继续写明那条带 `gc.disable()` 的验收命令，不得让人按普通 discover 的 139 退出码判定回归。

**2026-11-05 的状态更新（S1 验收轮）**：带 `gc.disable()` 的全量 1126 条（225.1 秒）**未复现** `WinError 32`，`test_companion_web_dialog.py` 单跑与全跑都绿——这条竞态仍是"随机"，不能作为"已自愈"的结论。同时按用户「对项目没影响的就清掉吧」删除了仓库根 18 个 `tmp*` 测试残留目录（临时 `companion.db` / `ui_state.json` / `tool_results` / 假 `mcp_keys.json`）与 `debug.log`：**F16 句柄侧的既有现场就此消失**，下次取证要自己重新制造（`-k` 分段 + faulthandler 落文件的步骤不变），别再去翻那几个目录。

## 3. 已排除的疑似缺陷（复核结论）

以下条目由初步排查提出，我逐条读码后判定**不成立**，不进入整改范围：

1. **"820 个用例不可复现 / 实跑 FAILED"** — 不成立。用错解释器（系统 3.8.8 且无 venv）导致。用 `.venv` + `PYTHONPATH=.` 实跑得 `Ran 820 tests, OK (skipped=3)`，与文档一致。
2. **"`persona_lines` 不过滤墓碑，遗忘的事实仍进系统提示"** — 不成立。`memory.py:660-690` 两次取数都限定 `statuses=["confirmed"]`（`:667`、`:679`），而 `forget()` 把行置为 `status='forgotten'`（`:541`），已被排除。真正的残留风险只在 F2 的跨会话维度。
3. **"BM25 前 30 候选窗口把正确记忆挤出且不降级"** — 未复现。构造 120 条含"咖啡"的干扰记忆 + 1 条目标，`recall('手冲咖啡')` 首位命中目标；把前 25 条改为 `candidate` 后 `recall('咖啡')` 仍返回 5 条。`FTS_CANDIDATE_LIMIT = 30`（`memory.py:386`）与"窗口后才过滤 `status`/墓碑"（`memory.py:440-442`）确实会让结果数缩水，但在可构造的规模内没有丢失目标。**列为观察项**，若 F4 重排后仍出现缩水再回来处理。
4. **"`_recall_via_keywords` 只扫最新 200 条消息"** — 描述有误。`memory.py:457-461` 扫的是 `list_memories(..., limit=200)`，即**记忆行**而非消息。构造 1 条记忆 + 250 条消息后 FTS 关闭，`recall('团子')` 仍命中。**未复现**，但 200 条记忆上限本身仍是隐患（长寿命用户的记忆行数会越过它），归入 F4 的"排序与窗口"一并重审。
5. **"仓库内存在提示注入文本"** — 不成立。全仓 grep（排除 `research/`、`.venv/`）无命中；该报告是把运行环境的技能清单提示误判为攻击。

## 4. 整改切片

依赖关系：`Q0 → Q1 → {Q2, Q3}`，`Q4`/`Q5` 无前置可并行，`Q6 → Q7`（打分改动需要 F5 的词形统一先落地）。`Q9` 无代码前置，但**用户已决定排在 M16 切片 4 之后**，不与功能切片争同一次改动（它会动 10 多个测试文件的收尾，混进功能切片就分不清谁改红了用例）。

| 切片 | 内容 | 覆盖缺陷 | 迁移 | 验收 |
| --- | --- | --- | --- | --- |
| **Q0 基线保护** | 与用户确认后提交 v1.57/M15 全部工作；本计划文件入档 | F15 | — | 工作树干净；测试 820 基线不变 |
| **Q1 遗忘语义闭环** | 墓碑读 `through_seq`、遗忘作用域参数、v11 作用域列、管理台作用域选择与二次确认 | F1 F2 | v11 | 见 §6 用例 U1–U6 |
| **Q4 执行边界** | `Pow` 位宽约束 + `ToolRegistry.execute` 超时；`companion/tools.py` 取消标志、有界 join、非幂等工具不自动重试 + 迟到提交审计 | F8 F9 | 否 | `9**9**8` 在 200ms 内返回错误；超时后无重复行 |
| **Q5 暴露面收口** | 抽同源+令牌校验中间件，`agent_service.py` 默认启用；`--host` 非回环需确认 | F10 | 否 | 无令牌 POST → 401；跨源表单 POST → 拒绝 |
| **Q3 预算与历史完整性** | `estimate_tokens` 接管计量、`select_history` 超窗策略、`list_messages_after` 分页 | F3 F7 F11 | 否 | 4096 档下真实 token 估算误差 <20%；400+ 短消息会话不丢历史 |
| **Q6 时间语义** | `晚上12点` 与 11/12/1 歧义、DST 安全的次日构造 | F6 | 否 | 时区×短语矩阵全绿 |
| **Q7 重新生成数据保全** | 先生成后原子替换，复用 `session_drafts` | F12 | 否 | 取消/异常/进程中断三路都不丢原回复 |
| **Q2 语言层召回** | NFKC 归一 + 统一分词器 + 3-gram 两侧一致 + BM25 主导重排 + 阈值重标 | F4 F5 | 重建 FTS | §6 用例 U7–U11；重建后召回不回退 |
| **Q8 度量卫生** | 评测集加干扰与真实查询形状；指标双栏；测试三栏披露 | F13 F14 | 否 | 新基线可在真实语料上复算 |
| **Q9 测试基建收尾** | 先取证（崩溃落在哪次 GC、`WinError 32` 时哪根线程持句柄），再统一 Qt 用例的受控析构与 Web 服务器/SSE 线程的退出；临时目录 `ignore_cleanup_errors` 只作兜底 | F16 | 否 | **普通 `discover` 与 `gc.disable()` 两条命令都全绿且数字相同**；连续 3 次全量无 139、无 `WinError 32`。排期：**M16 切片 4 完成之后再考虑**（用户 2026-11-03 决定），期间文档继续写明带 `gc.disable()` 的验收命令 |

## 5. 迁移、重建与数据影响

- **迁移代际已被占用（2026-11-03 校正）**：本计划当时写的 **v11** 是"留给遗忘作用域列"的，但 `SCHEMA_VERSION` 现已是 **11**——被 M16 切片 1 的文档库（`documents`/`doc_chunks`/`doc_fts` + `recall_log.scope`）占用，真实库也已由用户自己的实例迁到 v11。因此下面 (a)/(b) 两种取向**都改成 v12**，§4 的 Q1 行与 §7 的"v11 迁移"行同步按 v12 理解；§1 记的 `user_version 10` 与"生产库 0 行"同样是 M15 末期的快照，做 Q1/Q2 前需重新取证（真实库现在至少有一张 `documents` 表，是否仍 0 行未复核）。

- **v10 → v11**（**2026-11-03 起应按 v11 → v12 理解**，理由见上一条）：`memory_tombstones` 增加作用域表达。两种实现取向，建议后者：
  - (a) 新增 `scope TEXT NOT NULL DEFAULT 'session'`，全局行 `session_id` 填空串——语义清楚但读侧要防"空串会话"歧义；
  - (b) 允许 `session_id IS NULL` 表示全库墓碑——读侧 `WHERE session_id = ? OR session_id IS NULL` 一行搞定，且现有唯一索引需调整为按 `(session_id, content_hash)` 且容忍 NULL。
  迁移必须走既有 `pre-migration` 备份路径，并且**修复 `store.py:685-700` 的"备份失败只记 `migration_backup_error` 却继续往下 ALTER"**——本计划把它纳入 Q1 前置：备份不成就拒绝迁移。
- **FTS 重建**：F5 改词形后旧索引必然失配。沿用启动自愈与 `rebuild_memory_fts`，但需要一次性重建 + 一条 `doctor.py` 检查项报告"索引代际"，避免半新半旧。
- **不改** `ui_state.json` 结构；`session_drafts` 只复用不扩表。
- **真实库为 0 行**（§1），因此上述改动在开发机上无法用生产数据回归——Q1/Q2 的验收必须在合成语料上做，并明确写入文档"未在真实用户语料验证"。

## 6. 验收用例（行为级，不用静态契约）

| 编号 | 场景 | 断言 |
| --- | --- | --- |
| U1 | 抽取 → 遗忘 → 同一句再说 | 新建 ≥1 条；`recall` 命中；`memory_events` 含 `relearned_after_forget` |
| U2 | 遗忘前发送的消息被重新导入 | 不重建记忆（尊重 `through_seq`） |
| U3 | 同句存于 sA/sB，sA 内作用域遗忘 | sA 0 命中、sB 1 命中（现状即正确，防回归） |
| U4 | 同句存于 sA/sB，全局作用域遗忘 | 两会话均 0 命中；FTS 与 `persona_lines` 均无残留 |
| U5 | 全局遗忘后重开新会话再告知 | 能重新学习 |
| U6 | 备份目录不可写时执行 v11 | 拒绝迁移、`user_version` 仍为 10、库无损坏 |
| U7 | `recall('我不喜欢喝咖啡吗？')` vs 记忆"我喜欢喝咖啡" | 不进入前 5 或带 `polarity_mismatch` 标记 |
| U8 | 全角/Ext-A/带音输入（`Ａ０１`、`café`、`我㐀是`） | 索引与查询两侧词形一致且可召回 |
| U9 | 120 条同主题干扰记忆 + 目标记忆 | `recall` 首位为目标；`last_recall_source` 与实际路径一致 |
| U10 | `resolve_when` 短语×时区矩阵（含 11/12/1 点、海外 DST） | 期望时刻逐条相等 |
| U11 | `calculate` 边界（`9**9**8`、`2**100000`、`1/0`、嵌套括号溢出） | 全部在超时前返回错误字符串，无未捕获异常 |
| U12 | 非幂等工具超时 | 有界 join；无第二次落库；回执状态为 `unknown` 且引导确认 |
| U13 | 400+ 条短消息会话 | prompt 覆盖全量或全量进摘要，无静默缺口 |
| U14 | 重新生成中途取消/异常/进程中断 ×3 | 原回复可恢复，不出现双缺 |
| U15 | 全量连跑 3 次，分别用普通 `discover` 与 `gc.disable()` 两条命令 | 两次**数字相同**且都 `OK`；无 exit 139、无 `Fatal Python error`、无 `WinError 32` |
| U16 | 崩溃与句柄取证本身（Q9 的第一步，先于任何收尾改动） | 留下可复现的最小证据：崩在哪一次分代回收、哪个 QObject 处于环中；`WinError 32` 时持句柄线程的名字与栈。取证不成立则**不动测试基建**，避免把环境问题改成测试假绿 |

## 7. 风险与回滚

| 风险 | 影响 | 缓解 | 回滚粒度 |
| --- | --- | --- | --- |
| F5 改分词使旧索引失效 | 一次全量重建期间召回降级 | 双代际标记，重建完成前不放量 | 词形函数单独回退 |
| F4 重排改变召回顺序 | 用户已习惯的记忆表现化 | 保留旧打分作为并行特征一版，指标对照后再切 | 阈值+权重配置化 |
| F3 计量口径变严 | 同样档位下更早触发压缩 | 灰度：新旧并行估算并打日志对照 | `estimate_tokens` 单点替换，易回退 |
| F9 非幂等工具超时不重试 | 用户感知"提醒可能没设上" | 回执明示 `unknown` + 管理台一键确认 | 策略开关 |
| F10 加鉴权 | 已有外部集成脚本失配 | `--insecure` 兼容一版并打印弃用警告 | 服务入口参数 |
| F2 全局遗忘 | 误操作造成跨会话不可逆清除 | 默认会话内 + 二次确认 + 备份前置 | 作用域默认值 |
| v11 迁移 | 库结构变更 | U6 用例 + 迁移前强制成功备份 | 从 `pre-migration` 备份还原 |
| Q9 改测试收尾顺序 | 动 10 多个文件的 `tearDown`，可能把"靠 GC 时机侥幸通过"的用例改红；M14 S4 已实测清理里 `deleteLater()` 会在退出时段错误 | 先取证再改；一次只改一类页面（Qt 窗口 / Web 服务器）并各自跑全量对照；判据锁定"两条命令数字相同" | 按文件回退，`gc.disable()` 命令在整个过程中始终可用 |

## 8. 文档与决策更新清单（每片交付时同步）

- `docs/progress.md`：新增 **M17 缺陷整改轮**条目（计划 → 交付）。**编号校正（2026-11-03）**：progress.md 现最大决策为 **D84**（M16 切片 1/2/3 已占用 D82–D84）、最大风险为 **R28**，因此本计划的决策号整体后移——**D85**（墓碑按 `through_seq` 判定可重学）、**D86**（遗忘作用域与会话内默认）、**D87**（上下文计量改用 `estimate_tokens`）、**D88**（非幂等工具超时不自动重试）、**D89**（召回指标双栏披露）、**D90**（Q9：测试收尾受控析构 + 两条命令同数字判据）；风险 **R29**（真实语料未覆盖召回评测）、**R30**（v11 迁移依赖备份可用性）。
- **F16/Q9 的交付同步**：`docs/progress.md` §2.1 撤掉"验收必须带 `gc.disable()`"那条命令级妥协（改为常规 `discover`），并在 §11 记一次"崩溃从必现回到零"；`docs/v1-usage.md` 与 README 的测试数量表述随之去掉 `gc` 前缀。**若取证后判定不可修**（例如 PySide6 版本的既有缺陷），则**不假装修好**：把结论写进 §11 与本文件，验收命令保留 `gc.disable()` 形式并明确记录它遮蔽了哪一类信号（对象不析构）。
- `README.md`、`docs/v1-usage.md`：遗忘作用域的用户可见语义、`agent_service.py` 需要令牌、提醒时间解析修正。
- `docs/rag-mcp-plan.md`：里程碑顺延为 M17，并把该计划自认的"上传体无上限"与本计划 §5 交叉引用。
- 不改 §0 结论中"92–95% / 72–77%"口径的写法，但**必须**补一句"召回指标当前为合成语料指标"（F13）。

## 9. 待下轮取证（本计划未定级）

这些条目由初步排查提出，方向合理但我尚未独立复核，**不作为整改依据**，需专项取证后再定级：

1. `doctor.py` 在依赖齐备机器上是否真能"全项 OK"；本机依赖不全，报 3 FAIL，不能据此判定文档造假。
2. Tk 相对 Qt 的功能落后幅度（约 10 个里程碑的说法需按关键字逐个核对入口，而非行计数）。
3. `web/shell.py:9-12` 无条件 `QTWEBENGINE_DISABLE_SANDBOX=1`，而页面渲染模型输出。
4. `skills/library.py:242-244` 的 references 目录未拒符号链接，与 `file_actions.py` 的 `_assert_not_link` 不一致——同类防护两处不同标准。
5. ~~`web/server.py:854-856` 的 `Content-Length` 无上限/可为负~~ **已于 2026-11-03 关闭**：M16 切片 1 加了 `MAX_REQUEST_BYTES = 24 MiB`（`server.py:47`），非法值 → 400、负数与超限 → `_refuse_body`（`server.py:897-903`），且 `tests/test_companion_documents.py:990` 真的发了 `Content-Length: -5` 断言被拒。同条里**仍未取证**的是 `:203-219` 的 SSE 队列无界 + 每连接一线程无读超时——那条会阻塞到客户端断开的 SSE 线程是 `WinError 32` 的**候选之一**（另一个候选是 Windows 对刚关闭句柄/`-wal` 的删除延迟），目前证据不足以区分，故只作候选写进 Q9 的第一步，不写成结论。
6. `store.py:2118/2134/2171` 的 `fts_enabled` 是共享实例标志，任一线程一次瞬时 FTS 错误即全局降级。
7. `runtime.py:715-722 → 749-755` 错误路径把错误原文当正式回复落库，随后仍发 `done`。
8. `runtime.py:264` vs `:346`：`apply_settings` 换 `llm_client` 但不重建已持有的 classifier；`:2052-2058` 的一次 400 即永久关闭 function calling。

## 10. 建议起点

先做 **Q0（基线提交，需用户授权）→ Q10（会话/提案阻塞）→ Q11（执行线程与 CPU 热点）→ Q1（遗忘闭环）→ Q4（执行边界）**。理由：Q10/Q11 是 2026-11-09 实机复现、会直接让“中断会话 / 受理提案 / 写入”全部不可用的阻断项；Q0 保护已有成果、成本几乎为零；Q1 是用户能直接感知的正确性失败且改动集中；Q4 是唯一的可钉死线程面。F10 与 F9 可并行由独立会话推进。

**2026-11-03 排期补充**：用户对本计划整体仍未表态（状态仍是"待审、未开工"），只就新加的 **F16/Q9** 明确说「先写入缺陷工程计划，完成切片 4 后再考虑」。因此 **Q9 的前面插着一件事：M16 切片 4（MCP 治理）本身尚未开工、也尚未被放行**。在此之前，任何全量验收都按 F16 记的 `gc.disable()` 命令执行，且文档必须继续写明该命令的作用域——**不得**把 exit 139 当成产品回归。

## 11. 2026-11-09 实机交互故障复核（新增，优先于旧 §10 排期）

### 11.1 取证基线与执行方式

- 旧实例 `pythonw` pid **77320** 于 09:03:10 启动，09:20 前累计 CPU **468.73 s**，持续约 **60%–82%** CPU；有 **148–205** 个线程，其中约 147–204 个处于 Wait。桌面进程仍可响应 HTTP，但不是“空闲”状态。
- 停止旧实例后以最新工作树重启（pid **95768/95924**），在 `09:20:35` 建立新 endpoint；干净进程前 6 秒 CPU 仅约 1.4 s，随后端到端测试才触发高占用。
- 现场真实库为 **v17**；`AI/PPT` 授权为 `fs-read/fs-search/fs-write/fs-delete`，授权本身正常。
- 现有测试无法在当前沙箱正常导入 `ctypes/_ctypes`（Windows DLL init 失败），因此本轮优先用本地 HTTP、SQLite 只读副本和浏览器夹具取证；不能把未跑的 Python 套件写成全绿。

### 11.2 已确认的用户可见缺陷

| 编号 | 优先级 | 现象 | 取证 | 直接原因 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| **F17** | **P0** | 写入长时间不结束；`123.txt` 仍 0 字节，执行账本停在 `running`，没有回执 | `act_6175ea1695964be5` 在 00:24:37 创建，账本 12 ms 后 running；24 分钟后仍 running；`before.bin` 已生成但目标未替换 | 执行线程/回合在 `execute()` 完成前中断，Web 适配器的 RunContext 未及时收尾；恢复逻辑只在重启时处理账本，不释放内存 RunContext | 已加 300 s stale run 回收与 adapter worker 收尾；隔离实例正常写入约 18 ms；真实 Codex 子进程的外部目录 I/O 另有环境限制，见 §11.9 |
| **F18** | **P0** | 中断会话、删除会话、切换会话显示“正在生成回复”后失败 | 新建、重命名、置顶均为 200；写入回合停在 `tool` 后，`/api/sessions/delete` 返回 **409 正在生成回复** | `_assert_idle()` 只看 `_runs`，不区分“已结束但未释放”和真正运行中的回合；删除/切换被同一个卡住的 RunContext 锁住 | 已修复并通过 258 项相关回归；真实重启后旧 RunContext 不再阻塞 |
| **F19** | **P0** | 受理提案/任务卡“允许”没有响应，点击后状态不变 | 代码审计确认 `confirm_action()` 与 `_task_action()` 同样过 `_assert_idle()`；真实写入卡死后，后续所有审批都被这个忙状态拒绝 | 同一 RunContext 泄漏；不是按钮 DOM 事件本身（真实 Chromium 夹具可发出 POST） | 已修复；真实 Chromium 任务批准只提交一次并生成 completed 回执；无执行器时明确提示 unavailable |
| **F20** | **P1** | 进程长期高 CPU、线程数偏高；空闲时仍持续 I/O | pid 77320：CPU 468.73 s / 启动约 17 分钟；60–82% CPU；205 threads；约 60 KB/s I/O；新进程空闲约 0–3% CPU | 已定位一处确定性自旋：`app_catalog._read_env_target()` 在空环境变量块时 `at += len(signature)` 可回到同一位置；每遇到此类快捷方式即无限循环，造成一个线程持续满 CPU | 已改为 `at = end + 1` 并补回归；重启后累计 CPU 约 0.67 s、12 threads，未复现持续自旋 |

### 11.3 复现记录（最新进程）

1. `POST /api/sessions/create` → 200，返回新 `chat_*`；耗时约 10 ms。
2. `POST /api/sessions/rename` → 200 `changed=true`；`POST /api/sessions/pin` → 200。
3. `POST /api/sessions/switch` → 200；bootstrap 的 `session_id` 已切换。
4. `POST /api/desktop action=grant_directory` → 200；PPT 目录授权正常。
5. `POST /api/turn` 输入“在 111.txt 里写入……” → 202 `run_id=run_5b46…`。
6. 轮询 30 秒：阶段依次 `thinking → tool → answering → tool`，随后一直停在 `tool`；没有新增助手消息、没有审批卡，`111.txt` 不存在。
7. 删除测试会话 → **409 正在生成回复，当前侧栏不能删除会话**；此时会话数仍为 3。

结论：会话 API 的基本 CRUD 不自洽的问题不是数据库约束，而是**一个卡死的回合锁住了应用层的所有互斥操作**。因此修复顺序必须是“释放/看门狗优先”，不能先改按钮或会话 UI。

### 11.4 新增整改切片

| 切片 | 内容 | 覆盖缺陷 | 迁移 | 验收 |
| --- | --- | --- | --- | --- |
| **Q10 回合租约与阻塞解除** | 给 `RunContext` 加 heartbeat/deadline；进入会话、工作区、提案、任务操作前仅回收已结束或超过上限的运行；执行器异常时 adapter worker 必须 `abandon_run`；把“旧 running 回执”和“当前活跃 run”分开计数 | F17 F18 F19 | 否 | 卡死写入后 300 s 内可切换/删除会话；活动回合仍返回 409；提案/任务卡可受理一次且无重复副作用 |
| **Q11 执行线程看门狗** | 每个工具/执行器记录 `action_id`、阶段、开始时间、线程名；在 `advanced → diagnostics` 展示最近执行；强制 `write_file` 超时返回明确失败而不是只停在工具阶段；对执行线程做有界 join；保留异常栈到本地脱敏日志 | F17 F20 | 否（日志目录新建） | 真实写入完成/失败都在 30 s 内产生回执；异常后不再永久 running；空闲 CPU <5%，线程不持续增长 |
| **Q12 会话操作回归** | 新建/切换/重命名/置顶/删除/草稿 × 运行中/空闲/卡死后三种状态；真实 Chromium 点右键菜单和侧栏；确认删除只删目标会话、当前会话删除后回退到剩余最新会话 | F18 | 否 | API + 浏览器双层全绿；不再出现“正在生成回复”永久阻挡 |
| **Q13 提案与任务卡回归** | 受理/取消/确认过期、跨会话、任务批准/修改/拒绝；写入卡死后释放锁再受理；确认按钮只提交一次；任务状态与回执同步刷新 | F19 | 否 | 真实 Chromium 发出一次 POST；任务进入 EXECUTING 且有 receipt；无二次弹窗 |
| **Q14 写入性能与证据** | 基准记录 `create_proposal → claim → backup → temp→fsync→replace → receipt` 各段耗时；对 0 字节、1 MB、32 MB 文件分别测；把目标路径、前后哈希、回执、线程名写入日志 | F17 F20 | 否 | 0 字节文件执行中位 <100 ms；1 MB <1 s；任何超时都有阶段和错误，不伪造 completed |

### 11.5 本轮已落地的修补（仍需复测，不能提前判收口）

- `app_catalog._read_env_target()` 的空块死循环已修，补 `test_repeated_environment_blocks_do_not_stall_parsing`。
- `CompanionApplicationService.recover_stale_runs()` 增加；session、workspace、approval、task 入口会先回收超过 300 秒的旧 RunContext；开始一次新的运行前也做同样清理。
- `WebAdapter._start_run()` 的 worker 在未产生 terminal event 时调用 `abandon_run`，避免只释放 HTTP 请求、不释放运行锁。
- `LaunchChainService` / `write_file` 执行阶段已经记录 `last_execution_diagnostic`，下一轮应把它扩展到普通文件执行器并展示在“高级 → 诊断”。
- `write_file` 在唯一可写目录下自动补 `grant_id`；多个目录时按已有文件名唯一定位，无法唯一定位才要求模型选择。

### 11.6 未完成核验与风险（不能写成已修复）

- **写入仍然停在 tool 阶段**：新进程实测 `run_5b46…` 在 `thinking → tool → answering → tool` 后继续停留，账本 `act_1bb6…` 仍为 running，`111.txt` 不存在。这说明“stale run 回收”只解除应用层锁，不能解释当前执行器为何没有返回；Q11/Q14 是当前最高优先级。
- **强制 `write_file` 只约束模型首轮**：已有 `tool_choice` 请求探针通过，但实际模型在拿到工具结果后仍可能继续一轮工具调用；必须在工具执行完成/失败后主动结束循环，不能让模型把已经完成的写入再次当成未完成。
- **执行栈没有拿到**：当前环境没有 `py-spy`，进程是 `pythonw` 无控制台；CPU 高占用的第二来源尚未定位。`app_catalog._read_env_target()` 的修复是已确定的一处，不等于全部。
- **会话删除 409 已复现**：这是卡死 RunContext 的衍生错误；回收入口加入后还没有在真实 UI 中完成“卡死→释放→删除”的闭环验收。
- **任务卡受理**：浏览器夹具能发 POST，但真实卡死状态下仍走 `_assert_idle()`，必须验证 stale 回收发生在审批之前，而不是只发生在页面刷新时。

### 11.7 验收门槛

1. 真实用户发送“写入 123.txt”后，30 秒内必须出现以下之一：`completed` 回执且文件哈希/大小变化，或 `failed/not_executed` 回执且有明确错误；不允许停在 `running`。
2. 写入卡死/超时的同一时刻，侧栏切换、删除会话、取消提案和任务批准都必须能正常工作；活动中的合法回合仍要返回 409。
3. `diagnostics.last_execution` 或新的执行日志必须能回答：动作 ID、工具名、阶段、开始/结束时间、回执 ID、错误。
4. 真实桌面进程静止 5 分钟 CPU 中位数 <5%，线程数不再随写入尝试持续增加。
5. 在普通桌面终端执行完整 Python 套件；若环境仍报 `_ctypes` DLL 失败，记录为环境阻塞，不能以当前沙箱的静态编译/局部探针替代全量回归。

### 11.8 当前重启后的最后现场（09:20 实例）

- pid **95768** 于 09:20:35 启动；09:26 左右 CPU 已累计 **175.69 s**，仍持有 **约 405 MB** 工作集，`/api/bootstrap` 仍报告 `run.stage=tool`。
- 该实例在测试中创建了 `111.txt` 写入动作 `act_1bb6e6875ff245be`；账本仍在 `running`，目标文件不存在，最近审批/任务均为 0。
- 这说明 **F17 在“刚重启、无历史脏运行”的进程上仍可复现**，不是仅由旧 unknown 回执或旧 RunContext 引起；Q11 必须优先于 UI 按钮改动。
- 由于此实例会继续占用单实例锁和高 CPU，本轮未再次强制终止它；修复代码已经写入工作树，下一次用户明确重启时才加载。后续验收应先停止该实例，再执行 Q10/Q11 的专项回归。

### 11.9 2026-11-09 隔离实例复测与收口结果

#### 已确认并修复

1. **Q10 回合锁泄漏**：`CompanionApplicationService.recover_stale_runs()` 已接入会话、工作区、提案和任务入口；adapter worker 未产生终态时会 `abandon_run`。真实 Chromium 已验证任务批准点击只提交一次、无二次弹窗，并最终生成 `completed` 回执。
2. **Q11 确定性 CPU 自旋**：`app_catalog._read_env_target()` 的空环境变量块现在推进到 `end + 1`，不会再原地无限循环。
3. **Q14 写入阶段日志**：新增 `data/logs/companion.jsonl`，记录 `turn` 与文件执行各阶段。隔离实例的一次真实模型写入总耗时约 6.8 秒，其中文件执行阶段约 **18 ms**；说明该次延迟来自模型回合，不属于磁盘写入或 `fsync`。
4. **文件工具引用不可见**：`find_files` 的候选文本现在显式输出 `file_ref=<id>`，避免模型只看到文件名后自行猜测引用。
5. **文件变更后索引过期**：写入、移动、删除完成后会对涉及的授权目录安排后台重扫；隔离 HTTP 测试确认写入后的文件可由搜索重新找到。
6. **任务卡反馈缺失**：按钮事件改为异步委派并保证在弹窗取消或接口返回后恢复按钮；批准结果按实际回执摘要提示，不再无条件显示“计划已批准”。未配置转发器时按钮显示“暂无执行器”，点击仍会得到明确的“不可用”回执。
7. **页面点击时序**：页面完成首次投影和事件绑定后设置 `body[data-ready=true]`；真实 Chromium 用例等待该信号后再操作，消除了任务页签点击早于绑定而看似无响应的问题。
8. **授权与工作区解耦**：v17 的 `workspace_selected` 保留显式读、写、删除能力；工作区选择不再覆盖设置页中的能力授权。
9. **权威文件写入策略**：平衡和信任档对已获 `fs-write` 的显式写入直接执行，保守档禁用；两者都保留账本、备份、回执、撤销和幂等保护。旧用例已按该契约调整。
10. **F24 旧运行账本收口**：最终重启后，执行提案与回执各 4 条均为 `unknown`，没有持久化 `running`；应用层也不再把旧回合显示为活跃。
11. **F26 并行写工具调用**：强制执行前会按本轮用户消息中的显式文件名过滤并行 `write_file` 调用；唯一匹配时只执行目标文件，多个匹配则拒绝歧义调用。新增 `test_explicit_filename_wins_over_a_historical_parallel_write` 固定该行为。

#### 2026-11-09 复测结果

- 相关回归：**258 项通过，2 项环境跳过**，覆盖执行安全、运行时、流式回合、工具权限、授权、文件动作、真实 Chromium 会话与任务卡、数据库迁移和持久化。
- 隔离 HTTP 操作矩阵全部通过：新建/切换/重命名/置顶/草稿/删除会话；写入后搜索；移动；删除提案取消；删除确认与重复确认拒绝；回收站删除；撤销；文件立即搜索。
- 真实浏览器操作通过：新建、重命名、置顶、停止生成、任务批准。停止生成可以从生成状态回到空闲，并落一条“已停止生成”的助手消息；网络流阻塞期间最长等待仍取决于模型连接。
- 完整 Python 套件（`gc.disable()` 口径）：**1386 项通过，5 项环境跳过，无失败、无错误**，耗时 309.4 秒。

#### 仍未完成

| 编号 | 现象 | 当前证据 | 下一动作 |
| --- | --- | --- | --- |
| **F21** | 兼容模型把工具调用输出成普通 XML 文本，界面把它当正式回复落库 | 隔离实例 `chat_9308dbc106e64836` 的助手消息直接显示 `read_file` 工具 XML；下一次模型回合仍拿不到真实工具结果 | 在 `llm.py` 的流式解析中识别该提供方的文本工具语法，或对不支持的提供方明确降级；增加协议夹具和真实回合验收 |
| **F22** | 模型接口阻塞时，取消要等当前网络读取返回后才落终态 | 停止操作最终成功，约 10 秒后才出现“已停止生成”；线程栈停在 `ssl.read → openai._streaming → llm.stream_chat` | 给流式客户端增加可关闭的底层连接/读超时；在模型返回前不可安全释放同一 runtime 的会话锁 |
| **F23** | 默认任务卡没有可用执行器 | 批准请求成功，任务从 `EXECUTING` 回到 `NORMAL`，回执为 `unavailable` / `executor_not_configured` | UI 已明确提示；真正让“允许”执行任务还需配置具体转发器或接本地多步骤执行器 |
| **F25** | 设置页授权的“读取”与对话工作目录仍容易被用户理解成同一权限 | v17 已做存储层解耦并有 API 回归，但真实 UI 的“未选择工作目录”标签仍可能误导 | 产品文案区分“对话工作目录”和“文件授权目录”，并在授权列表展示当前可写/可删能力 |

#### 收口判据

- 文件写入：30 秒内必须完成并给出 `completed` 回执，或明确 `failed/not_executed`；不得停留 `running`。
- 会话与审批：空闲后必须可切换、删除、取消和批准；合法活动回合仍返回 409。
- 任务卡：按钮每次点击都必须得到状态变化或明确回执；无执行器时不得伪装成已完成。
- 流式协议：工具调用不得以 XML 源码展示给用户；无法解析时应返回可诊断的兼容性错误。
- 真实进程重启后：旧 running 账本必须转终态，空闲 CPU 与线程数需另行取样确认。

#### 真实实例重启与 I/O 环境边界

- 2026-11-09 15:03 使用最新代码再次重启真实实例，endpoint 更新为新端口，首页返回 200，`/api/bootstrap` 的 `run=null`。
- 启动恢复把 `act_1bb6e6875ff245be` 和本次中断的 `act_120157f4c5694ebd` 都从 `running` 转为 `unknown`，分别生成回执 `rcp_57acf639c32b4547`、`rcp_f1ca7336a1b74947`；数据库当前四条执行账本全部为 `unknown`，没有永久 `running`。
- 重启后空闲进程累计 CPU 约 **0.52 秒**，工作集约 **77 MB**，**10 个线程**，CPU 未再持续增长。pid 77320 时期 60%–82% CPU / 148–205 线程的现象没有复现。
- 2026-11-09 15:11 在最终全量回归后再次干净重启，新 endpoint 为 `60385`；首页 200，`/api/bootstrap` 返回 `run=null` 与 3 个会话。数据库仍为 v17，执行提案和回执各 4 条且全部为 `unknown`，没有 `running`。
- 最终重启后的工作进程 pid 为 **288500**，初始累计 CPU 约 **0.58 秒**、工作集约 **77 MB**、**10 个线程**；父进程 pid 为 **269088**。
- 从 Codex 的受限进程树启动真实应用后，对 `Desktop\...\PPT` 的 Python `open(..., "xb")` 返回 `PermissionError`，`tempfile.mkstemp()` 的线程栈停在 `Python312\Lib\tempfile.py:256 → _os.open`；同一路径由 PowerShell 创建临时文件约 **9 ms**。这是当前启动环境的文件系统限制，不是磁盘性能或文件执行器回环。
- 因此，本轮“写入 `123.txt` 卡在 `file.backup.done`”现场不能继续当作产品缺陷：执行器已进入 Python I/O 调用，而该调用在 Codex 工具环境外无法越过授权目录。真实写入验收必须从用户自己的普通终端或桌面快捷方式启动实例，不能从受工作区限制的 Codex 子进程启动。
- 同一真实回合暴露的 **F26：并行写工具调用与文件名继承**已修复：请求明确要求新建 `agent-defect-smoke-20261109.txt` 时，即使模型返回两个 `write_file` 调用，执行层也会按本轮显式文件名筛选；无法唯一匹配时拒绝执行，避免再次覆盖历史 `123.txt`。
