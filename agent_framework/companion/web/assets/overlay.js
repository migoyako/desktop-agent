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
    // Gesture ids already dispatched to the stage, for the same reason.
    motionSeen: new Set(),
    current: null,
    kind: "",
    timer: 0,
    refreshTimer: 0,
    suppressClick: false,
    stage: null,
    stagePending: false,
    stageToken: 0,
    stageSource: "",
    stageScale: 0,
    resizeFrame: 0,
    stageFailed: false,
    petAvailable: false,
    desired: { mood: "", reduceMotion: false },
    reported: null,
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
    el.pet.hidden = state.collapsed || !state.petAvailable;
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
    if (!state.collapsed) resizeStage();
  }

  function apply(data) {
    state.last = data;
    document.documentElement.dataset.theme = data.theme || "charcoal-pink";
    document.documentElement.style.setProperty(
      "--scale",
      String((data.font_scale || 100) / 100)
    );
    document.body.classList.toggle("reduce-motion", Boolean(data.reduce_motion));
    const mood = data.mood || "";
    state.mood = mood;
    applyStage(data);
    applyCollapsed(Boolean(data.collapsed), false);
    el.status.textContent = (data.status && data.status.label) || "待机";
    applyProactive(data.proactive || { enabled: false, unread: [] });
    applyMotions(data.motion_events || []);
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

  // -- content-driven gestures -----------------------------------------

  function applyMotions(events) {
    if (!state.stage) return;
    for (const event of events || []) {
      if (!event || !event.id || !event.name) continue;
      if (state.motionSeen.has(event.id)) continue;
      state.motionSeen.add(event.id);
      state.stage.setMotion(event.name);
      // Dispatching counts as delivered: a gesture replayed after a page
      // reload would be a surprise motion with no message behind it.
      api("/api/overlay/motion", {
        method: "POST",
        body: JSON.stringify({ event_id: event.id }),
      }).catch(() => {});
    }
  }

  // -- pet stage (Live2D or .mavatar) ----------------------------------

  function reportRuntime(ok, reason) {
    const signature = (ok ? "ok:" : "fail:") + (reason || "");
    if (state.reported === signature) return;
    state.reported = signature;
    api("/api/overlay/runtime", {
      method: "POST",
      body: JSON.stringify({ ok: ok, reason: reason || "" }),
    }).catch(() => {
      state.reported = null;
    });
  }

  function teardownStage() {
    state.stageToken += 1;
    if (state.stage) {
      state.stage.destroy();
      state.stage = null;
    }
    state.stagePending = false;
    state.stageSource = "";
    state.stageScale = 0;
  }

  function resizeStage() {
    cancelAnimationFrame(state.resizeFrame);
    state.resizeFrame = requestAnimationFrame(() => {
      state.resizeFrame = 0;
      if (!state.stage || el.pet.hidden) return;
      if (!el.pet.clientWidth || !el.pet.clientHeight) return;
      state.stage.resize();
    });
  }

  function stageModule(renderer) {
    if (renderer === "live2d") return window.CompanionStage;
    if (renderer === "mesh") return window.CompanionMeshStage;
    return undefined;
  }

  function applyStage(data) {
    const pet = data.pet || {};
    const renderer = String(data.renderer || pet.renderer || "");
    const url =
      renderer === "live2d"
        ? String(pet.model_url || "")
        : renderer === "mesh"
        ? String(pet.url || "")
        : "";
    const source = renderer + "|" + url;
    if (source !== state.stageSource) {
      teardownStage();
      state.stageFailed = false;
      state.stageSource = source;
    }
    const available = Boolean(pet.available) && Boolean(url) && !state.stageFailed;
    state.petAvailable = available;
    if (!available) {
      if (state.stage) {
        state.stage.destroy();
        state.stage = null;
      }
      return;
    }
    state.desired.mood = data.mood || "";
    state.desired.reduceMotion = Boolean(data.reduce_motion);
    const scale = Number(data.font_scale || 100) / 100;
    if (state.stage) {
      state.stage.setMood(state.desired.mood);
      state.stage.setReduceMotion(state.desired.reduceMotion);
      if (state.stageScale !== scale) {
        state.stageScale = scale;
        state.stage.resize();
      }
      return;
    }
    if (state.stagePending) return;
    const module = stageModule(renderer);
    if (!module) {
      state.stageFailed = true;
      state.petAvailable = false;
      reportRuntime(
        false,
        renderer === "live2d" ? "缺少 Live2D 前端模块。" : "缺少 mesh 前端模块。"
      );
      return;
    }
    const missing = module.unavailableReason();
    if (missing) {
      state.stageFailed = true;
      state.petAvailable = false;
      reportRuntime(false, missing);
      return;
    }
    state.stagePending = true;
    state.stageScale = scale;
    const generation = ++state.stageToken;
    const options = {
      mood: state.desired.mood,
      expressions: pet.expressions || [],
      motions: pet.motions || [],
      motionGroups: pet.motion_groups || {},
      reduceMotion: state.desired.reduceMotion,
      onError: (message) => {
        if (generation !== state.stageToken) return;
        teardownStage();
        state.stageFailed = true;
        state.petAvailable = false;
        el.pet.hidden = true;
        reportRuntime(false, message);
      },
    };
    if (renderer === "live2d") options.modelUrl = url;
    else {
      options.archiveUrl = url;
      options.token = token;
    }
    module
      .mount(el.pet, options)
      .then((stage) => {
        if (generation !== state.stageToken) {
          stage.destroy();
          return;
        }
        state.stagePending = false;
        state.stage = stage;
        stage.setMood(state.desired.mood);
        stage.setReduceMotion(state.desired.reduceMotion);
        reportRuntime(true, "");
      })
      .catch((error) => {
        if (generation !== state.stageToken) return;
        state.stagePending = false;
        state.stageFailed = true;
        state.petAvailable = false;
        el.pet.hidden = true;
        reportRuntime(
          false,
          (error && error.message) || (renderer === "live2d" ? "Live2D 加载失败。" : "mesh 加载失败。")
        );
      });
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

  // -- native click-through hit test -----------------------------------

  // The shell polls this with window-local CSS pixels; false means the
  // window keeps WS_EX_TRANSPARENT so the desktop behind it stays usable.
  // Hidden controls are out of the hit region because [hidden] and the
  // hover-gated pointer-events rules make elementFromPoint skip them.
  window.__companionHit = function (x, y) {
    if (document.body.classList.contains("browser")) return false;
    const target = document.elementFromPoint(x, y);
    if (!target) return false;
    return Boolean(target.closest("#pet, #ball, #bubble, #quick, #menu, #close"));
  };

  // Same window-local CSS pixels, fed on the same poll: the eyes and head turn
  // toward the cursor. Collapsed or motion-reduced states have nothing to
  // animate, so the round trip stays free.
  window.__companionCursor = function (x, y) {
    if (!state.stage || state.collapsed) return false;
    if (state.last && state.last.reduce_motion) return false;
    const box = el.pet.getBoundingClientRect();
    if (!box.width || !box.height) return false;
    const halfWidth = Math.max(40, box.width / 2);
    const halfHeight = Math.max(40, box.height / 2);
    state.stage.setFocus(
      (x - (box.left + box.width / 2)) / halfWidth,
      (y - (box.top + box.height / 2)) / halfHeight
    );
    return true;
  };

  // -- wiring ---------------------------------------------------------

  function bind() {
    let grab = null;
    const startDrag = (event) => {
      if (!state.host || event.button !== 0) return;
      grab = {
        // Offset of the pointer inside the window, not the window origin:
        // screenX - clientX would translate the window by its own position
        // on the first move and make it jump.
        x: event.clientX,
        y: event.clientY,
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
  window.addEventListener("resize", resizeStage);
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(resizeStage).observe(el.pet);
  }
  window.addEventListener("beforeunload", () => {
    if (state.stage) state.stage.destroy();
    state.stage = null;
  });
  refresh();
  // Fallback only: while the event stream is connected the overlay is pushed.
  setInterval(() => {
    if (!state.live) refresh();
  }, POLL_MS);
})();
