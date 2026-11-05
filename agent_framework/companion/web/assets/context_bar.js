/* Shared context budget bar (M15 slice 3).

   One renderer for the main window and the settings panels: both draw the same
   bands from the single ContextView the projection derives, so the two
   surfaces cannot disagree about how the window is filled. */
(function () {
  "use strict";

  const LABELS = {
    system: "系统提示",
    recall: "记忆召回",
    docs: "文档检索",
    history: "历史对话",
    reply: "回复预留",
    free: "空闲",
  };

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function band(segment) {
    const key = String((segment && segment.key) || "");
    return {
      key: key,
      label: LABELS[key] || String((segment && segment.label) || key),
      tokens: Number(segment && segment.tokens) || 0,
      chars: Number(segment && segment.chars) || 0,
    };
  }

  function bands(context) {
    const list = ((context || {}).segments || []).map(band);
    const limit = Number((context || {}).context_limit) || 0;
    // A prompt can overshoot a window the user just shrank; measure against the
    // larger of the two so no band spills out of the rounded track.
    const total = list.reduce((sum, item) => sum + item.chars, 0);
    return { items: list, measure: Math.max(limit, total, 1) };
  }

  function count(value) {
    return Number(value).toLocaleString("zh-CN");
  }

  function markup(context) {
    if (!context) return "";
    const view = bands(context);
    if (!view.items.length) return "";
    const track = view.items
      .map((item) => {
        const share = Math.max(0, (item.chars / view.measure) * 100);
        return `<span class="ctxbar-seg" data-key="${escapeHtml(item.key)}" style="--band: ${share.toFixed(2)}%"></span>`;
      })
      .join("");
    const legend = view.items
      .map((item) => {
        const share = Math.max(0, Math.round((item.chars / view.measure) * 100));
        return (
          `<li class="ctxbar-item" data-key="${escapeHtml(item.key)}">` +
          `<span class="ctxbar-dot" aria-hidden="true"></span>` +
          `<span class="ctxbar-name">${escapeHtml(item.label)}</span>` +
          `<span class="ctxbar-value">${count(item.tokens)} t · ${share}%</span>` +
          `<span class="ctxbar-raw">${count(item.chars)} 字符</span>` +
          `</li>`
        );
      })
      .join("");
    return (
      `<div class="ctxbar">` +
      `<div class="ctxbar-track" aria-hidden="true">${track}</div>` +
      `<ul class="ctxbar-legend">${legend}</ul>` +
      `</div>`
    );
  }

  // Before the first turn there is no assembled prompt to measure, but the
  // reply reservation is already decided by max_tokens, so the panel can still
  // show what the window is holding back.
  function reservation(contextTokens, maxTokens, charsPerToken) {
    const limit = Number(contextTokens) || 0;
    const reserve = Math.round((Number(maxTokens) || 0) * Number(charsPerToken || 2));
    if (limit <= 0) return null;
    const capped = Math.min(Math.max(0, reserve), limit);
    return {
      context_used: 0,
      context_limit: limit,
      compressed: false,
      segments: [
        { key: "reply", label: LABELS.reply, tokens: Number(maxTokens) || 0, chars: capped },
        {
          key: "free",
          label: LABELS.free,
          tokens: Math.ceil(Math.max(0, limit - capped) / Number(charsPerToken || 2)),
          chars: Math.max(0, limit - capped),
        },
      ],
    };
  }

  window.CompanionContextBar = { markup: markup, reservation: reservation, labels: LABELS };
})();
