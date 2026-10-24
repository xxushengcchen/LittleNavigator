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
    setText('[data-error-message]', error && error.message || String(error));
    $('#ira-error').hidden = false;
  }
  window.addEventListener("unhandledrejection", function (event) { event.preventDefault(); reportError(event.reason); });

  /* ---------- 内联图标 ----------
     原先这里是一句 `i[data-lucide]{display:none}`：把所有图标占位藏起来，
     但项目从没引入 lucide 库，也没有内联 svg，于是顶栏左上角的标记
     成了一个空白方块（用户反馈「logo 不见了」）。
     现在内置需要的 13 个图标，渲染时把 <i data-lucide> 换成真实 SVG。 */
  var LUCIDE = {
    "activity": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2" /></svg>',
    "bug": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20v-9" /> <path d="M14 7a4 4 0 0 1 4 4v3a6 6 0 0 1-12 0v-3a4 4 0 0 1 4-4z" /> <path d="M14.12 3.88 16 2" /> <path d="M21 21a4 4 0 0 0-3.81-4" /> <path d="M21 5a4 4 0 0 1-3.55 3.97" /> <path d="M22 13h-4" /> <path d="M3 21a4 4 0 0 1 3.81-4" /> <path d="M3 5a4 4 0 0 0 3.55 3.97" /> <path d="M6 13H2" /> <path d="m8 2 1.88 1.88" /> <path d="M9 7.13V6a3 3 0 1 1 6 0v1.13" /></svg>',
    "check": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>',
    "cpu": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20v2" /> <path d="M12 2v2" /> <path d="M17 20v2" /> <path d="M17 2v2" /> <path d="M2 12h2" /> <path d="M2 17h2" /> <path d="M2 7h2" /> <path d="M20 12h2" /> <path d="M20 17h2" /> <path d="M20 7h2" /> <path d="M7 20v2" /> <path d="M7 2v2" /> <rect x="4" y="4" width="16" height="16" rx="2" /> <rect x="8" y="8" width="8" height="8" rx="1" /></svg>',
    "gauge": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m12 14 4-4" /> <path d="M3.34 19a10 10 0 1 1 17.32 0" /></svg>',
    "history": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" /> <path d="M3 3v5h5" /> <path d="M12 7v5l4 2" /></svg>',
    "mic": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19v3" /> <path d="M19 10v2a7 7 0 0 1-14 0v-2" /> <rect x="9" y="2" width="6" height="13" rx="3" /></svg>',
    "radio": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M16.247 7.761a6 6 0 0 1 0 8.478" /> <path d="M19.075 4.933a10 10 0 0 1 0 14.134" /> <path d="M4.925 19.067a10 10 0 0 1 0-14.134" /> <path d="M7.753 16.239a6 6 0 0 1 0-8.478" /> <circle cx="12" cy="12" r="2" /></svg>',
    "share-2": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="18" cy="5" r="3" /> <circle cx="6" cy="12" r="3" /> <circle cx="18" cy="19" r="3" /> <line x1="8.59" x2="15.42" y1="13.51" y2="17.49" /> <line x1="15.41" x2="8.59" y1="6.51" y2="10.49" /></svg>',
    "sparkles": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.017 2.814a1 1 0 0 1 1.966 0l1.051 5.558a2 2 0 0 0 1.594 1.594l5.558 1.051a1 1 0 0 1 0 1.966l-5.558 1.051a2 2 0 0 0-1.594 1.594l-1.051 5.558a1 1 0 0 1-1.966 0l-1.051-5.558a2 2 0 0 0-1.594-1.594l-5.558-1.051a1 1 0 0 1 0-1.966l5.558-1.051a2 2 0 0 0 1.594-1.594z" /> <path d="M20 2v4" /> <path d="M22 4h-4" /> <circle cx="4" cy="20" r="2" /></svg>',
    "timer": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="10" x2="14" y1="2" y2="2" /> <line x1="12" x2="15" y1="14" y2="11" /> <circle cx="12" cy="14" r="8" /></svg>',
    "volume-2": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z" /> <path d="M16 9a5 5 0 0 1 0 6" /> <path d="M19.364 18.364a9 9 0 0 0 0-12.728" /></svg>',
    "wifi-off": '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h.01" /> <path d="M8.5 16.429a5 5 0 0 1 7 0" /> <path d="M5 12.859a10 10 0 0 1 5.17-2.69" /> <path d="M19 12.859a10 10 0 0 0-2.007-1.523" /> <path d="M2 8.82a15 15 0 0 1 4.177-2.643" /> <path d="M22 8.82a15 15 0 0 0-11.288-3.764" /> <path d="m2 2 20 20" /></svg>',
  };
  function paintIcons(root) {
    (root || document).querySelectorAll("i[data-lucide]").forEach(function (el) {
      var name = el.getAttribute("data-lucide");
      var svg = LUCIDE[name];
      if (!svg) return;                 // 认不出的保持隐藏，不画错图案
      el.innerHTML = svg;
      el.removeAttribute("data-lucide");
      el.classList.add("ira-icon");
    });
  }

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
    tabs.forEach(function (b) { if (b.dataset.nav === id) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current'); b.removeAttribute('aria-selected'); });
    var wasOpen=document.body.classList.contains('nav-open');closeNav();if(wasOpen)$('#ira-main').focus({preventScroll:true});
    if (location.hash !== '#' + id) history.replaceState(null, '', '#' + id);
    $('#ira-main').scrollTop = 0;
    if (id === "log") loadLog();
    if (id === "stats") loadStats();
    if (id === "knowledge") loadKnowledge();
    if (id === "model" || id === "asr" || id === "pack" || id === "route" || id === "ai" || id === "pit") loadSettings();
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


  // ---- 赛前检查总线 -------------------------------------------------
  // 四个子系统是否就绪。这是这个界面最该一眼回答的问题：
  // 「我现在能不能开跑」——而不是分散在五个页面里让人自己拼。
  function busSet(key, state, label) {
    var el = document.querySelector('[data-bus="' + key + '"]');
    if (!el) { return; }
    el.setAttribute("data-state", state);
    var v = el.querySelector("[data-bus-val]");
    if (v) { v.textContent = label; }
  }

  function renderBus(st) {
    var tele = st.telemetry || {};
    var ptt = st.ptt || {};
    var ai = st.ai || {};
    var cc = st.car_control || {};

    if (st.sim) { busSet("link", "warn", "模拟数据"); }
    else if (tele.connected) { busSet("link", "ok", "已连接"); }
    else { busSet("link", "bad", "未连接"); }

    if (ptt.error) { busSet("mic", "bad", "出错"); }
    else if (ptt.running) {
      if (ptt.state === "recording") { busSet("mic", "warn", "录音中"); }
      else if (ptt.state === "thinking") { busSet("mic", "warn", "处理中"); }
      else { busSet("mic", "ok", "待命"); }
    } else { busSet("mic", "off", "未监听"); }

    if (!ai.enabled) { busSet("ai", "off", "未启用"); }
    else if (!ai.configured) { busSet("ai", "warn", "缺密钥"); }
    else if (ai.breaker === "open") { busSet("ai", "bad", "已熔断"); }
    else if (ai.mode === "full") { busSet("ai", "ok", "全接管"); }
    else if (ai.mode === "analysis_key") { busSet("ai", "ok", "按键触发"); }
    else { busSet("ai", "ok", "白名单"); }

    if (!cc.enabled) { busSet("car", "off", "未启用"); }
    else if (!cc.available) { busSet("car", "warn", "未连游戏"); }
    else { busSet("car", "ok", "可执行"); }
  }

  function formatTime(seconds, decimals) {
    if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds)) || seconds < 0) return '—';
    var minutes = Math.floor(seconds / 60), rest = (seconds % 60).toFixed(decimals || 0);
    return minutes + ':' + rest.padStart(decimals ? decimals + 3 : 2, '0');
  }
  function renderPitServices(st) {
    var t = st.telemetry || {}, flags = t.connected && Number.isInteger(t.pit_sv_flags) ? t.pit_sv_flags : null;
    var key = JSON.stringify([flags, t.pit_sv_fuel, t.fast_repairs_available]);
    $$('[data-pit-service]').forEach(function(host) {
      if (host.dataset.key === key) return;
      host.dataset.key = key;
      var wheels = [['左前',1],['右前',2],['左后',4],['右后',8]];
      host.innerHTML = '<div class="pit-diagram" aria-label="轮胎更换勾选状态">' + wheels.map(function(w,i) {
        return '<div class="pit-wheel" data-on="'+(flags!==null && !!(flags&w[1]))+'"><span>'+w[0]+'</span><span>'+ (flags===null?'未读到':flags&w[1]?'已勾选':'不更换') +'</span></div>' + (i===1?'<svg class="pit-car" viewBox="0 0 66 138" fill="none" aria-hidden="true"><path d="M17 8 24 3h18l7 5 7 28v68l-7 27H17l-7-27V36Z" stroke="currentColor"/><path d="m17 37 7-15h18l7 15-4 20H21Zm4 45h24l4 25H17Z" stroke="currentColor"/><path d="M10 59h46M10 78h46M4 126h58" stroke="currentColor"/></svg>':'');
      }).join('')+'</div><div class="pit-service-row"><span>加油目标</span><strong class="ira-num">'+(flags===null?'—':flags&16?fmt(t.pit_sv_fuel,' L',1):'不加油')+'</strong></div><div class="pit-service-row"><span>快速维修</span><strong>'+ (flags===null?'—':flags&64?'已勾选':'未勾选') +'</strong></div><div class="pit-service-row"><span>剩余快修</span><strong class="ira-num">'+(t.connected && t.fast_repairs_available!=null?(t.fast_repairs_available===255?'不限次数':t.fast_repairs_available+' 次'):'—')+'</strong></div>';
    });
  }
  function renderState(st) {
    renderBus(st);
    var tele = st.telemetry || {}, ptt = st.ptt || {}, online = !!tele.connected;
    document.body.classList.toggle('desktop-shell', !!st.desktop);
    $('#ira-app').setAttribute('data-sim',online?'online':'offline');
    $('#connection-lost').hidden = true;
    setText('.ira-session', st.sim ? '模拟遥测 · 仅供测试' : online ? 'iRacing 已连接 · 第 ' + (tele.lap ?? '—') + ' 圈' : '等待 iRacing 连接');
    setText('[data-ptt-label]',PTT_TEXT[ptt.state] || '待机');
    setText('[data-dashboard-subtitle]',st.sim?'当前使用模拟数据，操作结果不代表真实比赛。':online?'遥测已连接，你的领航员已在工程台。':'打开 iRacing 后自动连接，先把说话键和耳机准备好。');
    var notice = $('#connection-notice');
    notice.hidden = online && !st.sim && !ptt.error;
    notice.dataset.tone = ptt.error || st.sim ? 'warn' : 'neutral';
    notice.textContent = ptt.error ? '语音输入未就绪：' + ptt.error : st.sim ? '模拟模式 · 以下读数仅用于功能与界面测试。' : '尚未读到游戏数据。你仍可配置语音、耳机和 AI，连接后自动更新。';
    setText('[data-binding]',({'键盘 0xA3':'右 Ctrl','键盘 0xA2':'左 Ctrl','键盘 0xA1':'右 Shift'})[ptt.binding_label] || ptt.binding_label || '未绑定');
    var listen = $('#dashboard-listen');
    if(listen.dataset.networkFailed){delete listen.dataset.networkFailed;listen.disabled=false;}
    if (!listen.disabled) listen.textContent = ptt.running ? '暂停监听' : '开启监听';
    var rec = st.last, ans = $('.ira-answer');
    if (rec && (rec.q || rec.trace)) {
      var tr = rec.trace || {}, actionNames = {clear_tires:'已取消换胎',tyre:'已设置换胎',fuel:'已设置加油',clear_fr:'已取消快修',fast_repair:'已勾选快修',clear:'已清空进站服务',clear_fuel:'已取消加油',clear_ws:'已取消撕膜',tearoff:'已勾选撕膜'};
      var a = rec.ai_text || rec.text || (rec.ok && rec.cc_action ? actionNames[rec.cc_action.kind] : '') || rec.reason || (tr.plan || []).map(function(p){return p.text || '';}).join('') || '本次没有可用回答。';
      setText('.ira-q','“'+(rec.q || tr.text || '')+'”',ans);
      setText('.ira-a',a,ans);
      var state = $('[data-answer-status]');
      state.textContent = rec.trace && rec.ok==null ? '仅预览' : rec.ok ? (rec.kind==='operation'?'操作已完成':'已回应') : '未能完成';
      state.dataset.tone = rec.trace && rec.ok==null ? 'neutral' : rec.ok ? 'good' : 'bad';
      var path = rec.kind==='operation'?'进站操作':rec.ai_source==='model'?'AI 模型':rec.ai_source==='learn'?'学习库':rec.ai_source==='refused'?'AI 未回答':'本地规则';
      var latency = rec.ai_latency_ms != null ? 'AI 总耗时 '+fmt(rec.ai_latency_ms/1000,' s',2) : rec.first_audio_ms != null ? '开始播报 '+rec.first_audio_ms+' ms' : '';
      $('[data-answer-meta]').innerHTML = '<span class="ira-badge">'+esc(path)+'</span><span class="ira-meta ira-num">'+esc(latency)+'</span><span class="ira-meta">'+esc(rec.ts || '')+'</span>';
    }
    var tiles=$$('.ira-strip .ira-tile');
    fillTile(tiles[0],'油量',online?fmt(tele.fuel_level,'',1):'—','L',online && tele.laps_of_fuel!=null?'预计可跑 '+fmt(tele.laps_of_fuel,' 圈',1):'等待完整圈油耗');
    fillTile(tiles[1],'名次',online && tele.position!=null?'P'+tele.position:'—',online && tele.field_size!=null?'/ '+tele.field_size:'','当前全场名次');
    fillTile(tiles[2],'后车差距',online?fmt(tele.gap_behind_s,'',1):'—','s',online && tele.gap_behind_s!=null?(tele.gap_estimated?'按位置估计':'时间间隔'):'尚无有效读数');
    fillTile(tiles[3],'上一圈',online?formatTime(tele.lap_last,2):'—','','最近完成的一圈');
    setText('[data-lap]',online && tele.lap!=null?tele.lap:'—');
    setText('[data-remaining]',online?formatTime(tele.time_remain_s):'—');
    setText('[data-track-state]',online?flagCn(tele.flags)+' · '+(tele.track_wetness==null?'干湿未知':tele.track_wetness>=3?'湿地':'干地'):'赛道状态未知');
    setText('[data-snapshot-status]',online?(st.now || '已更新'):'尚无数据');
    renderPitServices(st);
    var foot=$('.ira-navfoot'), version=st.app && st.app.version || '—';
    if (foot.dataset.version!==version) {
      foot.dataset.version=version;
      foot.innerHTML='<strong>小小领航员 <span class="ira-num">v'+esc(version)+'</span></strong><div class="credit">xushengchen · Moondrop-Dox Racing</div><img src="/static/partner-logos.png" alt="Moondrop 水月雨、Gravity 陨月重工、Drag-On XT Racing Team 与 Moondrip Racing" loading="lazy">';
    }
    window.__state=st;
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
  // v1.6.0：日志里的 layer 是内部代号，界面一律显示中文；后端字段名保持不变。
  // 新代号漏配时宁可显示「其他」，也不要把内部代号漏到用户眼前。
  var LAYER_LABELS = {
    phrase: "短语库",
    l1_keyword: "关键词兜底",
    ai_disambiguate: "AI 意图仲裁",
    refuse: "未识别（已拒绝）",
    segment_full_split: "分段全量（固定话术）",
    miss: "未识别",
    l2: "关键词兜底",
    l3: "遥测路由",
    "?": "旧记录（未分类）"
  };
  function layerLabel(code) {
    if (code === undefined || code === null || code === "") { return "本地规则"; }
    return LAYER_LABELS[code] || "其他";
  }
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
          q: r.q, a: r.ai_text || r.text || (r.reason ? "（" + r.reason + "）" : ""),
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
          '<div class="ira-logside">' + badge(cls, esc(layerLabel(r.path))) +
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
    s1.textContent = phrase.intent ? "命中 " + phrase.intent : "未命中";
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
      s2.textContent = "关键词命中 " + tr.l1.intent;
      s2.setAttribute("data-state", "hit");
      d2.textContent = tr.l1.why;
    } else if (tr.strict) {
      s2.textContent = "— 语音严格模式，跳过";
      s2.setAttribute("data-state", "skip");
      d2.textContent = "对不上短语库就直接拒绝，不猜";
    } else {
      s2.textContent = tr.l1 ? "未命中" : "— 关闭";
      s2.setAttribute("data-state", tr.l1 ? "miss" : "skip");
      d2.textContent = tr.l1 ? "关键词一个都没中" : "config 里关掉了关键词兜底";
    }
    // 行3：遥测路由
    var s3 = $("[data-dbg-l3-state]", rows[2]);
    var d3 = $("[data-dbg-l3-detail]", rows[2]);
    if (tr.kind === "operation") {
      s3.textContent = tr.playback ? "改车执行结果" : "改车指令（尚未执行）";
      s3.setAttribute("data-state", tr.playback && !tr.playback.ok ? "miss" : "hit");
      d3.textContent = tr.playback ? tr.playback.reason : (tr.note || "执行会更改进站设置");
    } else if (!tr.intent) {
      s3.textContent = "— 未进入路由"; s3.setAttribute("data-state", "skip");
      d3.textContent = "先要有意图才能查遥测";
    } else if (tr.decision.ok) {
      s3.textContent = "可以回答"; s3.setAttribute("data-state", "hit");
      d3.textContent = tr.decision.reason;
    } else {
      s3.textContent = "答不了"; s3.setAttribute("data-state", "miss");
      d3.textContent = tr.decision.reason;
    }
    // 判定结果
    var badgeEl = $("[data-dbg-intent]", panel);
    badgeEl.textContent = tr.kind === "operation" ? "改车 · " + ((tr.cc_action || {}).kind || "")
      : tr.intent ? tr.intent + " · " + layerLabel(tr.layer) : "拒绝";
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
    var note = tr.playback && tr.playback.ai_text ? "AI 回答：" + tr.playback.ai_text : plan.length
      ? plan.map(function (p) {
          return p.msgId + "「" + (p.text || "缺件") + "」" + (p.variants ? " · " + p.variants + " 变体" : "");
        }).join("；")
      : "没有可播的内容";
    setText("[data-dbg-note]", note, panel);
    var audition = $("[data-audition]", panel);
    audition.disabled = false;
    audition.textContent = tr.kind === "operation" ? "执行并播报（更改进站设置）" : "问答并播报";
    audition.onclick = async function () {
      audition.disabled = true;
      audition.textContent = "播报中…";
      try { renderTrace(await api("/api/ask", { text: tr.text, play: true })); }
      finally { audition.disabled = false; audition.textContent = tr.kind === "operation" ? "执行并播报（更改进站设置）" : "问答并播报"; }
    };
  }

  var previewRequest = 0;
  async function runQuery(text) {
    var request=++previewRequest, button=$('#ira-send');
    $('#ira-q').value=text;
    $('[data-audition]').disabled=true;
    button.disabled=true;button.textContent='解析中…';
    try {
      var tr = await api("/api/ask", { text: text, play: false });
      if(request!==previewRequest || $('#ira-q').value!==text)return;
      renderTrace(tr);
      var input = $("#ira-q");
      if (input) { input.value = text; }
    } catch (e) {
      reportError(e);
    } finally {
      if(request===previewRequest){button.disabled=false;button.textContent='预览解析';}
    }
  }

  function bindDebug() {
    var form = $("#ira-qform");
    var send = $("#ira-send");
    var input = $("#ira-q");
    input.addEventListener('input',function(){previewRequest++;send.disabled=false;send.textContent='预览解析';$('[data-audition]').disabled=true;setText('[data-dbg-note]','文字已更改，请重新预览后再执行。');});
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
    // v1.6.0：颜色按内部代号取，文案统一走 layerLabel()，不再直接展示代号。
    var cls = { phrase: "var(--accent)", l1_keyword: "var(--muted)",
                ai_disambiguate: "var(--warn)", refuse: "var(--faint)",
                segment_full_split: "var(--control)", miss: "var(--faint)",
                "?": "var(--control)" };

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
      var step=Math.max(1,Math.ceil(maxB/4));
      Charts.bars(bars,{labels:names,values:buckets,yTicks:[0,step,step*2,step*3,step*4,step*5],height:190,format:Charts.fmt.int});
    }
    var share = $(".ira-share", panel);
    if (share) {
      share.innerHTML = Object.keys(s.layers || {}).map(function (k) {
        return '<span style="width:' + (s.layers[k] / totalLayers * 100) + "%;background:" +
          (cls[k] || "#1A6FE0") + '"></span>';
      }).join("") || '<span class="ira-meta">暂无分类记录</span>';
    }
    var legend = $(".ira-legend", panel);
    if (legend) {
      legend.innerHTML = Object.keys(s.layers || {}).map(function (k) {
        return "<span><span class='ira-swatch' style='background:" + (cls[k] || "#1A6FE0") +
          "'></span>" + layerLabel(k) + " " + Math.round(s.layers[k] / totalLayers * 100) + "%</span>";
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
    var id = $("[data-nav][aria-current=page]").dataset.nav;
    var st = window.__state || await api("/api/state");
    var dev;
    try { dev = await api("/api/devices"); }
    catch (e) { dev = {inputs: [], outputs: []}; reportError(e); }
    window.__devices = dev;
    if ($('[data-nav][aria-current=page]').dataset.nav!==id) return;
    var priorPanel=$('[data-view-panel="'+id+'"]'),openSections=$$('details',priorPanel).map(function(d){return d.open;});
    var renders={model:renderIntentPanel,asr:renderAsrPanel,pack:renderPackPanel,route:renderRoutePanel,ai:renderAiPanel,pit:renderCarPanel};
    if (renders[id]) renders[id](st,dev);
    var current=$('[data-view-panel="'+id+'"]');
    $$('details',current).forEach(function(d,i){if(i<openSections.length)d.open=openSections[i];});
    Object.keys(pendingDrafts).forEach(function(key){var el=current.querySelector('#'+key);if(el)el.value=pendingDrafts[key];});
    if (current.querySelector('input,select') && Object.keys(pendingDrafts).some(function(key){return !!current.querySelector('#'+key);})) setText('[data-savehint]','有未保存的更改',current);
    paintIcons(current);
  }

  async function save(patch, hintEl, note) {
    var r = await api("/api/config", { patch: patch });
    var ids=patch.car_control?['ira-cc-on','ira-cc-verify']:patch.ai && patch.ai.tts?['ira-tts-adapter','ira-tts-url','ira-tts-speaker','ira-tts-key','ira-ack-on']:patch.ai && !patch.ai.analysis_key?['ira-ai-on','ira-ai-mode','ira-ai-preset','ira-ai-url','ira-ai-model','ira-ai-key','ira-ai-global','ira-ai-thinking']:patch.overlay?['ira-ov-on','ira-ov-opacity','ira-ov-corner','ira-ov-width','ira-ov-height','ira-ov-x','ira-ov-y']:[];
    ids.forEach(function(id){delete pendingDrafts[id];});
    if (hintEl) {
      hintEl.textContent = "已保存" + (r.restart_hint && r.restart_hint.length
        ? "（" + r.restart_hint.join("、") + " 重启后生效）" : "（已生效）");
      setTimeout(function () { hintEl.textContent = note || ""; }, 2600);
    }
    var st = await api("/api/state");
    renderState(st);
  }

  var fieldSequence=0;
  function field(label, inner, hint) {
    var match=inner.match(/\bid=["']([^"']+)/), id=match && match[1];
    if (!id && /<(input|select)/.test(inner)) { id='ira-field-'+(++fieldSequence); inner=inner.replace(/<(input|select)/,'<$1 id="'+id+'"'); }
    var hintId=id ? id+'-hint' : '';
    if (hint && id) inner=inner.replace(/<(input|select)/,'<$1 aria-describedby="'+hintId+'"');
    return '<div class="ira-field"><label'+(id?' for="'+id+'"':'')+'>'+label+'</label>'+inner+(hint?'<span class="ira-hint"'+(hintId?' id="'+hintId+'"':'')+'>'+hint+'</span>':'')+'</div>';
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

  // v1.5.2：关闭「全局交给 AI」时恢复的档位。进入页面时记住非 all 的 mode，
  // 从 all 关回来优先恢复它；没记住就回 whitelist。
  var globalAiRestoreMode = null;
  var AI_MODE_LABELS = {
    whitelist: "白名单", analysis_key: "按键触发",
    full: "规则答不上就接管", all: "全部交给 AI"
  };
  function aiModeLabel(mode) { return AI_MODE_LABELS[mode] || "白名单"; }

  function aiPresetOptions(current) {
    return AI_PRESETS.map(function (p) {
      return '<option value="' + p.key + '"' +
        (p.key === (current || "custom") ? " selected" : "") + ">" + p.label + "</option>";
    }).join("");
  }

  // ---------------------------------------------------------------- 模型名拉取
  // v1.6.0：GET /api/ai/models 用「已保存的」接口地址与密钥拉模型列表。
  // 列表只放在内存里（不落盘、不写日志），key 永远不进 DOM。
  // 拉取失败不阻塞手填：下拉收起，输入框照常可用。
  var aiModelCache = null;

  function aiModelsError(error) {
    var msg = String((error && error.message) || error || "").trim();
    var tail = "；可以直接手动填写模型名。";
    if (/401|403|unauthor|auth|invalid[ _-]?key/i.test(msg)) {
      return "拉取失败：密钥无效或没有权限" + tail;
    }
    if (/404|not[ _-]?found|no[ _-]?such/i.test(msg)) {
      return "拉取失败：这个接口地址不提供模型列表" + tail;
    }
    if (/超时|timeout|timed out|连接失败|network/i.test(msg)) {
      return "拉取失败：连接超时或网络不可达" + tail;
    }
    return "拉取失败：" + (msg || "未知错误") + tail;
  }

  function applyAiModelOptions(panel, current) {
    var select = $("#ira-ai-model-select", panel);
    var input = $("#ira-ai-model", panel);
    if (!select || !input) { return; }
    if (!aiModelCache || !aiModelCache.length) {
      select.hidden = true;
      select.innerHTML = "";
      input.hidden = false;
      return;
    }
    var wanted = String(current || "").trim();
    var selected = aiModelCache.indexOf(wanted) >= 0 ? wanted : "__custom__";
    select.innerHTML = '<option value="__custom__">手动填写 / 自定义模型</option>' +
      aiModelCache.map(function (id) {
        return '<option value="' + esc(id) + '">' + esc(id) + "</option>";
      }).join("");
    select.value = selected;
    select.hidden = false;
    input.hidden = selected !== "__custom__";
    if (selected !== "__custom__") { input.value = selected; }
    select.onchange = function () {
      var value = select.value;
      if (value === "__custom__") {
        input.hidden = false;
        input.focus();
        return;
      }
      input.hidden = true;
      input.value = value;
      pendingDrafts["ira-ai-model"] = value;
      setText("[data-savehint]", "已选择模型，点「保存模型设置」生效", panel);
    };
  }

  function renderAiPanel(st) {
    var panel = $('[data-view-panel="ai"]');
    if (!panel) return;
    var a = (st.config && st.config.ai) || {};
    var tts = a.tts || {};
    var s = st.ai || {};
    var ov = st.overlay || {};
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    if (!card) return;

    var statusText = !a.enabled ? "未启用"
      : (s.configured ? (s.available ? "就绪" : "已暂停（连续超时）") : "未配置 API");
    var ovOpacity = Math.round((ov.opacity == null ? 0.85 : ov.opacity) * 100);
    var ovCorner = ov.corner || "top-right";
    var ovHotkey = ov.toggle_key || "Ctrl+Alt+O";
    var ovCorners = [["top-right", "右上角"], ["top-left", "左上角"],
                     ["bottom-right", "右下角"], ["bottom-left", "左下角"]];
    var ackEnabled = a.ack_enabled === undefined ? true : !!a.ack_enabled;

    // v1.5.2：全局 AI / 思考模式 / Overlay 几何的回填。
    // 不能假设后端已经给出新字段：缺失时按契约默认值（whitelist / off / 720×200）。
    var aiMode = a.mode || "whitelist";
    if (aiMode !== "all") globalAiRestoreMode = aiMode;
    var thinkingOff = a.thinking_mode !== "low";              // 默认 off：关闭思考
    var globalStopLocal = a.global_stop_local !== false;        // all 档默认静音本地
    var ovCfg = (st.config && st.config.overlay) || {};
    function ovValue(key, fallback) {
      var value = ov[key] != null ? ov[key] : ovCfg[key];
      return value == null ? fallback : value;
    }
    var ovLayout = !!ov.layout_mode;
    var ovWidth = ovValue("width", 720);
    var ovHeight = ovValue("height", 200);
    var ovX = ovValue("x", "");
    var ovY = ovValue("y", "");
    var posText = function (value) { return value === "" ? "" : esc(String(value)); };

    card.innerHTML='<div class="ira-notice" id="ira-global-ai"><div class="ira-switchrow"><div><strong>全局交给 AI</strong><span class="ira-hint" id="ira-global-ai-desc">所有问答只走 AI，本地规则不再出声；安全/操作类只显示文字。</span></div><select class="ira-select" id="ira-ai-global" aria-label="全局交给 AI" style="width:190px;flex:none"><option value="1"'+(aiMode==='all'?' selected':'')+'>开启 · 全部交给 AI</option><option value="0"'+(aiMode==='all'?'':' selected')+'>关闭 · 按档位处理</option></select></div><div class="ira-meta" id="ira-ai-global-hint">'+(aiMode==='all' ? ('当前：所有问答交给 AI'+(globalStopLocal?' · 本地语音已静音':' · 本地语音未静音（global_stop_local=false）')) : ('当前：'+aiModeLabel(aiMode)+'；关闭后恢复到进入页面时的档位'))+'</div></div><h2>模型接入</h2><div class="ira-form ai-form-grid">'+
      field('启用 AI 分析','<select class="ira-select" id="ira-ai-on"><option value="0"'+(!a.enabled?' selected':'')+'>关闭</option><option value="1"'+(a.enabled?' selected':'')+'>开启</option></select>','当前状态：'+statusText)+
      field('响应方式','<select class="ira-select" id="ira-ai-mode"><option value="whitelist"'+(aiMode==='whitelist'?' selected':'')+'>白名单 · 推荐</option><option value="analysis_key"'+(aiMode==='analysis_key'?' selected':'')+'>分析键触发</option><option value="full"'+(aiMode==='full'?' selected':'')+'>规则答不上就接管</option><option value="all"'+(aiMode==='all'?' selected':'')+'>全部交给 AI · 最像真人</option></select>','「全部交给 AI」连规则答得上的也交给模型；安全、处罚、改车仍走本地规则（只显示文字）；需配好语音合成才听得到回答。')+
      field('思考模式','<select class="ira-select" id="ira-ai-thinking"><option value="off"'+(thinkingOff?' selected':'')+'>关闭 · 最快</option><option value="low"'+(thinkingOff?'':' selected')+'>不关闭 · 最低思考强度</option></select>','关闭最快；不关闭时使用最低思考强度。')+
      field('服务预设','<select class="ira-select" id="ira-ai-preset">'+aiPresetOptions(a.provider)+'</select>','选择服务商后可继续修改地址和模型名。')+
      field('模型名','<div class="ira-model-pick"><select class="ira-select" id="ira-ai-model-select" aria-label="已拉取的模型" hidden></select><input class="ira-input" id="ira-ai-model" aria-label="模型名（可手动填写）" value="'+esc(a.model || '')+'" placeholder="服务商提供的模型名"><button class="ira-btn" type="button" id="ira-ai-models">拉取模型</button></div><span class="ira-model-out" id="ira-ai-models-out" role="status"></span>','用已保存的接口地址与密钥拉取；拉取失败也能直接手填模型名。')+
      field('接口地址','<input class="ira-input" id="ira-ai-url" value="'+esc(a.base_url || '')+'" placeholder="https://api.deepseek.com">','使用兼容 Chat Completions 的接口。')+
      field('API Key','<input class="ira-input" id="ira-ai-key" type="password" autocomplete="off" placeholder="'+(a.api_key_set?'已配置，留空不修改':'粘贴你的密钥')+'">','密钥仅保存在本机，不随安装包发布。')+
      '</div><div class="ira-row settings-actions"><button class="ira-btn ira-btn-primary" id="ira-ai-save" type="button">保存模型设置</button><button class="ira-btn" id="ira-ai-test" type="button">测试连接</button><span class="ira-savehint" id="ira-ai-testout" role="status"></span></div>'+
      '<details class="settings-disclosure"'+(tts.adapter && tts.adapter!=='off'?' open':'')+'><summary>语音合成<span>让 AI 回答与等待语音通过耳机播放</span></summary><div class="ira-form ai-form-grid">'+
      field('合成服务','<select class="ira-select" id="ira-tts-adapter"><option value="off"'+(tts.adapter==='off'?' selected':'')+'>关闭 · 仅文字</option><option value="doubao"'+(tts.adapter==='doubao'?' selected':'')+'>豆包</option><option value="openai"'+(tts.adapter==='openai'?' selected':'')+'>OpenAI 兼容</option></select>','本地规则的语音包播报不受此开关影响。')+
      field('音色','<input class="ira-input" id="ira-tts-speaker" value="'+esc(tts.speaker || tts.voice || '')+'" placeholder="speaker 或 voice">','填写语音服务支持的音色名称。')+
      field('服务地址','<input class="ira-input" id="ira-tts-url" value="'+esc(tts.base_url || '')+'" placeholder="OpenAI 兼容服务才需要">','豆包使用内置服务地址。')+
      field('语音 API Key','<input class="ira-input" id="ira-tts-key" type="password" autocomplete="off" placeholder="'+(tts.api_key_set?'已配置，留空不修改':'粘贴语音服务密钥')+'">','与模型密钥分别保存。')+
      '</div><div class="ira-form">'+
      field('等待语音','<select class="ira-select" id="ira-ack-on"><option value="1"'+(ackEnabled?' selected':'')+'>开启 · 先播一句等待语</option><option value="0"'+(ackEnabled?'':' selected')+'>关闭 · 思考时保持安静</option></select>','AI 思考时先播一句等待语音（约 1 秒），关闭可缩短等待。')+
      '</div><div class="ira-row settings-actions"><button class="ira-btn" id="ira-tts-save" type="button">保存语音合成与等待语音</button><button class="ira-btn" id="ira-tts-test" type="button">试听</button><span class="ira-savehint" id="ira-tts-out" role="status"></span></div></details>'+
      '<details class="settings-disclosure"><summary>分析键绑定<span>仅按键触发模式需要</span></summary><div class="ira-form">'+
      field('当前绑定','<input class="ira-input" id="ira-ai-keyname" readonly value="'+(a.analysis_key_bound?'已绑定':'未绑定')+'">','独立于普通 PTT。按住分析键提问时直接请求 AI。')+
      '</div><div class="ira-row settings-actions"><button class="ira-btn" id="ira-ai-keycap" type="button">采集分析键</button><span class="ira-savehint" id="ira-ai-keyout" role="status"></span></div></details>'+
      '<details class="settings-disclosure"><summary>学习库维护<span>删除已保存的问答</span></summary><p class="ira-hint">清空后会失去已确认的知识和条件模板。此操作不会删除模型配置。</p><div class="ira-row settings-actions"><button class="ira-btn danger-button" id="ira-learn-clear" type="button">清空学习库</button><span class="ira-savehint" id="ira-learn-out" role="status"></span></div></details>'+
      '<details class="settings-disclosure" id="ira-overlay-settings"><summary>游戏内 Overlay<span>在游戏画面上显示实时识别与回答</span></summary><div class="ira-form ai-form-grid">'+
      field('启用 Overlay','<select class="ira-select" id="ira-ov-on"><option value="0"'+(ov.enabled?'':' selected')+'>关闭</option><option value="1"'+(ov.enabled?' selected':'')+'>开启</option></select>','游戏上方浮层：无边框、置顶、点击穿透，不影响操作。')+
      field('透明度','<input class="ira-range" type="range" id="ira-ov-opacity" min="20" max="100" step="5" value="'+ovOpacity+'"><span class="ira-rangeval"><span class="ira-num" id="ira-ov-opacityval">'+ovOpacity+'</span> %</span>','越低越不挡视线；最低 20%。')+
      field('显示位置','<select class="ira-select" id="ira-ov-corner">'+ovCorners.map(function(c){return '<option value="'+c[0]+'"'+(ovCorner===c[0]?' selected':'')+'>'+c[1]+'</option>';}).join('')+'</select>','多屏时以主屏为准。')+
      field('布局模式','<select class="ira-select" id="ira-ov-layout"><option value="0"'+(ovLayout?'':' selected')+'>关闭 · 点击穿透</option><option value="1"'+(ovLayout?' selected':'')+'>开启 · 可拖动缩放</option></select>','开启后临时关闭点击穿透，可拖动卡片、拖右下角缩放；游戏中按 Ctrl+Alt+L 也能切换。')+
      field('窗口尺寸','<div class="ira-inline"><input class="ira-input ira-num" id="ira-ov-width" inputmode="numeric" min="240" max="4096" value="'+esc(String(ovWidth))+'"><span class="ira-meta">×</span><input class="ira-input ira-num" id="ira-ov-height" inputmode="numeric" min="80" max="2160" aria-label="Overlay 窗口高度（像素）" value="'+esc(String(ovHeight))+'"></div>','像素。宽度 240–4096，高度 80–2160；默认 720×200。')+
      field('位置 X / Y','<div class="ira-inline"><input class="ira-input ira-num" id="ira-ov-x" inputmode="numeric" value="'+posText(ovX)+'" placeholder="留空 = 按角落"><span class="ira-meta">/</span><input class="ira-input ira-num" id="ira-ov-y" inputmode="numeric" aria-label="Overlay 位置 Y（留空按角落）" value="'+posText(ovY)+'" placeholder="留空 = 按角落"></div>','留空表示按「显示位置」的角落加边距排布；填写后固定到该坐标。')+
      field('切换热键','<input class="ira-input ira-num" value="'+esc(ovHotkey)+'" readonly>','游戏中按 '+esc(ovHotkey)+' 临时显示/隐藏，不会写入配置。')+
      '</div><div class="ira-row settings-actions"><button class="ira-btn ira-btn-primary" type="button" id="ira-ov-save">保存 Overlay 设置</button><button class="ira-btn" type="button" id="ira-ov-toggle">立即显示/隐藏</button><button class="ira-btn" type="button" id="ira-ov-reset">恢复默认位置与尺寸</button><span class="ira-savehint" id="ira-ov-out" role="status"></span></div></details>';

    bindAiPanel(panel, hint);
    applyAiModelOptions(panel, a.model || "");
    bindOverlaySettings(panel, ov);
    renderAiReview();
  }

  // 游戏内 Overlay 设置（v1.5.1）：写入 config.local.yaml 的 overlay 段。
  // 透明度滑杆与配置下限统一为 20%（0.2–1.0）。
  function bindOverlaySettings(panel, ov) {
    var hint = $("[data-savehint]", panel);
    var range = $("#ira-ov-opacity", panel);
    var rangeVal = $("#ira-ov-opacityval", panel);
    if (range && rangeVal) {
      range.oninput = function () { rangeVal.textContent = this.value; };
    }
    function clearOverlayDrafts() {
      ["ira-ov-on", "ira-ov-opacity", "ira-ov-corner",
       "ira-ov-width", "ira-ov-height", "ira-ov-x", "ira-ov-y"
      ].forEach(function (id) { delete pendingDrafts[id]; });
    }
    // 布局模式只调运行时接口，不改配置；服务器没有控制器时也要给出明确结果。
    var layoutSel = $("#ira-ov-layout", panel);
    if (layoutSel) {
      layoutSel.addEventListener("change", async function () {
        var out = $("#ira-ov-out", panel);
        var want = layoutSel.value === "1";
        layoutSel.disabled = true;
        try {
          var r = await api("/api/overlay/layout_mode", { enabled: want });
          var actual = r && typeof r.layout_mode === "boolean" ? r.layout_mode : want;
          layoutSel.value = actual ? "1" : "0";
          if (out) {
            out.textContent = actual
              ? "布局模式已开启：点击不再穿透，可拖动卡片、拖右下角缩放"
              : "布局模式已关闭：恢复点击穿透";
          }
        } catch (e) {
          layoutSel.value = want ? "0" : "1";
          if (out) out.textContent = "布局模式切换失败：" + e.message;
        } finally {
          layoutSel.disabled = false;
        }
      });
    }
    var saveBtn = $("#ira-ov-save", panel);
    if (saveBtn) {
      saveBtn.addEventListener("click", async function () {
        var out = $("#ira-ov-out", panel);
        var raw = parseInt(range && range.value, 10);
        if (!raw) raw = 85;
        var opacity = Math.round(Math.max(0.2, Math.min(1, raw / 100)) * 100) / 100;
        // v1.5.2：几何也一起保存；X/Y 留空写 null，后端按 corner+margin 排。
        var patch = { overlay: {
          enabled: ($("#ira-ov-on", panel) || {}).value === "1",
          opacity: opacity,
          corner: ($("#ira-ov-corner", panel) || {}).value || "top-right",
          width: overlayIntInput($("#ira-ov-width", panel), 720, 240, 4096),
          height: overlayIntInput($("#ira-ov-height", panel), 200, 80, 2160),
          x: overlayPositionInput($("#ira-ov-x", panel)),
          y: overlayPositionInput($("#ira-ov-y", panel))
        } };
        saveBtn.disabled = true;
        try {
          await save(patch, hint, "游戏内 Overlay 设置");
          clearOverlayDrafts();
          if (raw < 20 && out) out.textContent = "透明度最低 20%，已按 20% 保存";
          await loadSettings();
        } catch (e) {
          reportError(e);
          if (out) out.textContent = "保存失败：" + e.message;
        } finally {
          saveBtn.disabled = false;
        }
      });
    }
    var toggleBtn = $("#ira-ov-toggle", panel);
    if (toggleBtn) {
      toggleBtn.addEventListener("click", async function () {
        var out = $("#ira-ov-out", panel);
        toggleBtn.disabled = true;
        try {
          var r = await api("/api/overlay/toggle", {});
          if (out) {
            out.textContent = r.ok ? (r.visible ? "已显示（运行时切换）" : "已隐藏（运行时切换）")
              : (r.error || "切换失败");
          }
        } catch (e) {
          if (out) out.textContent = "切换失败：" + e.message;
        } finally {
          toggleBtn.disabled = false;
        }
      });
    }
    var resetBtn = $("#ira-ov-reset", panel);
    if (resetBtn) {
      resetBtn.addEventListener("click", async function () {
        var out = $("#ira-ov-out", panel);
        resetBtn.disabled = true;
        try {
          // 恢复默认：位置回到角落排布（null），尺寸回 720×200。
          await save({ overlay: { x: null, y: null, width: 720, height: 200 } },
                     hint, "Overlay 位置与尺寸已恢复默认");
          clearOverlayDrafts();
          if (out) out.textContent = "已恢复默认位置与尺寸";
          await loadSettings();
        } catch (e) {
          if (out) out.textContent = "重置失败：" + e.message;
          reportError(e);
        } finally {
          resetBtn.disabled = false;
        }
      });
    }
  }

  // Overlay 几何输入：空/非法回落到默认值或 null（null = 按角落排）。
  function overlayIntInput(el, fallback, min, max) {
    var raw = el ? parseInt(el.value, 10) : NaN;
    if (!isFinite(raw)) return fallback;
    return Math.max(min, Math.min(max, raw));
  }
  function overlayPositionInput(el) {
    if (!el) return null;
    var text = String(el.value == null ? "" : el.value).trim();
    if (!text) return null;
    var raw = parseInt(text, 10);
    if (!isFinite(raw)) return null;
    return Math.max(-10000, Math.min(10000, raw));
  }

  function renderCarPanel(st) {
    var panel=$('[data-view-panel="pit"]'), c=st.config.car_control || {};
    $('.ira-card',panel).innerHTML='<h2>语音进站操作</h2><div class="ira-form" style="margin-top:24px">'+
      field('允许语音改车','<select class="ira-select" id="ira-cc-on"><option value="0"'+(!c.enabled?' selected':'')+'>关闭</option><option value="1"'+(c.enabled?' selected':'')+'>开启</option></select>','开启后，明确的换胎、加油、快修指令会更改游戏中的进站服务。')+
      field('执行后确认','<select class="ira-select" id="ira-cc-verify"><option value="1"'+(c.verify?' selected':'')+'>读取游戏状态确认（推荐）</option><option value="0"'+(!c.verify?' selected':'')+'>只检查发送结果</option></select>','确认换胎范围、服务勾选和加油目标。读不到结果时不会报告执行成功。')+
      '</div><div class="ira-row" style="margin-top:24px"><button class="ira-btn ira-btn-primary" type="button" id="ira-cc-save">保存进站设置</button></div><hr class="ira-hr"><div class="pit-details-grid"><section><h2>游戏中的服务勾选</h2><div class="pit-settings-preview" data-pit-service></div></section><section><h2>常用进站指令</h2><p class="support-copy">'+
      '<strong>可以说</strong>　帮我取消换胎 · 只换左侧轮胎 · 加10升油 · 取消快修 · 勾上快修<br>轮胎配方、刹车平衡、TC、ABS 和雨刷需在游戏中设置。加油目前只支持整数升数。<br>语音可能识别错误，请先在练习中确认。</p></section></div>';
    $('#ira-cc-save',panel).onclick=async function(){var b=this;b.disabled=true;try{await save({car_control:{enabled:$('#ira-cc-on').value==='1',verify:$('#ira-cc-verify').value==='1'}},$('[data-savehint]',panel));await loadSettings();}finally{b.disabled=false;}};
    renderPitServices(st);
  }

  function aiPatchFromPanel(panel) {
    var p = {
      enabled: $("#ira-ai-on", panel).value === "1",
      mode: $("#ira-ai-mode", panel).value,
      provider: $("#ira-ai-preset", panel).value,
      base_url: $("#ira-ai-url", panel).value.trim(),
      model: $("#ira-ai-model", panel).value.trim()
    };
    // v1.5.2：思考模式作为 ai patch 的一部分保存（off=关闭，low=最低强度）。
    p.thinking_mode = ($("#ira-ai-thinking", panel) || {}).value === "low" ? "low" : "off";
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
    var ackEl = $("#ira-ack-on", panel);
    var ackOn = ackEl ? ackEl.value === "1" : true;
    return { ai: { tts: t, ack: { enabled: ackOn } } };
  }

  function bindAiPanel(panel, hint) {
    var globalSwitch = $("#ira-ai-global", panel);
    if (globalSwitch) {
      globalSwitch.addEventListener("change", async function () {
        var wantAll = globalSwitch.value === "1";
        // 开：写 all；关：恢复进入页面时记下的档位（从 all 进来时回 whitelist）。
        var restore = globalAiRestoreMode || "whitelist";
        var patch = wantAll ? { ai: { mode: "all" } } : { ai: { mode: restore } };
        globalSwitch.disabled = true;
        try {
          await save(patch, hint, "全局交给 AI");
          await loadSettings();
        } catch (e) {
          globalSwitch.value = wantAll ? "0" : "1";
          reportError(e);
        } finally {
          globalSwitch.disabled = false;
        }
      });
    }
    var preset = $("#ira-ai-preset", panel);
    if (preset) {
      preset.addEventListener("change", function () {
        var p = AI_PRESETS.filter(function (x) { return x.key === preset.value; })[0];
        if (!p) return;
        if (p.url) $("#ira-ai-url", panel).value = p.url;
        if (p.model) $("#ira-ai-model", panel).value = p.model;
        // 换服务商后模型列表不再可信：收起下拉，回到手填。
        aiModelCache = null;
        applyAiModelOptions(panel, $("#ira-ai-model", panel).value);
      });
    }
    var urlInput = $("#ira-ai-url", panel);
    if (urlInput) {
      urlInput.addEventListener("change", function () {
        aiModelCache = null;
        applyAiModelOptions(panel, $("#ira-ai-model", panel).value);
      });
    }
    var modelsBtn = $("#ira-ai-models", panel);
    if (modelsBtn) {
      modelsBtn.addEventListener("click", async function () {
        var out = $("#ira-ai-models-out", panel);
        var input = $("#ira-ai-model", panel);
        modelsBtn.disabled = true;
        if (out) { out.textContent = "正在拉取模型列表…"; }
        try {
          var r = await api("/api/ai/models");
          var list = ((r && r.models) || []).map(function (m) {
            if (!m) { return ""; }
            if (typeof m === "string") { return m; }
            return m.id || m.name || m.model || "";
          }).filter(function (id) { return !!id; });
          if (!r || r.ok === false) { throw new Error((r && r.error) || "服务商没有返回模型列表"); }
          if (!list.length) { throw new Error("服务商返回的模型列表是空的"); }
          // 只留内存缓存；不写 localStorage，也不把 key 带进任何展示。
          aiModelCache = list;
          applyAiModelOptions(panel, input ? input.value : "");
          if (out) { out.textContent = "已拉取 " + list.length + " 个模型；选择后点「保存模型设置」生效。"; }
        } catch (e) {
          if (out) { out.textContent = aiModelsError(e); }
        } finally {
          modelsBtn.disabled = false;
        }
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
          if (r.ok) {
            // v1.5.2：必须说明「有没有真的响」，只报耗时等于没测。
            out.textContent = "已播放"
              + (r.played === false ? "（服务端未实际播放，请检查输出设备）" : "")
              + (r.bytes ? " · " + r.bytes + " 字节" : "")
              + (r.latency_ms != null ? " · " + r.latency_ms + " ms" : "");
          } else {
            out.textContent = "失败：" + (r.error || "未知错误");
          }
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
    var hint = $("[data-savehint]", panel);
    var card = $(".ira-card", panel);
    // v1.6.0：已删除「压低游戏音量」功能；前端不再展示、也不再保存
    // playback.ducking。
    card.innerHTML = '<div class="ira-form">' +
      field("播报输出设备", '<select class="ira-select" id="ira-outdev2">' +
        deviceOptions(dev.outputs, pb.device, "系统默认") + "</select>",
        "换成耳机 / 声卡都行；和游戏同一路输出最省事") +
      "</div><hr class='ira-hr'>" +
      "<div class='ira-flow'><span class='ira-node hot'>PTT 按住</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>开麦收音</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>SenseVoice 识别</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>短语库匹配</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>遥测路由</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node'>片段拼装</span>" +
      "<span class='ira-arrow'>→</span><span class='ira-node ok'>扬声器</span></div>" +
      "<hr class='ira-hr'><div class='ira-switchrow'>" +
      "<input class='ira-switch' id='ira-auto-mute' type='checkbox' checked disabled>" +
      "<label for='ira-auto-mute'><span class='ira-swtitle'>播放时自动静麦</span>" +
      "<span class='ira-hint'>固定开启：不按 PTT 不开麦，播报期间按键忽略 —— 这条不开就会自己问自己答</span></label></div>";

    $("#ira-outdev2", panel).onchange = function () {
      save({ playback: { device: this.value === "" ? null : parseInt(this.value, 10) } },
        hint);
    };
  }

  /* ---------- 关闭询问（v1.6.0） ---------- */
  // 点标题栏 × 或 Alt+F4 时先问一句：隐藏到托盘 / 直接退出 / 取消。
  // 桌面端由 app/desktop.py 的 closing 事件兜底（窗口被系统关闭时也会叫出这个框）。
  var closeDialogEl = null;
  function ensureCloseDialog() {
    if (closeDialogEl) { return closeDialogEl; }
    var layer = document.createElement("div");
    layer.id = "ln-close-layer";
    layer.className = "ln-close-layer";
    layer.hidden = true;
    layer.innerHTML =
      '<div class="ln-close-dialog" role="dialog" aria-modal="true" aria-labelledby="ln-close-title" aria-describedby="ln-close-desc">' +
      '<h2 id="ln-close-title">要关闭小小领航员吗？</h2>' +
      '<p id="ln-close-desc" class="ira-hint">隐藏到系统托盘后继续在后台待命，双击托盘图标就能恢复窗口；直接退出会停掉监听、播报与 Overlay。</p>' +
      '<p class="ira-hint ln-close-note" id="ln-close-note" role="status" hidden></p>' +
      '<div class="ln-close-actions">' +
      '<button class="ira-btn ira-btn-primary" type="button" id="ln-close-tray">隐藏到系统托盘</button>' +
      '<button class="ira-btn" type="button" id="ln-close-quit">直接退出</button>' +
      '<button class="ira-btn" type="button" id="ln-close-cancel">取消</button>' +
      "</div></div>";
    document.body.appendChild(layer);
    closeDialogEl = layer;
    // 点遮罩 = 取消（和 Esc 一致），避免误点把窗口关掉。
    layer.addEventListener("mousedown", function (event) {
      if (event.target === layer) { closeCloseDialog(); }
    });
    $("#ln-close-cancel", layer).onclick = function () { closeCloseDialog(); };
    $("#ln-close-quit", layer).onclick = async function () {
      var note = $("#ln-close-note", layer);
      var btn = this;
      btn.disabled = true;
      if (note) { note.hidden = false; note.textContent = "正在退出…"; }
      try {
        // 沿用既有退出接口：/api/quit 只置 quit_requested，由桌面端收尾。
        await api("/api/quit", {});
      } catch (e) {
        btn.disabled = false;
        if (note) { note.textContent = "退出失败：" + e.message; }
      }
    };
    $("#ln-close-tray", layer).onclick = async function () {
      var note = $("#ln-close-note", layer);
      var btn = this;
      btn.disabled = true;
      try {
        if (!(window.pywebview && window.pywebview.api && window.pywebview.api.hide_to_tray)) {
          throw new Error("当前是浏览器预览模式，系统托盘只在桌面版可用；可以改选「直接退出」。");
        }
        var r = await window.pywebview.api.hide_to_tray();
        if (!r || r.ok === false) { throw new Error((r && r.error) || "系统托盘不可用"); }
        closeCloseDialog();
      } catch (e) {
        if (note) { note.hidden = false; note.textContent = "无法隐藏到托盘：" + e.message; }
      } finally {
        btn.disabled = false;
      }
    };
    // Esc = 取消；Tab 只在对话框内循环，避免焦点跑到底层页面。
    layer.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeCloseDialog();
        return;
      }
      if (event.key !== "Tab") { return; }
      var focusable = $$("button", layer).filter(function (b) { return !b.disabled; });
      if (!focusable.length) { return; }
      var first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    });
    return layer;
  }
  function openCloseDialog() {
    var layer = ensureCloseDialog();
    layer.hidden = false;
    document.body.classList.add("ln-close-open");
    var primary = $("#ln-close-tray", layer);
    if (primary) { primary.focus(); }
  }
  function closeCloseDialog() {
    if (!closeDialogEl) { return; }
    closeDialogEl.hidden = true;
    document.body.classList.remove("ln-close-open");
    var closeBtn = $('#window-close');
    if (closeBtn) { closeBtn.focus(); }
  }
  // 桌面端 closing 事件会调到这个钩子；返回 true 表示选择框已经叫出来了。
  window.__lnClosePrompt = function () {
    openCloseDialog();
    return true;
  };

  /* ---------- 启动 ---------- */
  var pendingDrafts={};
  $('#ira-main').addEventListener('input',function(event){var el=event.target,panel=el.closest('[data-view-panel]');if(el.id && panel && ['ai','pit'].includes(panel.dataset.viewPanel) && !el.readOnly){pendingDrafts[el.id]=el.value;setText('[data-savehint]','有未保存的更改',panel);}});
  function closeNav(){document.body.classList.remove('nav-open');$('#nav-toggle').setAttribute('aria-expanded','false');$('#nav-toggle').setAttribute('aria-label','打开导航');$('#ira-navigation').inert=matchMedia('(max-width:800px)').matches;}
  $('#nav-toggle').onclick=function(){var open=!document.body.classList.contains('nav-open');document.body.classList.toggle('nav-open',open);this.setAttribute('aria-expanded',String(open));this.setAttribute('aria-label',open?'关闭导航':'打开导航');$('#nav-scrim').hidden=!open;$('#ira-navigation').inert=!open;if(open)tabs[0].focus();};
  matchMedia('(max-width:800px)').addEventListener('change',function(e){$('#ira-navigation').inert=e.matches && !document.body.classList.contains('nav-open');});
  $('#nav-scrim').onclick=closeNav;
  document.addEventListener('keydown',function(e){if(e.key==='Escape' && document.body.classList.contains('nav-open')){closeNav();$('#nav-toggle').focus();}});
  document.addEventListener('keydown',function(e){if(e.key==='Tab' && document.body.classList.contains('nav-open')){var first=$('#nav-toggle'),last=tabs[tabs.length-1];if(e.shiftKey && document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey && document.activeElement===last){e.preventDefault();first.focus();}}});
  $$('[data-go]').forEach(function(b){b.onclick=function(){showView(b.dataset.go);};});
  $('#dismiss-error').onclick=function(){$('#ira-error').hidden=true;};
  $('#retry-connection').onclick=function(){poll();};
  $('#stats-refresh').onclick=function(){loadStats();};
  $('#ira-export-logs').onclick=async function(){var b=this;b.disabled=true;b.textContent='导出中…';try{var r=await api('/api/export-logs');if(!r.ok)throw new Error(r.error||'导出失败');reportError({message:'日志已导出：'+r.path});}finally{b.disabled=false;b.textContent='导出日志';}};
  $('#dashboard-listen').onclick=async function(){var b=this;b.disabled=true;b.textContent='处理中…';try{await api(window.__state && window.__state.ptt.running?'/api/ptt/stop':'/api/ptt/start',{});await poll();}finally{b.disabled=false;b.textContent=window.__state && window.__state.ptt.running?'暂停监听':'开启监听';}};
  bindDebug();
  $('#window-close').onclick=function(){openCloseDialog();};
  $('#window-minimize').onclick=function(){if(window.pywebview){window.pywebview.api.minimize();}};
  bindPttButtons();
  relabelFunnel();
  showView(tabs.some(function(b){return b.dataset.nav===location.hash.slice(1);})?location.hash.slice(1):"dash");

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
      paintIcons(document);            /* 重绘后补画图标 */
      if ($('#ira-tab-asr').getAttribute('aria-current')==='page' && Date.now()-wheelRefreshAt>3000) {
        wheelRefreshAt=Date.now();
        var wheels=await api('/api/wheels');
        var hint=$('#ira-wheel-devices');if(hint){hint.innerHTML=wheelDeviceText(wheels);}
      }
    } catch (e) {
      var app = $("#ira-app");
      if (app) { app.setAttribute("data-sim", "offline"); }
      $('#connection-lost').hidden=false;
      if(window.__state){var stale=Object.assign({},window.__state,{telemetry:{connected:false},last:window.__state.last});renderState(stale);$('#connection-lost').hidden=false;setText('.ira-session','服务连接已断开');['link','mic','ai','car'].forEach(function(k){busSet(k,'off','状态未知');});$('#dashboard-listen').disabled=true;$('#dashboard-listen').dataset.networkFailed='1';}
    }
  }
  /* ---------- 知识库 ----------
     第十一个页面：图谱 / 矩阵两种看法，共用右侧详情与选中状态。
     数据只读，来自 GET /api/knowledge；不播报、不改车、不调模型。 */
  var KB = {data: null, mode: "graph", selected: null, category: null, filter: ""};
  var KB_CATEGORIES = ["对象/目标", "时间/圈数", "趋势/预测", "比较/差值", "状态/是否", "基础读数"];
  var KB_COL = {intent: 8, field: 286, out: 552};
  var KB_IW = 208, KB_FW = 232, KB_OW = 112;
  var KB_TOP = 44, KB_IH = 28, KB_IGAP = 6, KB_FH = 54, KB_FGAP = 8, KB_OY = 50, KB_OGAP = 86;

  function kbField(key) {
    return (KB.data.fields || []).filter(function (f) { return f.key === key; })[0];
  }
  function kbIntent(key) {
    return (KB.data.intents || []).filter(function (i) { return i.key === key; })[0];
  }
  function kbVisible() {
    var q = KB.filter.toLowerCase();
    if (!q) return KB.data.intents;
    return KB.data.intents.filter(function (i) {
      if (i.label.toLowerCase().indexOf(q) >= 0 || i.key.toLowerCase().indexOf(q) >= 0) return true;
      if ((i.samples || []).some(function (s) { return s.toLowerCase().indexOf(q) >= 0; })) return true;
      return i.fields.some(function (k) {
        var f = kbField(k);
        if (!f) return false;
        return f.label.toLowerCase().indexOf(q) >= 0 ||
          f.items.some(function (x) { return x.toLowerCase().indexOf(q) >= 0; });
      });
    });
  }
  function kbSelectedKind() {
    if (!KB.selected) return null;
    return KB.selected.split(":")[0];
  }
  function kbSelectedKey() {
    if (!KB.selected) return null;
    return KB.selected.split(":")[1];
  }

  function kbEl(tag, attrs, parent) {
    var n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (parent) parent.appendChild(n);
    return n;
  }

  function renderKbGraph() {
    var host = $("[data-kb-graph]");
    if (!host) return;
    var intents = kbVisible();
    host.innerHTML = "";
    if (!intents.length) {
      host.innerHTML = '<p class="ira-empty">没有匹配的意图，清空过滤再试。</p>';
      return;
    }
    var kind = kbSelectedKind(), key = kbSelectedKey();
    var fields = KB.data.fields.filter(function (f) {
      return !KB.filter || intents.some(function (i) { return i.fields.indexOf(f.key) >= 0; }) ||
        f.label.toLowerCase().indexOf(KB.filter.toLowerCase()) >= 0;
    });
    var outputs = KB.data.outputs.filter(function (o) {
      return !KB.filter || intents.some(function (i) { return i.outputs.indexOf(o) >= 0; });
    });

    var iy = {}, fy = {}, oy = {};
    intents.forEach(function (n, i) { iy[n.key] = KB_TOP + i * (KB_IH + KB_IGAP); });
    fields.forEach(function (n, i) { fy[n.key] = KB_TOP + i * (KB_FH + KB_FGAP); });
    outputs.forEach(function (n, i) { oy[n] = KB_OY + i * KB_OGAP; });
    // 三列各自按内容高度排布，画布取最高的一列，避免把节点挤在一起。
    var H = Math.max(KB_TOP + intents.length * (KB_IH + KB_IGAP) + 14,
                     KB_TOP + fields.length * (KB_FH + KB_FGAP) + 14,
                     KB_OY + outputs.length * KB_OGAP + 14,
                     320);

    var svg = kbEl("svg", { viewBox: "0 0 672 " + H, role: "img",
      "aria-label": "意图、遥测字段与播报方式的连线图" }, host);
    var gE = kbEl("g", {}, svg), gN = kbEl("g", {}, svg);
    var focus = !!KB.selected;
    // 选中意图时，和它直接相连的字段/播报要保留可读，而不是一起压暗。
    var relatedFields = {}, relatedOuts = {};
    if (focus && kind === "intent") {
      var sel = kbIntent(key);
      if (sel) {
        (sel.fields || []).forEach(function (f) { relatedFields[f] = true; });
        (sel.outputs || []).forEach(function (o) { relatedOuts[o] = true; });
      }
    }

    intents.forEach(function (n) {
      var y = iy[n.key] + KB_IH / 2;
      // 选中某个意图时，无论从它出发的哪条边都算「相关」，让链路能一眼读出来。
      var selIntent = focus && kind === "intent" && key === n.key;
      n.fields.forEach(function (fk) {
        if (fy[fk] === undefined) return;
        var y2 = fy[fk] + KB_FH / 2;
        var x1 = KB_COL.intent + KB_IW, x2 = KB_COL.field, mx = (x1 + x2) / 2;
        var on = !focus || selIntent || (kind === "field" && key === fk);
        kbEl("path", { d: "M" + x1 + " " + y + " C " + mx + " " + y + ", " + mx + " " + y2 + ", " + x2 + " " + y2,
          class: "kb-edge" + (on ? " on" : " off"),
          stroke: on && focus ? "#7f9dc4" : "#3d4c60" }, gE);
      });
      (n.outputs || []).forEach(function (ok) {
        if (oy[ok] === undefined) return;
        var y2 = oy[ok] + 16;
        var x1 = KB_COL.intent + KB_IW, x2 = KB_COL.out, mx = (x1 + x2) / 2;
        var on = !focus || selIntent || (kind === "out" && key === ok);
        kbEl("path", { d: "M" + x1 + " " + y + " C " + mx + " " + y + ", " + mx + " " + y2 + ", " + x2 + " " + y2,
          class: "kb-edge" + (on ? " on" : " off"),
          stroke: on && focus ? "#c9a86a" : "#7d6038", "stroke-dasharray": "3 4" }, gE);
      });
    });

    function node(cls, x, y, w, h, label, sub, sub2, nodeKey, aria, oneLine) {
      var g = kbEl("g", { class: "kb-node" + cls, tabindex: "0", role: "button", "aria-label": aria }, gN);
      g.dataset.key = nodeKey;
      kbEl("rect", { x: x, y: y, width: w, height: h, rx: 4, class: "kb-box" }, g);
      var t = kbEl("text", { x: x + 10, y: y + (oneLine ? 18 : 15), class: "kb-name" }, g);
      t.textContent = label;
      if (oneLine) {
        // 单行：名称在左、条数在右。窗口被详情栏挤压时，两行文本会缩到不足 10px。
        var r = kbEl("text", { x: x + w - 10, y: y + 18, class: "kb-num", "text-anchor": "end" }, g);
        r.textContent = sub || "";
      } else if (sub) {
        var s1 = kbEl("text", { x: x + 10, y: y + 28, class: "kb-num" }, g); s1.textContent = sub;
      }
      if (!oneLine && sub2) {
        var s2 = kbEl("text", { x: x + 10, y: y + 40, class: "kb-lbl" }, g); s2.textContent = sub2;
      }
      g.addEventListener("click", function () { kbSelect(nodeKey); });
      g.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); kbSelect(nodeKey); }
      });
    }

    intents.forEach(function (n) {
      var on = kind === "intent" && key === n.key;
      node(on ? " on" : (focus ? " off" : ""), KB_COL.intent, iy[n.key], KB_IW, KB_IH, n.label,
        n.phrases + " 条", null, "intent:" + n.key,
        n.label + "，" + n.phrases + " 条短语", true);
    });
    fields.forEach(function (n) {
      var on = kind === "field" && key === n.key;
      var linked = relatedFields[n.key] ? " linked" : "";
      node(on ? " on" : (focus ? " off" + linked : ""), KB_COL.field, fy[n.key], KB_FW, KB_FH, n.label,
        n.items.slice(0, 2).join(" · "), n.items.length + " 个字段", "field:" + n.key,
        n.label + "，" + n.items.length + " 个字段", false);
    });
    outputs.forEach(function (n) {
      var on = kind === "out" && key === n;
      var linked = relatedOuts[n] ? " linked" : "";
      node(on ? " on" : (focus ? " off" + linked : ""), KB_COL.out, oy[n], KB_OW, 32, n, null, null, "out:" + n,
        n + " 播报方式", false);
    });

    [["意图 · " + intents.length, KB_COL.intent],
     ["遥测字段 · " + fields.length + " 组", KB_COL.field],
     ["播报方式", KB_COL.out]].forEach(function (pair) {
      var h = kbEl("text", { x: pair[1], y: 26, class: "kb-head" }, gN);
      h.textContent = pair[0];
    });
  }

  function renderKbMatrix() {
    var host = $("[data-kb-matrix]");
    if (!host) return;
    var intents = kbVisible();
    if (!intents.length) {
      host.innerHTML = '<p class="ira-empty">没有匹配的意图，清空过滤再试。</p>';
      return;
    }
    var kind = kbSelectedKind(), key = kbSelectedKey();
    var maxCell = 1;
    intents.forEach(function (n) {
      KB_CATEGORIES.forEach(function (c) { maxCell = Math.max(maxCell, n.buckets[c] || 0); });
    });
    function color(v) {
      if (!v) return "var(--raised)";
      var t = v / maxCell;
      if (t < 0.05) return "#2f4a66";
      if (t < 0.2) return "#3f7cbe";
      if (t < 0.6) return "#5b95dc";
      return "#6ba8ed";
    }
    var html = '<table aria-label="意图与问法语义类别的数量矩阵"><thead><tr>' +
      '<th class="kb-rowhead" scope="col">意图 / 类别</th>';
    KB_CATEGORIES.forEach(function (c) { html += '<th scope="col">' + esc(c) + "</th>"; });
    html += '<th scope="col">示例合计</th></tr></thead><tbody>';
    intents.forEach(function (n) {
      var on = kind === "intent" && key === n.key;
      html += '<tr><th scope="row" class="kb-rowhead"><button type="button" data-kb-row="' + esc(n.key) + '"' +
        (on ? ' class="on"' : "") + "><span>" + esc(n.label) + "</span><b>" + n.examples +
        "</b></button></th>";
      KB_CATEGORIES.forEach(function (c) {
        var v = n.buckets[c] || 0;
        var sel = KB.category === c && on;
        html += '<td><button type="button" class="kb-cell' + (v ? "" : " dim") + (sel ? " sel" : "") +
          '" data-kb-cell="' + esc(n.key) + "|" + esc(c) + '" style="background:' + color(v) +
          ";color:" + (v ? "var(--ink)" : "var(--faint)") + '" aria-label="' + esc(n.label) + "，" +
          esc(c) + "，" + v + ' 条问法">' + v + "</button></td>";
      });
      html += '<td class="kb-total">' + n.examples + "</td></tr>";
    });
    html += "</tbody></table>";
    host.innerHTML = html;
    $$("[data-kb-row]", host).forEach(function (b) {
      b.onclick = function () { kbSelect("intent:" + b.getAttribute("data-kb-row")); };
    });
    $$("[data-kb-cell]", host).forEach(function (b) {
      b.onclick = function () {
        var parts = b.getAttribute("data-kb-cell").split("|");
        var already = KB.selected === "intent:" + parts[0] && KB.category === parts[1];
        KB.selected = "intent:" + parts[0];
        KB.category = already ? null : parts[1];
        kbRenderAll();
      };
    });
  }

  function kbStatCard(label, value, meta) {
    return '<div class="ira-stat"><span class="ira-label">' + esc(label) + '</span><div class="ira-num">' +
      value + "</div><span class=\"ira-meta\">" + esc(meta) + "</span></div>";
  }

  function renderKbStats() {
    var box = $("[data-kb-stats]");
    if (!box || !KB.data) return;
    var t = KB.data.totals;
    box.innerHTML = kbStatCard("意图", t.intents, "全部可直接回答") +
      kbStatCard("已载入短语", t.phrases.toLocaleString(), "另有 " + t.negatives + " 条拒绝清单") +
      kbStatCard("原始示例", t.examples.toLocaleString(), "intents_examples.yaml") +
      kbStatCard("规范问句", t.canonical, t.aliases.toLocaleString() + " 条别名") +
      kbStatCard("规范候选", t.candidates, "去重合并后") +
      kbStatCard("遥测字段组", t.fields, "按用途归并");
    var stamp = $("#kb-stamp");
    if (stamp) stamp.textContent = "v" + (KB.data.app_version || "—") + " · " + KB.data.generated;
  }

  function renderKbChips() {
    var box = $("#kb-chips");
    if (!box || !KB.data) return;
    var conflicts = KB.data.conflicts || [];
    var chips = [
      { label: "全部", run: function () { KB.selected = null; KB.category = null; KB.filter = ""; $("#kb-filter").value = ""; } },
      { label: "问法最多", run: function () {
          var top = KB.data.intents.slice().sort(function (a, b) { return b.phrases - a.phrases; })[0];
          KB.selected = "intent:" + top.key; KB.category = null;
        } },
      { label: "可疑样本", run: function () {
          var wrap = $("#kb-detail");
          wrap.innerHTML = '<div class="kb-eyebrow"><span>可疑样本</span><span>' + conflicts.length +
            " 条</span></div><h2>规则问题</h2><p class=\"kb-sub\">这些问法的规则层结果可疑，不是数据缺失。</p>" +
            '<div class="kb-rows">' + (conflicts.length ? conflicts.map(function (c) {
              return '<div class="kb-row" title="' + esc(c.note) + '"><span>' + esc(c.q) + "</span><b>" +
                esc(c.kind) + "</b></div>";
            }).join("") : '<div class="kb-row"><span>没有发现规则问题</span><b>—</b></div>') + "</div>" +
            '<div class="kb-note">数据缺失类拒绝（模拟快照没有 RPM、进站检测等）共 ' +
            (KB.data.data_gaps || 0) + " 条，已单独计数，不列为规则问题。</div>";
        } },
      { label: "缺口", run: function () {
          var worst = KB.data.intents.slice().sort(function (a, b) {
            var za = KB_CATEGORIES.filter(function (c) { return !a.buckets[c]; }).length;
            var zb = KB_CATEGORIES.filter(function (c) { return !b.buckets[c]; }).length;
            return zb - za;
          })[0];
          KB.selected = worst ? "intent:" + worst.key : null;
          KB.category = null;
        } }
    ];
    box.innerHTML = chips.map(function (c, i) {
      return '<button type="button" class="ira-btn kb-chip" aria-pressed="' + (!KB.selected && i === 0) +
        '">' + esc(c.label) + "</button>";
    }).join("");
    $$(".kb-chip", box).forEach(function (b, i) {
      b.onclick = function () {
        $$(".kb-chip", box).forEach(function (x) { x.setAttribute("aria-pressed", "false"); });
        b.setAttribute("aria-pressed", "true");
        chips[i].run();
        if (chips[i].label !== "可疑样本") kbRenderAll();
      };
    });
  }

  function renderKbDetail() {
    var box = $("#kb-detail");
    if (!box) return;
    if (!KB.data) { box.innerHTML = '<p class="ira-meta">正在读取知识库…</p>'; return; }
    if (!KB.selected) {
      var conflicts = KB.data.conflicts || [];
      box.innerHTML = '<div class="kb-eyebrow"><span>未选择</span><span>' +
        (KB.mode === "graph" ? "点节点" : "点行或格子") + "</span></div>" +
        "<h2>先选一个意图</h2>" +
        '<p class="kb-sub">图谱看链路，矩阵看密度；两种视图共用这份详情，选中状态会保留。</p>' +
        '<div class="kb-block"><h3>库内可疑样本 <span>' + conflicts.length + " 条</span></h3>" +
        '<div class="kb-rows">' + (conflicts.length ? conflicts.map(function (c) {
          return '<div class="kb-row" title="' + esc(c.note) + '"><span>' + esc(c.q) + "</span><b>" +
            esc(c.kind) + "</b></div>";
        }).join("") : '<div class="kb-row"><span>没有发现规则问题</span><b>—</b></div>') + "</div></div>" +
        '<div class="kb-note">数据缺失类拒绝 ' + (KB.data.data_gaps || 0) +
        " 条已单独计数：模拟快照没有 RPM、进站检测等实时值，不代表词库出错。</div>";
      return;
    }
    var kind = kbSelectedKind(), key = kbSelectedKey();
    if (kind === "field") {
      var f = kbField(key);
      box.innerHTML = '<div class="kb-eyebrow"><span>字段组</span><span>' + f.items.length + " 个</span></div>" +
        "<h2>" + esc(f.label) + "</h2><p class=\"kb-sub\">" + esc(f.items.join(" · ")) + "</p>" +
        '<div class="kb-block"><h3>使用它的意图 <span>' + f.users.length + "</span></h3>" +
        '<div class="kb-rows">' + f.users.map(function (k) {
          var i = kbIntent(k);
          return '<div class="kb-row"><span>' + esc(i.label) + "</span><b>" + i.phrases + " 条</b></div>";
        }).join("") + "</div></div>" +
        '<div class="kb-note">字段名逐条对照 Snapshot；实际读取由路由层决定。</div>';
      return;
    }
    if (kind === "out") {
      var users = KB.data.intents.filter(function (i) { return i.outputs.indexOf(key) >= 0; });
      box.innerHTML = '<div class="kb-eyebrow"><span>播报方式</span><span>' + users.length + " 个意图</span></div>" +
        "<h2>" + esc(key) + "</h2>" +
        '<div class="kb-block"><h3>使用它的意图</h3><div class="kb-rows">' + users.map(function (u) {
          return '<div class="kb-row"><span>' + esc(u.label) + "</span><b>" + u.phrases + " 条</b></div>";
        }).join("") + "</div></div>";
      return;
    }
    var n = kbIntent(key);
    if (!n) return;
    var maxCat = 1;
    KB_CATEGORIES.forEach(function (c) { maxCat = Math.max(maxCat, n.buckets[c] || 0); });
    var cat = KB.category;
    box.innerHTML = '<div class="kb-eyebrow"><span>意图</span><span>' + esc(n.key) + "</span></div>" +
      "<h2>" + esc(n.label) + (cat ? ' <span class="ira-meta">· ' + esc(cat) + "</span>" : "") + "</h2>" +
      '<p class="kb-sub">' + n.phrases + " 条短语 · " + n.canonical + " 个规范问句 · " + n.examples +
      " 条原始示例</p>" +
      '<div class="kb-kv"><div><b>' + n.fields.length + "</b><span>字段组</span></div>" +
      "<div><b>" + (n.outputs || []).length + "</b><span>播报方式</span></div></div>" +
      (cat ? '<div class="kb-block"><h3>' + esc(cat) + " <span>" + (n.buckets[cat] || 0) + " 条</span></h3>" +
        '<p class="ira-meta">' + (n.buckets[cat]
          ? "该意图在这一类下有问法；切回图谱可看它连到哪些字段。"
          : "该意图在这一类下没有问法，属于可补的空白。") + "</p></div>" : "") +
      '<div class="kb-block"><h3>示例问法</h3><div class="kb-rows">' +
      (n.samples || []).map(function (s) {
        return '<div class="kb-row"><span>' + esc(s) + "</span><b>→ " + esc(n.label) + "</b></div>";
      }).join("") + "</div></div>" +
      '<div class="kb-block"><h3>问法分布</h3><div class="kb-catbars">' +
      KB_CATEGORIES.map(function (c) {
        var v = n.buckets[c] || 0;
        return '<div class="kb-catbar"><span>' + esc(c) + '</span><span class="t"><i style="width:' +
          (v / maxCat * 100) + "%" + (cat === c ? ";background:var(--ink)" : "") + '"></i></span><b>' + v + "</b></div>";
      }).join("") + "</div></div>" +
      '<div class="kb-block"><h3>读取字段</h3><div class="kb-tags">' + n.fields.map(function (k) {
        return "<span>" + esc(kbField(k).label) + "</span>";
      }).join("") + "</div></div>" +
      '<div class="kb-block"><h3>播报方式</h3><div class="kb-tags">' + (n.outputs || []).map(function (o) {
        return '<span class="' + (o === "整句" ? "good" : "") + '">' + esc(o) + "</span>";
      }).join("") + "</div></div>";
  }

  function kbSelect(key) {
    KB.selected = KB.selected === key ? null : key;
    if (kbSelectedKind() === "intent") KB.category = null;
    kbRenderAll();
  }

  function kbRenderAll() {
    if (!KB.data) return;
    renderKbStats();
    if (KB.mode === "graph") renderKbGraph(); else renderKbMatrix();
    renderKbDetail();
  }

  function kbSetMode(mode) {
    KB.mode = mode;
    $("#kb-view-graph").hidden = mode !== "graph";
    $("#kb-view-matrix").hidden = mode !== "matrix";
    $("#kb-mode-graph").classList.toggle("is-active", mode === "graph");
    $("#kb-mode-matrix").classList.toggle("is-active", mode === "matrix");
    $("#kb-mode-graph").setAttribute("aria-selected", mode === "graph" ? "true" : "false");
    $("#kb-mode-matrix").setAttribute("aria-selected", mode === "matrix" ? "true" : "false");
    var grid = $(".kb-grid");
    if (grid) grid.setAttribute("data-kb-mode", mode);
    if (mode === "matrix" && KB.selected && kbSelectedKind() !== "intent") {
      KB.selected = null; KB.category = null;
    }
    kbRenderAll();
  }

  async function loadKnowledge(force) {
    var panel = $('[data-view-panel="knowledge"]');
    if (!panel) return;
    if (KB.data && !force) { kbRenderAll(); return; }
    try {
      KB.data = await api("/api/knowledge");
    } catch (e) {
      $("[data-kb-stats]").innerHTML = '<p class="ira-empty">读不到知识库：' + esc(e.message) + "</p>";
      $("#kb-detail").innerHTML = '<p class="ira-meta">接口 /api/knowledge 不可用。</p>';
      return;
    }
    renderKbChips();
    kbSetMode(KB.mode);
  }

  function bindKnowledge() {
    $("#kb-mode-graph").onclick = function () { kbSetMode("graph"); };
    $("#kb-mode-matrix").onclick = function () { kbSetMode("matrix"); };
    $("#kb-refresh").onclick = function () { loadKnowledge(true); };
    $("#kb-filter").addEventListener("input", function () {
      KB.filter = this.value.trim();
      kbRenderAll();
    });
    $('[data-view-panel="knowledge"]').addEventListener("keydown", function (e) {
      if (e.key === "Escape" && KB.selected) {
        KB.selected = null; KB.category = null; kbRenderAll();
      }
    });
    // 焦点可能停在任意按钮上（点的行、格子或节点），所以 Esc 也挂在 document；
    // 只在知识库页可见时生效，不影响其它页面的 Esc 行为。
    document.addEventListener("keydown", function (e) {
      var panel = $('[data-view-panel="knowledge"]');
      if (e.key !== "Escape" || !panel || panel.hidden || !KB.selected) return;
      KB.selected = null; KB.category = null; kbRenderAll();
    });
  }
  bindKnowledge();

  // 首屏把图标画出来（否则顶栏左上角是个空白方块）
  paintIcons(document);

  poll();
  async function pollLoop() { await poll(); setTimeout(pollLoop, 900); }
  setTimeout(pollLoop, 900);
})();
