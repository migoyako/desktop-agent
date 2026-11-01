/* Live2D desktop stage: the only renderer for the pet (D58).
   Loads local PixiJS + pixi-live2d-display + Cubism Core; no fallback figure. */
(function (global) {
  "use strict";

  // Mirrors AffectService labels (affect.py). Expressions are matched by name
  // against model3.json; a miss keeps the model's default pose.
  const WARM = ["warm", "positive"];
  const LOW = ["low", "tired", "concerned"];
  const CALM = ["calm", "neutral"];

  function normalize(value) {
    return String(value || "")
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fff]+/g, "");
  }

  function groupOf(mood) {
    const label = String(mood || "").toLowerCase();
    if (WARM.indexOf(label) >= 0) return "warm";
    if (LOW.indexOf(label) >= 0) return "low";
    return "calm";
  }

  function pickExpression(names, mood) {
    if (!names || !names.length) return "";
    const key = normalize(mood);
    const exact = key && names.find((name) => normalize(name) === key);
    if (exact) return exact;
    const wanted = groupOf(mood);
    const pool = wanted === "warm" ? WARM : wanted === "low" ? LOW : CALM;
    const direct = names.find((name) => pool.indexOf(normalize(name)) >= 0);
    if (direct) return direct;
    return names.find((name) => normalize(name).indexOf(wanted) >= 0) || "";
  }

  function webglAvailable() {
    try {
      const probe = document.createElement("canvas");
      return Boolean(probe.getContext("webgl2") || probe.getContext("webgl"));
    } catch (error) {
      return false;
    }
  }

  function unavailableReason() {
    if (typeof PIXI === "undefined") return "缺少 PixiJS 运行时。";
    if (!PIXI.live2d || typeof PIXI.live2d.Live2DModel !== "function") {
      return "缺少 pixi-live2d-display 运行时。";
    }
    if (typeof Live2DCubismCore === "undefined") return "缺少 Cubism Core。";
    if (!webglAvailable()) return "当前环境不支持 WebGL。";
    return "";
  }

  function measure(container) {
    return {
      width: Math.max(48, Math.round(container.clientWidth || 132)),
      height: Math.max(48, Math.round(container.clientHeight || 132)),
    };
  }

  function fit(stage) {
    const model = stage.model;
    const width = stage.app.screen.width;
    const height = stage.app.screen.height;
    if (!model || !model.width || !model.height) return;
    const scale = Math.min(width / model.width, height / model.height) * 0.98;
    model.scale.set(scale);
    model.anchor.set(0.5, 1);
    model.x = width / 2;
    model.y = height;
  }

  function paint(stage) {
    if (!stage.model) return;
    try {
      stage.model.update(0);
      stage.app.render();
    } catch (error) {
      /* a static frame is best effort; failures surface on the next resume */
    }
  }

  function startIdle(stage) {
    const manager =
      stage.model.internalModel && stage.model.internalModel.motionManager;
    const groups = manager && manager.definitions ? Object.keys(manager.definitions) : [];
    const idle = groups.find((name) => /idle/i.test(name)) || groups[0];
    if (!idle) return;
    try {
      Promise.resolve(stage.model.motion(idle)).catch(() => {});
    } catch (error) {
      /* no idle motion is a valid model; keep the default pose */
    }
  }

  function applyMood(stage, mood) {
    if (!stage.model || stage.mood === mood) return;
    stage.mood = mood || "";
    const name = pickExpression(stage.options.expressions, stage.mood);
    const manager =
      stage.model.internalModel && stage.model.internalModel.motionManager;
    if (!name) {
      stage.expression = "";
      if (manager && manager.expressionManager) {
        manager.expressionManager.resetExpression();
      }
      if (stage.options.reduceMotion) paint(stage);
      return;
    }
    if (stage.expression === name) return;
    stage.expression = name;
    try {
      Promise.resolve(stage.model.expression(name))
        .then(() => {
          if (stage.options.reduceMotion) paint(stage);
        })
        .catch(() => {
          /* an unreadable expression file leaves the default pose */
        });
    } catch (error) {
      /* ignore: expression-level degradation only */
    }
  }

  function mount(container, options) {
    const missing = unavailableReason();
    if (missing) return Promise.reject(new Error(missing));
    container.innerHTML = "";
    const canvas = document.createElement("canvas");
    container.appendChild(canvas);
    const size = measure(container);
    let app;
    try {
      app = new PIXI.Application({
        view: canvas,
        width: size.width,
        height: size.height,
        backgroundAlpha: 0,
        antialias: true,
        autoDensity: true,
        resolution: global.devicePixelRatio || 1,
        autoStart: !options.reduceMotion,
        powerPreference: "low-power",
      });
    } catch (error) {
      return Promise.reject(new Error("无法创建 WebGL 渲染器。"));
    }
    canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      if (typeof options.onError === "function") {
        options.onError("WebGL 上下文丢失。");
      }
    });
    const stage = {
      app: app,
      model: null,
      mood: "",
      expression: "",
      options: {
        expressions: options.expressions || [],
        reduceMotion: Boolean(options.reduceMotion),
      },
    };
    return PIXI.live2d.Live2DModel.from(options.modelUrl, { autoInteract: false })
      .then((model) => {
        stage.model = model;
        app.stage.addChild(model);
        fit(stage);
        applyMood(stage, options.mood);
        if (stage.options.reduceMotion) {
          app.ticker.stop();
          paint(stage);
        } else {
          startIdle(stage);
          app.ticker.start();
        }
        return {
          setMood: (mood) => applyMood(stage, mood),
          setReduceMotion: (value) => {
            stage.options.reduceMotion = Boolean(value);
            if (stage.options.reduceMotion) {
              app.ticker.stop();
              paint(stage);
            } else if (!app.ticker.started) {
              app.ticker.start();
            }
          },
          resize: () => {
            const next = measure(container);
            app.renderer.resize(next.width, next.height);
            fit(stage);
            if (stage.options.reduceMotion) paint(stage);
          },
          destroy: () => {
            try {
              app.destroy(false, { children: true, texture: true, baseTexture: true });
            } catch (error) {
              /* already torn down */
            }
            container.innerHTML = "";
          },
        };
      })
      .catch((error) => {
        try {
          app.destroy(false, { children: true, texture: true, baseTexture: true });
        } catch (ignored) {
          /* nothing to release */
        }
        container.innerHTML = "";
        const reason = error && error.message ? error.message : "模型加载失败。";
        throw new Error("Live2D 模型加载失败：" + reason);
      });
  }

  global.CompanionStage = {
    group: groupOf,
    pickExpression: pickExpression,
    unavailableReason: unavailableReason,
    mount: mount,
  };
})(window);
