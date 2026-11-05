/* Live2D desktop stage: the only renderer for the pet (D58).
   Loads local PixiJS + pixi-live2d-display + Cubism Core; no fallback figure. */
(function (global) {
  "use strict";

  // Mirrors AffectService labels (affect.py). Expressions are matched by name
  // against model3.json; a miss keeps the model's default pose.
  const WARM = ["warm", "positive"];
  const LOW = ["low", "tired", "concerned"];
  const CALM = ["calm", "neutral"];

  // The standby group is the stage's own business; it never enters the
  // content-action vocabulary.
  const IDLE_GROUP = /idle/i;

  // Head/eye follow is driven through the library's focus controller, which
  // writes ParamEyeBallX/Y and ParamAngleX/Y/Z. This skin's deformation links
  // to the VTube-style controller ids instead, so the same numbers are copied
  // onto those ids once per frame.
  const GAZE_MIRROR = [
    ["AngleX", (x, y) => 30 * x],
    ["AngleY", (x, y) => 18 * y],
    ["AngleZ", (x, y) => -8 * x],
  ];

  // DeepSeek's free Remielle pack gates every arm and hand mesh behind
  // Movement1..3 and leaves all three at 0 in the bind pose, so an untouched
  // model renders sleeves with no hands at all. Movement2 is the open hand at
  // the hip; every clip that ends "切回默认手" drives it back to 0, which would
  // hide the hands again, so it is held open every frame instead.
  const REST_POSE = [["Movement2", 1]];

  const GESTURE_MIN_MS = 42000;
  const GESTURE_SPAN_MS = 48000;
  const GESTURE_CLIP_PAD_MS = 250;
  const GESTURE_WATCHDOG_MS = 12000;
  const GESTURE_QUEUE = 3;

  // Two expressions cross-fade for about a second and both face layers are
  // visible while they do, so rapid switching reads as a doubled face.
  const EXPRESSION_GAP_MS = 1600;

  // Cubism can pack four mask channels into one render texture. The library
  // defaults to one texture (36 masks); complex models may exceed that and
  // silently draw nothing, so scale the texture count before the first draw.
  const MASKS_PER_TEXTURE = 32;
  const SINGLE_TEXTURE_MASK_LIMIT = 36;

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

  // Standalone so the contract is testable without a WebGL context.
  function gesturePool(motions, groups) {
    const declared = (groups && groups.idle_pool) || null;
    const source = declared && declared.length ? declared : motions || [];
    const pool = [];
    for (const name of source) {
      if (!name || IDLE_GROUP.test(name)) continue;
      if (pool.indexOf(name) < 0) pool.push(name);
    }
    return pool;
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

  function modelBaseSize(model) {
    const internal = model && model.internalModel;
    if (internal && internal.originalWidth > 0 && internal.originalHeight > 0) {
      return {
        width: internal.originalWidth,
        height: internal.originalHeight,
      };
    }
    const scaleX = Math.abs(Number(model.scale.x)) || 1;
    const scaleY = Math.abs(Number(model.scale.y)) || 1;
    return {
      width: Number(model.width) / scaleX,
      height: Number(model.height) / scaleY,
    };
  }

  function fit(stage) {
    const model = stage.model;
    const width = stage.app.screen.width;
    const height = stage.app.screen.height;
    if (!model) return;
    const base = modelBaseSize(model);
    if (!base.width || !base.height) return;
    const scale = Math.min(width / base.width, height / base.height) * 0.98;
    model.scale.set(scale);
    model.anchor.set(0.5, 1);
    model.x = width / 2;
    model.y = height;
  }

  function hasParam(coreModel, id) {
    try {
      return coreModel.getParameterIndex(id) !== -1;
    } catch (error) {
      return false;
    }
  }

  function writeParam(coreModel, id, value) {
    try {
      coreModel.setParameterValueById(id, value);
    } catch (error) {
      /* an unknown id is simply not part of this model */
    }
  }

  function ensureMaskCapacity(model) {
    const internal = model && model.internalModel;
    const renderer = internal && internal.renderer;
    const core = internal && internal.coreModel;
    const manager = renderer && renderer._clippingManager;
    if (
      !core ||
      !renderer ||
      !manager ||
      typeof manager.getClippingMaskCount !== "function" ||
      typeof renderer.initialize !== "function"
    ) {
      return 1;
    }
    let count = 0;
    try {
      count = Number(manager.getClippingMaskCount()) || 0;
    } catch (error) {
      return 1;
    }
    if (count <= SINGLE_TEXTURE_MASK_LIMIT) return 1;
    const textures = Math.max(2, Math.ceil(count / MASKS_PER_TEXTURE));
    try {
      renderer.initialize(core, textures);
    } catch (error) {
      /* the model loader reports the failure; do not hide it behind a blank pet */
      throw new Error("无法初始化 Live2D 遮罩。");
    }
    return textures;
  }

  // A clip keeps its last value while it fades out, and the chain only clears
  // gestureName some time after the queue has gone quiet, so gating the rest
  // pose on either of them leaves the hands blank for about a second. Movement2
  // is this pack's hand switch, so it is held open all the time instead.
  function applyFrameState(stage) {
    const internal = stage.model && stage.model.internalModel;
    const core = internal && internal.coreModel;
    if (!core) return;
    const focus = internal.focusController;
    if (focus && (focus.x || focus.y)) {
      for (const entry of GAZE_MIRROR) {
        if (hasParam(core, entry[0])) writeParam(core, entry[0], entry[1](focus.x, focus.y));
      }
    }
    for (const entry of REST_POSE) {
      if (hasParam(core, entry[0])) writeParam(core, entry[0], entry[1]);
    }
  }

  function paint(stage) {
    if (!stage.model) return;
    try {
      applyFrameState(stage);
      stage.model.update(0);
      stage.app.render();
    } catch (error) {
      /* a static frame is best effort; failures surface on the next resume */
    }
  }

  function managerOf(stage) {
    return stage.model && stage.model.internalModel && stage.model.internalModel.motionManager;
  }

  function priority(name) {
    const table = (global.PIXI && global.PIXI.live2d && global.PIXI.live2d.MotionPriority) || null;
    if (!table) return 2;
    return table[name] !== undefined ? table[name] : 2;
  }

  function stopTimers(stage) {
    clearTimeout(stage.chainTimer);
    clearTimeout(stage.rotator);
    stage.chainTimer = 0;
    stage.rotator = 0;
  }

  function scheduleRotator(stage) {
    clearTimeout(stage.rotator);
    stage.rotator = 0;
    if (stage.options.reduceMotion) return;
    if (!stage.pool.length) return;
    const wait = GESTURE_MIN_MS + Math.random() * GESTURE_SPAN_MS;
    stage.rotator = setTimeout(() => {
      stage.rotator = 0;
      if (stage.options.reduceMotion || stage.destroyed) return;
      const name = stage.pool[Math.floor(Math.random() * stage.pool.length)];
      enqueueGesture(stage, name, true);
      scheduleRotator(stage);
    }, wait);
  }

  function enqueueGesture(stage, name, fromRotator) {
    if (!name || stage.destroyed) return;
    if (stage.options.reduceMotion) return;
    const known = stage.groups.indexOf(name) >= 0;
    if (!known) {
      applyExpressionGesture(stage, name);
      return;
    }
    if (fromRotator && stage.gestureName) return;
    if (stage.queue.length >= GESTURE_QUEUE) return;
    if (stage.gestureName === name || stage.queue.indexOf(name) >= 0) return;
    stage.queue.push(name);
    pumpGestures(stage);
    if (!fromRotator) scheduleRotator(stage);
  }

  function applyExpressionGesture(stage, name) {
    // An action id that names an expression is a face change, not a clip.
    if ((stage.options.expressions || []).indexOf(name) < 0) return;
    applyExpression(stage, name, true);
  }

  function pumpGestures(stage) {
    if (stage.gestureName || stage.destroyed) return;
    if (stage.options.reduceMotion) {
      stage.queue.length = 0;
      return;
    }
    const name = stage.queue.shift();
    if (!name) {
      scheduleRotator(stage);
      return;
    }
    startChain(stage, name);
  }

  function startChain(stage, name) {
    const manager = managerOf(stage);
    const clips = manager && manager.definitions ? manager.definitions[name] : null;
    if (!clips || !clips.length) {
      stage.gestureName = "";
      pumpGestures(stage);
      return;
    }
    const indexes = clips.length > 1 ? [0, clips.length - 1] : [0];
    const token = ++stage.chainToken;
    stage.gestureName = name;
    step(0);

    function step(position) {
      if (token !== stage.chainToken || stage.destroyed) return;
      if (position >= indexes.length) {
        stage.gestureName = "";
        if (stage.options.reduceMotion) paint(stage);
        pumpGestures(stage);
        return;
      }
      let pending;
      try {
        pending = Promise.resolve(
          manager.startMotion(name, indexes[position], priority("NORMAL"))
        );
      } catch (error) {
        stage.gestureName = "";
        pumpGestures(stage);
        return;
      }
      pending
        .then((motion) => {
          if (token !== stage.chainToken || stage.destroyed) return;
          let seconds = 0;
          try {
            if (motion && typeof motion.getLoopDuration === "function") {
              seconds = motion.getLoopDuration();
            }
          } catch (error) {
            seconds = 0;
          }
          if (!isFinite(seconds) || seconds <= 0) seconds = 1;
          const wait = Math.min(seconds * 1000 + GESTURE_CLIP_PAD_MS, GESTURE_WATCHDOG_MS);
          clearTimeout(stage.chainTimer);
          stage.chainTimer = setTimeout(() => step(position + 1), wait);
        })
        .catch(() => {
          // A clip that will not load degrades to the next one; the pet itself
          // is still fine, so this must never be reported as a runtime failure.
          if (token !== stage.chainToken || stage.destroyed) return;
          step(position + 1);
        });
    }
  }

  function applyExpression(stage, name, forced) {
    const target = name || "";
    if (!forced && stage.expression === target) return;
    const now = Date.now();
    if (now - stage.expressionAt < EXPRESSION_GAP_MS) {
      stage.pendingExpression = { name: target, forced: forced };
      clearTimeout(stage.expressionTimer);
      stage.expressionTimer = setTimeout(() => {
        stage.expressionTimer = 0;
        const pending = stage.pendingExpression;
        stage.pendingExpression = null;
        if (pending) applyExpression(stage, pending.name, pending.forced);
      }, EXPRESSION_GAP_MS);
      return;
    }
    stage.expressionAt = now;
    const manager = managerOf(stage);
    stage.expression = target;
    if (!target) {
      if (manager && manager.expressionManager) {
        try {
          manager.expressionManager.resetExpression();
        } catch (error) {
          /* nothing was applied; the default pose stays */
        }
      }
      if (stage.options.reduceMotion) paint(stage);
      return;
    }
    try {
      Promise.resolve(stage.model.expression(target))
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

  function applyMood(stage, mood) {
    if (!stage.model || stage.mood === mood) return;
    stage.mood = mood || "";
    applyExpression(stage, pickExpression(stage.options.expressions, stage.mood), false);
  }

  function setFocus(stage, x, y) {
    if (!stage.model || stage.options.reduceMotion) return;
    const internal = stage.model.internalModel;
    const controller = internal && internal.focusController;
    if (!controller || typeof controller.focus !== "function") return;
    const clamp = (value) => Math.max(-1, Math.min(1, Number(value) || 0));
    controller.focus(clamp(x), clamp(y));
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
      expressionAt: 0,
      expressionTimer: 0,
      pendingExpression: null,
      gestureName: "",
      chainToken: 0,
      chainTimer: 0,
      rotator: 0,
      queue: [],
      pool: [],
      groups: [],
      destroyed: false,
      options: {
        expressions: options.expressions || [],
        motions: options.motions || [],
        motionGroups: options.motionGroups || {},
        reduceMotion: Boolean(options.reduceMotion),
      },
    };
    stage.groups = (options.motions || []).slice();
    stage.pool = gesturePool(stage.groups, stage.options.motionGroups);
    return PIXI.live2d.Live2DModel.from(options.modelUrl, { autoInteract: false })
      .then((model) => {
        stage.model = model;
        ensureMaskCapacity(model);
        app.stage.addChild(model);
        fit(stage);
        const internal = model.internalModel;
        if (internal && typeof internal.on === "function") {
          internal.on("beforeModelUpdate", () => applyFrameState(stage));
        }
        applyMood(stage, options.mood);
        if (stage.options.reduceMotion) {
          app.ticker.stop();
          paint(stage);
        } else {
          app.ticker.start();
          scheduleRotator(stage);
        }
        return {
          setMood: (mood) => applyMood(stage, mood),
          setMotion: (name) => enqueueGesture(stage, name, false),
          setFocus: (x, y) => {
            setFocus(stage, x, y);
            if (stage.options.reduceMotion) paint(stage);
          },
          setReduceMotion: (value) => {
            stage.options.reduceMotion = Boolean(value);
            if (stage.options.reduceMotion) {
              stopTimers(stage);
              stage.queue.length = 0;
              stage.gestureName = "";
              stage.chainToken += 1;
              const manager = managerOf(stage);
              if (manager) {
                try {
                  manager.stopAllMotions();
                } catch (error) {
                  /* nothing was playing */
                }
              }
              app.ticker.stop();
              paint(stage);
            } else if (!app.ticker.started) {
              app.ticker.start();
              paint(stage);
            }
            scheduleRotator(stage);
          },
          resize: () => {
            const next = measure(container);
            app.renderer.resize(next.width, next.height);
            fit(stage);
            if (stage.options.reduceMotion) paint(stage);
          },
          destroy: () => {
            stage.destroyed = true;
            stopTimers(stage);
            clearTimeout(stage.expressionTimer);
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
    gesturePool: gesturePool,
    fit: fit,
    ensureMaskCapacity: ensureMaskCapacity,
    unavailableReason: unavailableReason,
    mount: mount,
  };
})(window);
