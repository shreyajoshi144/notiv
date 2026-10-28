// Lecture viewer: media + slide-aware ruler + slides on the stage; transcript, evidence, moments, summary, search, ask, study and notes in the margin.
import { $, $$, h, clear, api, mountShell, pageError, skeletons, icon, plural, when, mmss, duration, stateChip, explain, toast, debounce, announce, confirmDialog, withBusy, copyText, notice, menuButton, formDialog, ApiError, onKeyActivate } from "/static/ui/core.js";
import { addToNotebookDialog, saveToNotebookDialog } from "/static/ui/dialogs.js";
import { mountAsk, mountStudy, mountNotes } from "/static/ui/lecture-tools.js";

const LID = Number((location.pathname.match(/\/lectures\/(\d+)/) || [])[1] || 0);
const hashParams = () => new URLSearchParams(location.hash.slice(1));
const L = p => `/lectures/${LID}/${p}`;
const S = { ov: null, slides: [], segs: [], tl: null, evid: null, moments: [], summary: [], media: null, curSlide: null, curSeg: null, spans: [], follow: true, evFilter: "", course: null };
const SPAN_COLORS = ["#cdd3ae", "#ecd29a", "#e2bcab", "#d3c9b0"];
let main, pollTimer = null, started = false;

/* ── shared pieces ─────────────────────────────────────────────────────────────────── */
const badge = (origin, label) => h("span", { class: "origin " + origin, text: label });
function citeBtn(c, extra) {
  const can = c.seek_seconds != null || c.slide_index != null;
  return h("button", { class: "cite", type: "button", disabled: !can, title: can ? "Jump to this point in the lecture" : "No lecture position available", onclick: ev => { ev.stopPropagation(); seekTo({ seconds: c.seek_seconds, slide: c.slide_index, ...(extra || {}), play: true }); } }, c.label);
}
function evidenceRow(it, o = {}) {
  const c = it.citation;
  return h("div", { class: "evrow", dataset: { ev: it.evidence_id } }, h("div", { class: "cites", style: { marginTop: 0 } }, badge(it.origin, it.origin_label), citeBtn(c), c.source && c.source.filename ? h("span", { class: "faint", text: "from " + c.source.filename }) : null),
    h("div", { class: o.quote ? "quote" : "", text: it.text }));
}
const supportBlock = evs => evs && evs.length ? h("details", null, h("summary", { text: `Supporting lecture evidence (${evs.length})` }), h("div", null, ...evs.map(e => evidenceRow(e, { quote: true })))) : null;
const ctx = { get LID() { return LID; }, get S() { return S; }, L, citeBtn, evidenceRow, badge, seekTo: o => seekTo(o), openEvidence: (...a) => openEvidence(...a), saveToNotebook: o => saveToNotebookDialog({ lectureId: LID, lectureTitle: S.ov && S.ov.title, ...o }), activate: id => activate(id) };

/* ── page states ───────────────────────────────────────────────────────────────────── */
function headerBlock(ov) {
  const menu = menuButton({ cls: "btn quiet", content: [icon("more", "more"), h("span", { class: "sr", text: "More actions" })], build: close => [
    h("button", { type: "button", onclick: () => { close(); $("#assetPicker").click(); } }, icon("upload"), "Upload another source"),
    h("button", { type: "button", onclick: () => { close(); copyMoment(); } }, icon("link"), "Copy link to this moment"),
    h("div", { class: "sep" }),
    h("button", { type: "button", onclick: async () => { close(); const ok = await confirmDialog({ title: "Delete this lecture?", body: `“${ov.title}” — the recording, slides, transcript, notes and study history are permanently deleted. Notebooks that linked it keep what you already saved.`, confirm: "Delete lecture", danger: true }); if (!ok) return;
      try { await api("/lectures/" + LID, { method: "DELETE" }); location.assign("/app"); } catch (e) { toast(explain(e, "delete the lecture"), "err"); } } }, icon("trash"), "Delete lecture")] });
  return h("div", { class: "lec-head" },
    h("div", { class: "crumbs" }, h("a", { href: "/app", text: "Desk" }), ov.course_id ? [h("span", { class: "sep", text: "/" }), h("a", { href: "/app/courses/" + ov.course_id, text: S.course || "Course" })] : null),
    h("div", { class: "pagehead", style: { margin: 0 } }, h("div", { class: "titles" }, h("h1", { text: ov.title || "Untitled lecture" }), h("div", { class: "kv", style: { marginTop: "8px" } }, stateChip(ov.state === "done" || ov.ready ? "done" : ov.state), ov.duration ? h("span", { text: duration(ov.duration) }) : null, ov.created_at ? h("span", { text: "Added " + when(ov.created_at) }) : null)),
      h("div", { class: "actions" }, ov.ready ? h("button", { class: "btn", onclick: () => addToNotebookDialog({ lectureId: LID, lectureTitle: ov.title }) }, icon("book"), "Add to notebook") : null,
        h("button", { class: "btn", onclick: () => $("#assetPicker").click() }, icon("upload"), "Upload source"), menu)),
    h("input", { id: "assetPicker", type: "file", hidden: true, accept: ".pdf,.pptx,.mp3,.mp4", onchange: uploadAsset }));
}
async function uploadAsset(e) {
  const file = e.target.files && e.target.files[0]; if (!file) return;
  try {
    toast(`Uploading ${file.name}…`);
    const form = new FormData(); form.append("file", file, file.name);
    await api(`/lectures/${LID}/assets`, { method: "POST", form });
    await api(`/lectures/${LID}/process`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } });
    toast("Uploaded. Processing has started."); start();
  } catch (err) { toast(err.status === 422 ? err.message : explain(err, "upload the file"), "err"); }
  finally { e.target.value = ""; }
}
function processingBody(ov) {
  const failed = ov.state === "failed", pct = ov.progress_pct || 0, noInput = ov.state !== "failed" && !ov.job && !(ov.inputs && ov.inputs.length);
  const stages = (ov.stages || []).map(s => h("li", { class: s.status }, h("span", { class: "g", text: s.status === "completed" ? "✓" : s.status === "failed" ? "✕" : s.status === "running" ? "…" : "·" }), h("span", { text: s.stage.replaceAll("_", " ").toLowerCase() }), s.error ? h("span", { class: "faint", text: "— " + s.error }) : null));
  if (noInput) return h("div", { class: "proc" }, h("h2", { text: "This lecture has no recording or slides yet" }), h("p", { class: "muted", text: "Upload a recording, slides, or both. Notiv will transcribe the audio, place each line on its slide and keep every result tied to its source." }),
    h("div", { class: "actions", style: { display: "flex", gap: "10px" } }, h("button", { class: "btn primary", onclick: () => $("#assetPicker").click() }, icon("upload"), "Upload a recording or slides")));
  return h("div", { class: "proc", role: failed ? "alert" : "status" },
    h("h2", { text: failed ? "Processing failed" : ov.state === "queued" ? "Waiting to process" : "Processing your lecture" }),
    h("p", { class: "muted", text: failed ? (ov.error || "Something went wrong while processing this lecture.") : `${ov.stage || "Starting"}. This page updates by itself, so you can leave and come back.` }),
    failed ? null : h("div", { class: "bar", style: { width: "100%" }, role: "progressbar", "aria-label": "Processing progress", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct }, h("i", { style: { width: pct + "%" } })),
    stages.length ? h("ul", { class: "stages" }, ...stages) : null,
    h("div", { style: { display: "flex", gap: "10px", flexWrap: "wrap" } },
      failed && ov.can_retry && ov.job ? h("button", { class: "btn primary", onclick: async e => { await withBusy(e.currentTarget, async () => { try { await api(`/jobs/${ov.job.id}/retry`, { method: "POST" }); start(); } catch (x) { toast(explain(x, "retry"), "err"); } }); } }, icon("refresh"), "Retry processing") : null,
      h("button", { class: "btn", onclick: () => $("#assetPicker").click() }, icon("upload"), "Upload another source")));
}

async function start() {
  clearTimeout(pollTimer);
  if (!LID) return pageError(main, new ApiError(404, "not found"), null);
  let ov;
  try { ov = await api(L("overview")); } catch (e) { if (e.status !== 401) pageError(main, e, start, "this lecture"); return; }
  S.ov = ov;
  try {   // "continue where you left off" on the desk: the last few lectures opened in this browser (title + id only)
    const k = "notiv.recent.v1", list = JSON.parse(localStorage.getItem(k) || "[]").filter(x => x && x.id !== LID);
    localStorage.setItem(k, JSON.stringify([{ id: LID, title: ov.title || "Untitled lecture", at: Date.now() }, ...list].slice(0, 5)));
  } catch (_) {}
  if (!S.course && ov.course_id) { try { S.course = (await api("/courses?limit=200")).items.find(c => c.id === ov.course_id)?.name; } catch (_) {} }
  document.title = (ov.title || "Lecture") + " — Notiv";
  if (!ov.ready) { clear(main, headerBlock(ov), processingBody(ov)); if (ov.state !== "failed" && (ov.job || (ov.inputs && ov.inputs.length))) pollTimer = setTimeout(start, 2500); return; }
  clear(main, headerBlock(ov), h("div", { class: "lec-grid" }, h("div", { class: "stage" }, ...skeletons(2)), h("div", { class: "margin" }, ...skeletons(4, ""))));
  try { await loadWorkspace(); } catch (e) { if (e.status !== 401) pageError(main, e, start, "the lecture content"); }
}

async function loadWorkspace() {
  const [sl, tr, tl, mo, su] = await Promise.all([api(L("slides")), api(L("transcript?limit=1000")), api(L("timeline")), api(L("key-moments")), api(L("summary"))]);
  Object.assign(S, { slides: sl.slides, segs: tr.segments, tl, moments: mo.key_moments, summary: su.sections, summaryInfo: su, spans: tl.spans.filter(s => s.slide_index != null), evid: null, curSlide: null, curSeg: null });
  buildLayout(); wireMedia(); renderAll();
  const p = hashParams();
  if (p.get("t")) seekTo({ seconds: Number(p.get("t")) }); else if (p.get("e")) openEvidenceId(Number(p.get("e"))); else if (S.slides.length) showSlide(S.slides[0].slide_index);
  if (!hashWired) { hashWired = true; window.addEventListener("hashchange", () => { const q = hashParams(); if (!S.ov || !S.ov.ready) return; if (q.get("t") != null && q.get("t") !== "") seekTo({ seconds: Number(q.get("t")), play: false }); else if (q.get("e")) openEvidenceId(Number(q.get("e"))); }); }
}
let hashWired = false;

const TABS = [["transcript", "Transcript"], ["evidence", "Evidence"], ["moments", "Key moments"], ["summary", "Summary"], ["ask", "Ask"], ["study", "Study"], ["notes", "Notes"], ["search", "Search"]];
function buildLayout() {
  const ov = S.ov, m = ov.media, hasSlides = S.slides.length > 0;
  const player = !m ? h("div", { class: "noplay", role: "note", text: "No recording was uploaded for this lecture. You can read the transcript and slides, but there is nothing to play." })
    : h("div", { class: "player" }, m.type === "video" ? h("video", { id: "media", controls: true, preload: "metadata", src: m.url, "aria-label": "Lecture video" }) : h("audio", { id: "media", controls: true, preload: "metadata", src: m.url, "aria-label": "Lecture audio" }));
  const warns = (ov.warnings || []).filter(w => w.code !== "no_recording").map(w => h("div", { class: "noplay", role: "note", text: w.message }));
  const ruler = m ? h("div", null, h("div", { id: "timeline", class: "ruler", role: "slider", tabindex: 0, "aria-label": "Lecture timeline", "aria-valuemin": 0, "aria-valuemax": Math.round(ov.duration || 0), "aria-valuenow": 0, "aria-valuetext": "0:00" }, h("div", { class: "head", id: "head" })),
    h("div", { class: "ruler-info" }, h("span", { id: "tlNow", class: "mono", text: "0:00" }), h("span", { id: "tlSlide" }), h("span", { class: "mono", text: mmss(ov.duration) }))) : null;
  const slideCard = hasSlides ? h("div", null, slideToolbar(), h("div", { class: "slidebox", id: "slidebox", tabindex: "0", role: "group", "aria-label": "Slide viewer. Arrow keys change slide; plus and minus zoom.", onkeydown: slideKeys }, h("img", { id: "curImg", alt: "", onload: layoutSlide })),
    h("div", { class: "slidemeta" }, h("span", { class: "ttl", id: "curTitle" }), h("label", { class: "check", style: { fontSize: ".88rem" } }, h("input", { type: "checkbox", id: "follow", checked: true, onchange: e => { S.follow = e.target.checked; if (S.follow && S.media) onTime(); } }), "Follow the video")),
    h("div", { class: "thumbs", id: "thumbs", role: "list" }, ...S.slides.map(s => h("button", { role: "listitem", type: "button", dataset: { slide: s.slide_index }, title: s.title, "aria-label": `Slide ${s.slide_index}: ${s.title}`, onclick: () => seekTo({ slide: s.slide_index, seconds: s.seek_seconds, pin: s.seek_seconds == null }) },
      h("img", { src: s.image_url, alt: "", loading: "lazy" }), h("span", { class: "n", text: s.slide_index }), s.seek_seconds != null ? h("span", { class: "nt", text: mmss(s.seek_seconds) }) : null)))) : null;
  const margin = h("div", { class: "margin" },
    h("div", { class: "tabs", id: "tabs", role: "tablist", "aria-label": "Lecture material" }, ...TABS.map(([id, label], i) => {
      const n = id === "transcript" ? S.segs.length : id === "evidence" ? ov.counts.evidence : id === "moments" ? ov.counts.key_moments : id === "summary" ? ov.counts.summary_sections : null;
      return h("button", { class: "tab", role: "tab", id: "tab-" + id, "aria-selected": i === 0, "aria-controls": "p-" + id, tabindex: i === 0 ? 0 : -1, onclick: () => activate(id), onkeydown: tabKeys }, label, n != null ? h("span", { class: "n", text: n }) : null); })),
    ...TABS.map(([id], i) => h("div", { class: "mpanel", id: "p-" + id, role: "tabpanel", "aria-labelledby": "tab-" + id, tabindex: "0", hidden: i !== 0 })));
  clear(main, headerBlock(ov), h("div", { class: "lec-grid" }, h("div", { class: "stage" }, player, ...warns, ruler, slideCard), margin));
  if (m) wireTimeline();
}
function tabKeys(e) {
  const t = $$("#tabs button"); let i = t.indexOf(e.target);
  if (e.key === "ArrowRight") i = (i + 1) % t.length; else if (e.key === "ArrowLeft") i = (i - 1 + t.length) % t.length; else if (e.key === "Home") i = 0; else if (e.key === "End") i = t.length - 1; else return;
  e.preventDefault(); t[i].focus(); activate(t[i].id.slice(4));
}
const loaded = {};
function activate(id) {
  $$("#tabs button").forEach(b => { const on = b.id === "tab-" + id; b.setAttribute("aria-selected", on); b.tabIndex = on ? 0 : -1; });
  $$(".mpanel").forEach(p => { p.hidden = p.id !== "p-" + id; });
  if (id === "evidence" && !S.evid) loadEvidence();
  if (id === "search") { const q = $("#q"); if (q) q.focus(); }
  if (id === "ask" && !loaded.ask) { loaded.ask = true; mountAsk($("#p-ask"), ctx); }
  if (id === "study" && !loaded.study) { loaded.study = true; mountStudy($("#p-study"), ctx); }
  if (id === "notes" && !loaded.notes) { loaded.notes = true; mountNotes($("#p-notes"), ctx); }
}

/* ── panels ────────────────────────────────────────────────────────────────────────── */
function renderAll() { for (const k of Object.keys(loaded)) delete loaded[k]; renderTranscript(); renderMoments(); renderSummary(); renderSearchShell(); }
function renderTranscript() {
  const p = $("#p-transcript");
  if (!S.segs.length) { clear(p, h("div", { class: "empty" }, h("h3", { text: "No transcript" }), h("p", { text: S.ov.mode.has_transcript ? "" : "There is no recording, or no speech was detected in it." }))); return; }
  clear(p, h("p", { class: "mnote" }, badge("professor_said", "Professor said"), " Transcribed from the recording. Select a line to jump to it; use the pencil to correct a mistake."),
    ...S.segs.map(s => {
      const row = h("div", { class: "line", id: "seg-" + s.segment_id, dataset: { seg: s.segment_id }, role: "button", tabindex: 0, onclick: () => seekTo({ seconds: s.citation.seek_seconds, slide: s.slide_index, segment: s.segment_id, play: true }) },
        h("span", { class: "t", text: s.timestamp }),
        h("div", null, h("div", { class: "m" }, s.slide_index != null ? h("span", { text: "Slide " + s.slide_index }) : (S.slides.length ? h("span", { text: "no slide matched" }) : null), s.confidence != null && s.confidence < 0.6 ? h("span", { title: "Low alignment confidence", text: "low confidence" }) : null, s.text_edited ? h("span", { text: "corrected" }) : null), h("p", { class: "tx", text: s.text })),
        h("button", { class: "iconbtn fix", type: "button", "aria-label": `Correct the line at ${s.timestamp}`, onclick: e => { e.stopPropagation(); correctLine(s); } }, icon("edit")));
      onKeyActivate(row, e => { if (e.target === row) row.click(); }); return row; }));
}
async function correctLine(s) {
  const slideOpts = [{ value: "", label: s.slide_index != null ? "Keep Slide " + s.slide_index : "Leave unmatched" }, ...S.slides.map(x => ({ value: String(x.slide_index), label: `Slide ${x.slide_index}: ${x.title}` })), { value: "none", label: "Not on any slide" }];
  const v = await formDialog({ title: `Correct the line at ${s.timestamp}`, intro: "Your correction replaces the text everywhere it's quoted. Notes that cited the original will show it was edited.", fields: [{ name: "text", label: "What was said", type: "textarea", value: s.text, required: true, max: 5000, rows: 4 }, ...(S.slides.length ? [{ name: "slide", label: "Slide on screen", type: "select", value: "", options: slideOpts }] : [])], submit: "Save correction" });
  if (!v) return;
  const body = {}; if (v.text && v.text !== s.text) body.text = v.text; if (v.slide === "none") body.clear_slide = true; else if (v.slide) body.slide_index = Number(v.slide);
  if (!Object.keys(body).length) return;
  try { await api(L("segments/" + s.segment_id), { method: "PATCH", json: body }); toast("Correction saved."); const tr = await api(L("transcript?limit=1000")); S.segs = tr.segments; S.evid = null; renderTranscript(); S.curSeg = null; onTime(); }
  catch (e) { toast(explain(e, "save the correction"), "err"); }
}
async function loadEvidence() {
  const p = $("#p-evidence"); clear(p, ...skeletons(3));
  try { S.evid = await api(L("evidence?limit=200")); renderEvidence(); } catch (e) { clear(p, notice("err", h("p", { text: explain(e, "load the evidence") }))); }
}
function renderEvidence() {
  const p = $("#p-evidence");
  const sel = h("select", { "aria-label": "Filter evidence", style: { width: "auto" }, onchange: e => { S.evFilter = e.target.value; renderEvidence(); } }, h("option", { value: "" }, "All lecture evidence"), h("option", { value: "transcript" }, "Professor said"), h("option", { value: "slide" }, "Slide text")); sel.value = S.evFilter;
  const items = S.evid.items.filter(i => !S.evFilter || i.source_type === S.evFilter);
  clear(p, h("div", { class: "toolbar", style: { marginTop: 0 } }, sel, h("span", { class: "faint", text: plural(items.length, "item") })), h("p", { class: "mnote", text: "Evidence is the source material every Notiv answer is built from." }),
    ...(items.length ? items.map(i => evidenceRow(i)) : [h("p", { class: "faint", text: "No evidence." })]), S.evid.has_more ? h("p", { class: "mnote", text: "Showing the first " + S.evid.items.length + " items." }) : null);
}
function insightCard(m, body) {
  const label = [m.slide_index != null ? "Slide " + m.slide_index : null, m.start != null ? mmss(m.start) + (m.end != null ? "–" + mmss(m.end) : "") : null].filter(Boolean).join(" · ");
  return h("div", { class: "card-note", dataset: { insight: m.id } }, h("div", { class: "cites", style: { marginTop: 0 } }, badge(m.origin || "notiv_generated", m.origin_label || "Notiv analysis"), (m.seek_seconds != null || m.slide_index != null) ? citeBtn({ label: label || "jump", seek_seconds: m.seek_seconds, slide_index: m.slide_index }) : null),
    h("h3", { text: m.title }), m.reason ? h("div", { class: "why", text: m.reason }) : null, ...(body || []), supportBlock(m.evidence),
    h("div", { style: { marginTop: "10px" } }, h("button", { class: "btn sm quiet", type: "button", onclick: async () => { await ctx.saveToNotebook({ title: m.title, content: `## ${m.title}\n\n${m.body || m.reason || ""}\n\n${label ? "Source: " + label : ""}`.trim(), evidenceIds: m.evidence_ids || [], kind: "lecture_insight" }); } }, icon("book"), "Save to a notebook")));
}
function renderMoments() { clear($("#p-moments"), h("p", { class: "mnote", text: "Selected by Notiv from the transcript and slides. Each links to the evidence it came from." }), ...(S.moments.length ? S.moments.map(m => insightCard(m)) : [h("p", { class: "faint", text: "No key moments could be derived from this lecture's evidence." })])); }
function renderSummary() {
  const su = S.summaryInfo || {}, ai = su.mode === "ai";
  const note = ai ? (su.note || "Written by AI from this lecture's evidence; every section cites its pages.") : "Assembled from verbatim lecture excerpts, not rewritten. Open a section to see its sources.";
  const head = ai ? [su.overview ? h("div", { class: "card-note" }, h("h3", { text: su.title || "Overview" }), h("p", { text: su.overview })) : null,
    (su.takeaways || []).length ? h("div", { class: "card-note" }, h("h3", { text: "Key takeaways" }), h("ul", null, ...su.takeaways.map(t => h("li", { text: t.text + (t.pages && t.pages.length ? ` (p. ${t.pages.join(", ")})` : t.times && t.times.length ? ` (at ${t.times.slice(0, 4).map(mmss).join(", ")})` : "") })))) : null] : [];
  clear($("#p-summary"), h("p", { class: "mnote", text: note }), ...head.filter(Boolean),
    ...(S.summary.length ? S.summary.map(m => insightCard(m, m.body ? [ai ? h("p", { text: m.body }) : h("blockquote", { class: "quote", text: m.body })] : [])) : [h("p", { class: "faint", text: "There isn't enough evidence to build a summary." })]));
}
let searchSeq = 0;
function renderSearchShell() {
  clear($("#p-search"), h("div", { class: "field" }, h("label", { class: "sr", for: "q", text: "Search this lecture" }), h("input", { id: "q", type: "search", placeholder: "Search transcript, slides and evidence   ( / )", autocomplete: "off", oninput: debounce(e => runSearch(e.target.value), 250) })),
    h("div", { id: "results", "aria-live": "polite" }, h("p", { class: "faint", text: "Type to search. Each result shows where it sits in the lecture." })));
}
async function runSearch(q) {
  const box = $("#results"); q = q.trim(); if (!q) { clear(box, h("p", { class: "faint", text: "Type to search." })); return; }
  const my = ++searchSeq; let r;
  try { r = await api(L("search?limit=30&q=" + encodeURIComponent(q))); } catch (e) { if (my === searchSeq) clear(box, notice("err", h("p", { text: e.status === 400 ? e.message : "Search failed: " + explain(e, "search") }))); return; }
  if (my !== searchSeq) return;
  announce(r.count + " results");
  if (!r.results.length) { clear(box, h("p", { class: "faint", text: `No matches for “${q}”.` })); return; }
  clear(box, r.match === "any" ? h("p", { class: "mnote", text: "No line contains every word, so these contain some of them." }) : null,
    ...r.results.map(it => h("div", { class: "evrow", role: "button", tabindex: 0, dataset: { ev: it.evidence_id }, onclick: () => openEvidence(it, { tab: true }) },
      h("div", { class: "cites", style: { marginTop: 0 } }, badge(it.origin, it.origin_label), citeBtn(it.citation, { tab: it.source_type === "transcript" })), h("div", null, ...it.snippet.map(s => s.match ? h("mark", { text: s.text }) : s.text)))));
}

/* ── synchronisation: one entry point for every "go here" ──────────────────────────── */
const bisect = (arr, t, key) => { let lo = 0, hi = arr.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid][key] <= t) lo = mid + 1; else hi = mid; } return lo - 1; };
function slideAt(t) { const i = bisect(S.spans, t, "start"); if (i < 0) return null; const e = S.spans[i]; return t < e.end || (i === S.spans.length - 1 && t <= e.end) ? e.slide_index : null; }
function segAt(t) { const i = bisect(S.segs, t, "start"); for (let j = i; j >= Math.max(0, i - 3); j--) if (S.segs[j] && S.segs[j].start <= t && t < S.segs[j].end) return S.segs[j]; return null; }
// ── slide viewer: every page complete (never cropped), fit-to-page / fit-to-width, zoom, page navigation, scrolling ──
const Z = { mode: "page", z: 1, aspectSet: false };
function slideToolbar() {
  Z.aspectSet = false; Z.mode = "page"; Z.z = 1;
  const btn = (id, label, text, fn) => h("button", { class: "btn sm quiet", type: "button", id, "aria-label": label, title: label, onclick: fn }, text);
  return h("div", { class: "slidebar", role: "toolbar", "aria-label": "Slide controls" },
    btn("sPrev", "Previous slide", "‹", () => stepSlide(-1)),
    h("label", { class: "sr", for: "sNum", text: "Slide number" }),
    h("input", { id: "sNum", class: "snum", type: "number", min: 1, max: S.slides.length, value: 1, inputmode: "numeric", onchange: e => gotoSlideNumber(Number(e.target.value)) }),
    h("span", { class: "stot", id: "sTot", text: "of " + S.slides.length }),
    btn("sNext", "Next slide", "›", () => stepSlide(1)),
    h("span", { class: "sgap" }),
    btn("zOut", "Zoom out", "−", () => zoomBy(1 / 1.25)), h("span", { class: "zlbl mono", id: "zLbl", "aria-live": "polite", text: "100%" }), btn("zIn", "Zoom in", "+", () => zoomBy(1.25)),
    btn("zPage", "Fit whole slide", "Fit page", () => { Z.mode = "page"; layoutSlide(); }),
    btn("zWidth", "Fit slide to width", "Fit width", () => { Z.mode = "width"; layoutSlide(); }));
}
function layoutSlide() {
  const box = $("#slidebox"), img = $("#curImg"); if (!box || !img || !img.naturalWidth) return;
  if (!Z.aspectSet) { box.style.aspectRatio = String(Math.min(2, Math.max(0.6, img.naturalWidth / img.naturalHeight))); Z.aspectSet = true; }
  const bw = box.clientWidth, bh = box.clientHeight, nw = img.naturalWidth, nh = img.naturalHeight;
  if (!bw || !bh) return;
  const fitP = Math.min(bw / nw, bh / nh), fitW = bw / nw;
  const scale = Z.mode === "width" ? fitW : Z.mode === "custom" ? fitP * Z.z : fitP;
  img.style.width = Math.round(nw * scale) + "px"; img.style.height = Math.round(nh * scale) + "px";
  const lbl = $("#zLbl"); if (lbl) lbl.textContent = Math.round(scale / fitP * 100) + "%";
  const pg = $("#zPage"), wd = $("#zWidth"); if (pg) pg.setAttribute("aria-pressed", Z.mode === "page"); if (wd) wd.setAttribute("aria-pressed", Z.mode === "width");
}
function zoomBy(f) {
  const box = $("#slidebox"), img = $("#curImg"); if (!box || !img || !img.naturalWidth) return;
  const fitP = Math.min(box.clientWidth / img.naturalWidth, box.clientHeight / img.naturalHeight);
  const cur = img.getBoundingClientRect().width / img.naturalWidth;
  Z.mode = "custom"; Z.z = Math.min(4, Math.max(0.25, (cur / fitP) * f)); layoutSlide();
}
function stepSlide(d) {
  const i = S.slides.findIndex(x => x.slide_index === S.curSlide), n = S.slides[Math.min(S.slides.length - 1, Math.max(0, (i < 0 ? 0 : i) + d))];
  if (n) seekTo({ slide: n.slide_index, seconds: n.seek_seconds, pin: n.seek_seconds == null });
}
function gotoSlideNumber(k) {
  const n = S.slides[Math.min(S.slides.length, Math.max(1, Math.round(k) || 1)) - 1];
  if (n) seekTo({ slide: n.slide_index, seconds: n.seek_seconds, pin: n.seek_seconds == null });
}
function slideKeys(e) {
  if (e.target.tagName === "INPUT") return;
  if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); stepSlide(1); }
  else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); stepSlide(-1); }
  else if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomBy(1.25); }
  else if (e.key === "-") { e.preventDefault(); zoomBy(1 / 1.25); }
  else if (e.key === "0") { e.preventDefault(); Z.mode = "page"; layoutSlide(); }
}
if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => layoutSlide()).observe(document.body);
function showSlide(idx) {
  if (idx == null || idx === S.curSlide || !S.slides.length) return;
  const s = S.slides.find(x => x.slide_index === idx); if (!s) return;
  S.curSlide = idx; const img = $("#curImg"); img.src = s.image_url; img.alt = `Slide ${idx}: ${s.title}`; $("#curTitle").textContent = `Slide ${idx}: ${s.title}`;
  const num = $("#sNum"); if (num) num.value = S.slides.findIndex(x => x.slide_index === idx) + 1; const box = $("#slidebox"); if (box) { box.scrollTop = 0; box.scrollLeft = 0; }
  const pi = S.slides.findIndex(x => x.slide_index === idx), pb = $("#sPrev"), nb = $("#sNext"); if (pb) pb.disabled = pi <= 0; if (nb) nb.disabled = pi >= S.slides.length - 1;
  $$("#thumbs button").forEach(b => { const on = Number(b.dataset.slide) === idx; b.setAttribute("aria-current", on); if (on && S.follow) { const t = $("#thumbs"); t.scrollLeft = b.offsetLeft - (t.clientWidth - b.offsetWidth) / 2; } });
}
function markSeg(id, scroll) {
  if (id === S.curSeg) return; S.curSeg = id;
  $$("#p-transcript .line.on").forEach(r => r.classList.remove("on"));
  const r = id != null && $("#seg-" + id); if (!r) return; r.classList.add("on");
  if (scroll && !$("#p-transcript").hidden) r.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion:reduce)").matches ? "auto" : "smooth" });
}
function paintTime(t) {
  const d = S.ov.duration || (S.media && S.media.duration) || 0, head = $("#head"); if (head && d) head.style.left = Math.min(100, t / d * 100) + "%";
  const tl = $("#timeline"); if (tl) { tl.setAttribute("aria-valuenow", Math.round(t)); tl.setAttribute("aria-valuetext", mmss(t)); }
  const now = $("#tlNow"); if (now) now.textContent = mmss(t); const sl = $("#tlSlide"); if (sl) sl.textContent = S.curSlide != null ? "Slide " + S.curSlide : "";
}
function onTime() { const t = S.media ? S.media.currentTime : 0; if (S.follow) showSlide(slideAt(t)); const g = segAt(t); markSeg(g ? g.segment_id : null, S.follow); paintTime(t); }
function seekTo({ seconds = null, slide = null, segment = null, play = false, tab = false, pin = false }) {
  if (tab) activate("transcript");
  if (seconds != null && S.media) { const d = S.media.duration || S.ov.duration || Infinity; S.media.currentTime = Math.min(Math.max(0, seconds), d); if (play) S.media.play().catch(() => {}); S.follow = true; const f = $("#follow"); if (f) f.checked = true; }
  else if (pin) { S.follow = false; const f = $("#follow"); if (f) f.checked = false; }
  if (slide != null) showSlide(slide); else if (seconds != null) showSlide(slideAt(seconds));
  const seg = segment != null ? S.segs.find(s => s.segment_id === segment) : seconds != null ? segAt(seconds) : null;
  markSeg(seg ? seg.segment_id : null, true);
  if (seg) { const el = $("#seg-" + seg.segment_id); if (el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); } }
  paintTime(seconds != null ? seconds : (S.media ? S.media.currentTime : 0));
  if (seconds != null) announce("Jumped to " + mmss(seconds));
}
async function openEvidence(it, o = {}) {
  const c = it.citation; let pos = { seek_seconds: c.seek_seconds, slide_index: c.slide_index, segment_id: null };
  try { pos = await api(L("position?evidence=" + it.evidence_id)); } catch (_) {}
  seekTo({ seconds: pos.seek_seconds, slide: pos.slide_index, segment: pos.segment_id, play: pos.seek_seconds != null, tab: !!o.tab && it.source_type === "transcript" });
}
async function openEvidenceId(id) { const r = await api(L(`evidence/${id}`)).catch(() => null); if (r) openEvidence(r); }
let keysWired = false;
function wireMedia() {
  S.media = $("#media"); if (!S.media) return;
  ["timeupdate", "seeked", "loadedmetadata"].forEach(ev => S.media.addEventListener(ev, onTime));
  S.media.addEventListener("pause", () => history.replaceState(null, "", "#t=" + Math.floor(S.media.currentTime)));
  let refreshing = false;
  S.media.addEventListener("error", async () => { if (refreshing) return; refreshing = true; const t = S.media.currentTime;
    try { const ov = await api(L("overview")); S.media.src = ov.media.url; S.media.addEventListener("loadedmetadata", () => { S.media.currentTime = t; }, { once: true }); } catch (e) { toast("The recording couldn't be loaded: " + explain(e, "load it"), "err"); } finally { refreshing = false; } });
  if (keysWired) return; keysWired = true;
  document.addEventListener("keydown", e => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "/" && tag !== "input" && tag !== "textarea") { e.preventDefault(); activate("search"); return; }
    if (!S.media || tag === "input" || tag === "select" || tag === "textarea" || e.target.closest("#timeline") || e.target.closest("dialog")) return;
    if (e.key === "ArrowRight" && !e.target.closest(".tabs")) S.media.currentTime += 5; else if (e.key === "ArrowLeft" && !e.target.closest(".tabs")) S.media.currentTime -= 5;
    else if (e.key === " " && !["button", "summary", "a"].includes(tag) && !e.target.closest(".line")) { e.preventDefault(); S.media.paused ? S.media.play() : S.media.pause(); }
  });
}
function wireTimeline() {
  const tl = $("#timeline"), d = S.ov.duration || 1, head = $("#head");
  for (const s of S.tl.spans) tl.insertBefore(h("div", { class: "span" + (s.slide_index == null ? " none" : ""), style: { left: s.start / d * 100 + "%", width: (s.end - s.start) / d * 100 + "%", ...(s.slide_index != null ? { background: SPAN_COLORS[(s.slide_index - 1) % SPAN_COLORS.length] } : {}) },
    title: s.slide_index != null ? `Slide ${s.slide_index}: ${s.title || ""}, ${mmss(s.start)} to ${mmss(s.end)}` : `No slide recognised, ${mmss(s.start)} to ${mmss(s.end)}` }), head);
  for (const k of S.tl.key_moments) tl.append(h("button", { class: "km", type: "button", style: { left: k.start / d * 100 + "%" }, "aria-label": `Key moment at ${mmss(k.start)}: ${k.title}`, title: `${mmss(k.start)}: ${k.title} (Notiv analysis)`, onclick: e => { e.stopPropagation(); seekTo({ seconds: k.start, slide: k.slide_index, play: true }); } }));
  const at = e => { const r = tl.getBoundingClientRect(); return Math.min(d, Math.max(0, (e.clientX - r.left) / r.width * d)); };
  let drag = false;
  tl.addEventListener("pointerdown", e => { if (e.target.classList.contains("km")) return; drag = true; tl.setPointerCapture(e.pointerId); seekTo({ seconds: at(e) }); });
  tl.addEventListener("pointermove", e => { if (drag) seekTo({ seconds: at(e) }); });
  tl.addEventListener("pointerup", () => { drag = false; });
  tl.addEventListener("keydown", e => { const step = e.shiftKey ? 30 : 5, t = S.media.currentTime;
    if (e.key === "ArrowRight") seekTo({ seconds: t + step }); else if (e.key === "ArrowLeft") seekTo({ seconds: t - step }); else if (e.key === "Home") seekTo({ seconds: 0 }); else if (e.key === "End") seekTo({ seconds: d }); else return; e.preventDefault(); });
}
async function copyMoment() { const t = S.media ? Math.floor(S.media.currentTime) : null; const url = location.origin + location.pathname + (t != null ? "#t=" + t : ""); try { await copyText(url); toast("Link copied" + (t != null ? ` (at ${mmss(t)}).` : ".")); } catch (_) { toast(url); } }

(async function init() { main = await mountShell("desk"); clear(main, ...skeletons(3)); start(); })();
