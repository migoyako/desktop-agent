/* Desktop overlay: avatar, status bubble, proactive queue, menu and drag. */
(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const token = new URLSearchParams(location.search).get("t") || "";
  const BUBBLE_MS = 9000;
  const POLL_MS = 8000;
  const QUEUE_LIMIT = 3;

  const state = {
    host: null,
    last: null,
    live: false,
    mood: "",
    collapsed: false,
    queue: [],
    // Ids already shown once: a refresh that lands before the read callback
    // must not re-queue the same message.
    seen: new Set(),
    current: null,
    kind: "",
    timer: 0,
    refreshTimer: 0,
    suppressClick: false,
  };

  const el = {
    overlay: $("#overlay"),
    pet: $("#pet"),
    ball: $("#ball"),
    bubble: $("#bubble"),
    bubbleText: $("#bubbleText"),
    chip: $("#chip"),
    status: $("#status"),
    close: $("#close"),
    quickDnd: $("#quickDnd"),
    quickCollapse: $("#quickCollapse"),
    menu: $("#menu"),
    menuDnd: $("#menuDnd"),
  };

  async function api(path, options) {
    const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token);
    const settings = options || {};
    const response = await fetch(url, {
      ...settings,
      headers: {
        "X-Companion-Token": token,
        "Content-Type": "application/json",
        ...(settings.headers || {}),
      },
    });
    if (!response.ok) throw new Error("unavailable");
    return response.json();
  }

  // -- appearance -----------------------------------------------------

  function applyCollapsed(collapsed, persist) {
    state.collapsed = Boolean(collapsed);
    document.body.classList.toggle("collapsed", state.collapsed);
    el.pet.hidden = state.collapsed;
    el.ball.hidden = !state.collapsed;
    el.status.hidden = state.collapsed;
    el.quickCollapse.textContent = state.collapsed ? "展开" : "折叠";
    const label = state.collapsed ? "展开形象" : "折叠成小球";
    el.quickCollapse.title = label;
    el.quickCollapse.setAttribute("aria-label", label);
    if (persist) {
      api("/api/ui", {
        method: "POST",
        body: JSON.stringify({ overlay_collapsed: state.collapsed }),
      }).catch(() => {});
    }
  }

  function apply(data) {
    state.last = data;
    document.documentElement.dataset.theme = data.theme || "charcoal-pink";
    document.documentElement.style.setProperty(
      "--scale",
      String((data.font_scale || 100) / 100)
    );
    const mood = data.mood || "";
    if (mood !== state.mood) {
      state.mood = mood;
      CompanionAvatar.mount(el.pet, 132, mood);
    }
    applyCollapsed(Boolean(data.collapsed), false);
    el.status.textContent = (data.status && data.status.label) || "待机";
    applyProactive(data.proactive || { enabled: false, unread: [] });
    renderBubble();
    renderMenuState();
  }

  async function refresh() {
    let data;
    try {
      data = await api("/api/overlay");
    } catch (error) {
      return;
    }
    apply(data);
  }

  function scheduleRefresh() {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(refresh, 140);
  }

  // -- proactive bubble queue -----------------------------------------

  function applyProactive(proactive) {
    if (proactive.enabled === false) {
      state.queue = [];
      state.current = null;
      clearTimer();
      return;
    }
    const events = [...(proactive.unread || [])].reverse();
    for (const event of events) {
      if (!event || !event.id || state.seen.has(event.id)) continue;
      if (state.queue.some((item) => item.id === event.id)) continue;
      state.queue.push(event);
      state.seen.add(event.id);
    }
    while (state.queue.length > QUEUE_LIMIT) state.queue.shift();
  }

  function clearTimer() {
    clearTimeout(state.timer);
    state.timer = 0;
  }

  function showBubble(chip, text, kind) {
    el.chip.textContent = chip;
    el.bubbleText.textContent = text;
    el.bubble.classList.add("show");
    el.bubble.dataset.kind = kind;
    state.kind = kind;
  }

  function hideBubble() {
    el.bubble.classList.remove("show");
    el.bubble.dataset.kind = "";
    state.kind = "";
  }

  function next() {
    const item = state.queue.shift();
    if (!item) return;
    state.current = item;
    showBubble("关心", item.text, "proactive");
    clearTimer();
    state.timer = setTimeout(acknowledge, BUBBLE_MS);
  }

  function acknowledge() {
    const item = state.current;
    state.current = null;
    clearTimer();
    if (item && item.id) {
      // A displayed bubble counts as delivered even when the user did not
      // click it; otherwise it would re-queue on every refresh.
      api("/api/proactive", {
        method: "POST",
        body: JSON.stringify({ action: "read", event_id: item.id }),
      }).catch(() => {});
    }
    renderBubble();
  }

  function renderBubble() {
    const status = (state.last && state.last.status) || {};
    const detail = (status.detail || "").trim();
    if (detail) {
      clearTimer();
      showBubble(status.label || "提示", detail, "status");
      return;
    }
    if (state.current) {
      showBubble("关心", state.current.text, "proactive");
      if (!state.timer) state.timer = setTimeout(acknowledge, BUBBLE_MS);
      return;
    }
    if (state.queue.length) {
      next();
      return;
    }
    hideBubble();
  }

  // -- menu and quick actions -----------------------------------------

  function dndEnabled() {
    return Boolean(state.last && state.last.proactive && state.last.proactive.enabled);
  }

  function renderMenuState() {
    const enabled = dndEnabled();
    el.quickDnd.classList.toggle("on", !enabled);
    el.quickDnd.setAttribute("aria-pressed", enabled ? "false" : "true");
    el.quickDnd.title = enabled ? "勿扰" : "勿扰中，点击恢复";
    el.menuDnd.textContent = enabled ? "勿扰：关" : "勿扰：开";
  }

  async function toggleDnd() {
    const enabled = dndEnabled();
    try {
      await api("/api/proactive", {
        method: "POST",
        body: JSON.stringify({ action: "settings", enabled: !enabled }),
      });
    } catch (error) {
      return;
    }
    await refresh();
  }

  function openMain() {
    if (state.host) state.host.openMain();
    else window.open("/?t=" + encodeURIComponent(token), "_blank");
  }

  function hideOverlay() {
    if (state.host) state.host.hideOverlay();
    else window.location.href = "/?t=" + encodeURIComponent(token);
  }

  function openMenu(x, y) {
    el.menu.hidden = false;
    el.menu.style.left = "0px";
    el.menu.style.top = "0px";
    const box = el.menu.getBoundingClientRect();
    const left = Math.min(Math.max(0, x), Math.max(0, window.innerWidth - box.width));
    const top = Math.min(Math.max(0, y), Math.max(0, window.innerHeight - box.height));
    el.menu.style.left = left + "px";
    el.menu.style.top = top + "px";
    renderMenuState();
  }

  function closeMenu() {
    el.menu.hidden = true;
  }

  function handleMenu(action) {
    if (action === "chat") openMain();
    else if (action === "toggle-main") {
      if (state.host) state.host.toggleMain();
      else openMain();
    } else if (action === "dnd") toggleDnd();
    else if (action === "hide") hideOverlay();
  }

  // -- wiring ---------------------------------------------------------

  function bind() {
    let grab = null;
    const startDrag = (event) => {
      if (!state.host || event.button !== 0) return;
      grab = {
        x: event.screenX - event.clientX,
        y: event.screenY - event.clientY,
        sx: event.screenX,
        sy: event.screenY,
        moved: false,
      };
      event.preventDefault();
    };
    const move = (event) => {
      if (!grab || !state.host) return;
      if (
        Math.abs(event.screenX - grab.sx) > 3 ||
        Math.abs(event.screenY - grab.sy) > 3
      ) {
        grab.moved = true;
      }
      state.host.moveOverlay(event.screenX - grab.x, event.screenY - grab.y);
    };
    const stop = () => {
      if (!grab) return;
      if (grab.moved && state.host) {
        state.suppressClick = true;
        setTimeout(() => {
          state.suppressClick = false;
        }, 0);
        state.host.snapOverlay();
      }
      grab = null;
    };
    el.pet.addEventListener("mousedown", startDrag);
    el.ball.addEventListener("mousedown", startDrag);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);

    el.pet.addEventListener("dblclick", openMain);
    el.bubble.addEventListener("click", () => {
      if (state.kind === "proactive") acknowledge();
      openMain();
    });
    el.close.addEventListener("click", (event) => {
      event.stopPropagation();
      hideOverlay();
    });

    let ballClick = 0;
    el.ball.addEventListener("click", () => {
      if (state.suppressClick) return;
      // Give a following dblclick a chance to expand instead of opening chat.
      clearTimeout(ballClick);
      ballClick = setTimeout(openMain, 260);
    });
    el.ball.addEventListener("dblclick", () => {
      clearTimeout(ballClick);
      applyCollapsed(false, true);
    });

    document.addEventListener("click", (event) => {
      const quick = event.target.closest("[data-quick]");
      if (quick) {
        if (quick.dataset.quick === "chat") openMain();
        else if (quick.dataset.quick === "dnd") toggleDnd();
        else if (quick.dataset.quick === "collapse") {
          applyCollapsed(!state.collapsed, true);
        }
        return;
      }
      if (!el.menu.hidden && !el.menu.contains(event.target)) closeMenu();
    });

    el.menu.addEventListener("click", (event) => {
      const item = event.target.closest("[data-menu]");
      if (!item) return;
      closeMenu();
      handleMenu(item.dataset.menu);
    });

    el.overlay.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openMenu(event.clientX, event.clientY);
    });
    window.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenu();
    });
    window.addEventListener("blur", closeMenu);
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

  function connectEvents() {
    if (typeof EventSource === "undefined") return;
    const source = new EventSource("/api/events?t=" + encodeURIComponent(token));
    source.onopen = () => {
      state.live = true;
      refresh();
    };
    source.onerror = () => {
      state.live = false;
    };
    source.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (error) {
        return;
      }
      if (payload.type === "refresh" || payload.type === "ui") scheduleRefresh();
    };
  }

  bind();
  connectHost();
  connectEvents();
  refresh();
  // Fallback only: while the event stream is connected the overlay is pushed.
  setInterval(() => {
    if (!state.live) refresh();
  }, POLL_MS);
})();
