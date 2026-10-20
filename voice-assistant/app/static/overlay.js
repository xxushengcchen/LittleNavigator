/* 游戏内 Overlay 轮询 + 布局脚本（v1.5.2）
   1) 每 250ms 读 /api/overlay，把「听到」和「状态/回答」写进两行文字。
      内容没变化就不碰 DOM（避免闪烁与无谓重绘）；全部用 textContent，杜绝注入。
   2) 默认整窗点击穿透（原生 WS_EX_TRANSPARENT）。Ctrl+Alt+L 进入/退出布局模式：
      桌面端热键线程切换原生样式后调用 window.ovSetLayoutMode()，本页再关掉穿透感、
      显示虚线窗口框 / 右下缩放手柄 / 提示条；页面自己也监听同一组合键（窗口有焦点时）。
   3) 布局模式下拖动/缩放实时 POST /api/overlay/layout {x,y,width,height}：
      窗口位置由桌面端真正移动，页面只负责算几何；坐标用 screenX/screenY 的绝对位移，
      避免「窗口跟着鼠标动导致增量自激」的经典漂移。
   4) 轮询回报里的 layout_mode / x / y / width / height 作为兜底同步（T2 的 /api/overlay 提供），
      字段缺失时静默跳过，不因为接口没接线就白屏。 */
(function () {
  "use strict";

  var STATES = ["idle", "thinking", "answer", "error"];
  var FALLBACK = { idle: "等待提问", thinking: "AI 正在思考…" };
  var OFFLINE_IDLE_FALLBACK = "等待主程序";
  /* 与桌面端 OverlayController.set_bounds 的钳制保持一致，避免拖出负尺寸后窗口消失 */
  var MIN_WIDTH = 240, MAX_WIDTH = 4096, MIN_HEIGHT = 80, MAX_HEIGHT = 2160;
  var LAYOUT_SEND_MS = 70;

  var root = document.getElementById("ov-card");
  var heardEl = document.getElementById("ov-heard-text");
  var answerEl = document.getElementById("ov-answer");
  var frameEl = document.getElementById("ov-frame");
  var handleEl = document.getElementById("ov-resize");
  var hintEl = document.getElementById("ov-hint");

  var lastSignature = "";
  var failures = 0;
  var layoutMode = false;
  var geom = null;          /* 服务端最近一次已知几何 {x,y,width,height} */
  var drag = null;          /* 拖动/缩放起点快照 */
  var pendingLayout = null; /* 待发送的几何 */
  var sendTimer = 0;

  // ---------------------------------------------------------------- 文本
  function pickState(live) {
    var state = live && live.state;
    return STATES.indexOf(state) >= 0 ? state : "idle";
  }

  function answerTextFor(state, live) {
    if (state === "answer") {
      var answer = String((live && live.answer) || "").trim();
      return answer || "…";
    }
    if (state === "error") {
      var error = String((live && live.error) || "").trim();
      return error || "AI 暂时不可用";
    }
    return FALLBACK[state] || FALLBACK.idle;
  }

  function setText(el, text) {
    if (el && el.textContent !== text) el.textContent = text;
  }

  // ---------------------------------------------------------------- 布局模式 UI
  function applyLayoutMode(enabled) {
    layoutMode = !!enabled;
    if (document.body) document.body.dataset.layout = layoutMode ? "true" : "false";
    if (frameEl) frameEl.hidden = !layoutMode;
    if (hintEl) hintEl.hidden = !layoutMode;
    if (handleEl) handleEl.hidden = !layoutMode;
    if (!layoutMode) stopDrag();
  }

  /* 桌面端热键线程通过 evaluate_js 调这个入口（保持与原生样式同步） */
  window.ovSetLayoutMode = applyLayoutMode;

  function postJSON(path, body) {
    return fetch(path, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (response) {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    });
  }

  function requestLayoutMode(enabled) {
    var target = !!enabled;
    applyLayoutMode(target);           /* 先本地生效，等确认回来再对齐 */
    return postJSON("/api/overlay/layout_mode", { enabled: target })
      .then(function (data) {
        var value = data && typeof data.layout_mode === "boolean"
          ? data.layout_mode : target;
        applyLayoutMode(value);
        return value;
      })
      .catch(function () {
        /* 无控制器（浏览器预览）或接口未接线时保留本地 UI 切换，便于调样式 */
        return target;
      });
  }

  function toggleLayoutMode() {
    return requestLayoutMode(!layoutMode);
  }

  document.addEventListener("keydown", function (event) {
    if (!event.ctrlKey || !event.altKey) return;
    var key = String(event.key || "").toLowerCase();
    if (key !== "l" && event.code !== "KeyL") return;
    event.preventDefault();
    toggleLayoutMode();
  }, true);

  // ---------------------------------------------------------------- 几何
  function clamp(value, lo, hi) {
    return Math.min(hi, Math.max(lo, value));
  }

  function geometryNow() {
    if (geom && typeof geom.x === "number" && typeof geom.y === "number") {
      return { x: geom.x, y: geom.y, width: geom.width, height: geom.height };
    }
    return {
      x: Math.round(window.screenX || 0),
      y: Math.round(window.screenY || 0),
      width: window.innerWidth,
      height: window.innerHeight
    };
  }

  function flushLayout() {
    sendTimer = 0;
    var body = pendingLayout;
    pendingLayout = null;
    if (!body) return;
    geom = body;
    postJSON("/api/overlay/layout", body).then(function (data) {
      if (data && typeof data.x === "number" && typeof data.y === "number" &&
          typeof data.width === "number" && typeof data.height === "number") {
        geom = { x: data.x, y: data.y, width: data.width, height: data.height };
      }
    }).catch(function () {
      /* 桌面端未实现该接口时静默：下一条轮询会用 config 里的几何兜底 */
    });
  }

  function queueLayout(next) {
    pendingLayout = next;
    if (sendTimer) return;
    sendTimer = setTimeout(flushLayout, LAYOUT_SEND_MS);
  }

  function beginDrag(kind, event) {
    if (!layoutMode || event.button !== 0 || event.isPrimary === false) return;
    var start = geometryNow();
    drag = {
      kind: kind,
      sx: event.screenX,
      sy: event.screenY,
      ox: start.x,
      oy: start.y,
      ow: start.width,
      oh: start.height
    };
    if (!geom) geom = { x: start.x, y: start.y, width: start.width, height: start.height };
    if (document.body) document.body.dataset.dragging = kind;
    if (event.target && event.target.setPointerCapture) {
      try { event.target.setPointerCapture(event.pointerId); } catch (error) { /* 忽略 */ }
    }
    event.preventDefault();
  }

  function moveDrag(event) {
    if (!drag) return;
    var dx = event.screenX - drag.sx;
    var dy = event.screenY - drag.sy;
    if (drag.kind === "resize") {
      queueLayout({
        x: drag.ox,
        y: drag.oy,
        width: clamp(Math.round(drag.ow + dx), MIN_WIDTH, MAX_WIDTH),
        height: clamp(Math.round(drag.oh + dy), MIN_HEIGHT, MAX_HEIGHT)
      });
    } else {
      queueLayout({
        x: Math.round(drag.ox + dx),
        y: Math.round(drag.oy + dy),
        width: drag.ow,
        height: drag.oh
      });
    }
    event.preventDefault();
  }

  function stopDrag() {
    drag = null;
    if (document.body) delete document.body.dataset.dragging;
  }

  function endDrag() {
    if (!drag) return;
    stopDrag();
    if (pendingLayout) flushLayout();
  }

  document.addEventListener("pointerdown", function (event) {
    if (!layoutMode) return;
    var onHandle = handleEl &&
      (event.target === handleEl ||
       (handleEl.contains && handleEl.contains(event.target)));
    beginDrag(onHandle ? "resize" : "move", event);
  }, true);
  document.addEventListener("pointermove", function (event) {
    if (drag) moveDrag(event);
  }, true);
  document.addEventListener("pointerup", function () {
    if (drag) endDrag();
  }, true);
  document.addEventListener("pointercancel", function () {
    if (drag) endDrag();
  }, true);
  window.addEventListener("blur", function () {
    if (drag) endDrag();
  });

  // ---------------------------------------------------------------- 轮询
  function syncLayout(data) {
    if (!data) return;
    if (typeof data.layout_mode === "boolean" && data.layout_mode !== layoutMode) {
      applyLayoutMode(data.layout_mode);
    }
    if (typeof data.width === "number" && typeof data.height === "number" &&
        !drag && !pendingLayout) {
      if (typeof data.x === "number" && typeof data.y === "number") {
        geom = { x: data.x, y: data.y, width: data.width, height: data.height };
      } else {
        geom = null;   /* x/y 为 null：按 corner 排，等布局接口回包再拿绝对坐标 */
      }
    }
  }

  function render(data) {
    var available = !!(data && data.available);
    var live = (data && data.live) || {};
    var state = available ? pickState(live) : "idle";
    var heard = available ? String(live.heard || "").trim() : "";
    if (root) root.dataset.state = state;
    setText(heardEl, available ? (heard || "等待语音输入") : OFFLINE_IDLE_FALLBACK);
    setText(answerEl, available ? answerTextFor(state, live) : "Overlay 未就绪");
    syncLayout(data);
  }

  function signatureOf(data) {
    var live = (data && data.live) || {};
    return [!!(data && data.available), data && data.enabled,
            live.heard, live.state, live.answer, live.error,
            data && data.layout_mode, data && data.x, data && data.y,
            data && data.width, data && data.height].join("\u0001");
  }

  function markOffline() {
    if (lastSignature === "__offline__") return;
    lastSignature = "__offline__";
    if (root) root.dataset.state = "idle";
    setText(heardEl, "等待主程序");
    setText(answerEl, "Overlay 未连接");
  }

  async function poll() {
    try {
      var response = await fetch("/api/overlay", { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      var data = await response.json();
      failures = 0;
      var signature = signatureOf(data);
      if (signature !== lastSignature) {
        lastSignature = signature;
        render(data);
      }
    } catch (error) {
      failures += 1;
      if (failures >= 3) markOffline();
    } finally {
      setTimeout(poll, 250);
    }
  }

  applyLayoutMode(false);
  poll();
})();
