// Full-page study companion chat. It IS Ask Notiv: same endpoints (/lectures/{id}/chat), same grounded answers,
// same evidence citations. No separate chatbot and no client-side answers.
import { $, h, clear, api, mountShell, icon, toast, explain, confirmDialog, notice, announce } from "/static/ui/core.js";
import { citeLink } from "/static/ui/render.js";

const REASONS = { out_of_scope: "Nothing in this lecture matches that slide or time.", no_match: "Nothing in this lecture matches that.", weak_match: "The lecture touches on related words, but not enough to answer that.",
  unsafe_evidence: "The matching passages couldn't be used safely.", no_terms: "Ask about a topic, or about a slide or time (for example: what is on slide 3?).", model_declined: "The lecture evidence doesn't answer that." };

const qs = new URLSearchParams(location.search);
let main, lectures = [], cur = null, msgs = [], sending = false, log, input, sendBtn, starters;

const ltitle = id => { const l = lectures.find(x => x.id === id); return (l && (l.title || l.label)) || "this lecture"; };

async function boot() {
  main = await mountShell("ask");
  clear(main, h("div", { class: "skeleton block" }));
  try {
    const all = (await api("/lectures?limit=200")).items || [];
    let allowed = null;
    if (qs.get("notebook")) { try { const nb = await api(`/notebooks/${Number(qs.get("notebook"))}`); allowed = new Set((nb.sources || []).map(s => s.source_id)); } catch (_) {} }
    if (qs.get("course")) allowed = new Set(all.filter(l => String(l.course_id) === qs.get("course")).map(l => l.id));
    lectures = all.filter(l => l.status === "done" && (!allowed || allowed.has(l.id)));
    if (allowed && !lectures.length) lectures = all.filter(l => l.status === "done");
  } catch (e) {
    clear(main, notice("err", h("p", { text: explain(e, "load your lectures") }), h("button", { class: "btn", type: "button", onclick: () => location.reload() }, "Try again")));
    return;
  }
  const want = Number(qs.get("lecture"));
  cur = lectures.some(l => l.id === want) ? want : lectures[0] && lectures[0].id;
  layout();
}

function layout() {
  const back = document.referrer && new URL(document.referrer).origin === location.origin ? document.referrer : "/app";
  if (!lectures.length) {
    clear(main, h("div", { class: "chat-head" }, h("div", null, h("h1", { text: "Your study companion" }), h("p", { class: "lede", text: "Answers come only from your processed lectures, with sources." }))),
      h("div", { class: "empty" }, h("h2", { text: "No processed lecture yet" }),
        h("p", { text: "Upload a recording (MP3 or MP4) with its slides (PDF or PPTX) on your desk. Once it finishes processing, you can ask about it here." }),
        h("div", { class: "actions" }, h("a", { class: "btn primary", href: "/app" }, "Go to your desk"))));
    return;
  }
  const sel = h("select", { id: "cc-src", onchange: e => { cur = Number(e.target.value); history.replaceState(null, "", `?lecture=${cur}`); loadChat(); } },
    ...lectures.map(l => h("option", { value: l.id, selected: l.id === cur, text: l.title || `Lecture ${l.id}` })));
  log = h("div", { class: "chat-log", role: "log", "aria-live": "polite", "aria-label": "Conversation" });
  starters = h("div", { class: "chat-starters" });
  input = h("textarea", { id: "cc-in", rows: 1, maxlength: 500, "aria-label": "Your question", placeholder: "Ask about this lecture…",
    onkeydown: e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } },
    oninput: e => { e.target.style.height = "auto"; e.target.style.height = Math.min(200, e.target.scrollHeight) + "px"; } });
  sendBtn = h("button", { class: "btn primary", type: "submit" }, "Ask");
  clear(main, h("div", { class: "chat-page" },
    h("div", { class: "chat-head" },
      h("div", null, h("p", { class: "eyebrow faint mono", text: "Ask Notiv" }), h("h1", { text: "Your study companion" }),
        h("p", { class: "lede", text: "Answers come only from what was said and shown in the lecture. Each claim links to its source." })),
      h("div", { class: "field" }, h("label", { for: "cc-src", text: "Ask about" }), sel)),
    log,
    h("div", null, starters,
      h("form", { class: "chat-form", onsubmit: e => { e.preventDefault(); send(); } }, input, sendBtn),
      h("div", { class: "chat-tools" }, h("span", { text: "Enter to send · Shift+Enter for a new line" }),
        h("span", null, h("a", { href: `/app/lectures/${cur}`, id: "cc-open" }, "Open lecture"), " · ", h("a", { href: back }, "Back"), " · ",
          h("button", { class: "btn sm quiet", type: "button", onclick: reset }, icon("trash"), "Clear conversation"))))));
  loadChat();
}

function cited(m) {
  const by = new Map((m.citations || []).map(c => [c.evidence_id, c])); const out = [];
  for (const part of String(m.content || "").split(/(\[E\d+\])/)) {
    const mm = part.match(/^\[E(\d+)\]$/); if (!mm) { out.push(part); continue; }
    const c = by.get(Number(mm[1])); if (c) out.push(" ", citeLink({ ...c, lecture_id: cur }));
  }
  return out;
}
async function askGK(m, btn) {
  btn.disabled = true;
  try { const g = await api(`/lectures/${cur}/chat/${m.message_id}/general-knowledge`, { method: "POST" }); const i = msgs.findIndex(x => x.message_id === m.message_id); msgs.splice(i + 1, 0, g); m.general_knowledge_offer = false; draw(); }
  catch (e) { btn.disabled = false; toast(explain(e, "get a general-knowledge explanation"), "err"); }
}
function gkCard(m) {
  return h("div", { class: "msg bot none" }, h("div", { class: "meta" }, h("span", { class: "origin general_knowledge", text: "General knowledge — not from your uploaded material" })),
    h("div", { class: "txt", text: m.content }), h("p", { class: "faint", text: "This explanation comes from the AI model's general knowledge, not from this lecture, so it has no sources. Check it before relying on it." }));
}
function bot(m) {
  if (m.general_knowledge) return gkCard(m);
  const live = (m.citations || []).filter(c => c.available !== false);
  return h("div", { class: "msg bot" + (m.grounded ? "" : " none") },
    h("div", { class: "meta" }, h("span", { class: "origin notiv_generated", text: "Notiv" }), m.grounded ? h("span", { text: m.answer_mode === "llm" ? "Written from lecture evidence" : "Quotes from the lecture" }) : null, m.degraded ? h("span", { text: "AI wording unavailable" + (m.degraded_reason ? ": " + m.degraded_reason : ".") + " Showing lecture quotes." }) : null),
    h("div", { class: "txt" }, ...cited(m)),
    !m.grounded ? h("p", { class: "faint", text: REASONS[m.reason] || "Try rephrasing, or ask about something covered in this lecture." }) : null,
    live.length ? h("details", { style: { marginTop: "12px" } }, h("summary", { text: `Sources (${live.length})` }),
      h("div", { class: "stack", style: { marginTop: "10px" } }, ...live.map(c => h("div", null, h("blockquote", { class: "quote", text: c.text || c.quote || "" }),
        h("div", { class: "cites" }, h("span", { class: "origin " + (c.origin || ""), text: c.origin_label || "" }), citeLink({ ...c, lecture_id: cur })))))) : null,
    (m.follow_ups || []).length || m.general_knowledge_offer ? h("div", { class: "acts" }, m.general_knowledge_offer ? h("button", { class: "btn sm", type: "button", onclick: e => askGK(m, e.currentTarget) }, "Explain from general knowledge (not from your sources)") : null, ...(m.follow_ups || []).slice(0, 3).map(f => h("button", { class: "btn sm quiet", type: "button", onclick: () => send(f) }, f))) : null);
}
function draw(sugg = []) {
  if (!msgs.length) clear(log, h("p", { class: "faint", text: `Ask anything about “${ltitle(cur)}”.` }));
  else clear(log, ...msgs.map(m => m.role === "user" ? h("div", { class: "msg user", text: m.content }) : bot(m)));
  clear(starters, ...(msgs.length ? [] : sugg.map(t => h("button", { class: "btn sm", type: "button", onclick: () => send(t) }, t))));
  const o = $("#cc-open"); if (o) o.href = `/app/lectures/${cur}`;
  log.lastElementChild && log.lastElementChild.scrollIntoView({ block: "nearest" });
}
async function loadChat() {
  msgs = []; clear(log, h("div", { class: "skeleton block" }));
  const id = cur;
  try { const r = await api(`/lectures/${id}/chat`); if (id !== cur) return; msgs = r.messages || []; }
  catch (e) { clear(log, notice("err", h("p", { text: explain(e, "load the conversation") }), h("button", { class: "btn sm", type: "button", onclick: loadChat }, "Retry"))); return; }
  let sugg = [];
  if (!msgs.length) { try { const sl = await api(`/lectures/${id}/slides`); sugg = (sl.slides || []).map(s => s.title).filter(t => t && !/^slide \d+$/i.test(t)).slice(0, 3).map(t => `What does the lecture say about ${t}?`); } catch (_) {} }
  if (id === cur) draw(sugg);
}
async function send(text) {
  if (sending) return; const q = (text != null ? text : input.value).trim(); if (!q) return;
  sending = true; sendBtn.disabled = true; input.value = ""; input.style.height = "auto";
  msgs.push({ role: "user", content: q }); draw();
  const wait = h("div", { class: "msg bot", role: "status" }, h("span", { class: "faint", text: "Searching the lecture…" })); log.append(wait); wait.scrollIntoView({ block: "nearest" });
  try { const m = await api(`/lectures/${cur}/chat`, { method: "POST", json: { question: q } }); msgs.push(m); announce(m.grounded ? "Answer ready." : "That isn't covered in the lecture."); }
  catch (e) { msgs.pop(); input.value = q; toast(e.status === 429 ? `You're asking too fast. Try again in ${(e.detail && e.detail.retry_after) || 60} seconds.` : e.status === 409 ? "This lecture is still processing." : explain(e, "get an answer"), "err"); }
  finally { wait.remove(); sending = false; sendBtn.disabled = false; draw(); input.focus(); }
}
async function reset() {
  if (!msgs.length) return toast("There's nothing to clear.");
  if (!await confirmDialog({ title: "Clear this conversation?", body: "The questions and answers for this lecture are deleted. Notes you saved stay.", confirm: "Clear conversation", danger: true })) return;
  try { await api(`/lectures/${cur}/chat`, { method: "DELETE" }); msgs = []; draw(); toast("Cleared."); } catch (e) { toast(explain(e, "clear it"), "err"); }
}
boot();
