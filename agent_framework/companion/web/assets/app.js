/* Pixso-designed companion UI wired to the local application service. */
(function () {
  "use strict";

  const $ = (selector, root) => (root || document).querySelector(selector);
  const token = new URLSearchParams(location.search).get("t") || "";

  const el = {
    shell: $("#shell"),
    rail: $("#rail"),
    scrim: $("#scrim"),
    sessionList: $("#sessionList"),
    sessionSearch: $("#sessionSearch"),
    wsCard: $("#wsCard"),
    newTask: $("#newTask"),
    openSettings: $("#openSettings"),
    topbar: $("#topbar"),
    menuBtn: $("#menuBtn"),
    avatarSlot: $("#avatarSlot"),
    personaName: $("#personaName"),
    personaState: $("#personaState"),
    modeSwitch: $("#modeSwitch"),
    toggleInspector: $("#toggleInspector"),
    chat: $("#chat"),
    chatInner: $("#chatInner"),
    jump: $("#jump"),
    runbar: $("#runbar"),
    runStrip: $("#runStrip"),
    runLabel: $("#runLabel"),
    runDetail: $("#runDetail"),
    runElapsed: $("#runElapsed"),
    runSteps: $("#runSteps"),
    runStepsToggle: $("#runStepsToggle"),
    stopRun: $("#stopRun"),
    pills: $("#pills"),
    input: $("#input"),
    count: $("#count"),
    send: $("#send"),
    statusText: $("#statusText"),
    statusHint: $("#statusHint"),
    composer: $("#composer"),
    inspector: $("#inspector"),
    inspectorTabs: $("#inspectorTabs"),
    inspectorBody: $("#inspectorBody"),
    closeInspector: $("#closeInspector"),
    inspectorResizer: $("#inspectorResizer"),
    toast: $("#toast"),
  };

  const state = {
    data: null,
    run: null,
    runStage: "",
    steps: [],
    stepsOpen: false,
    stream: null,
    streamText: "",
    streamTools: [],
    tab: "执行",
    stick: true,
    draftTimer: 0,
    elapsedTimer: 0,
    inspectorOpen: true,
    railDrawer: false,
    inspectorDrawer: false,
    filter: "",
    host: null,
  };

  const COMMANDS = ["minimize", "hide"];

  const EMPTY = {
    chat: {
      title: "今天想聊点什么？",
      body: (name) => `${name} 在听。说说今天的计划、心情，或者只是打个招呼。`,
      prompts: ["整理今天的想法", "陪我复盘一下", "随便聊聊"],
    },
    work: {
      title: "把一件事交给我",
      body: (name) => `${name} 会先给出计划，确认后再动手。`,
      prompts: ["写一份周报", "搜索资料并保存笔记", "梳理项目待办"],
    },
  };

  const TAB_MAP = {
    执行: ["当前状态", "待确认提案", "最近回执"],
    文件: ["文件"],
    来源: ["来源"],
    上下文: ["上下文"],
    任务: ["工作任务", "主动陪伴", "技能"],
  };

  const STATUS_LABELS = {
    awaiting_confirmation: "等待确认",
    running: "正在执行",
    completed: "已完成",
    failed: "执行失败",
    not_executed: "未执行",
    cancelled: "已取消",
    drafted: "草稿",
    unknown: "未知",
  };

  const RECEIPT_LABELS = {
    awaiting_confirmation: "等待确认",
    forwarding: "正在转发",
    accepted: "已被接受",
    completed: "已完成",
    unknown: "结果不确定",
    unavailable: "不可用",
  };

  const STAGE_LABELS = {
    pending: "准备中",
    thinking: "思考中",
    tool: "调用工具",
    answering: "生成回答",
    waiting_approval: "等待审批",
    completed: "已完成",
    cancelled: "已取消",
    failed: "失败",
  };

  const RISK_LABELS = {
    safe: "安全",
    "fs-read": "读取文件",
    "fs-write": "写入文件",
    shell: "执行命令",
    network: "联网",
    "input-control": "控制系统",
  };

  // ---------- transport ----------

  async function api(path, options) {
    const options_ = options || {};
    const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token);
    const response = await fetch(url, {
      ...options_,
      headers: {
        "Content-Type": "application/json",
        "X-Companion-Token": token,
        ...(options_.headers || {}),
      },
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (error) {
      payload = {};
    }
    if (!response.ok) {
      throw new Error(payload.error || response.status + " " + response.statusText);
    }
    return payload;
  }

  function post(path, body) {
    return api(path, { method: "POST", body: JSON.stringify(body || {}) });
  }

  function toast(message) {
    el.toast.textContent = message;
    el.toast.classList.add("on");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.toast.classList.remove("on"), 2400);
  }

  async function guard(promise) {
    try {
      return await promise;
    } catch (error) {
      toast(error.message || "操作失败");
      return null;
    }
  }

  // ---------- avatar ----------

  function avatarSvg(size, mood) {
    const warm = ["warm", "positive", "happy"].includes(mood);
    const low = ["low", "tired", "concerned"].includes(mood);
    const eyeY = low ? 55 : 51;
    const mouth = warm
      ? '<path d="M64 64 q11 12 22 0" fill="none" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>'
      : low
      ? '<path d="M66 69 h18" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>'
      : '<path d="M65 64 q10 8 20 0" fill="none" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>';
    return `<svg class="avatar" width="${size}" height="${size}" viewBox="0 0 150 150" aria-hidden="true">
      <ellipse cx="75" cy="126" rx="44" ry="10" fill="rgba(0,0,0,.22)"/>
      <line x1="75" y1="20" x2="75" y2="10" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <circle cx="75" cy="7" r="6" fill="var(--accent)"/>
      <rect x="39" y="58" width="72" height="62" rx="25" fill="var(--avatar-body)" stroke="var(--avatar-outline)" stroke-width="2"/>
      <ellipse cx="75" cy="52" rx="38" ry="34" fill="var(--avatar-body)" stroke="var(--avatar-outline)" stroke-width="2"/>
      <circle cx="61" cy="${eyeY}" r="4.5" fill="var(--avatar-ink)"/>
      <circle cx="89" cy="${eyeY}" r="4.5" fill="var(--avatar-ink)"/>
      ${mouth}
      <line x1="42" y1="76" x2="30" y2="96" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <line x1="108" y1="76" x2="120" y2="96" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <line x1="58" y1="118" x2="54" y2="130" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
      <line x1="92" y1="118" x2="96" y2="130" stroke="var(--accent)" stroke-width="4" stroke-linecap="round"/>
    </svg>`;
  }

  // ---------- markdown ----------

  // Grammar lives in markdown.js so the Qt bubble and the Web bubble share one
  // sample set; app.js only decorates the rendered blocks.
  const escapeHtml = CompanionMarkdown.escapeHtml;
  const markdown = CompanionMarkdown.render;

  function decorateCode(root) {
    if (!root) return;
    for (const pre of root.querySelectorAll("pre.md-code")) {
      if (pre.parentElement && pre.parentElement.classList.contains("code-block")) continue;
      const wrap = document.createElement("div");
      wrap.className = "code-block";
      pre.parentNode.insertBefore(wrap, pre);
      const label = wrap.previousElementSibling;
      if (label && label.classList.contains("md-lang")) wrap.appendChild(label);
      wrap.appendChild(pre);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "code-copy";
      button.textContent = "复制";
      button.setAttribute("aria-label", "复制代码");
      button.dataset.copy = pre.textContent;
      wrap.appendChild(button);
    }
  }

  // ---------- time ----------

  function localDate(value) {
    const date = new Date(value);
    if (!isNaN(date)) {
      return date.getFullYear() + "-" + (date.getMonth() + 1) + "-" + date.getDate();
    }
    const match = String(value || "").match(/(\d{4})-(\d{2})-(\d{2})/);
    return match ? match[1] + "-" + Number(match[2]) + "-" + Number(match[3]) : "";
  }

  function clock(value) {
    const date = new Date(value);
    if (!isNaN(date)) {
      return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
    }
    const text = String(value || "");
    return text.length >= 16 ? text.slice(11, 16) : "";
  }

  function dayLabel(value) {
    const target = localDate(value);
    const today = localDate(new Date().toISOString());
    const yesterday = localDate(new Date(Date.now() - 86400000).toISOString());
    if (target === today) return "今天";
    if (target === yesterday) return "昨天";
    return target || "更早";
  }

  function groupLabel(value) {
    const target = localDate(value);
    const today = localDate(new Date().toISOString());
    const yesterday = localDate(new Date(Date.now() - 86400000).toISOString());
    if (target === today) return "今天";
    if (target === yesterday) return "昨天";
    const date = new Date(value);
    if (!isNaN(date) && Date.now() - date.getTime() < 7 * 86400000) return "最近 7 天";
    return "更早";
  }

  // ---------- rendering ----------

  function applyUi(ui) {
    document.documentElement.dataset.theme = ui.theme || "charcoal-pink";
    document.documentElement.style.setProperty("--scale", String((ui.font_scale || 100) / 100));
    document.body.classList.toggle("reduce-motion", Boolean(ui.reduce_motion));
    state.inspectorOpen = ui.inspector_visible !== false;
    state.inspectorWidth = ui.inspector_width || 268;
  }

  function render() {
    if (!state.data) return;
    const data = state.data;
    el.personaName.textContent = data.persona.name || "数字分身";
    el.avatarSlot.innerHTML = avatarSvg(34, (data.affect.agent || {}).mood_label || "calm");
    const status = data.status || {};
    el.personaState.textContent = shortState(status);
    el.personaState.className = "pill state " + (status.state === "awaiting_confirmation" ? "accent" : "");

    for (const button of el.modeSwitch.querySelectorAll("button")) {
      const active = button.dataset.mode === data.mode;
      button.classList.toggle("on", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
    el.input.placeholder =
      data.mode === "work"
        ? "把一件事交给我：写周报、查资料、整理待办…"
        : "说点什么，或只是打个招呼…";

    renderRail();
    renderChat();
    renderInspector();
    renderPills();
    renderStatus();
    layout();
  }

  function shortState(status) {
    const label = status.label || "待机";
    if (status.state === "awaiting_confirmation") return "等待确认";
    if (status.state === "working") return "工作中";
    return label.length > 8 ? label.slice(0, 8) : label;
  }

  function renderRail() {
    const data = state.data;
    const workspace = data.workspace || "";
    el.wsCard.innerHTML = workspace
      ? `<span aria-hidden="true">◫</span><span class="path" title="${escapeHtml(workspace)}">${escapeHtml(workspace)}</span><button data-act="clear-ws" title="取消工作区" aria-label="取消工作区">×</button>`
      : `<span aria-hidden="true">◫</span><span class="path">未授权工作区</span><button data-act="pick-ws" title="选择目录" aria-label="选择工作区目录">＋</button>`;

    const filter = state.filter.trim().toLowerCase();
    const sessions = (data.sessions || []).filter(
      (item) =>
        !filter ||
        item.title.toLowerCase().includes(filter) ||
        (item.last_content || "").toLowerCase().includes(filter)
    );
    const groups = new Map();
    for (const item of sessions) {
      const label = groupLabel(item.last_at);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(item);
    }
    const parts = [];
    for (const [label, items] of groups) {
      parts.push(`<div class="group-title"><span>${label}</span><span>${items.length}</span></div>`);
      for (const item of items) {
        const active = item.id === data.session_id;
        parts.push(`<button class="session ${active ? "on" : ""}" data-session="${escapeHtml(item.id)}" title="${escapeHtml(item.title)}"${active ? ' aria-current="true"' : ""}>
          ${item.pinned ? '<span class="pin" role="img" aria-label="已置顶">★</span>' : ""}
          <span class="title">${escapeHtml(item.title)}</span>
          <span class="meta">${clock(item.last_at) || ""}${item.message_count ? " · " + item.message_count + " 条" : ""}${item.unread && !active ? '<span class="dot" role="img" aria-label="有未读消息"></span>' : ""}</span>
        </button>`);
      }
    }
    el.sessionList.innerHTML = parts.join("") || '<div class="empty-note">还没有会话</div>';
  }

  function messageRow(block) {
    const mine = block.role === "user";
    const name = mine ? "你" : state.data.persona.name;
    return `<div class="msg ${mine ? "me" : "ag"}">
      <div class="bubble">
        <div class="meta"><span>${escapeHtml(name)} ${clock(block.created_at)}</span><button class="copy" data-copy="${escapeHtml(block.text)}">复制</button></div>
        <div class="body">${markdown(block.text)}</div>
      </div>
    </div>`;
  }

  function renderChat() {
    const data = state.data;
    const stick = state.stick;
    const parts = [];
    let lastDay = "";
    if (!data.messages.length && !data.approvals.length && !state.stream) {
      const mode = data.mode === "work" ? EMPTY.work : EMPTY.chat;
      parts.push(`<div class="empty">
        <h2>${mode.title}</h2>
        <p>${escapeHtml(mode.body(data.persona.name))}</p>
        <div class="prompts">${mode.prompts.map((text) => `<button data-prompt="${escapeHtml(text)}">${escapeHtml(text)}</button>`).join("")}</div>
      </div>`);
    }
    for (const block of data.messages) {
      const day = dayLabel(block.created_at);
      if (day !== lastDay) {
        parts.push(`<div class="day">${escapeHtml(day)}</div>`);
        lastDay = day;
      }
      parts.push(messageRow(block));
    }
    for (const approval of data.approvals) {
      parts.push(approvalHtml(approval));
    }
    parts.push('<div id="toolStack"></div>');
    parts.push('<div id="streamSlot" aria-live="polite"></div>');
    el.chatInner.innerHTML = parts.join("");
    decorateCode(el.chatInner);
    if (state.stream) {
      ensureStream();
      renderTools();
    }
    if (stick) requestAnimationFrame(() => scrollBottom());
  }

  function approvalHtml(view) {
    const preview = view.preview || {};
    const status = view.status;
    const awaiting = status === "awaiting_confirmation" && !view.expired;
    const chips = [
      `<span class="pill accent">${status === "completed" ? "已执行" : "待确认写入"}</span>`,
      `<span class="pill ${view.expired ? "danger" : ""}">${view.expired ? "已过期" : STATUS_LABELS[status] || status}</span>`,
    ];
    const risk = RISK_LABELS[view.risk] || view.risk || "";
    const rows = [
      ["目标", view.target_summary || preview.relative_path || ""],
      ["风险", risk],
      ["工作区", view.workspace || preview.workspace || ""],
      ["可撤销", view.reversible ? "有备份" : "不可撤销"],
      ["模式", view.mode === "work" ? "工作" : "陪伴"],
      ["有效期", (view.expires_at || "").replace("T", " ").slice(0, 16)],
    ];
    const diffLines = (preview.diff || [])
      .slice(0, 40)
      .map(([kind, text]) => {
        const cls = kind === "add" ? "add" : kind === "del" ? "del" : "ctx";
        const sign = kind === "add" ? "+" : kind === "del" ? "-" : " ";
        return `<div class="${cls}">${escapeHtml(sign + " " + text)}</div>`;
      })
      .join("");
    const changeLabel =
      preview.change === "create"
        ? "新建文件"
        : preview.change === "update"
        ? "更新文件"
        : preview.change === "unavailable"
        ? "改动预览不可用"
        : preview.change || "";
    const actions = awaiting
      ? `<button class="primary" data-approve="${escapeHtml(view.action_id)}">允许这次写入</button>
         <button class="ghost" data-cancel-approval="${escapeHtml(view.action_id)}">取消</button>`
      : `<span class="hint">${view.expired ? "提案已过期，请重新生成。" : "这条动作已经处理。"}</span>`;
    return `<div class="approval" data-approval="${escapeHtml(view.action_id)}">
      <div class="chips">${chips.join("")}</div>
      <div class="meta">${rows
        .map(
          ([label, value]) =>
            `<div class="row"><label>${label}</label><b title="${escapeHtml(value)}">${escapeHtml(value || "—")}</b></div>`
        )
        .join("")}</div>
      <div class="plan">${escapeHtml(view.plan || "")}</div>
      <div class="diff-line"><span>${changeLabel}</span><span class="add">+${preview.added || 0}</span><span class="del">-${preview.removed || 0}</span><span>行</span></div>
      ${diffLines ? `<div class="diff">${diffLines}</div>` : ""}
      <div class="actions">${actions}</div>
    </div>`;
  }

  function taskCards() {
    const tasks = (state.data && state.data.tasks) || [];
    if (!tasks.length) return "";
    return tasks
      .map((task) => {
        const actionable = task.state === "PLAN_REVIEW" || task.state === "PLAN_DISCUSSING";
        const canApprove = task.state === "PLAN_REVIEW" && task.approval_status === "pending";
        const receipt = task.receipt
          ? `<div class="line">回执：${escapeHtml(RECEIPT_LABELS[task.receipt.status] || task.receipt.status)}${task.receipt.summary ? " · " + escapeHtml(task.receipt.summary) : ""}</div>`
          : "";
        const actions = actionable
          ? `<div class="actions">
              ${canApprove ? `<button class="primary" data-task-act="approve" data-task-id="${escapeHtml(task.task_id)}">批准计划</button>` : ""}
              <button class="ghost" data-task-act="revise" data-task-id="${escapeHtml(task.task_id)}">修改</button>
              <button class="ghost danger-text" data-task-act="reject" data-task-id="${escapeHtml(task.task_id)}">拒绝</button>
            </div>`
          : "";
        return `<div class="section-card task-card">
          <div class="task-head"><b>${escapeHtml(task.title || "工作任务")}</b><span class="state">${escapeHtml(task.state_label || task.state)}</span></div>
          ${task.reason ? `<div class="line">原因：${escapeHtml(task.reason)}</div>` : ""}
          ${task.goal ? `<div class="line">目标：${escapeHtml(task.goal)}</div>` : ""}
          <div class="line">执行器：${escapeHtml(task.executor || "未指定")}${task.project ? " · 项目：" + escapeHtml(task.project) : ""}</div>
          ${receipt}
          ${actions}
        </div>`;
      })
      .join("");
  }

  function renderInspector() {
    const data = state.data;
    const wanted = TAB_MAP[state.tab] || [];
    const sections = (data.inspector || []).filter((section) => wanted.includes(section.title));
    const cards = state.tab === "任务" ? taskCards() : "";
    if (!sections.length && !cards) {
      el.inspectorBody.innerHTML = '<div class="empty-note">这一栏暂时没有内容</div>';
    } else {
      el.inspectorBody.innerHTML =
        cards +
        sections
          .map(
            (section) => `<div class="section-card">
            <div class="section-title">${escapeHtml(section.title)}</div>
            ${section.lines.map((line) => `<div class="line">${escapeHtml(line)}</div>`).join("") || '<div class="line muted">—</div>'}
          </div>`
          )
          .join("");
    }
    for (const button of el.inspectorTabs.querySelectorAll("button")) {
      const active = button.dataset.tab === state.tab;
      button.classList.toggle("on", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    }
  }

  function renderPills() {
    const data = state.data;
    const workspace = data.workspace ? shortPath(data.workspace) : "选择工作区";
    const context = contextView();
    const pills = [
      `<button data-pill="mode" title="切换模式">${data.mode === "work" ? "工作" : "陪伴"}</button>`,
      `<button data-pill="workspace" title="${escapeHtml(data.workspace || "选择工作区")}">${escapeHtml(workspace)}</button>`,
      context
        ? `<button data-pill="context" class="gauge ${context.level}" style="--percent: ${context.percent}"` +
          ` title="上下文 ${context.used} / ${context.limit} 字符${context.compressed ? "（历史已压缩）" : ""}">` +
          `<span class="gauge-ring" aria-hidden="true"></span>` +
          `<span class="gauge-text">上下文 ${context.percent}%</span></button>`
        : "",
      data.approvals.length ? `<button data-pill="approval" class="danger-text">待确认 ${data.approvals.length}</button>` : "",
    ];
    el.pills.innerHTML = pills.join("");
    if (context) {
      const button = el.pills.querySelector("[data-pill=context]");
      if (button) button.setAttribute("aria-label", `上下文用量 ${context.percent}%，点击查看组成`);
    }
  }

  function contextView() {
    const context = state.data ? state.data.context : null;
    if (!context) return null;
    const used = Number(context.context_used) || 0;
    const limit = Number(context.context_limit) || 0;
    if (limit <= 0) return null;
    const percent = Math.min(100, Math.round((used / limit) * 100));
    return {
      percent: percent,
      used: used,
      limit: limit,
      compressed: Boolean(context.compressed),
      level: percent >= 90 ? "danger" : percent >= 70 ? "warning" : "ok",
    };
  }

  function shortPath(path) {
    const parts = String(path).split(/[\\/]/).filter(Boolean);
    if (parts.length <= 2) return parts.join(" / ") || path;
    return parts.slice(-2).join(" / ");
  }

  function renderStatus() {
    const data = state.data;
    const summary = data.settings_summary || {};
    const proactive = data.proactive || {};
    const bits = ["已就绪"];
    if (summary.memory_count) bits.push(`本地记忆 ${summary.memory_count} 条`);
    if (proactive.daily_count !== undefined) bits.push(`今日主动 ${proactive.daily_count} 次`);
    el.statusText.textContent = bits.join(" · ");
    el.statusHint.textContent = data.persona.loaded
      ? `人设 ${data.persona.name} ${data.persona.version || ""}`.trim()
      : data.persona.error
      ? "人设加载失败"
      : "";
  }

  function responsiveWidth() {
    // Type scales but the window does not, so the breakpoints measure the window
    // in 100% equivalents: 1000px at 200% has the room of 500px at 100%.
    const scale = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--scale")) || 1;
    return window.innerWidth / scale;
  }

  function layout() {
    const px = window.innerWidth;
    const width = responsiveWidth();
    const railAuto = width < 860;
    const inspectorAuto = width < 940;
    document.body.classList.toggle("compact", width < 780);
    el.menuBtn.classList.toggle("show", railAuto);
    el.rail.classList.toggle("hidden", railAuto ? !state.railDrawer : false);
    el.shell.classList.toggle("drawer", railAuto && state.railDrawer);
    el.scrim.classList.toggle("show", railAuto && state.railDrawer);

    const inspectorVisible = inspectorAuto ? state.inspectorDrawer : state.inspectorOpen;
    el.inspector.classList.toggle("hidden", !inspectorVisible);
    el.inspector.classList.toggle("floating", inspectorAuto && state.inspectorDrawer);
    if (inspectorVisible) {
      const max = Math.max(220, Math.min(420, px - 260));
      el.inspector.style.width = Math.max(220, Math.min(state.inspectorWidth || 268, max)) + "px";
    }
  }

  // ---------- streaming ----------

  function ensureStream() {
    const slot = $("#streamSlot");
    if (!slot) return null;
    let row = $("#streamRow");
    if (!row) {
      slot.innerHTML = `<div class="msg ag" id="streamRow">
        <div class="bubble streaming">
          <div class="meta"><span>${escapeHtml(state.data.persona.name)}</span><span></span></div>
          <div class="body"></div>
        </div>
      </div>`;
      row = $("#streamRow");
    }
    const body = $(".body", row);
    if (body) {
      body.innerHTML = markdown(state.streamText);
      decorateCode(body);
    }
    return row;
  }

  function renderTools() {
    const stack = $("#toolStack");
    if (!stack) return;
    stack.innerHTML = state.streamTools
      .map((tool) => {
        const running = tool.state === "running";
        const failed = tool.state === "error";
        return `<div class="tool-card ${running ? "running" : ""}">
          <div class="head"><span>${escapeHtml(tool.name)}</span><span>·</span><span class="state ${failed ? "bad" : running ? "" : "ok"}">${running ? "运行中" : failed ? "失败" : "已完成"}</span><span style="margin-left:auto" class="muted">${running ? escapeHtml(tool.arguments || "") : "详情"}</span></div>
          ${tool.summary ? `<div class="detail" title="${escapeHtml(tool.summary)}">${escapeHtml(tool.summary)}</div>` : ""}
        </div>`;
      })
      .join("");
  }

  function beginRun(runId, stage) {
    state.run = runId;
    state.stream = true;
    state.streamText = "";
    state.streamTools = [];
    state.runStage = stage || "";
    el.runStrip.classList.add("show");
    el.composer.classList.add("awaiting");
    el.send.classList.add("stop");
    el.send.textContent = "停止";
    el.runLabel.textContent = STAGE_LABELS[state.runStage] || "思考中";
    el.runDetail.textContent = "";
    renderRunSteps([]);
    startElapsed();
    renderChat();
    state.stick = true;
    scrollBottom();
  }

  function finishRun() {
    state.run = null;
    state.stream = null;
    state.streamText = "";
    state.streamTools = [];
    state.runStage = "";
    el.runStrip.classList.remove("show");
    el.composer.classList.remove("awaiting");
    el.send.classList.remove("stop");
    el.send.textContent = "发送";
    stopElapsed();
    el.runElapsed.textContent = "";
    renderRunSteps([]);
  }

  function renderRunSteps(steps) {
    const rows = steps || [];
    state.steps = rows;
    el.runStepsToggle.hidden = rows.length < 2;
    el.runSteps.hidden = !state.stepsOpen || !rows.length;
    el.runStepsToggle.setAttribute("aria-expanded", state.stepsOpen ? "true" : "false");
    el.runSteps.innerHTML = rows
      .map(
        (step) => `<li class="${escapeHtml(step.stage)}">
          <span class="stage">${escapeHtml(STAGE_LABELS[step.stage] || step.stage)}</span>
          <span class="what">${escapeHtml(step.detail || step.tool || "")}</span>
          <span class="at">${escapeHtml(clock(step.at))}</span>
        </li>`
      )
      .join("");
    if (!el.runSteps.hidden) el.runSteps.scrollTop = el.runSteps.scrollHeight;
  }

  function handleEvent(event) {
    const kind = event.kind;
    if (kind === "started") {
      beginRun(event.run_id, event.stage);
      return;
    }
    if (event.run_id === state.run && event.stage && event.stage !== state.runStage) {
      state.runStage = event.stage;
      scheduleRefresh();
    }
    if (kind === "delta") {
      state.streamText += event.text || "";
      ensureStream();
      if (state.stick) scrollBottom();
      return;
    }
    if (kind === "reset") {
      state.streamText = "";
      ensureStream();
      return;
    }
    if (kind === "tool") {
      const tool = {
        name: event.tool_name || "",
        state: event.state || "",
        summary: event.summary || event.error || "",
        arguments: summarizeArguments(event.arguments),
      };
      const existing = state.streamTools.find(
        (item) => item.name === tool.name && item.state === "running"
      );
      if (existing && tool.state !== "running") {
        Object.assign(existing, tool);
      } else {
        state.streamTools.push(tool);
      }
      renderTools();
      if (state.stick) scrollBottom();
      return;
    }
    if (kind === "error") {
      state.streamText += (state.streamText ? "\n\n" : "") + (event.message || "执行失败。");
      ensureStream();
      return;
    }
    if (kind === "done") {
      scheduleRefresh();
    }
  }

  function summarizeArguments(args) {
    if (!args) return "";
    if (typeof args === "string") return args;
    try {
      return Object.entries(args)
        .map(([key, value]) => `${key}=${String(value).slice(0, 24)}`)
        .join(" ");
    } catch (error) {
      return "";
    }
  }

  function startElapsed() {
    stopElapsed();
    const started = Date.now();
    state.elapsedTimer = setInterval(() => {
      const seconds = Math.round((Date.now() - started) / 1000);
      el.runElapsed.textContent = seconds + " 秒";
    }, 1000);
    el.runElapsed.textContent = "0 秒";
  }

  function stopElapsed() {
    if (state.elapsedTimer) clearInterval(state.elapsedTimer);
    state.elapsedTimer = 0;
  }

  // ---------- scrolling ----------

  function isNearBottom() {
    return el.chat.scrollHeight - el.chat.scrollTop - el.chat.clientHeight < 48;
  }

  function scrollBottom() {
    el.chat.scrollTop = el.chat.scrollHeight;
  }

  function onScroll() {
    state.stick = isNearBottom();
    el.jump.classList.toggle("show", !state.stick);
  }

  // ---------- data ----------

  async function refresh() {
    const data = await guard(api("/api/bootstrap"));
    if (!data) return;
    state.data = data;
    applyUi(data.ui);
    if (data.run) {
      if (data.run.run_id !== state.run) {
        state.run = data.run.run_id;
        state.stream = true;
        state.streamText = "";
        state.streamTools = [];
      }
      state.runStage = data.run.stage;
      el.runStrip.classList.add("show");
      el.runLabel.textContent = STAGE_LABELS[data.run.stage] || "运行中";
      el.runDetail.textContent = data.run.detail || data.run.tool || "";
      renderRunSteps(data.run.steps);
      el.send.classList.add("stop");
      el.send.textContent = "停止";
    } else if (state.run) {
      finishRun();
    }
    render();
  }

  let refreshTimer = 0;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, 140);
  }

  function connectEvents() {
    const source = new EventSource("/api/events?t=" + encodeURIComponent(token));
    source.onmessage = (message) => {
      let payload;
      try {
        payload = JSON.parse(message.data);
      } catch (error) {
        return;
      }
      if (payload.type === "turn") {
        handleEvent(payload.event);
      } else if (payload.type === "refresh") {
        scheduleRefresh();
      } else if (payload.type === "ui") {
        applyUi(payload.ui);
        render();
      } else if (payload.type === "shutdown") {
        source.close();
      }
    };
    source.onerror = () => {
      // EventSource reconnects on its own; keep the UI usable meanwhile.
    };
  }

  // ---------- actions ----------

  async function sendTurn() {
    const text = el.input.value.trim();
    if (!text) return;
    if (state.run) {
      toast("正在生成回复，请先停止或等待完成。");
      return;
    }
    el.input.value = "";
    resizeInput();
    updateCount();
    await guard(post("/api/turn", { text: text, mode: state.data.mode }));
  }

  async function stopRun() {
    if (!state.run) return;
    await guard(post("/api/runs/cancel", { run_id: state.run }));
  }

  async function switchMode(mode) {
    if (!state.data || state.data.mode === mode) return;
    const data = await guard(post("/api/mode", { mode: mode }));
    if (data) {
      state.data.mode = data.mode;
      render();
    }
  }

  async function switchSession(sessionId) {
    if (!sessionId || sessionId === state.data.session_id) {
      state.railDrawer = false;
      layout();
      return;
    }
    await guard(post("/api/sessions/switch", { session_id: sessionId }));
    state.railDrawer = false;
    state.stick = true;
    await refresh();
  }

  async function newSession() {
    await guard(post("/api/sessions/create", { mode: state.data.mode }));
    state.railDrawer = false;
    state.stick = true;
    await refresh();
    el.input.focus();
  }

  function openSettings() {
    if (state.host) state.host.openSettings();
    else window.open("/settings?t=" + encodeURIComponent(token), "_blank");
  }

  function openConsole() {
    if (state.host) state.host.openConsole();
    else window.open("/console?t=" + encodeURIComponent(token), "_blank");
  }

  async function chooseWorkspace() {
    const path = window.prompt("输入要授权的工作目录绝对路径", state.data.workspace || "");
    if (!path) return;
    await guard(post("/api/workspace", { path: path }));
    await refresh();
  }

  async function clearWorkspace() {
    await guard(post("/api/workspace", { clear: true }));
    await refresh();
  }

  function resizeInput() {
    const input = el.input;
    input.style.height = "auto";
    const line = parseFloat(getComputedStyle(input).lineHeight) || 20;
    const min = line * 2;
    const max = line * 5;
    input.style.height = Math.max(min, Math.min(input.scrollHeight, max)) + "px";
  }

  function updateCount() {
    el.count.textContent = el.input.value.length + " 字";
  }

  function saveDraft() {
    clearTimeout(state.draftTimer);
    state.draftTimer = setTimeout(() => {
      post("/api/draft", { content: el.input.value }).catch(() => {});
    }, 600);
  }

  async function confirmApproval(actionId) {
    await guard(post("/api/approvals/confirm", { action_id: actionId }));
    await refresh();
  }

  async function cancelApproval(actionId) {
    await guard(post("/api/approvals/cancel", { action_id: actionId }));
    await refresh();
  }

  async function taskAction(action, taskId) {
    if (action === "approve") {
      if (!window.confirm("批准这份计划并开始执行？")) return;
      await guard(post("/api/console/tasks", { action: "approve", task_id: taskId }));
    } else if (action === "revise") {
      const instruction = window.prompt("要修改什么？");
      if (!instruction) return;
      await guard(
        post("/api/console/tasks", { action: "revise", task_id: taskId, instruction: instruction })
      );
    } else if (action === "reject") {
      const reason = window.prompt("拒绝原因（可留空）", "");
      if (reason === null) return;
      await guard(post("/api/console/tasks", { action: "reject", task_id: taskId, reason: reason }));
    }
    await refresh();
  }

  function showContextMenu(event, sessionId) {
    event.preventDefault();
    const existing = $("#sessionMenu");
    if (existing) existing.remove();
    const menu = document.createElement("div");
    menu.id = "sessionMenu";
    menu.style.cssText =
      "position:fixed;z-index:60;min-width:120px;padding:4px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);box-shadow:var(--shadow);font-size:var(--fs-ui)";
    menu.style.left = Math.min(event.clientX, window.innerWidth - 140) + "px";
    menu.style.top = Math.min(event.clientY, window.innerHeight - 120) + "px";
    const options = [
      ["重命名", async () => {
        const item = state.data.sessions.find((row) => row.id === sessionId);
        const title = window.prompt("会话名称", item ? item.title : "");
        if (!title) return;
        await guard(post("/api/sessions/rename", { session_id: sessionId, title: title }));
        await refresh();
      }],
      ["置顶 / 取消置顶", async () => {
        const item = state.data.sessions.find((row) => row.id === sessionId);
        await guard(post("/api/sessions/pin", { session_id: sessionId, pinned: !(item && item.pinned) }));
        await refresh();
      }],
      ["删除", async () => {
        if (!window.confirm("删除这个会话及其消息？")) return;
        await guard(post("/api/sessions/delete", { session_id: sessionId }));
        await refresh();
      }],
    ];
    for (const [label, action] of options) {
      const button = document.createElement("button");
      button.textContent = label;
      button.style.cssText = "display:block;width:100%;text-align:left;padding:6px 9px;border-radius:5px";
      button.onmouseenter = () => (button.style.background = "var(--hover)");
      button.onmouseleave = () => (button.style.background = "transparent");
      button.onclick = () => {
        menu.remove();
        action();
      };
      menu.appendChild(button);
    }
    document.body.appendChild(menu);
    setTimeout(() => {
      const close = () => {
        menu.remove();
        document.removeEventListener("mousedown", close);
      };
      document.addEventListener("mousedown", close);
    }, 0);
  }

  // ---------- events ----------

  function bind() {
    el.newTask.onclick = newSession;
    el.openSettings.onclick = openSettings;
    el.menuBtn.onclick = () => {
      state.railDrawer = !state.railDrawer;
      layout();
    };
    el.scrim.onclick = () => {
      state.railDrawer = false;
      layout();
    };
    el.closeInspector.onclick = () => {
      state.inspectorOpen = false;
      state.inspectorDrawer = false;
      layout();
      post("/api/ui", { inspector_visible: false }).catch(() => {});
    };
    el.toggleInspector.onclick = () => {
      if (responsiveWidth() < 940) {
        state.inspectorDrawer = !state.inspectorDrawer;
      } else {
        state.inspectorOpen = !state.inspectorOpen;
        post("/api/ui", { inspector_visible: state.inspectorOpen }).catch(() => {});
      }
      layout();
    };
    el.inspectorTabs.onclick = (event) => {
      const button = event.target.closest("button[data-tab]");
      if (!button) return;
      state.tab = button.dataset.tab;
      renderInspector();
    };
    el.modeSwitch.onclick = (event) => {
      const button = event.target.closest("button[data-mode]");
      if (button) switchMode(button.dataset.mode);
    };
    el.send.onclick = () => {
      if (state.run) stopRun();
      else sendTurn();
    };
    el.stopRun.onclick = stopRun;
    el.runStepsToggle.onclick = () => {
      state.stepsOpen = !state.stepsOpen;
      renderRunSteps(state.steps);
    };
    el.input.addEventListener("input", () => {
      resizeInput();
      updateCount();
      saveDraft();
    });
    el.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendTurn();
      }
    });
    el.chat.addEventListener("scroll", onScroll);
    el.jump.onclick = () => {
      state.stick = true;
      el.jump.classList.remove("show");
      scrollBottom();
    };
    el.sessionList.onclick = (event) => {
      const item = event.target.closest(".session");
      if (item) switchSession(item.dataset.session);
    };
    el.sessionList.oncontextmenu = (event) => {
      const item = event.target.closest(".session");
      if (item) showContextMenu(event, item.dataset.session);
    };
    el.sessionSearch.oninput = () => {
      state.filter = el.sessionSearch.value;
      renderRail();
    };
    el.wsCard.onclick = (event) => {
      const action = event.target.closest("[data-act]");
      if (!action) return;
      if (action.dataset.act === "pick-ws") chooseWorkspace();
      else clearWorkspace();
    };
    el.chatInner.addEventListener("click", (event) => {
      const copy = event.target.closest("[data-copy]");
      if (copy) {
        navigator.clipboard.writeText(copy.dataset.copy).then(
          () => toast("已复制"),
          () => toast("复制失败")
        );
        return;
      }
      const promptButton = event.target.closest("[data-prompt]");
      if (promptButton) {
        el.input.value = promptButton.dataset.prompt;
        resizeInput();
        updateCount();
        sendTurn();
        return;
      }
      const approve = event.target.closest("[data-approve]");
      if (approve) {
        confirmApproval(approve.dataset.approve);
        return;
      }
      const cancel = event.target.closest("[data-cancel-approval]");
      if (cancel) cancelApproval(cancel.dataset.cancelApproval);
    });
    el.inspectorBody.onclick = (event) => {
      const button = event.target.closest("[data-task-act]");
      if (!button) return;
      taskAction(button.dataset.taskAct, button.dataset.taskId);
    };
    el.pills.onclick = (event) => {
      const pill = event.target.closest("[data-pill]");
      if (!pill) return;
      if (pill.dataset.pill === "mode") switchMode(state.data.mode === "work" ? "chat" : "work");
      else if (pill.dataset.pill === "workspace") chooseWorkspace();
      else if (pill.dataset.pill === "approval") {
        const approval = $("[data-approval]");
        if (approval) approval.scrollIntoView({ block: "center", behavior: "smooth" });
      } else if (pill.dataset.pill === "context") {
        state.tab = "上下文";
        state.inspectorOpen = true;
        layout();
        renderInspector();
      }
    };
    for (const button of document.querySelectorAll("[data-cmd]")) {
      button.onclick = () => {
        if (state.host) state.host[button.dataset.cmd]();
        else if (button.dataset.cmd === "hide") window.close();
      };
    }
    window.addEventListener("resize", layout);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const menu = $("#sessionMenu");
        if (menu) menu.remove();
        state.railDrawer = false;
        state.inspectorDrawer = false;
        layout();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "n") {
        event.preventDefault();
        newSession();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        el.sessionSearch.focus();
        el.sessionSearch.select();
      }
      if ((event.ctrlKey || event.metaKey) && event.key === ",") {
        event.preventDefault();
        openSettings();
      }
    });
    bindInspectorResize();
    bindWindowDrag();
  }

  function bindInspectorResize() {
    let drag = null;
    el.inspectorResizer.addEventListener("mousedown", (event) => {
      drag = { x: event.clientX, width: el.inspector.getBoundingClientRect().width };
      event.preventDefault();
    });
    window.addEventListener("mousemove", (event) => {
      if (!drag) return;
      const width = Math.max(220, Math.min(420, drag.width - (event.clientX - drag.x)));
      state.inspectorWidth = width;
      el.inspector.style.width = width + "px";
    });
    window.addEventListener("mouseup", () => {
      if (!drag) return;
      drag = null;
      post("/api/ui", { inspector_width: Math.round(state.inspectorWidth || 268) }).catch(() => {});
    });
  }

  function bindWindowDrag() {
    let grab = null;
    el.topbar.addEventListener("mousedown", (event) => {
      if (!state.host || event.button !== 0) return;
      if (event.target.closest("button, input, textarea")) return;
      grab = { x: event.screenX - event.clientX, y: event.screenY - event.clientY };
      document.body.style.userSelect = "none";
    });
    window.addEventListener("mousemove", (event) => {
      if (!grab || !state.host) return;
      state.host.moveWindow(event.screenX - grab.x, event.screenY - grab.y);
    });
    window.addEventListener("mouseup", () => {
      grab = null;
      document.body.style.userSelect = "";
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
      if (state.host && state.host.ready) state.host.ready();
    });
  }

  async function start() {
    await refresh();
    if (!state.data) {
      document.body.innerHTML =
        '<div class="empty-note">无法连接本地服务，请重新启动应用。</div>';
      return;
    }
    if (state.data.draft) {
      el.input.value = state.data.draft;
      resizeInput();
      updateCount();
    }
    bind();
    connectHost();
    connectEvents();
    el.input.focus();
  }

  start();
})();
