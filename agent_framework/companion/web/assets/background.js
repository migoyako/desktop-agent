/* The user's window background: one switch for the three chrome windows.
   The overlay never loads this file, so the desk pet stays transparent. */
(function (global) {
  "use strict";

  function apply(ui, assets, token) {
    const root = document.documentElement;
    const background = (assets || {}).background;
    if (!background || !ui || !ui.background_enabled) {
      root.removeAttribute("data-bg");
      root.style.removeProperty("--bg-image");
      return;
    }
    const src =
      background.url +
      "?t=" + encodeURIComponent(token) +
      "&v=" + encodeURIComponent(background.stamp);
    root.style.setProperty("--bg-image", 'url("' + src + '")');
    root.dataset.bg = "on";
  }

  global.CompanionBackground = { apply: apply };
})(window);
