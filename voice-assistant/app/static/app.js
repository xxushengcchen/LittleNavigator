/* iRacing 语音问答助手 · 真实前端
 *
 * 定稿稿（index.html）只留结构与样式，这里负责：
 *   1. 分页切换
 *   2. 主面板实时数据（连接 / PTT / 上一条问答 / 遥测条）
 *   3. 问答记录与统计（读日志）
 *   4. 意图调试台（打字 → 看逐层判定 → 试听）
 *   5. 设置页（读写 config.local.yaml，热生效）
 */
(function () {
  "use strict";

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  async function api(path, body) {
    var opt = { method: body === undefined ? "GET" : "POST" };
    if (body !== undefined) {
      opt.headers = { "Content-Type": "application/json", "X-Assistant-Token": $('meta[name="assistant-token"]').content };
      opt.body = JSON.stringify(body);
    }
    var res = await fetch(path, opt);
    var result = await res.json();
    if (!res.ok || result.error) { throw new Error(result.error || "请求失败"); }
    return result;
  }
  function reportError(error) {
    var notice = $("#ira-error");
    if (!notice) {
      notice = document.createElement("div"); notice.id = "ira-error"; notice.setAttribute("role", "alert");
      notice.style.cssText = "position:fixed;bottom:18px;right:18px;max-width:480px;padding:14px 20px;background:#fff1f0;color:#a02020;z-index:9999;border:1px solid #d99;border-radius:8px";
      notice.onclick = function () { notice.hidden = true; };
      document.body.appendChild(notice);
    }
    notice.textContent = error.message || String(error); notice.hidden = false;
  }
  window.addEventListener("unhandledrejection", function (event) { event.preventDefault(); reportError(event.reason); });

  /* ---------- 样式补丁 ---------- */
  var css = document.createElement("style");
  css.textContent = [
    "i[data-lucide]{display:none!important}",
    ".ira-logrows .ira-logrow{cursor:default}",
    ".ira-bad{color:#C0392B}", ".ira-good{color:#0E9F6E}",
    ".ira-kvgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:6px 14px}",
    ".ira-savehint{font-size:12px;color:#0E9F6E;margin-left:8px}",
    ".ira-field .ira-btn{margin-top:4px}",
  ].join("");
  document.head.appendChild(css);

  /* ---------- 分页切换 ---------- */
  var tabs = $$("[data-nav]");
  var panels = $$("[data-view-panel]");
  function showView(id) {
    tabs.forEach(function (b) {
      var on = b.getAttribute("data-nav") === id;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-selected", on ? "true" : "false");
    });
    panels.forEach(function (p) { p.hidden = p.getAttribute("data-view-panel") !== id; });
    if (id === "log") loadLog();
    if (id === "stats") loadStats();
    if (id === "model" || id === "asr" || id === "pack" || id === "route") loadSettings();
  }
  tabs.forEach(function (b) {
    b.addEventListener("click", function () { showView(b.getAttribute("data-nav")); });
  });

  /* ---------- 小工具 ---------- */
  function cardByLabel(text) {
    var found = null;
    $$(".ira-card").forEach(function (c) {
      var l = $(".ira-label", c);
      if (l && l.textContent.trim() === text) { found = c; }
    });
    return found;
  }
  function setText(sel, text, root) { var e = $(sel, root); if (e) { e.textContent = text; } }
  function fmt(v, unit, nd) {
    if (v === null || v === undefined) { return "—"; }
    return Number(v).toFixed(nd === undefined ? 1 : nd) + (unit || "");
  }
  function badge(cls, text) { return '<span class="ira-badge ' + cls + '">' + text + "</span>"; }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  /* ---------- 主面板 ---------- */
  var PTT_TEXT = { idle: "待机", recording: "收音中", thinking: "处理中",
                   speaking: "播报中", off: "未启动" };

  function renderState(st) {
    var app = $("#ira-app");
    var tele = st.telemetry || {};
    var online = !!tele.connected;
    document.body.classList.toggle('desktop-shell',!!st.desktop);
    app.setAttribute("data-sim", online ? "online" : "offline");
    var topRight = $(".ira-topright");
    if (topRight) {
      setText("[data-conn-label]", st.sim ? "模拟模式" : (online ? "已连接" : "未连接"), topRight);
      setText("[data-ptt-label]", PTT_TEXT[st.ptt.state] || st.ptt.state, topRight);
      $$("[data-conn-state]", topRight).forEach(function (el) { el.setAttribute("data-state", online ? "on" : "off"); });
      $$("[data-ptt-state]", topRight).forEach(function (el) { el.setAttribute("data-state", st.ptt.state); });
    }

    // 定稿稿里的演示开关：真实应用不需要
    var simctl = $(".ira-simctl");
    if (simctl) { simctl.style.display = "none"; }

    // 顶部会话行与延迟
    var session = $(".ira-session");
    if (session) {
      if (st.sim) {
        session.textContent = "模拟模式 · 以下为测试数据";
      } else if (online) {
        var bits = [];
        if (tele.lap) { bits.push("第 " + tele.lap + " 圈"); }
        if (tele.laps_remain !== null && tele.laps_remain !== undefined) {
          bits.push("剩 " + tele.laps_remain + " 圈");
        }
        session.textContent = bits.join(" · ") || "已连接";
      } else {
        session.textContent = st.sim ? "模拟遥测（未开游戏）" : "iRacing 未连接";
      }
    }
    var ms = null;
    if (st.last && st.last.first_audio_ms !== undefined) { ms = st.last.first_audio_ms; }
    else if (st.ptt && st.ptt.last && st.ptt.last.asr_ms) { ms = st.ptt.last.asr_ms; }
    var topLat = $(".ira-topright .ira-num");
    if (topLat) { topLat.textContent = ms === null ? "—" : (ms / 1000).toFixed(2); }

    // 连接卡
    var connCard = cardByLabel("连接");
    if (connCard) {
      var dot = $("[data-conn-state]", connCard);
      if (dot) { dot.setAttribute("data-state", online ? "on" : "off"); dot.setAttribute("data-conn-state", online ? "on" : "off"); }
      setText("[data-conn-label]", st.sim ? "模拟模式" : (online ? "iRacing 已连接" : "iRacing 未连接"), connCard);
      setText(".ira-meta", (tele.source || "-") + " · " + (st.sim ? "模拟遥测" : "实时遥测"), connCard);
    }

    // PTT 卡
    var pttCard = cardByLabel("按住说话（PTT）");
    if (pttCard) {
      var pd = $("[data-ptt-state]", pttCard);
      if (pd) { pd.setAttribute("data-state", st.ptt.state); pd.setAttribute("data-ptt-state", st.ptt.state); }
      setText("[data-ptt-label]", PTT_TEXT[st.ptt.state] || st.ptt.state, pttCard);
      var meta = $(".ira-meta", pttCard);
      if (meta) {
        meta.textContent = st.ptt.running
          ? st.ptt.binding_label
          : (st.ptt.error ? "监听未启动" : st.ptt.binding_label + " · 未启动");
      }
    }

    // 上次回应
    var latCard = cardByLabel("上次回应");
    if (latCard) {
      setText(".ira-num", ms === null ? "—" : (ms / 1000).toFixed(2), latCard);
      setText(".ira-meta", ms === null ? "还没有问答" : "回应处理 " + ms + " ms · 目标 <300 ms", latCard);
    }

    // 最后一条问答
    var ans = $(".ira-answer");
    if (ans) {
      var rec = st.last;
      if (rec && (rec.q || rec.trace)) {
        var tr = rec.trace || null;
        var q = rec.q || (tr && tr.text) || "—";
        var a = rec.text || (tr && tr.plan && tr.plan.map(function (p) { return p.text || ""; }).join("")) || "";
        // AI 回答优先显示：它是新句子，规则层没有对应录音
        if (rec.ai_text) { a = rec.ai_text; }
        if (!a && !tr && rec.ok === false) { a = "（" + (rec.reason || "没听明白，请再说一次") + "）"; }
        if (!a && tr && !tr.intent) { a = "（没听明白，已经拒绝——绝不猜）"; }
        else if (!a && tr && !tr.decision.ok) { a = "（听懂了，但答不了：" + tr.decision.reason + "）"; }
        setText(".ira-q", "「" + q + "」", ans);
        setText(".ira-a", a || "（没有播报）", ans);
        var row = $(".ira-metarow", ans);
        if (row) {
          var layer = (tr && tr.layer) || rec.layer || (rec.intent ? "phrase" : "refuse");
          var badgeCls = layer === "phrase" ? "l1" : (layer === "l1_keyword" ? "l2" : "l3");
          var planIds = (rec.plan || (tr && tr.decision.plan) || []).join(" + ") || "—";
          if (rec.ai_source) { layer = rec.ai_source === "learn" ? "学习库" : "AI"; badgeCls = "l3"; }
          row.innerHTML = badge(badgeCls, layer) +
            '<span class="ira-num ira-meta">' + (rec.first_audio_ms !== undefined
              ? (rec.first_audio_ms / 1000).toFixed(2) + " s" : "—") + "</span>" +
            '<span class="ira-meta">' + (rec.ts || st.now) + "</span>" +
            '<span class="ira-meta">' + esc(planIds) + "</span>";
        }
      }
    }

    // 遥测条
    var tiles = $$(".ira-strip .ira-tile");
    if (tiles.length === 4) {
      fillTile(tiles[0], "油量",
        fmt(tele.fuel_level, "", 1), "L",
        tele.laps_of_fuel === null || tele.laps_of_fuel === undefined
          ? "算不出每圈油耗" : "够跑 " + fmt(tele.laps_of_fuel, " 圈", 1));
      fillTile(tiles[1], "名次",
        tele.position === null || tele.position === undefined ? "—" : "P" + tele.position,
        tele.field_size ? "/ " + tele.field_size : "",
        "");
      fillTile(tiles[2], "落差",
        tele.gap_behind_s === null || tele.gap_behind_s === undefined
          ? "—" : fmt(tele.gap_behind_s, "", 1),
        "秒 · 后车",
        tele.gap_behind_s === null || tele.gap_behind_s === undefined
          ? "对手数据读不到"
          : (tele.gap_behind_trend < -0.05 ? "在追近" : "当前稳定"));
      var wet = tele.track_wetness;
      var track = wet === null || wet === undefined || wet === 0 ? "—" : (wet >= 3 ? "湿" : "干");
      fillTile(tiles[3], "赛道", track, flagCn(tele.flags),
        tele.incident_count === null || tele.incident_count === undefined
          ? "" : "事故点 " + tele.incident_count);
    }

    // 页脚
    var foot = $(".ira-navfoot");
    var footerKey=JSON.stringify([st.app,st.pack.version,st.pack.msgIds,st.desktop,online]);
    if (foot && foot.dataset.renderKey!==footerKey) {
      foot.dataset.renderKey=footerKey;
      var appVer = (st.app && st.app.version) || "—";
      foot.innerHTML = [
        "小小领航员 v" + esc(appVer),
        "xushengchen from Moondrop-Dox Racing",
        "语音包 v" + esc(st.pack.version || "?") + " · " +
          (st.pack.msgIds || 0).toLocaleString() + " msgId",
        online ? "" : "iRacing 未连接",
      ].filter(Boolean).join("<br>");
      foot.insertAdjacentHTML('beforeend','<div class="partner-logos" role="group" aria-label="合作品牌">' +
        ['Moondrop 水月雨','Moondrop Gravity 陨月重工','Drag-On XT Racing Team','Moondrip Racing'].map(function(name){
          return '<div class="partner-logo" role="img" aria-label="'+esc(name)+'" title="'+esc(name)+'"></div>';
        }).join('')+'</div>');
      if (st.desktop) {
        var q = document.createElement("button");
        q.type = "button";
        q.className = "ira-btn";
        q.style.marginTop = "10px";
        q.textContent = "退出程序";
        q.onclick = async function () {
          q.disabled = true;
          q.textContent = "正在退出…";
          try { await api("/api/quit", {}); } catch (e) { /* 服务先关了也无所谓 */ }
        };
        foot.appendChild(q);
      }
    }
    window.__state = st;
  }

  function fillTile(tile, label, value, unit, meta) {
    setText(".ira-label", label, tile);
    var num = $(".ira-tile-num", tile);
    if (num) { num.textContent = value; }
    var u = $(".ira-unit", tile);
    if (u) { u.textContent = unit || ""; }
    var metas = $$(".ira-meta", tile);
    if (metas.length) { metas[metas.length - 1].textContent = meta || ""; }
  }

  var FLAG_CN = { yellow: "黄旗", caution: "全场黄旗", green: "绿旗", blue: "蓝旗",
                  white: "白旗", black: "黑旗", checkered: "格子旗", red: "红旗",
                  debris: "碎片", repair: "维修旗", disqualify: "取消资格",
                  yellow_waving: "挥动黄旗", caution_waving: "全场黄旗", furled: "卷起黑旗" };
  function flagCn(list) {
    if (!list || !list.length) { return "无旗"; }
    var priority = ["disqualify", "repair", "red", "black", "checkered", "white", "yellow_waving", "caution_waving", "yellow", "caution", "blue", "green"];
    var chosen = priority.find(function (name) { return list.indexOf(name) >= 0; }) || list[0];
    return FLAG_CN[chosen] || chosen;
  }

  /* ---------- 问答记录 ---------- */
  var logFilter = "all";
  var logCache = null;

  async function loadLog() {
    try {
      logCache = await api("/api/log?limit=100");
      renderLog();
    } catch (e) {
      logCache = { qa: [], miss: [], console: [] };
      renderLog();
    }
  }

  function renderLog() {
    var panel = $('[data-view-panel="log"]');
    if (!panel || !logCache) { return; }
    var rows = [];
    if (logFilter === "all") {
      rows = logCache.qa.map(function (r) {
        var path = r.layer || (r.text ? "phrase" : "refuse");
        var msRaw = r.first_audio_ms !== undefined ? r.first_audio_ms
                                                   : (r.asr_ms !== undefined ? r.asr_ms : null);
        return {
          time: (r.ts || "").slice(11) || r.ts || "",
          q: r.q, a: r.text || (r.reason ? "（" + r.reason + "）" : ""),
          path: path,
          ms: msRaw === null ? "" : (msRaw < 100 ? msRaw + " ms" : (msRaw / 1000).toFixed(2) + " s"),
        };
      });
    } else if (logFilter === "miss") {
      rows = logCache.miss.map(function (r) {
        return { time: (r.ts || "").slice(11), q: r.q, a: r.reason || "",
                 path: r.layer || "miss", ms: "" };
      });
    } else {
      rows = logCache.miss.filter(function (r) { return r.layer === "refuse"; })
        .map(function (r) {
          return { time: (r.ts || "").slice(11), q: r.q, a: "没听明白（已拒绝）", path: "refuse", ms: "" };
        });
    }
    var host = $(".ira-logrows", panel);
    if (!host) { return; }
    if (!rows.length) {
      host.innerHTML = '<div class="ira-empty"><strong>还没有记录</strong>' +
        '<span class="ira-meta">按 PTT 问一句，或到「意图调试台」用打字试</span></div>';
    } else {
      host.innerHTML = rows.map(function (r) {
        var cls = r.path === "phrase" ? "l1" : (r.path === "l1_keyword" ? "l2" : "l3");
        return '<div class="ira-logrow" data-logrow data-path="' + esc(r.path) + '">' +
          '<span class="ira-logtime ira-num">' + esc(r.time) + "</span>" +
          '<div><div class="ira-logq">「' + esc(r.q) + '」</div>' +
          '<div class="ira-loga">' + esc(r.a) + "</div></div>" +
          '<div class="ira-logside">' + badge(cls, esc(r.path)) +
          '<span class="ira-num">' + esc(r.ms) + "</span></div></div>";
      }).join("");
    }
    var vmeta = $(".ira-vmeta", panel);
    if (vmeta) {
      vmeta.innerHTML = '<span class="ira-chip">最近 ' + logCache.qa.length + " 条</span>" +
        '<span class="ira-chip">未识别 ' +
        logCache.miss.filter(function (r) { return r.layer === "refuse"; }).length + " 笔</span>";
    }
    $$("[data-logfilter]", panel).forEach(function (b) {
      var on = b.getAttribute("data-logfilter") === logFilter;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
      b.onclick = function () { logFilter = b.getAttribute("data-logfilter"); renderLog(); };
    });
  }

  /* ---------- 意图调试台 ---------- */
  function renderTrace(tr) {
    var panel = $('[data-view-panel="debug"]');
    var rows = $$(".ira-frow", panel);
    var phrase = tr.phrase || {};
    // 行1：短语库
    var s1 = $("[data-dbg-l1-state]", rows[0]);
    var d1 = $("[data-dbg-l1-detail]", rows[0]);
    s1.textContent = phrase.intent ? "✓ 命中 " + phrase.intent : "✗ 拒绝";
    s1.setAttribute("data-state", phrase.intent ? "hit" : "miss");
    d1.textContent = phrase.intent
      ? "短语「" + phrase.phrase + "」得分 " + phrase.score + "（阈值 " + phrase.accept + "，领先 " + phrase.margin + "）"
      : phrase.reason;
    // 行2：关键词兜底
    var s2 = $("[data-dbg-l2-state]", rows[1]);
    var d2 = $("[data-dbg-l2-detail]", rows[1]);
    if (tr.phrase && tr.phrase.intent) {
      s2.textContent = "— 未参与（短语库已命中）";
      s2.setAttribute("data-state", "skip");
      d2.textContent = "短语库说话算数；关键词兜底只在她拿不准时才上";
    } else if (tr.l1 && tr.l1.hit) {
      s2.textContent = "✓ 兜底命中 " + tr.l1.intent;
      s2.setAttribute("data-state", "hit");
      d2.textContent = tr.l1.why;
    } else if (tr.strict) {
      s2.textContent = "— 语音严格模式，跳过";
      s2.setAttribute("data-state", "skip");
      d2.textContent = "对不上短语库就直接拒绝，不猜";
    } else {
      s2.textContent = tr.l1 ? "✗ 未命中" : "— 关闭";
      s2.setAttribute("data-state", tr.l1 ? "miss" : "skip");
      d2.textContent = tr.l1 ? "关键词一个都没中" : "config 里关掉了关键词兜底";
    }
    // 行3：遥测路由
    var s3 = $("[data-dbg-l3-state]", rows[2]);
    var d3 = $("[data-dbg-l3-detail]", rows[2]);
    if (!tr.intent) {
      s3.textContent = "— 未进入路由"; s3.setAttribute("data-state", "skip");
      d3.textContent = "先要有意图才能查遥测";
    } else if (tr.decision.ok) {
      s3.textContent = "✓ 可以回答"; s3.setAttribute("data-state", "hit");
      d3.textContent = tr.decision.reason;
    } else {
      s3.textContent = "✗ 答不了"; s3.setAttribute("data-state", "miss");
      d3.textContent = tr.decision.reason;
    }
    // 判定结果
    var badgeEl = $("[data-dbg-intent]", panel);
    badgeEl.textContent = tr.intent ? tr.intent + " · " + (tr.layer || "") : "拒绝";
    badgeEl.className = "ira-badge " + (tr.intent ? "l1" : "l3");
    setText("[data-dbg-totaltime]", "判定耗时 " + tr.intent_ms + " ms", panel);

    // 遥测快照
    var teleHost = $("[data-dbg-tele]", panel);
    var S = tr.snapshot || {};
    var picks = [
      ["连接", S.connected ? "已连接（" + S.source + "）" : "未连接"],
      ["油量", S.fuel_level === null || S.fuel_level === undefined ? "—"
        : fmt(S.fuel_level, " L", 1) + " · 够跑 " + fmt(S.laps_of_fuel, " 圈", 1)],
      ["名次", S.position === null || S.position === undefined ? "—" : "P" + S.position],
      ["圈数", "第 " + (S.lap ?? "—") + " 圈 · 剩 " + (S.laps_remain ?? "—") + " 圈"],
      ["上圈/最佳", fmt(S.lap_last, " s", 3) + " / " + fmt(S.lap_best, " s", 3)],
      ["后车", fmt(S.gap_behind_s, " s", 1)],
      ["赛道", (S.track_wetness === null || S.track_wetness === undefined ? "—"
        : (S.track_wetness === 0 ? "未知" : (S.track_wetness >= 3 ? "湿" : "干"))) + " · " + flagCn(S.flags)],
      ["事故点", S.incident_count ?? "—"],
    ];
    teleHost.innerHTML = '<div class="ira-kvgrid">' + picks.map(function (p) {
      return '<div class="ira-kv"><span>' + esc(p[0]) + '</span><span class="ira-num">' +
        esc(p[1]) + "</span></div>";
    }).join("") + "</div>";

    // 播报合成
    var plan = tr.plan || [];
    setText("[data-dbg-msgid]", plan.map(function (p) { return p.msgId; }).join(" + ") || "—", panel);
    setText("[data-dbg-file]", plan.map(function (p) { return p.file || "缺件"; }).join(" + ") || "—", panel);
    var note = plan.length
      ? plan.map(function (p) {
          return p.msgId + "「" + (p.text || "缺件") + "」" + (p.variants ? " · " + p.variants + " 变体" : "");
        }).join("；")
      : "没有可播的内容";
    setText("[data-dbg-note]", note, panel);
    var audition = $("[data-audition]", panel);
    audition.disabled = !plan.length;
    audition.textContent = "试听（真播）";
    audition.onclick = async function () {
      audition.disabled = true;
      audition.textContent = "播报中…";
      try { await api("/api/ask", { text: tr.text, play: true }); }
      finally { audition.disabled = false; audition.textContent = "试听（真播）"; }
    };
  }

  async function runQuery(text) {
    try {
      var tr = await api("/api/ask", { text: text, play: false });
      renderTrace(tr);
      var input = $("#ira-q");
      if (input) { input.value = text; }
    } catch (e) {
      reportError(e);
    }
  }

  function bindDebug() {
    var form = $("#ira-qform");
    var send = $("#ira-send");
    var input = $("#ira-q");
    if (form) { form.addEventListener("submit", function (e) { e.preventDefault(); runQuery(input.value); }); }
    if (send) { send.addEventListener("click", function () { runQuery(input.value); }); }
    $$("[data-q]").forEach(function (b) {
      b.addEventListener("click", function () { runQuery(b.getAttribute("data-q")); });
    });
  }

  /* ---------- 统计 ---------- */
  async function loadStats() {
    var panel = $('[data-view-panel="stats"]');
    if (!panel) { return; }
    var s;
    try { s = await api("/api/stats"); } catch (e) { return; }
    var buckets = s.buckets || [0, 0, 0, 0];
    var maxB = Math.max(1, Math.max.apply(null, buckets));
    var totalLayers = Object.keys(s.layers || {}).reduce(function (a, k) { return a + s.layers[k]; }, 0) || 1;
    var cls = { phrase: "#0E9F6E", l1_keyword: "#4A9BE8", refuse: "#6E7F97",
                "?": "#B9C4D2" };
    var label = { phrase: "短语库", l1_keyword: "关键词兜底", refuse: "拒绝",
                  "?": "旧记录（未分类）" };

    var grid = $(".ira-statgrid", panel);
    if (grid) {
      var avg = s.avg_first_audio_ms;
      grid.innerHTML = statCard("提问次数", s.total, "最近 1000 条问答")
        + statCard("平均首包", avg === null ? "—" : (avg < 1000 ? avg : (avg / 1000).toFixed(2)),
                   avg === null ? "还没有数据" : (avg < 1000 ? "毫秒 · 目标 300 以内" : "秒 · 目标 0.3 以内"))
        + statCard("未成功", s.miss, "未识别、数据缺失或播放失败")
        + statCard("未成功率", ((s.miss / Math.max(s.total, 1)) * 100).toFixed(0), "%");
    }
    var bars = $(".ira-bars", panel);
    if (bars) {
      var names = ["≤ 0.25 s", "0.25–0.35", "0.35–0.7", "> 0.7 s"];
      bars.innerHTML = buckets.map(function (v, i) {
        return '<div class="ira-bar"><span class="ira-barval ira-num">' + v + "</span>" +
          '<div class="ira-barfill" style="height:' + Math.round(v / maxB * 100) + '%"></div>' +
          '<span class="ira-barlabel">' + names[i] + "</span></div>";
      }).join("");
    }
    var share = $(".ira-share", panel);
    if (share) {
      share.innerHTML = Object.keys(s.layers || {}).map(function (k) {
        return '<span style="width:' + (s.layers[k] / totalLayers * 100) + "%;background:" +
          (cls[k] || "#1A6FE0") + '"></span>';
      }).join("") || '<span style="width:100%;background:#6E7F97"></span>';
    }
    var legend = $(".ira-legend", panel);
    if (legend) {
      legend.innerHTML = Object.keys(s.layers || {}).map(function (k) {
        return "<span><span class='ira-swatch' style='background:" + (cls[k] || "#1A6FE0") +
          "'></span>" + (label[k] || k) + " " + Math.round(s.layers[k] / totalLayers * 100) + "%</span>";
      }).join("") || "<span>还没有数据</span>";
    }
    var missCard = $("[data-misscard]", panel);
    if (missCard && s.top_miss && s.top_miss.length) {
      missCard.innerHTML = '<span class="ira-label">没听明白的问法（补进 intents_examples.yaml 就好）</span>'
        + s.top_miss.map(function (m) {
          return '<div class="ira-missrow"><span>「' + esc(m[0]) + '」</span>' +
            '<span class="ira-meta" style="margin-left:auto">' + m[1] + " 次</span></div>";
        }).join("");
    }
    if (missCard && (!s.top_miss || !s.top_miss.length)) {
      missCard.innerHTML = '<span class="ira-label">没听明白的问法（补进 intents_examples.yaml 就好）</span>'
        + '<div class="ira-missrow"><span class="ira-meta">暂无未命中记录</span></div>';
    }
    var foot = $(".ira-metarow", panel);
    if (foot) {
      foot.innerHTML = '<span class="ira-meta">识别与匹配全程本地执行 · 零联网调用 · 短语库 ' +
        (window.__state ? window.__state.config.intent.library_size : "—") + " 条</span>";
    }
  }

  function statCard(label, value, meta) {
    return '<div class="ira-stat"><span class="ira-label">' + label + '</span><div class="ira-num">' +
      value + '</div><span class="ira-meta">' + meta + "</span></div>";
  }

  /* ---------- 设置页 ---------- */
  async function loadSettings() {
    var st = window.__state || await api("/api/state");
    var dev;
    try { dev = await api("/api/devices"); }
    catch (e) { dev = {inputs: [], outputs: []}; reportError(e); }
    window.__devices = dev;
    renderIntentPanel(st, dev);
    renderAsrPanel(st, dev);
    renderPackPanel(st);
    renderRoutePanel(st, dev);
    renderAiPanel(st);
  }

  async function save(patch, hintEl, note) {
    var r = await api("/api/config", { patch: patch });
    if (hintEl) {
      hintEl.textContent = "已保存" + (r.restart_hint && r.restart_hint.length
        ? "（" + r.restart_hint.join("、") + " 重启后生效）" : "（已生效）");
      setTimeout(function () { hintEl.textContent = note || ""; }, 2600);
    }
    var st = await api("/api/state");
    renderState(st);
  }

  function field(label, inner, hint) {
    return '<div class="ira-field"><label>' + label + "</label>" + inner +
      (hint ? '<span class="ira-hint">' + hint + "</span>" : "") + "</div>";
  }

  function renderIntentPanel(st, dev) {
    var panel = $('[data-view-panel="model"]');
    var c = st.config.intent;
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    card.innerHTML = '<div class="ira-form">' +
      field("短语库", '<input class="ira-input ira-num" value="' + c.library_size +
        ' 条（intents_examples.yaml）" readonly>',
        "命中要求：得分 ≥ " + c.accept + " 且领先第二名 ≥ " + c.margin) +
      field("语音严格模式", '<select class="ira-select" id="ira-strict">' +
        '<option value="1"' + (c.voice_strict ? " selected" : "") + ">开：对不上就拒绝（推荐）</option>" +
        '<option value="0"' + (c.voice_strict ? "" : " selected") + ">关：允许关键词兜底</option></select>",
        "开车场景建议一直开着") +
      field("键盘关键词兜底", '<select class="ira-select" id="ira-l1">' +
        '<option value="1"' + (c.l1_keyword ? " selected" : "") + ">开</option>" +
        '<option value="0"' + (c.l1_keyword ? "" : " selected") + ">关</option></select>",
        "语音严格模式关闭时，语音也允许关键词兜底") +
      "</div>";
    $("#ira-strict", panel).onchange = function () {
      save({ intent: { voice_strict: this.value === "1" } }, hint);
    };
    $("#ira-l1", panel).onchange = function () {
      save({ intent: { l1_keyword: { enabled: this.value === "1" } } }, hint);
    };
  }

  function deviceOptions(list, current, autoLabel) {
    var out = '<option value="">' + autoLabel + "</option>";
    list.forEach(function (d) {
      out += '<option value="' + d.index + '"' +
        (String(current) === String(d.index) ? " selected" : "") + ">" +
        d.index + " · " + esc(d.name) + "</option>";
    });
    return out;
  }

  function renderAsrPanel(st, dev) {
    var panel = $('[data-view-panel="asr"]');
    var a = st.config.asr, p = st.config.ptt;
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    var models = [["sense-voice", "SenseVoice（随软件提供）"],
                  ["zipformer-ctc", "Zipformer-CTC（需另外安装模型）"],
                  ["streaming-zip", "流式 Zipformer（需另外安装模型）"]];
    card.innerHTML = '<div class="ira-form">' +
      field("ASR 模型", '<select class="ira-select" id="ira-asrmodel2">' +
        models.map(function (m) {
          return '<option value="' + m[0] + '"' + (a.model === m[0] ? " selected" : "") + ">" + m[1] + "</option>";
        }).join("") + "</select>", "保存后自动重新载入；缺少模型会提示错误") +
      field("线程", '<input class="ira-input ira-num" id="ira-asrthreads2" value="' +
        (a.num_threads || 4) + '" inputmode="numeric">', "留 CPU 给游戏，4 就够") +
      field("PTT 键位", '<div class="ira-inline"><input class="ira-input ira-num" id="ira-vk" value="' +
        esc(st.ptt.binding_label) + '" readonly><button class="ira-btn" type="button" id="ira-capture">采集键盘键</button></div>',
        "支持键盘、鼠标侧键和方向盘按钮；游戏在前台时同样可以使用") +
      field('方向盘按钮','<button class="ira-btn" type="button" id="ira-wheel-capture">采集方向盘按钮</button>',
        '<span id="ira-wheel-devices">'+wheelDeviceText(dev)+'</span>') +
      field("麦克风", '<select class="ira-select" id="ira-micdev2">' +
        deviceOptions(dev.inputs, p.input_device, "系统默认") + "</select>",
        "系统默认一般就行") +
      field("误触阈值", '<input class="ira-input ira-num" id="ira-hold" value="' +
        (p.min_hold_ms || 250) + '" inputmode="numeric">', "按住短于这个毫秒数当误触丢弃") +
      '<div class="ira-metarow"><button class="ira-btn" type="button" id="ira-listen-toggle">' +
      (st.ptt.running ? "停止监听" : "启动监听") + "</button>" +
      '<button class="ira-btn" type="button" id="ira-simulate">模拟按下（真录音）</button>' +
      "</div></div>";

    $("#ira-asrmodel2", panel).onchange = function () {
      save({ asr: { model: this.value } }, hint);
    };
    $("#ira-asrthreads2", panel).onchange = function () {
      save({ asr: { num_threads: parseInt(this.value, 10) || 4 } }, hint);
    };
    $("#ira-micdev2", panel).onchange = function () {
      save({ ptt: { input_device: this.value === "" ? null : parseInt(this.value, 10) } },
        hint);
    };
    $("#ira-hold", panel).onchange = function () {
      save({ ptt: { min_hold_ms: parseInt(this.value, 10) || 250 } }, hint);
    };
    $("#ira-capture", panel).onclick = async function () {
      var btn = this;
      btn.disabled = true; btn.textContent = "按键中…（6 秒内按一下）";
      var got;
      try { got = await api("/api/ptt/capture", { timeout_s: 6,kind:'keyboard' }); }
      finally { btn.disabled = false; btn.textContent = "采集键盘键"; }
      if (got && got.vk) {
        $("#ira-vk", panel).value = got.name + "（0x" + got.vk.toString(16).toUpperCase() + "）";
        save({ ptt: { vk: got.vk,binding:null } }, hint);
      }
    };
    $('#ira-wheel-capture',panel).onclick=async function(){
      var btn=this;btn.disabled=true;btn.textContent='8 秒内按一下方向盘按钮';
      try {
        var got=await api('/api/ptt/capture',{kind:'wheel',timeout_s:8});
        $('#ira-vk',panel).value=got.name;
        await save({ptt:{binding:got.binding}},hint);
      } finally {btn.disabled=false;btn.textContent='采集方向盘按钮';}
    };
    $("#ira-listen-toggle", panel).onclick = async function () {
      var on = (window.__state.ptt.running);
      await api("/api/listen", { running: !on });
      loadSettings();
    };
    $("#ira-simulate", panel).onclick = async function () {
      var btn = this;
      btn.disabled = true; btn.textContent = "按住 2 秒…（对着麦克风说话）";
      var r;
      try { r = await api("/api/ptt/simulate", { seconds: 2 }); }
      finally { btn.disabled = false; btn.textContent = "模拟按下（真录音）"; }
      if (r.error) { alert(r.error); }
    };
  }


  // ---------------------------------------------------------------- AI 分析
  var AI_PRESETS = [
    {key: "deepseek", label: "DeepSeek", url: "https://api.deepseek.com", model: "deepseek-chat"},
    {key: "ark", label: "火山方舟", url: "https://ark.cn-beijing.volces.com/api/v3", model: ""},
    {key: "moonshot", label: "月之暗面", url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k"},
    {key: "ollama", label: "本地 Ollama", url: "http://127.0.0.1:11434/v1", model: "qwen2.5:7b"},
    {key: "custom", label: "自定义", url: "", model: ""}
  ];

  function aiPresetOptions(current) {
    return AI_PRESETS.map(function (p) {
      return '<option value="' + p.key + '"' +
        (p.key === (current || "custom") ? " selected" : "") + ">" + p.label + "</option>";
    }).join("");
  }

  function renderAiPanel(st) {
    var panel = $('[data-view-panel="ai"]');
    if (!panel) return;
    var a = (st.config && st.config.ai) || {};
    var tts = a.tts || {};
    var s = st.ai || {};
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    if (!card) return;

    var statusText = !a.enabled ? "未启用"
      : (s.configured ? (s.available ? "就绪" : "已暂停（连续超时）") : "未配置 API");

    card.innerHTML = '<div class="ira-form">' +
      field("启用 AI 分析",
        '<select class="ira-select" id="ira-ai-on">' +
        '<option value="1"' + (a.enabled ? " selected" : "") + ">开</option>" +
        '<option value="0"' + (a.enabled ? "" : " selected") + ">关</option></select>",
        "当前状态：" + statusText + "。关掉后行为与之前完全一致。") +
      field("档位",
        '<select class="ira-select" id="ira-ai-mode">' +
        '<option value="whitelist"' + (a.mode === "whitelist" ? " selected" : "") + ">C · 白名单（推荐）</option>" +
        '<option value="analysis_key"' + (a.mode === "analysis_key" ? " selected" : "") + ">B · 按住分析键才用</option>" +
        '<option value="full"' + (a.mode === "full" ? " selected" : "") + ">A · 规则答不上就接管</option>" +
        "</select>",
        "C 最省：只对分析型问题与白名单意图调模型。A 每个问题都可能慢 2 秒。") +
      "</div><hr class='ira-hr'>" +
      "<span class='ira-label'>模型接入</span>" +
      "<div class='ira-form'>" +
      field("预设", '<select class="ira-select" id="ira-ai-preset">' +
        aiPresetOptions(a.provider) + "</select>",
        "只是帮你填地址，填完可以改") +
      field("接口地址", '<input class="ira-input" id="ira-ai-url" value="' +
        esc(a.base_url || "") + '" placeholder="https://api.deepseek.com">',
        "按 OpenAI Chat Completions 协议调用") +
      field("模型名", '<input class="ira-input" id="ira-ai-model" value="' +
        esc(a.model || "") + '" placeholder="deepseek-chat">', "") +
      field("API Key", '<input class="ira-input" id="ira-ai-key" type="password" placeholder="' +
        (a.api_key_set ? "已配置，留空则不修改" : "粘贴你的 key") + '">',
        "明文存在 config.local.yaml，不进安装包、不进日志。也可用环境变量 LITTLE_NAVIGATOR_AI_KEY") +
      "</div>" +
      "<div class='ira-row' style='margin-top:10px'>" +
      "<button class='ira-btn' id='ira-ai-save'>保存</button>" +
      "<button class='ira-btn ghost' id='ira-ai-test'>测试连接</button>" +
      "<span class='ira-savehint' id='ira-ai-testout'></span></div>" +
      "<hr class='ira-hr'>" +
      "<span class='ira-label'>分析键（B 档用）</span>" +
      "<div class='ira-form'>" +
      field("当前绑定",
        '<input class="ira-input" id="ira-ai-keyname" readonly value="' +
        (a.analysis_key_bound ? "已绑定" : "未绑定") + '">',
        "按住它提问会直接走 AI；与 PTT 是两套按键") +
      "</div>" +
      "<div class='ira-row'><button class='ira-btn ghost' id='ira-ai-keycap'>采集按键</button>" +
      "<span class='ira-savehint' id='ira-ai-keyout'></span></div>" +
      "<hr class='ira-hr'>" +
      "<span class='ira-label'>语音合成（可选）</span>" +
      "<div class='ira-form'>" +
      field("适配器",
        '<select class="ira-select" id="ira-tts-adapter">' +
        '<option value="off"' + (tts.adapter === "off" ? " selected" : "") + ">关闭（只出文字）</option>" +
        '<option value="doubao"' + (tts.adapter === "doubao" ? " selected" : "") + ">豆包</option>" +
        '<option value="openai"' + (tts.adapter === "openai" ? " selected" : "") + ">OpenAI 兼容</option>" +
        "</select>",
        "不配也能用：AI 回答显示在屏幕上") +
      field("地址", '<input class="ira-input" id="ira-tts-url" value="' +
        esc(tts.base_url || "") + '" placeholder="OpenAI 兼容才需要">', "") +
      field("音色 / speaker", '<input class="ira-input" id="ira-tts-speaker" value="' +
        esc(tts.speaker || tts.voice || "") + '" placeholder="豆包填音色 ID，OpenAI 填 voice">', "") +
      field("API Key", '<input class="ira-input" id="ira-tts-key" type="password" placeholder="' +
        (tts.api_key_set ? "已配置，留空则不修改" : "粘贴 TTS 的 key") + '">', "") +
      "</div>" +
      "<div class='ira-row' style='margin-top:10px'>" +
      "<button class='ira-btn' id='ira-tts-save'>保存</button>" +
      "<button class='ira-btn ghost' id='ira-tts-test'>试听</button>" +
      "<span class='ira-savehint' id='ira-tts-out'></span></div>" +
      "<hr class='ira-hr'>" +
      "<div class='ira-row'><button class='ira-btn ghost' id='ira-learn-clear'>清空学习库</button>" +
      "<span class='ira-savehint' id='ira-learn-out'></span></div>";

    bindAiPanel(panel, hint);
    renderAiReview();
  }

  function aiPatchFromPanel(panel) {
    var p = {
      enabled: $("#ira-ai-on", panel).value === "1",
      mode: $("#ira-ai-mode", panel).value,
      provider: $("#ira-ai-preset", panel).value,
      base_url: $("#ira-ai-url", panel).value.trim(),
      model: $("#ira-ai-model", panel).value.trim()
    };
    var key = $("#ira-ai-key", panel).value.trim();
    if (key) p.api_key = key;
    return { ai: p };
  }

  function ttsPatchFromPanel(panel) {
    var t = {
      adapter: $("#ira-tts-adapter", panel).value,
      base_url: $("#ira-tts-url", panel).value.trim(),
      speaker: $("#ira-tts-speaker", panel).value.trim()
    };
    var key = $("#ira-tts-key", panel).value.trim();
    if (key) t.api_key = key;
    return { ai: { tts: t } };
  }

  function bindAiPanel(panel, hint) {
    var preset = $("#ira-ai-preset", panel);
    if (preset) {
      preset.addEventListener("change", function () {
        var p = AI_PRESETS.filter(function (x) { return x.key === preset.value; })[0];
        if (!p) return;
        if (p.url) $("#ira-ai-url", panel).value = p.url;
        if (p.model) $("#ira-ai-model", panel).value = p.model;
      });
    }
    var saveBtn = $("#ira-ai-save", panel);
    if (saveBtn) {
      saveBtn.addEventListener("click", async function () {
        try { await save(aiPatchFromPanel(panel), hint, "AI 设置"); await loadSettings(); }
        catch (e) { reportError(e); }
      });
    }
    var testBtn = $("#ira-ai-test", panel);
    if (testBtn) {
      testBtn.addEventListener("click", async function () {
        var out = $("#ira-ai-testout", panel);
        out.textContent = "测试中…";
        try {
          var r = await api("/api/ai/test", {});
          out.textContent = r.ok
            ? ("连通，往返 " + r.latency_ms + " ms" + (r.latency_ms > 1500 ? "（偏慢，比赛中可能来不及）" : ""))
            : ("失败：" + (r.error || "未知错误"));
        } catch (e) { out.textContent = "失败：" + e.message; }
      });
    }
    var capBtn = $("#ira-ai-keycap", panel);
    if (capBtn) {
      capBtn.addEventListener("click", async function () {
        var out = $("#ira-ai-keyout", panel);
        out.textContent = "按下你想用作分析键的按钮…";
        try {
          var got = await api("/api/ptt/capture", { kind: "keyboard", timeout_s: 6 });
          if (!got || got.error) { out.textContent = got && got.error || "没等到按键"; return; }
          await save({ ai: { analysis_key: { vk: got.vk, label: got.label || "" } } },
                     hint, "分析键");
          out.textContent = "已绑定：" + (got.label || ("0x" + got.vk.toString(16)));
          await loadSettings();
        } catch (e) { out.textContent = "采集失败：" + e.message; }
      });
    }
    var ttsSave = $("#ira-tts-save", panel);
    if (ttsSave) {
      ttsSave.addEventListener("click", async function () {
        try { await save(ttsPatchFromPanel(panel), hint, "语音合成"); await loadSettings(); }
        catch (e) { reportError(e); }
      });
    }
    var ttsTest = $("#ira-tts-test", panel);
    if (ttsTest) {
      ttsTest.addEventListener("click", async function () {
        var out = $("#ira-tts-out", panel);
        out.textContent = "合成中…";
        try {
          var r = await api("/api/ai/tts/test", {});
          out.textContent = r.ok
            ? ("成功 " + r.bytes + " 字节，" + r.latency_ms + " ms")
            : ("失败：" + (r.error || "未知错误"));
        } catch (e) { out.textContent = "失败：" + e.message; }
      });
    }
    var clearBtn = $("#ira-learn-clear", panel);
    if (clearBtn) {
      clearBtn.addEventListener("click", async function () {
        if (!confirm("确定清空学习库？已沉淀的问答会被删除。")) return;
        var out = $("#ira-learn-out", panel);
        try {
          await api("/api/ai/learn/clear", {});
          out.textContent = "已清空";
          renderAiReview();
        } catch (e) { out.textContent = "失败：" + e.message; }
      });
    }
  }

  async function renderAiReview() {
    var card = $(".ira-review");
    if (!card) return;
    var data;
    try { data = await api("/api/ai/review"); }
    catch (e) { card.innerHTML = "<span class='ira-label'>读不到复盘数据</span>"; return; }
    var rows = data.rows || [];
    if (!rows.length) {
      card.innerHTML = "<span class='ira-label'>本场还没有走过 AI 的问答。" +
        "开启 AI 后提出问题，这里会列出每次的答案，供你赛后确认。</span>";
      return;
    }
    card.innerHTML = rows.slice().reverse().map(function (r) {
      return "<div class='ira-reviewrow' data-id='" + esc(r.id || "") + "'>" +
        "<div class='ira-reviewq'>" + esc(r.q || "") + "</div>" +
        "<div class='ira-reviewa'>" + esc(r.answer || "") + "</div>" +
        "<div class='ira-reviewmeta'>" + esc(r.source || "") +
        " · " + (r.latency_ms || 0) + " ms</div>" +
        "<div class='ira-row'>" +
        "<button class='ira-btn small' data-mark='ok'>正确，入库</button>" +
        "<button class='ira-btn small ghost' data-mark='bad'>错误</button>" +
        "<button class='ira-btn small ghost' data-mark='meh'>没帮助</button>" +
        "</div></div>";
    }).join("");
    Array.prototype.forEach.call(card.querySelectorAll("[data-mark]"), function (btn) {
      btn.addEventListener("click", async function () {
        var row = btn.closest(".ira-reviewrow");
        var id = row.getAttribute("data-id");
        var verdict = btn.getAttribute("data-mark");
        try {
          var r = await api("/api/ai/review/mark", { id: id, verdict: verdict });
          if (verdict === "ok") {
            row.querySelector(".ira-reviewmeta").textContent =
              r.stored_as === "template" ? "已存为条件模板"
                : (r.stored_as === "knowledge" ? "已存为知识" : "未通过校验，未入库");
          } else {
            row.querySelector(".ira-reviewmeta").textContent = "已记录";
          }
        } catch (e) { reportError(e); }
      });
    });
  }

  function renderPackPanel(st) {
    var panel = $('[data-view-panel="pack"]');
    var c = st.config.voice_pack;
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    card.innerHTML = '<div class="ira-form">' +
      field("语音包路径", '<input class="ira-input ira-num" value="' + esc(st.pack.dir) + '" readonly>',
        "v" + esc(st.pack.version) + " · " + st.pack.msgIds + " msgId · " +
        st.pack.clips + " 条 · " + (st.pack.rate
          ? Math.round(st.pack.rate / 100) / 10 + " kHz / 16-bit" : "16-bit")) +
      field("主音量", '<input class="ira-range" type="range" id="ira-vol" min="0" max="100" value="' +
        Math.round((st.config.playback.volume ?? 1) * 100) + '">' +
        '<span class="ira-rangeval"><span class="ira-num" id="ira-volval">' +
        Math.round((st.config.playback.volume ?? 1) * 100) + "</span> %</span>",
        "对齐游戏内播报音量用") +
      field("变体冷却", '<input class="ira-input ira-num" value="' + c.cooldown + '" readonly>',
        "同一条消息最近 N 条变体不再出现（跨场次记录）") +
      field("片段间隔", '<input class="ira-input ira-num" value="' + c.gap_ms + '" readonly>',
        "拼装片段之间的静音毫秒") +
      "</div><hr class='ira-hr'><span class='ira-label'>快速试听</span><div class='ira-logrows'>" +
      ["SPCC_YELOUT", "SPCC_FUELFINRACE", "SPCC_SESSIONDRY", "AST_REFUSE_HEARD", "AST_REFUSE_CANT"]
        .map(function (m) {
          return '<div class="ira-missrow"><code>' + m + "</code>" +
            '<button class="ira-btn" type="button" data-playmsgid="' + m +
            '" style="margin-left:auto">试听</button></div>';
        }).join("") + "</div>";

    var vol = $("#ira-vol", panel);
    vol.oninput = function () { setText("#ira-volval", this.value, panel); };
    vol.onchange = function () {
      save({ playback: { volume: parseInt(this.value, 10) / 100 } }, hint);
    };
    $$("[data-playmsgid]", panel).forEach(function (b) {
      b.onclick = async function () {
        b.disabled = true;
        try { var r = await api("/api/play", { msgId: b.getAttribute("data-playmsgid") });
          if (!r.ok) { throw new Error("试听失败，请检查音频文件或输出设备"); }
        } finally { b.disabled = false; }
      };
    });
  }

  function renderRoutePanel(st, dev) {
    var panel = $('[data-view-panel="route"]');
    var pb = st.config.playback;
    var duck = (pb.ducking || {});
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    card.innerHTML = '<div class="ira-form">' +
      field("播报输出设备", '<select class="ira-select" id="ira-outdev2">' +
        deviceOptions(dev.outputs, pb.device, "系统默认") + "</select>",
        "换成耳机 / 声卡都行；和游戏同一路输出最省事") +
      field("压低游戏音量", '<select class="ira-select" id="ira-duck2">' +
        '<option value="1"' + (duck.enabled ? " selected" : "") + ">开</option>" +
        '<option value="0"' + (duck.enabled ? "" : " selected") + ">关</option></select>",
        "播报时把 iRacing 的音量降 " + Math.abs(duck.amount_db || 12) + " dB，播完还原") +
      "</div><hr class='ira-hr'>" +
      "<div class='ira-flow'><span class='ira-node hot'>PTT 按住</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>开麦收音</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>SenseVoice 识别</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>短语库匹配</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>遥测路由</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>片段拼装</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node ok'>扬声器</span></div>" +
      "<hr class='ira-hr'><div class='ira-switchrow'>" +
      "<input class='ira-switch' type='checkbox' checked disabled>" +
      "<label><span class='ira-swtitle'>播放时自动静麦</span>" +
      "<span class='ira-hint'>固定开启：不按 PTT 不开麦，播报期间按键忽略 —— 这条不开就会自己问自己答</span></label></div>";

    $("#ira-outdev2", panel).onchange = function () {
      save({ playback: { device: this.value === "" ? null : parseInt(this.value, 10) } },
        hint);
    };
    $("#ira-duck2", panel).onchange = function () {
      save({ playback: { ducking: { enabled: this.value === "1" } } }, hint);
    };
  }

  /* ---------- 启动 ---------- */
  bindDebug();
  $('#window-close').onclick=async function(){await api('/api/quit',{});};
  $('#window-minimize').onclick=function(){if(window.pywebview){window.pywebview.api.minimize();}};
  bindPttButtons();
  relabelFunnel();
  showView("dash");

  function relabelFunnel() {
    var names = ["短语库", "关键词兜底", "遥测路由"];
    $$('[data-view-panel="debug"] .ira-frow').forEach(function (row, i) {
      var el = $(".ira-fname", row);
      if (el && names[i]) { el.innerHTML = names[i]; }
    });
  }

  function bindPttButtons() {
    $$("[data-ptt-next]").forEach(function (b) {
      b.textContent = "模拟按下";
      b.addEventListener("click", async function () {
        var old = b.textContent;
        b.disabled = true; b.textContent = "按住 2 秒…";
        var r;
        try { r = await api("/api/ptt/simulate", { seconds: 2 }); }
        finally { b.disabled = false; b.textContent = old; }
        if (r.error) { alert(r.error); }
      });
    });
  }

  function wheelDeviceText(dev) {
    return (dev.wheels || []).filter(function(d){return d.buttons>0;}).map(function(d){return esc(d.name)+' · '+d.buttons+' 个按钮';}).join('<br>') ||
      esc(dev.wheel_error || '未发现带按钮的控制器，请连接方向盘');
  }
  var wheelRefreshAt=0;
  async function poll() {
    try {
      renderState(await api("/api/state"));
      if ($('#ira-tab-asr').getAttribute('aria-selected')==='true' && Date.now()-wheelRefreshAt>3000) {
        wheelRefreshAt=Date.now();
        var wheels=await api('/api/wheels');
        var hint=$('#ira-wheel-devices');if(hint){hint.innerHTML=wheelDeviceText(wheels);}
      }
    } catch (e) {
      var app = $("#ira-app");
      if (app) { app.setAttribute("data-sim", "offline"); }
      var card = cardByLabel("连接");
      if (card) { setText("[data-conn-label]", "服务未连接", card); }
    }
  }
  poll();
  async function pollLoop() { await poll(); setTimeout(pollLoop, 900); }
  setTimeout(pollLoop, 900);
})();
