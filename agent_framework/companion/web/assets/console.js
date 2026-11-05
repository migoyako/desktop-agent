/* Management console for the Pixso-designed companion UI. */
(function () {
  "use strict";

  const $ = (selector, root) => (root || document).querySelector(selector);
  const token = new URLSearchParams(location.search).get("t") || "";

  const state = {
    page: "memory",
    rows: [],
    memories: [],
    selected: null,
    detail: null,
    data: null,
    host: null,
  };

  const el = {
    tabs: $("#tabs"),
    page: $("#page"),
    hint: $("#hint"),
    note: $("#note"),
    refresh: $("#refresh"),
    refreshAll: $("#refreshAll"),
    closeTop: $("#closeTop"),
    closeBottom: $("#closeBottom"),
    toast: $("#toast"),
    head: $("#winHead"),
  };

  const HINTS = {
    memory: "来源证据、候选、召回与派生关系均可追溯；彻底删除不可撤销。",
    recall: "每次回复前的召回都会记录指纹、命中事实与延迟。",
    candidates: "候选事实尚未确认，确认后才进入长期记忆。",
    audit: "每条写入回执都保留目标、状态与撤销能力。",
    affect: "情绪只是估计；你的纠正会立即覆盖估计值。",
    failures: "失败与错过集中在这里，便于复盘但不阻塞对话。",
    settings: "数据只保存在本机；主题与字号会立即生效。",
  };

  const KIND_LABELS = {
    preference: "偏好",
    fact: "事实",
    episode: "事件",
    persona: "人设",
    profile: "档案",
  };

  const STATUS_LABELS = {
    confirmed: "active",
    active: "active",
    candidate: "candidate",
    superseded: "superseded",
    forgotten: "forgotten",
  };

  const MOODS = [
    { value: "neutral", label: "平静" },
    { value: "positive", label: "开心" },
    { value: "low", label: "低落" },
    { value: "tired", label: "疲惫" },
  ];

  function escapeHtml(text) {
    return String(text == null ? "" : text).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function clip(text, limit) {
    const flat = String(text || "").replace(/\s+/g, " ").trim();
    return flat.length <= limit ? flat : flat.slice(0, limit - 1) + "…";
  }

  function stamp(value) {
    const text = String(value || "");
    return text ? text.replace("T", " ").slice(0, 16) : "";
  }

  function statusLabel(value) {
    return STATUS_LABELS[value] || value || "";
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

  function applyTheme(ui) {
    document.documentElement.dataset.theme = ui.theme || "charcoal-pink";
    document.documentElement.style.setProperty("--scale", String((ui.font_scale || 100) / 100));
  }

  function table(headers, rows, rowRenderer) {
    if (!rows.length) return '<div class="empty">暂时没有记录</div>';
    return `<div class="table-card"><table class="table">
      <thead><tr>${headers.map((item) => `<th>${item}</th>`).join("")}</tr></thead>
      <tbody>${rows.map(rowRenderer).join("")}</tbody>
    </table></div>`;
  }

  // ---------- pages ----------

  function renderMemory(rows) {
    state.rows = rows;
    if (!state.selected && rows.length) state.selected = rows[0].memory_id;
    el.page.innerHTML =
      table(
        ["ID", "类型", "内容", "状态", "保护"],
        rows,
        (row) => `<tr data-memory="${escapeHtml(row.memory_id)}" tabindex="0" class="${row.memory_id === state.selected ? "on" : ""}">
          <td class="id">${escapeHtml(shortId(row.memory_id))}</td>
          <td>${escapeHtml(KIND_LABELS[row.kind] || row.kind)}</td>
          <td class="clip" title="${escapeHtml(row.content)}">${escapeHtml(clip(row.content, 78))}</td>
          <td>${escapeHtml(statusLabel(row.status))}</td>
          <td>${row.protected ? "是" : "否"}</td>
        </tr>`
      ) +
      `<div class="detail" id="memoryDetail"><div class="empty">选择一条记忆查看证据</div></div>
      <div class="actions-row">
        <button class="ghost" data-memory-act="correct">纠正</button>
        <button class="ghost" data-memory-act="forget">忘记</button>
        <button class="ghost" data-memory-act="protect">切换保护</button>
        <button class="ghost danger-text" data-memory-act="purge">彻底删除</button>
      </div>`;
    if (state.selected) loadDetails(state.selected);
  }

  function shortId(value) {
    const text = String(value || "");
    return text.length <= 10 ? text : text.slice(0, 8);
  }

  async function loadDetails(memoryId) {
    const detail = await guard(api("/api/console/memory?id=" + encodeURIComponent(memoryId)));
    if (!detail) return;
    state.detail = detail;
    const memory = detail.memory || {};
    const evidence = detail.evidence || [];
    const recalls = detail.recalls || [];
    const candidates = detail.candidates || [];
    const derived = detail.derived || [];
    const box = $("#memoryDetail");
    if (!box) return;
    box.innerHTML = `<h2>${escapeHtml(memory.content || "（无内容）")}</h2>
      <div class="meta">
        ID：${escapeHtml(memory.memory_id || "")}　类型：${escapeHtml(KIND_LABELS[memory.kind] || memory.kind || "")}　状态：${escapeHtml(statusLabel(memory.status))}　保护：${memory.protected ? "是" : "否"}<br>
        规范键：${escapeHtml(memory.canonical_key || "—")}　证据：${memory.evidence_count || evidence.length} 条　重要度：${Number(memory.importance || 0).toFixed(2)}　置信度：${Number(memory.confidence || 0).toFixed(2)}
      </div>
      <div class="group-title">来源证据</div>
      ${
        evidence.length
          ? evidence
              .map(
                (item) => `<div class="evidence">
                  <div class="who">消息 #${item.message_id}（${escapeHtml(item.source_role || "")}） · ${escapeHtml(stamp(item.created_at))}</div>
                  <div class="quote">${escapeHtml(clip(item.quote || item.message_content, 160))}</div>
                </div>`
              )
              .join("")
          : '<div class="meta">没有来源证据。</div>'
      }
      <div class="group-title">最近召回</div>
      ${
        recalls.length
          ? recalls
              .map(
                (item) => `<div class="meta">${escapeHtml(stamp(item.created_at))} · fts · 评分 ${Number(item.score || 0).toFixed(3)} · ${item.used_in_reply ? "已采用" : "未采用"}</div>`
              )
              .join("")
          : '<div class="meta">还没有召回记录。</div>'
      }
      ${derived.length ? `<div class="group-title">派生记忆</div><div class="meta">${derived.map(shortId).join("、")}</div>` : ""}
      ${candidates.length ? `<div class="group-title">候选</div><div class="meta">${candidates.length} 条待确认</div>` : ""}`;
  }

  function renderRecall(rows) {
    el.page.innerHTML = table(
      ["时间", "查询指纹", "命中事实", "延迟", "采用"],
      rows,
      (row) => {
        let facts = [];
        try {
          facts = JSON.parse(row.fact_ids || "[]");
        } catch (error) {
          facts = [];
        }
        return `<tr>
          <td>${escapeHtml(stamp(row.created_at))}</td>
          <td class="id">${escapeHtml(shortId(row.query_hash))}</td>
          <td>${facts.length} 条</td>
          <td>${Number(row.latency_ms || 0).toFixed(0)} ms</td>
          <td>${row.used_in_reply ? "已采用" : "未采用"}</td>
        </tr>`;
      }
    );
  }

  function renderCandidates(rows) {
    const candidates = rows.filter((row) => row.status === "candidate");
    el.page.innerHTML =
      table(
        ["ID", "类型", "内容", "置信度", "更新时间"],
        candidates,
        (row) => `<tr>
          <td class="id">${escapeHtml(shortId(row.memory_id))}</td>
          <td>${escapeHtml(KIND_LABELS[row.kind] || row.kind)}</td>
          <td class="clip" title="${escapeHtml(row.content)}">${escapeHtml(clip(row.content, 78))}</td>
          <td>${Number(row.confidence || 0).toFixed(2)}</td>
          <td>${escapeHtml(stamp(row.updated_at))}</td>
        </tr>`
      ) + '<div class="meta" style="color:var(--muted);font-size:var(--fs-caption)">候选由对话自动提取，确认后进入长期记忆。</div>';
  }

  function renderAudit(rows) {
    el.page.innerHTML = table(
      ["时间", "回执", "状态", "摘要", "尝试"],
      rows,
      (row) => `<tr>
        <td>${escapeHtml(stamp(row.created_at))}</td>
        <td class="id">${escapeHtml(shortId(row.receipt_id))}</td>
        <td>${escapeHtml(row.status || "")}</td>
        <td class="clip" title="${escapeHtml(row.summary)}">${escapeHtml(clip(row.summary, 70))}</td>
        <td>${row.attempts || 0}</td>
      </tr>`
    );
  }

  function moodCard(title, item) {
    const bar = (label, value, max) => `<span>${label}</span><span class="bar"><i style="width:${Math.max(0, Math.min(100, (Number(value || 0) / max) * 100))}%"></i></span>`;
    return `<div class="mood-card">
      <div class="who">${title}</div>
      <div class="mood">${escapeHtml(item.mood_text || item.mood_label || "平静")}</div>
      <div class="bars">
        ${bar("效价", Number(item.valence || 0) + 1, 2)}
        ${bar("唤醒", item.arousal, 1)}
        ${bar("精力", item.energy, 1)}
        ${bar("置信度", item.confidence, 1)}
      </div>
      <div class="meta" style="color:var(--muted);font-size:var(--fs-caption);margin-top:6px">来源 ${escapeHtml(item.source || "")} · ${escapeHtml(stamp(item.created_at))}</div>
    </div>`;
  }

  function renderAffect(data) {
    const snapshot = data.snapshot || {};
    const activity = data.activity || {};
    el.page.innerHTML = `<div class="mood-grid">
        ${moodCard("你的状态", snapshot.user || {})}
        ${moodCard("角色状态", snapshot.agent || {})}
      </div>
      <div class="detail">
        <h2>活动状态</h2>
        <div class="meta">${escapeHtml(activity.label || "未知")} · 空闲 ${escapeHtml(String(activity.idle_seconds == null ? "未知" : activity.idle_seconds + " 秒"))} · ${activity.available === false ? "不可用" : "可用"}</div>
        <div class="form-row">
          <label>纠正你的情绪</label>
          <select class="field" id="moodSelect">${MOODS.map((mood) => `<option value="${mood.value}" ${mood.value === (snapshot.user || {}).mood_label ? "selected" : ""}>${mood.label}</option>`).join("")}</select>
          <button class="ghost" id="saveMood">保存</button>
        </div>
      </div>`;
  }

  function renderFailures(data) {
    const receipts = data.receipts || [];
    const proactive = data.proactive || [];
    const skills = data.skills || [];
    el.page.innerHTML = `<div class="detail"><h2>执行失败（${receipts.length}）</h2>
      ${receipts.length ? receipts.map((item) => `<div class="meta">${escapeHtml(stamp(item.created_at))} · ${escapeHtml(item.status)} · ${escapeHtml(clip(item.summary, 90))}</div>`).join("") : '<div class="meta">没有失败的写入。</div>'}
      </div>
      <div class="detail"><h2>主动错过（${proactive.length}）</h2>
      ${proactive.length ? proactive.map((item) => `<div class="meta">${escapeHtml(stamp(item.created_at))} · ${escapeHtml(item.trigger_reason || "")} · ${escapeHtml(clip(item.content, 90))}</div>`).join("") : '<div class="meta">没有错过的主动消息。</div>'}
      </div>
      <div class="detail"><h2>技能问题（${skills.length}）</h2>
      ${skills.length ? skills.map((item) => `<div class="meta">${escapeHtml(typeof item === "string" ? item : JSON.stringify(item))}</div>`).join("") : '<div class="meta">技能校验通过。</div>'}
      </div>`;
  }

  function renderSettings(data, bootstrap) {
    const persona = data.persona || {};
    const skills = data.skills || {};
    const proactive = data.proactive || {};
    const available = skills.available || [];
    const avatar = ((bootstrap || state.data || {}).assets || {}).avatar;
    const avatarHtml = avatar
      ? `<img class="persona-avatar" src="${avatar.url}?t=${encodeURIComponent(token)}&v=${avatar.stamp}" alt="当前人设头像" width="48" height="48">`
      : "";
    el.page.innerHTML = `<div class="stats">
        <div class="stat"><div class="k">工作区</div><div class="v">${escapeHtml(data.workspace || "未授权")}</div></div>
        <div class="stat"><div class="k">模式</div><div class="v">${data.mode === "work" ? "工作" : "陪伴"}</div></div>
        <div class="stat"><div class="k">会话</div><div class="v">${data.sessions || 0} 个</div></div>
        <div class="stat"><div class="k">记忆</div><div class="v">${data.memories || 0} 条</div></div>
      </div>
      <div class="detail"><h2>人设与主题</h2>
        <div class="persona-head">${avatarHtml}
          <div class="meta">人设：${escapeHtml(persona.name || "数字分身")} ${escapeHtml(persona.version || "")} · 主题：${data.ui.theme === "pearl-white" ? "珍珠白" : "经典深色"} · 字号：${data.ui.font_scale}%</div>
        </div>
        <div class="actions-row">
          <button class="ghost" id="openSettings">打开设置</button>
        </div>
      </div>
      <div class="detail"><h2>主动陪伴</h2>
        <div class="meta">开关：${proactive.enabled ? "开启" : "关闭"} · 今日 ${proactive.daily_count || 0} / ${proactive.daily_limit || 0} · 未回复 ${proactive.unanswered || 0} / ${proactive.max_unanswered || 0} · 活动 ${escapeHtml(proactive.activity_label || "未知")}</div>
      </div>
      <div class="detail"><h2>技能（${available.length}）</h2>
        ${available.length ? available.map((item) => `<div class="meta">· ${escapeHtml(item.name || item.id || "")}</div>`).join("") : '<div class="meta">没有可用技能。</div>'}
        ${(skills.issues || []).length ? `<div class="meta danger-text">校验问题 ${(skills.issues || []).length} 个</div>` : ""}
      </div>`;
  }

  // ---------- loading ----------

  async function loadPage(page) {
    state.page = page;
    el.hint.textContent = HINTS[page] || "";
    for (const button of el.tabs.querySelectorAll("button")) {
      const active = button.dataset.page === page;
      button.classList.toggle("on", active);
      button.setAttribute("aria-selected", active ? "true" : "false");
    }
    if (page === "memory" || page === "candidates") {
      const data = await guard(api("/api/console/memory"));
      if (!data) return;
      state.memories = data.rows || [];
      if (page === "memory") renderMemory(state.memories);
      else renderCandidates(state.memories);
      return;
    }
    if (page === "recall") {
      const data = await guard(api("/api/console/recall"));
      if (data) renderRecall(data.rows || []);
      return;
    }
    if (page === "audit") {
      const data = await guard(api("/api/console/audit"));
      if (data) renderAudit(data.rows || []);
      return;
    }
    if (page === "affect") {
      const data = await guard(api("/api/console/affect"));
      if (data) renderAffect(data);
      return;
    }
    if (page === "failures") {
      const data = await guard(api("/api/console/failures"));
      if (data) renderFailures(data);
      return;
    }
    if (page === "settings") {
      const data = await guard(api("/api/console/settings"));
      if (data) renderSettings(data, state.data);
    }
  }

  // ---------- actions ----------

  async function memoryAction(action) {
    const memoryId = state.selected;
    if (!memoryId) {
      toast("先选择一条记忆");
      return;
    }
    if (action === "correct") {
      const current = state.detail && state.detail.memory ? state.detail.memory.content : "";
      const replacement = await CompanionDialog.prompt("纠正后的内容", current, {
        title: "纠正记忆",
        okText: "保存",
      });
      if (!replacement) return;
      await guard(post("/api/console/memory", { action: "correct", memory_id: memoryId, replacement: replacement }));
    } else if (action === "forget") {
      const confirmed = await CompanionDialog.confirm("忘记这条记忆？", {
        title: "忘记记忆",
        okText: "忘记",
        danger: true,
      });
      if (!confirmed) return;
      await guard(post("/api/console/memory", { action: "forget", memory_id: memoryId }));
    } else if (action === "protect") {
      const memory = state.detail && state.detail.memory ? state.detail.memory : {};
      await guard(post("/api/console/memory", { action: "protect", memory_id: memoryId, protected: !memory.protected }));
    } else if (action === "purge") {
      const confirmed = await CompanionDialog.confirm("彻底删除不可撤销，继续？", {
        title: "彻底删除",
        okText: "彻底删除",
        danger: true,
      });
      if (!confirmed) return;
      await guard(post("/api/console/memory", { action: "purge", memory_id: memoryId }));
    }
    state.selected = null;
    state.detail = null;
    await loadPage(state.page);
  }

  // ---------- chrome ----------

  function bind() {
    el.tabs.onclick = (event) => {
      const button = event.target.closest("button[data-page]");
      if (!button) return;
      state.selected = null;
      state.detail = null;
      loadPage(button.dataset.page);
    };
    el.page.onkeydown = (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      const row = event.target.closest("tr[data-memory]");
      if (!row) return;
      event.preventDefault();
      row.click();
    };
    el.page.onclick = async (event) => {
      const row = event.target.closest("tr[data-memory]");
      if (row) {
        state.selected = row.dataset.memory;
        for (const item of el.page.querySelectorAll("tr[data-memory]")) {
          item.classList.toggle("on", item.dataset.memory === state.selected);
        }
        await loadDetails(state.selected);
        return;
      }
      const action = event.target.closest("[data-memory-act]");
      if (action) {
        await memoryAction(action.dataset.memoryAct);
        return;
      }
      if (event.target.closest("#saveMood")) {
        const mood = $("#moodSelect").value;
        await guard(post("/api/console/affect", { mood: mood }));
        const label = (MOODS.find((item) => item.value === mood) || {}).label || mood;
        toast("已纠正为 " + label);
        await loadPage("affect");
        return;
      }
      if (event.target.closest("#openSettings")) {
        if (state.host) state.host.openSettings();
        else window.open("/settings?t=" + encodeURIComponent(token), "_blank");
      }
    };
    el.refresh.onclick = () => loadPage(state.page);
    el.refreshAll.onclick = async () => {
      await loadPage(state.page);
      toast("已刷新");
    };
    const close = () => {
      if (state.host) state.host.hide();
      else window.location.href = "/?t=" + encodeURIComponent(token);
    };
    el.closeTop.onclick = close;
    el.closeBottom.onclick = close;
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
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
    const bootstrap = await guard(api("/api/bootstrap"));
    if (bootstrap) {
      state.data = bootstrap;
      applyTheme(bootstrap.ui);
      CompanionBackground.apply(bootstrap.ui, bootstrap.assets, token);
    }
    bind();
    connectHost();
    await loadPage("memory");
  }

  start();
})();
