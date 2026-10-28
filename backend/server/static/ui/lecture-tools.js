// Lecture viewer panels that talk to the API on their own: Ask, Study, Notes. `ctx` comes from lecture.js.
import { $, h, clear, api, icon, toast, explain, confirmDialog, notice, announce, withBusy, plural, when, mmss } from "/static/ui/core.js";

const REASONS = { out_of_scope: "Nothing in this lecture matches that slide or time.", no_match: "Nothing in this lecture matches that.", no_definition: "The material mentions this but never explains it.", broad_topic: "Every term in that question is everywhere in the material, so nothing specific could be found.", retrieval_error: "Search failed just now. Try again.", weak_match: "The lecture touches on related words, but not enough to answer that.",
  unsafe_evidence: "The matching passages couldn't be used safely.", no_terms: "Ask about a topic, or about a slide or time (for example: what is on slide 3?).", model_declined: "The lecture evidence doesn't answer that." };
const live = evs => (evs || []).filter(e => e.available !== false);
const srcLine = c => c && c.citation ? c.citation.label : "";

/* ── Ask ───────────────────────────────────────────────────────────────────────────── */
export function mountAsk(p, ctx) {
  let msgs = [], sending = false, starters = [];
  const log = h("div", { class: "ask-log", role: "log", "aria-live": "polite", "aria-label": "Conversation" });
  const input = h("textarea", { id: "ask-in", rows: 1, maxlength: 500, "aria-label": "Your question", placeholder: "Ask about this lecture",
    onkeydown: e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }, oninput: e => { e.target.style.height = "auto"; e.target.style.height = Math.min(140, e.target.scrollHeight) + "px"; } });
  const sendBtn = h("button", { class: "btn primary", type: "button", onclick: () => send() }, "Ask");
  const starterBox = h("div", { class: "chips" });
  clear(p, h("p", { class: "mnote", text: "Answers come only from this lecture's transcript and slides. If it isn't covered, Notiv says so instead of guessing." }), log,
    h("div", { class: "ask-form" }, h("div", { class: "in" }, h("div", { style: { flex: 1 } }, input), sendBtn), starterBox),
    h("div", { style: { marginTop: "10px" } }, h("button", { class: "btn quiet sm", type: "button", onclick: reset }, icon("trash"), "Clear conversation")));

  function cited(m) {
    const by = new Map((m.citations || []).map(c => [c.evidence_id, c])), out = [];
    for (const part of m.content.split(/(\[E\d+\])/)) {
      const mm = part.match(/^\[E(\d+)\]$/); if (!mm) { out.push(part); continue; }
      const c = by.get(Number(mm[1])); if (c && c.available !== false && c.citation) out.push(" ", ctx.citeBtn(c.citation));
    }
    return out;
  }
  const plain = m => { const by = new Map((m.citations || []).map(c => [c.evidence_id, c])); return m.content.replace(/\[E(\d+)\]/g, (_, n) => { const c = by.get(Number(n)); return c && c.available !== false && c.citation ? `[${c.citation.label}]` : ""; }).replace(/\s+([.,;:])/g, "$1").trim(); };

  async function askGK(m, btn) {
    btn.disabled = true;
    try { const g = await api(`/lectures/${ctx.LID}/chat/${m.message_id}/general-knowledge`, { method: "POST" }); const i = msgs.findIndex(x => x.message_id === m.message_id); msgs.splice(i + 1, 0, g); m.general_knowledge_offer = false; draw(); }
    catch (e) { btn.disabled = false; toast(explain(e, "get a general-knowledge explanation"), "err"); }
  }
  function gkCard(m) {
    return h("div", { class: "msg bot none", dataset: { msg: m.message_id } }, h("div", { class: "meta" }, h("span", { class: "origin general_knowledge", text: "General knowledge — not from your uploaded material" })),
      h("div", { class: "txt", text: m.content }), h("p", { class: "faint", text: "This explanation comes from the AI model's general knowledge, not from this lecture, so it has no sources. Check it before relying on it." }));
  }
  function bot(m, q) {
    if (m.general_knowledge) return gkCard(m);
    const ev = live(m.citations);
    return h("div", { class: "msg bot" + (m.grounded ? "" : " none") },
      h("div", { class: "meta" }, ctx.badge("notiv_generated", "Notiv"), m.grounded ? h("span", { text: m.answer_mode === "llm" ? "Written from lecture evidence" : "Quotes from the lecture" }) : null, m.degraded ? h("span", { text: "AI wording unavailable" + (m.degraded_reason ? ": " + m.degraded_reason : ".") + " Showing lecture quotes." }) : null),
      h("div", { class: "txt" }, ...cited(m)), !m.grounded ? h("p", { class: "faint", text: REASONS[m.reason] || "Try rephrasing, or ask about something covered in this lecture." }) : null,
      ev.length ? h("details", { style: { marginTop: "10px" } }, h("summary", { text: `Sources (${ev.length})` }), h("div", { class: "stack", style: { marginTop: "10px" } }, ...ev.map(c => h("div", null, h("blockquote", { class: "quote", text: c.quote || c.text || "" }), h("div", { class: "cites" }, ctx.badge(c.origin || "", c.origin_label || ""), c.citation ? ctx.citeBtn(c.citation) : null))))) : null,
      h("div", { class: "acts" }, m.general_knowledge_offer ? h("button", { class: "btn sm", type: "button", onclick: e => askGK(m, e.currentTarget) }, "Explain from general knowledge (not from your sources)") : null, m.grounded ? h("button", { class: "btn sm", type: "button", onclick: () => ctx.saveToNotebook({ title: (q || "Answer").slice(0, 120), content: `## ${q}\n\n${plain(m)}\n\n${ev.length ? "Sources: " + ev.map(srcLine).filter(Boolean).join(", ") : ""}`.trim(), evidenceIds: ev.map(c => c.evidence_id), question: q, kind: "ask_notiv" }) }, icon("book"), "Save to a notebook") : null,
        ...(m.follow_ups || []).slice(0, 3).map(f => h("button", { class: "btn sm quiet", type: "button", onclick: () => send(f) }, f))));
  }
  function draw() {
    let lastQ = "";
    if (!msgs.length) clear(log, h("p", { class: "faint", text: "Ask anything about this lecture. Each claim links to the moment it came from." }));
    else clear(log, ...msgs.map(m => { if (m.role === "user") { lastQ = m.content; return h("div", { class: "msg user", text: m.content }); } return bot(m, lastQ); }));
    clear(starterBox, ...(msgs.length ? [] : starters.map(t => h("button", { class: "btn sm", type: "button", onclick: () => send(t) }, t))));
    log.lastElementChild && log.lastElementChild.scrollIntoView({ block: "nearest" });
  }
  async function load() {
    clear(log, h("div", { class: "skeleton block" }));
    try { msgs = (await api(`/lectures/${ctx.LID}/chat`)).messages; } catch (e) { clear(log, notice("err", h("p", { text: explain(e, "load the conversation") }))); return; }
    starters = ctx.S.slides.map(s => s.title).filter(t => t && !/^slide \d+$/i.test(t)).slice(0, 3).map(t => `What does the lecture say about ${t}?`);
    draw();
  }
  async function send(text) {
    if (sending) return; const q = (text != null ? text : input.value).trim(); if (!q) return;
    sending = true; sendBtn.disabled = true; input.value = ""; input.style.height = "auto";
    msgs.push({ role: "user", content: q }); draw();
    const wait = h("div", { class: "msg bot", role: "status" }, h("span", { class: "faint", text: "Searching the lecture…" })); log.append(wait); wait.scrollIntoView({ block: "nearest" });
    try { const m = await api(`/lectures/${ctx.LID}/chat`, { method: "POST", json: { question: q } }); msgs.push(m); announce(m.grounded ? "Answer ready." : "That isn't covered in the lecture."); }
    catch (e) { msgs.pop(); input.value = q; toast(e.status === 429 ? `You're asking too fast. Try again in ${(e.detail && e.detail.retry_after) || 60} seconds.` : e.status === 409 ? "This lecture is still processing." : explain(e, "get an answer"), "err"); }
    finally { wait.remove(); sending = false; sendBtn.disabled = false; draw(); input.focus(); }
  }
  async function reset() {
    if (!msgs.length) return toast("There's nothing to clear.");
    if (!await confirmDialog({ title: "Clear this conversation?", body: "The questions and answers for this lecture are deleted. Anything you saved to a notebook stays.", confirm: "Clear conversation", danger: true })) return;
    try { await api(`/lectures/${ctx.LID}/chat`, { method: "DELETE" }); msgs = []; draw(); toast("Cleared."); } catch (e) { toast(explain(e, "clear it"), "err"); }
  }
  load();
}

/* ── Study ─────────────────────────────────────────────────────────────────────────── */
const KIND_LABEL = { flashcard: "Flashcards", short_answer: "Short answer", mcq: "Multiple choice" };
export function mountStudy(p, ctx) {
  let mode = "concepts";
  const head = h("div", { class: "toolbar", style: { marginTop: 0 }, role: "group", "aria-label": "Study view" });
  const body = h("div");
  const btn = (id, label) => h("button", { class: "btn sm" + (mode === id ? " primary" : ""), type: "button", "aria-pressed": mode === id, onclick: () => { mode = id; paint(); } }, label);
  clear(p, h("div", { class: "studyprog", id: "sprog" }), head, body);
  function paint() { clear(head, btn("concepts", "Concepts"), btn("practice", "Practice"), btn("items", "Study items")); ({ concepts, practice, items })[mode](); }
  async function progress() {
    const box = $("#sprog"); if (!box) return;
    try {
      const g = await api(`/lectures/${ctx.LID}/study/progress`);
      clear(box, h("div", { class: "stats" }, h("span", { text: `${g.mastered} of ${g.total} items mastered` }), g.accuracy != null ? h("span", { text: `${Math.round(g.accuracy * 100)}% accuracy` }) : null, h("span", { text: `${g.concepts.reviewed} of ${g.concepts.total} concepts reviewed` })),
        h("div", { class: "prog-line", role: "progressbar", "aria-label": "Mastery", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": g.completion_pct }, h("i", { style: { width: g.completion_pct + "%" } })));
    } catch (e) { clear(box, notice("err", h("p", { text: explain(e, "load progress") }))); }
  }
  const regenerate = async b => withBusy(b, async () => {
    try { const r = await api(`/lectures/${ctx.LID}/knowledge/regenerate`, { method: "POST" }); const g = await api(`/lectures/${ctx.LID}/study/generate`, { method: "POST", json: {} });
      toast(`Refreshed from the lecture. ${plural(g.active_items, "study item")} available.`); progress(); paint(); } catch (e) { toast(explain(e, "refresh study material"), "err"); } });

  async function concepts() {
    clear(body, h("div", { class: "skeleton block" }));
    let r; try { r = await api(`/lectures/${ctx.LID}/concepts`); } catch (e) { clear(body, notice("err", h("p", { text: explain(e, "load concepts") }))); return; }
    const list = r.concepts;
    if (!list.length) { clear(body, h("div", { class: "empty" }, h("h3", { text: "No concepts found" }), h("p", { text: "Notiv looks for slide titles and explicit definitions. This lecture doesn't state any clearly." }), h("button", { class: "btn", onclick: e => regenerate(e.currentTarget) }, icon("refresh"), "Look again"))); return; }
    clear(body, h("p", { class: "mnote", text: "Pulled from what the lecture says and shows. Mark a concept reviewed as you revise it." }),
      ...list.map(c => {
        const parts = [["Definitions", c.definitions], ["Formulas", c.formulas], ["Examples", c.examples], ["Warnings", c.warnings], ["Facts", c.facts], ["Emphasis", c.emphasis]].filter(([, a]) => a && a.length);
        const ev = live(c.evidence)[0];
        const card = h("div", { class: "card-note", dataset: { concept: c.knowledge_id } },
          h("div", { class: "cites", style: { marginTop: 0 } }, ctx.badge("notiv_generated", "Study note"), ev ? ctx.citeBtn(ev.citation) : null, c.reviewed ? h("span", { class: "chip ok", text: "Reviewed" }) : null),
          h("h3", { text: c.title }), c.body ? h("blockquote", { class: "quote", text: c.body }) : null,
          ...parts.map(([label, arr]) => h("div", { class: "kitem" }, h("strong", { text: label }), ...arr.map(k => h("div", null, h("span", { text: k.body || k.title }), " ", live(k.evidence)[0] ? ctx.citeBtn(live(k.evidence)[0].citation) : null)))),
          h("div", { style: { marginTop: "10px", display: "flex", gap: "8px", flexWrap: "wrap" } },
            h("button", { class: "btn sm", type: "button", disabled: !!c.reviewed, onclick: async e => { try { await api(`/knowledge/${c.knowledge_id}/review`, { method: "POST" }); toast("Marked reviewed."); progress(); concepts(); } catch (x) { toast(explain(x, "mark it reviewed"), "err"); } } }, icon("check"), c.reviewed ? "Reviewed" : "Mark reviewed"),
            h("button", { class: "btn sm quiet", type: "button", onclick: () => ctx.saveToNotebook({ title: c.title, content: `## ${c.title}\n\n${[c.body, ...parts.flatMap(([l, a]) => a.map(k => `- **${l.replace(/s$/, "")}:** ${k.body || k.title}`))].filter(Boolean).join("\n")}`, evidenceIds: live(c.evidence).map(e => e.evidence_id), kind: "concept" }) }, icon("book"), "Save to a notebook"),
            h("button", { class: "btn sm quiet", type: "button", onclick: async () => { if (!await confirmDialog({ title: "Remove this concept?", body: "It won't come back when you refresh from the lecture.", confirm: "Remove", danger: true })) return;
              try { await api(`/knowledge/${c.knowledge_id}`, { method: "DELETE" }); toast("Removed."); progress(); concepts(); } catch (x) { toast(explain(x, "remove it"), "err"); } } }, icon("trash"), "Remove")));
        return card; }),
      h("div", { style: { marginTop: "14px" } }, h("button", { class: "btn quiet sm", onclick: e => regenerate(e.currentTarget) }, icon("refresh"), "Refresh from the lecture")));
  }

  async function practice() {
    let g; try { g = await api(`/lectures/${ctx.LID}/study/progress`); } catch (e) { clear(body, notice("err", h("p", { text: explain(e, "load practice") }))); return; }
    if (!g.total) { clear(body, h("div", { class: "empty" }, h("h3", { text: "Nothing to practise yet" }), h("p", { text: "Study items are built from the lecture's evidence. Build them now." }), h("button", { class: "btn primary", onclick: e => regenerate(e.currentTarget) }, "Build study items"))); return; }
    const kinds = Object.keys(g.by_kind), sel = new Set(kinds);
    const checks = kinds.map(k => h("label", { class: "check" }, h("input", { type: "checkbox", checked: true, onchange: e => { e.target.checked ? sel.add(k) : sel.delete(k); } }), `${KIND_LABEL[k] || k} (${g.by_kind[k].total})`));
    const review = h("input", { type: "checkbox" });
    clear(body, h("p", { class: "mnote", text: "A session picks items you haven't mastered yet. Your answers are private to you." }), h("div", { class: "stack" }, ...checks, h("label", { class: "check" }, review, "Include items I've already mastered")),
      h("div", { style: { marginTop: "14px" } }, h("button", { class: "btn primary", onclick: async e => { if (!sel.size) return toast("Choose at least one kind.", "err");
        await withBusy(e.currentTarget, async () => { try { const s = await api(`/lectures/${ctx.LID}/study/sessions`, { method: "POST", json: { kinds: [...sel], limit: 10, include_mastered: review.checked } });
          if (!s.items.length) { toast("You've mastered everything here. Tick “include mastered” to revisit."); return; } run(s); } catch (x) { toast(explain(x, "start the session"), "err"); } }); } }, "Start a session of up to 10")));
  }
  function run(s) {
    let i = 0, res = {}, revealed = null, busy = false;
    const draw = () => {
      if (i >= s.items.length) return finish();
      const it = s.items[i], out = revealed;
      const answer = out ? h("div", { class: "reveal", role: "status" }, h("p", { class: "serif", text: out.reveal.answer || "" }), out.reveal.explanation ? h("p", { class: "faint", text: out.reveal.explanation }) : null, live(out.reveal.evidence).length ? h("div", { class: "cites" }, ...live(out.reveal.evidence).slice(0, 3).map(e => ctx.citeBtn(e.citation))) : null,
        h("div", { style: { marginTop: "10px" } }, h("button", { class: "btn primary sm", onclick: () => { i++; revealed = null; draw(); } }, i === s.items.length - 1 ? "Finish" : "Next"))) : null;
      let ctl;
      if (it.kind === "mcq") ctl = h("div", { role: "group", "aria-label": "Answers" }, ...(it.options || []).map(o => h("button", { type: "button", class: "opt" + (out ? (o.id === out.reveal.correct_option ? " ok" : o.id === out.selected_option ? " bad" : "") : ""), disabled: !!out || busy, onclick: () => attempt(it, { selected_option: o.id }) }, h("span", { class: "k mono", text: o.id.toUpperCase() }), h("span", { text: o.text }))));
      else if (!out) ctl = h("div", null, it.kind === "short_answer" ? h("textarea", { id: "resp", rows: 3, maxlength: 2000, "aria-label": "Your answer", placeholder: "Write your answer, then compare it with the lecture" }) : null,
        h("p", { class: "faint", text: "Try to answer first." }), h("button", { class: "btn sm primary", type: "button", onclick: () => attempt(it, { show: true }) }, "Show the answer"));
      else ctl = h("div", { class: "grade", role: "group", "aria-label": "How well did you know it?" }, !out.graded ? [["know", "Got it"], ["unsure", "Unsure"], ["dont_know", "Missed it"]].map(([k, l]) => h("button", { class: "btn sm", type: "button", onclick: () => attempt(it, { self_grade: k }, true) }, l)) : null);
      clear(body, h("div", { class: "deck-top" }, h("span", { class: "faint", text: `${KIND_LABEL[it.kind] || it.kind} · ${i + 1} of ${s.items.length}` }), h("span", { class: "faint", text: it.slide_index != null ? "Slide " + it.slide_index : "" })),
        h("div", { class: "card-face" }, h("div", { class: "face-text serif", text: it.prompt })), ctl, answer);
    };
    // Flashcards and short answers are self-graded: reveal first (no attempt recorded), then grade, which records and reveals the answer.
    const pending = {};
    async function attempt(it, o, graded) {
      if (busy) return;
      if (o.show) { pending[it.item_id] = ($("#resp") || {}).value || ""; return grade(it); }
      busy = true;
      try { const body = o.selected_option ? { item_id: it.item_id, selected_option: o.selected_option } : { item_id: it.item_id, self_grade: o.self_grade, ...(pending[it.item_id] ? { response_text: pending[it.item_id] } : {}) };
        const r = await api(`/study/sessions/${s.session_id}/attempts`, { method: "POST", json: body }); res[it.item_id] = r.correct; revealed = { ...r, graded: true }; progress(); announce(r.correct ? "Correct" : "Not quite"); }
      catch (e) { toast(explain(e, "record your answer"), "err"); }
      finally { busy = false; draw(); }
    }
    function grade(it) { clear(body, h("div", { class: "deck-top" }, h("span", { class: "faint", text: `${KIND_LABEL[it.kind] || it.kind} · ${i + 1} of ${s.items.length}` })), h("div", { class: "card-face" }, h("div", { class: "face-text serif", text: it.prompt })),
      pending[it.item_id] ? h("blockquote", { class: "quote", text: "Your answer: " + pending[it.item_id] }) : null, h("p", { class: "faint", text: "How well did you know it? The lecture's answer appears once you choose." }),
      h("div", { class: "grade", role: "group", "aria-label": "How well did you know it?" }, ...[["know", "Got it"], ["unsure", "Unsure"], ["dont_know", "Missed it"]].map(([k, l]) => h("button", { class: "btn sm", type: "button", onclick: () => attempt(it, { self_grade: k }) }, l)))); }
    async function finish() {
      let f; try { f = await api(`/study/sessions/${s.session_id}/finish`, { method: "POST" }); } catch (e) { toast(explain(e, "finish the session"), "err"); }
      const sm = (f && f.summary) || { total: s.items.length, answered: Object.keys(res).length, correct: Object.values(res).filter(Boolean).length };
      clear(body, h("div", { class: "empty" }, h("h3", { text: "Session complete" }), h("p", { text: `${sm.correct} of ${sm.answered} answered correctly.` }), h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: practice }, "Practise again"))));
      progress();
    }
    draw();
  }

  async function items() {
    clear(body, h("div", { class: "skeleton block" }));
    let r; try { r = await api(`/lectures/${ctx.LID}/study/items?reveal=true`); } catch (e) { clear(body, notice("err", h("p", { text: explain(e, "load study items") }))); return; }
    if (!r.items.length) { clear(body, h("div", { class: "empty" }, h("h3", { text: "No study items yet" }), h("button", { class: "btn primary", onclick: e => regenerate(e.currentTarget) }, "Build study items"))); return; }
    const groups = {}; r.items.forEach(i => (groups[i.kind] = groups[i.kind] || []).push(i));
    clear(body, r.stale ? notice("warn", h("p", { text: "The lecture changed since these were built. Refresh to rebuild them." })) : null,
      ...Object.entries(groups).map(([k, arr]) => h("details", { open: Object.keys(groups).length === 1 }, h("summary", { text: `${KIND_LABEL[k] || k} (${arr.length})` }),
        h("div", null, ...arr.map(it => h("div", { class: "card-note" }, h("div", { class: "cites", style: { marginTop: 0 } }, h("span", { class: "faint", text: it.difficulty }), it.slide_index != null ? h("span", { class: "faint", text: "Slide " + it.slide_index }) : null, live(it.evidence)[0] ? ctx.citeBtn(live(it.evidence)[0].citation) : null),
          h("p", { class: "serif", text: it.prompt }), it.options ? h("ul", null, ...it.options.map(o => h("li", { text: `${o.id.toUpperCase()}. ${o.text}${o.id === it.correct_option ? "  ✓" : ""}` }))) : null,
          h("p", { class: "faint", text: it.answer || "" }),
          h("div", { style: { display: "flex", gap: "8px", flexWrap: "wrap" } },
            h("button", { class: "btn sm quiet", type: "button", onclick: async e => withBusy(e.currentTarget, async () => { try { await api(`/study/items/${it.item_id}/regenerate`, { method: "POST" }); toast("Rebuilt from the lecture."); items(); } catch (x) { toast(explain(x, "rebuild it"), "err"); } }) }, icon("refresh"), "Rebuild"),
            h("button", { class: "btn sm quiet", type: "button", onclick: async () => { if (!await confirmDialog({ title: "Delete this item?", body: "It won't be rebuilt automatically.", confirm: "Delete", danger: true })) return;
              try { await api(`/study/items/${it.item_id}`, { method: "DELETE" }); toast("Deleted."); progress(); items(); } catch (x) { toast(explain(x, "delete it"), "err"); } } }, icon("trash"), "Delete"))))))),
      h("div", { style: { marginTop: "14px" } }, h("button", { class: "btn quiet sm", onclick: e => regenerate(e.currentTarget) }, icon("refresh"), "Refresh from the lecture")));
  }
  progress(); paint();
}

/* ── Notes ─────────────────────────────────────────────────────────────────────────── */
export function mountNotes(p, ctx) {
  let list = [];
  const ta = h("textarea", { id: "newnote", rows: 3, maxlength: 20000, "aria-label": "New note", placeholder: "Write a note. It's pinned to the moment you're at." });
  const pin = h("span", { class: "faint" });
  const box = h("div");
  const here = () => { const m = ctx.S.media; return { t: m ? Math.floor(m.currentTime) : null, slide: ctx.S.curSlide }; };
  const updPin = () => { const x = here(); pin.textContent = x.t != null || x.slide != null ? "Pinned to " + [x.slide != null ? "Slide " + x.slide : null, x.t != null ? mmss(x.t) : null].filter(Boolean).join(" · ") : "Not pinned to a moment"; };
  ta.addEventListener("focus", updPin);
  clear(p, h("p", { class: "mnote", text: "Private to you. Notes stay with the lecture and can be pinned to a moment." }), ta, h("div", { style: { display: "flex", gap: "10px", alignItems: "center", margin: "8px 0 var(--s4)" } }, h("button", { class: "btn primary sm", type: "button", onclick: async e => {
    const body = ta.value.trim(); if (!body) return toast("Write something first.");
    await withBusy(e.currentTarget, async () => { const x = here(); try { const n = await api(`/lectures/${ctx.LID}/notes`, { method: "POST", json: { body, ...(x.t != null ? { t: x.t } : {}), ...(x.slide != null ? { slide_index: x.slide } : {}) } }); list.unshift(n); ta.value = ""; draw(); toast("Note saved."); } catch (err) { toast(explain(err, "save the note"), "err"); } }); } }, "Add note"), pin), box);
  function draw() {
    if (!list.length) return clear(box, h("p", { class: "faint", text: "No notes yet." }));
    clear(box, ...list.map(n => {
      const where = [n.slide_index != null ? "Slide " + n.slide_index : null, n.t != null ? mmss(n.t) : null].filter(Boolean).join(" · ");
      return h("div", { class: "note-card", dataset: { note: n.id } }, h("div", { class: "cites", style: { marginTop: 0 } }, where ? ctx.citeBtn({ label: where, seek_seconds: n.t, slide_index: n.slide_index }) : null, h("span", { class: "faint", text: when(n.updated_at) })),
        h("div", { class: "nt", text: n.body }), h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } },
          h("button", { class: "btn sm quiet", type: "button", onclick: () => edit(n) }, icon("edit"), "Edit"),
          h("button", { class: "btn sm quiet", type: "button", onclick: () => ctx.saveToNotebook({ title: n.body.split("\n")[0].slice(0, 80) || "Note", content: n.body + (where ? `\n\nSource: ${where}` : ""), kind: "lecture_note" }) }, icon("book"), "Save to a notebook"),
          h("button", { class: "btn sm quiet", type: "button", onclick: async () => { if (!await confirmDialog({ title: "Delete this note?", body: "This can't be undone.", confirm: "Delete", danger: true })) return;
            try { await api("/notes/" + n.id, { method: "DELETE" }); list = list.filter(x => x.id !== n.id); draw(); toast("Deleted."); } catch (e) { toast(explain(e, "delete the note"), "err"); } } }, icon("trash"), "Delete")));
    }));
  }
  function edit(n) {
    const card = $(`[data-note="${n.id}"]`), t = h("textarea", { rows: 4, maxlength: 20000, "aria-label": "Edit note" }, n.body); t.value = n.body;
    clear(card, t, h("div", { style: { display: "flex", gap: "8px", marginTop: "8px" } }, h("button", { class: "btn primary sm", type: "button", onclick: async e => { const b = t.value.trim(); if (!b) return toast("A note can't be empty.");
      await withBusy(e.currentTarget, async () => { try { const u = await api("/notes/" + n.id, { method: "PATCH", json: { body: b } }); Object.assign(n, u); draw(); toast("Saved."); } catch (x) { toast(explain(x, "save the note"), "err"); } }); } }, "Save"), h("button", { class: "btn sm", type: "button", onclick: draw }, "Cancel"))); t.focus();
  }
  (async () => { clear(box, h("div", { class: "skeleton block" })); try { list = (await api(`/lectures/${ctx.LID}/notes?limit=200`)).notes.slice().reverse(); draw(); } catch (e) { clear(box, notice("err", h("p", { text: explain(e, "load notes") }))); } })();
}
