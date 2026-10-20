/* 游戏内 Overlay 轮询脚本（v1.5.1）
   只做一件事：每 250ms 读 /api/overlay，把「听到」和「状态/回答」写进两行文字。
   内容没变化就不碰 DOM（避免闪烁与无谓重绘）；全部用 textContent，杜绝注入。 */
(function () {
  "use strict";

  var STATES = ["idle", "thinking", "answer", "error"];
  var FALLBACK = { idle: "等待提问", thinking: "AI 正在思考…" };
  var OFFLINE_IDLE_FALLBACK = "等待主程序";

  var root = document.getElementById("ov-card");
  var heardEl = document.getElementById("ov-heard-text");
  var answerEl = document.getElementById("ov-answer");
  var lastSignature = "";
  var failures = 0;

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

  function render(data) {
    var available = !!(data && data.available);
    var live = (data && data.live) || {};
    var state = available ? pickState(live) : "idle";
    var heard = available ? String(live.heard || "").trim() : "";
    if (root) root.dataset.state = state;
    setText(heardEl, available ? (heard || "等待语音输入") : OFFLINE_IDLE_FALLBACK);
    setText(answerEl, available ? answerTextFor(state, live) : "Overlay 未就绪");
  }

  function signatureOf(data) {
    var live = (data && data.live) || {};
    return [!!(data && data.available), data && data.enabled,
            live.heard, live.state, live.answer, live.error].join("\u0001");
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

  poll();
})();
