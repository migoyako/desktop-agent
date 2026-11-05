/* Mesh Avatar desktop stage: renders a .mavatar archive with the vendored
   mesh-avatar runtime. No fallback figure; failures surface as unavailable. */
(function (global) {
  "use strict";

  // Mirrors AffectService labels (affect.py). Mesh expressions are a fixed
  // vocabulary; the mood picks one of them.
  const WARM = ["warm", "positive"];
  const LOW = ["low", "tired", "concerned"];
  const GROUP_EXPRESSION = { warm: "smile", low: "sad", calm: "neutral" };

  function groupOf(mood) {
    const label = String(mood || "").toLowerCase();
    if (WARM.indexOf(label) >= 0) return "warm";
    if (LOW.indexOf(label) >= 0) return "low";
    return "calm";
  }

  function pickExpression(mood) {
    return GROUP_EXPRESSION[groupOf(mood)] || "neutral";
  }

  function webglAvailable() {
    try {
      return Boolean(document.createElement("canvas").getContext("webgl2"));
    } catch (error) {
      return false;
    }
  }

  function unavailableReason() {
    if (
      typeof MeshAvatarRuntime === "undefined" ||
      typeof MeshAvatarRuntime.loadMeshAvatar !== "function"
    ) {
      return "缺少 mesh-avatar 运行时。";
    }
    if (!webglAvailable()) return "当前环境不支持 WebGL2。";
    return "";
  }

  function mount(container, options) {
    const missing = unavailableReason();
    if (missing) return Promise.reject(new Error(missing));
    container.innerHTML = "";
    const canvas = document.createElement("canvas");
    canvas.className = "mesh-canvas";
    container.appendChild(canvas);
    return fetch(options.archiveUrl, {
      headers: { "X-Companion-Token": options.token || "" },
      cache: "no-store",
    })
      .then((response) => {
        if (!response.ok) throw new Error(".mavatar 读取失败。");
        return response.arrayBuffer();
      })
      .then((buffer) =>
        MeshAvatarRuntime.loadMeshAvatar(canvas, buffer, {
          manual: true,
          fit: "contain",
        })
      )
      .then((avatar) => {
        const stage = {
          avatar: avatar,
          mood: "",
          reduceMotion: Boolean(options.reduceMotion),
          raf: 0,
          last: 0,
          destroyed: false,
        };
        const stop = () => {
          if (stage.raf) cancelAnimationFrame(stage.raf);
          stage.raf = 0;
        };
        const draw = () => {
          if (!stage.destroyed) avatar.advance(0);
        };
        const fail = (message) => {
          stop();
          if (typeof options.onError === "function") options.onError(message);
        };
        const frame = (now) => {
          if (stage.destroyed) return;
          const dt = stage.last ? Math.min(0.05, (now - stage.last) / 1000) : 1 / 60;
          stage.last = now;
          try {
            avatar.advance(dt);
          } catch (error) {
            fail("mesh 渲染失败。");
            return;
          }
          stage.raf = requestAnimationFrame(frame);
        };
        const start = () => {
          if (stage.raf) return;
          stage.last = 0;
          stage.raf = requestAnimationFrame(frame);
        };
        const applyMood = (mood) => {
          if (stage.mood === mood) return;
          stage.mood = mood || "";
          avatar.setExpression(pickExpression(stage.mood));
          if (stage.reduceMotion) draw();
        };
        const setReduceMotion = (value) => {
          stage.reduceMotion = Boolean(value);
          if (stage.reduceMotion) {
            stop();
            avatar.setAutoIdle(false);
            avatar.setAutoMotion(false);
            avatar.setSwayGain(0);
            draw();
          } else {
            avatar.setAutoIdle(true);
            avatar.setAutoMotion(true);
            avatar.setSwayGain(1);
            start();
          }
        };
        canvas.addEventListener("webglcontextlost", (event) => {
          event.preventDefault();
          fail("WebGL 上下文丢失。");
        });
        applyMood(options.mood);
        setReduceMotion(stage.reduceMotion);
        return {
          setMood: applyMood,
          // A .mavatar has no clip library and no rig to point at the cursor.
          setMotion: () => {},
          setFocus: () => {},
          setReduceMotion: setReduceMotion,
          resize: () => {
            if (stage.reduceMotion) draw();
          },
          destroy: () => {
            stage.destroyed = true;
            stop();
            try {
              avatar.destroy();
            } catch (error) {
              /* already torn down */
            }
            container.innerHTML = "";
          },
        };
      })
      .catch((error) => {
        container.innerHTML = "";
        const reason = error && error.message ? error.message : "未知错误";
        throw new Error("mesh 形象加载失败：" + reason);
      });
  }

  global.CompanionMeshStage = {
    group: groupOf,
    pickExpression: pickExpression,
    unavailableReason: unavailableReason,
    mount: mount,
  };
})(window);
