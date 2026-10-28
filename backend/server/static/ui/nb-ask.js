// Ask Notiv inside a notebook: grounded questions about one linked lecture, with sources, and "save as note".
import { $, h, clear, api, icon, toast, explain, confirmDialog, notice, stateChip, announce } from "/static/ui/core.js";
import { citeLink } from "/static/ui/render.js";

const REASONS = { out_of_scope: "Nothing in this lecture matches that slide or time.", no_match: "Nothing in this lecture matches that.", no_definition: "The material mentions this but never explains it.", broad_topic: "Every term in that question is everywhere in the material, so nothing specific could be found.", retrieval_error: "Search failed just now. Try again.", weak_match: "The lecture touches on related words, but not enough to answer that.",
  unsafe_evidence: "The matching passages couldn't be used safely.", no_terms: "Ask about a topic, or about a slide or time (for example: what is on slide 3?).", model_declined: "The lecture evidence doesn't answer that." };

export function mountAsk(p, { nb, lectures, onSaved, openItem }) {
  const ready = nb.sources.map(s => ({ s, l: lectures.find(l => l.id === s.source_id) })).filter(x => x.l && x.l.status === "done");
  if (!ready.length) {
    clear(p, h("div", { class: "empty" }, h("h2", { text: nb.sources.length ? "Waiting for a lecture to finish processing" : "Link a lecture to ask it questions" }),
      h("p", { text: nb.sources.length ? "Ask Notiv answers only from a processed lecture's transcript and slides. Your linked lectures aren't ready yet." : "Ask Notiv answers only from what was said and shown in a lecture, and shows you where." }),
      h("div", { class: "actions" }, h("a", { class: "btn primary", href: "#sources" }, nb.sources.length ? "See your sources" : "Link a lecture"))));
    return () => {};
  }
  let cur = ready[0].s.source_id, msgs = [], sending = false, alive = true, starters = [];
  const log = h("div", { class: "ask-log", role: "log", "aria-live": "polite", "aria-label": "Conversation" });
  const input = h("textarea", { id: "ask-in", rows: 1, maxlength: 500, "aria-label": "Your question", placeholder: "Ask about this lecture", onkeydown: e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } },
    oninput: e => { e.target.style.height = "auto"; e.target.style.height = Math.min(140, e.target.scrollHeight) + "px"; } });
  const sendBtn = h("button", { class: "btn primary", type: "button", onclick: () => send() }, "Ask");
  const clearBtn = h("button", { class: "btn quiet", type: "button", onclick: reset }, icon("trash"), "Clear conversation");
  const pick = ready.length > 1 ? h("div", { class: "field", style: { maxWidth: "420px" } }, h("label", { for: "ask-src", text: "Ask about" }),
    h("select", { id: "ask-src", onchange: e => { cur = Number(e.target.value); load(); } }, ...ready.map(({ s }) => h("option", { value: s.source_id }, s.label || "Lecture " + s.source_id)))) : null;
  const title = () => (ready.find(x => x.s.source_id === cur).s.label) || "this lecture";
  clear(p, h("div", { class: "section-head" }, h("h2", { text: "Ask Notiv" }), h("div", { class: "tools" }, clearBtn)),
    h("p", { class: "muted", style: { paddingTop: "12px", maxWidth: "62ch" }, text: "Answers come only from the lecture's transcript and slides. If it isn't covered, Notiv says so instead of guessing." }), pick, log,
    h("div", { class: "ask-form" }, h("div", { class: "in" }, h("div", { style: { flex: 1 } }, h("label", { class: "sr", for: "ask-in", text: "Your question" }), input), sendBtn), h("div", { class: "chips", id: "starters" })));

  function plain(m) { const by = new Map((m.citations || []).map(c => [c.evidence_id, c])); return m.content.replace(/\[E(\d+)\]/g, (_, n) => { const c = by.get(Number(n)); return c && c.available !== false && c.citation ? `[${c.citation.label}]` : ""; }).replace(/\s+([.,;:])/g, "$1").trim(); }
  function cited(m) {
    const by = new Map((m.citations || []).map(c => [c.evidence_id, c])); const out = [];
    for (const part of m.content.split(/(\[E\d+\])/)) {
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
    return h("div", { class: "msg bot none", dataset: { msg: m.message_id } }, h("div", { class: "meta" }, h("span", { class: "origin general_knowledge", text: "General knowledge — not from your uploaded material" })),
      h("div", { class: "txt", text: m.content }), h("p", { class: "faint", text: "This explanation comes from the AI model's general knowledge, not from this lecture, so it has no sources. Check it before relying on it." }));
  }
  function bot(m, q) {
    if (m.general_knowledge) return gkCard(m);
    const live = (m.citations || []).filter(c => c.available !== false);
    const saveBtn = m.grounded ? h("button", { class: "btn sm", type: "button", onclick: async e => { const b = e.currentTarget; b.disabled = true;
      try { const body = `## ${q}\n\n${plain(m)}\n\n${live.length ? "Sources: " + live.map(c => c.citation && c.citation.label).filter(Boolean).join(", ") : ""}`.trim();
        const it = await api(`/notebooks/${nb.id}/items`, { method: "POST", json: { item_type: "note", title: (q || "Answer").slice(0, 120), content: body, provenance: [{ source_type: "lecture", source_id: cur, evidence_ids: live.map(c => c.evidence_id), generated_by: "ask_notiv", question: q }] } });
        await onSaved(it); clear(b.parentElement, h("button", { class: "btn sm", type: "button", onclick: () => openItem(it.id) }, icon("note"), "Saved. Open the note")); }
      catch (x) { b.disabled = false; toast(explain(x, "save the answer"), "err"); } } }, icon("note"), "Save as a note") : null;
    return h("div", { class: "msg bot" + (m.grounded ? "" : " none"), dataset: { msg: m.message_id } },
      h("div", { class: "meta" }, h("span", { class: "origin notiv_generated", text: "Notiv" }), m.grounded ? h("span", { text: m.answer_mode === "llm" ? "Written from lecture evidence" : "Quotes from the lecture" }) : null, m.degraded ? h("span", { text: "AI wording unavailable" + (m.degraded_reason ? ": " + m.degraded_reason : ".") + " Showing lecture quotes." }) : null),
      h("div", { class: "txt" }, ...cited(m)), !m.grounded ? h("p", { class: "faint", text: REASONS[m.reason] || "Try rephrasing, or ask about something covered in this lecture." }) : null,
      live.length ? h("details", { style: { marginTop: "10px" } }, h("summary", { text: `Sources (${live.length})` }), h("div", { class: "stack", style: { marginTop: "10px" } }, ...live.map(c => h("div", null, h("blockquote", { class: "quote", text: c.quote || c.text || "" }), h("div", { class: "cites" }, h("span", { class: "origin " + (c.origin || ""), text: c.origin_label || "" }), citeLink({ ...c, lecture_id: cur })))))) : null,
      h("div", { class: "acts" }, m.general_knowledge_offer ? h("button", { class: "btn sm", type: "button", onclick: e => askGK(m, e.currentTarget) }, "Explain from general knowledge (not from your sources)") : null, saveBtn, ...(m.follow_ups || []).slice(0, 3).map(f => h("button", { class: "btn sm quiet", type: "button", onclick: () => send(f) }, f))));
  }
  function draw() {
    let lastQ = "";
    if (!msgs.length) { clear(log, h("p", { class: "faint", text: `Ask anything about “${title()}”. Each claim links to the moment in the lecture.` })); }
    else clear(log, ...msgs.map(m => { if (m.role === "user") { lastQ = m.content; return h("div", { class: "msg user", text: m.content }); } return bot(m, lastQ); }));
    clear($("#starters"), ...(msgs.length ? [] : starters.map(t => h("button", { class: "btn sm", type: "button", onclick: () => send(t) }, t))));
    log.lastElementChild && log.lastElementChild.scrollIntoView({ block: "nearest" });
  }
  async function load() {
    msgs = []; starters = []; clear(log, h("div", { class: "skeleton block" }));
    try { msgs = (await api(`/lectures/${cur}/chat`)).messages; } catch (e) { clear(log, notice("err", h("p", { text: explain(e, "load the conversation") }))); return; }
    if (!msgs.length) { try { const sl = await api(`/lectures/${cur}/slides`); starters = sl.slides.map(s => s.title).filter(t => t && !/^slide \d+$/i.test(t)).slice(0, 3).map(t => `What does the lecture say about ${t}?`); } catch (_) {} }
    if (alive) draw();
  }
  async function send(text) {
    if (sending) return; const q = (text != null ? text : input.value).trim(); if (!q) return;
    sending = true; sendBtn.disabled = true; input.value = ""; input.style.height = "auto";
    msgs.push({ role: "user", content: q }); draw();
    const wait = h("div", { class: "msg bot", role: "status" }, h("span", { class: "faint", text: "Searching the lecture…" })); log.append(wait); wait.scrollIntoView({ block: "nearest" });
    try { const m = await api(`/lectures/${cur}/chat`, { method: "POST", json: { question: q } }); msgs.push(m); announce(m.grounded ? "Answer ready." : "That isn't covered in the lecture."); }
    catch (e) { msgs.pop(); input.value = q; toast(e.status === 429 ? `You're asking too fast. Try again in ${(e.detail && e.detail.retry_after) || 60} seconds.` : e.status === 409 ? "This lecture is still processing." : explain(e, "get an answer"), "err"); }
    finally { wait.remove(); sending = false; sendBtn.disabled = false; if (alive) { draw(); input.focus(); } }
  }
  async function reset() {
    if (!msgs.length) return toast("There's nothing to clear.");
    if (!await confirmDialog({ title: "Clear this conversation?", body: "The questions and answers for this lecture are deleted. Notes you saved from them stay.", confirm: "Clear conversation", danger: true })) return;
    try { await api(`/lectures/${cur}/chat`, { method: "DELETE" }); msgs = []; draw(); toast("Cleared."); } catch (e) { toast(explain(e, "clear it"), "err"); }
  }
  load();
  return () => { alive = false; };
}
