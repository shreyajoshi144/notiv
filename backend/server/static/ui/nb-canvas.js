// Brainstorm canvas: drag ideas, edit them, connect any two, delete, undo, autosave with version checks.
import { $, h, clear, api, icon, toast, explain, notice, download, announce, dialog } from "/static/ui/core.js";

const W = 220;
export function mountCanvas(p, { nb, reload, openItemPicker }) {
  let state = JSON.parse(JSON.stringify(nb.canvas || { nodes: [], edges: [] }));
  state.nodes ||= []; state.edges ||= [];
  let version = nb.canvas_version || 0, selNode = null, selEdge = null, linkFrom = null, editing = null, dirty = false, saving = false, conflict = false, timer = null, drag = null;
  const undo = [];
  const status = h("span", { class: "status", role: "status", "aria-live": "polite", text: state.nodes.length ? "Saved" : "" });
  const board = h("div", { class: "board" });
  const svgNS = "http://www.w3.org/2000/svg";
  const edgesSvg = document.createElementNS(svgNS, "svg"); edgesSvg.setAttribute("class", "edges"); edgesSvg.setAttribute("aria-hidden", "true"); board.append(edgesSvg);
  const wrap = h("div", { class: "board-wrap", tabindex: "0", role: "application", "aria-label": "Brainstorm canvas. Use Tab to move between ideas. Arrow keys move the focused idea, Enter edits it, Delete removes it, L starts a connection." }, board);
  const hint = h("p", { class: "cv-hint" });
  const connectBtn = h("button", { class: "btn sm", type: "button", "aria-pressed": "false", onclick: () => toggleLink(selNode) }, icon("link"), "Connect two ideas");
  const undoBtn = h("button", { class: "btn sm quiet", type: "button", disabled: true, onclick: doUndo }, icon("back"), "Undo");

  clear(p, h("div", { class: "section-head" }, h("h2", { text: "Brainstorm canvas" }), h("div", { class: "tools" }, status)),
    h("div", { class: "cv-tools", style: { paddingTop: "14px" } },
      h("button", { class: "btn sm primary", type: "button", onclick: () => addNode() }, icon("plus"), "Add an idea"), connectBtn, undoBtn,
      h("button", { class: "btn sm", type: "button", onclick: importMap }, icon("canvas"), "Import a concept map"),
      h("span", { class: "grow" }),
      h("button", { class: "btn sm", type: "button", onclick: async e => { if (dirty) await save(); try { await download(`/notebooks/${nb.id}/export?format=svg`, "notiv-canvas.svg"); toast("Downloaded."); } catch (x) { toast(explain(x, "export the canvas"), "err"); } } }, icon("download"), "Export SVG")),
    h("div", { id: "cv-conflict" }), wrap, hint);

  const pushUndo = () => { undo.push(JSON.stringify(state)); if (undo.length > 30) undo.shift(); undoBtn.disabled = false; };
  function doUndo() { const s = undo.pop(); if (!s) return; state = JSON.parse(s); selNode = selEdge = null; undoBtn.disabled = !undo.length; changed(false); draw(); }
  function setHint() {
    hint.textContent = linkFrom ? "Now click the idea to connect to. Press Escape to cancel." : selEdge != null ? "Connection selected. Press Delete to remove it." : state.nodes.length ? "Drag an idea by its top bar. Double-click to edit. Use “Connect two ideas”, or press L on a selected idea." : "";
    connectBtn.setAttribute("aria-pressed", String(!!linkFrom)); connectBtn.classList.toggle("primary", !!linkFrom);
  }
  function changed(pushed = true) { dirty = true; conflict ? null : (status.className = "status", status.textContent = "Unsaved changes"); clearTimeout(timer); timer = setTimeout(save, 1200); }
  async function save() {
    if (saving || !dirty || conflict) return;
    saving = true; dirty = false; status.className = "status"; status.textContent = "Saving…";
    try { const r = await api(`/notebooks/${nb.id}/canvas`, { method: "PUT", json: { state: { nodes: state.nodes, edges: state.edges }, expected_version: version || undefined } }); version = r.version; nb.canvas_version = version; nb.canvas = { nodes: state.nodes, edges: state.edges }; status.textContent = "Saved " + new Date().toLocaleTimeString(); }
    catch (e) { dirty = true;
      if (e.status === 409) { conflict = true; status.className = "status err"; status.textContent = "Not saved"; clear($("#cv-conflict"), h("div", { class: "notice warn", role: "alert" }, icon("warn"), h("div", null, h("strong", { text: "This canvas changed in another tab." }), h("p", { text: "Your latest edits weren't saved. Load the newer version, or copy anything you need first." })), h("button", { class: "btn sm", onclick: async () => { const n = await reload(); state = JSON.parse(JSON.stringify(n.canvas)); version = n.canvas_version; dirty = false; conflict = false; undo.length = 0; clear($("#cv-conflict")); status.textContent = "Saved"; draw(); } }, "Load newer version"))); }
      else { status.className = "status err"; status.textContent = "Not saved: " + explain(e, "save the canvas") + " Will retry."; timer = setTimeout(save, 4000); } }
    finally { saving = false; if (dirty && !conflict && !timer) timer = setTimeout(save, 1200); }
  }
  const nodeById = id => state.nodes.find(n => n.id === id);
  function freeSpot() {
    const sx = wrap.scrollLeft + 40, sy = wrap.scrollTop + 40; let x = sx, y = sy;
    while (state.nodes.some(n => Math.abs(n.x - x) < 40 && Math.abs(n.y - y) < 40)) { x += 36; y += 36; }
    return { x: Math.round(x), y: Math.round(y) };
  }
  function addNode(text = "") { pushUndo(); const n = { id: (crypto.randomUUID ? crypto.randomUUID() : "n" + Date.now() + Math.random().toString(36).slice(2)), text, ...freeSpot() }; state.nodes.push(n); selNode = n.id; changed(); draw(); startEdit(n.id); }
  function removeNode(id) { pushUndo(); state.nodes = state.nodes.filter(n => n.id !== id); state.edges = state.edges.filter(e => e.from !== id && e.to !== id); if (selNode === id) selNode = null; if (linkFrom === id) linkFrom = null; changed(); draw(); announce("Idea deleted."); }
  function toggleLink(from) { if (linkFrom) { linkFrom = null; setHint(); draw(); return; } if (state.nodes.length < 2) { toast("Add at least two ideas to connect them.", "err"); return; } if (!from) { toast("Select an idea first, then choose Connect.", "err"); return; } linkFrom = from; setHint(); draw(); announce("Choose the idea to connect to."); }
  function connect(to) { if (!linkFrom || to === linkFrom) return; if (state.edges.some(e => (e.from === linkFrom && e.to === to) || (e.from === to && e.to === linkFrom))) { toast("Those two are already connected."); linkFrom = null; setHint(); draw(); return; } pushUndo(); state.edges.push({ from: linkFrom, to }); linkFrom = null; changed(); setHint(); draw(); announce("Connected."); }
  function startEdit(id) { editing = id; draw(); const ta = $(`.node[data-id="${CSS.escape(id)}"] textarea`); if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); } }
  function nodeEl(n) {
    const sel = selNode === n.id;
    const el = h("div", { class: "node" + (sel ? " sel" : "") + (linkFrom === n.id ? " linking" : ""), dataset: { id: n.id }, tabindex: "0", role: "group", "aria-label": "Idea: " + (n.text || "empty"), style: { left: n.x + "px", top: n.y + "px" },
      onclick: e => { if (linkFrom && linkFrom !== n.id) { connect(n.id); e.stopPropagation(); return; } selNode = n.id; selEdge = null; setHint(); markSel(); },
      onkeydown: e => {
        if (editing === n.id) return;
        const step = e.shiftKey ? 40 : 10;
        if (e.key === "Enter") { e.preventDefault(); startEdit(n.id); }
        else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); removeNode(n.id); wrap.focus(); }
        else if (e.key === "l" || e.key === "L") { e.preventDefault(); selNode = n.id; toggleLink(n.id); }
        else if (e.key === "Escape" && linkFrom) { linkFrom = null; setHint(); draw(); }
        else if (e.key.startsWith("Arrow")) { e.preventDefault(); pushUndo(); n.x = Math.max(0, n.x + (e.key === "ArrowRight" ? step : e.key === "ArrowLeft" ? -step : 0)); n.y = Math.max(0, n.y + (e.key === "ArrowDown" ? step : e.key === "ArrowUp" ? -step : 0)); el.style.left = n.x + "px"; el.style.top = n.y + "px"; drawEdges(); changed(); }
      } },
      h("div", { class: "grip", onpointerdown: e => startDrag(e, n, el) }, h("span", { text: "Drag" }), h("span", null,
        h("button", { class: "iconbtn", type: "button", "aria-label": "Connect this idea to another", onpointerdown: e => e.stopPropagation(), onclick: e => { e.stopPropagation(); selNode = n.id; toggleLink(n.id); } }, icon("link")),
        h("button", { class: "iconbtn", type: "button", "aria-label": "Edit this idea", onpointerdown: e => e.stopPropagation(), onclick: e => { e.stopPropagation(); startEdit(n.id); } }, icon("edit")),
        h("button", { class: "iconbtn", type: "button", "aria-label": "Delete this idea", onpointerdown: e => e.stopPropagation(), onclick: e => { e.stopPropagation(); removeNode(n.id); } }, icon("trash")))));
    if (editing === n.id) {
      const ta = h("textarea", { maxlength: 5000, "aria-label": "Idea text", rows: 3 }); ta.value = n.text || "";
      const done = () => { if (editing !== n.id) return; const v = ta.value.trim(); editing = null; if (v !== n.text) { pushUndo(); n.text = v; changed(); } draw(); const again = $(`.node[data-id="${CSS.escape(n.id)}"]`); if (again) again.focus(); };
      ta.addEventListener("blur", done); ta.addEventListener("keydown", e => { if (e.key === "Escape") { ta.value = n.text || ""; ta.blur(); } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { ta.blur(); } e.stopPropagation(); });
      el.append(ta);
    } else el.append(h("div", { class: "txt", ondblclick: () => startEdit(n.id) }, n.text ? n.text : h("span", { class: "ph", text: "Empty idea. Double-click to write." })));
    return el;
  }
  function markSel() { $$n(".node").forEach(el => el.classList.toggle("sel", el.dataset.id === selNode)); }
  const $$n = s => [...board.querySelectorAll(s)];
  function startDrag(e, n, el) {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    e.preventDefault(); const grip = e.currentTarget; grip.setPointerCapture(e.pointerId);
    drag = { sx: e.clientX, sy: e.clientY, ox: n.x, oy: n.y, moved: false }; pushUndo(); selNode = n.id; markSel();
    const move = ev => { const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy; if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true; n.x = Math.max(0, Math.round(drag.ox + dx)); n.y = Math.max(0, Math.round(drag.oy + dy)); el.style.left = n.x + "px"; el.style.top = n.y + "px"; drawEdges(); sizeBoard(); };
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); grip.removeEventListener("pointercancel", up); if (drag.moved) changed(); else { undo.pop(); undoBtn.disabled = !undo.length; } drag = null; };
    grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up); grip.addEventListener("pointercancel", up);
  }
  function sizeBoard() { const w = Math.max(wrap.clientWidth, ...state.nodes.map(n => n.x + W + 80)), hh = Math.max(wrap.clientHeight, ...state.nodes.map(n => n.y + 220)); board.style.width = w + "px"; board.style.height = hh + "px"; edgesSvg.setAttribute("width", w); edgesSvg.setAttribute("height", hh); }
  function drawEdges() {
    edgesSvg.replaceChildren();
    state.edges.forEach((e, i) => { const a = nodeById(e.from), b = nodeById(e.to); if (!a || !b) return;
      const ea = board.querySelector(`.node[data-id="${CSS.escape(a.id)}"]`), eb = board.querySelector(`.node[data-id="${CSS.escape(b.id)}"]`);
      const ah = ea ? ea.offsetHeight : 80, bh = eb ? eb.offsetHeight : 80;
      const x1 = a.x + W / 2, y1 = a.y + ah / 2, x2 = b.x + W / 2, y2 = b.y + bh / 2, mx = (x1 + x2) / 2;
      const path = document.createElementNS(svgNS, "path"); path.setAttribute("d", `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`); if (selEdge === i) path.setAttribute("class", "sel");
      path.setAttribute("tabindex", "0"); path.setAttribute("role", "button"); path.setAttribute("aria-label", `Connection from “${(a.text || "idea").slice(0, 30)}” to “${(b.text || "idea").slice(0, 30)}”. Press Delete to remove.`);
      const pick = ev => { ev.stopPropagation(); selEdge = i; selNode = null; setHint(); drawEdges(); markSel(); };
      path.addEventListener("click", pick); path.addEventListener("focus", () => { selEdge = i; });
      path.addEventListener("keydown", ev => { if (ev.key === "Delete" || ev.key === "Backspace") { ev.preventDefault(); delEdge(i); } });
      edgesSvg.append(path); });
  }
  function delEdge(i) { pushUndo(); state.edges.splice(i, 1); selEdge = null; changed(); setHint(); draw(); announce("Connection removed."); }
  function draw() {
    board.querySelectorAll(".node").forEach(n => n.remove());
    state.nodes.forEach(n => board.append(nodeEl(n)));
    sizeBoard(); drawEdges(); setHint();
    if (!state.nodes.length && !board.querySelector(".cv-empty")) board.append(h("div", { class: "cv-empty empty", style: { position: "absolute", left: "28px", top: "20px" } }, h("h3", { text: "A blank board" }), h("p", { text: "Add an idea, or import a concept map you generated from a lecture. Drag ideas around and connect the ones that relate." })));
    else if (state.nodes.length) board.querySelectorAll(".cv-empty").forEach(x => x.remove());
  }
  wrap.addEventListener("click", e => { if (e.target === wrap || e.target === board) { if (linkFrom) { linkFrom = null; } selNode = null; selEdge = null; setHint(); draw(); } });
  wrap.addEventListener("keydown", e => { if ((e.metaKey || e.ctrlKey) && e.key === "z") { e.preventDefault(); doUndo(); } else if ((e.key === "Delete" || e.key === "Backspace") && selEdge != null && editing == null) { e.preventDefault(); delEdge(selEdge); } else if (e.key === "Escape" && linkFrom) { linkFrom = null; setHint(); draw(); } });
  async function importMap() {
    const maps = openItemPicker();
    if (!maps.length) { dialog({ title: "No concept maps yet", body: h("p", { text: "Generate a concept map from a linked lecture on the Notes and study tab, then import it here." }), actions: [{ label: "Close", id: "x", value: "" }] }); return; }
    const box = h("ul", { class: "ledger" }, ...maps.map(m => h("li", null, h("button", { class: "row-link", style: { width: "100%", background: "none", border: 0, textAlign: "left", cursor: "pointer", gridTemplateColumns: "1fr auto" }, onclick: () => { place(m); d.close("ok"); } }, h("span", { class: "row-title", text: m.title }), h("span", { class: "btn sm", text: "Import" })))));
    const d = dialog({ title: "Import a concept map", body: box, actions: [{ label: "Cancel", id: "c", value: "" }] });
  }
  function place(m) {
    const c = m.content || {}; const ns = (c.nodes || []).slice(0, Math.max(0, 500 - state.nodes.length)); if (!ns.length) { toast("That map has no ideas.", "err"); return; }
    pushUndo(); const ids = new Set(state.nodes.map(n => n.id)); const map = {}; const baseY = state.nodes.reduce((a, n) => Math.max(a, n.y + 150), 30);
    ns.forEach((n, i) => { let id = String(n.id); while (ids.has(id)) id += "-" + Math.random().toString(36).slice(2, 5); ids.add(id); map[n.id] = id; state.nodes.push({ id, text: String(n.text || "").slice(0, 400), x: Math.max(0, +n.x || 30 + (i % 3) * 250), y: baseY + Math.max(0, (+n.y || Math.floor(i / 3) * 120) - 30) }); });
    (c.edges || []).forEach(e => { if (map[e.from] && map[e.to]) state.edges.push({ from: map[e.from], to: map[e.to] }); });
    changed(); draw(); toast(`Imported ${ns.length} ideas.`);
  }
  const flush = () => { if (dirty && !conflict) { clearTimeout(timer); save(); } };
  const beforeUnload = e => { if (dirty && !conflict) { e.preventDefault(); e.returnValue = ""; flush(); } };
  window.addEventListener("beforeunload", beforeUnload);
  draw();
  return () => { window.removeEventListener("beforeunload", beforeUnload); clearTimeout(timer); flush(); };
}
