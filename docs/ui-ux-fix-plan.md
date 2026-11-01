# UI 体验问题修复计划书

- 日期：2026-10-31
- 范围：默认 Web 界面（`--ui web`）与承载它的 QtWebEngine 壳层；Qt/Tk 兼容入口不在本次范围
- 状态：**S1–S4 已实施（2026-10-31），S4 收口复查 2026-11-01**。S1 交付主窗原生拖动、浮窗偏移修正与会话菜单点外关闭；S2 交付页内确认/输入对话框，替换全部 9 处 `window.confirm/prompt`（QtWebEngine 不实现原生对话框）；S3 把 composer 与搜索框的焦点反馈收回到圆角容器；S4 让壳层内容填满窗口、补原生四边/四角缩放、主窗锁定 16:9（默认 1152×648、最小 1024×576）并把旧几何迁移到新基线。收口复查补了页面 8px 边沿带的两处误伤（带内 `button/input/textarea/a` 必须照常可点；只 `preventDefault`，不 `stopPropagation`，否则会吞掉关闭菜单的那一下按压）并顺带修掉 `doctor.py` 在 GBK 控制台中被第三方报错字符中断的缺陷。

## 0. 摘要

| 编号 | 用户现象 | 根因 | 修复方向 | 优先级 |
| --- | --- | --- | --- | --- |
| U1 | 拖动窗口时窗口跳动 | 拖动起点把"窗口原点"当成"抓取偏移"，第一次移动就整体错位 | 主窗改用系统原生拖动；浮窗修正偏移公式 | P0 |
| U2 | 删除会话功能失效 | ① 右键菜单在 `mousedown` 阶段把自己删掉，`click` 永远到不了菜单项；② QtWebEngine 默认不实现 `confirm()/prompt()`，删除分支被 `if (!confirm) return` 短路 | 菜单改为"点菜单外才关闭"；用页内对话框替换 `window.confirm/prompt` | P0 |
| U3 | 输入框里的矩形方框 | 全局 `:focus-visible` 焦点环命中 `textarea`，在圆角 composer 内部画出一个 2px 强调色矩形 | 文本控件的焦点反馈交给 composer 容器，移除文本域自身的焦点环 | P1 |
| U4 | 窗口不可自由调整大小 | WebView 与容器都是固定尺寸，顶层窗口没有布局；无边框窗口没有原生边缘命中 | 让内容随窗口布局填充；补原生边缘缩放 | P1 |
| U5 | 窗口长宽比与背景图不一致 | 主窗 1000×700（10:7），背景 3840×2160（16:9），`background-size: cover` 必然裁切 | 主窗锁定 16:9（默认 1152×648、最小 1024×576），最大化/贴边例外 | P1 |

结论：五个现象全部可以在代码层定位，不涉及模型、数据库或后端契约。删除会话是**两个独立缺陷叠加**，只修其中一个仍然无效。

## 1. 环境与现状（取证基线）

- 默认入口 `companion.py --ui web` → `web/shell.py` 的非透明主窗 + 透明浮窗。壳层为 PySide6 6.12 / QtWebEngine。
- 用户桌面：物理 2560×1440，Windows 150% 缩放（逻辑 1707×960；正在运行的 WebEngine 渲染进程带 `--device-scale-factor=1.5`）。
- 主窗常量 `MAIN_WIDTH/MAIN_HEIGHT = 1000/700`、`MIN_WIDTH/MIN_HEIGHT = 780/560`（`web/shell.py:26-29`）；当前 `data/ui_state.json` 保存的 `window_geometry` 为 `[406, 184, 1000, 700]`。
- 背景素材 `data/assets/remielle/background.png`：3840×2160，比例 **16:9**；页面以 `center / cover` 绘制（`web/assets/app.css:6-10`、`tokens.css:205-210`）。
- 本次取证使用离屏 QtWebEngine 与真实 Chromium（Playwright）；取证用的临时目录 `data/_probe_tmp/` 已删除，未留下常驻进程（见 §8）。

## 2. 根因分析

### U1 拖动窗口时窗口跳动

**命中代码**：`web/assets/app.js:1562-1578`（主窗）、`web/assets/overlay.js:412-432`（浮窗，同样问题）。

```js
grab = { x: event.screenX - event.clientX, y: event.screenY - event.clientY };
// ...
state.host.moveWindow(event.screenX - grab.x, event.screenY - grab.y);
```

`event.screenX - event.clientX` 等于**按下瞬间窗口左上角在屏幕上的坐标**，不是光标在窗口内的抓取偏移。第二次计算却把它当成"抓取偏移"去减，结果窗口第一次移动就被放到 `光标屏幕坐标 - 窗口原点` 的位置。

**实测**（离屏 QtWebEngine，窗口位于 (200,150)）：按住时 `clientX=400`，对应 `screenX=602`，于是 `grab.x=202`；代码把窗口移到 `602-202=430`，而正确位置是 `602-400=202`（考虑 2px 取证偏差约 232）。**拖动开始的第一次 `mousemove` 会让窗口瞬间平移约 200px，此后才按 1:1 跟随**——这就是用户看到的"跳动"。

**次级因素**：当前拖动数据来自页面内的 `mousemove`。光标一旦跑出 WebView（快速拖动时很常见），事件停止，窗口停在原地；光标重新进入后下一帧按绝对坐标归位，表现为第二次跳动。QWebChannel 的异步调用会放大这一现象。

**浮窗**：`overlay.js` 复制了同一公式，所以拖动桌宠同样会在起手时跳一下。

### U2 删除会话功能失效

后端没有问题：`/api/sessions/delete` 已被路由（`web/server.py:715-716, 745-746`），`CompanionApplicationService.delete_session()`（`app/service.py:465-468`）与 `runtime.delete_session()`（`runtime.py:407-422`）会级联删除并切换到剩余会话。失效发生在 UI 层，且是两处独立缺陷：

#### U2-a 右键菜单在点击菜单项时把自己删掉

`showContextMenu()`（`web/assets/app.js:1292-1341`）在菜单挂载后用 `setTimeout(..., 0)` 注册了：

```js
const close = () => { menu.remove(); document.removeEventListener("mousedown", close); };
document.addEventListener("mousedown", close);
```

`mousedown` 早于 `click`，而且没有判断事件目标是否在菜单内。用户按下"删除"的瞬间菜单节点被移除，随后的 `click` 不再派发到菜单按钮，动作不会执行。

**真实 Chromium 复现**：右键后菜单存在（`menu present: 1`），点击"删除"后 `handler fired: 0`、菜单已消失（`menu still: 0`）。该方法同时承载重命名与置顶，所以三个菜单动作在浏览器与 Qt 壳层里都失效。

#### U2-b QtWebEngine 默认不实现 `confirm()/prompt()`

删除确认写的是 `if (!window.confirm("删除这个会话及其消息？")) return;`（`app.js:1315-1318`）。壳层的 `WebView` 直接使用原生 `QWebEnginePage`（`web/shell.py:227-238`），没有覆写对话框虚函数；QtWebEngine 的默认实现不会弹出任何对话框。

**实测**：默认页面里 `String(window.confirm(...))` 返回空值，`window.confirm('x') === false` 的求值直接超时（无对话框可交互）；给 `QWebEnginePage` 加上 `javaScriptConfirm/javaScriptPrompt` 覆写后，同一表达式返回 `true|typed`。说明**钩子存在，但壳层没有实现**。

因此即便修好 U2-a，在 Qt 壳层里点"删除"仍会被 `confirm` 的假值短路。同样的调用还出现在：工作区授权 `app.js:1226`、任务批准/修改/拒绝 `app.js:1269-1280`、会话重命名 `app.js:1303-1308`、管理台记忆纠正/忘记/彻底删除 `console.js:402-412`。这些入口在 Qt 壳层下都不可用。

### U3 输入框中的矩形方框

**命中代码**：`web/assets/tokens.css:493-496` 的全局焦点环：

```css
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
```

按 CSS 规范，文本类控件在获得焦点时**总是**匹配 `:focus-visible`（鼠标点击也匹配）。composer 自身是圆角容器（`app.css:950-968`），内部 `textarea`（`app.css:1217-1228`）没有清除焦点环，于是浏览器在圆角框内部再画一个 2px 的强调色直角矩形。

**真实 Chromium 实测**（点击 `#input` 后读取计算样式）：`outlineStyle: solid`、`outlineWidth: 2px`、`outlineColor: rgb(255, 91, 138)`（即 `#ff5b8a`）、`outlineOffset: 2px`、`matches(':focus-visible'): true`。这就是用户看到的"矩形方框"，不是边框、不是滚动条。

**约束**：`tests/test_companion_web_style.py:168-171` 目前断言所有页面都不得出现 `outline: none`，用于防止焦点反馈被整体抹掉。修复必须保留键盘可达的可见焦点，并同步调整这条测试（见 §3 U3）。

### U4 窗口不可自由调整大小

**命中代码**：`web/shell.py:241-286`。

```python
self.view = WebView(self, self, transparent=transparent)
self.view.setFixedSize(size[0], size[1])
container = QWidget(self)
container.setGeometry(0, 0, size[0], size[1])
layout = QVBoxLayout(container)   # 布局挂在 container 上，窗口本身没有布局
```

- WebView 被 `setFixedSize` 钉死为建窗尺寸；容器几何也固定。窗口即使变大，内容仍停留在 1000×700，右侧/下方出现空白。
- 主窗使用 `Qt.FramelessWindowHint | Qt.Window`（`shell.py:249`），Windows 下没有 `WS_THICKFRAME`，没有原生边缘/角落缩放；`MainWindow` 只设置了最小尺寸（`shell.py:316`）。
- 唯一可拖的是 `BaseWindow` 里 14×14 的 `QSizeGrip`（`shell.py:276-278`）。它改变的是顶层窗口外框，不会改变固定尺寸的 WebView，所以体验上等于"不可调整"。

设置页与管理台复用同一个 `BaseWindow`，存在同样的"有 grip 但内容不跟随"问题。

### U5 窗口长宽比与背景图不一致

背景图为 3840×2160（16:9），主窗为 1000×700（10:7 ≈ 1.43）。`background-size: cover` 会按短边铺满，左右（或上下）必然被裁掉：在 1000×700 内只能看到背景图约 80% 的横向内容。窗口最大化或 Windows 贴边时比例还会变化。当前没有任何比例约束代码，`_restore_geometry()` 也只做像素级最小尺寸夹取（`shell.py:324-337`）。

## 3. 解决方案

### U1 拖动（方案：原生拖动为主，JS 修正为兜底）

1. **主窗改为系统原生拖动（推荐，P0）**
   - `ShellHost` 新增 `beginWindowDrag()` 槽位；`app.js` 顶栏 `mousedown` 只做两件事：排除 `button/input/textarea` 后调用 `host.beginWindowDrag()`。
   - Windows 实现：`ReleaseCapture()` + `SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)`。拖动由系统消息循环接管，光标移出窗口也持续跟随，DPI/多屏/贴边吸附都由系统处理；不再依赖页面 `mousemove`。
   - 兜底：非 Windows 或无 `windowHandle` 时调用 `QWindow.startSystemMove()`；两者都不可用时才退回 JS 位移。
2. **浮窗**：保留 JS 位移（需要拖动中不吸附、松手后 `snapOverlay()`），只修正抓取偏移：
   ```js
   grab = { x: event.clientX, y: event.clientY };
   state.host.moveOverlay(event.screenX - grab.x, event.screenY - grab.y);
   ```
3. **删除**主窗旧的 `mousemove/mouseup` 拖动实现，避免两条路径同时移动窗口。
4. 次要项：顶栏双击可映射到 `toggleMaximize()`（可选，不属于本次必需）。

验收要点：起手无位移；快速拖动光标离开窗口时窗口仍跟随；多屏与 150% 缩放下不漂移。

### U2 删除会话（菜单与对话框分别修）

1. **菜单关闭条件**（`app.js`）
   - `mousedown` 监听改为"目标在菜单外才关闭"：
     ```js
     const close = (e) => {
       if (menu.contains(e.target)) return;
       menu.remove();
       document.removeEventListener("mousedown", close);
     };
     ```
   - 同时补 `Escape`、`window.blur`、`scroll` 关闭；菜单补 `role="menu"`、菜单项 `role="menuitem"`，按钮文字保持现有三项（重命名 / 置顶 / 删除）。
   - 菜单打开时的右键 `preventDefault()` 保留。
2. **页内对话框组件（新增 `web/assets/dialog.js`）**
   - 暴露 `CompanionDialog.confirm(message, {title, okText, danger}) -> Promise<boolean>` 与 `CompanionDialog.prompt(message, defaultValue, {title}) -> Promise<string|null>`。
   - 结构与样式：遮罩 + `role="dialog" aria-modal="true"`，复用 `--surface / --accent / --danger / --radius-* / --sp-*` token；默认焦点在取消键（删除类动作应显式 `danger`）；`Esc` 取消、`Enter` 确认（IME 组字期间不响应）、点击遮罩取消；关闭后焦点回到触发元素。
   - 在 `index.html`、`settings.html`、`console.html` 中于 `app.js`/`console.js` 之前引入。
   - 替换全部调用点：`app.js:1226, 1269, 1272, 1278, 1303-1308, 1315-1318`；`console.js:402, 406, 412`。替换后 `grep -n "window.confirm\|window.prompt"` 在 `web/assets/*.js` 中应为零。
3. **可选加固（P2）**：给壳层加 `CompanionPage(QWebEnginePage)`，覆写 `javaScriptAlert/Confirm/Prompt` 并用 `QMessageBox/QInputDialog` 兜底。完成 1+2 后它不是必需项；保留它可防止未来新增代码再次踩坑。若采用，对话框决策要抽成可注入回调，便于离屏测试不弹模态框。
4. **后端不改**：删除接口、级联删除、活动会话回退都已存在并有测试；不需要数据库迁移。

验收要点：右键任一菜单项都会执行动作；删除先弹出页内确认，取消无副作用；删除当前/最后一个会话后自动切到剩余或新建会话；重命名、置顶、任务审批、工作区授权、管理台记忆操作在 Qt 壳层下全部可用。

### U3 输入框焦点矩形

1. **把焦点反馈交给容器**（`app.css`）
   ```css
   .composer textarea:focus-visible { outline: none; }
   .composer:focus-within {
     border-color: var(--accent);
     box-shadow: 0 0 0 2px var(--accent-soft), var(--shadow-1);
   }
   ```
   圆角外框成为唯一的焦点指示，既去掉了内部直角矩形，又保持可见的键盘焦点。对比度需满足 3:1（`accent` 在两种主题上都已用于主按钮，可直接复用）。
2. **同类控件**：`.rail-search input` 采用同一模式（移除自身焦点环，`.rail-search:focus-within` 加圆角环）。设置页/管理台的矩形输入框本身有边框，保留全局 `:focus-visible` 作为焦点反馈，不动。
3. **测试同步**：`tests/test_companion_web_style.py:168-171` 的"禁止 outline: none"要改成**定向白名单**——允许 `.composer textarea:focus-visible` 与 `.rail-search input:focus-visible` 的两条 scoped 规则，同时断言 `.composer:focus-within` 仍提供可见环、`button/a` 的全局焦点环仍存在。不要全局放开 `outline: none`。
4. **可访问性**：Tab 进入 composer 时看到容器环；鼠标点击时同样只有容器环。Chromium 回归用例断言 `textarea` 计算样式 `outlineStyle === "none"` 且 composer 的 `box-shadow` 非 `none`。

### U4 可调整大小

1. **内容填满窗口**（`web/shell.py:241-286`）
   - 非浮窗：把 `QVBoxLayout` 直接挂到顶层窗口（contentsMargins 全 0），`addWidget(self.view)`；删除 `view.setFixedSize(...)` 与 `container.setGeometry(...)`；`self.view.setSizePolicy(Expanding, Expanding)`。
   - 保留 `container` 仅作为无边框窗口的绘制容器时可去掉；`MainWindow.set_theme_background()` 继续作用于 `view.page()`。
   - 浮窗保持固定 300×250 与透明属性不变。
2. **主窗可缩放**（`MainWindow`）
   - 补 Windows 原生边缘命中：在 `nativeEvent` 处理 `WM_NCHITTEST (0x0084)`，把距客户区边缘 8 逻辑像素（按 `devicePixelRatio()` 换算物理像素）的 8 个方向返回 `HTLEFT/HTRIGHT/HTTOP/HTTOPLEFT/.../HTBOTTOMRIGHT`；最大化/全屏/浮窗不参与。
   - 非 Windows 用 `QWindow.startSystemResize(edges)` 兜底。
   - 移除或隐藏 `QSizeGrip`（原生命中已覆盖四边与四角，保留会出现视觉与命中区重叠）。
   - 设置页/管理台：作为固定尺寸工具窗，改为显式 `setFixedSize`，去掉"可拖但内容不跟随"的假象；若后续需要可缩放，再走同一套原生命中 + 最小尺寸。
3. **几何持久化**：`MainWindow._save_geometry()` 已存在，尺寸变化后继续写 `ui_state.json`；注意鼠标拖动缩放时 `moveEvent/resizeEvent` 会高频触发，现有 350ms 防抖足够。

### U5 16:9 比例

1. **常量**（`shell.py`）
   - `MAIN_SIZE = (1152, 648)`（16:9，接近现有 1000×700 的观感，宽度略增）。
   - `MIN_SIZE = (1024, 576)`（16:9）。依据：200% 字号下顶栏+composer+状态条约占 409px，576 高度仍能给聊天区约 167px（≈29%），可以继续满足 `layout_audit` 的"聊天区 ≥25%"回归；再小会跌破该底线。若用户更希望窗口能缩得更小，需要同时下调该回归并接受 200% 下的可用性损失。
2. **手动缩放锁定**（Windows）
   - 在 `nativeEvent` 处理 `WM_SIZING (0x0214)`：按 `wParam` 指定的拖动边/角调整 `RECT`，用宽度推导高度（`h = round(w * 9 / 16)`），保持对角/对边锚定，返回 `TRUE`。框架在绘制前完成修正，拖动过程不抖。
   - `resizeEvent` 里再放一个"非最大化且比例偏差 > 2px 时纠偏"的安全网，覆盖程序化 resize；用重入标志避免递归。
   - 最大化、全屏、Windows 贴边（Snap）不强制比例——这些是系统行为，强行改尺寸会与用户预期冲突；恢复普通状态后回到 16:9。
3. **启动恢复**
   - `_restore_geometry()` 读取旧几何后统一归一：宽度夹到 `[MIN_WIDTH, 屏幕可用宽度]`，高度按 16:9 推导，再 `clamp_position()` 夹取位置。当前保存的 `[406,184,1000,700]` 会变成 `1024×576`（宽度低于新最小宽度）；这是预期迁移，不需要改 `ui_state.json` 格式。
   - 可用纯函数 `fit_aspect(width, height)` 承载推导规则，便于单元测试。
4. **不做的事**：不给设置页/管理台强制 16:9（680×560、1040×720 是为内容可读性选的）；这两页背景仍是 `cover`，会有裁切，属于已登记行为。若用户要求所有窗口一致，再单独评估。

## 4. 实施切片与影响面

| 切片 | 内容 | 主要文件 | 测试 |
| --- | --- | --- | --- |
| S1（P0，约 0.5 天） | 主窗原生拖动 + 浮窗偏移修正；右键菜单点外关闭 | `web/assets/app.js`、`web/assets/overlay.js`、`web/shell.py` | **已完成（2026-10-31）**：静态契约（抓取偏移使用 `clientX/clientY`、菜单关闭含 `menu.contains`、壳层含 `WM_NCLBUTTONDOWN`）+ 真实 Chromium（主窗委派且不触发 `moveWindow`、菜单 `mousedown` 存活并置顶、点外/Esc 关闭、浮窗首帧位移、旧壳层兜底）；全量 649 → 658 |
| S2（P0，约 1 天） | `dialog.js` 页内对话框；替换 9 处 `confirm/prompt`；三个页面引入 | `web/assets/dialog.js`（新）、`app.js`、`console.js`、`index/settings/console.html`、`tokens.css` | **已完成（2026-10-31）**：`CompanionDialog.confirm/prompt`（`role=dialog`、`aria-modal`、默认焦点取消/输入框、Esc/遮罩取消、Enter 确认、IME 组字保护、Tab 焦点环、关闭还原焦点），共享样式进 `tokens.css`，三个页面在页面脚本前加载；真实 Chromium 覆盖删除确认/取消、Esc、遮罩、重命名 IME 与输入预填，静态断言全部 JS 零 `window.confirm/prompt`；全量 658 → 666 |
| S3（P1，约 0.5 天） | composer / 搜索框焦点反馈归容器；更新焦点测试 | `web/assets/app.css`、`tests/test_companion_web_style.py` | **已完成（2026-10-31）**：`.composer textarea` 与 `.rail-search input` 的 `:focus-visible` 只清自身 outline，`.composer:focus-within`（accent 边框 + `0 0 0 2px accent-soft` 环）与 `.rail-search:focus-within` 成为唯一可见环；风格测试从“全局禁止 outline:none”改为**定向白名单**并断言容器环，新增真实 Chromium 3 条（点击输入框/搜索框无内框且容器有环、Tab 到发送按钮仍有全局环）；全量 666 → 670 |
| S4（P1，约 1–1.5 天） | 壳层布局填充、原生边缘缩放、`WM_SIZING` 16:9、几何迁移、设置/管理台固定尺寸 | `web/shell.py`、`web/layout_audit.py`、`tests/*`、`README.md`、`docs/*` | **已完成（2026-10-31）**：`BaseWindow` 的 `QVBoxLayout` 直接挂在顶层窗口、WebView `Expanding/Expanding` 且随窗口 resize，`QSizeGrip` 删除；设置/管理台显式 `setFixedSize`；主窗 `nativeEvent` 处理 `WM_NCHITTEST`（8 逻辑像素 × DPR、8 方向、最大化/全屏除外）与 `WM_SIZING`（宽高按 16:9 重写、按拖动边/角锚定、DPI 换算、最大/全屏除外），非 Windows 由页面边沿委派 `QWindow.startSystemResize`；`fit_aspect`/`normalized_geometry` 纯函数把旧 `[406,184,1000,700]` 迁移为 `1024×576`；`layout_audit` 主界面矩阵改为 1152×648 / 1024×576 + 真实 Chromium 零溢出，新增 Qt 离屏 13 个用例；2026-11-01 收口复查另修页面边沿带两处误伤（带内控件照常可点、不 `stopPropagation`）+1 用例；全量 670 → 684 |

依赖关系：S1/S2/S3 相互独立，可并行；S4 最后做，因为它会改变既有尺寸基线并触发文档更新。

**必须同步的既有契约**：

- `web/layout_audit.py:31` 的主界面尺寸 `(1000,700)/(780,560)` 改为 `(1152,648)/(1024,576)`；`tests/test_companion_web_layout.py` 的四条断言（零裁切、每行 16–45 字、聊天区 ≥25%、图标退化）随之重跑。
- `README.md:31,33,103`、`docs/progress.md:831,857,861,871,909`、`docs/v1-usage.md:583` 中"1000×700 / 最小 780×560"的表述需要一并修订，不能只改代码。**S4 已同步**：README 的界面段与已知限制、`docs/progress.md` 的阶段表与壳层描述、`docs/v1-usage.md` 的"主窗口布局与主题"和测试基线段（595 → 684）均已改写；Qt/Tk 兼容界面仍是 1000×700（最小 780×560），文档按"只有 Web 壳层接入 16:9 锁定"表述，不声称两端同源。
- `tests/test_companion_web_style.py:168-171` 的焦点环断言按 §3 U3 定向修改。
- 新增决策建议：D73（拖动交给 OS、浮窗保留 JS 位移）、D74（菜单关闭条件）、D75（页内对话框）、D76（文本控件焦点反馈归容器）、D77（壳层布局填充 + 原生八向缩放 + 主窗 16:9 + 工具窗固定尺寸 + 不做 `resizeEvent` 兜底）；新增风险 R27（Win32 命中/DPI/最大化例外、页内对话框的 IME 与焦点陷阱、浮窗坐标语义、菜单生命周期）、R28（DPI 物理/逻辑像素、`WS_POPUP` 无 `WS_THICKFRAME` 的系统缩放循环依赖、页面 8px 边沿带与真实点击、最小尺寸 780×560 → 1024×576 的基线变化）。实际编号与理由见 `docs/progress.md` §9。

## 5. 验收标准

### 自动化

1. `.venv/Scripts/python.exe -m unittest discover -s tests -t tests`：全量通过；新增用例不少于：
   - 真实 Chromium：会话右键菜单三项动作均触发；删除确认后 `sessions` 少一条且活动会话正确回退；取消后条数不变。
   - 真实 Chromium：composer 聚焦后 `textarea` 无 outline、`.composer` 有可见焦点环；按钮键盘焦点环仍在。
   - 真实 Chromium：窗口 8px 边沿带内的按钮（最小化/隐藏）只激活、不上报缩放；同一次带内按压仍能关闭已打开的会话菜单，会话行不被误置顶。
   - 静态契约：`app.js/overlay.js` 使用 `clientX/clientY` 作为抓取偏移；`showContextMenu` 的关闭逻辑含目标包含判断；`dialog.js` 被三个页面引入；`web/assets/*.js` 不再出现 `window.confirm/prompt`。
   - Qt 离屏：`fit_aspect(1000,700) == (1024,576)` 一类归一规则；`[406,184,1000,700]` 恢复为 16:9；主窗 resize 后 WebView 尺寸等于客户区（无固定尺寸残留）。
2. `python -m agent_framework.companion.web.layout_audit --out data/layout_audit`：`Layout audit passed.`，新矩阵 4 档字号 × 1152×648/1024×576 零溢出；1024×576/200% 的聊天区占比 ≥25%。
3. `doctor.py` 保持 `Environment check passed.`。

### 实机人工（R14/R23/R25 同一批）

- 顶栏拖动：起手无跳动；快速甩动、光标离开窗口、跨屏拖动均连续跟随。
- 四边/四角缩放：内容实时填充；任意手动尺寸保持 16:9；最大化、还原、贴边行为正常。
- 主窗在 1024×576 与默认 1152×648 下聊天、composer、右栏均可用；背景图不再被横向裁切（普通状态）。
- Qt 壳层内：右键菜单可点、删除有确认框、重命名可输入、任务审批与工作区授权可用。
- 输入框聚焦只显示圆角容器环，无内部矩形；键盘 Tab 仍有清晰焦点。

## 6. 风险与回滚

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| Win32 命中测试/DPI | `WM_NCHITTEST` 返回物理像素，Qt 用逻辑像素；150% 缩放下命中区可能偏小 | 命中区按 `devicePixelRatio()` 换算并设下限 8 逻辑像素；最大化/浮窗跳过；实机逐方向验证 |
| 16:9 与最大化/贴边冲突 | 系统缩放是自由比例 | 只在普通状态强制；最大化/贴边/全屏例外，恢复普通状态再纠偏 |
| 最小尺寸上调 | 780×560 的旧契约与截图基线失效 | 同步更新 `layout_audit`、测试与文档；若用户坚持更小窗口，改为"自由缩放 + 背景用 contain/letterbox"的替代方案 |
| 页内对话框的输入法 | Enter 可能在中文组字时误确认 | 监听 `compositionstart/end` 或 `event.isComposing`；Esc 只在非组字时关闭 |
| 焦点环移除 | 可能被视为可访问性回退 | 容器环保持 3:1 对比度；全局按钮/链接焦点环不动；静态测试改为定向白名单而非整体放开 |
| 原生拖动阻塞 | `SendMessage(WM_NCLBUTTONDOWN)` 在拖动期间占用当前槽调用 | 槽调用本就不需要返回页面；Windows 消息循环照常泵消息；若实测异常退回 JS 修正方案 |

回滚粒度：U1/U2/U3 都是局部 JS/CSS 改动，可单独回退；U4/U5 集中在 `shell.py` 与尺寸常量，回退时恢复 `setFixedSize` 与 1000×700 基线即可。

## 7. 文档与决策更新清单

- `docs/progress.md`：新增本轮里程碑条目（计划 → 交付）、D73–D76 与 R27；更新已过时的窗口尺寸陈述。
- `README.md`、`docs/v1-usage.md`：窗口尺寸、可缩放与 16:9、删除/对话框在 Qt 壳层的可用性。
- `docs/technical-plan.md`：如需，把"桌面壳层与窗口行为"补一段（原生拖动、比例约束、对话框传输层）。
- 不新增数据库迁移；不改 `ui_state.json` 结构。

## 8. 本次分析的进程清理

- 取证脚本全部使用 `.venv/Scripts/python.exe` 并正常退出；Playwright 浏览器已 `close()`；临时目录 `data/_probe_tmp/` 已删除。
- 检查时仍存在的 `pythonw.exe companion.py`（含其 `QtWebEngineProcess.exe` 子进程）创建于本次分析之前，属于**用户正在运行的桌面端**，已保留、未终止；本次分析没有新增常驻后台进程。

---

附：取证命令（可复现）

1. 输入框焦点环（真实 Chromium）：加载 `tokens.css + app.css` 的 composer，点击 `#input`，读取 `getComputedStyle(textarea)`。
2. 菜单自删（真实 Chromium）：复刻 `showContextMenu` 结构，右键打开后点击菜单项，断言 `fired === 0` 且菜单已移除。
3. QtWebEngine 对话框：默认页面 `runJavaScript("window.confirm('x') === false")` 无结果；覆写 `QWebEnginePage.javaScriptConfirm/javaScriptPrompt` 后返回 `true|typed`。
4. 拖动坐标：离屏 QtWebEngine 窗口置于 (200,150)，`QTest.mouseMove` 到 (400,120)，页面记录 `screenX=602`、`clientX=400`，据此复算偏移误差。
