/* Settings page for the Pixso-designed companion UI. */
(function () {
  "use strict";

  const $ = (selector, root) => (root || document).querySelector(selector);
  const token = new URLSearchParams(location.search).get("t") || "";

  const state = {
    data: null,
    settings: null,
    models: [],
    mcp: null,
    desktop: null,
    desktopTab: "directories",
    desktopSearch: null,
    desktopEditingChain: "",
    desktopChainDraft: null,
    desktopDetection: null,
    mcpOpen: {},
    mcpBusy: {},
    cat: "company",
    panel: null,
    filter: "",
    host: null,
  };

  // 移除走已审核的 S02b 确认弹层；弹层不能叠弹层，所以编辑表单里的「移除」先把
  // 目标记在这里、收掉自己，再由外层弹出确认。
  let sheetRemoval = "";

  const MOODS = [
    { value: "neutral", label: "平静" },
    { value: "positive", label: "开心" },
    { value: "low", label: "低落" },
    { value: "tired", label: "疲惫" },
  ];

  const CATEGORIES = [
    { id: "company", name: "陪伴", desc: "记忆、情绪与人设" },
    { id: "work", name: "工作", desc: "主动关心与任务卡" },
    { id: "desktop", name: "桌面", desc: "目录、文件、应用与启动链" },
    { id: "model", name: "模型", desc: "推理参数与服务接入" },
    { id: "data", name: "数据与外观", desc: "管理、主题与浮窗" },
  ];

  const ENTRIES = {
    memory: { cat: "company", name: "对话记忆与交互", desc: "记忆开关、回答方式与清空" },
    affect: { cat: "company", name: "情绪", desc: "状态估计与纠正" },
    persona: { cat: "company", name: "基础信息", desc: "称呼、边界与头像导入" },
    proactive: { cat: "work", name: "主动", desc: "免打扰、频率与未回复阈值" },
    tasks: { cat: "work", name: "任务", desc: "任务卡与执行记录" },
    desktop: { cat: "desktop", name: "桌面能力", desc: "授权目录、文件、应用目录与启动链" },
    model: { cat: "model", name: "模型推理", desc: "模型、采样、上下文窗口与服务接入" },
    rag: { cat: "model", name: "RAG 文档库", desc: "词法检索已进上下文，上传与列表界面等画板" },
    mcp: { cat: "model", name: "MCP 服务", desc: "外部工具服务器与逐工具开关" },
    manage: { cat: "data", name: "管理台", desc: "记忆、召回与审计" },
    theme: { cat: "data", name: "主题", desc: "经典深色与珍珠白" },
    font: { cat: "data", name: "字号", desc: "100% – 200% 缩放" },
    motion: { cat: "data", name: "减少动效", desc: "关闭过渡与呼吸动画" },
    background: { cat: "data", name: "背景图", desc: "主窗、设置与管理台" },
    overlay: { cat: "data", name: "桌面浮窗", desc: "形象与主动气泡" },
    advanced: { cat: "data", name: "高级", desc: "权限档位、工具轮次与诊断" },
  };

  const el = {
    nav: $("#nav"),
    content: $("#content"),
    search: $("#search"),
    toast: $("#toast"),
    close: $("#closeBtn"),
    themeBtn: $("#themeBtn"),
    overlayBtn: $("#overlayBtn"),
    head: $("#winHead"),
  };

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  async function api(path, options) {
    const opts = options || {};
    const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token);
    const response = await fetch(url, {
      ...opts,
      headers: {
        "Content-Type": "application/json",
        "X-Companion-Token": token,
        ...(opts.headers || {}),
      },
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok) throw new Error(payload.error || response.statusText);
    return payload;
  }

  function post(path, body) {
    return api(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("on");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.toast.classList.remove("on"), 2200);
  }

  async function guard(promise) {
    try {
      return await promise;
    } catch (error) {
      toast(error.message || "操作失败");
      return null;
    }
  }

  function applyUi(ui) {
    document.documentElement.dataset.theme = ui.theme || "charcoal-pink";
    document.documentElement.style.setProperty("--scale", String((ui.font_scale || 100) / 100));
    document.body.classList.toggle("reduce-motion", Boolean(ui.reduce_motion));
    CompanionBackground.apply(ui, (state.data || {}).assets, token);
  }

  function renderNav() {
    el.nav.innerHTML = CATEGORIES.map(
      (cat) => `<button data-cat="${cat.id}" class="${state.cat === cat.id && !state.panel ? "on" : ""}"${
        state.cat === cat.id && !state.panel ? ' aria-current="true"' : ""
      }>
        <span class="name">${cat.name}</span>
        <span class="desc">${cat.desc}</span>
      </button>`
    ).join("");
  }

  function summaryCard(compact) {
    const data = state.data;
    const summary = data.settings_summary || {};
    return `<div class="summary">
      <div class="lines">
        <div class="main">记忆 ${summary.memory_count || 0} 条 · 情绪${escapeHtml(summary.mood_text || summary.mood || "平静")} · 人设 ${escapeHtml(summary.persona.name)}</div>
        <div class="sub">陪伴偏好已记录，重要事实均保留来源。</div>
      </div>
      ${compact ? "" : '<button class="ghost" data-open="console">更多</button>'}
    </div>`;
  }

  function entriesFor(cat) {
    return Object.entries(ENTRIES).filter(([, item]) => item.cat === cat);
  }

  function renderList() {
    const query = state.filter.trim().toLowerCase();
    if (query) {
      const matches = Object.entries(ENTRIES).filter(
        ([, item]) =>
          item.name.toLowerCase().includes(query) ||
          item.desc.toLowerCase().includes(query)
      );
      el.content.innerHTML =
        `<div class="section-title">搜索结果 · ${matches.length}</div>` +
        (matches.length
          ? matches.map(([id, item]) => entryHtml(id, item)).join("")
          : '<div class="empty-note">没有匹配的设置</div>');
      return;
    }
    el.content.innerHTML =
      summaryCard() + entriesFor(state.cat).map(([id, item]) => entryHtml(id, item)).join("");
  }

  function entryHtml(id, item) {
    return `<button class="entry" data-panel="${id}">
      <span class="text"><span class="name">${item.name}</span><span class="desc">${item.desc}</span></span>
      <span class="chev">›</span>
    </button>`;
  }

  function wrapPanel(id, body) {
    const item = ENTRIES[id];
    return `<button class="back" data-back="1">‹ 返回</button>
      <div class="panel"><h2>${item.name}</h2>${body}</div>`;
  }

  // One block per setting so every panel labels the same control the same way;
  // `data-setting` / `data-switch` is what collectForm() reads back.
  function numberRow(key, label, min, max, step, unit) {
    const values = settingsView().settings || {};
    return `<div class="form-block"><div class="form-row">
      <label for="st-${key}">${escapeHtml(label)}</label>
      <input class="field number" type="number" id="st-${key}" data-setting="${key}" min="${min}" max="${max}" step="${step || 1}" value="${Number(values[key]) || 0}">
      <span class="unit">${escapeHtml(unit || "")}</span>
    </div>${fieldError(key)}</div>`;
  }

  function selectRow(key, label, options, note) {
    const values = settingsView().settings || {};
    const current = String(values[key] || "");
    return `<div class="form-block"><div class="form-row">
      <label for="st-${key}">${escapeHtml(label)}</label>
      <select class="field" id="st-${key}" data-setting="${key}">${options
        .map(
          (item) => `<option value="${escapeHtml(item.value)}" ${
            item.value === current ? "selected" : ""
          }>${escapeHtml(item.label)}</option>`
        )
        .join("")}</select>
    </div>${note ? `<div class="hint" id="hint-${key}">${escapeHtml(note)}</div>` : ""}${fieldError(key)}</div>`;
  }

  function switchRow(key, label, desc, on) {
    return `<div class="switch-row">
      <div class="text"><div class="name">${escapeHtml(label)}</div><div class="desc">${escapeHtml(desc)}</div></div>
      <button class="switch ${on ? "on" : ""}" data-switch="${key}" aria-label="${escapeHtml(label)}"></button>
    </div>`;
  }

  function saveRow() {
    return '<div class="form-actions"><button class="primary" data-save-settings>保存</button></div>';
  }

  // A panel for a capability that is deliberately not implemented yet: the
  // control is present, disabled, and says why in text rather than by colour.
  function unwired(label, note) {
    return `<div class="unwired" aria-disabled="true">
      <div class="unwired-head"><span class="pill">未接入</span><span class="name">${escapeHtml(label)}</span></div>
      <div class="hint">${escapeHtml(note)}</div>
    </div>`;
  }

  function panelMemory() {
    const summary = state.data.settings_summary || {};
    const values = settingsView().settings || {};
    const count = Number(summary.memory_count || 0);
    return wrapPanel(
      "memory",
      switchRow(
        "memory_enabled",
        "启用长期记忆",
        "关闭后不再自动记录新的记忆，也不再召回历史记忆",
        values.memory_enabled !== false
      ) +
      selectRow("output_format", "回答方式", outputFormats(), outputFormatHint(values.output_format)) +
      numberRow("max_history_messages", "带入的历史轮数", 2, 200, 1, "条") +
      `<div class="stat-grid">
        <div class="stat"><div class="k">已记录</div><div class="v">${count} 条</div></div>
        <div class="stat"><div class="k">来源策略</div><div class="v">保留原消息</div></div>
      </div>
      <div class="hint">记忆分为偏好、事实与事件，每条都指向原消息；「回答方式」只写进本轮提示词，不改你的记忆。关闭记忆后，你在消息上主动点的「记住这条」仍然会记录——那是你直接下的指令。</div>
      <div class="danger-zone">
        <div class="text"><div class="name">清空记忆</div><div class="desc">硬删除全部记忆、证据与召回记录，会话与回执保留</div></div>
        <button class="ghost" data-purge-memories ${count ? "" : "disabled"}>清空 ${count} 条</button>
      </div>
      <div class="hint">受保护的记忆不会被清空，清空后会告诉你还剩几条。逐条纠正与遗忘在管理台。</div>
      ${saveRow()}
      <div class="form-actions"><button class="ghost" data-open="console">打开管理台</button></div>`
    );
  }

  function panelAffect() {
    const affect = state.data.affect || {};
    const user = affect.user || {};
    const agent = affect.agent || {};
    const card = (label, value) => `<div class="stat">
      <div class="k">${label}</div>
      <div class="v">${escapeHtml(value.mood_text || value.mood_label || "平静")}</div>
      <div class="k" style="margin-top:4px">效价 ${Number(value.valence || 0).toFixed(2)} · 置信度 ${Number(value.confidence || 0).toFixed(2)}</div>
    </div>`;
    return wrapPanel(
      "affect",
      `<div class="stat-grid">${card("你的状态", user)}${card("角色状态", agent)}</div>
      <div class="form-row"><label>纠正情绪</label>
        <select class="field" id="moodSelect">${MOODS.map((mood) => `<option value="${mood.value}" ${mood.value === user.mood_label ? "selected" : ""}>${mood.label}</option>`).join("")}</select>
      </div>
      <div class="hint">情绪只是估计；你的纠正会立即覆盖估计值。</div>
      <div class="form-actions"><button class="primary" id="saveMood">保存纠正</button></div>`
    );
  }

  function avatarMarkup() {
    const avatar = (state.data.assets || {}).avatar;
    if (!avatar) return "";
    const src = avatar.url + "?t=" + encodeURIComponent(token) + "&v=" + avatar.stamp;
    return `<img class="persona-avatar" src="${src}" alt="当前人设头像" width="64" height="64">`;
  }

  function avatarStatus() {
    const avatar = (state.data.assets || {}).avatar;
    if (!avatar) {
      return '<div class="hint">当前没有头像：头像位与托盘图标留空，不会用程序化形象顶替。</div>';
    }
    return `<div class="hint">当前生效：avatar.${escapeHtml(avatar.kind)} · ${Number(
      avatar.size
    ).toLocaleString()} 字节。导入会替换这个文件，你选的原始图片不动。</div>`;
  }

  function panelPersona() {
    const info = state.data.persona || {};
    const prefs = state.data.persona_preferences || {};
    const field = (key, label, hint) => `<div class="form-row" style="align-items:flex-start">
      <label style="padding-top:6px">${label}</label>
      <div style="flex:1">
        <textarea class="field" id="persona-${key}" rows="2" placeholder="${hint}">${escapeHtml(prefs[key] || "")}</textarea>
      </div>
      <button class="ghost" data-save-persona="${key}">保存</button>
    </div>`;
    return wrapPanel(
      "persona",
      `<div class="persona-head">
        ${avatarMarkup()}
      <div class="stat-grid">
          <div class="stat"><div class="k">当前人设</div><div class="v">${escapeHtml(info.name || "数字分身")}</div></div>
          <div class="stat"><div class="k">版本</div><div class="v">${escapeHtml(info.version || "未加载")}</div></div>
        </div>
      </div>
      <div class="import-row">
        <input class="field file" type="file" id="avatarFile" accept=".png,.jpg,.jpeg,.webp" aria-label="选择头像图片">
        <button class="primary" data-avatar-import>导入头像</button>
      </div>
      ${avatarStatus()}
      <div class="hint">只接受 .png / .jpg / .webp，8 MiB 以内；写入 data/assets/${escapeHtml(
        info.id || "remielle"
      )}/avatar.&lt;类型&gt;，同槽位里其它类型的旧头像会被清掉，桌宠模型与背景图不受影响。</div>
      ${unwired("导入人设包", "人设包（persona.json / facts.md / worldbook.md）的目录导入在桌面版管理台，Web 端还没有对应的文件选择通道。")}
      ${field("address", "称呼", "希望被怎么称呼")}
      ${field("relationship", "关系", "你们之间的关系")}
      ${field("boundaries", "边界", "不希望被触碰的话题")}
      ${info.error ? `<div class="hint danger-text">${escapeHtml(info.error)}</div>` : ""}`
    );
  }

  function panelProactive() {
    const status = state.data.proactive || {};
    const settings = status.settings || {};
    const number = (key, label, value, unit, min, max) => `<div class="form-row">
      <label>${label}</label>
      <input class="field" type="number" id="pro-${key}" value="${Number(value || 0)}" min="${min}" max="${max}">
      <span class="unit">${unit}</span>
    </div>`;
    return wrapPanel(
      "proactive",
      `<div class="switch-row">
        <div class="text"><div class="name">允许主动关心</div><div class="desc">关闭后不会再主动发起对话</div></div>
        <button class="switch ${status.enabled ? "on" : ""}" id="pro-enabled"></button>
      </div>
      ${number("quiet_start", "免打扰开始", settings.quiet_start, "点", 0, 23)}
      ${number("quiet_end", "免打扰结束", settings.quiet_end, "点", 0, 23)}
      ${number("daily_limit", "每日上限", settings.daily_limit, "次", 0, 20)}
      ${number("min_interval_hours", "最小间隔", settings.min_interval_hours, "小时", 0, 168)}
      ${number("max_unanswered", "未回复阈值", settings.max_unanswered, "条", 0, 20)}
      <div class="switch-row">
        <div class="text"><div class="name">引用记忆</div><div class="desc">允许主动消息引用本地记忆</div></div>
        <button class="switch ${settings.include_memory ? "on" : ""}" id="pro-memory"></button>
      </div>
      <div class="hint">今日已主动 ${status.daily_count || 0} / ${status.daily_limit || 0} 次；未回复 ${status.unanswered || 0} / ${status.max_unanswered || 0}。</div>
      <div class="form-actions"><button class="primary" id="saveProactive">保存</button></div>`
    );
  }

  function panelTasks() {
    const summary = state.data.settings_summary || {};
    return wrapPanel(
      "tasks",
      `<div class="stat-grid">
        <div class="stat"><div class="k">当前状态</div><div class="v">${escapeHtml(summary.work_state || "空闲")}</div></div>
        <div class="stat"><div class="k">主动提醒</div><div class="v">${summary.proactive_enabled ? "开启" : "关闭"}</div></div>
      </div>
      <div class="hint">任务卡会先给出计划，确认后才执行。执行记录与回执都在管理台。</div>
      <div class="form-actions"><button class="primary" data-open="console-tasks">查看任务</button></div>`
    );
  }

  function panelManage() {
    const data = state.data;
    return wrapPanel(
      "manage",
      `<div class="stat-grid">
        <div class="stat"><div class="k">记忆</div><div class="v">${data.settings_summary.memory_count || 0} 条</div></div>
        <div class="stat"><div class="k">会话</div><div class="v">${(data.sessions || []).length} 个</div></div>
        <div class="stat"><div class="k">工作区</div><div class="v" style="word-break:break-all">${escapeHtml(data.workspace || "未授权")}</div></div>
        <div class="stat"><div class="k">数据目录</div><div class="v">仅本机</div></div>
      </div>
      <div class="hint">管理台提供记忆账本、召回日志、执行审计、失败记录与数据设置。</div>
      <div class="form-actions"><button class="primary" data-open="console">打开管理台</button></div>`
    );
  }

  function panelTheme() {
    const current = state.data.ui.theme;
    const card = (id, label, side, main) => `<button class="theme-card ${current === id ? "on" : ""}" data-theme="${id}">
      <span class="preview"><i class="side" style="background:${side}"></i><i class="main" style="background:${main}"></i></span>
      <span class="label">${label}</span>
    </button>`;
    return wrapPanel(
      "theme",
      `<div class="theme-grid">
        ${card("charcoal-pink", "经典深色", "#1b1b1b", "#141414")}
        ${card("pearl-white", "珍珠白", "#ffffff", "#ececee")}
      </div>
      <div class="hint">主题会立即应用到主窗口、设置与管理台。</div>`
    );
  }

  function panelFont() {
    const current = Number(state.data.ui.font_scale || 100);
    return wrapPanel(
      "font",
      `<div class="choose-row">${[100, 125, 150, 200]
        .map((value) => `<button data-font="${value}" class="${current === value ? "on" : ""}">${value}%</button>`)
        .join("")}</div>
      <div class="hint">字号只影响文字大小，不改变窗口布局。</div>`
    );
  }

  function panelMotion() {
    const on = Boolean(state.data.ui.reduce_motion);
    return wrapPanel(
      "motion",
      `<div class="switch-row">
        <div class="text"><div class="name">减少动效</div><div class="desc">关闭过渡与光标闪烁</div></div>
        <button class="switch ${on ? "on" : ""}" id="motionSwitch"></button>
      </div>
      <div class="hint">开启后界面切换会立即完成，适合晕动敏感或远程桌面。</div>`
    );
  }

  function panelOverlay() {
    const on = Boolean(state.data.ui.overlay_visible);
    const pet = (state.data.assets || {}).pet || {};
    const available = Boolean(pet.available);
    const reason = String(pet.reason || "缺少 Live2D 模型或 avatar.mavatar");
    const disabled = available
      ? ""
      : ` disabled aria-disabled="true" title="${escapeHtml(reason)}"`;
    const notes = available
      ? `<div class="hint">浮窗可以拖动；双击形象会回到主窗口。</div>`
      : `<div class="hint danger-text">桌面浮窗不可用：${escapeHtml(
          reason
        )}。请把 Live2D 模型放到 data/assets/&lt;人设&gt;/live2d/ 目录内（model3.json 或角色名.model3.json），或把 .mavatar 归档放到 data/assets/&lt;人设&gt;/avatar.mavatar 后重启程序。</div>
        <div class="hint">不会显示替代形象；主动消息仍可在主窗口“任务”面板和托盘查看。</div>`;
    return wrapPanel(
      "overlay",
      `<div class="switch-row">
        <div class="text"><div class="name">显示桌面浮窗</div><div class="desc">在桌面右下角显示形象与主动气泡</div></div>
        <button class="switch ${on ? "on" : ""}" id="overlaySwitch"${disabled}></button>
      </div>
      ${notes}`
    );
  }

  function panelBackground() {
    const assets = state.data.assets || {};
    const background = assets.background;
    const enabled = Boolean(state.data.ui.background_enabled);
    const using = enabled && Boolean(background);
    const src = background
      ? background.url + "?t=" + encodeURIComponent(token) + "&v=" + background.stamp
      : "";
    const preview = background
      ? `<img class="bg-preview" src="${src}" alt="当前背景图">`
      : '<div class="bg-preview none">未检测到背景图</div>';
    return wrapPanel(
      "background",
      `${preview}
      <div class="hint">把图片放到 data/assets/remielle/background.png（也支持 .jpg 与 .webp），回到这里启用。程序只读取，不会复制、重命名或删除你的文件；桌面浮窗始终透明。</div>
      <div class="form-actions">
        <button class="primary" data-bg-use ${background && !enabled ? "" : "disabled"}>使用背景</button>
        <button class="ghost" data-bg-clear ${enabled ? "" : "disabled"}>清除背景</button>
      </div>
      <div class="hint">${
        using
          ? "当前已启用：主窗、设置与管理台显示这张图，遮罩由当前主题自动决定。"
          : "当前未启用：界面保持纯色主题。"
      }</div>`
    );
  }

  function desktopView() {
    return state.desktop || {
      directories: [],
      apps: [],
      chains: [],
      capabilities: [],
      trigger_kinds: [],
      recording: { active: false, candidates: [] },
      uia: { available: false, reason: "" },
      summary: {},
      limits: { files: 20 },
    };
  }

  function humanBytes(value) {
    const size = Number(value || 0);
    if (size < 1024) return size + " B";
    if (size < 1024 * 1024) return (size / 1024).toFixed(1) + " KB";
    return (size / 1024 / 1024).toFixed(1) + " MB";
  }

  function deskChip(text, kind) {
    return `<span class="pill${kind ? " " + kind : ""}">${escapeHtml(text)}</span>`;
  }

  function desktopTabBar() {
    const tabs = [
      ["directories", "目录授权"],
      ["files", "文件"],
      ["apps", "应用目录"],
      ["chains", "启动链"],
      ["record", "录制"],
    ];
    return `<div class="desk-tabs">${tabs
      .map(
        ([id, label]) =>
          `<button data-desk-tab="${id}" class="${state.desktopTab === id ? "on" : ""}" ${
            state.desktopTab === id ? 'aria-current="true"' : ""
          }>${label}</button>`
      )
      .join("")}</div>`;
  }

  function panelDesktopDirectories() {
    const view = desktopView();
    const capabilityOptions = (view.capabilities || [])
      .map(
        (item) =>
          `<label class="cap-check"><input type="checkbox" value="${escapeHtml(item.value)}" ${
            ["fs-read", "fs-search"].indexOf(item.value) >= 0 ? "checked" : ""
          }> <span>${escapeHtml(item.label)}</span></label>`
      )
      .join("");
    const rows = (view.directories || [])
      .map((row) => {
        const caps = (row.capabilities || [])
          .map((cap) => {
            const label = (view.capabilities || []).filter((item) => item.value === cap)[0];
            return deskChip(label ? label.label : cap, "accent");
          })
          .join("");
        const stateLabel =
          row.index_status === "running" || row.indexing
            ? "正在索引"
            : row.index_status === "ready"
            ? "索引就绪"
            : row.index_status === "failed"
            ? "索引失败"
            : "等待索引";
        return `<div class="desk-item">
          <div class="desk-item-main">
            <div class="name mono" title="${escapeHtml(row.path || "")}">${escapeHtml(row.path || "")}</div>
            <div class="desk-meta">${caps}${
              row.workspace_selected ? deskChip("当前工作目录", "accent") : ""
            }${deskChip(stateLabel, row.index_status === "failed" ? "danger" : "")}</div>
            <div class="hint">${Number(row.files_indexed || 0)} 个文件 · 跳过 ${Number(row.files_skipped || 0)} 项${
              row.index_error ? " · " + escapeHtml(row.index_error) : ""
            }</div>
          </div>
          <div class="desk-actions">
            <button class="ghost" data-desk-act="reindex-directory" data-id="${escapeHtml(row.grant_id || "")}">重建索引</button>
            <button class="ghost danger-text" data-desk-act="revoke-directory" data-id="${escapeHtml(row.grant_id || "")}">撤销授权</button>
          </div>
        </div>`;
      })
      .join("");
    return `<div class="stat-grid">
      <div class="stat"><div class="k">授权目录</div><div class="v">${Number((view.summary || {}).directories || 0)} 个</div></div>
      <div class="stat"><div class="k">已索引文件</div><div class="v">${Number((view.summary || {}).catalog_files || 0).toLocaleString()} 条</div></div>
    </div>
    <div class="desk-form">
      <div class="section-title">新增授权目录</div>
      <div class="form-row"><label for="deskGrantPath">目录路径</label><input class="field mono" id="deskGrantPath" placeholder="输入已存在的绝对路径"></div>
      <div class="cap-grid">${capabilityOptions}</div>
      <label class="cap-check"><input type="checkbox" id="deskSensitive"> <span>允许敏感目录（仅在你明确需要时勾选）</span></label>
      <div class="form-actions"><button class="primary" data-desk-act="grant-directory">授权并索引</button></div>
      <div class="hint">这里的授权独立生效，不需要再在对话里选择工作目录；文件工具只在工作/执行模式可用，写入、删除和外发仍只获得你勾选的能力。</div>
    </div>
    <div class="section-title">已授权目录</div>
    <div class="desk-list">${rows || '<div class="empty-note">还没有授权目录。</div>'}</div>`;
  }

  function panelDesktopFiles() {
    const search = state.desktopSearch || {};
    const rows = (search.candidates || [])
      .map((row) => `<div class="desk-item compact">
        <div class="desk-item-main">
          <div class="name">${escapeHtml(row.name || row.rel_path || "文件")}</div>
          <div class="desk-meta">${deskChip(humanBytes(row.size_bytes || row.size || 0))}${deskChip(
            row.grant_id || ""
          )}</div>
          <div class="hint mono">${escapeHtml(row.rel_path || "")}</div>
        </div>
        <div class="desk-actions">
          <button class="ghost" data-desk-act="open-file" data-ref="${escapeHtml(
            row.file_ref || ""
          )}">打开</button>
          <button class="ghost" data-desk-act="move-file" data-ref="${escapeHtml(
            row.file_ref || ""
          )}">移动</button>
          <button class="ghost danger-text" data-desk-act="delete-file" data-ref="${escapeHtml(
            row.file_ref || ""
          )}">移入回收站</button>
        </div>
      </div>`)
      .join("");
    return `<div class="desk-form">
      <div class="form-row"><label for="deskFileQuery">搜索文件</label><input class="field" id="deskFileQuery" placeholder="文件名或路径关键词" value="${escapeHtml(
        search.query || ""
      )}"><button class="primary" data-desk-act="search-files">搜索</button></div>
      <div class="hint">搜索只返回授权目录中的元数据；打开、移动和删除都继续走已有权限与确认链。</div>
    </div>
    <div class="section-title">搜索结果${
      search.matched != null ? " · " + Number(search.matched || 0) : ""
    }</div>
    <div class="desk-list">${
      rows || '<div class="empty-note">输入关键词后搜索授权目录。</div>'
    }</div>
    ${
      (search.refused || []).length
        ? `<div class="hint danger-text">${escapeHtml(
            search.refused.join("；")
          )}</div>`
        : ""
    }`;
  }

  function panelDesktopApps() {
    const view = desktopView();
    const statusLabel = {
      ready: "就绪",
      missing: "缺失",
      drifted: "身份漂移",
      blocked: "已阻止",
    };
    const rows = (view.apps || [])
      .slice(0, 120)
      .map((row) => {
        const allowed = Boolean(row.allowed);
        const statusKind =
          row.status === "ready"
            ? "accent"
            : row.status === "drifted" || row.status === "missing"
            ? "danger"
            : "";
        return `<div class="desk-item">
          <div class="desk-item-main">
            <div class="name">${escapeHtml(row.name || row.target || "应用")}</div>
            <div class="desk-meta">${deskChip(statusLabel[row.status] || row.status || "", statusKind)}${
              allowed ? deskChip("已允许启动", "accent") : ""
            }${deskChip(row.source || "应用")}</div>
            <div class="hint mono" title="${escapeHtml(row.target || "")}">${escapeHtml(
              row.target || ""
            )}</div>
            <div class="hint">${escapeHtml(
              row.publisher || "无签名身份"
            )}${row.file_version ? " · " + escapeHtml(row.file_version) : ""}</div>
          </div>
          <div class="desk-actions">${
            allowed
              ? `<button class="ghost danger-text" data-desk-act="revoke-app" data-id="${escapeHtml(
                  row.grant_id || ""
                )}">撤销</button>`
              : `<button class="ghost" data-desk-act="allow-app" data-id="${escapeHtml(
                  row.app_id || ""
                )}" ${row.status === "missing" ? "disabled" : ""}>${
                  row.status === "drifted" ? "重新登记" : "允许启动"
                }</button>`
          }</div>
        </div>`;
      })
      .join("");
    return `<div class="stat-grid">
      <div class="stat"><div class="k">应用目录</div><div class="v">${Number((view.summary || {}).apps || 0)} 条</div></div>
      <div class="stat"><div class="k">允许启动</div><div class="v">${Number((view.summary || {}).apps_allowed || 0)} 个</div></div>
    </div>
    <div class="form-actions"><button class="primary" data-desk-act="scan-apps">扫描本机应用</button><span class="hint">发现不等于授权；扫描不会启动程序。</span></div>
    <div class="desk-form">
      <div class="section-title">手动登记</div>
      <div class="form-row"><label for="deskAppName">名称</label><input class="field" id="deskAppName" placeholder="例如 Visual Studio Code"></div>
      <div class="form-row"><label for="deskAppTarget">目标</label><input class="field mono" id="deskAppTarget" placeholder="程序绝对路径，或 shell:RecycleBinFolder"></div>
      <div class="form-row"><label for="deskAppArgs">参数</label><input class="field mono" id="deskAppArgs" placeholder="可留空"></div>
      <div class="form-row"><label for="deskAppCwd">工作目录</label><input class="field mono" id="deskAppCwd" placeholder="可留空"></div>
      <div class="form-actions"><button class="primary" data-desk-act="register-app">登记并允许启动</button></div>
    </div>
    <div class="section-title">发现结果${
      (view.apps || []).length > 120 ? " · 当前显示前 120 条" : ""
    }</div>
    <div class="desk-list">${rows || '<div class="empty-note">还没有扫描或登记应用。</div>'}</div>`;
  }

  function panelDesktopChains() {
    const view = desktopView();
    const readyApps = (view.apps || []).filter(
      (row) => row.status === "ready" && row.allowed
    );
    const appOptions = (selected) =>
      readyApps
        .map(
          (row) =>
            `<option value="${escapeHtml(row.app_id || "")}" ${
              row.app_id === selected ? "selected" : ""
            }>${escapeHtml(row.name || row.target || row.app_id)}</option>`
        )
        .join("");
    const editingId = state.desktopEditingChain;
    const editing =
      (view.chains || []).filter((row) => row.chain_id === editingId)[0] || null;
    const draft =
      state.desktopChainDraft &&
      (!editingId || state.desktopChainDraft.chain_id === editingId)
        ? state.desktopChainDraft
        : null;
    const base = draft || editing || {};
    const detection = base.detection_rules || {};
    const readiness = base.readiness_rules || {};
    const triggerConfig = base.trigger_config || {};
    const triggerKind = base.trigger_kind || "auto";
    const triggerOptions = (view.trigger_kinds || []).length
      ? view.trigger_kinds
      : [
          { value: "auto", label: "自动启动" },
          { value: "argument", label: "固定参数" },
          { value: "uri", label: "URI Scheme" },
          { value: "api", label: "已登记 API 入口" },
          { value: "uia", label: "UI Automation" },
          { value: "manual", label: "人工接管" },
        ];
    const selectedTrigger = triggerOptions
      .map(
        (item) =>
          `<option value="${escapeHtml(item.value)}" ${
            item.value === triggerKind ? "selected" : ""
          }>${escapeHtml(item.label)}</option>`
      )
      .join("");
    const detectionResult = state.desktopDetection || null;
    const detectionRows =
      detectionResult && detectionResult.chain_id === editingId
        ? (detectionResult.candidates || [])
            .map(
              (row) => `<div class="desk-item compact">
                <div class="desk-item-main">
                  <div class="name">${escapeHtml(row.name || "目标进程")}</div>
                  <div class="desk-meta">${deskChip("PID " + row.pid)}${deskChip(
                    row.exe_path || ""
                  )}</div>
                  <div class="hint mono">${escapeHtml(
                    (row.windows || [])
                      .map((window) => window.title || window.class_name || "")
                      .filter(Boolean)
                      .join(" · ") || "没有可见窗口"
                  )}</div>
                </div>
                <div class="desk-actions">
                  <button class="ghost" data-desk-act="use-detected-target" data-pid="${Number(
                    row.pid || 0
                  )}">用这个实例</button>
                </div>
              </div>`
            )
            .join("")
        : "";
    const rows = (view.chains || [])
      .map(
        (row) => `<div class="desk-item">
          <div class="desk-item-main">
            <div class="name">${escapeHtml(row.name || row.chain_id || "启动链")}</div>
            <div class="desk-meta">${
              row.enabled ? deskChip("已启用", "accent") : deskChip("已停用")
            }${deskChip(row.trigger_kind || "auto")}${
              row.runnable ? deskChip("可执行", "accent") : ""
            }</div>
            <div class="hint">${escapeHtml(
              row.starter_name || row.starter_app_id || ""
            )} → ${escapeHtml(
              row.target_name || row.target_app_id || ""
            )} · 超时 ${Number((row.readiness_rules || {}).timeout_seconds || 30)} 秒</div>
          </div>
          <div class="desk-actions">
            <button class="ghost" data-desk-act="toggle-chain" data-id="${escapeHtml(
              row.chain_id || ""
            )}" data-enabled="${row.enabled ? "1" : "0"}">${
              row.enabled ? "停用" : "启用"
            }</button>
            <button class="ghost" data-desk-act="run-chain" data-id="${escapeHtml(
              row.chain_id || ""
            )}" ${row.runnable ? "" : "disabled"}>生成启动提案</button>
            <button class="ghost" data-desk-act="edit-chain" data-id="${escapeHtml(
              row.chain_id || ""
            )}">编辑</button>
            <button class="ghost" data-desk-act="detect-chain" data-id="${escapeHtml(
              row.chain_id || ""
            )}">检测实例</button>
            <button class="ghost danger-text" data-desk-act="delete-chain" data-id="${escapeHtml(
              row.chain_id || ""
            )}">删除</button>
          </div>
        </div>`
      )
      .join("");
    return `<div class="stat-grid">
      <div class="stat"><div class="k">启动链</div><div class="v">${Number((view.summary || {}).chains || 0)} 条</div></div>
      <div class="stat"><div class="k">当前可执行</div><div class="v">${Number((view.summary || {}).chains_runnable || 0)} 条</div></div>
    </div>
    <div class="desk-list">${rows || '<div class="empty-note">还没有启动链。先在应用目录里允许起点与目标应用。</div>'}</div>
    <div class="desk-form">
      <div class="section-title">${editing ? "编辑启动链" : "新建启动链"}</div>
      <div class="form-row"><label for="deskChainName">名称</label><input class="field" id="deskChainName" placeholder="例如 Codex++ → Codex" value="${escapeHtml(
        base.name || ""
      )}"></div>
      <div class="form-row"><label for="deskChainStarter">起点应用</label><select class="field" id="deskChainStarter">${appOptions(
        base.starter_app_id
      )}</select></div>
      <div class="form-row"><label for="deskChainTarget">目标应用</label><select class="field" id="deskChainTarget">${appOptions(
        base.target_app_id
      )}</select></div>
      <div class="form-row"><label for="deskChainTrigger">触发方式</label><select class="field" id="deskChainTrigger">${selectedTrigger}</select></div>
      <div data-trigger-field="argument" ${
        triggerKind === "argument" ? "" : "hidden"
      }>
        <div class="form-row"><label for="deskChainArguments">固定参数</label><textarea class="field mono" id="deskChainArguments" rows="3" placeholder="每行一个参数">${escapeHtml(
          (triggerConfig.arguments || []).join("\n")
        )}</textarea></div>
      </div>
      <div data-trigger-field="uri" ${
        triggerKind === "uri" ? "" : "hidden"
      }>
        <div class="form-row"><label for="deskChainUri">固定 URI</label><input class="field mono" id="deskChainUri" placeholder="codexplusplus://open/project" value="${escapeHtml(
          triggerConfig.uri || ""
        )}"></div>
      </div>
      <div data-trigger-field="api" ${
        triggerKind === "api" ? "" : "hidden"
      }>
        <div class="form-row"><label for="deskChainApiApp">API 入口</label><select class="field" id="deskChainApiApp">${appOptions(
          triggerConfig.api_app_id || ""
        )}</select></div>
        <div class="form-row"><label for="deskChainApiArguments">API 参数</label><textarea class="field mono" id="deskChainApiArguments" rows="3" placeholder="每行一个参数">${escapeHtml(
          (triggerConfig.arguments || []).join("\n")
        )}</textarea></div>
      </div>
      <div data-trigger-field="uia" ${
        triggerKind === "uia" ? "" : "hidden"
      }>
        <div class="form-row"><label for="deskChainUiaWindowTitle">窗口标题</label><input class="field" id="deskChainUiaWindowTitle" placeholder="标题包含的文字" value="${escapeHtml(
          triggerConfig.window_title_contains || ""
        )}"></div>
        <div class="form-row"><label for="deskChainUiaWindowClass">窗口类</label><input class="field mono" id="deskChainUiaWindowClass" placeholder="可留空" value="${escapeHtml(
          triggerConfig.window_class || ""
        )}"></div>
        <div class="form-row"><label for="deskChainUiaAutomationId">Automation ID</label><input class="field mono" id="deskChainUiaAutomationId" placeholder="优先使用 Automation ID" value="${escapeHtml(
          triggerConfig.automation_id || ""
        )}"></div>
        <div class="form-row"><label for="deskChainUiaControlName">控件名称</label><input class="field" id="deskChainUiaControlName" placeholder="Automation ID 为空时使用" value="${escapeHtml(
          triggerConfig.control_name || ""
        )}"></div>
        <div class="form-row"><label for="deskChainUiaControlType">控件类型</label><input class="field" id="deskChainUiaControlType" placeholder="例如 Button" value="${escapeHtml(
          triggerConfig.control_type || ""
        )}"></div>
        <div class="hint">${
          (view.uia || {}).available
            ? "UIA 适配器可用；只会点击起点应用进程内的唯一匹配窗口。"
            : "UIA 当前不可用：" + escapeHtml((view.uia || {}).reason || "未知原因")
        }</div>
      </div>
      <div class="form-row"><label for="deskChainTimeout">就绪超时</label><input class="field number" id="deskChainTimeout" type="number" min="1" max="120" step="1" value="${Number(
        readiness.timeout_seconds || 30
      )}"><span class="unit">秒</span></div>
      <div class="form-row"><label for="deskChainWindowTitle">检测标题</label><input class="field" id="deskChainWindowTitle" placeholder="标题包含文字，可留空" value="${escapeHtml(
        (detection.window_title_contains || []).join("\n")
      )}"></div>
      <div class="form-row"><label for="deskChainWindowClass">检测窗口类</label><input class="field mono" id="deskChainWindowClass" placeholder="精确窗口类，可留空" value="${escapeHtml(
        (detection.window_class || []).join("\n")
      )}"></div>
      <div class="cap-grid">
        <label class="cap-check"><input type="checkbox" id="deskChainDescendant" ${
          detection.require_descendant ? "checked" : ""
        }> <span>目标必须在起点进程树中</span></label>
        <label class="cap-check"><input type="checkbox" id="deskChainWindow" ${
          detection.require_window !== false ? "checked" : ""
        }> <span>目标必须有可见窗口</span></label>
        <label class="cap-check"><input type="checkbox" id="deskChainReuse" ${
          detection.reuse_existing ? "checked" : ""
        }> <span>允许复用已运行实例</span></label>
      </div>
      <div class="form-actions">
        <button class="primary" data-desk-act="save-chain" data-id="${escapeHtml(
          editingId || ""
        )}">${editing ? "保存修改" : "保存启动链"}</button>
        ${
          editing
            ? '<button class="ghost" data-desk-act="cancel-edit-chain">取消编辑</button>'
            : ""
        }
      </div>
      <div class="hint">保存只固化触发与检测配置；执行时仍会重新复核两个应用的身份与授权。</div>
    </div>
    ${
      editingId
        ? `<div class="section-title">检测到的目标实例</div>
           <div class="desk-list">${
             detectionRows ||
             '<div class="empty-note">还没有检测结果；点击列表中的“检测实例”。</div>'
           }</div>`
        : ""
    }`;
  }

  function panelDesktopRecord() {
    const view = desktopView();
    const recording = view.recording || {
      active: false,
      candidates: [],
      candidate_count: 0,
    };
    const starterOptions = (view.apps || [])
      .filter((row) => row.status === "ready" && row.allowed)
      .map(
        (row) =>
          `<option value="${escapeHtml(row.app_id || "")}">${escapeHtml(
            row.name || row.target || row.app_id
          )}</option>`
      )
      .join("");
    if (!recording.active) {
      return `<div class="desk-form">
        <div class="section-title">录制启动链</div>
        <div class="form-row"><label for="deskRecordStarter">中转应用</label><select class="field" id="deskRecordStarter">${starterOptions}</select></div>
        <div class="form-actions"><button class="primary" data-desk-act="start-recording">开始录制</button></div>
        <div class="hint">开始后手动完成一次启动目标应用的操作，再刷新候选。录制不会启动程序，也不会自动授予权限。</div>
      </div>`;
    }
    const starter = (view.apps || []).filter(
      (row) => row.app_id === recording.starter_app_id
    )[0];
    const rows = (recording.candidates || [])
      .map(
        (row, index) => `<div class="desk-item">
          <div class="desk-item-main">
            <div class="name">${escapeHtml(row.name || "新进程")}</div>
            <div class="desk-meta">${deskChip("PID " + row.pid)}${
              row.app_name ? deskChip(row.app_name, "accent") : deskChip("不在应用目录")
            }${row.app_allowed ? deskChip("已允许", "accent") : ""}</div>
            <div class="hint mono">${escapeHtml(row.exe_path || "")}</div>
            <div class="hint">${escapeHtml(
              (row.windows || [])
                .map((window) => window.title || window.class_name || "")
                .filter(Boolean)
                .join(" · ") || "没有新的可见窗口"
            )}</div>
          </div>
          <div class="desk-actions">
            <button class="ghost" data-desk-act="select-record-candidate" data-index="${index}" ${
              row.selectable ? "" : "disabled"
            }>${row.selectable ? "选为目标" : "先登记应用"}</button>
          </div>
        </div>`
      )
      .join("");
    return `<div class="desk-form">
      <div class="section-title">正在录制</div>
      <div class="desk-meta">${deskChip(
        starter ? starter.name || starter.target : recording.starter_app_id,
        "accent"
      )}${deskChip("剩余 " + Number(recording.expires_in_seconds || 0) + " 秒")}</div>
      <div class="hint">${escapeHtml(recording.instructions || "")}</div>
      <div class="form-actions">
        <button class="primary" data-desk-act="refresh-recording">刷新候选</button>
        <button class="ghost danger-text" data-desk-act="cancel-recording">取消录制</button>
      </div>
    </div>
    <div class="section-title">新出现的进程与窗口 · ${Number(
      recording.candidate_count || 0
    )}</div>
    <div class="desk-list">${rows || '<div class="empty-note">现在手动启动一次目标应用，然后刷新候选。</div>'}</div>`;
  }

  function panelDesktop() {
    const body = {
      directories: panelDesktopDirectories,
      files: panelDesktopFiles,
      apps: panelDesktopApps,
      chains: panelDesktopChains,
      record: panelDesktopRecord,
    }[state.desktopTab] || panelDesktopDirectories;
    return wrapPanel("desktop", desktopTabBar() + body());
  }

  async function loadDesktop() {
    const view = await guard(api("/api/desktop"));
    if (!view) return;
    state.desktop = view;
    if (state.panel === "desktop") render();
  }

  async function desktopCommand(payload, success) {
    const result = await guard(post("/api/desktop", payload));
    if (!result) return null;
    if (result.desktop) state.desktop = result.desktop;
    if (result.search) state.desktopSearch = result.search;
    if (result.recording) {
      state.desktop = state.desktop || {};
      state.desktop.recording = result.recording;
    }
    if (result.detection) state.desktopDetection = result.detection;
    if (success) toast(success);
    render();
    return result;
  }

  async function moveDesktopFile(fileRef) {
    const writable = (desktopView().directories || []).filter(
      (row) => (row.capabilities || []).indexOf("fs-write") >= 0
    );
    if (!writable.length) {
      toast("没有授予 fs-write 的目标目录。");
      return;
    }
    const host = document.createElement("div");
    host.innerHTML = `<div class="sheet">
      <div class="form-row"><label for="deskMoveGrant">目标目录</label><select class="field" id="deskMoveGrant">${writable
        .map(
          (row) =>
            `<option value="${escapeHtml(row.grant_id || "")}">${escapeHtml(
              row.path || ""
            )}</option>`
        )
        .join("")}</select></div>
      <div class="form-row"><label for="deskMovePath">相对路径</label><input class="field mono" id="deskMovePath" placeholder="archive/report.md"></div>
      <div class="hint">这里只生成移动提案，仍需回到对话确认；目标已存在或跨卷会在提案阶段拒绝。</div>
    </div>`;
    const body = host.firstChild;
    const ok = await CompanionDialog.form(
      "选择目标授权目录并填写目录内相对路径。",
      body,
      { title: "移动文件", okText: "生成提案" }
    );
    if (!ok) return;
    const result = await desktopCommand(
      {
        action: "move_file",
        file_ref: fileRef,
        target_grant_id: body.querySelector("#deskMoveGrant").value,
        target_rel_path: body.querySelector("#deskMovePath").value,
      }
    );
    if (result) {
      toast(
        result.auto_executed
          ? (result.execution || {}).summary || "文件已移动"
          : "移动提案已生成，回到对话确认"
      );
    }
  }

  function settingsView() {
    return state.settings || { settings: {}, api_key: {}, runtime: {}, model_service: {} };
  }

  // The labels and the exact sentence the model will be told both come from the
  // server, so the page can promise "回答方式只写进提示词" and show it.
  function outputFormats() {
    return settingsView().output_formats || [];
  }

  function outputFormatHint(value) {
    const hit = outputFormats().filter((item) => item.value === String(value || "default"))[0];
    return hit ? hit.hint || "" : "";
  }

  function permissionTiers() {
    return (settingsView().permissions || {}).tiers || [];
  }

  function permissionHint(value) {
    const hit = permissionTiers().filter((item) => item.value === String(value || ""))[0];
    return hit ? hit.hint || "" : "";
  }

  function modelCatalog() {
    return (settingsView().model_service || {}).catalog || {};
  }

  // Mirrors settings.catalog_window: an exact id wins, otherwise the longest
  // family prefix whose next character is a separator. Dated upstream ids such
  // as "qwen-plus-2025-04-28" have to resolve the same way here and on the
  // server, or the hint contradicts the saved value.
  function catalogWindow(name) {
    const catalog = modelCatalog();
    const text = String(name || "");
    if (catalog[text] > 0) return Number(catalog[text]);
    let best = "";
    Object.keys(catalog).forEach((key) => {
      const separator = "-._/".indexOf(text.charAt(key.length)) >= 0;
      if (text.length > key.length && text.indexOf(key) === 0 && separator && key.length > best.length) {
        best = key;
      }
    });
    return best ? Number(catalog[best]) : 0;
  }

  function windowHintText(name) {
    const known = catalogWindow(name);
    return known
      ? "内置对照表给出 " + known.toLocaleString() + " tokens，可按需下调。"
      : "这个模型不在对照表里，请按上游文档手填窗口大小。";
  }

  function applyModelWindow(name) {
    const stored = (settingsView().model_service || {}).stored || {};
    const known = Number(stored[name]) > 0 ? Number(stored[name]) : catalogWindow(name);
    const input = $("#st-context_tokens");
    if (input && known) input.value = String(known);
    const hint = $("#modelWindowHint");
    if (hint) hint.textContent = windowHintText(name);
  }

  function fieldError(key) {
    const errors = (state.settings || {}).errors || [];
    const hit = errors.filter((item) => item.key === key)[0];
    return hit ? `<div class="field-error">${escapeHtml(hit.error)}</div>` : "";
  }

  async function loadSettings() {
    const result = await guard(api("/api/settings"));
    if (result) state.settings = result;
    if (state.panel === "model" || state.panel === "rag" || state.panel === "memory" || state.panel === "advanced") render();
  }

  // The model panel and the RAG panel draw the same bar out of the same
  // ContextView; a second renderer here would let the two settings surfaces
  // disagree with the composer gauge in the main window.
  function budgetBlock() {
    const view = settingsView();
    const values = view.settings || {};
    const runtime = view.runtime || {};
    const context = view.context;
    const preview = context
      ? null
      : window.CompanionContextBar
      ? window.CompanionContextBar.reservation(
          values.context_tokens,
          values.max_tokens,
          runtime.chars_per_token
        )
      : null;
    const bar = window.CompanionContextBar ? window.CompanionContextBar.markup(context || preview) : "";
    const barNote = context
      ? "上一条提示词的真实分段；按 token 估算，窗口以「上下文窗口」设置为准。"
      : "还没有发出过一轮对话，读不到实际占用。下面只显示当前窗口与 max_tokens 算出的回复预留。";
    return `<div class="budget-card"><div class="budget-title">上下文预算</div>${bar}<div class="hint">${escapeHtml(barNote)}</div></div>`;
  }

  function panelModel() {
    const view = settingsView();
    const values = view.settings || {};
    const runtime = view.runtime || {};
    const key = view.api_key || {};
    const names = [];
    [values.model].concat(state.models.map((item) => item.id)).forEach((name) => {
      const text = String(name || "");
      if (text && names.indexOf(text) < 0) names.push(text);
    });
    const option = (name) =>
      `<option value="${escapeHtml(name)}" ${name === values.model ? "selected" : ""}>${escapeHtml(name)}</option>`;
    const block = (field, row) => `<div class="form-block">${row}${fieldError(field)}</div>`;
    const slider = (field, label, min, max, step) =>
      block(
        field,
        `<div class="form-row">
        <label for="st-${field}">${label}</label>
        <input class="slider" type="range" id="rng-${field}" data-sync="${field}" min="${min}" max="${max}" step="${step}" value="${Number(values[field]) || 0}" aria-label="${label} 滑块">
        <input class="field number" type="number" id="st-${field}" data-setting="${field}" min="${min}" max="${max}" step="${step}" value="${Number(values[field]) || 0}">
      </div>`
      );
    const number = (field, label, min, max, step) =>
      block(
        field,
        `<div class="form-row">
        <label for="st-${field}">${label}</label>
        <input class="field number" type="number" id="st-${field}" data-setting="${field}" min="${min}" max="${max}" step="${step}" value="${Number(values[field]) || 0}">
        <span class="unit">tokens</span>
      </div>`
      );
    const windowHint = windowHintText(values.model);
    return wrapPanel(
      "model",
      block(
        "model",
        `<div class="form-row">
        <label for="st-model">模型</label>
        <select class="field" id="st-model" data-setting="model">${names.map(option).join("") || '<option value="">未设置</option>'}</select>
        <button class="ghost" id="modelOpen">服务接入</button>
      </div>`
      ) +
      `<div class="hint">${escapeHtml(key.present ? "密钥已保存：" + key.masked : "还没有 API 密钥，点“服务接入”填写。")}（${escapeHtml(key.source || "config/keys.json")}，不入库、不进备份）</div>
      ${slider("temperature", "temperature", 0, 2, 0.05)}
      ${slider("top_p", "top_p", 0, 1, 0.05)}
      ${number("max_tokens", "max_tokens", 1, 32768, 1)}
      ${number("context_tokens", "上下文窗口", 4000, 2000000, 512)}
      <div class="hint" id="modelWindowHint">${escapeHtml(windowHint)}</div>
      ${budgetBlock()}
      <div class="switch-row">
        <div class="text"><div class="name">离线模式</div><div class="desc">不调用远程模型，只走本地演示回复</div></div>
        <button class="switch ${values.offline ? "on" : ""}" data-switch="offline" aria-label="离线模式"></button>
      </div>
      <div class="hint">当前生效：${runtime.offline ? "离线" : "远程"} · ${escapeHtml(runtime.model || values.model || "未设置")} · 上下文折算约 ${(runtime.context_chars || 0).toLocaleString()} 字符${
        runtime.forced_offline ? " · 已由启动参数强制离线" : ""
      }</div>
      ${saveRow()}`
    );
  }

  function panelRag() {
    const values = settingsView().settings || {};
    const runtime = settingsView().runtime || {};
    return wrapPanel(
      "rag",
      numberRow("memory_top_k", "召回条数 Top-K", 1, 20, 1, "条") +
        `<div class="form-block"><div class="form-row">
          <label for="st-memory_score_threshold">相似度阈值</label>
          <input class="slider" type="range" id="rng-memory_score_threshold" data-sync="memory_score_threshold" min="0" max="1" step="0.05" value="${Number(values.memory_score_threshold) || 0}" aria-label="相似度阈值 滑块">
          <input class="field number" type="number" id="st-memory_score_threshold" data-setting="memory_score_threshold" min="0" max="1" step="0.05" value="${Number(values.memory_score_threshold) || 0}">
          <span class="unit">0–1</span>
        </div>${fieldError("memory_score_threshold")}</div>
      <div class="hint">Top-K 管两条链：每轮最多带 ${Number(values.memory_top_k) || 0} 条记忆，也最多带同样多块文档。相似度阈值只作用于记忆链——文档走本机词法（BM25），分数与记忆分数不可比，所以不给它设阈值${Number(
        values.memory_score_threshold
      ) > 0 ? `，低于 ${Number(values.memory_score_threshold).toFixed(2)} 的记忆不带` : ""}。${
        runtime.offline
          ? "现在是离线模式：一轮回复不组装提示词，记忆与文档都不进上下文；检索本身照常可用（记忆窗口与管理台走同一套本机检索）。"
          : "文档命中会同时出现在右栏「来源」里，能点开看块号与原文路径。"
      }</div>
      ${budgetBlock()}
      ${unwired("上传文档", "入库、切块与检索的后端已经接好（文档命中会进上下文与来源），但上传进度与解析失败这两种界面还没有经审核的画板，因此这块仍按未接入显示。")}
      ${unwired("文档列表", "列表可以按 document_id 列出与删除，但删除确认与索引重建的界面状态同样等画板，这里不放假的表格。")}
      ${saveRow()}`
    );
  }

  function panelMcp() {
    const view = mcpView();
    const rows = view.rows || [];
    const summary = view.summary || {};
    const issues = (view.issues || []).concat(view.error ? [view.error] : []);
    const keyInfo = view.keys || {};
    const head = `<div class="mcp-bar">
      <div class="text">
        <div class="name">${rows.length
          ? `${Number(summary.servers) || 0} 个服务 · ${Number(summary.connected) || 0} 已连接 · 共 ${Number(summary.tools) || 0} 个工具`
          : "还没有添加 MCP 服务器，添加并连接后才会出现工具。"}</div>
        <div class="desc">开启的工具每次调用都先出提案，确认后才真正出网。</div>
      </div>
      <button class="ghost" data-mcp-reload ${rows.length ? "" : "disabled"}>重新加载全部</button>
      <button class="primary" data-mcp-add>添加服务器</button>
    </div>`;
    const offline = view.offline
      ? '<div class="hint">现在是离线模式：面板不主动连接，点「连接并获取工具」仍会真的出网。</div>'
      : "";
    const trouble = issues.length
      ? issues.map((line) => `<div class="field-error">${escapeHtml(line)}</div>`).join("")
      : "";
    const keys = keyInfo.exists
      ? `<div class="hint">凭据文件已有 ${Number(keyInfo.entries) || 0} 项（${escapeHtml(
          keyInfo.file || "config/mcp_keys.json"
        )}），界面只显示名称与掩码。</div>`
      : '<div class="hint">还没有凭据文件：第一次为服务器写令牌时才会创建 config/mcp_keys.json。</div>';
    return wrapPanel(
      "mcp",
      head +
        trouble +
        offline +
        (rows.length
          ? rows.map((row) => mcpServerCard(row)).join("")
          : '<div class="empty-note">还没有添加 MCP 服务器，添加并连接后才会出现工具。</div>') +
        `<div class="hint">工具变更在下一次对话生效。</div>
        ${keys}
        <div class="hint">本机的技能（data/skills/&lt;id&gt;/SKILL.md）不受影响，仍在管理台的「技能」页查看。</div>
        <div class="form-actions"><button class="ghost" data-open="console">打开管理台</button></div>`
    );
  }

  function mcpView() {
    return (
      state.mcp || {
        rows: [],
        summary: { servers: 0, connected: 0, tools: 0, enabled_tools: 0 },
        transports: [],
        issues: [],
        keys: {},
        limits: {},
        offline: false,
      }
    );
  }

  // S04d 的五个状态：停用与在线来自表与连接，「发现中」只在请求在飞的这段时间里显示，
  // 超时是远端原文里的关键字，缺凭据指 config/mcp_keys.json 里没有这一台。
  function mcpState(row) {
    if (state.mcpBusy[row.server_id]) return { text: "发现中", cls: "accent" };
    if (!row.enabled) return { text: "服务已停用", cls: "" };
    if (row.state === "connected") return { text: "已连接", cls: "success" };
    if (row.state === "failed") {
      const timeout = /超时|timeout/i.test(String(row.error || ""));
      return {
        text: timeout ? `握手超时（${Number(row.timeout) || 0} 秒）` : "错误",
        cls: "danger",
      };
    }
    const cred = row.credentials || {};
    return { text: cred.present ? "未连接" : "未连接，缺少凭据", cls: "" };
  }

  function mcpEndpoint(row) {
    const parts = [row.transport_label || row.transport];
    if (row.transport === "stdio") {
      parts.push([row.command].concat(row.args || []).filter(Boolean).join(" "));
      if (row.cwd) parts.push(row.cwd);
    } else {
      parts.push(row.url);
    }
    return parts.filter((text) => String(text || "").trim()).join(" · ");
  }

  function mcpServerCard(row) {
    const status = mcpState(row);
    const busy = state.mcpBusy[row.server_id];
    const open = Boolean(state.mcpOpen[row.server_id]);
    const tools = row.tools || [];
    const cred = row.credentials || {};
    const endpoint = mcpEndpoint(row);
    const missingCredential = !cred.present && row.state === "down" && row.enabled;
    const actions = [];
    if (row.state === "connected") {
      actions.push(`<button class="ghost" data-mcp-action="refresh" data-id="${escapeHtml(row.server_id)}"${busy ? " disabled" : ""}>重新校验</button>`);
      actions.push(`<button class="ghost" data-mcp-action="disconnect" data-id="${escapeHtml(row.server_id)}"${busy ? " disabled" : ""}>断开连接</button>`);
    } else {
      actions.push(`<button class="ghost" data-mcp-action="connect" data-id="${escapeHtml(row.server_id)}"${busy ? " disabled" : ""}>${row.error ? "重试" : "连接并获取工具"}</button>`);
    }
    if (tools.length) {
      actions.push(`<button class="ghost" data-mcp-toggle="${escapeHtml(row.server_id)}">${open ? "收起" : "展开工具"}</button>`);
    }
    actions.push(`<button class="ghost" data-mcp-edit="${escapeHtml(row.server_id)}">编辑</button>`);
    actions.push(`<button class="ghost danger-text" data-mcp-remove="${escapeHtml(row.server_id)}">移除</button>`);
    // 同一条收敛说明只出现一次：收起时留在卡片上，展开后交给 S04e 的工具清单。
    const errors = open
      ? ""
      : (row.issues || []).map((line) => `<div class="hint">${escapeHtml(line)}</div>`).join("");
    return `<div class="mcp-server">
      <div class="mcp-row">
        <div class="text">
          <div class="name">${escapeHtml(row.label || row.server_id)}
            <span class="pill">${escapeHtml(row.transport_label || row.transport)}</span>
            <span class="pill ${status.cls}">${status.text}</span>
          </div>
          <div class="desc">${escapeHtml(endpoint)}${
            row.error ? " · " + escapeHtml(row.error) : ""
          }</div>
        </div>
        <div class="mcp-tools-count">${Number(row.enabled_tools) || 0} / ${
          tools.length
        } 个工具</div>
        <button class="switch ${row.enabled ? "on" : ""}" data-mcp-enable="${escapeHtml(
          row.server_id
        )}" aria-label="启用 ${escapeHtml(row.label || row.server_id)}" aria-pressed="${
          row.enabled ? "true" : "false"
        }"></button>
      </div>
      <div class="mcp-actions">${actions.join("")}</div>
      ${row.error && /401|鉴权|unauthor/i.test(row.error)
        ? '<div class="hint danger-text">密钥可能已过期。错误文案不会回显令牌原文。</div>'
        : ""}
      ${missingCredential
        ? '<div class="hint">config/mcp_keys.json 里还没有这一台的条目；如果它不需要令牌，直接点连接。</div>'
        : ""}
      ${errors}
      ${open ? mcpToolList(row) : ""}
    </div>`;
  }

  function mcpToolList(row) {
    const busy = state.mcpBusy[row.server_id];
    const tools = row.tools || [];
    if (!tools.length) {
      return '<div class="hint">还没有发现工具：点「连接并获取工具」后才会列出这台服务器的工具。</div>';
    }
    const rows = tools
      .map(
        (tool) => `<div class="mcp-tool">
          <div class="text">
            <div class="name mono">${escapeHtml(tool.name)}</div>
            <div class="desc">${escapeHtml(tool.description || "")}</div>
          </div>
          <span class="pill ${tool.tag === "远端写入" ? "danger" : ""}">${escapeHtml(tool.tag)}</span>
          <button class="switch ${tool.enabled ? "on" : ""}" data-mcp-tool="${escapeHtml(
            row.server_id
          )}" data-tool-name="${escapeHtml(tool.name)}" aria-label="${escapeHtml(
            tool.name
          )}" aria-pressed="${tool.enabled ? "true" : "false"}"${busy ? " disabled" : ""}></button>
        </div>`
      )
      .join("");
    const notes = (row.issues || []).map((line) => `<div class="hint">${escapeHtml(line)}</div>`).join("");
    return `<div class="mcp-tools">
      <div class="mcp-tools-head">
        <span class="name">工具逐条开关 · 共 ${tools.length} 个工具</span>
        <span class="sp"></span>
        <button class="ghost" data-mcp-all="${escapeHtml(row.server_id)}" data-enabled="1"${busy ? " disabled" : ""}>全开</button>
        <button class="ghost" data-mcp-all="${escapeHtml(row.server_id)}" data-enabled="0"${busy ? " disabled" : ""}>全关</button>
      </div>
      ${rows}
      ${notes}
      <div class="hint">未开启的工具不进模型可见清单；开启的工具每次调用都先出提案，确认后才真正出网。</div>
    </div>`;
  }

  async function loadMcp() {
    const result = await guard(api("/api/mcp"));
    if (result) state.mcp = result;
    if (state.panel === "mcp") render();
    return result;
  }

  // 每个写操作都让服务器重读一遍：页面不自己猜开关改了什么，也不把凭据留在内存里。
  async function mcpPost(body, busyId) {
    if (busyId) state.mcpBusy[busyId] = body.action;
    render();
    const result = await guard(post("/api/mcp", body));
    if (busyId) delete state.mcpBusy[busyId];
    await loadMcp();
    if (!result) return null;
    const payload = result.result;
    if (payload && payload.ok === false && payload.error) toast(payload.error);
    return payload;
  }

  function mcpLimits() {
    return mcpView().limits || {};
  }

  function mcpRowById(serverId) {
    return (mcpView().rows || []).filter((row) => row.server_id === serverId)[0] || null;
  }

  function mcpSheetRow(entry) {
    return `<div class="mcp-cred">
      <span class="name mono">${escapeHtml(entry.name)}</span>
      <span class="masked">${escapeHtml(entry.masked)}</span>
      <input class="field" type="password" data-cred-value="${escapeHtml(entry.name)}" placeholder="新的值" aria-label="替换 ${escapeHtml(entry.name)}">
      <button class="ghost" data-cred-set="${escapeHtml(entry.name)}">替换</button>
      <button class="ghost danger-text" data-cred-del="${escapeHtml(entry.name)}">删除</button>
    </div>`;
  }

  function mcpSheetMarkup(row) {
    const view = mcpView();
    const limits = mcpLimits();
    const editing = Boolean(row);
    const transports = view.transports.length
      ? view.transports
      : [{ value: "stdio", label: "stdio", available: true, reason: "" }];
    const current = editing ? row.transport : "stdio";
    const cred = (row && row.credentials) || { sections: { env: [], headers: [] } };
    const section = current === "stdio" ? "env" : "headers";
    const sectionLabel = current === "stdio" ? "环境变量" : "请求头";
    const entries = (cred.sections || {})[section] || [];
    const segments = transports
      .map((item) => `<button type="button" data-mcp-transport="${escapeHtml(item.value)}" class="${
        item.value === current ? "on" : ""
      }"${item.available ? "" : " disabled aria-disabled=\"true\""}>${escapeHtml(item.label)}</button>`)
      .join("");
    const unavailable = transports
      .filter((item) => !item.available)
      .map((item) => `<div class="hint">${escapeHtml(item.reason)}</div>`)
      .join("");
    const stdioFields = `<div class="form-row">
        <label for="mc-command">命令</label>
        <input class="field" id="mc-command" placeholder="npx" value="${escapeHtml((row && row.command) || "")}">
      </div>
      <div class="form-row">
        <label for="mc-args">参数</label>
        <input class="field" id="mc-args" placeholder="-y @modelcontextprotocol/server-filesystem" value="${escapeHtml(
          ((row && row.args) || []).join(" ")
        )}">
      </div>
      <div class="form-row">
        <label for="mc-cwd">工作目录</label>
        <input class="field" id="mc-cwd" placeholder="D:\\docs" value="${escapeHtml((row && row.cwd) || "")}">
      </div>`;
    const httpFields = `<div class="form-row">
        <label for="mc-url">地址</label>
        <input class="field" id="mc-url" placeholder="https://mcp.example.com/search" value="${escapeHtml((row && row.url) || "")}">
      </div>`;
    const credentialBlock = editing
      ? `<div class="form-row"><label>${sectionLabel}</label>
          <div class="mcp-cred-list">${
            entries.length
              ? entries.map((entry) => mcpSheetRow(entry)).join("")
              : '<div class="hint">还没有保存的凭据条目。</div>'
          }</div>
        </div>
        <div class="form-row">
          <label for="mc-new-name">新增一项</label>
          <input class="field" id="mc-new-name" placeholder="${
            current === "stdio" ? "API_TOKEN" : "Authorization"
          }">
          <input class="field" id="mc-new-value" type="password" placeholder="值" aria-label="新的凭据值">
          <button class="ghost" data-cred-add>写入</button>
        </div>
        <div class="hint">凭据改动只落盘到 config/mcp_keys.json。</div>`
      : `<div class="form-row" style="align-items:flex-start">
          <label for="mc-secrets" style="padding-top:6px">${sectionLabel}</label>
          <textarea class="field" id="mc-secrets" rows="2" placeholder="${
            current === "stdio" ? "API_TOKEN=xxx" : "Authorization: Bearer xxx"
          }"></textarea>
        </div>`;
    return `<div class="sheet">
      <div class="form-row">
        <label for="mc-name">名称</label>
        <input class="field" id="mc-name" autocomplete="off" placeholder="filesystem" value="${escapeHtml(
          (row && row.server_id) || ""
        )}">
      </div>
      <div class="hint">${
        editing
          ? "改名等于新增服务器，工具开关全部回到关闭 · 传输类型编辑时锁定"
          : "名称会用作标识：小写字母、数字、下划线和连字符，不超过 32 个字符。"
      }</div>
      ${
        editing
          ? `<div class="form-row"><label>传输类型</label><span class="pill accent">${escapeHtml(
              (row && (row.transport_label || row.transport)) || ""
            )}</span></div>`
          : `<div class="segmented">${segments}</div>${unavailable}`
      }
      <div data-stdio-fields${current === "stdio" ? "" : ' hidden'}>${stdioFields}</div>
      <div data-http-fields${current === "stdio" ? " hidden" : ""}>${httpFields}</div>
      ${credentialBlock}
      <div class="form-row">
        <label for="mc-timeout">超时</label>
        <input class="field number" id="mc-timeout" type="number" min="${Number(
          limits.timeout_min
        ) || 1}" max="${Number(limits.timeout_max) || 300}" step="1" value="${Number(
          (row && row.timeout) || limits.timeout_default || 15
        )}">
        <span class="unit">秒</span>
      </div>
      <div class="hint">连接即出网：点「连接并获取工具」才会向远端发 tools/list。</div>
      <div class="form-actions">
        <button class="ghost" data-mcp-validate type="button">重新校验</button>
        ${
          editing
            ? `<button class="ghost" data-mcp-connect type="button">${
                row.error ? "重试" : "连接并获取工具"
              }</button>
               <button class="ghost" data-mcp-disconnect type="button">断开连接</button>`
            : ""
        }
      </div>
      <div class="hint" data-mcp-note>${
        editing
          ? "工具名会加上服务器前缀：mcp__" + escapeHtml((row && row.server_id) || "名称") + "__read_file。"
          : "工具名会加上服务器前缀：mcp__名称__工具名；添加后需手动开启工具才会注入到对话。"
      }</div>
      ${
        editing
          ? `<div class="danger-zone">
              <div class="text">
                <div class="name">移除服务器 ${escapeHtml(row.server_id)}</div>
                <div class="desc">同时删除它的 ${Number(row.enabled_tools) || 0} 个工具开关记录与 mcp_keys.json 里的凭据条目。</div>
              </div>
              <button class="ghost danger-text" data-mcp-remove="${escapeHtml(row.server_id)}">移除</button>
            </div>`
          : ""
      }
    </div>`;
  }

  function bindMcpSheet(body) {
    // 编辑时传输类型锁定（S04c）：分段选择器已经不画在弹层里，初值直接取这一行。
    const marked = body.querySelector("[data-mcp-transport].on");
    let transport = marked
      ? marked.dataset.mcpTransport
      : (mcpView().rows || []).filter((row) => row.server_id === body.dataset.edit)
          .map((row) => row.transport)[0] || "stdio";
    const show = (value) => {
      transport = value;
      body.dataset.transport = value;
      const stdio = body.querySelector("[data-stdio-fields]");
      const http = body.querySelector("[data-http-fields]");
      if (stdio) stdio.hidden = value !== "stdio";
      if (http) http.hidden = value === "stdio";
    };
    const picker = body.querySelector(".segmented");
    if (picker) {
      picker.onclick = (event) => {
        const pick = event.target.closest("[data-mcp-transport]");
        if (!pick || pick.disabled) return;
        picker.querySelectorAll("[data-mcp-transport]").forEach((node) => node.classList.remove("on"));
        pick.classList.add("on");
        show(pick.dataset.mcpTransport);
      };
    }
    show(transport);
    body.onclick = async (event) => {
      const note = body.querySelector("[data-mcp-note]");
      const target = event.target;
      const apply = (payload, busyId) => mcpPost(payload, busyId);
      if (target.closest("[data-cred-add]")) {
        const name = body.querySelector("#mc-new-name").value.trim();
        const value = body.querySelector("#mc-new-value").value;
        if (!name || !value) {
          note.textContent = "名称和值都要填。";
          return;
        }
        const result = await apply(
          { action: "credential", server_id: body.dataset.edit, name: name, value: value },
          body.dataset.edit
        );
        note.textContent = result ? "已写入 " + name + "：值只保存在 config/mcp_keys.json。" : "凭据没有写入。";
        body.querySelector("#mc-new-value").value = "";
        return;
      }
      const credSet = target.closest("[data-cred-set]");
      if (credSet) {
        const input = body.querySelector('[data-cred-value="' + credSet.dataset.credSet + '"]');
        if (!input || !input.value) {
          note.textContent = "先在同一个输入框里填新的值。";
          return;
        }
        const result = await apply(
          { action: "credential", server_id: body.dataset.edit, name: credSet.dataset.credSet, value: input.value },
          body.dataset.edit
        );
        note.textContent = result ? "已替换 " + credSet.dataset.credSet + "。" : "凭据没有替换。";
        input.value = "";
        return;
      }
      const credDel = target.closest("[data-cred-del]");
      if (credDel) {
        const result = await apply(
          { action: "credential", server_id: body.dataset.edit, name: credDel.dataset.credDel, remove: true },
          body.dataset.edit
        );
        note.textContent = result ? "已删除 " + credDel.dataset.credDel + "。" : "凭据没有删除。";
        return;
      }
      if (target.closest("[data-mcp-validate]")) {
        note.textContent = "正在校验…";
        const result = await apply(
          { action: "validate", record: collectMcpForm(body, transport, false) },
          null
        );
        if (!result) {
          note.textContent = "字段没有通过校验，按提示改一下。";
          return;
        }
        note.textContent = result.exists && result.discovered
          ? "校验通过：这台服务器已缓存 " + result.discovered + " 个工具，开启的工具默认保持原样。"
          : "校验通过：字段可用。工具清单要连接后才知道。";
        return;
      }
      if (target.closest("[data-mcp-connect]")) {
        note.textContent = "正在连接并获取工具列表…";
        const saved = await apply({ action: "save", record: collectMcpForm(body, transport, true) }, null);
        if (!saved) return;
        const result = await apply({ action: "connect", server_id: saved.server_id }, saved.server_id);
        if (result) {
          const found = ((result.row || {}).tools || []).length;
          note.textContent = result.ok
            ? "已连接：发现 " + found + " 个工具；未开启的工具不进模型可见清单。"
            : "连接失败，这台服务器保持在列表里，可以改完再重试。";
        }
        return;
      }
      if (target.closest("[data-mcp-disconnect]")) {
        const result = await apply(
          { action: "disconnect", server_id: body.dataset.edit },
          body.dataset.edit
        );
        if (result) note.textContent = "已断开连接：工具清单留在缓存里。";
        return;
      }
      const remove = target.closest("[data-mcp-remove]");
      if (remove) {
        // 弹层不能叠弹层：先收掉这张表单，再走已审核的 S02b 确认形状。
        const cancel = document.querySelector(".dialog-cancel");
        sheetRemoval = remove.dataset.mcpRemove;
        if (cancel) cancel.click();
      }
    };
  }

  function collectMcpForm(body, transport, keepSecrets) {
    const limits = mcpLimits();
    const name = body.querySelector("#mc-name").value.trim().toLowerCase();
    const record = {
      server_id: name,
      label: name,
      transport: transport,
      command: (body.querySelector("#mc-command") || { value: "" }).value.trim(),
      args: ((body.querySelector("#mc-args") || { value: "" }).value || "").split(/\s+/).filter(Boolean),
      cwd: (body.querySelector("#mc-cwd") || { value: "" }).value.trim(),
      url: (body.querySelector("#mc-url") || { value: "" }).value.trim(),
      timeout: body.querySelector("#mc-timeout").value.trim(),
    };
    if (limits.args) record.args = record.args.slice(0, Number(limits.args));
    if (!keepSecrets) return record;
    const box = body.querySelector("#mc-secrets");
    if (box) {
      const pairs = {};
      box.value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean)
        .forEach((line) => {
          const split = line.indexOf("=");
          const colon = line.indexOf(":");
          let key = line;
          let value = "";
          if (split > 0) {
            key = line.slice(0, split);
            value = line.slice(split + 1);
          } else if (colon > 0) {
            key = line.slice(0, colon);
            value = line.slice(colon + 1);
          }
          if (key.trim() && value.trim()) pairs[key.trim()] = value.trim();
        });
      if (Object.keys(pairs).length) {
        record._secrets = transport === "stdio" ? { env: pairs } : { headers: pairs };
      }
    }
    return record;
  }

  async function openMcpSheet(existingId) {
    const view = mcpView();
    const row = existingId
      ? (view.rows || []).filter((item) => item.server_id === existingId)[0]
      : null;
    if (existingId && !row) {
      toast("这台服务器已经不在了。");
      await loadMcp();
      return;
    }
    const body = document.createElement("div");
    body.innerHTML = mcpSheetMarkup(row);
    const sheet = body.firstChild;
    sheet.dataset.edit = row ? row.server_id : "";
    bindMcpSheet(sheet);
    const ok = await CompanionDialog.form(
      row
        ? "保存会改写这台服务器的地址与超时；凭据只写入 config/mcp_keys.json。"
        : "填写命令或地址后添加；添加后工具默认全部关闭，要逐条开启才会进模型可见清单。",
      sheet,
      {
        title: row ? "编辑与移除服务器" : "添加服务器",
        okText: row ? "保存" : "添加",
        cancelText: "放弃修改",
      }
    );
    if (sheetRemoval) {
      const id = sheetRemoval;
      sheetRemoval = "";
      await removeMcpServer(id);
      return;
    }
    if (!ok) return;
    const transport = sheet.dataset.transport || "stdio";
    const record = collectMcpForm(sheet, transport, !row);
    // 密钥不进 save 的请求体：地址与开关入库，值只走 credentials 这一条路（Q2）。
    const secrets = record._secrets;
    delete record._secrets;
    record.enabled = row ? row.enabled : true;
    const saved = await mcpPost({ action: "save", record: record }, null);
    if (!saved) return;
    if (!row && secrets) {
      await mcpPost(
        {
          action: "credentials",
          server_id: saved.server_id,
          env: secrets.env,
          headers: secrets.headers,
        },
        null
      );
    }
    if (row) {
      toast(
        saved.server_id !== row.server_id
          ? "已按新名称添加：工具开关全部回到关闭"
          : "已保存 " + saved.server_id
      );
      return;
    }
    // S04b 的「已发现 N 个工具」只有在真的握手之后才可以说，所以添加会连一次。
    const connected = await mcpPost(
      { action: "connect", server_id: saved.server_id },
      saved.server_id
    );
    if (!connected || !connected.ok) return;
    toast(
      "已连接：发现 " +
        (((connected.row || {}).tools || []).length) +
        " 个工具，添加后默认全部关闭"
    );
  }

  async function removeMcpServer(serverId) {
    const row = (mcpView().rows || []).filter((item) => item.server_id === serverId)[0];
    if (!row) {
      toast("这台服务器已经不在了。");
      await loadMcp();
      return;
    }
    const ok = await CompanionDialog.confirm(
      "同时删除它的 " +
        (Number(row.enabled_tools) || 0) +
        " 个工具开关记录与 mcp_keys.json 里的凭据条目。",
      { title: "移除 MCP 服务器？", okText: "确认移除", danger: true }
    );
    if (!ok) return;
    const result = await mcpPost({ action: "remove", server_id: serverId }, serverId);
    if (!result) return;
    toast(
      result.warning
        ? "已移除，但凭据没有清掉：" + result.warning
        : "已移除 " + serverId
    );
  }

  function panelAdvanced() {
    const view = settingsView();
    const values = view.settings || {};
    const permissions = view.permissions || {};
    const current = String(permissions.current || values.tool_permission || "balanced");
    const tiers = (permissions.tiers || []).map((tier) => ({ value: tier.value, label: tier.label }));
    const diag = view.diagnostics || {};
    const execution = diag.last_execution || {};
    return wrapPanel(
      "advanced",
      `<details class="group" open>
        <summary>权限与确认</summary>
        ${selectRow("tool_permission", "权限档位", tiers, permissionHint(current))}
        ${numberRow("max_tool_rounds", "单轮工具调用上限", 1, 8, 1, "次")}
        <div class="hint">档位只改「要不要确认」与「危险工具给不给」：拒绝的永远不会因为档位变成允许，shell 与界面控制在「信任」档也照样要确认。</div>
        ${saveRow()}
      </details>
      <details class="group">
        <summary>诊断</summary>
        <div class="diag-grid">
          <div class="k">数据目录</div><div class="v diag-path">${escapeHtml(diag.data_dir || "未知")}</div>
          <div class="k">数据库</div><div class="v diag-path">${escapeHtml(diag.database || "未知")}</div>
          <div class="k">表结构版本</div><div class="v">v${Number(diag.schema_version) || 0} / 期望 v${Number(diag.expected_schema_version) || 0}</div>
          <div class="k">实例锁</div><div class="v">${
            diag.locked_by ? "由进程 " + escapeHtml(String(diag.locked_by)) + " 持有" : "空闲"
          }</div>
          <div class="k">备份</div><div class="v">${Number(diag.backups) || 0} 份${
            diag.latest_backup ? " · 最近 " + escapeHtml(diag.latest_backup) : ""
          }</div>
          <div class="k">最近文件执行</div><div class="v">${
            execution.status
              ? escapeHtml(
                  execution.status +
                    (execution.tool_name ? " · " + execution.tool_name : "") +
                    (execution.error ? " · " + execution.error : "")
                )
              : "本次启动后还没有执行记录"
          }</div>
        </div>
        <div class="hint">路径可以直接选中复制。程序目前没有写日志文件，所以这里给的是已经存在的事实：库、锁与备份。完整自检请在终端运行 <code>python doctor.py</code>。</div>
        <div class="form-actions"><button class="ghost" data-open="console">打开管理台</button></div>
      </details>`
    );
  }

  function sheetMarkup() {
    const view = settingsView();
    const values = view.settings || {};
    const service = view.model_service || {};
    const key = view.api_key || {};
    return `<div class="sheet">
      <div class="form-row">
        <label for="ms-url">服务地址</label>
        <input class="field" type="text" id="ms-url" autocomplete="off" placeholder="https://example.com/v1" value="${escapeHtml(service.base_url || values.base_url || "")}">
      </div>
      <div class="form-row">
        <label for="ms-key">API 密钥</label>
        <input class="field" type="password" id="ms-key" autocomplete="off" placeholder="${escapeHtml(key.masked || "未设置")}">
      </div>
      <div class="hint">${key.present ? "留空表示不修改已保存的密钥；密钥只写入 config/keys.json，界面只回显脱敏值。" : "密钥只写入 config/keys.json，不写入数据库，也不进备份与导出。"}</div>
      <div class="form-actions">
        <button class="ghost" id="ms-fetch" type="button">拉取模型</button>
      </div>
      <div class="hint" id="ms-note">上游的模型列表一般不带窗口大小，按内置对照表自动带出；表里没有的必须手填。</div>
      <div class="model-list" id="ms-list"></div>
    </div>`;
  }

  function renderModelList(host) {
    const list = host.querySelector("#ms-list");
    const view = settingsView();
    const active = String((view.settings || {}).model || "");
    if (!state.models.length) {
      list.innerHTML = '<div class="empty-note">还没有取到模型列表。</div>';
      return;
    }
    const SOURCE_LABEL = { stored: "已保存", catalog: "内置", manual: "手填" };
    list.innerHTML = state.models
      .map(
        (item) => `<div class="model-row${item.id === active ? " on" : ""}">
          <button type="button" class="model-pick" data-pick="${escapeHtml(item.id)}">${escapeHtml(item.id)}</button>
          <input class="field number model-window" type="number" min="4000" max="2000000" step="512" data-window="${escapeHtml(item.id)}" data-prefill="${Number(item.context_tokens || 0) || 0}" value="${Number(item.context_tokens || 0) || ""}" aria-label="${escapeHtml(item.id)} 上下文窗口">
          <span class="pill${item.source === "stored" ? " accent" : ""}">${SOURCE_LABEL[item.source] || item.source}</span>
        </div>`
      )
      .join("");
  }

  function bindModelSheet(host) {
    host.querySelector("#ms-fetch").onclick = async () => {
      const note = host.querySelector("#ms-note");
      note.textContent = "正在向上游请求模型列表…";
      const result = await guard(
        post("/api/models/fetch", {
          base_url: host.querySelector("#ms-url").value,
          api_key: host.querySelector("#ms-key").value,
        })
      );
      if (!result) {
        note.textContent = "没取到模型列表，检查地址与密钥后重试。";
        return;
      }
      state.models = result.models || [];
      note.textContent = "取到 " + state.models.length + " 个模型。";
      renderModelList(host);
    };
    host.onclick = (event) => {
      const pick = event.target.closest("[data-pick]");
      if (!pick) return;
      host.querySelectorAll(".model-row").forEach((row) => row.classList.remove("on"));
      pick.closest(".model-row").classList.add("on");
    };
    renderModelList(host);
  }

  function collectModelSheet(host) {
    const windows = {};
    host.querySelectorAll("[data-window]").forEach((input) => {
      const value = Number(input.value || 0);
      // Untouched rows keep whatever the server already knows; saving a dialog
      // must not freeze every built-in guess into the user's own table.
      if (value > 0 && value !== Number(input.dataset.prefill || 0)) {
        windows[input.dataset.window] = Math.round(value);
      }
    });
    const selected = host.querySelector(".model-row.on [data-pick]");
    const model = selected ? selected.dataset.pick : "";
    const body = {
      base_url: host.querySelector("#ms-url").value.trim(),
      model: model,
      model_contexts: windows,
    };
    const secret = host.querySelector("#ms-key").value.trim();
    if (secret) body.api_key = secret;
    const row = host.querySelector(".model-row.on [data-window]");
    if (row && Number(row.value || 0) > 0) body.context_tokens = Math.round(Number(row.value));
    return body;
  }

  async function openModelSheet() {
    const host = document.createElement("div");
    host.innerHTML = sheetMarkup();
    const body = host.firstChild;
    bindModelSheet(body);
    const ok = await CompanionDialog.form(
      "填写服务地址与密钥后拉取模型；保存会同时写入 config/keys.json 与应用设置。",
      body,
      { title: "模型服务接入", okText: "保存并启用" }
    );
    if (!ok) return;
    const result = await guard(post("/api/model_service", collectModelSheet(body)));
    if (!result) return;
    state.settings = result;
    const errors = result.errors || [];
    toast(errors.length ? "已保存，但 " + errors.length + " 项被拒绝" : "模型服务已保存并生效");
    render();
  }

  // Every panel posts through here: inputs and selects carry data-setting,
  // toggles carry data-switch, and the server's field table decides what is a
  // legal key at all. A panel that forgets a control simply does not send it.
  function collectForm() {
    const patch = {};
    el.content.querySelectorAll("[data-setting]").forEach((node) => {
      patch[node.dataset.setting] = node.value;
    });
    el.content.querySelectorAll("[data-switch]").forEach((node) => {
      patch[node.dataset.switch] = node.classList.contains("on");
    });
    return patch;
  }

  async function saveForm() {
    const result = await guard(post("/api/settings", collectForm()));
    if (!result) return;
    state.settings = result;
    const errors = result.errors || [];
    toast(errors.length ? errors.length + " 项没有保存" : "已保存并生效");
    render();
  }

  async function purgeMemories() {
    const count = Number((state.data.settings_summary || {}).memory_count || 0);
    const ok = await CompanionDialog.confirm(
      "将硬删除这 " +
        count +
        " 条记忆连同它们的证据与召回记录，会话与回执保留。受保护的记忆不会被删，删完会告诉你还剩几条。此操作不可撤销。",
      { title: "清空记忆", okText: "全部清空", danger: true }
    );
    if (!ok) return;
    const result = await guard(post("/api/console/memory", { action: "purge_all" }));
    if (!result) return;
    toast(
      "已清空 " +
        Number(result.deleted || 0) +
        " 条，保留 " +
        Number(result.protected || 0) +
        " 条受保护"
    );
    await refresh();
    await loadSettings();
  }

  // The picture arrives as a data URL; only the base64 body is sent, and the
  // server re-checks suffix, size and magic bytes before it touches the slot.
  async function importAvatarFile() {
    const input = $("#avatarFile");
    const file = input && input.files && input.files[0];
    if (!file) {
      toast("先选一张 .png / .jpg / .webp 图片。");
      return;
    }
    if (file.size > 8 * 1024 * 1024) {
      toast("这张图片超过 8 MiB，请先压缩。");
      return;
    }
    const dataUrl = await guard(
      new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("读取文件失败。"));
        reader.readAsDataURL(file);
      })
    );
    if (!dataUrl) return;
    const result = await guard(
      post("/api/asset/avatar", { filename: file.name, data: dataUrl })
    );
    if (!result) return;
    const removed = (result.removed || []).filter(Boolean);
    toast(
      removed.length
        ? "头像已导入，同时清掉了旧槽位的 " + removed.join("、")
        : "头像已导入"
    );
    input.value = "";
    await refresh();
  }

  function lines(value) {
    return String(value || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function captureChainForm(chainId) {
    const view = desktopView();
    const current = (view.chains || []).filter(
      (row) => row.chain_id === chainId
    )[0];
    const base = state.desktopChainDraft || current || {};
    const triggerKind = ($("#deskChainTrigger") || {}).value || "auto";
    let triggerConfig = {};
    if (triggerKind === "argument") {
      triggerConfig = {
        arguments: lines(($("#deskChainArguments") || {}).value),
      };
    } else if (triggerKind === "uri") {
      triggerConfig = { uri: ($("#deskChainUri") || {}).value || "" };
    } else if (triggerKind === "api") {
      triggerConfig = {
        api_app_id: ($("#deskChainApiApp") || {}).value || "",
        arguments: lines(($("#deskChainApiArguments") || {}).value),
      };
    } else if (triggerKind === "uia") {
      triggerConfig = {
        window_title_contains:
          ($("#deskChainUiaWindowTitle") || {}).value || "",
        window_class: ($("#deskChainUiaWindowClass") || {}).value || "",
        automation_id:
          ($("#deskChainUiaAutomationId") || {}).value || "",
        control_name: ($("#deskChainUiaControlName") || {}).value || "",
        control_type:
          ($("#deskChainUiaControlType") || {}).value || "",
      };
    }
    return {
      ...base,
      chain_id: chainId || "",
      name: ($("#deskChainName") || {}).value || "",
      starter_app_id: ($("#deskChainStarter") || {}).value || "",
      target_app_id: ($("#deskChainTarget") || {}).value || "",
      trigger_kind: triggerKind,
      trigger_config: triggerConfig,
      detection_rules: {
        executable_path: (base.detection_rules || {}).executable_path || "",
        process_name: (base.detection_rules || {}).process_name || "",
        reuse_existing: Boolean(
          $("#deskChainReuse") && $("#deskChainReuse").checked
        ),
        require_descendant: Boolean(
          $("#deskChainDescendant") && $("#deskChainDescendant").checked
        ),
        require_window: Boolean(
          $("#deskChainWindow") && $("#deskChainWindow").checked
        ),
        window_title_contains: lines(
          ($("#deskChainWindowTitle") || {}).value
        ),
        window_class: lines(($("#deskChainWindowClass") || {}).value),
      },
      readiness_rules: {
        timeout_seconds: Number(
          ($("#deskChainTimeout") || {}).value || 30
        ),
        poll_interval_seconds: 0.25,
      },
    };
  }

  function applyTriggerVisibility() {
    const select = $("#deskChainTrigger");
    if (!select) return;
    el.content.querySelectorAll("[data-trigger-field]").forEach((node) => {
      node.hidden = node.dataset.triggerField !== select.value;
    });
  }

  async function handleDesktopAction(event) {
    const tab = event.target.closest("[data-desk-tab]");
    if (tab) {
      state.desktopTab = tab.dataset.deskTab;
      render();
      return true;
    }
    const button = event.target.closest("[data-desk-act]");
    if (!button) return false;
    const action = button.dataset.deskAct;
    if (action === "grant-directory") {
      const path = ($("#deskGrantPath") || {}).value || "";
      const capabilities = Array.from(
        el.content.querySelectorAll(".cap-grid input[type=checkbox]:checked")
      ).map((node) => node.value);
      if (!path.trim()) {
        toast("先输入要授权的目录绝对路径。");
        return true;
      }
      await desktopCommand(
        {
          action: "grant_directory",
          path: path.trim(),
          capabilities: capabilities,
          allow_sensitive: Boolean(
            $("#deskSensitive") && $("#deskSensitive").checked
          ),
        },
        "目录已授权，索引会在后台开始"
      );
      return true;
    }
    if (action === "revoke-directory") {
      const ok = await CompanionDialog.confirm(
        "撤销后，模型和文件工具会立即失去这个目录的权限；目录里的文件不会被删除。",
        { title: "撤销目录授权？", okText: "确认撤销", danger: true }
      );
      if (ok) {
        await desktopCommand(
          { action: "revoke_directory", grant_id: button.dataset.id },
          "目录授权已撤销"
        );
      }
      return true;
    }
    if (action === "reindex-directory") {
      await desktopCommand(
        { action: "reindex_directory", grant_id: button.dataset.id },
        "已安排重建索引"
      );
      return true;
    }
    if (action === "search-files") {
      await desktopCommand({
        action: "search_files",
        query: ($("#deskFileQuery") || {}).value || "",
        limit: 12,
      });
      return true;
    }
    if (action === "open-file") {
      await desktopCommand(
        { action: "open_file", file_ref: button.dataset.ref },
        "已交给系统默认程序打开"
      );
      return true;
    }
    if (action === "delete-file") {
      const ok = await CompanionDialog.confirm(
        "这将生成一张“移入系统回收站”的确认卡；设置页不会直接删除文件。",
        { title: "生成删除提案？", okText: "生成提案", danger: true }
      );
      if (ok) {
        await desktopCommand(
          { action: "delete_file", file_ref: button.dataset.ref },
          "删除提案已生成，回到对话确认"
        );
      }
      return true;
    }
    if (action === "move-file") {
      await moveDesktopFile(button.dataset.ref);
      return true;
    }
    if (action === "scan-apps") {
      toast("正在扫描开始菜单与 App Paths…");
      await desktopCommand({ action: "scan_apps" }, "应用扫描完成");
      return true;
    }
    if (action === "allow-app") {
      await desktopCommand(
        { action: "allow_app", app_id: button.dataset.id },
        "已登记并允许启动"
      );
      return true;
    }
    if (action === "revoke-app") {
      const ok = await CompanionDialog.confirm(
        "撤销只删除启动授权，不会关闭已经启动的进程，也不会删除应用目录里的记录。",
        { title: "撤销启动授权？", okText: "确认撤销", danger: true }
      );
      if (ok) {
        await desktopCommand(
          { action: "revoke_app", grant_id: button.dataset.id },
          "启动授权已撤销"
        );
      }
      return true;
    }
    if (action === "register-app") {
      await desktopCommand(
        {
          action: "register_app",
          name: ($("#deskAppName") || {}).value || "",
          target: ($("#deskAppTarget") || {}).value || "",
          arguments: ($("#deskAppArgs") || {}).value || "",
          working_dir: ($("#deskAppCwd") || {}).value || "",
        },
        "应用已登记并允许启动"
      );
      return true;
    }
    if (action === "save-chain") {
      const draft = captureChainForm(button.dataset.id || "");
      if (!draft.name || !draft.starter_app_id || !draft.target_app_id) {
        toast("名称、起点和目标应用都要填写。");
        return true;
      }
      if (draft.starter_app_id === draft.target_app_id) {
        toast("起点和目标必须是两个不同应用。");
        return true;
      }
      const result = await desktopCommand(
        { action: "save_chain", ...draft },
        draft.chain_id ? "启动链修改已保存" : "启动链已保存"
      );
      if (result) {
        state.desktopEditingChain = "";
        state.desktopChainDraft = null;
        state.desktopDetection = null;
        render();
      }
      return true;
    }
    if (action === "edit-chain") {
      const row = (desktopView().chains || []).filter(
        (item) => item.chain_id === button.dataset.id
      )[0];
      if (!row) return true;
      state.desktopEditingChain = row.chain_id;
      state.desktopChainDraft = { ...row };
      state.desktopDetection = null;
      render();
      return true;
    }
    if (action === "cancel-edit-chain") {
      state.desktopEditingChain = "";
      state.desktopChainDraft = null;
      state.desktopDetection = null;
      render();
      return true;
    }
    if (action === "detect-chain") {
      const chainId = button.dataset.id;
      const row = (desktopView().chains || []).filter(
        (item) => item.chain_id === chainId
      )[0];
      state.desktopEditingChain = chainId;
      state.desktopChainDraft = row ? { ...row } : null;
      await desktopCommand({
        action: "detect_chain_targets",
        chain_id: chainId,
      });
      return true;
    }
    if (action === "use-detected-target") {
      const detection = state.desktopDetection || {};
      const candidate = (detection.candidates || []).filter(
        (row) => String(row.pid) === String(button.dataset.pid)
      )[0];
      if (!candidate) return true;
      const window = (candidate.windows || [])[0] || {};
      const base = state.desktopChainDraft || captureChainForm(
        state.desktopEditingChain
      );
      state.desktopChainDraft = {
        ...base,
        detection_rules: {
          ...(base.detection_rules || {}),
          executable_path: candidate.exe_path || "",
          process_name: candidate.name || "",
          require_window: Boolean(window.title),
          window_title_contains: window.title ? [window.title] : [],
          window_class: window.class_name ? [window.class_name] : [],
        },
      };
      state.desktopDetection = null;
      render();
      toast("已把该实例写入检测规则，保存后生效。");
      return true;
    }
    if (action === "start-recording") {
      const starter = ($("#deskRecordStarter") || {}).value || "";
      if (!starter) {
        toast("先选择已允许启动的中转应用。");
        return true;
      }
      await desktopCommand(
        { action: "start_recording", starter_app_id: starter },
        "录制已开始，现在手动启动一次目标应用"
      );
      return true;
    }
    if (action === "refresh-recording") {
      await desktopCommand({
        action: "refresh_recording",
        recording_id: ((desktopView().recording || {}).recording_id || ""),
      });
      return true;
    }
    if (action === "cancel-recording") {
      await desktopCommand(
        {
          action: "cancel_recording",
          recording_id: ((desktopView().recording || {}).recording_id || ""),
        },
        "录制已取消"
      );
      return true;
    }
    if (action === "select-record-candidate") {
      const recording = desktopView().recording || {};
      const candidate = (recording.candidates || [])[
        Number(button.dataset.index || 0)
      ];
      if (!candidate || !candidate.app_id) {
        toast("这个进程还不在应用目录里，先在“应用目录”登记并允许启动。");
        return true;
      }
      const starter = (desktopView().apps || []).filter(
        (row) => row.app_id === recording.starter_app_id
      )[0] || {};
      const window = (candidate.windows || [])[0] || {};
      state.desktopEditingChain = "";
      state.desktopChainDraft = {
        chain_id: "",
        name: `${starter.name || "中转应用"} → ${candidate.app_name || candidate.name}`,
        starter_app_id: recording.starter_app_id || "",
        target_app_id: candidate.app_id,
        trigger_kind: "auto",
        trigger_config: {},
        detection_rules: {
          executable_path: candidate.exe_path || "",
          process_name: candidate.process_name || "",
          reuse_existing: false,
          require_descendant: false,
          require_window: Boolean(window.title),
          window_title_contains: window.title ? [window.title] : [],
          window_class: window.class_name ? [window.class_name] : [],
        },
        readiness_rules: {
          timeout_seconds: 30,
          poll_interval_seconds: 0.25,
        },
      };
      state.desktopDetection = null;
      state.desktopTab = "chains";
      render();
      toast("目标实例已带入启动链表单，请检查并保存。");
      return true;
    }
    if (action === "toggle-chain") {
      await desktopCommand(
        {
          action: "toggle_chain",
          chain_id: button.dataset.id,
          enabled: button.dataset.enabled !== "1",
        },
        button.dataset.enabled === "1" ? "启动链已停用" : "启动链已启用"
      );
      return true;
    }
    if (action === "delete-chain") {
      const ok = await CompanionDialog.confirm(
        "删除启动链配置不会退出任何已经启动的进程。",
        { title: "删除启动链？", okText: "确认删除", danger: true }
      );
      if (ok) {
        await desktopCommand(
          { action: "delete_chain", chain_id: button.dataset.id },
          "启动链已删除"
        );
      }
      return true;
    }
    if (action === "run-chain") {
      await desktopCommand(
        { action: "run_chain", chain_id: button.dataset.id },
        "启动链提案已生成，回到对话确认"
      );
      return true;
    }
    return false;
  }

  const PANELS = {
  memory: panelMemory,
    affect: panelAffect,
    persona: panelPersona,
    proactive: panelProactive,
    tasks: panelTasks,
    desktop: panelDesktop,
    model: panelModel,
    rag: panelRag,
    mcp: panelMcp,
    advanced: panelAdvanced,
    manage: panelManage,
    theme: panelTheme,
    font: panelFont,
    motion: panelMotion,
    background: panelBackground,
    overlay: panelOverlay,
  };

  function render() {
    if (!state.data) return;
    applyUi(state.data.ui);
    renderNav();
    if (state.panel && !state.filter) {
      el.content.innerHTML = PANELS[state.panel]();
    } else {
      renderList();
    }
    bindPanel();
    applyTriggerVisibility();
  }

  function bindPanel() {
    const panel = el.content;
    panel.onclick = async (event) => {
      if (await handleDesktopAction(event)) return;
      const back = event.target.closest("[data-back]");
      if (back) {
        state.panel = null;
        render();
        return;
      }
      const open = event.target.closest("[data-open]");
      if (open) {
        if (state.host) {
          if (open.dataset.open === "console-tasks") state.host.openConsole();
          else state.host.openConsole();
        } else {
          window.open("/console?t=" + encodeURIComponent(token), "_blank");
        }
        return;
      }
      const theme = event.target.closest(".theme-card[data-theme]");
      if (theme) {
        await setUi({ theme: theme.dataset.theme });
        return;
      }
      const font = event.target.closest("[data-font]");
      if (font) {
        await setUi({ font_scale: Number(font.dataset.font) });
        return;
      }
      const savePersona = event.target.closest("[data-save-persona]");
      if (savePersona) {
        const key = savePersona.dataset.savePersona;
        const input = $("#persona-" + key);
        await guard(post("/api/persona", { field: key, content: input.value }));
        toast("已保存");
        await refresh();
        return;
      }
      const enabled = event.target.closest("#pro-enabled");
      if (enabled) {
        enabled.classList.toggle("on");
        return;
      }
      const memory = event.target.closest("#pro-memory");
      if (memory) {
        memory.classList.toggle("on");
        return;
      }
      const useBg = event.target.closest("[data-bg-use]");
      if (useBg) {
        await setUi({ background_enabled: true });
        toast("已启用背景图");
        return;
      }
      const clearBg = event.target.closest("[data-bg-clear]");
      if (clearBg) {
        await setUi({ background_enabled: false });
        toast("已清除背景显示");
        return;
      }
      const motion = event.target.closest("#motionSwitch");
      if (motion) {
        await setUi({ reduce_motion: !state.data.ui.reduce_motion });
        return;
      }
      const overlay = event.target.closest("#overlaySwitch");
      if (overlay) {
        const pet = (state.data.assets || {}).pet || {};
        if (!pet.available) {
          toast("桌面浮窗不可用：" + (pet.reason || "缺少形象素材"));
          return;
        }
        const value = !state.data.ui.overlay_visible;
        await setUi({ overlay_visible: value });
        if (state.host) state.host.setOverlayVisible(value);
        else if (value) window.open("/overlay?t=" + encodeURIComponent(token), "_blank");
        return;
      }
      const mcpAdd = event.target.closest("[data-mcp-add]");
      if (mcpAdd) {
        await openMcpSheet("");
        return;
      }
      const mcpEdit = event.target.closest("[data-mcp-edit]");
      if (mcpEdit) {
        await openMcpSheet(mcpEdit.dataset.mcpEdit);
        return;
      }
      const mcpRemove = event.target.closest("[data-mcp-remove]");
      if (mcpRemove) {
        await removeMcpServer(mcpRemove.dataset.mcpRemove);
        return;
      }
      const mcpToggle = event.target.closest("[data-mcp-toggle]");
      if (mcpToggle) {
        const id = mcpToggle.dataset.mcpToggle;
        state.mcpOpen[id] = !state.mcpOpen[id];
        render();
        return;
      }
      const mcpReload = event.target.closest("[data-mcp-reload]");
      if (mcpReload) {
        const result = await mcpPost({ action: "reload_all" }, null);
        if (result) {
          const bad = (result.refreshed || []).filter((item) => !item.ok).length;
          toast(bad ? bad + " 台服务器没有重新取到工具清单" : "已重新加载全部服务器");
        }
        return;
      }
      const mcpEnable = event.target.closest("[data-mcp-enable]");
      if (mcpEnable) {
        const id = mcpEnable.dataset.mcpEnable;
        const row = mcpRowById(id);
        if (!row) return;
        const result = await mcpPost(
          { action: "enable", server_id: id, enabled: !row.enabled },
          row.enabled ? id : null
        );
        if (result && !row.enabled) toast("已开启 " + id + "：工具仍要逐条开启");
        return;
      }
      const mcpTool = event.target.closest("[data-mcp-tool]");
      if (mcpTool) {
        const id = mcpTool.dataset.mcpTool;
        const name = mcpTool.dataset.toolName;
        await mcpPost(
          { action: "tools", server_id: id, names: [name], enable: !mcpTool.classList.contains("on") },
          null
        );
        return;
      }
      const mcpAll = event.target.closest("[data-mcp-all]");
      if (mcpAll) {
        await mcpPost(
          {
            action: "all_tools",
            server_id: mcpAll.dataset.mcpAll,
            enabled: mcpAll.dataset.enabled === "1",
          },
          mcpAll.dataset.mcpAll
        );
        return;
      }
      const mcpCall = event.target.closest("[data-mcp-action]");
      if (mcpCall) {
        await mcpPost(
          { action: mcpCall.dataset.mcpAction, server_id: mcpCall.dataset.id },
          mcpCall.dataset.mcpAction === "connect" ? mcpCall.dataset.id : null
        );
        return;
      }
      if (event.target.closest("#modelOpen")) {
        await openModelSheet();
        return;
      }
      const toggle = event.target.closest("[data-switch]");
      if (toggle) {
        toggle.classList.toggle("on");
        toggle.setAttribute("aria-pressed", toggle.classList.contains("on") ? "true" : "false");
        return;
      }
      const save = event.target.closest("[data-save-settings]");
      if (save) {
        await saveForm();
        return;
      }
      const purge = event.target.closest("[data-purge-memories]");
      if (purge) {
        await purgeMemories();
        return;
      }
      const importAvatar = event.target.closest("[data-avatar-import]");
      if (importAvatar) {
        await importAvatarFile();
        return;
      }
      if (event.target.closest("#saveMood")) {
        const mood = $("#moodSelect").value;
        await guard(post("/api/console/affect", { mood: mood }));
        const label = (MOODS.find((item) => item.value === mood) || {}).label || mood;
        toast("已纠正为 " + label);
        await refresh();
        return;
      }
      if (event.target.closest("#saveProactive")) {
        const valueOf = (key) => Number($("#pro-" + key).value || 0);
        const enabledOn = $("#pro-enabled").classList.contains("on");
        const memoryOn = $("#pro-memory").classList.contains("on");
        await guard(
          post("/api/proactive", {
            action: "settings",
            enabled: enabledOn,
            quiet_start: valueOf("quiet_start"),
            quiet_end: valueOf("quiet_end"),
            daily_limit: valueOf("daily_limit"),
            min_interval_hours: valueOf("min_interval_hours"),
            max_unanswered: valueOf("max_unanswered"),
            include_memory: memoryOn,
          })
        );
        toast("主动设置已保存");
        await refresh();
      }
    };
    panel.oninput = (event) => {
      const node = event.target;
      if (node.dataset.sync) {
        const number = $("#st-" + node.dataset.sync);
        if (number) number.value = node.value;
        return;
      }
      if (node.dataset.setting) {
        const range = $("#rng-" + node.dataset.setting);
        if (range) range.value = node.value;
      }
    };
    panel.onchange = (event) => {
      const node = event.target;
      if (node.id === "deskChainTrigger") {
        applyTriggerVisibility();
        return;
      }
      if (node.id === "st-model") {
        applyModelWindow(node.value);
        return;
      }
      // The two selects whose copy explains what the server will actually do
      // with the value: the hint has to follow the choice, not the page load.
      if (node.id === "st-output_format" || node.id === "st-tool_permission") {
        const hint = $("#hint-" + node.dataset.setting);
        if (hint) {
          hint.textContent =
            node.dataset.setting === "output_format"
              ? outputFormatHint(node.value)
              : permissionHint(node.value);
        }
      }
    };
    el.nav.onclick = (event) => {
      const button = event.target.closest("[data-cat]");
      if (!button) return;
      state.cat = button.dataset.cat;
      state.panel = null;
      state.filter = "";
      el.search.value = "";
      render();
    };
  }

  async function setUi(values) {
    const result = await guard(post("/api/ui", values));
    if (!result) return;
    state.data.ui = result;
    applyUi(result);
    render();
  }

  async function refresh() {
    const data = await guard(api("/api/bootstrap"));
    if (!data) return;
    state.data = data;
    render();
  }

  function goMain() {
    if (state.host) state.host.hide();
    else window.location.href = "/?t=" + encodeURIComponent(token);
  }

  function bindChrome() {
    el.close.onclick = goMain;
    el.themeBtn.onclick = () => {
      state.cat = "data";
      state.panel = "theme";
      state.filter = "";
      el.search.value = "";
      render();
    };
    el.overlayBtn.onclick = () => {
      state.cat = "data";
      state.panel = "overlay";
      state.filter = "";
      el.search.value = "";
      render();
    };
    el.search.oninput = () => {
      state.filter = el.search.value;
      state.panel = null;
      renderList();
    };
    el.search.onkeydown = (event) => {
      if (event.key === "Escape") {
        if (el.search.value) {
          el.search.value = "";
          state.filter = "";
          render();
        } else {
          el.close.click();
        }
      }
    };
    document.addEventListener("click", async (event) => {
      const entry = event.target.closest("[data-panel]");
      if (entry) {
        state.panel = entry.dataset.panel;
        state.filter = "";
        el.search.value = "";
        // The asset folder is user-managed, so re-read it when the panel opens.
        if (state.panel === "background" || state.panel === "persona") await refresh();
        else if (state.panel === "mcp") await loadMcp();
        else if (state.panel === "desktop") await loadDesktop();
        else if (["model", "rag", "memory", "advanced"].indexOf(state.panel) >= 0)
          await loadSettings();
        else render();
      }
    });
    bindDrag();
  }

  function bindDrag() {
    // Same contract as the main window: the OS caption loop owns the move.
    // A page-level mousemove stops when the cursor leaves the WebView and the
    // old screenX-clientX grab translated the window by its own origin.
    el.head.addEventListener("mousedown", (event) => {
      if (!state.host || event.button !== 0) return;
      if (event.target.closest("button, input")) return;
      event.preventDefault();
      if (typeof state.host.beginWindowDrag === "function") {
        state.host.beginWindowDrag();
        return;
      }
      const grab = { x: event.clientX, y: event.clientY };
      const move = (moveEvent) => {
        state.host.moveWindow(moveEvent.screenX - grab.x, moveEvent.screenY - grab.y);
      };
      const stop = () => {
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", stop);
        window.removeEventListener("blur", stop);
      };
      document.body.style.userSelect = "none";
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", stop);
      window.addEventListener("blur", stop);
    });
  }

  function connectHost() {
    if (!window.qt || !window.qt.webChannelTransport || typeof QWebChannel === "undefined") {
      document.body.classList.add("browser");
      return;
    }
    new QWebChannel(window.qt.webChannelTransport, (channel) => {
      state.host = channel.objects.host || null;
      document.body.classList.toggle("browser", !state.host);
    });
  }

  async function start() {
    await refresh();
    if (!state.data) {
      document.body.innerHTML = '<div class="empty-note">无法连接本地服务。</div>';
      return;
    }
    bindChrome();
    connectHost();
  }

  start();
})();
