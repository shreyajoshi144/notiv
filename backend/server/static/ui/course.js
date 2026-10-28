// Course page: the lectures in one course, concepts that recur across them, search across all of them, and practice over the whole course.
import { $, h, clear, api, mountShell, pageError, skeletons, icon, plural, when, mmss, explain, toast, debounce, announce, notice, withBusy, stateChip, ApiError } from "/static/ui/core.js";
import { citeLink } from "/static/ui/render.js";
import { newLectureDialog } from "/static/ui/dialogs.js";

const CID = Number((location.pathname.match(/\/courses\/(\d+)/) || [])[1] || 0);
const KIND = { flashcard: "Flashcards", short_answer: "Short answer", mcq: "Multiple choice" };
const live = e => (e || []).filter(x => x.available !== false);
let main, course = null, tab = "lectures", overview = null;
const lectureLink = (id, title, frag = "") => h("a", { href: `/app/lectures/${id}${frag}`, text: title });
const cite = (ev) => ev && ev.citation ? citeLink(ev) : null;
const titleOf = id => (overview.lectures.find(l => l.lecture_id === id) || {}).title || "Lecture " + id;

async function init() {
  main = await mountShell("desk"); clear(main, ...skeletons(3));
  if (!CID) return pageError(main, new ApiError(404, "not found"));
  try {
    const [cs, ov, lc] = await Promise.all([api("/courses?limit=200"), api(`/courses/${CID}/overview`), api(`/lectures?limit=200&course_id=${CID}`)]);
    course = cs.items.find(c => c.id === CID); if (!course) throw new ApiError(404, "not found");
    overview = ov; overview.lectureRows = (lc.items || []).filter(l => l.course_id === CID);
  } catch (e) { if (e.status !== 401) pageError(main, e, init, "this course"); return; }
  document.title = course.name + " — Notiv"; draw();
}
function draw() {
  const tabs = [["lectures", "Lectures"], ["concepts", "Concepts"], ["search", "Search"], ["practice", "Practice"]];
  clear(main, h("div", { class: "crumbs" }, h("a", { href: "/app", text: "Desk" }), h("span", { class: "sep", text: "/" }), h("span", { text: "Course" })),
    h("div", { class: "pagehead" }, h("div", { class: "titles" }, h("h1", { text: course.name }), h("p", { class: "lede", text: `${plural(course.lecture_count, "lecture")}, ${course.ready_count} ready.` })),
      h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: async () => { const id = await newLectureDialog({ courses: [course], courseId: course.id }); if (id) location.assign("/app/lectures/" + id); } }, icon("upload"), "Add a lecture"))),
    h("div", { class: "tabs", role: "tablist", "aria-label": "Course" }, ...tabs.map(([id, l]) => h("button", { class: "tab", role: "tab", id: "ct-" + id, "aria-selected": tab === id, "aria-controls": "cpanel", onclick: () => { tab = id; draw(); } }, l))),
    h("div", { id: "cpanel", role: "tabpanel", "aria-labelledby": "ct-" + tab, tabindex: "0", style: { paddingTop: "var(--s5)" } }));
  ({ lectures, concepts, search, practice })[tab]($("#cpanel"));
}

function lectures(p) {
  const rows = overview.lectures;
  if (!rows.length) return clear(p, h("div", { class: "empty" }, h("h2", { text: "No lectures in this course yet" }), h("p", { text: "Add a recording or slides and Notiv will process them here." })));
  const byId = new Map((overview.lectureRows || []).map(l => [l.id, l]));
  clear(p, h("ul", { class: "ledger" }, ...rows.map(l => { const r = byId.get(l.lecture_id);
    return h("li", null, h("a", { class: "row-link", href: "/app/lectures/" + l.lecture_id }, h("span", null, h("span", { class: "row-title", text: l.title }), h("br"), h("span", { class: "faint", text: `${plural(l.knowledge_items, "study note")}, ${plural(l.study_items, "practice item")}` + (l.stale ? ", out of date" : "") })), r ? stateChip(r.status === "done" ? "done" : r.status) : h("span"))); })));
}

async function concepts(p) {
  clear(p, ...skeletons(3));
  let r; try { r = await api(`/courses/${CID}/concepts`); } catch (e) { return clear(p, notice("err", h("p", { text: explain(e, "load concepts") }))); }
  if (!r.concepts.length) return clear(p, h("div", { class: "empty" }, h("h2", { text: "No concepts yet" }), h("p", { text: "Concepts appear once at least one lecture in this course is processed." })));
  const shared = h("input", { type: "checkbox", onchange: () => paint() }), box = h("div");
  const paint = () => { const list = r.concepts.filter(c => !shared.checked || c.lectures_count > 1);
    clear(box, ...(list.length ? list.map(c => h("div", { class: "card-note" }, h("div", { class: "cites", style: { marginTop: 0 } }, h("span", { class: "chip plain", text: plural(c.lectures_count, "lecture") })), h("h3", { text: c.title }),
      ...(c.definitions || []).slice(0, 2).map(d => h("div", null, h("blockquote", { class: "quote", text: d.body || d.title }), h("div", { class: "cites" }, lectureLink(d.lecture_id, titleOf(d.lecture_id)), cite(live(d.evidence)[0])))),
      h("div", { class: "cites" }, ...(c.lectures || []).map(x => h("a", { class: "cite", href: `/app/lectures/${x.lecture_id}` + (x.slide_index != null ? "" : ""), title: x.lecture_title }, `${x.lecture_title}${x.slide_index != null ? " · slide " + x.slide_index : ""}`))))) : [h("p", { class: "faint", text: "No concept appears in more than one lecture yet." })])); };
  clear(p, h("label", { class: "check", style: { marginBottom: "var(--s4)" } }, shared, "Only concepts that appear in more than one lecture"), box); paint();
}

function search(p) {
  const box = h("div", { "aria-live": "polite" }, h("p", { class: "faint", text: "Search every processed lecture in this course." }));
  let seq = 0;
  const run = async q => { q = q.trim(); if (!q) return clear(box, h("p", { class: "faint", text: "Type to search." })); const my = ++seq; let r;
    try { r = await api(`/courses/${CID}/search?q=${encodeURIComponent(q)}`); } catch (e) { if (my === seq) clear(box, notice("err", h("p", { text: e.status === 400 ? e.message : explain(e, "search") }))); return; }
    if (my !== seq) return; announce(`${r.knowledge.length + r.evidence.length} results`);
    if (!r.knowledge.length && !r.evidence.length) return clear(box, h("p", { class: "faint", text: `No matches for “${q}”.` }));
    clear(box, r.match === "any" ? h("p", { class: "mnote", text: "Nothing contains every word, so these contain some of them." }) : null,
      r.knowledge.length ? h("h3", { text: "Study notes" }) : null, ...r.knowledge.map(k => h("div", { class: "evrow" }, h("div", { class: "cites", style: { marginTop: 0 } }, h("span", { class: "chip plain", text: k.kind }), lectureLink(k.lecture_id, titleOf(k.lecture_id)), cite(live(k.evidence)[0])), h("strong", { text: k.title }), k.body ? h("div", { class: "quote", text: k.body }) : null)),
      r.evidence.length ? h("h3", { text: "From the lectures", style: { marginTop: "var(--s5)" } }) : null, ...r.evidence.map(e => h("div", { class: "evrow" }, h("div", { class: "cites", style: { marginTop: 0 } }, h("span", { class: "origin " + e.origin, text: e.origin_label }), lectureLink(e.lecture_id, titleOf(e.lecture_id)), citeLink(e)), h("div", { class: "quote", text: e.text }))));
  };
  clear(p, h("div", { class: "field" }, h("label", { class: "sr", for: "cq", text: "Search this course" }), h("input", { id: "cq", type: "search", placeholder: "Search this course", autocomplete: "off", oninput: debounce(e => run(e.target.value), 250) })), box);
  $("#cq").focus();
}

async function practice(p) {
  clear(p, ...skeletons(2));
  let g; try { g = await api(`/courses/${CID}/study/progress`); } catch (e) { return clear(p, notice("err", h("p", { text: explain(e, "load progress") }))); }
  if (!g.total) return clear(p, h("div", { class: "empty" }, h("h2", { text: "Nothing to practise yet" }), h("p", { text: "Open a lecture, go to Study, and build its study items. They'll show up here for the whole course." })));
  const kinds = Object.keys(g.by_kind), sel = new Set(kinds), review = h("input", { type: "checkbox" });
  clear(p, h("div", { class: "stats" }, h("span", { text: `${g.mastered} of ${g.total} items mastered` }), g.accuracy != null ? h("span", { text: `${Math.round(g.accuracy * 100)}% accuracy` }) : null),
    h("div", { class: "prog-line", role: "progressbar", "aria-label": "Mastery", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": g.completion_pct }, h("i", { style: { width: g.completion_pct + "%" } })),
    h("div", { class: "stack", style: { marginTop: "var(--s4)" } }, ...kinds.map(k => h("label", { class: "check" }, h("input", { type: "checkbox", checked: true, onchange: e => { e.target.checked ? sel.add(k) : sel.delete(k); } }), `${KIND[k] || k} (${g.by_kind[k].total})`)), h("label", { class: "check" }, review, "Include items I've already mastered")),
    h("div", { style: { marginTop: "14px" } }, h("button", { class: "btn primary", onclick: async e => { if (!sel.size) return toast("Choose at least one kind.", "err");
      await withBusy(e.currentTarget, async () => { try { const s = await api(`/courses/${CID}/study/sessions`, { method: "POST", json: { kinds: [...sel], limit: 10, include_mastered: review.checked } }); if (!s.items.length) return toast("You've mastered everything here."); run(p, s); } catch (x) { toast(explain(x, "start the session"), "err"); } }); } }, "Start a session of up to 10")));
}
function run(p, s) {
  let i = 0, res = {}, out = null, busy = false;
  const head = it => h("div", null, h("div", { class: "deck-top" }, h("span", { class: "faint", text: `${KIND[it.kind] || it.kind} · ${i + 1} of ${s.items.length}` }), lectureLink(it.lecture_id, titleOf(it.lecture_id))), h("div", { class: "card-face" }, h("div", { class: "face-text serif", text: it.prompt })));
  async function attempt(it, body) {
    if (busy) return; busy = true;
    try { out = await api(`/study/sessions/${s.session_id}/attempts`, { method: "POST", json: { item_id: it.item_id, ...body } }); res[it.item_id] = out.correct; announce(out.correct ? "Correct" : "Not quite"); } catch (e) { toast(explain(e, "record your answer"), "err"); } finally { busy = false; draw(); }
  }
  function draw() {
    if (i >= s.items.length) return done();
    const it = s.items[i];
    const reveal = out ? h("div", { class: "reveal", role: "status" }, h("p", { class: "serif", text: out.reveal.answer || "" }), out.reveal.explanation ? h("p", { class: "faint", text: out.reveal.explanation }) : null,
      live(out.reveal.evidence).length ? h("div", { class: "cites" }, ...live(out.reveal.evidence).slice(0, 3).map(e => citeLink({ ...e, lecture_id: it.lecture_id }))) : null,
      h("button", { class: "btn primary sm", onclick: () => { i++; out = null; draw(); } }, i === s.items.length - 1 ? "Finish" : "Next")) : null;
    let ctl;
    if (it.kind === "mcq") ctl = h("div", { role: "group", "aria-label": "Answers" }, ...(it.options || []).map(o => h("button", { type: "button", class: "opt" + (out ? (o.id === out.reveal.correct_option ? " ok" : o.id === out.selected_option ? " bad" : "") : ""), disabled: !!out || busy, onclick: () => attempt(it, { selected_option: o.id }) }, h("span", { class: "k mono", text: o.id.toUpperCase() }), h("span", { text: o.text }))));
    else if (!out) ctl = h("div", { class: "grade", role: "group", "aria-label": "How well did you know it?" }, h("p", { class: "faint", text: "Try to answer in your head, then grade yourself. The lecture's answer appears after." }), ...[["know", "Got it"], ["unsure", "Unsure"], ["dont_know", "Missed it"]].map(([k, l]) => h("button", { class: "btn sm", type: "button", onclick: () => attempt(it, { self_grade: k }) }, l)));
    clear(p, head(it), ctl, reveal);
  }
  async function done() { let f; try { f = await api(`/study/sessions/${s.session_id}/finish`, { method: "POST" }); } catch (e) { toast(explain(e, "finish"), "err"); }
    const sm = (f && f.summary) || { answered: Object.keys(res).length, correct: Object.values(res).filter(Boolean).length };
    clear(p, h("div", { class: "empty" }, h("h2", { text: "Session complete" }), h("p", { text: `${sm.correct} of ${sm.answered} answered correctly.` }), h("button", { class: "btn primary", onclick: () => practice(p) }, "Practise again"))); }
  draw();
}
init();
