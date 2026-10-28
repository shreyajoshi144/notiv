// Notiv UI core: DOM builder, icons, API client + session, toasts, dialogs, app shell.
// Everything user-controlled is inserted with textContent / text nodes — never parsed as HTML.

export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];

export function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k === "dataset") Object.assign(e.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (typeof v === "boolean") e.setAttribute(k, k.startsWith("aria-") ? String(v) : "");
    else e.setAttribute(k, v);
  }
  appendKids(e, kids);
  return e;
}
function appendKids(e, kids) {
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
}
export const clear = (el, ...kids) => { el.replaceChildren(); appendKids(el, kids); return el; };

const ICONS = {
  plus: "M12 5v14M5 12h14", search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4", upload: "M12 16V4m0 0L7 9m5-5l5 5M5 20h14",
  link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1",
  trash: "M4 7h16M10 11v6m4-6v6M6 7l1 13h10l1-13M9 7V4h6v3", edit: "M4 20h4L19 9l-4-4L4 16v4zM13.5 6.5l4 4",
  download: "M12 4v12m0 0l-5-5m5 5l5-5M5 20h14", share: "M8 12l8-5M8 12l8 5M6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  arrow: "M5 12h14m-6-6l6 6-6 6", back: "M19 12H5m6-6l-6 6 6 6", close: "M6 6l12 12M18 6L6 18", play: "M8 5v14l11-7z", check: "M5 12.5l4.5 4.5L19 7",
  book: "M5 4h10a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4zM8 17h10", note: "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h7", user: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21c0-4 3.6-6 8-6s8 2 8 6",
  canvas: "M4 5h6v5H4zM14 14h6v5h-6zM10 7.5h4a3 3 0 0 1 3 3V14", ask: "M4 5h16v11H9l-5 4zM9 9.5h6", study: "M12 4l9 4-9 4-9-4zM7 10.5V16c0 1.5 2.2 3 5 3s5-1.5 5-3v-5.5",
  info: "M12 8h.01M11 12h1v5h1M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z", warn: "M12 9v5m0 3h.01M10.3 4.2L2.6 18a2 2 0 0 0 1.7 3h15.4a2 2 0 0 0 1.7-3L13.7 4.2a2 2 0 0 0-3.4 0z",
  sign: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h9", copy: "M9 9h10v11H9zM5 15V4h10", more: "M5 12h.01M12 12h.01M19 12h.01", flip: "M4 8h13l-3-3M20 16H7l3 3",
  folder: "M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", clock: "M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z",
  archive: "M3 5h18v4H3zM5 9v10h14V9M10 13h4",
  home: "M4 11l8-7 8 7M6 10v10h12V10M10 20v-6h4v6", library: "M4 4v16M9 4v16M14 6l5 13M3 20h18", quiz: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01",
  timer: "M12 21a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM12 9v4l2 2M9.5 2.5h5", cat: "M5 20c-1-6 0-10 2-12L6 4l4 2h4l4-2-1 4c2 2 3 6 2 12zM9.5 13h.01M14.5 13h.01M11 16.5h2", gear: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.5-2.3 1a7 7 0 0 0-2-1.2L14.3 3h-4l-.3 2.6a7 7 0 0 0-2 1.2l-2.3-1-2 3.5 2 1.5A7 7 0 0 0 5 12c0 .4 0 .8.1 1.2l-2 1.5 2 3.5 2.3-1a7 7 0 0 0 2 1.2l.3 2.6h4l.3-2.6a7 7 0 0 0 2-1.2l2.3 1 2-3.5-2-1.5c.1-.4.1-.8.1-1.2z",
  pause: "M8 5v14M16 5v14", reset: "M4 12a8 8 0 1 0 3-6.2M4 4v5h5", refresh: "M20 11a8 8 0 0 0-14-4l-2 3m0-5v5h5M4 13a8 8 0 0 0 14 4l2-3m0 5v-5h-5",
};
export function icon(name, cls = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "ico " + cls); s.setAttribute("aria-hidden", "true"); s.setAttribute("focusable", "false");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", ICONS[name] || ICONS.info); s.append(p);
  return s;
}

/* ── formatting ─────────────────────────────────────────────────────────────────────── */
export const mmss = t => { t = Math.max(0, Math.floor(t || 0)); const hh = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60;
  return (hh ? hh + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0"); };
export function when(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000), diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return Math.floor(diff / 60) + " min ago";
  if (diff < 86400) return Math.floor(diff / 3600) + " h ago";
  if (diff < 86400 * 7) return Math.floor(diff / 86400) + " d ago";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
}
export const dateLong = ts => ts ? new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
export function duration(sec) {
  if (!sec) return "";
  const m = Math.round(sec / 60);
  return m < 1 ? Math.round(sec) + " s" : m < 60 ? m + " min" : Math.floor(m / 60) + " h " + (m % 60 ? (m % 60) + " min" : "");
}
// A stable accent per subject so a shelf of notebooks is scannable. Palette is the brand palette only.
const SPINES = ["#9c4a2f", "#5a6440", "#b8863a", "#4a433b", "#7a5a3c", "#6f7a52"];
export function spine(text) { let n = 0; for (const c of String(text || "")) n = (n * 31 + c.charCodeAt(0)) >>> 0; return SPINES[n % SPINES.length]; }

/* ── session + API ──────────────────────────────────────────────────────────────────── */
export class ApiError extends Error {
  constructor(status, detail) { super(typeof detail === "string" ? detail : (detail && detail.message) || "Request failed"); this.status = status; this.detail = detail; }
}
// Browser authentication is exclusively the server-issued HttpOnly cookie session.
// The backend still accepts bearer tokens for existing non-browser API integrations.
export const session = { token() { return null; }, set() { return false; }, clear() {} };
export function toSignIn(reason) {
  const next = location.pathname + location.search;
  location.replace("/sign-in?" + new URLSearchParams({ next, ...(reason ? { reason } : {}) }));
}
let ending = false;
export async function api(path, { method = "GET", json, form, headers = {}, raw = false, auth = true } = {}) {
  const hd = new Headers(headers);
  if (!["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase())) {
    const match = document.cookie.match(/(?:^|;\s*)notiv_csrf=([^;]+)/);
    if (match) hd.set("X-CSRF-Token", decodeURIComponent(match[1]));
  }
  let body;
  if (json !== undefined) { hd.set("Content-Type", "application/json"); body = JSON.stringify(json); } else if (form) body = form;
  let r;
  try { r = await fetch(path, { method, headers: hd, body, credentials: "same-origin" }); }
  catch (_) { throw new ApiError(0, "Can't reach Notiv. Check your connection and try again."); }
  if (r.status === 401 && auth && !ending && !location.pathname.startsWith("/sign-in") && !["/register", "/forgot-password", "/reset-password"].includes(location.pathname)) {
    ending = true; toSignIn("expired"); throw new ApiError(401, "Your session has ended.");
  }
  if (raw) { if (!r.ok) throw await errorFrom(r); return r; }
  if (r.status === 204) return null;
  let data = null;
  const ct = r.headers.get("content-type") || "";
  try { data = ct.includes("json") ? await r.json() : await r.text(); } catch (_) {}
  if (!r.ok) throw await errorFrom(r, data);
  return data;
}
async function errorFrom(r, data) {
  if (data === undefined) { try { data = await r.json(); } catch (_) { data = null; } }
  let detail = data && typeof data === "object" ? data.detail : data;
  if (Array.isArray(detail)) detail = detail.map(d => (d.loc || []).slice(1).join(".") + " " + d.msg).join("; ");
  return new ApiError(r.status, detail !== undefined && detail !== null && detail !== "" ? detail : r.statusText || "Request failed");
}
// Human wording for the errors people actually hit.
export function explain(e, ctx = "do that") {
  if (!(e instanceof ApiError)) return e && e.message ? e.message : "Something went wrong.";
  if (e.status === 0) return e.message;
  if (e.status === 429) { const s = e.detail && e.detail.retry_after; return `You're going too fast. Try again in ${s || "a minute"}${s ? " seconds" : ""}.`; }
  if (e.status === 413) return "That is larger than your plan allows. Try a smaller file or remove something first.";
  if (e.status === 404) return "That no longer exists, or it isn't yours to open.";
  if (e.status === 409) return typeof e.detail === "string" || (e.detail && e.detail.message) ? e.message : `Couldn't ${ctx} right now.`;
  if (e.status === 501) return e.message;
  if (e.status === 503) return "Notiv isn't fully configured on this server yet. " + e.message;
  if (e.status >= 500) return `Notiv hit a server problem while trying to ${ctx}. Nothing was lost; try again.`;
  return e.message;
}
export async function download(path, fallbackName) {
  const r = await api(path, { raw: true });
  const cd = r.headers.get("content-disposition") || "";
  const name = (cd.match(/filename="?([^";]+)"?/) || [])[1] || fallbackName;
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name }); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  return name;
}
export async function withBusy(btn, fn) {
  if (btn) { btn.classList.add("busy"); btn.disabled = true; }
  try { return await fn(); } finally { if (btn) { btn.classList.remove("busy"); btn.disabled = false; } }
}
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

/* ── live region + toasts ───────────────────────────────────────────────────────────── */
function ensureRegions() {
  if (!$("#toasts")) document.body.append(h("div", { id: "toasts", role: "region", "aria-label": "Notifications" }));
  if (!$("#live")) document.body.append(h("div", { id: "live", class: "sr", role: "status", "aria-live": "polite" }));
}
export const announce = m => { ensureRegions(); const l = $("#live"); l.textContent = ""; setTimeout(() => { l.textContent = m; }, 30); };
export function toast(message, kind = "ok", ms = 4800) {
  ensureRegions();
  for (const old of $$("#toasts .toast")) if (old.firstChild.textContent === message) old.remove();      // never stack identical toasts
  const t = h("div", { class: "toast " + (kind === "err" ? "err" : ""), role: kind === "err" ? "alert" : "status" }, h("span", { text: message }),
    h("button", { "aria-label": "Dismiss", onclick: () => t.remove() }, "×"));
  $("#toasts").append(t);
  if (kind !== "err") setTimeout(() => t.remove(), ms); else setTimeout(() => t.remove(), 9000);
  return t;
}

/* ── dialogs (native <dialog>: focus trap, Esc, inert background) ───────────────────── */
export function dialog({ title, body, actions = [], wide = false, onClose, describedBy }) {
  const dlg = h("dialog", { class: wide ? "wide" : "", "aria-labelledby": "dlg-title" });
  const done = v => { dlg.close(v); };
  const foot = h("div", { class: "dlg-foot" });
  for (const a of actions) {
    foot.append(h("button", { class: "btn " + (a.kind || ""), type: a.submit ? "submit" : "button", value: a.submit ? (a.value ?? "ok") : null, dataset: a.id ? { act: a.id } : {},
      onclick: a.submit ? null : async e => { if (a.onClick) { const r = await a.onClick(e, dlg); if (r === false) return; } done(a.value ?? a.id ?? ""); } }, a.label));
  }
  const inner = [h("div", { class: "dlg-head" }, h("h2", { id: "dlg-title", text: title }),
    h("button", { class: "iconbtn", type: "button", "aria-label": "Close", onclick: () => done("") }, icon("close"))),
    h("div", { class: "dlg-body" }, body), foot];
  const form = h("form", { method: "dialog", style: { display: "contents" } }, inner);
  dlg.append(form);
  document.body.append(dlg);
  const opener = document.activeElement;
  const result = new Promise(res => dlg.addEventListener("close", () => {
    dlg.remove(); if (opener && opener.focus) try { opener.focus(); } catch (_) {}
    if (onClose) onClose(dlg.returnValue); res(dlg.returnValue);
  }, { once: true }));
  dlg.addEventListener("click", e => { if (e.target === dlg) dlg.close(""); });
  dlg.showModal();
  const first = dlg.querySelector("[autofocus],input,textarea,select") || dlg.querySelector(".dlg-foot .btn");
  if (first) first.focus();
  return { el: dlg, close: v => dlg.close(v), result, form };
}
export function confirmDialog({ title, body, confirm = "Confirm", danger = false, cancel = "Cancel" }) {
  return dialog({ title, body: h("p", { text: body }), actions: [{ label: cancel, id: "cancel", value: "" }, { label: confirm, id: "ok", kind: danger ? "danger" : "primary", value: "ok" }] })
    .result.then(v => v === "ok");
}
// fields: [{name,label,type:'text'|'textarea'|'select',value,required,max,hint,options:[{value,label}]}]
export function formDialog({ title, intro, fields, submit = "Save", wide = false }) {
  const els = {};
  const body = [intro ? h("p", { class: "muted", text: intro }) : null, ...fields.map(f => {
    const id = "f-" + f.name;
    const control = f.type === "textarea" ? h("textarea", { id, maxlength: f.max, rows: f.rows || 4, required: f.required, placeholder: f.placeholder })
      : f.type === "select" ? h("select", { id }, ...(f.options || []).map(o => h("option", { value: o.value, selected: String(o.value) === String(f.value) }, o.label)))
      : h("input", { id, type: f.type || "text", maxlength: f.max, required: f.required, placeholder: f.placeholder, autocomplete: "off" });
    if (f.type !== "select") control.value = f.value ?? "";
    els[f.name] = control;
    return h("div", { class: "field" }, h("label", { for: id, text: f.label }), control, f.hint ? h("p", { class: "hint", text: f.hint }) : null);
  })];
  const d = dialog({ title, body, wide, actions: [{ label: "Cancel", id: "cancel", value: "" }, { label: submit, id: "ok", kind: "primary", submit: true, value: "ok" }] });
  return d.result.then(v => v === "ok" ? Object.fromEntries(Object.entries(els).map(([k, el]) => [k, el.value.trim()])) : null);
}

/* ── popover menu ───────────────────────────────────────────────────────────────────── */
export function menuButton({ label, content, build, cls = "btn", align = "right" }) {
  const wrap = h("div", { class: "usermenu", style: { position: "relative", display: "inline-block" } });
  const btn = h("button", { class: cls, type: "button", "aria-haspopup": "menu", "aria-expanded": "false" }, content || label);
  let menu = null;
  const close = () => { if (menu) { menu.remove(); menu = null; btn.setAttribute("aria-expanded", "false"); document.removeEventListener("click", away, true); document.removeEventListener("keydown", key, true); } };
  const away = e => { if (menu && !wrap.contains(e.target)) close(); };
  const key = e => { if (e.key === "Escape") { close(); btn.focus(); } };
  btn.addEventListener("click", () => {
    if (menu) return close();
    menu = h("div", { class: "menu", role: "menu", style: align === "left" ? { left: 0, right: "auto" } : {} }, build(close));
    wrap.append(menu); btn.setAttribute("aria-expanded", "true");
    const f = menu.querySelector("button,a"); if (f) f.focus();
    document.addEventListener("click", away, true); document.addEventListener("keydown", key, true);
  });
  wrap.append(btn);
  return wrap;
}

/* ── app shell ──────────────────────────────────────────────────────────────────────── */
let me = null;
export async function loadMe() {
  if (me) return me;
  try { me = await api("/auth/me"); } catch (e) { if (e.status === 401) throw e; me = { id: null }; }
  return me;
}
export async function signOut() {
  let url = null;
  const csrf = document.cookie.match(/(?:^|;\s*)notiv_csrf=([^;]+)/);
  const headers = csrf ? { "X-CSRF-Token": decodeURIComponent(csrf[1]) } : {};
  try {
    const r = await fetch("/auth/logout", { method: "POST", credentials: "same-origin", headers });
    if (r.ok) { const data = await r.json(); url = data && data.end_session_url; }
  } catch (_) { /* Always leave the workspace; server-side revocation status is audited separately. */ }
  location.replace(url || "/sign-in?reason=signed-out");
}
export function logoMark(size = 44) {
  const ns = "http://www.w3.org/2000/svg", el = (t, at, ...k) => { const e = document.createElementNS(ns, t); for (const [a, v] of Object.entries(at)) e.setAttribute(a, v); e.append(...k); return e; };
  return el("svg", { viewBox: "0 0 64 64", width: size, height: size, "aria-hidden": "true" },
    el("defs", {}, el("linearGradient", { id: "lg", x1: 0, y1: 0, x2: 0, y2: 1 }, el("stop", { offset: 0, "stop-color": "#1f5f94" }), el("stop", { offset: ".62", "stop-color": "#6a6f8f" }), el("stop", { offset: 1, "stop-color": "#f36b1d" }))),
    el("rect", { width: 64, height: 64, rx: 16, fill: "url(#lg)" }), el("circle", { cx: 38, cy: 33, r: 9, fill: "#fff4e4" }), el("path", { d: "M0 50c14-12 30-17 64-9v23H0z", fill: "#16304f" }));
}
export const NAV = [
  ["desk", "/app", "Desk", "home"], ["notebooks", "/app/notebooks", "Notebooks", "book"], ["library", "/app?view=library", "Library", "library"],
  ["ask", "/static/study-companion.html", "Ask", "ask"], ["practice", "/static/practice-quiz.html", "Practice", "quiz"],
];
export async function mountShell(active, { wide = false, narrow = false } = {}) {
  ensureRegions();
  document.body.classList.add("workspace");
  document.body.prepend(h("a", { class: "skip", href: "#main", text: "Skip to content" }));
  const item = ([key, href, text, ic]) => h("a", { class: "item", href, "aria-current": active === key ? "page" : null }, icon(ic), h("span", { text }));
  const acct = h("a", { class: "item", href: "/static/account.html", "aria-current": active === "account" ? "page" : null, "aria-label": "Account and settings" }, icon("gear"), h("span", { text: "Account" }));
  const rail = h("aside", { class: "rail" },
    h("a", { class: "logo", href: "/app", "aria-label": "Notiv — study desk" }, logoMark(44)),
    h("nav", { "aria-label": "Primary" }, NAV.map(item), acct), h("div", { class: "grow" }));
  const main = h("main", { id: "main", class: "page" + (narrow ? " narrow" : ""), tabindex: "-1" });
  document.body.insertBefore(rail, document.body.children[1]);
  document.body.insertBefore(main, rail.nextSibling);
  try { await loadMe(); } catch (_) { /* api() already redirected on 401 */ }
  import("/static/ui/companion.js").then(m => m.mountCompanion({ active })).catch(() => {});
  return main;
}
export function pageError(main, e, retry, what = "this page") {
  clear(main, h("div", { class: "empty" }, h("h2", { text: e.status === 404 ? "We can't find that" : `Couldn't load ${what}` }),
    h("p", { text: explain(e, "load " + what) }),
    h("div", { class: "actions" }, retry && e.status !== 404 ? h("button", { class: "btn primary", onclick: retry }, icon("refresh"), "Try again") : null,
      h("a", { class: "btn", href: "/app" }, "Back to your desk"))));
  main.focus();
}
export const skeletons = (n = 3, cls = "block") => Array.from({ length: n }, () => h("div", { class: "skeleton " + cls }));
export function notice(kind, ...kids) { return h("div", { class: "notice " + kind, role: kind === "err" ? "alert" : "status" }, icon(kind === "ok" ? "check" : kind === "err" || kind === "warn" ? "warn" : "info"), h("div", null, ...kids)); }
export function stateChip(status) {
  const map = { done: "Ready", queued: "Waiting", running: "Processing", processing: "Processing", failed: "Needs attention", created: "No source yet", cancelled: "Cancelled" };
  return h("span", { class: "chip " + status }, map[status] || status);
}
export function onKeyActivate(el, fn) { el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(e); } }); }
export function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = h("textarea", { style: { position: "fixed", opacity: 0 } }); ta.value = text; document.body.append(ta); ta.select();
  try { document.execCommand("copy"); } finally { ta.remove(); }
  return Promise.resolve();
}
