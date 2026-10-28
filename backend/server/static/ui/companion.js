// Notiv desk companion: a study cat (launcher) + a focus timer. Frontend only; uses no new API.
// The timer stores an absolute end time, so it stays accurate after tab inactivity, sleep or reload.

const KEY = "notiv.focus.v1", PREF = "notiv.companion.v1", LOG = "notiv.focus.log.v1", GOAL = "notiv.focus.goal.v1";
const load = (k, d) => { try { return { ...d, ...JSON.parse(localStorage.getItem(k) || "{}") }; } catch (_) { return d; } };
const save = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) {} };

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
}

const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...kids) { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); e.append(...kids); return e; }
const C = "#2c3b5e", CREAM = "#f3e6d3", EMBER = "#e8743a", GOLD = "#f6b26b", INK = "#17213a";
function catSvg() {
  return svg("svg", { viewBox: "0 0 130 112", "aria-hidden": "true", class: "cat-svg" },
    svg("path", { class: "cat-tail", d: "M104 92c18 2 25-12 19-26-3-7-11-5-10 2 1 8-4 14-14 15", fill: C }),
    svg("ellipse", { cx: 64, cy: 80, rx: 46, ry: 30, fill: C }), svg("ellipse", { cx: 64, cy: 88, rx: 28, ry: 18, fill: CREAM }),
    svg("path", { d: "M24 42 26 12l24 17M104 42 102 12 78 29", fill: C }), svg("path", { d: "M31 35l1-14 11 8M97 35l-1-14-11 8", fill: EMBER, opacity: ".85" }),
    svg("ellipse", { cx: 64, cy: 52, rx: 42, ry: 32, fill: C }),
    svg("g", { class: "cat-eyes" }, svg("ellipse", { cx: 48, cy: 50, rx: 6, ry: 7, fill: GOLD }), svg("ellipse", { cx: 80, cy: 50, rx: 6, ry: 7, fill: GOLD }),
      svg("ellipse", { cx: 48, cy: 51, rx: 2.4, ry: 4.6, fill: INK }), svg("ellipse", { cx: 80, cy: 51, rx: 2.4, ry: 4.6, fill: INK })),
    svg("path", { d: "M61 61h6l-3 3.4z", fill: EMBER }), svg("path", { d: "M64 64.5c-2 4-7 4-9 2M64 64.5c2 4 7 4 9 2", stroke: CREAM, "stroke-width": "1.7", fill: "none", "stroke-linecap": "round" }),
    svg("path", { d: "M30 58l-12-3M30 63l-12 3M98 58l12-3M98 63l12 3", stroke: CREAM, "stroke-width": "1.2", opacity: ".7", "stroke-linecap": "round" }),
    svg("ellipse", { cx: 38, cy: 63, rx: 6, ry: 3.4, fill: EMBER, opacity: ".35" }), svg("ellipse", { cx: 90, cy: 63, rx: 6, ry: 3.4, fill: EMBER, opacity: ".35" }),
    svg("ellipse", { cx: 44, cy: 106, rx: 13, ry: 6.5, fill: C }), svg("ellipse", { cx: 84, cy: 106, rx: 13, ry: 6.5, fill: C }));
}

// ── focus log: minutes of completed focus per local day (for "today", the daily goal and the streak) ─────
const dayKey = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function readLog() { try { const v = JSON.parse(localStorage.getItem(LOG) || "{}"); return v && typeof v === "object" ? v : {}; } catch (_) { return {}; } }
function logFocus(min) {
  const log = readLog(), k = dayKey(); log[k] = (log[k] || 0) + min;
  const keep = Object.keys(log).sort().slice(-90); save(LOG, Object.fromEntries(keep.map(d => [d, log[d]])));
}
/** {today, goal, streak, week:[{day, min}]} — read by the desk's Today panel. */
export function focusStats() {
  const log = readLog(), goal = Math.max(10, Math.min(600, Number(load(GOAL, { min: 50 }).min) || 50));
  let streak = 0; const d = new Date();
  if (!log[dayKey(d)]) d.setDate(d.getDate() - 1);                 // today not started yet: the streak can still be alive
  while (log[dayKey(d)] > 0) { streak++; d.setDate(d.getDate() - 1); }
  const week = [];
  for (let i = 6; i >= 0; i--) { const x = new Date(); x.setDate(x.getDate() - i); week.push({ day: x, min: log[dayKey(x)] || 0 }); }
  return { today: log[dayKey()] || 0, goal, streak, week };
}
export function setFocusGoal(min) { save(GOAL, { min: Math.max(10, Math.min(600, Math.round(min))) }); }

// ── focus timer state ───────────────────────────────────────────────────────────────────
let T = load(KEY, { mode: "focus", focusMin: 25, breakMin: 5, endAt: null, remaining: 25 * 60000, notify: false, sound: true, rounds: 0 });
const dur = m => (m === "focus" ? T.focusMin : T.breakMin) * 60000;
const left = () => T.endAt ? Math.max(0, T.endAt - Date.now()) : T.remaining;
const fmt = ms => { const s = Math.ceil(ms / 1000); return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
const persist = () => save(KEY, T);

function start() { if (T.endAt) return; T.endAt = Date.now() + T.remaining; persist(); render(); }
function pause() { if (!T.endAt) return; T.remaining = left(); T.endAt = null; persist(); render(); }
function reset() { T.endAt = null; T.remaining = dur(T.mode); persist(); render(); }
function setMode(m) { T.mode = m; T.endAt = null; T.remaining = dur(m); persist(); render(); }
function finish() {
  const was = T.mode; if (was === "focus") { T.rounds++; logFocus(T.focusMin); window.dispatchEvent(new CustomEvent("notiv:focus-logged")); }
  T.mode = was === "focus" ? "break" : "focus"; T.endAt = null; T.remaining = dur(T.mode); persist();
  const msg = was === "focus" ? "Focus session done. Time for a break." : "Break's over. Ready to focus?";
  say(msg); if (T.sound !== false) chime();
  if (T.notify && "Notification" in window && Notification.permission === "granted") { try { new Notification("Notiv focus timer", { body: msg, icon: "/static/favicon.svg" }); } catch (_) {} }
  render();
}
function chime() {
  try { const a = new (window.AudioContext || window.webkitAudioContext)(), o = a.createOscillator(), g = a.createGain();
    o.frequency.value = 660; g.gain.setValueAtTime(.0001, a.currentTime); g.gain.exponentialRampToValueAtTime(.12, a.currentTime + .02); g.gain.exponentialRampToValueAtTime(.0001, a.currentTime + .9);
    o.connect(g).connect(a.destination); o.start(); o.stop(a.currentTime + 1); } catch (_) {}
}

// ── ambient study sound (generated in the browser: no audio files, plays only after the person picks one) ─────
const AMB = { ctx: null, src: null, gain: null, kind: "off" };
function noiseBuffer(ctx, kind) {
  const n = ctx.sampleRate * 3, b = ctx.createBuffer(1, n, ctx.sampleRate), d = b.getChannelData(0);
  let last = 0, b0 = 0, b1 = 0, b2 = 0, b3 = 0, b4 = 0, b5 = 0, b6 = 0;
  for (let i = 0; i < n; i++) {
    const w = Math.random() * 2 - 1;
    if (kind === "brown") { last = (last + 0.02 * w) / 1.02; d[i] = last * 3.5; continue; }
    b0 = .99886 * b0 + w * .0555179; b1 = .99332 * b1 + w * .0750759; b2 = .969 * b2 + w * .153852; b3 = .8665 * b3 + w * .3104856;
    b4 = .55 * b4 + w * .5329522; b5 = -.7616 * b5 - w * .016898; d[i] = (b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * .5362) * .11; b6 = w * .115926;
  }
  return b;
}
function ambience(kind, vol) {
  try {
    if (!AMB.ctx) AMB.ctx = new (window.AudioContext || window.webkitAudioContext)();
    const ctx = AMB.ctx, now = ctx.currentTime;
    if (AMB.src) { const old = AMB.src, g = AMB.gain; g.gain.cancelScheduledValues(now); g.gain.setTargetAtTime(0, now, .25); setTimeout(() => { try { old.stop(); } catch (_) {} }, 1200); AMB.src = null; }
    AMB.kind = kind;
    if (kind === "off") return;
    ctx.resume();
    const src = ctx.createBufferSource(); src.buffer = noiseBuffer(ctx, kind === "rain" ? "pink" : "brown"); src.loop = true;
    const f1 = ctx.createBiquadFilter(), f2 = ctx.createBiquadFilter(), g = ctx.createGain();
    if (kind === "rain") { f1.type = "highpass"; f1.frequency.value = 500; f2.type = "lowpass"; f2.frequency.value = 7000; }
    else if (kind === "cafe") { f1.type = "bandpass"; f1.frequency.value = 420; f1.Q.value = .7; f2.type = "lowpass"; f2.frequency.value = 2200; }
    else { f1.type = "lowpass"; f1.frequency.value = 900; f2.type = "highpass"; f2.frequency.value = 30; }
    g.gain.value = 0; g.gain.setTargetAtTime(vol, now, .6);
    src.connect(f1).connect(f2).connect(g).connect(ctx.destination); src.start();
    AMB.src = src; AMB.gain = g;
  } catch (_) { /* Web Audio unavailable: ambience simply stays off */ }
}

// ── UI ──────────────────────────────────────────────────────────────────────────────────
const P = load(PREF, { hidden: false, calm: false, vol: 0.18 });
let root, launcher, timerPanel, pill, catBtn, live, origTitle = document.title;

function say(t) { if (live) { live.textContent = ""; setTimeout(() => live.textContent = t, 30); } }

function chatHref() {
  const p = location.pathname; let m;
  if ((m = p.match(/^\/app\/lectures\/(\d+)/))) return `/static/study-companion.html?lecture=${m[1]}`;
  if ((m = p.match(/^\/app\/notebooks\/(\d+)/))) return `/static/study-companion.html?notebook=${m[1]}`;
  if ((m = p.match(/^\/app\/courses\/(\d+)/))) return `/static/study-companion.html?course=${m[1]}`;
  return "/static/study-companion.html";
}

function quizHref() { const m = location.pathname.match(/^\/app\/lectures\/(\d+)/); return "/static/practice-quiz.html" + (m ? `?lecture=${m[1]}` : ""); }

function build() {
  root = el("div", { class: "companion" + (P.hidden ? " is-hidden" : "") + (P.calm ? " calm" : "") });
  live = el("div", { class: "sr", role: "status", "aria-live": "polite" });

  catBtn = el("button", { class: "cat-btn", type: "button", "aria-haspopup": "true", "aria-expanded": "false", "aria-controls": "cmp-menu", "aria-label": "Study companion" });
  catBtn.append(catSvg());
  catBtn.addEventListener("click", () => toggle(launcher));

  pill = el("button", { class: "focus-pill", type: "button", hidden: true, onclick: () => toggle(timerPanel, true) });

  launcher = el("div", { class: "cmp-pop cmp-menu", id: "cmp-menu", role: "menu", hidden: true, "aria-label": "Study companion" },
    el("p", { class: "cmp-hi", text: "Hi! What shall we do?" }),
    el("a", { role: "menuitem", class: "cmp-item", href: chatHref() }, el("strong", { text: "Ask your study companion" }), el("span", { text: "Questions answered from your lectures, with sources" })),
    el("button", { role: "menuitem", type: "button", class: "cmp-item", onclick: () => toggle(timerPanel, true) }, el("strong", { text: "Focus timer" }), el("span", { text: "Pomodoro focus and break sessions" })),
    el("a", { role: "menuitem", class: "cmp-item", href: quizHref() }, el("strong", { text: "Practice quiz" }), el("span", { text: "A quiz from a lecture, tailored to your goal" })),
    el("button", { role: "menuitem", type: "button", class: "cmp-item sub", onclick: () => { P.calm = !P.calm; save(PREF, P); root.classList.toggle("calm", P.calm); calmBtn(); } }),
    el("button", { role: "menuitem", type: "button", class: "cmp-item sub", onclick: () => { P.hidden = true; save(PREF, P); root.classList.add("is-hidden"); closeAll(); showBtn.focus(); } }, "Tuck the cat away"));
  const calmBtn = () => { launcher.children[4].textContent = P.calm ? "Turn animations on" : "Calm the animations"; };
  calmBtn();

  const showBtn = el("button", { class: "cat-peek", type: "button", "aria-label": "Show study companion", onclick: () => { P.hidden = false; save(PREF, P); root.classList.remove("is-hidden"); catBtn.focus(); } }, "🐾");

  const num = (id, label, key) => {
    const i = el("input", { id, type: "number", min: 1, max: key === "focusMin" ? 120 : 60, value: T[key], inputmode: "numeric" });
    i.addEventListener("change", () => { const v = Math.round(Number(i.value)); if (!v || v < 1) { i.value = T[key]; return; }
      T[key] = Math.min(Number(i.max), v); i.value = T[key]; if (!T.endAt && ((key === "focusMin" && T.mode === "focus") || (key === "breakMin" && T.mode === "break"))) T.remaining = dur(T.mode); persist(); render(); });
    return el("label", { class: "ft-num", for: id }, el("span", { text: label }), i, el("span", { class: "faint", text: "min" }));
  };
  const snd = el("input", { type: "checkbox", id: "ft-sound" }); snd.checked = T.sound !== false; snd.addEventListener("change", () => { T.sound = snd.checked; persist(); });
  const notif = el("input", { type: "checkbox", id: "ft-notify" });
  notif.checked = !!T.notify;
  notif.addEventListener("change", async () => {
    if (notif.checked && "Notification" in window && Notification.permission !== "granted") {
      const p = await Notification.requestPermission().catch(() => "denied");
      if (p !== "granted") { notif.checked = false; say("Notifications are blocked in this browser."); }
    }
    T.notify = notif.checked; persist();
  });

  timerPanel = el("section", { class: "cmp-pop ft", hidden: true, "aria-labelledby": "ft-h" },
    el("div", { class: "ft-head" }, el("h2", { id: "ft-h", text: "Focus timer" }), el("button", { class: "iconbtn", type: "button", "aria-label": "Close focus timer", onclick: closeAll, text: "✕" })),
    el("div", { class: "ft-modes", role: "group", "aria-label": "Session type" },
      el("button", { type: "button", class: "ft-mode", "data-m": "focus", onclick: () => setMode("focus"), text: "Focus" }),
      el("button", { type: "button", class: "ft-mode", "data-m": "break", onclick: () => setMode("break"), text: "Break" })),
    el("div", { class: "ft-dial" }, el("p", { class: "ft-time", role: "timer", "aria-live": "off" }), el("p", { class: "ft-sub" })),
    el("div", { class: "ft-ctl" },
      el("button", { type: "button", class: "btn primary ft-go" }),
      el("button", { type: "button", class: "btn", onclick: reset, text: "Reset" })),
    el("div", { class: "ft-amb" }, el("span", { class: "ft-amb-l", text: "Ambience" }),
      el("div", { class: "ft-amb-seg", role: "group", "aria-label": "Ambient study sound" },
        ...[["off", "Off"], ["rain", "Rain"], ["cafe", "Café"], ["brown", "Deep"]].map(([k, t]) =>
          el("button", { type: "button", class: "ft-amb-b", "data-k": k, "aria-pressed": String(k === "off"), text: t, title: k === "brown" ? "Deep brown noise" : null,
            onclick: () => { ambience(k, P.vol); timerPanel.querySelectorAll(".ft-amb-b").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.k === k))); say(k === "off" ? "Ambience off" : `${t} ambience on`); } }))),
      el("input", { type: "range", class: "ft-vol", min: "0.02", max: "0.6", step: "0.02", value: String(P.vol), "aria-label": "Ambience volume",
        oninput: e => { P.vol = Number(e.target.value); save(PREF, P); if (AMB.gain && AMB.ctx) AMB.gain.gain.setTargetAtTime(P.vol, AMB.ctx.currentTime, .1); } })),
    el("details", { class: "ft-set" }, el("summary", { text: "Settings" }),
      el("div", { class: "ft-grid" }, num("ft-f", "Focus", "focusMin"), num("ft-b", "Break", "breakMin")),
      el("label", { class: "check", for: "ft-sound" }, snd, "Play a soft chime when a session ends"),
      el("label", { class: "check", for: "ft-notify" }, notif, "Notify me when a session ends")));
  timerPanel.querySelector(".ft-dial").prepend(svg("svg", { viewBox: "0 0 100 100", "aria-hidden": "true" }, svg("circle", { cx: 50, cy: 50, r: 44, class: "ft-track" }), svg("circle", { cx: 50, cy: 50, r: 44, class: "ft-prog", pathLength: 100 })));
  timerPanel.querySelector(".ft-go").addEventListener("click", () => T.endAt ? (pause(), say("Paused")) : (start(), say("Timer running")));

  root.append(timerPanel, launcher, pill, catBtn, showBtn, live);
  document.body.append(root);

  document.addEventListener("keydown", e => { if (e.key === "Escape" && (!launcher.hidden || !timerPanel.hidden)) { closeAll(); catBtn.focus(); } });
  document.addEventListener("click", e => { if (!root.contains(e.target)) { launcher.hidden = true; catBtn.setAttribute("aria-expanded", "false"); } });
  launcher.addEventListener("keydown", e => {
    const items = [...launcher.querySelectorAll(".cmp-item")], i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
  });
}
function closeAll() { launcher.hidden = true; timerPanel.hidden = true; catBtn.setAttribute("aria-expanded", "false"); }
function toggle(panel, only) {
  const open = panel.hidden || only;
  launcher.hidden = true; timerPanel.hidden = true;
  panel.hidden = !open; catBtn.setAttribute("aria-expanded", String(!launcher.hidden));
  if (open) (panel.querySelector(".cmp-item, .ft-go") || panel).focus();
}

function render() {
  if (!timerPanel) return;
  const ms = left(), running = !!T.endAt, total = dur(T.mode);
  timerPanel.querySelector(".ft-time").textContent = fmt(ms);
  timerPanel.querySelector(".ft-time").setAttribute("aria-label", `${Math.ceil(ms / 60000)} minutes remaining`);
  const today = focusStats().today;                               // real per-day minutes (the old counter never reset)
  timerPanel.querySelector(".ft-sub").textContent = `${T.mode === "focus" ? "Focus" : "Break"} · ${running ? "running" : ms < total ? "paused" : "ready"}${today ? ` · ${today} min today` : ""}`;
  timerPanel.querySelector(".ft-prog").style.strokeDashoffset = String(100 - (100 * (total - ms)) / total);
  const go = timerPanel.querySelector(".ft-go"); go.textContent = running ? "Pause" : ms < total ? "Resume" : "Start";
  timerPanel.querySelectorAll(".ft-mode").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.m === T.mode)));
  timerPanel.classList.toggle("is-break", T.mode === "break");
  pill.hidden = !running && ms >= total;
  pill.textContent = `${T.mode === "focus" ? "Focus" : "Break"} ${fmt(ms)}${running ? "" : " · paused"}`;
  pill.setAttribute("aria-label", `Focus timer: ${pill.textContent}. Open timer`);
  document.title = running ? `${fmt(ms)} · ${origTitle}` : origTitle;
}

function tick() { if (T.endAt && left() <= 0) finish(); else render(); }

export function mountCompanion({ active } = {}) {
  if (document.querySelector(".companion")) return;
  if (!document.querySelector('link[href$="companion.css"]')) { const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/ui/companion.css"; document.head.append(l); }
  build(); render();
  setInterval(tick, 500);
  document.addEventListener("visibilitychange", tick);
  window.addEventListener("storage", e => { if (e.key === KEY) { T = load(KEY, T); render(); } });
  // the desk's "Start a focus session" button (and anything else) can start a focus round without opening the menu
  window.addEventListener("notiv:focus", () => { if (P.hidden) { P.hidden = false; save(PREF, P); root.classList.remove("is-hidden"); }
    if (!T.endAt) { if (T.mode !== "focus") setMode("focus"); start(); say("Focus session started"); } toggle(timerPanel, true); });
  new MutationObserver(() => { if (!document.title.match(/^\d\d:\d\d · /)) origTitle = document.title; }).observe(document.querySelector("title") || document.head, { childList: true });
}

