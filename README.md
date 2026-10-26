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
- 工具循环有上限：单轮最多 4 次调用、最多 3 轮；工具级超时（搜索 30 秒、写入 10 秒、时间查询 5 秒）。
- 模型不支持工具调用时自动降级为普通对话；显式“搜索…并保存为笔记”仍走正则快路径。
- 搜索后整理为 Markdown 笔记，确认前绝不写文件。
- 授权目录、路径逃逸检查、文件哈希冲突检测、覆盖备份和撤销。
- 提案绑定工作区快照与有效期；确认时复核当前模式、工作区和策略，不一致则不写入。
- 执行账本保证重复确认只写入一次；结果不确定时标记为 `unknown`，重启后不自动重放。
- SQLite 持久化对话、提案、执行回执、记忆、共同经历和情绪快照；数据库启动时按 `user_version` 自动迁移。
- 用户偏好与事实记忆带用户原文证据、规范键与候选评分；同键新值会取代旧值，支持查看、纠正和忘记。
- 遗忘使用墓碑与遗忘序号，阻止相同内容被重新导入或召回；召回结果带来源、证据数量与原因。
- 情绪估计也可由用户纠正，并与角色状态分开保存。
- 关闭后重开可恢复历史会话、模式与待确认动作。

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
python -m unittest discover -s tests -v
```

## 接入个人微信 / ClawBot

项目提供 `agent_service.py`，把当前 `ChatAgent` 暴露为本地 OpenAI 兼容接口。OpenClaw 已配置为通过 `current-agent/python-agent` 调用它。

启动本地服务：

```powershell
python agent_service.py
```

详细配置和故障排查见 `integrations/openclaw/README.md`。
