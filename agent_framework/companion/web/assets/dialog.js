/* In-page confirm/prompt: QtWebEngine does not implement the native dialogs,
   so their JavaScript counterparts return unusable values inside the shell. */
(function () {
  "use strict";

  let current = null;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function focusables(root) {
    return Array.prototype.slice.call(
      root.querySelectorAll("button, input, [tabindex]:not([tabindex='-1'])")
    );
  }

  function show(kind, message, options, initial) {
    options = options || {};
    return new Promise((resolve) => {
      if (current) current.cancel();
      const trigger = document.activeElement;
      const scrim = node("div", "dialog-scrim");
      const dialog = node("div", "dialog" + (options.danger ? " danger" : ""));
      dialog.setAttribute("role", "dialog");
      dialog.setAttribute("aria-modal", "true");
      dialog.setAttribute("aria-labelledby", "companionDialogTitle");
      dialog.setAttribute("aria-describedby", "companionDialogMessage");
      dialog.tabIndex = -1;

      const title = node(
        "h2",
        "dialog-title",
        options.title || (kind === "prompt" ? "请输入" : "请确认")
      );
      title.id = "companionDialogTitle";
      const body = node("p", "dialog-message", message || "");
      body.id = "companionDialogMessage";
      dialog.appendChild(title);
      dialog.appendChild(body);

      let input = null;
      if (kind === "prompt") {
        input = node("input", "field dialog-input");
        input.type = "text";
        input.value = initial == null ? "" : String(initial);
        input.setAttribute("aria-label", message || "输入内容");
        dialog.appendChild(input);
      }
      // A caller-built body (the 模型服务接入 sheet) keeps the scrim, the
      // Escape/Tab/focus-restore contract and the theme in one place.
      if (options.content) {
        dialog.appendChild(options.content);
      }

      const actions = node("div", "dialog-actions");
      const cancel = node("button", "ghost dialog-cancel", options.cancelText || "取消");
      cancel.type = "button";
      const ok = node("button", "primary dialog-ok", options.okText || "确定");
      ok.type = "button";
      if (options.danger) ok.classList.add("danger");
      actions.appendChild(cancel);
      actions.appendChild(ok);
      dialog.appendChild(actions);
      scrim.appendChild(dialog);

      let closed = false;
      function finish(result) {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKey, true);
        scrim.remove();
        if (current && current.finish === finish) current = null;
        if (trigger && document.contains(trigger) && typeof trigger.focus === "function") {
          trigger.focus();
        }
        resolve(result);
      }
      function cancelDialog() {
        finish(kind === "prompt" ? null : false);
      }
      function accept() {
        finish(kind === "prompt" ? input.value : true);
      }
      function onKey(event) {
        if (event.key === "Escape") {
          event.preventDefault();
          cancelDialog();
          return;
        }
        if (event.key === "Tab") {
          const items = focusables(dialog);
          if (!items.length) return;
          const first = items[0];
          const last = items[items.length - 1];
          if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
          return;
        }
        if (event.key !== "Enter") return;
        // A form sheet owns Enter: intercepting it here would turn Enter on a
        // focused 取消 into an accept, and would save before the list is fetched.
        if (kind === "form") return;
        // An IME confirmation also fires Enter; treating it as submit would
        // swallow the last composed characters.
        if (event.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        accept();
      }

      current = { cancel: cancelDialog, finish: finish };
      cancel.onclick = cancelDialog;
      ok.onclick = accept;
      scrim.addEventListener("click", (event) => {
        if (event.target === scrim) cancelDialog();
      });
      document.addEventListener("keydown", onKey, true);
      document.body.appendChild(scrim);
      const first = options.content ? focusables(options.content)[0] : null;
      (input || first || cancel).focus();
      if (input) input.select();
    });
  }

  window.CompanionDialog = {
    confirm: function (message, options) {
      return show("confirm", message, options);
    },
    prompt: function (message, defaultValue, options) {
      return show("prompt", message, options, defaultValue);
    },
    // Caller-built body (the 模型服务接入 sheet); same scrim, Escape, focus
    // trap and focus restore as confirm/prompt so dialogs do not fork twice.
    form: function (message, content, options) {
      const config = Object.assign({}, options || {}, { content: content });
      return show("form", message, config, null);
    },
  };
})();
