/* Settings page for the Pixso-designed companion UI. */
(function () {
  "use strict";

  const $ = (selector, root) => (root || document).querySelector(selector);
  const token = new URLSearchParams(location.search).get("t") || "";

  const state = {
    data: null,
    cat: "company",
    panel: null,
    filter: "",
    host: null,
  };

  const MOODS = [
    { value: "neutral", label: "平静" },
    { value: "positive", label: "开心" },
    { value: "low", label: "低落" },
    { value: "tired", label: "疲惫" },
  ];

  const CATEGORIES = [
    { id: "company", name: "陪伴", desc: "记忆、情绪与人设" },
    { id: "work", name: "工作", desc: "主动关心与任务卡" },
    { id: "data", name: "数据与外观", desc: "管理、主题与浮窗" },
  ];

  const ENTRIES = {
    memory: { cat: "company", name: "记忆", desc: "账本、来源与遗忘" },
    affect: { cat: "company", name: "情绪", desc: "状态估计与纠正" },
    persona: { cat: "company", name: "人设", desc: "称呼、边界与人设包" },
    proactive: { cat: "work", name: "主动", desc: "免打扰、频率与未回复阈值" },
    tasks: { cat: "work", name: "任务", desc: "任务卡与执行记录" },
    manage: { cat: "data", name: "管理台", desc: "记忆、召回与审计" },
    theme: { cat: "data", name: "主题", desc: "经典深色与珍珠白" },
    font: { cat: "data", name: "字号", desc: "100% – 200% 缩放" },
    motion: { cat: "data", name: "减少动效", desc: "关闭过渡与呼吸动画" },
    overlay: { cat: "data", name: "桌面浮窗", desc: "形象与主动气泡" },
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

  function panelMemory() {
    const summary = state.data.settings_summary || {};
    return wrapPanel(
      "memory",
      `<div class="stat-grid">
        <div class="stat"><div class="k">已记录</div><div class="v">${summary.memory_count || 0} 条</div></div>
        <div class="stat"><div class="k">来源策略</div><div class="v">保留原消息</div></div>
      </div>
      <div class="hint">记忆分为偏好、事实与事件。删除与纠正集中在管理台，所有变更都会写审计。</div>
      <div class="form-actions"><button class="primary" data-open="console">打开管理台</button></div>`
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
      `<div class="stat-grid">
        <div class="stat"><div class="k">当前人设</div><div class="v">${escapeHtml(info.name || "数字分身")}</div></div>
        <div class="stat"><div class="k">版本</div><div class="v">${escapeHtml(info.version || "未加载")}</div></div>
      </div>
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
    return wrapPanel(
      "overlay",
      `<div class="switch-row">
        <div class="text"><div class="name">显示桌面浮窗</div><div class="desc">在桌面右下角显示形象与主动气泡</div></div>
        <button class="switch ${on ? "on" : ""}" id="overlaySwitch"></button>
      </div>
      <div class="hint">浮窗可以拖动；双击形象会回到主窗口。</div>`
    );
  }

  const PANELS = {
    memory: panelMemory,
    affect: panelAffect,
    persona: panelPersona,
    proactive: panelProactive,
    tasks: panelTasks,
    manage: panelManage,
    theme: panelTheme,
    font: panelFont,
    motion: panelMotion,
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
  }

  function bindPanel() {
    const panel = el.content;
    panel.onclick = async (event) => {
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
      const motion = event.target.closest("#motionSwitch");
      if (motion) {
        await setUi({ reduce_motion: !state.data.ui.reduce_motion });
        return;
      }
      const overlay = event.target.closest("#overlaySwitch");
      if (overlay) {
        const value = !state.data.ui.overlay_visible;
        await setUi({ overlay_visible: value });
        if (state.host) state.host.setOverlayVisible(value);
        else if (value) window.open("/overlay?t=" + encodeURIComponent(token), "_blank");
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
    document.addEventListener("click", (event) => {
      const entry = event.target.closest("[data-panel]");
      if (entry) {
        state.panel = entry.dataset.panel;
        state.filter = "";
        el.search.value = "";
        render();
      }
    });
    bindDrag();
  }

  function bindDrag() {
    let grab = null;
    el.head.addEventListener("mousedown", (event) => {
      if (!state.host || event.button !== 0) return;
      if (event.target.closest("button, input")) return;
      grab = { x: event.screenX - event.clientX, y: event.screenY - event.clientY };
    });
    window.addEventListener("mousemove", (event) => {
      if (!grab || !state.host) return;
      state.host.moveWindow(event.screenX - grab.x, event.screenY - grab.y);
    });
    window.addEventListener("mouseup", () => {
      grab = null;
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
