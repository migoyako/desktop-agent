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
    railToggle: $("#railToggle"),
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
    queueDock: $("#queueDock"),
    queueCount: $("#queueCount"),
    queueList: $("#queueList"),
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
    queue: [],
    queueSeq: 0,
    queueEpoch: 0,
    queueSession: "",
    flushing: false,
    tab: "执行",
    stick: true,
    draftTimer: 0,
    elapsedTimer: 0,
    inspectorOpen: true,
    railDrawer: false,
    railCollapsed: false,
    inspectorDrawer: false,
    filter: "",
    host: null,
  };

  const COMMANDS = ["minimize", "hide"];

  // A run owns the session until it ends, so extra input waits here instead of
  // being rejected; five is enough to cover "I thought of three more things".
  const QUEUE_LIMIT = 5;

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

  // Artwork lives in avatar.js so the header slot and the desktop overlay draw
  // the same figure; this page only chooses the size and the CSS class.

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
    CompanionAvatar.mount(
      el.avatarSlot,
      34,
      (data.affect.agent || {}).mood_label || "calm",
      "avatar"
    );
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
      ? `<span aria-hidden="true">◫</span><span class="path" title="${escapeHtml(workspace)}">${escapeHtml(workspace)}</span>` +
        `<span class="acts"><button data-act="clear-ws" title="取消工作区" aria-label="取消工作区">×</button></span>`
      : `<span aria-hidden="true">◫</span><span class="path">未授权工作区</span>` +
        `<span class="acts"><button data-act="pick-ws" title="选择目录" aria-label="选择工作区目录">＋</button></span>`;

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
        const glyph = Array.from((item.title || "").trim())[0] || "·";
        parts.push(`<button class="session ${active ? "on" : ""}" data-session="${escapeHtml(item.id)}" title="${escapeHtml(item.title)}"${active ? ' aria-current="true"' : ""}>
          <span class="glyph" aria-hidden="true">${escapeHtml(glyph)}</span>
          ${item.pinned ? '<span class="pin" role="img" aria-label="已置顶">★</span>' : ""}
          <span class="title">${escapeHtml(item.title)}</span>
          <span class="meta">${clock(item.last_at) || ""}${item.message_count ? " · " + item.message_count + " 条" : ""}${item.unread && !active ? '<span class="dot" role="img" aria-label="有未读消息"></span>' : ""}</span>
        </button>`);
      }
    }
    el.sessionList.innerHTML = parts.join("") || '<div class="empty-note">还没有会话</div>';
  }

  function messageActionIds(block) {
    const actions = (state.data && state.data.message_actions) || {};
    return {
      regenerate: Number(actions.regenerate_id) === block.id,
      source: Number(actions.source_id) === block.id,
    };
  }

  function messageActionsHtml(block) {
    const ids = messageActionIds(block);
    return (
      `<button class="copy" data-copy="${escapeHtml(block.text)}">复制</button>` +
      (ids.regenerate
        ? `<button class="msg-act" type="button" data-msg-act="regenerate" data-act-mid="${block.id}" title="用同一条消息重新生成回复">重新生成</button>`
        : "") +
      `<button class="msg-act" type="button" data-msg-act="remember" data-act-mid="${block.id}" title="把这条内容保存为长期记忆">存为记忆</button>` +
      (ids.source
        ? `<button class="msg-act" type="button" data-msg-act="source" data-act-mid="${block.id}" title="查看这轮回复引用的来源">跳转来源</button>`
        : "")
    );
  }

  function messageActionsSignature(block) {
    const ids = messageActionIds(block);
    return (ids.regenerate ? "r" : "") + (ids.source ? "s" : "");
  }

  function messageRow(block) {
    const mine = block.role === "user";
    const name = mine ? "你" : state.data.persona.name;
    return `<div class="msg ${mine ? "me" : "ag"}" data-mid="${block.id}">
      <div class="bubble">
        <div class="meta"><span>${escapeHtml(name)} ${clock(block.created_at)}</span><span class="msg-actions">${messageActionsHtml(block)}</span></div>
        <div class="body">${markdown(block.text)}</div>
      </div>
      <span class="tail" aria-hidden="true"></span>
    </div>`;
  }

  function elementFrom(html) {
    const template = document.createElement("template");
    template.innerHTML = html.trim();
    return template.content.firstElementChild;
  }

  function messageNode(block) {
    const node = elementFrom(messageRow(block));
    node.dataset.key = "m:" + block.id;
    node.dataset.text = block.text;
    node.dataset.actions = messageActionsSignature(block);
    decorateCode(node);
    return node;
  }

  function updateMessageActions(node, block) {
    const slot = $(".meta .msg-actions", node);
    if (!slot) return;
    slot.innerHTML = messageActionsHtml(block);
    node.dataset.actions = messageActionsSignature(block);
  }

  function updateMessageText(node, text) {
    const body = $(".body", node);
    if (!body) return;
    body.innerHTML = markdown(text);
    decorateCode(body);
    node.dataset.text = text;
    const copy = $("[data-copy]", node);
    if (copy) copy.dataset.copy = text;
  }

  function keyedNodes(container) {
    const nodes = new Map();
    for (const child of Array.from(container.children)) {
      if (child.dataset.key) nodes.set(child.dataset.key, child);
    }
    return nodes;
  }

  function removeStale(container, nodes, desired) {
    const keep = new Set(desired);
    for (const [key, node] of nodes) {
      if (!keep.has(key) && node.parentNode === container) node.remove();
    }
  }

  function orderNodes(container, nodes, desired) {
    desired.forEach((key, index) => {
      const node = nodes.get(key);
      const at = container.children[index];
      if (node && at !== node) container.insertBefore(node, at || null);
    });
  }

  // The transcript is reconciled by message id: history nodes are reused as-is,
  // so a refresh only appends what is new instead of rebuilding every bubble.
  function syncMessages(container, messages) {
    const nodes = keyedNodes(container);
    const desired = [];
    let lastDay = "";
    for (const block of messages) {
      const day = dayLabel(block.created_at);
      if (day !== lastDay) {
        const key = "day:" + block.id;
        if (!nodes.has(key)) {
          const divider = document.createElement("div");
          divider.className = "day";
          divider.dataset.key = key;
          divider.textContent = day;
          nodes.set(key, divider);
        }
        desired.push(key);
        lastDay = day;
      }
      const key = "m:" + block.id;
      let node = nodes.get(key);
      if (!node) {
        node = messageNode(block);
        nodes.set(key, node);
      } else if (node.dataset.text !== block.text) {
        updateMessageText(node, block.text);
      }
      if (node.dataset.actions !== messageActionsSignature(block)) {
        updateMessageActions(node, block);
      }
      desired.push(key);
    }
    removeStale(container, nodes, desired);
    orderNodes(container, nodes, desired);
  }

  function approvalNode(view) {
    const node = elementFrom(approvalHtml(view));
    node.dataset.key = "a:" + view.action_id;
    node.dataset.signature = view.status + "|" + (view.expired ? "1" : "0");
    return node;
  }

  function syncApprovals(container, approvals) {
    const nodes = keyedNodes(container);
    const desired = [];
    for (const view of approvals) {
      const key = "a:" + view.action_id;
      const signature = view.status + "|" + (view.expired ? "1" : "0");
      let node = nodes.get(key);
      if (!node || node.dataset.signature !== signature) {
        const fresh = approvalNode(view);
        if (node && node.parentNode === container) node.replaceWith(fresh);
        nodes.set(key, fresh);
        node = fresh;
      }
      desired.push(key);
    }
    removeStale(container, nodes, desired);
    orderNodes(container, nodes, desired);
  }

  function chatSlots() {
    let list = $("#msgList", el.chatInner);
    if (!list) {
      el.chatInner.innerHTML =
        '<div id="chatEmpty" class="empty" hidden></div>' +
        '<div id="msgList"></div>' +
        '<div id="approvalList"></div>' +
        '<div id="toolStack"></div>' +
        '<div id="streamSlot" aria-live="polite"></div>';
      list = $("#msgList", el.chatInner);
    }
    return {
      empty: $("#chatEmpty", el.chatInner),
      list,
      approvals: $("#approvalList", el.chatInner),
      tools: $("#toolStack", el.chatInner),
      stream: $("#streamSlot", el.chatInner),
    };
  }

  function renderEmpty(container, data) {
    const show = !data.messages.length && !data.approvals.length && !state.stream;
    container.hidden = !show;
    if (!show) return;
    const mode = data.mode === "work" ? EMPTY.work : EMPTY.chat;
    const signature = data.mode + "\u0000" + (data.persona.name || "");
    if (container.dataset.signature === signature) return;
    container.dataset.signature = signature;
    container.innerHTML = `
      <h2>${mode.title}</h2>
      <p>${escapeHtml(mode.body(data.persona.name))}</p>
      <div class="prompts">${mode.prompts.map((text) => `<button data-prompt="${escapeHtml(text)}">${escapeHtml(text)}</button>`).join("")}</div>`;
  }

  function renderChat() {
    const data = state.data;
    if (!data) return;
    const stick = state.stick;
    const slots = chatSlots();
    renderEmpty(slots.empty, data);
    syncMessages(slots.list, data.messages);
    syncApprovals(slots.approvals, data.approvals);
    if (state.stream) {
      ensureStream();
      renderTools();
    } else {
      if (slots.stream.firstChild) slots.stream.replaceChildren();
      if (slots.tools.firstChild) slots.tools.replaceChildren();
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
    const context = contextView();
    // Mode and workspace live in exactly one place each: the top segmented
    // control and the rail's workspace row. The composer only adds read-outs.
    const pills = [
      context
        ? `<button data-pill="context" class="gauge ${context.level}" style="--percent: ${context.percent}"` +
          ` title="上下文 ${context.used} / ${context.limit} 字符${context.compressed ? "（历史已压缩）" : ""}">` +
          `<span class="gauge-ring" aria-hidden="true"></span>` +
          `<span class="gauge-text">上下文 ${context.percent}%</span></button>`
        : "",
      data.approvals.length ? `<button data-pill="approval" class="danger-text">待确认 ${data.approvals.length}</button>` : "",
    ];
    el.pills.innerHTML = pills.join("");
    el.pills.hidden = !pills.some(Boolean);
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
    // The drawer always shows full labels; the icon state only exists inline.
    const collapsed = !railAuto && state.railCollapsed;
    el.rail.classList.toggle("collapsed", collapsed);
    el.railToggle.hidden = railAuto;
    el.railToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
    el.railToggle.title = collapsed ? "展开会话栏" : "收起会话栏";
    $(".ico", el.railToggle).textContent = collapsed ? "»" : "«";
    $(".rail-label", el.railToggle).textContent = collapsed ? "展开" : "收起";
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
    const slot = chatSlots().stream;
    if (!slot) return null;
    let row = $("#streamRow", slot);
    if (!row) {
      slot.innerHTML = `<div class="msg ag" id="streamRow">
        <div class="bubble streaming">
          <div class="meta"><span>${escapeHtml(state.data.persona.name)}</span><span></span></div>
          <div class="body"></div>
        </div>
        <span class="tail" aria-hidden="true"></span>
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
    const stack = $("#toolStack", el.chatInner);
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
    if (state.queue.length && state.queueSession !== data.session_id) {
      resetQueue("已切换会话，待发队列已清空。");
    }
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
    if (!state.run && state.queue.length) flushQueue();
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
    if (state.run || state.queue.length) {
      enqueue(text);
      return;
    }
    el.input.value = "";
    resizeInput();
    updateCount();
    await guard(post("/api/turn", { text: text, mode: state.data.mode }));
  }

  function enqueue(text) {
    if (state.queue.length >= QUEUE_LIMIT) {
      toast(`待发队列最多 ${QUEUE_LIMIT} 条，先撤回一条或等本轮结束。`);
      return false;
    }
    state.queueSession = state.data.session_id;
    state.queue.push({ id: ++state.queueSeq, text: text, session: state.queueSession });
    el.input.value = "";
    resizeInput();
    updateCount();
    saveDraft();
    renderQueue();
    toast(
      state.queue.length === 1
        ? "已加入待发队列，本轮结束后自动发送"
        : `已加入待发队列（共 ${state.queue.length} 条）`
    );
    return true;
  }

  function renderQueue() {
    const items = state.queue;
    el.queueDock.hidden = !items.length;
    el.queueCount.textContent = `待发 ${items.length} 条`;
    el.queueList.innerHTML = items
      .map(
        (item) => `<li class="queue-item" data-queue="${item.id}">
          <span class="text" title="${escapeHtml(item.text)}">${escapeHtml(item.text)}</span>
          <button type="button" data-queue-edit="${item.id}" aria-label="编辑这条待发消息">编辑</button>
          <button type="button" data-queue-drop="${item.id}" aria-label="撤回这条待发消息">撤回</button>
        </li>`
      )
      .join("");
  }

  function editQueueItem(id) {
    const index = state.queue.findIndex((item) => item.id === id);
    if (index < 0) return;
    const [item] = state.queue.splice(index, 1);
    const rest = el.input.value;
    el.input.value = item.text + (rest.trim() ? "\n" + rest : "");
    resizeInput();
    updateCount();
    saveDraft();
    renderQueue();
    el.input.focus();
    el.input.selectionStart = el.input.selectionEnd = el.input.value.length;
  }

  function dropQueueItem(id) {
    const before = state.queue.length;
    state.queue = state.queue.filter((item) => item.id !== id);
    if (state.queue.length === before) return;
    renderQueue();
    toast("已撤回待发消息");
  }

  function resetQueue(notice) {
    // The epoch invalidates a flush that is still in flight: its failure must not
    // put the item back into a queue the session switch already emptied.
    state.queueEpoch += 1;
    state.queueSession = state.data ? state.data.session_id : "";
    if (!state.queue.length) return;
    state.queue = [];
    renderQueue();
    if (notice) toast(notice);
  }

  async function flushQueue() {
    if (state.flushing || state.run || !state.queue.length) return;
    const item = state.queue.shift();
    const epoch = state.queueEpoch;
    state.flushing = true;
    renderQueue();
    try {
      const payload = await post("/api/turn", { text: item.text, mode: state.data.mode });
      // The server registers the run before it answers 202, so claiming the run
      // here keeps a bootstrap that raced the POST from flushing a second item.
      if (payload && payload.run_id) beginRun(payload.run_id, "pending");
    } catch (error) {
      if (epoch === state.queueEpoch && item.session === state.data.session_id) {
        state.queue.unshift(item);
        renderQueue();
        toast("待发消息没有发出去：" + (error.message || "请重试"));
      }
    } finally {
      state.flushing = false;
    }
  }

  async function stopRun() {
    if (!state.run) return;
    await guard(post("/api/runs/cancel", { run_id: state.run }));
  }

  async function regenerateMessage(messageId) {
    if (state.run) {
      toast("当前回复结束后才能重新生成");
      return;
    }
    const payload = await guard(
      post("/api/messages/regenerate", { message_id: messageId })
    );
    if (payload && payload.run_id) beginRun(payload.run_id, "pending");
  }

  async function rememberMessage(messageId) {
    const payload = await guard(
      post("/api/console/memory", { action: "remember", message_id: messageId })
    );
    if (!payload) return;
    toast(payload.created ? "已保存为长期记忆" : "这条内容已经在记忆里");
  }

  function openSources() {
    state.tab = "来源";
    state.inspectorOpen = true;
    if (responsiveWidth() < 940) state.inspectorDrawer = true;
    layout();
    renderInspector();
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
    const data = await guard(post("/api/sessions/switch", { session_id: sessionId }));
    if (!data) return;
    resetQueue("已切换会话，待发队列已清空。");
    state.railDrawer = false;
    state.stick = true;
    await refresh();
  }

  async function newSession() {
    const data = await guard(post("/api/sessions/create", { mode: state.data.mode }));
    if (!data) return;
    resetQueue("已新建会话，待发队列已清空。");
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
    el.railToggle.onclick = () => {
      state.railCollapsed = !state.railCollapsed;
      layout();
    };
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
    el.queueList.onclick = (event) => {
      const edit = event.target.closest("[data-queue-edit]");
      if (edit) {
        editQueueItem(Number(edit.dataset.queueEdit));
        return;
      }
      const drop = event.target.closest("[data-queue-drop]");
      if (drop) dropQueueItem(Number(drop.dataset.queueDrop));
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
      // The icon-only rail cannot fit the pick/clear buttons, so a click on the
      // workspace glyph brings the labels back instead of acting blind.
      if (el.rail.classList.contains("collapsed")) {
        state.railCollapsed = false;
        layout();
        return;
      }
      const action = event.target.closest("[data-act]");
      if (!action) return;
      if (action.dataset.act === "pick-ws") chooseWorkspace();
      else clearWorkspace();
    };
    el.chatInner.addEventListener("click", (event) => {
      const action = event.target.closest("[data-msg-act]");
      if (action) {
        const messageId = Number(action.dataset.actMid);
        if (action.dataset.msgAct === "regenerate") regenerateMessage(messageId);
        else if (action.dataset.msgAct === "remember") rememberMessage(messageId);
        else if (action.dataset.msgAct === "source") openSources();
        return;
      }
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
      if (pill.dataset.pill === "approval") {
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
        // The search box is hidden in the icon-only rail; bring it back first.
        if (el.rail.classList.contains("collapsed")) {
          state.railCollapsed = false;
          layout();
        }
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
