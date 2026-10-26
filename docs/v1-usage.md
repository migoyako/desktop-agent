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
- 遗忘会写入墓碑，阻止相同内容被重新召回或导入。
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

## 数据位置

默认数据目录：

```text
data/
├── companion.db
└── backups/
```

SQLite 使用 WAL 模式。备份只保存被覆盖文件的写入前内容，撤销时会再次校验哈希。

可以通过参数指定独立数据目录：

```powershell
python companion.py --data-dir C:\path\to\companion-data
```

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
- FTS5/BM25 中文召回、旧聊天导入与跨类型遗忘传播。
- 向量召回与多模态记忆。
- 主动发言调度。
- Live2D、语音输入和语音合成。
- 操作系统钥匙串迁移。
- 打包安装程序。

这些能力应在现有动作状态机、记忆账本和权限策略上继续扩展。

## 验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q agent_framework companion.py tests
```

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
- Tkinter 界面创建冒烟测试。
