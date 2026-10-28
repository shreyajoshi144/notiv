import { $, h, clear, api, mountShell, pageError, skeletons, icon, plural, when, mmss, duration, spine, stateChip, explain, toast, debounce, announce } from "/static/ui/core.js";
import { newLectureDialog, newNotebookDialog, newCourseDialog } from "/static/ui/dialogs.js";
import { focusStats, setFocusGoal } from "/static/ui/companion.js";

const S = { lectures: [], courses: [], notebooks: [], filter: "all", q: "", course: "", timer: null, me: null };
let main;
const VIEW = new URLSearchParams(location.search).get("view") === "library" ? "library" : "desk";

const isProcessing = l => l.status === "queued" && l.job_id || l.status === "running" || l.status === "processing" || (l.job_status && ["queued", "running"].includes(l.job_status));
const bucket = l => l.status === "failed" ? "attention" : l.status === "done" ? "ready" : isProcessing(l) ? "processing" : "other";

async function load() {
  const [lec, cou, nb, me] = await Promise.all([api("/lectures?limit=200"), api("/courses?limit=200"), api("/notebooks"), api("/auth/me").catch(() => null)]);
  S.lectures = lec.items; S.courses = cou.items; S.notebooks = nb.items; S.me = me;
}

function head() {
  const ready = S.lectures.filter(l => l.status === "done").length, proc = S.lectures.filter(l => bucket(l) === "processing").length, bad = S.lectures.filter(l => l.status === "failed").length;
  const bits = [];
  if (!S.lectures.length && !S.notebooks.length) bits.push("Nothing here yet. Start with a lecture.");
  else { bits.push(plural(ready, "lecture") + " ready"); if (proc) bits.push(proc + " processing"); if (bad) bits.push(bad + " need attention"); bits.push(plural(S.notebooks.length, "notebook")); }
  return h("div", { class: "pagehead" },
    h("div", { class: "titles" }, h("h1", { text: VIEW === "library" ? "Library" : "Your study desk" }), h("p", { class: "lede", text: bits.join(", ") + "." })),
    h("div", { class: "actions" },
      h("button", { class: "btn", onclick: async () => { const nb = await newNotebookDialog(); if (nb) location.assign("/app/notebooks/" + nb.id); } }, icon("book"), "New notebook"),
      h("button", { class: "btn primary", onclick: addLecture }, icon("upload"), "Add a lecture")));
}
async function retryJob(l, btn) {
  btn.disabled = true;
  try { await api(`/jobs/${l.job_id}/retry`, { method: "POST" }); toast("Retrying. Progress will show here."); await refresh(); }
  catch (e) { btn.disabled = false; toast(explain(e, "retry"), "err"); }
}
function nextAction() {
  const failed = S.lectures.find(l => l.status === "failed"), proc = S.lectures.filter(l => bucket(l) === "processing"), ready = S.lectures.find(l => l.status === "done");
  let eyebrow, title, body, acts;
  if (failed) { eyebrow = "Needs attention"; title = `“${failed.title || "Untitled lecture"}” didn't finish`; body = failed.error ? String(failed.error).replace(/^\w*Error:\s*/, "").slice(0, 140) : "Processing stopped before it was ready. Your original file is kept, so retrying is safe.";
    acts = [failed.job_id ? h("button", { class: "btn primary", type: "button", onclick: e => retryJob(failed, e.currentTarget) }, icon("refresh"), "Retry processing") : null, h("a", { class: "btn", href: "/app/lectures/" + failed.id }, "Open it")]; }
  else if (proc.length) { eyebrow = "In progress"; title = proc.length === 1 ? `“${proc[0].title || "Your lecture"}” is processing` : `${proc.length} lectures are processing`; body = "You can leave this page. Progress is saved and shows up here when you return."; acts = [h("a", { class: "btn primary", href: "/app?view=library" }, "See progress")]; }
  else if (!S.lectures.length) { eyebrow = "Start here"; title = "Add your first lecture"; body = "Upload a PDF, PowerPoint, MP3 or MP4. Notiv prepares it for reading, asking and practice."; acts = [h("button", { class: "btn primary", type: "button", onclick: addLecture }, icon("upload"), "Add a lecture")]; }
  else if (ready && !S.notebooks.length) { eyebrow = "Next step"; title = "Start a notebook"; body = "A notebook keeps a subject's lectures, notes and study material together."; acts = [h("button", { class: "btn primary", type: "button", onclick: async () => { const nb = await newNotebookDialog(); if (nb) location.assign("/app/notebooks/" + nb.id); } }, icon("book"), "New notebook"), h("a", { class: "btn", href: "/app/lectures/" + ready.id }, "Open a lecture")]; }
  else if (ready) { eyebrow = "Next up"; title = `Study “${ready.title || "your latest lecture"}”`; body = "Open it to read the pages or play the recording, or ask a question and see the source."; acts = [h("a", { class: "btn primary", href: "/app/lectures/" + ready.id }, "Open lecture", icon("arrow")), h("a", { class: "btn", href: "/static/study-companion.html?lecture=" + ready.id }, icon("ask"), "Ask about it")]; }
  else return null;
  return h("section", { class: "next", "aria-labelledby": "next-h" }, h("p", { class: "eyebrow mono", id: "next-h", text: eyebrow }), h("h2", { text: title }), h("p", { text: body }), h("div", { class: "actions" }, ...acts));
}
async function addLecture() {
  const id = await newLectureDialog({ courses: S.courses });
  if (id) location.assign("/app/lectures/" + id);
}

function resume() {
  const nb = S.notebooks[0];
  if (nb) return h("section", { "aria-labelledby": "resume-h" },
    h("div", { class: "resume", style: { "--spine": spine(nb.subject || nb.title) } },
      h("div", null, h("span", { class: "faint", id: "resume-h", text: "Pick up where you left off" }), h("h2", null, h("a", { href: "/app/notebooks/" + nb.id, style: { color: "inherit", textDecoration: "none" }, text: nb.title })),
        h("p", { text: nb.description || [nb.subject, plural(nb.source_count, "lecture") + " linked", plural(nb.item_count, "saved item")].filter(Boolean).join(", ") + ". Updated " + when(nb.updated_at) + "." })),
      h("a", { class: "btn primary", href: "/app/notebooks/" + nb.id }, "Open notebook", icon("arrow"))));
  const l = S.lectures.find(x => x.status === "done");
  if (l) return h("section", { "aria-labelledby": "resume-h" },
    h("div", { class: "resume" }, h("div", null, h("span", { class: "faint", id: "resume-h", text: "Ready to explore" }), h("h2", null, h("a", { href: "/app/lectures/" + l.id, style: { color: "inherit", textDecoration: "none" }, text: l.title })),
      h("p", { text: "Open it to read the transcript beside the slides, ask questions, or start a notebook from it." })),
      h("a", { class: "btn primary", href: "/app/lectures/" + l.id }, "Open lecture", icon("arrow"))));
  return null;
}

// ── Today: the study-place panel (greeting, focus goal ring, streak, week, continue where you left off) ─────────
const SVGNS = "http://www.w3.org/2000/svg";
const sv = (tag, attrs = {}) => { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; };
function firstName() {
  const m = S.me || {}, n = (m.name || m.display_name || "").trim() || (m.email || "").split("@")[0];
  return n ? n.split(/\s+/)[0].replace(/^./, c => c.toUpperCase()) : "";
}
function greeting() { const h = new Date().getHours(); return h < 5 ? "Burning the midnight oil" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; }
function recentLecture() {
  try { const ids = new Map(S.lectures.map(l => [l.id, l])); const r = JSON.parse(localStorage.getItem("notiv.recent.v1") || "[]").find(x => ids.has(x.id)); return r ? { ...r, lec: ids.get(r.id) } : null; }
  catch (_) { return null; }
}
function ring(today, goal) {
  const pct = Math.min(1, today / goal), C = 2 * Math.PI * 42;
  const svg = sv("svg", { viewBox: "0 0 100 100", class: "ring", "aria-hidden": "true" });
  svg.append(sv("circle", { cx: 50, cy: 50, r: 42, class: "ring-track" }),
    Object.assign(sv("circle", { cx: 50, cy: 50, r: 42, class: "ring-fill" + (pct >= 1 ? " done" : ""), "stroke-dasharray": `${C}`, "stroke-dashoffset": `${C * (1 - pct)}` })));
  return svg;
}
function todayPanel() {
  const st = focusStats(), rec = recentLecture(), name = firstName();
  const goalSeg = h("div", { class: "goal-seg", role: "group", "aria-label": "Daily focus goal" }, ...[25, 50, 90, 120].map(m =>
    h("button", { type: "button", "aria-pressed": String(st.goal === m), onclick: () => { setFocusGoal(m); render(); announce(`Daily goal set to ${m} minutes`); } }, `${m}m`)));
  const days = ["S", "M", "T", "W", "T", "F", "S"], max = Math.max(st.goal, ...st.week.map(w => w.min));
  const week = h("ol", { class: "week", "aria-label": "Focus minutes, last 7 days" }, ...st.week.map((w, i) =>
    h("li", { title: `${w.min} min`, class: i === 6 ? "is-today" : "" }, h("span", { class: "bar-v" }, h("i", { style: { height: `${Math.max(4, Math.round(100 * w.min / max))}%` } })),
      h("span", { class: "d", text: days[w.day.getDay()] }), h("span", { class: "sr", text: `${w.min} minutes` }))));
  return h("section", { class: "today", "aria-labelledby": "today-h" },
    h("div", { class: "today-main" },
      h("p", { class: "eyebrow mono", text: new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" }) }),
      h("h2", { id: "today-h", text: `${greeting()}${name ? ", " + name : ""}.` }),
      h("p", { class: "today-sub", text: st.today >= st.goal ? "You've hit today's focus goal. Anything more is a bonus." : st.today ? `${st.goal - st.today} more focused minutes to reach today's goal.` : "Pick something to study and start a focused session." }),
      h("div", { class: "today-acts" },
        h("button", { class: "btn primary", type: "button", onclick: () => window.dispatchEvent(new CustomEvent("notiv:focus")) }, icon("clock"), "Start a 25-minute focus"),
        rec ? h("a", { class: "btn", href: "/app/lectures/" + rec.id }, "Continue “" + (rec.title.length > 34 ? rec.title.slice(0, 33) + "…" : rec.title) + "”", icon("arrow")) : null)),
    h("div", { class: "today-stats" },
      h("div", { class: "ring-wrap" }, ring(st.today, st.goal),
        h("div", { class: "ring-txt" }, h("strong", { text: String(st.today) }), h("span", { text: `of ${st.goal} min` }))),
      h("div", { class: "today-side" },
        st.streak ? h("p", { class: "streak" }, h("b", { text: String(st.streak) }), st.streak === 1 ? "-day streak — keep it going" : "-day streak")
          : h("p", { class: "streak" }, "Finish one focus session to start a streak."),
        week, goalSeg)));
}
window.addEventListener("notiv:focus-logged", () => { if (VIEW !== "library") render(); });

function notebooksSection() {
  const list = S.notebooks.slice(0, 5);
  return h("section", { class: "section", "aria-labelledby": "nb-h" },
    h("div", { class: "section-head" }, h("h2", { id: "nb-h", text: "Notebooks" }), h("div", { class: "tools" }, h("a", { href: "/app/notebooks", text: S.notebooks.length > 5 ? `All ${S.notebooks.length} notebooks` : "Manage notebooks" }))),
    list.length ? h("ul", { class: "ledger" }, ...list.map(nb => h("li", null, h("a", { class: "row-link nb-row", href: "/app/notebooks/" + nb.id, style: { "--spine": spine(nb.subject || nb.title) } },
      h("div", null, h("div", { class: "row-title", text: nb.title }), h("div", { class: "sub" }, nb.subject ? h("span", { text: nb.subject }) : null, h("span", { text: plural(nb.source_count, "lecture") + " linked" }), h("span", { text: plural(nb.item_count, "saved item") }))),
      h("div", { class: "go", text: "Updated " + when(nb.updated_at) })))))
    : h("div", { class: "empty" }, h("h3", { text: "No notebooks yet" }), h("p", { text: "A notebook keeps the lectures for one subject together with your notes, study material and sketches." }),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: async () => { const nb = await newNotebookDialog(); if (nb) location.assign("/app/notebooks/" + nb.id); } }, "Create a notebook"))));
}

function lectureRow(l) {
  const b = bucket(l);
  const prog = l.status === "done" ? stateChip("done")
    : b === "processing" ? h("div", { class: "prog" }, stateChip("processing"), h("div", { class: "bar", role: "progressbar", "aria-label": "Processing progress", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": l.progress_pct || 0 }, h("i", { style: { width: (l.progress_pct || 0) + "%" } })), h("span", { class: "faint", text: (l.stage || "Starting") }))
    : l.status === "failed" ? h("div", { class: "prog" }, stateChip("failed"), h("span", { class: "faint", text: l.error ? String(l.error).replace(/^\w*Error:\s*/, "").slice(0, 90) : "Open it to retry." }))
    : h("div", { class: "prog" }, h("span", { class: "chip plain" }, "No source yet"));
  return h("li", { class: "lec-li", dataset: { lecture: l.id } }, h("a", { class: "row-link lec-row", href: "/app/lectures/" + l.id },
    h("div", null, h("div", { class: "row-title", text: l.title || "Untitled lecture" }), h("div", { class: "meta", text: l.course_name || "No course" })),
    prog, h("div", { class: "dur", text: l.duration ? duration(l.duration) : "" }), h("div", { class: "added", text: when(l.created_at) })),
    l.status === "failed" && l.job_id ? h("button", { class: "btn sm", type: "button", "aria-label": "Retry processing " + (l.title || "lecture"), onclick: e => retryJob(l, e.currentTarget) }, icon("refresh"), "Retry") : null);
}
function lectureList() {
  const q = S.q.trim().toLowerCase();
  const rows = S.lectures.filter(l => (S.filter === "all" || bucket(l) === S.filter) && (!S.course || String(l.course_id) === S.course) && (!q || (l.title + " " + (l.course_name || "")).toLowerCase().includes(q)));
  if (!S.lectures.length) return h("div", { class: "empty" }, h("h3", { text: "Add your first lecture" }),
    h("p", { text: "Upload a recording, slides, or both. Notiv transcribes the audio, places each line on its slide, and keeps every result tied to the moment it came from." }),
    h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: addLecture }, icon("upload"), "Add a lecture")),
    h("ol", { class: "guide" }, ...[["Upload", "A recording and its slides. Large files can take a while to send."], ["Wait a little", "Processing runs on the server. You can leave this page and come back."], ["Explore", "Read, search, ask questions, and study from the lecture."]].map(([t, d]) => h("li", null, h("h3", { text: t }), h("p", { text: d })))));
  if (!rows.length) return h("div", { class: "empty" }, h("h3", { text: "Nothing matches" }), h("p", { text: "No lecture fits this filter or search." }), h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => { S.filter = "all"; S.q = ""; S.course = ""; render(); } }, "Clear filters")));
  return h("ul", { class: "ledger", id: "lec-list" }, ...rows.map(lectureRow));
}
function lecturesSection() {
  const count = k => k === "all" ? S.lectures.length : S.lectures.filter(l => bucket(l) === k).length;
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Filter lectures" }, ...[["all", "All"], ["ready", "Ready"], ["processing", "Processing"], ["attention", "Needs attention"]].map(([k, label]) =>
    h("button", { type: "button", "aria-pressed": S.filter === k, onclick: () => { S.filter = k; render(); } }, label, h("span", { class: "n", text: count(k) }))));
  const search = h("div", { class: "search" }, icon("search"), h("label", { class: "sr", for: "lq", text: "Search lectures" }),
    h("input", { id: "lq", type: "search", placeholder: "Search lectures", value: S.q, oninput: debounce(e => { S.q = e.target.value; const pos = e.target.selectionStart; render(); const i = $("#lq"); i.focus(); i.setSelectionRange(pos, pos); }, 180) }));
  const course = h("select", { "aria-label": "Filter by course", onchange: e => { S.course = e.target.value; render(); } }, h("option", { value: "" }, "All courses"), ...S.courses.map(c => h("option", { value: c.id, selected: String(c.id) === S.course }, c.name)));
  return h("section", { class: "section", "aria-labelledby": "lec-h" },
    h("div", { class: "section-head" }, h("h2", { id: "lec-h", text: "Lectures" }), h("div", { class: "tools" }, h("button", { class: "btn sm", onclick: addLecture }, icon("plus"), "Add a lecture"))),
    S.lectures.length ? h("div", { class: "filters" }, seg, search, S.courses.length ? course : null) : null, lectureList());
}
function coursesSection() {
  return h("section", { class: "section", "aria-labelledby": "co-h" },
    h("div", { class: "section-head" }, h("h2", { id: "co-h", text: "Courses" }), h("div", { class: "tools" }, h("button", { class: "btn sm", onclick: async () => { const c = await newCourseDialog(); if (c) { await refresh(); } } }, icon("plus"), "New course"))),
    S.courses.length ? h("ul", { class: "ledger" }, ...S.courses.map(c => h("li", null, h("a", { class: "row-link course-row", href: "/app/courses/" + c.id },
      h("div", { class: "row-title", text: c.name }), h("span", { class: "faint", text: plural(c.lecture_count, "lecture") }), h("span", { class: "faint", text: c.ready_count + " ready" })))))
    : h("div", { class: "empty" }, h("p", { text: "Courses group lectures so you can search and practise across all of them. Create one when you have two or more lectures on a subject." })));
}

function render() {
  const keepScroll = scrollY;
  if (VIEW === "library") clear(main, head(), lecturesSection(), coursesSection());
  else { const recent = S.lectures.slice(0, 4); clear(main, head(), todayPanel(), nextAction(), notebooksSection(),
    recent.length ? h("section", { class: "section", "aria-labelledby": "rec-h" }, h("div", { class: "section-head" }, h("h2", { id: "rec-h", text: "Recent materials" }), h("div", { class: "tools" }, h("a", { href: "/app?view=library", text: "Open the library" }))), h("ul", { class: "ledger" }, ...recent.map(lectureRow))) : null); }
  scrollTo(0, keepScroll);
  clearTimeout(S.timer);
  if (S.lectures.some(l => bucket(l) === "processing")) S.timer = setTimeout(poll, 4000);
}
async function poll() { try { const lec = await api("/lectures?limit=200"); const before = S.lectures.filter(l => l.status === "done").length; S.lectures = lec.items; render(); if (S.lectures.filter(l => l.status === "done").length > before) announce("A lecture finished processing."); } catch (_) { S.timer = setTimeout(poll, 8000); } }
async function refresh() { try { await load(); render(); } catch (e) { toast(explain(e, "refresh"), "err"); } }

(async function init() {
  main = await mountShell(VIEW === "library" ? "library" : "desk");
  clear(main, h("div", { class: "pagehead" }, h("div", { class: "titles" }, h("h1", { text: VIEW === "library" ? "Library" : "Your study desk" }))), ...skeletons(4));
  try { await load(); render(); } catch (e) { if (e.status !== 401) pageError(main, e, init, "your desk"); }
})();
