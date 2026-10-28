// Dialogs shared by several screens: new lecture (real upload + processing), new course, new notebook, add-to-notebook.
import { h, api, dialog, formDialog, icon, toast, explain, withBusy, announce, clear, notice, plural, stateChip, debounce, ApiError } from "/static/ui/core.js";

const MEDIA_ACCEPT = ".mp3,.mp4,audio/mpeg,video/mp4";
const DECK_ACCEPT = ".pdf,.pptx,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation";
const mb = n => (n / 1048576 >= 10 ? Math.round(n / 1048576) : (n / 1048576).toFixed(1)) + " MB";

export async function newCourseDialog() {
  const v = await formDialog({ title: "New course", intro: "Courses group lectures so you can search and study across them.",
    fields: [{ name: "name", label: "Course name", required: true, max: 200, placeholder: "Deep Learning" }], submit: "Create course" });
  if (!v || !v.name) return null;
  try { const r = await api("/courses", { method: "POST", json: { name: v.name }, headers: { "Idempotency-Key": crypto.randomUUID() } }); toast("Course created."); return { id: r.course_id, name: v.name }; }
  catch (e) { toast(explain(e, "create the course"), "err"); return null; }
}

export async function newNotebookDialog() {
  const v = await formDialog({ title: "New notebook", intro: "A notebook gathers lectures from your library and everything you make from them.",
    fields: [{ name: "title", label: "Title", required: true, max: 200, placeholder: "Optimisation, weeks 1–4" },
      { name: "subject", label: "Subject", max: 120, hint: "Optional. Helps you tell notebooks apart.", placeholder: "Deep Learning" },
      { name: "description", label: "What is it for?", type: "textarea", max: 2000, rows: 3 }], submit: "Create notebook" });
  if (!v || !v.title) return null;
  try { const n = await api("/notebooks", { method: "POST", json: { title: v.title, subject: v.subject, description: v.description } }); toast("Notebook created."); return n; }
  catch (e) { toast(explain(e, "create the notebook"), "err"); return null; }
}

// Creates the lecture, uploads each chosen file, then queues processing. Resolves with the lecture id (or null if cancelled).
export function newLectureDialog({ courses = [], courseId = null } = {}) {
  return new Promise(resolve => {
    const title = h("input", { id: "nl-title", required: true, maxlength: 300, placeholder: "Week 3: Gradient descent", autocomplete: "off" });
    const course = h("select", { id: "nl-course" }, h("option", { value: "" }, "No course"), ...courses.map(c => h("option", { value: c.id, selected: String(c.id) === String(courseId) }, c.name)));
    const media = h("input", { id: "nl-media", type: "file", accept: MEDIA_ACCEPT });
    const deck = h("input", { id: "nl-deck", type: "file", accept: DECK_ACCEPT });
    const status = h("div", { role: "status", "aria-live": "polite" });
    const body = [
      h("div", { class: "field" }, h("label", { for: "nl-title", text: "Lecture title" }), title),
      h("div", { class: "field" }, h("label", { for: "nl-course", text: "Course" }), course),
      h("div", { class: "field" }, h("label", { for: "nl-media", text: "Recording" }), media, h("p", { class: "hint", text: "MP3 (audio) or MP4 (video) with an audio track. Notiv works from uploaded files." })),
      h("div", { class: "field" }, h("label", { for: "nl-deck", text: "Slides" }), deck, h("p", { class: "hint", text: "PDF or PowerPoint (.pptx). Optional, but it lets Notiv match speech to slides." })),
      status];
    let busy = false;
    const d = dialog({ title: "Add a lecture", body, actions: [{ label: "Cancel", id: "cancel", value: "" }, { label: "Upload and process", id: "go", kind: "primary", submit: true, value: "go" }], onClose: () => {} });
    const go = d.form.querySelector("[type=submit]");
    d.form.addEventListener("submit", async e => {
      e.preventDefault();
      if (busy) return;
      if (!d.form.checkValidity()) return d.form.reportValidity();
      const files = [media.files[0], deck.files[0]].filter(Boolean);
      if (!files.length) { clear(status, notice("warn", h("p", { text: "Choose a recording, slides, or both. A lecture needs at least one source." }))); return; }
      busy = true; go.classList.add("busy"); go.disabled = true;
      let lid = null, step = "creating the lecture";
      try {
        const r = await api("/lectures", { method: "POST", json: { title: title.value.trim(), course_id: course.value ? Number(course.value) : null }, headers: { "Idempotency-Key": crypto.randomUUID() } });
        lid = r.lecture_id;
        for (const f of files) {
          step = "uploading " + f.name;
          clear(status, notice("info", h("p", { text: `Uploading ${f.name} (${mb(f.size)}). Keep this window open.` })));
          const form = new FormData(); form.append("file", f, f.name);
          await api(`/lectures/${lid}/assets`, { method: "POST", form });
        }
        step = "starting processing";
        await api(`/lectures/${lid}/process`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } });
        announce("Lecture uploaded. Processing has started.");
        d.el.dataset.lid = lid; d.close("go");
      } catch (err) {
        busy = false; go.classList.remove("busy"); go.disabled = false;
        const msg = err instanceof ApiError && err.status === 422 ? err.message : explain(err, step);
        clear(status, notice("err", h("strong", { text: `Problem ${step}.` }), h("p", { text: msg }),
          lid ? h("p", { class: "faint", text: "The lecture was created. You can add the source again from its page." }) : null));
        if (lid) { d.el.dataset.lid = lid; d.form.querySelector("[data-act=cancel]").textContent = "Open lecture"; d.form.querySelector("[data-act=cancel]").dataset.open = lid; }
      }
    });
    d.form.querySelector("[data-act=cancel]").addEventListener("click", ev => { if (ev.currentTarget.dataset.open) d.el.dataset.lid = ev.currentTarget.dataset.open; });
    d.result.then(() => resolve(d.el.dataset.lid ? Number(d.el.dataset.lid) : null));
  });
}

// Pick one of the person's notebooks (or make one) and link a lecture to it.
export async function addToNotebookDialog({ lectureId, lectureTitle }) {
  let list;
  try { list = (await api("/notebooks")).items; } catch (e) { toast(explain(e, "load your notebooks"), "err"); return null; }
  const box = h("div");
  const pick = async nb => {
    try { await api(`/notebooks/${nb.id}/sources`, { method: "POST", json: { source_type: "lecture", source_id: lectureId, label: lectureTitle || "" } });
      toast(`Linked to “${nb.title}”.`); d.close("ok"); out = nb; }
    catch (e) { toast(explain(e, "link the lecture"), "err"); }
  };
  let out = null;
  const draw = () => clear(box, list.length ? h("ul", { class: "ledger" }, ...list.map(nb => h("li", null,
    h("button", { class: "row-link", style: { width: "100%", background: "none", border: 0, textAlign: "left", cursor: "pointer", gridTemplateColumns: "1fr auto" }, onclick: () => pick(nb) },
      h("span", null, h("span", { class: "row-title", text: nb.title }), h("br"), h("span", { class: "faint", text: [nb.subject, plural(nb.source_count, "source")].filter(Boolean).join(" — ") })),
      h("span", { class: "btn sm" }, "Link here")))))
    : h("div", { class: "empty" }, h("p", { text: "You don't have a notebook yet." })));
  draw();
  const d = dialog({ title: "Add to a notebook", body: [h("p", { class: "muted", text: lectureTitle ? `Link “${lectureTitle}” to one of your notebooks.` : "Link this lecture to a notebook." }), box],
    actions: [{ label: "Close", id: "close", value: "" }, { label: "New notebook", id: "new", onClick: async () => {
      d.close(""); const nb = await newNotebookDialog(); if (nb) { try { await api(`/notebooks/${nb.id}/sources`, { method: "POST", json: { source_type: "lecture", source_id: lectureId, label: lectureTitle || "" } }); toast(`Linked to “${nb.title}”.`); out = nb; } catch (e) { toast(explain(e, "link the lecture"), "err"); } }
      return false; } }] });
  await d.result;
  return out;
}

// Save a piece of lecture material (answer, summary section, concept…) into a notebook as a note, linking the lecture as a source.
export async function saveToNotebookDialog({ lectureId, lectureTitle, title, content, evidenceIds = [], question = null, kind = "lecture" }) {
  let list;
  try { list = (await api("/notebooks")).items; } catch (e) { toast(explain(e, "load your notebooks"), "err"); return null; }
  let saved = null;
  const save = async nb => {
    try {
      await api(`/notebooks/${nb.id}/sources`, { method: "POST", json: { source_type: "lecture", source_id: lectureId, label: lectureTitle || "" } });
      const it = await api(`/notebooks/${nb.id}/items`, { method: "POST", json: { item_type: "note", title: (title || "Saved from lecture").slice(0, 240), content, provenance: [{ source_type: "lecture", source_id: lectureId, evidence_ids: evidenceIds, generated_by: kind, ...(question ? { question } : {}) }] } });
      saved = { nb, item: it }; toast(`Saved to “${nb.title}”.`); d.close("ok");
    } catch (e) { toast(explain(e, "save to the notebook"), "err"); }
  };
  const box = h("div");
  const draw = () => clear(box, list.length ? h("ul", { class: "ledger" }, ...list.map(nb => h("li", null,
    h("button", { type: "button", class: "row-link", style: { width: "100%", background: "none", border: 0, textAlign: "left", cursor: "pointer", gridTemplateColumns: "1fr auto" }, onclick: () => save(nb) },
      h("span", null, h("span", { class: "row-title", text: nb.title }), h("br"), h("span", { class: "faint", text: nb.subject || plural(nb.item_count, "saved item") })), h("span", { class: "btn sm primary" }, "Save here")))))
    : h("p", { class: "faint", text: "You don't have a notebook yet. Create one to save this." }));
  draw();
  const d = dialog({ title: "Save to a notebook", body: [h("p", { class: "muted", text: "This is saved as a note with its source, and the lecture is linked to the notebook." }), box],
    actions: [{ label: "Cancel", id: "cancel", value: "" }, { label: "New notebook", id: "new", onClick: async () => { d.close(""); const nb = await newNotebookDialog(); if (nb) { list = [nb]; await saveAfterCreate(nb); } return false; } }] });
  async function saveAfterCreate(nb) {
    try {
      await api(`/notebooks/${nb.id}/sources`, { method: "POST", json: { source_type: "lecture", source_id: lectureId, label: lectureTitle || "" } });
      const it = await api(`/notebooks/${nb.id}/items`, { method: "POST", json: { item_type: "note", title: (title || "Saved from lecture").slice(0, 240), content, provenance: [{ source_type: "lecture", source_id: lectureId, evidence_ids: evidenceIds, generated_by: kind, ...(question ? { question } : {}) }] } });
      saved = { nb, item: it }; toast(`Saved to “${nb.title}”.`);
    } catch (e) { toast(explain(e, "save to the notebook"), "err"); }
  }
  await d.result;
  return saved;
}
