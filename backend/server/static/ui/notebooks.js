import { $, $$, h, clear, api, mountShell, pageError, skeletons, icon, plural, when, dateLong, spine, stateChip, explain, toast, debounce, announce, dialog, formDialog, confirmDialog, withBusy, download, copyText, notice, menuButton, duration, ApiError } from "/static/ui/core.js";
import { newNotebookDialog, newLectureDialog } from "/static/ui/dialogs.js";
import { renderItemBody, TYPE_LABEL } from "/static/ui/render.js";
import { mountCanvas } from "/static/ui/nb-canvas.js";
import { mountAsk } from "/static/ui/nb-ask.js";

let main;
const match = location.pathname.match(/^\/app\/notebooks\/(\d+)\/?$/);

/* ═══════════════ notebook list ═══════════════ */
async function listPage() {
  const S = { archived: false, q: "" };
  const head = h("div", { class: "pagehead" }, h("div", { class: "titles" }, h("h1", { text: "Notebooks" }), h("p", { class: "lede", text: "One notebook per subject: the lectures, your notes, study material and sketches together." })),
    h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: async () => { const nb = await newNotebookDialog(); if (nb) location.assign("/app/notebooks/" + nb.id); } }, icon("plus"), "New notebook")));
  const results = h("div", { "aria-live": "polite" });
  const q = h("input", { id: "nq", type: "search", placeholder: "Search notebooks", oninput: debounce(e => { S.q = e.target.value; load(); }, 220) });
  const arch = h("button", { class: "btn sm", type: "button", "aria-pressed": "false", onclick: e => { S.archived = !S.archived; e.currentTarget.setAttribute("aria-pressed", S.archived); e.currentTarget.lastChild.textContent = S.archived ? "Hide archived" : "Show archived"; load(); } }, icon("archive"), h("span", { text: "Show archived" }));
  clear(main, head, h("div", { class: "toolbar" }, h("div", { class: "search" }, icon("search"), h("label", { class: "sr", for: "nq", text: "Search notebooks" }), q), arch), results);
  async function load() {
    clear(results, ...skeletons(3));
    try {
      const d = await api(`/notebooks?include_archived=${S.archived}&q=${encodeURIComponent(S.q)}`);
      if (!d.items.length) return clear(results, h("div", { class: "empty" }, h("h3", { text: S.q ? "No notebook matches that search" : S.archived ? "Nothing archived" : "No notebooks yet" }),
        h("p", { text: S.q ? "Try a different word, or clear the search." : "Create one, then link lectures from your library. Notiv keeps study material, notes and sketches next to the sources they came from." }),
        S.q || S.archived ? null : h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: async () => { const nb = await newNotebookDialog(); if (nb) location.assign("/app/notebooks/" + nb.id); } }, "Create a notebook"))));
      clear(results, h("ul", { class: "ledger" }, ...d.items.map(nb => h("li", null, h("a", { class: "row-link nb-row", href: "/app/notebooks/" + nb.id, style: { "--spine": spine(nb.subject || nb.title) } },
        h("div", null, h("div", { class: "row-title", text: nb.title }), h("div", { class: "sub" }, nb.status === "archived" ? h("span", { class: "chip plain" }, "Archived") : null, nb.subject ? h("span", { text: nb.subject }) : null,
          h("span", { text: plural(nb.source_count, "lecture") + " linked" }), h("span", { text: plural(nb.item_count, "saved item") }))),
        h("div", { class: "go", text: "Updated " + when(nb.updated_at) }))))));
    } catch (e) { if (e.status !== 401) clear(results, h("div", { class: "notice err", role: "alert" }, icon("warn"), h("div", null, h("p", { text: explain(e, "load your notebooks") })), h("button", { class: "btn sm", onclick: load }, "Try again"))); }
  }
  load();
}

/* ═══════════════ one notebook ═══════════════ */
const N = { id: 0, nb: null, lectures: null, tab: "overview", itemId: null, teardown: null };
const TABS = [["overview", "Overview"], ["sources", "Sources"], ["material", "Notes and study"], ["ask", "Ask Notiv"], ["canvas", "Canvas"], ["share", "Export and share"]];
const NEEDS = { summary: "Summary", concepts: "Concepts", definitions: "Definitions", flashcards: "Flashcards", quiz: "Quiz", mind_map: "Concept map" };

async function reload() { N.nb = await api("/notebooks/" + N.id); return N.nb; }
const lectureById = id => (N.lectures || []).find(l => l.id === id);
async function ensureLectures() { if (!N.lectures) { try { N.lectures = (await api("/lectures?limit=200")).items; } catch (_) { N.lectures = []; } } return N.lectures; }
const sourceLabel = sid => { const s = N.nb.sources.find(x => x.source_id === sid); return s ? s.label : "Lecture " + sid; };

function header() {
  const nb = N.nb;
  const more = menuButton({ cls: "btn quiet", content: [icon("more", "more"), h("span", { class: "sr", text: "More actions" })], build: close => [
    h("button", { type: "button", onclick: () => { close(); editDetails(); } }, icon("edit"), "Edit details"),
    h("button", { type: "button", onclick: async () => { close(); try { await api("/notebooks/" + nb.id, { method: "PATCH", json: { status: nb.status === "active" ? "archived" : "active" } }); toast(nb.status === "active" ? "Archived. It's hidden from your lists but nothing is deleted." : "Restored."); await reload(); render(); } catch (e) { toast(explain(e, "update the notebook"), "err"); } } }, icon("archive"), nb.status === "active" ? "Archive" : "Restore from archive"),
    h("div", { class: "sep" }),
    h("button", { type: "button", onclick: async () => { close(); const ok = await confirmDialog({ title: "Delete this notebook?", body: `“${nb.title}” and its notes, study material, canvas and share links will be permanently deleted. Your lectures are not affected.`, confirm: "Delete notebook", danger: true }); if (!ok) return;
      try { await api("/notebooks/" + nb.id, { method: "DELETE" }); location.assign("/app/notebooks"); } catch (e) { toast(explain(e, "delete the notebook"), "err"); } } }, icon("trash"), "Delete notebook")] });
  return h("div", { class: "nb-head", style: { "--spine": spine(nb.subject || nb.title) } },
    h("div", { class: "crumbs" }, h("a", { href: "/app/notebooks", text: "Notebooks" }), h("span", { class: "sep", text: "/" }), h("span", { text: nb.subject || "Notebook" })),
    h("div", { class: "spine" }),
    h("div", { class: "pagehead", style: { margin: 0 } }, h("div", { class: "titles" }, h("h1", { id: "nb-title", text: nb.title }), nb.status === "archived" ? h("p", null, h("span", { class: "chip plain" }, "Archived")) : null,
      nb.description ? h("p", { class: "desc", text: nb.description }) : h("p", { class: "desc faint", text: "No description yet." }),
      h("div", { class: "kv" }, h("span", null, h("b", { text: nb.sources.length }), " ", nb.sources.length === 1 ? "lecture" : "lectures", " linked"), h("span", null, h("b", { text: nb.items.length }), " saved ", nb.items.length === 1 ? "item" : "items"), h("span", { text: "Updated " + when(nb.updated_at) }))),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => go("share") }, icon("share"), "Share or export"), more)));
}
async function editDetails() {
  const nb = N.nb;
  const v = await formDialog({ title: "Edit notebook", fields: [{ name: "title", label: "Title", value: nb.title, required: true, max: 200 }, { name: "subject", label: "Subject", value: nb.subject, max: 120 }, { name: "description", label: "Description", type: "textarea", value: nb.description, max: 2000, rows: 3 }], submit: "Save changes" });
  if (!v) return;
  try { await api("/notebooks/" + nb.id, { method: "PATCH", json: { title: v.title, subject: v.subject, description: v.description } }); await reload(); render(); toast("Saved."); document.title = nb.title + " — Notiv"; }
  catch (e) { toast(explain(e, "save the details"), "err"); }
}

function parseHash() {
  const [t, id] = location.hash.slice(1).split("/");
  N.tab = TABS.some(x => x[0] === t) ? t : "overview";
  N.itemId = N.tab === "material" && /^\d+$/.test(id || "") ? Number(id) : null;
}
function go(tab, id) { location.hash = tab + (id ? "/" + id : ""); }

function render() {
  if (N.teardown) { N.teardown(); N.teardown = null; }
  parseHash();
  document.title = N.nb.title + " — Notiv";
  const tabs = h("div", { class: "tabs", role: "tablist", "aria-label": "Notebook sections" }, ...TABS.map(([id, label]) => {
    const n = id === "sources" ? N.nb.sources.length : id === "material" ? N.nb.items.length : null;
    return h("button", { class: "tab", role: "tab", id: "tab-" + id, "aria-selected": N.tab === id, "aria-controls": "panel", tabindex: N.tab === id ? 0 : -1, onclick: () => go(id),
      onkeydown: e => { const i = TABS.findIndex(x => x[0] === N.tab); let j = i; if (e.key === "ArrowRight") j = (i + 1) % TABS.length; else if (e.key === "ArrowLeft") j = (i - 1 + TABS.length) % TABS.length; else return; e.preventDefault(); go(TABS[j][0]); setTimeout(() => $("#tab-" + TABS[j][0]).focus(), 0); } },
      label, n != null ? h("span", { class: "n", text: n }) : null);
  }));
  const panel = h("div", { id: "panel", class: "panel", role: "tabpanel", "aria-labelledby": "tab-" + N.tab, tabindex: "-1" });
  clear(main, header(), tabs, panel);
  ({ overview, sources, material, ask: askTab, canvas: canvasTab, share })[N.tab](panel);
}

/* ── overview ── */
function overview(p) {
  const nb = N.nb, next = !nb.sources.length ? "link" : !nb.items.length ? "generate" : null;
  const kids = [];
  if (next === "link") kids.push(h("div", { class: "empty" }, h("h2", { text: "Start by linking a lecture" }), h("p", { text: "Everything in a notebook is built from lectures in your library. Link one and you can ask it questions, generate study material, and write notes against it." }),
    h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: () => { go("sources"); setTimeout(linkDialog, 50); } }, icon("link"), "Link a lecture"), h("a", { class: "btn", href: "/app" }, "Add a new lecture on your desk"))));
  else if (next === "generate") kids.push(h("div", { class: "empty" }, h("h2", { text: "Now make something from it" }), h("p", { text: "Generate a summary, concepts, flashcards or a quiz from a linked lecture. Each one is built from the lecture's own evidence and cites it. Or just start a note." }),
    h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: () => generateDialog() }, icon("study"), "Generate study material"), h("button", { class: "btn", onclick: () => newNote() }, icon("note"), "Write a note"))));
  const src = h("section", { "aria-labelledby": "ov-src" }, h("div", { class: "section-head" }, h("h2", { id: "ov-src", text: "Sources" }), h("div", { class: "tools" }, h("button", { class: "btn sm quiet", onclick: () => go("sources") }, "Manage"))),
    nb.sources.length ? h("ul", { class: "ledger" }, ...nb.sources.map(s => h("li", null, h("a", { class: "row-link", href: "/app/lectures/" + s.source_id }, h("span", { class: "row-title", text: s.label || "Lecture " + s.source_id })))))
      : h("p", { class: "faint", style: { paddingTop: "12px" }, text: "No lectures linked yet." }));
  const recent = h("section", { "aria-labelledby": "ov-rec" }, h("div", { class: "section-head" }, h("h2", { id: "ov-rec", text: "Recently saved" }), h("div", { class: "tools" }, h("button", { class: "btn sm quiet", onclick: () => go("material") }, "See all"))),
    nb.items.length ? h("ul", { class: "ledger" }, ...nb.items.slice(0, 5).map(it => h("li", null, h("a", { class: "row-link", href: "#material/" + it.id }, h("span", null, h("span", { class: "row-title", text: it.title }), h("br"), h("span", { class: "faint", text: (TYPE_LABEL[it.item_type] || it.item_type) + ", " + when(it.updated_at) }))))))
      : h("p", { class: "faint", style: { paddingTop: "12px" }, text: "Notes and study material you save will appear here." }));
  const quick = h("div", { class: "actions", style: { display: "flex", gap: "10px", flexWrap: "wrap", margin: "0 0 var(--s5)" } },
    h("button", { class: "btn", onclick: () => newNote() }, icon("note"), "New note"), h("button", { class: "btn", onclick: () => generateDialog(), disabled: !nb.sources.length }, icon("study"), "Generate study material"),
    h("button", { class: "btn", onclick: () => go("ask"), disabled: !nb.sources.length }, icon("ask"), "Ask a question"), h("button", { class: "btn", onclick: () => go("canvas") }, icon("canvas"), "Open the canvas"));
  clear(p, ...kids, nb.sources.length ? quick : null, h("div", { class: "two" }, recent, src));
}

/* ── sources ── */
async function sources(p) {
  clear(p, ...skeletons(2));
  const lecs = await ensureLectures();
  const nb = N.nb;
  const head = h("div", { class: "section-head" }, h("h2", { text: "Linked lectures" }), h("div", { class: "tools" }, h("button", { class: "btn primary sm", onclick: linkDialog }, icon("link"), "Link a lecture")));
  const list = nb.sources.length ? h("ul", { class: "ledger" }, ...nb.sources.map(s => {
    const l = lectureById(s.source_id);
    return h("li", null, h("div", { class: "row-link src-row" }, h("div", null, h("div", { class: "row-title", text: s.label || "Lecture " + s.source_id }),
      h("div", { class: "kv", style: { marginTop: "4px" } }, l ? stateChip(l.status) : h("span", { class: "chip err" }, "Lecture removed"), l && l.course_name ? h("span", { text: l.course_name }) : null, l && l.duration ? h("span", { text: duration(l.duration) }) : null, h("span", { text: "Linked " + when(s.created_at) }))),
      h("div", { class: "acts" }, l ? h("a", { class: "btn sm", href: "/app/lectures/" + s.source_id }, "Open lecture") : null,
        h("button", { class: "btn sm danger", onclick: async () => { const ok = await confirmDialog({ title: "Unlink this lecture?", body: "The lecture stays in your library. Notes and study material you already saved from it stay in this notebook.", confirm: "Unlink", danger: true }); if (!ok) return;
          try { await api(`/notebooks/${nb.id}/sources/${s.id}`, { method: "DELETE" }); await reload(); render(); toast("Unlinked."); } catch (e) { toast(explain(e, "unlink"), "err"); } } }, "Unlink"))));
  })) : h("div", { class: "empty" }, h("h3", { text: "No lectures linked" }), h("p", { text: "Link a lecture from your library to ask it questions and build study material from it." }), h("div", { class: "actions" }, h("button", { class: "btn primary", onclick: linkDialog }, "Link a lecture")));
  clear(p, head, list);
}
async function linkDialog() {
  const lecs = await ensureLectures();
  const linked = new Set(N.nb.sources.map(s => s.source_id));
  const q = h("input", { type: "search", placeholder: "Search your lectures", "aria-label": "Search your lectures" });
  const box = h("div");
  const draw = () => {
    const t = q.value.trim().toLowerCase();
    const rows = lecs.filter(l => !t || (l.title + " " + (l.course_name || "")).toLowerCase().includes(t));
    clear(box, rows.length ? h("ul", { class: "ledger" }, ...rows.map(l => h("li", null, h("div", { class: "row-link src-row" }, h("div", null, h("div", { class: "row-title", text: l.title }), h("div", { class: "kv", style: { marginTop: "3px" } }, stateChip(l.status), l.course_name ? h("span", { text: l.course_name }) : null)),
      linked.has(l.id) ? h("span", { class: "faint", text: "Already linked" }) : h("button", { class: "btn sm primary", type: "button", onclick: async e => { await withBusy(e.currentTarget, async () => {
        try { await api(`/notebooks/${N.id}/sources`, { method: "POST", json: { source_type: "lecture", source_id: l.id, label: l.title } }); linked.add(l.id); await reload(); render(); toast("Linked. " + (l.status === "done" ? "" : "It will be usable once processing finishes.")); draw(); }
        catch (x) { toast(explain(x, "link the lecture"), "err"); } }); } }, "Link"))))) : h("p", { class: "faint", text: lecs.length ? "No lecture matches." : "Your library is empty. Add a lecture first." }));
  };
  q.addEventListener("input", draw); draw();
  const d = dialog({ title: "Link a lecture", wide: true, body: [h("div", { class: "field" }, q), box], actions: [{ label: "Done", id: "done", value: "done", kind: "primary" },
    { label: "Add a new lecture", id: "new", onClick: async () => { d.close(""); const id = await newLectureDialog({ courses: (await api("/courses?limit=200")).items }); if (id) { try { await api(`/notebooks/${N.id}/sources`, { method: "POST", json: { source_type: "lecture", source_id: id } }); N.lectures = null; await reload(); render(); toast("Uploaded and linked. Processing has started."); } catch (e) { toast(explain(e, "link the new lecture"), "err"); } } return false; } }] });
}

/* ── notes and study material ── */
const TYPE_FILTERS = [["all", "All"], ["note", "Notes"], ["summary", "Summaries"], ["concepts", "Concepts"], ["definitions", "Definitions"], ["flashcards", "Flashcards"], ["quiz", "Quizzes"], ["mind_map", "Maps"]];
const MF = { type: "all", q: "" };
function material(p) {
  if (N.itemId) return itemView(p, N.nb.items.find(i => i.id === N.itemId));
  const nb = N.nb;
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Filter by type" }, ...TYPE_FILTERS.map(([k, l]) => h("button", { type: "button", "aria-pressed": MF.type === k, onclick: () => { MF.type = k; material(p); } }, l)));
  const q = h("input", { id: "iq", type: "search", placeholder: "Search saved items", value: MF.q, oninput: debounce(e => { MF.q = e.target.value; const pos = e.target.selectionStart; material(p); const i = $("#iq"); i.focus(); i.setSelectionRange(pos, pos); }, 200) });
  const t = MF.q.trim().toLowerCase();
  const items = nb.items.filter(i => (MF.type === "all" || i.item_type === MF.type) && (!t || (i.title + " " + (typeof i.content === "string" ? i.content : JSON.stringify(i.content))).toLowerCase().includes(t)));
  clear(p, h("div", { class: "section-head" }, h("h2", { text: "Notes and study material" }), h("div", { class: "tools" }, h("button", { class: "btn sm", onclick: () => newNote() }, icon("note"), "New note"), h("button", { class: "btn sm primary", onclick: () => generateDialog() }, icon("study"), "Generate"))),
    nb.items.length ? h("div", { class: "toolbar" }, seg, h("div", { class: "search" }, icon("search"), h("label", { class: "sr", for: "iq", text: "Search saved items" }), q)) : null,
    items.length ? h("ul", { class: "ledger" }, ...items.map(it => { const prov = (it.provenance || []).find(x => x.source_id); return h("li", null, h("a", { class: "row-link item-row", href: "#material/" + it.id }, h("div", null, h("div", { class: "row-title", text: it.title }),
      h("div", { class: "sub" }, h("span", { class: "type-tag", text: TYPE_LABEL[it.item_type] || it.item_type }), prov ? h("span", { text: "from " + sourceLabel(prov.source_id) }) : null, h("span", { text: "Saved " + when(it.updated_at) }))), h("span", { class: "go faint", text: "Open" }))); }))
    : h("div", { class: "empty" }, h("h3", { text: nb.items.length ? "Nothing matches" : "Nothing saved yet" }),
      h("p", { text: nb.items.length ? "Clear the search or pick another type." : "Write a note, or generate a summary, concepts, flashcards or a quiz from a linked lecture. Each one stays in this notebook and cites its source." }),
      nb.items.length ? h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => { MF.type = "all"; MF.q = ""; material(p); } }, "Clear filters"))
        : h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => newNote() }, "Write a note"), h("button", { class: "btn primary", disabled: !nb.sources.length, onclick: () => generateDialog() }, "Generate study material"), !nb.sources.length ? h("span", { class: "faint", text: "Link a lecture first to generate." }) : null)));
}
async function newNote() {
  const v = await formDialog({ title: "New note", fields: [{ name: "title", label: "Title", required: true, max: 240, placeholder: "Why momentum overshoots" }], submit: "Start writing" });
  if (!v) return;
  try { const it = await api(`/notebooks/${N.id}/items`, { method: "POST", json: { item_type: "note", title: v.title, content: "", provenance: [] } }); await reload(); go("material", it.id); }
  catch (e) { toast(explain(e, "create the note"), "err"); }
}
function generateDialog(pre = {}) {
  const nb = N.nb;
  if (!nb.sources.length) { toast("Link a lecture first.", "err"); return; }
  const src = h("select", { id: "g-src" }, ...nb.sources.map(s => { const l = lectureById(s.source_id); const ok = !l || l.status === "done"; return h("option", { value: s.source_id, disabled: l && l.status !== "done", selected: String(pre.source) === String(s.source_id) }, (s.label || "Lecture " + s.source_id) + (l && l.status !== "done" ? " (not processed yet)" : "")); }));
  const type = h("select", { id: "g-type" }, ...Object.entries(NEEDS).map(([v, l]) => h("option", { value: v, selected: pre.type === v }, l)));
  const hint = h("p", { class: "hint", text: "" });
  const hints = { summary: "An overview, key takeaways and section summaries written from the lecture, each citing its pages.", concepts: "Key concepts with what your notes say, plus a labelled supplementary explanation.", definitions: "Definitions supported by your notes, with their pages.", flashcards: "Questions that test understanding, each linked to its source.", quiz: "Multiple-choice questions with explanations and the supporting evidence.", mind_map: "Topics and concepts with labelled connections you can place on the canvas." };
  const setHint = () => { hint.textContent = hints[type.value]; }; type.addEventListener("change", setHint); setHint();
  const status = h("div", { role: "status", "aria-live": "polite" });
  const d = dialog({ title: "Generate study material", body: [h("p", { class: "muted", text: "Notiv writes this from the lecture's own transcript and slides and cites the pages it used. Anything not from your notes is labelled as a supplementary explanation." }),
    h("div", { class: "field" }, h("label", { for: "g-src", text: "From lecture" }), src), h("div", { class: "field" }, h("label", { for: "g-type", text: "What to make" }), type, hint), status],
    actions: [{ label: "Cancel", id: "cancel", value: "" }, { label: "Generate", id: "go", kind: "primary", onClick: async (e) => {
      const btn = e.currentTarget;
      return await withBusy(btn, async () => {
        clear(status, notice("info", h("p", { text: "Working from the lecture evidence. This can take a few seconds." })));
        try { const it = await api(`/notebooks/${N.id}/generate`, { method: "POST", json: { source_id: Number(src.value), output_type: type.value, title: `${NEEDS[type.value]}: ${(nb.sources.find(s => String(s.source_id) === src.value) || {}).label || "Lecture " + src.value}`.slice(0, 240) } }); await reload(); d.close("ok"); go("material", it.id); toast("Saved to this notebook."); return true; }
        catch (x) { clear(status, notice("err", h("p", { text: x.status === 409 ? "That lecture is still processing. Try again once it shows as Ready." : explain(x, "generate this") }))); return false; }
      });
    } }] });
}

function itemView(p, item) {
  if (!item) { clear(p, h("div", { class: "empty" }, h("h3", { text: "That item no longer exists" }), h("p", { text: "It may have been deleted in another tab." }), h("div", { class: "actions" }, h("a", { class: "btn", href: "#material" }, "Back to notes and study material")))); return; }
  const isNote = item.item_type === "note";
  const status = h("div", { class: "status", role: "status", "aria-live": "polite", text: isNote ? "Saved" : "" });
  const view = h("div", { class: "item-view" });
  const exportMenu = menuButton({ cls: "btn sm", content: [icon("download"), "Export"], build: close => ["md", "txt", "csv", "json"].map(f => h("button", { type: "button", onclick: async () => { close(); try { await download(`/notebooks/${N.id}/items/${item.id}/export?format=${f}`, "notiv-item." + f); toast("Downloaded."); } catch (e) { toast(explain(e, "export"), "err"); } } }, { md: "Markdown (.md)", txt: "Plain text (.txt)", csv: "CSV (.csv)", json: "JSON (.json)" }[f])) });
  const bar = h("div", { class: "bar-top" }, h("a", { class: "btn sm quiet", href: "#material" }, icon("back"), "All notes and study material"), h("span", { class: "spacer", style: { flex: 1 } }), exportMenu,
    h("button", { class: "btn sm", onclick: async () => { try { const c = await api(`/notebooks/${N.id}/items`, { method: "POST", json: { item_type: item.item_type, title: (item.title + " (copy)").slice(0, 240), content: item.content, provenance: item.provenance || [] } }); await reload(); go("material", c.id); toast("Duplicated."); } catch (e) { toast(explain(e, "duplicate"), "err"); } } }, icon("copy"), "Duplicate"),
    h("button", { class: "btn sm danger", onclick: async () => { const ok = await confirmDialog({ title: "Delete this item?", body: `“${item.title}” will be removed from the notebook.`, confirm: "Delete", danger: true }); if (!ok) return; try { await api(`/notebooks/${N.id}/items/${item.id}`, { method: "DELETE" }); await reload(); go("material"); toast("Deleted."); } catch (e) { toast(explain(e, "delete"), "err"); } } }, icon("trash"), "Delete"));
  const titleRow = h("div", null, h("span", { class: "type-tag", text: TYPE_LABEL[item.item_type] || item.item_type }), h("h2", { text: item.title }),
    h("div", { class: "kv" }, h("span", { text: "Saved " + dateLong(item.updated_at) }), h("span", { text: "Version " + item.version }), h("button", { class: "btn sm quiet", onclick: async () => { const v = await formDialog({ title: "Rename", fields: [{ name: "title", label: "Title", value: item.title, required: true, max: 240 }], submit: "Rename" }); if (!v) return;
      try { await api(`/notebooks/${N.id}/items/${item.id}`, { method: "PUT", json: { item_type: item.item_type, title: v.title, content: item.content, provenance: item.provenance || [], expected_version: item.version } }); await reload(); render(); } catch (e) { toast(e.status === 409 ? "This item changed in another tab. Reload to see the latest version." : explain(e, "rename"), "err"); } } }, icon("edit"), "Rename")));
  let body;
  if (isNote) {
    const ta = h("textarea", { class: "editor", "aria-label": "Note text", maxlength: 200000, placeholder: "Write here. Use # for headings, - for bullets, **bold** and *italic*." }); ta.value = typeof item.content === "string" ? item.content : "";
    let version = item.version, timer = null, saving = false, dirty = false, conflict = false;
    const save = async () => {
      if (saving || conflict) return; if (!dirty) return;
      saving = true; dirty = false; status.className = "status"; status.textContent = "Saving…";
      try { const r = await api(`/notebooks/${N.id}/items/${item.id}`, { method: "PUT", json: { item_type: "note", title: item.title, content: ta.value, provenance: item.provenance || [], expected_version: version } });
        version = r.version; item.version = version; item.content = ta.value; const i = N.nb.items.findIndex(x => x.id === item.id); if (i >= 0) N.nb.items[i] = r; status.textContent = "Saved " + new Date().toLocaleTimeString(); }
      catch (e) { dirty = true; if (e.status === 409) { conflict = true; status.className = "status err"; clear(status, "This note changed in another tab, so your latest edits weren't saved. ", h("button", { class: "btn sm", onclick: async () => { await reload(); render(); } }, "Load the newer version")); }
        else { status.className = "status err"; status.textContent = "Not saved: " + explain(e, "save the note") + " Retrying when you type."; } }
      finally { saving = false; if (dirty && !conflict) schedule(); }
    };
    const schedule = () => { clearTimeout(timer); timer = setTimeout(save, 900); };
    ta.addEventListener("input", () => { dirty = true; status.className = "status"; status.textContent = "Unsaved changes"; schedule(); });
    const flush = () => { if (dirty && !conflict) { clearTimeout(timer); save(); } };
    window.addEventListener("beforeunload", beforeUnload); function beforeUnload(e) { if (dirty && !conflict) { e.preventDefault(); e.returnValue = ""; flush(); } }
    N.teardown = () => { window.removeEventListener("beforeunload", beforeUnload); flush(); };
    const preview = h("details", { style: { marginTop: "16px" } }, h("summary", { text: "Preview formatting" }), h("div", { id: "np" }));
    preview.addEventListener("toggle", () => { if (preview.open) clear($("#np"), renderItemBody({ item_type: "note", content: ta.value })); });
    body = [ta, status, preview];
  } else body = [renderItemBody(item, { onPlaceMap: (nodes, edges) => placeOnCanvas(nodes, edges) }),
    (item.provenance || []).length ? h("p", { class: "faint", style: { marginTop: "22px" }, text: "Built from " + [...new Set(item.provenance.map(x => x.source_id && sourceLabel(x.source_id)).filter(Boolean))].join(", ") + "." }) : null];
  clear(view, bar, titleRow, h("div", { style: { marginTop: "var(--s5)" } }, ...body));
  clear(p, view);
  setTimeout(() => { if (isNote) $(".editor").focus({ preventScroll: true }); }, 0);
}
async function placeOnCanvas(nodes, edges) {
  const cv = N.nb.canvas || { nodes: [], edges: [] };
  const existing = new Set((cv.nodes || []).map(n => n.id));
  const idMap = {}; const add = [];
  const baseY = (cv.nodes || []).reduce((m, n) => Math.max(m, (+n.y || 0) + 120), 30);
  nodes.slice(0, 60).forEach((n, i) => { let id = String(n.id); while (existing.has(id)) id += "-" + Math.random().toString(36).slice(2, 5); existing.add(id); idMap[n.id] = id; add.push({ id, text: String(n.text || "").slice(0, 400), x: +n.x || 30 + (i % 3) * 250, y: baseY + (+n.y || Math.floor(i / 3) * 120) - 30 }); });
  const newEdges = (edges || []).filter(e => idMap[e.from] && idMap[e.to]).map(e => ({ from: idMap[e.from], to: idMap[e.to], ...(e.label ? { label: String(e.label).slice(0, 60) } : {}) }));
  try { await api(`/notebooks/${N.id}/canvas`, { method: "PUT", json: { state: { nodes: [...(cv.nodes || []), ...add], edges: [...(cv.edges || []), ...newEdges] }, expected_version: N.nb.canvas_version || undefined } }); await reload(); toast(`Placed ${add.length} ideas on the canvas.`); go("canvas"); }
  catch (e) { toast(explain(e, "place the map on the canvas"), "err"); }
}

/* ── ask ── */
async function askTab(p) { await ensureLectures(); N.teardown = mountAsk(p, { nb: N.nb, lectures: N.lectures, onSaved: async it => { await reload(); toast("Saved as a note.", "ok"); return it; }, openItem: id => go("material", id) }); }
/* ── canvas ── */
function canvasTab(p) { N.teardown = mountCanvas(p, { nb: N.nb, reload, openItemPicker: () => N.nb.items.filter(i => i.item_type === "mind_map") }); }

/* ── export and share ── */
const FORMATS = [["md", "Markdown", "Headings and sources, readable anywhere"], ["docx", "Word", "An editable .docx"], ["pdf", "PDF", "For reading and printing"], ["pptx", "PowerPoint", "One slide per item"], ["txt", "Plain text", "No formatting"], ["csv", "CSV", "Items and cards as rows"], ["json", "JSON", "Everything, machine-readable"], ["svg", "Canvas as SVG", "The brainstorm canvas as a picture"]];
async function share(p) {
  const nb = N.nb;
  const fm = h("div", { class: "formats" }, ...FORMATS.map(([f, l, d]) => h("button", { class: "format", type: "button", onclick: async e => { const b = e.currentTarget; b.disabled = true; try { const name = await download(`/notebooks/${nb.id}/export?format=${f}`, "notiv-notebook." + f); toast("Downloaded " + name + "."); } catch (x) { toast(explain(x, "export"), "err"); } finally { b.disabled = false; } } }, h("b", { text: l }), h("span", { text: d }))));
  const result = h("div", { "aria-live": "polite" });
  const list = h("div");
  const exp = h("select", { id: "sh-exp" }, ...[["", "Never expires"], ["86400", "1 day"], ["604800", "7 days"], ["2592000", "30 days"]].map(([v, l]) => h("option", { value: v }, l)));
  const make = h("button", { class: "btn primary", onclick: async e => { await withBusy(e.currentTarget, async () => {
    try { const r = await api(`/notebooks/${nb.id}/shares`, { method: "POST", json: { permission: "read", expires_in_seconds: exp.value ? Number(exp.value) : null } });
      const url = location.origin + "/share/" + r.token;
      clear(result, h("div", { class: "notice ok", role: "status" }, icon("check"), h("div", null, h("strong", { text: "Link created. Copy it now." }), h("p", { text: "For your security Notiv stores only a fingerprint of the link, so it can't be shown again. If you lose it, revoke it and make a new one." }),
        h("div", { class: "share-box" }, h("input", { id: "share-url", readonly: true, value: url, "aria-label": "Share link", onfocus: e => e.target.select() }), h("button", { class: "btn sm", type: "button", onclick: async () => { await copyText(url); toast("Link copied."); } }, icon("copy"), "Copy")))));
      loadShares(); }
    catch (x) { toast(explain(x, "create the link"), "err"); } }); } }, icon("link"), "Create read-only link");
  async function loadShares() {
    try { const d = (await api(`/notebooks/${nb.id}/shares`)).items; const now = Date.now() / 1000;
      clear(list, d.length ? h("ul", { class: "ledger" }, ...d.map(s => { const st = s.revoked_at ? "revoked" : s.expires_at && s.expires_at < now ? "expired" : "active";
        return h("li", null, h("div", { class: "row-link src-row" }, h("div", null, h("div", { class: "kv" }, h("span", { class: "chip " + (st === "active" ? "ok" : "plain") }, st === "active" ? "Active" : st === "expired" ? "Expired" : "Revoked"), h("span", { text: "Read-only" }), h("span", { text: "Created " + dateLong(s.created_at) }), h("span", { text: s.expires_at ? (st === "expired" ? "Expired " : "Expires ") + dateLong(s.expires_at) : "No expiry" }))),
          st === "active" ? h("button", { class: "btn sm danger", onclick: async () => { const ok = await confirmDialog({ title: "Revoke this link?", body: "Anyone using it will immediately lose access.", confirm: "Revoke link", danger: true }); if (!ok) return; try { await api(`/notebooks/${nb.id}/shares/${s.id}`, { method: "DELETE" }); toast("Link revoked."); loadShares(); } catch (x) { toast(explain(x, "revoke"), "err"); } } }, "Revoke") : null)); })) : h("p", { class: "faint", text: "No links yet." })); }
    catch (x) { clear(list, notice("err", h("p", { text: explain(x, "load your links") }))); }
  }
  clear(p, h("div", { class: "two" }, h("section", { "aria-labelledby": "ex-h" }, h("div", { class: "section-head" }, h("h2", { id: "ex-h", text: "Export" })), h("p", { class: "muted", style: { paddingTop: "12px" }, text: "Download this whole notebook: its notes, study material, sources and (for SVG) the canvas." }), fm),
    h("section", { "aria-labelledby": "sh-h" }, h("div", { class: "section-head" }, h("h2", { id: "sh-h", text: "Share" })),
      h("p", { class: "muted", style: { paddingTop: "12px" }, text: "A link shows this notebook's notes, study material and canvas, read-only. It never exposes your lectures, recordings or personal lecture notes." }),
      h("div", { class: "field" }, h("label", { for: "sh-exp", text: "Link lasts" }), exp), make, result, h("h3", { style: { margin: "28px 0 6px" }, text: "Your links" }), list)));
  loadShares();
}

/* ═══════════════ boot ═══════════════ */
(async function init() {
  main = await mountShell("notebooks");
  if (!match) { clear(main, h("div", { class: "pagehead" }, h("div", { class: "titles" }, h("h1", { text: "Notebooks" }))), ...skeletons(3)); return listPage(); }
  N.id = Number(match[1]);
  clear(main, ...skeletons(4));
  try { await reload(); render(); window.addEventListener("hashchange", () => { if (N.nb) { render(); } }); }
  catch (e) { if (e.status !== 401) pageError(main, e, init, "this notebook"); }
})();
