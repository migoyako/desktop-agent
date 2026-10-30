/* Desktop overlay: avatar, status bubble, drag and open-main. */
(function () {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const token = new URLSearchParams(location.search).get("t") || "";

  const state = { host: null, last: null };

  const el = {
    overlay: $("#overlay"),
    pet: $("#pet"),
    bubble: $("#bubble"),
    bubbleText: $("#bubbleText"),
    chip: $("#chip"),
    status: $("#status"),
    close: $("#close"),
  };

  function avatarSvg(size, mood) {
    const warm = ["warm", "positive", "happy"].includes(mood);
    const low = ["low", "tired", "concerned"].includes(mood);
    const eyeY = low ? 55 : 51;
    const mouth = warm
      ? '<path d="M64 64 q11 12 22 0" fill="none" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>'
      : low
      ? '<path d="M66 69 h18" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>'
      : '<path d="M65 64 q10 8 20 0" fill="none" stroke="var(--avatar-ink)" stroke-width="3" stroke-linecap="round"/>';
    return `<svg width="${size}" height="${size}" viewBox="0 0 150 150" aria-hidden="true">
      <ellipse cx="75" cy="126" rx="44" ry="10" fill="rgba(0,0,0,.28)"/>
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

  async function api(path) {
    const url = path + (path.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(token);
    const response = await fetch(url, { headers: { "X-Companion-Token": token } });
    if (!response.ok) throw new Error("unavailable");
    return response.json();
  }

  async function refresh() {
    let data;
    try {
      data = await api("/api/overlay");
    } catch (error) {
      return;
    }
    state.last = data;
    document.documentElement.dataset.theme = data.theme || "charcoal-pink";
    document.documentElement.style.setProperty(
      "--scale",
      String((data.font_scale || 100) / 100)
    );
    el.pet.innerHTML = avatarSvg(132, data.mood);
    el.status.textContent = data.status.label || "待机";
    const detail = data.status.detail || "";
    if (detail) {
      el.chip.textContent = data.status.label || "提示";
      el.bubbleText.textContent = detail;
      el.bubble.classList.add("show");
    } else {
      el.bubble.classList.remove("show");
    }
  }

  function bind() {
    let grab = null;
    const startDrag = (event) => {
      if (!state.host || event.button !== 0) return;
      grab = { x: event.screenX - event.clientX, y: event.screenY - event.clientY };
      event.preventDefault();
    };
    const move = (event) => {
      if (!grab || !state.host) return;
      state.host.moveOverlay(event.screenX - grab.x, event.screenY - grab.y);
    };
    const stop = () => {
      grab = null;
    };
    el.pet.addEventListener("mousedown", startDrag);
    el.bubble.addEventListener("mousedown", startDrag);
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", stop);
    el.pet.addEventListener("dblclick", () => {
      if (state.host) state.host.openMain();
      else window.open("/?t=" + encodeURIComponent(token), "_blank");
    });
    el.bubble.addEventListener("click", () => {
      if (state.host) state.host.openMain();
      else window.open("/?t=" + encodeURIComponent(token), "_blank");
    });
    el.close.addEventListener("click", (event) => {
      event.stopPropagation();
      if (state.host) state.host.hideOverlay();
      else window.location.href = "/?t=" + encodeURIComponent(token);
    });
    el.overlay.addEventListener("contextmenu", (event) => event.preventDefault());
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

  bind();
  connectHost();
  refresh();
  setInterval(refresh, 8000);
})();
