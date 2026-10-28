// Renders notebook items (notes, summaries, concepts, flashcards, quizzes, maps) as DOM. Used by the notebook and by shared links.
// All text goes in via text nodes. Interactive decks/quizzes keep their state in memory only; nothing is faked or persisted here.
import { h, clear, icon, mmss, notice } from "/static/ui/core.js";

export const TYPE_LABEL = { note: "Note", summary: "Summary", concepts: "Concepts", definitions: "Definitions", flashcards: "Flashcards", quiz: "Quiz", mind_map: "Concept map", presentation: "Presentation", study_guide: "Study guide", brainstorm: "Brainstorm" };

// A source reference that opens the lecture at the right moment.
export function citeLink(ev, { readonly = false } = {}) {
  const c = ev.citation || {};
  const lid = ev.lecture_id;
  const label = c.label || (c.seek_seconds != null ? mmss(c.seek_seconds) : "Source");
  if (ev.available === false) return h("span", { class: "cite", "aria-disabled": "true", title: "The lecture was re-processed and this source no longer exists" }, "source replaced");
  if (readonly || !lid) return h("span", { class: "cite", title: "Reference in the original lecture" }, label);
  const frag = c.seek_seconds != null ? "#t=" + Math.floor(c.seek_seconds) : ev.evidence_id ? "#e=" + ev.evidence_id : "";
  return h("a", { class: "cite", href: `/app/lectures/${lid}${frag}`, title: "Open the lecture at this point" }, label);
}
const evidenceFor = x => (x.evidence || []).filter(e => e.available !== false);

/* ── tiny, safe markdown: headings, bullets, numbered lists, **bold**, *italic*, `code`, paragraphs ─ */
function inline(text) {
  const out = []; const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g; let last = 0, m;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    out.push(t.startsWith("**") ? h("strong", { text: t.slice(2, -2) }) : t.startsWith("`") ? h("code", { class: "mono", text: t.slice(1, -1) }) : h("em", { text: t.slice(1, -1) }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
export function markdown(src) {
  const root = h("div", { class: "prose" }); let list = null, para = [];
  const flush = () => { if (para.length) { root.append(h("p", null, ...inline(para.join(" ")))); para = []; } };
  const endList = () => { list = null; };
  for (const raw of String(src ?? "").split(/\r?\n/)) {
    const line = raw.trimEnd(); let m;
    if (!line.trim()) { flush(); endList(); continue; }
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flush(); endList(); root.append(h("h" + (m[1].length + 1), null, ...inline(m[2]))); continue; }
    if ((m = line.match(/^\s*[-*]\s+(.*)$/))) { flush(); if (!list || list.tagName !== "UL") { list = h("ul"); root.append(list); } list.append(h("li", null, ...inline(m[1]))); continue; }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) { flush(); if (!list || list.tagName !== "OL") { list = h("ol"); root.append(list); } list.append(h("li", null, ...inline(m[1]))); continue; }
    endList(); para.push(line.trim());
  }
  flush();
  return root;
}

/* ── per-type bodies ────────────────────────────────────────────────────────────────── */
function sources(x, o) { const seen = new Set(); const ev = evidenceFor(x).filter(e => { const k = (e.citation && e.citation.label) || e.evidence_id; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 6); return ev.length ? h("div", { class: "cites" }, ...ev.map(e => citeLink(e, o))) : null; }

function summaryBody(c, o) {
  const secs = c.sections || [];
  if (!secs.length) return empty("This summary has no sections.");
  const ai = c.mode === "ai";   // AI-written prose is cited, but it is not a verbatim quote: never style it as one
  const head = ai ? [c.note ? h("p", { class: "faint", text: c.note }) : null, c.overview ? h("section", { class: "sec" }, h("h3", { text: c.title || "Overview" }), h("p", { text: c.overview })) : null,
    (c.takeaways || []).length ? h("section", { class: "sec" }, h("h3", { text: "Key takeaways" }), h("ul", null, ...c.takeaways.map(t => h("li", null, t.text, sources(t, o))))) : null] : [];
  return h("div", { class: "stack" }, ...head.filter(Boolean), ...secs.map(s => h("section", { class: "sec" }, h("h3", { text: s.title }), s.body ? (ai ? h("p", { text: s.body }) : h("blockquote", { class: "quote", text: s.body })) : null, sources(s, o))));
}
function knowledgeBody(c, o) {
  const items = c.items || c.concepts || [];
  if (!items.length) return empty("Nothing was extracted. The lecture may not state definitions or key terms explicitly.");
  return h("div", { class: "stack" }, ...items.map(k => {
    const ai = k.origin === "ai_generated";
    return h("section", { class: "sec" }, h("h3", { text: k.title }),
      k.body ? (ai ? h("div", null, h("div", { class: "faint", text: "From your notes" }), h("p", { text: k.body })) : h("blockquote", { class: "quote", text: k.body })) : null,
      ai && !k.body && k.not_in_source_note ? h("p", { class: "faint", text: k.not_in_source_note }) : null,
      ai && k.supplementary_explanation ? h("div", { class: "notice info" }, h("div", { class: "faint", text: k.supplementary_label || "Supplementary explanation (AI): not from your notes" }), h("p", { text: k.supplementary_explanation })) : null,
      sources(k, o));
  }));
}
function empty(text) { return h("p", { class: "faint", text }); }

export function deck(items, o = {}) {
  let i = 0, shown = false; const marks = {};
  const root = h("div", { class: "deck", "aria-label": "Flashcards" });
  const draw = () => {
    const it = items[i]; const done = Object.keys(marks).length;
    const front = h("div", { class: "card-face" }, h("div", { class: "face-label faint", text: shown ? "Answer" : "Question" }), h("div", { class: "face-text serif", text: shown ? (it.answer || "") : it.prompt }),
      shown && it.explanation ? h("p", { class: "faint", text: it.explanation }) : null);
    clear(root,
      h("div", { class: "deck-top" }, h("span", { class: "faint", text: `Card ${i + 1} of ${items.length}` }), h("span", { class: "faint", text: done ? `${Object.values(marks).filter(v => v === "know").length} known, ${done} reviewed` : "" })),
      h("button", { class: "flash", type: "button", "aria-live": "polite", "aria-label": shown ? "Showing the answer. Press to see the question." : "Showing the question. Press to reveal the answer.", onclick: () => { shown = !shown; draw(); root.querySelector(".flash").focus(); } }, front),
      h("div", { class: "deck-actions" },
        h("button", { class: "btn sm", type: "button", disabled: i === 0, onclick: () => { i--; shown = false; draw(); } }, icon("back"), "Previous"),
        shown ? h("div", { class: "grade", role: "group", "aria-label": "How well did you know it?" }, ...[["know", "Got it"], ["unsure", "Unsure"], ["miss", "Missed it"]].map(([k, l]) => h("button", { class: "btn sm", type: "button", "aria-pressed": marks[it.item_id ?? i] === k, onclick: () => { marks[it.item_id ?? i] = k; if (i < items.length - 1) { i++; shown = false; } draw(); } }, l)))
          : h("button", { class: "btn sm primary", type: "button", onclick: () => { shown = true; draw(); root.querySelector(".flash").focus(); } }, icon("flip"), "Reveal answer"),
        h("button", { class: "btn sm", type: "button", disabled: i === items.length - 1, onclick: () => { i++; shown = false; draw(); } }, "Next", icon("arrow"))),
      sources(it, o));
  };
  draw(); return root;
}
export function quiz(items, o = {}) {
  const picked = {}; const root = h("div", { class: "quiz" });
  const draw = () => {
    const answered = Object.keys(picked).length, correct = items.filter((it, n) => picked[n] && picked[n] === it.correct_option).length;
    clear(root, h("p", { class: "faint", role: "status", text: answered ? `${correct} of ${answered} correct so far.` : `${items.length} questions. Pick an answer to check it against the lecture.` }),
      ...items.map((it, n) => {
        const done = picked[n] != null;
        return h("fieldset", { class: "q" }, h("legend", { class: "serif", text: `${n + 1}. ${it.prompt}` }),
          ...(it.options || []).map(o => h("button", { type: "button", class: "opt" + (done ? (o.id === it.correct_option ? " ok" : o.id === picked[n] ? " bad" : "") : ""), disabled: done, "aria-pressed": picked[n] === o.id,
            onclick: () => { picked[n] = o.id; draw(); } }, h("span", { class: "k mono", text: o.id.toUpperCase() }), h("span", { text: o.text }), done && o.id === it.correct_option ? h("span", { class: "sr", text: " (correct answer)" }) : null)),
          done ? h("div", { class: "reveal" }, h("p", { text: (picked[n] === it.correct_option ? "Correct. " : "Not quite. ") + (it.explanation || "") }), sources(it, o)) : null);
      }));
  };
  draw(); return root;
}
export function miniMap(c) {
  const nodes = c.nodes || [], edges = c.edges || [];
  if (!nodes.length) return notice("info", h("p", { text: c.message || "This map has no nodes." }));
  const W = 230, H = 56, xs = nodes.map(n => +n.x || 0), ys = nodes.map(n => +n.y || 0);
  const minx = Math.min(...xs), miny = Math.min(...ys), vw = Math.max(...xs) - minx + W + 20, vh = Math.max(...ys) - miny + H + 20;
  const NS = "http://www.w3.org/2000/svg", el = (t, a) => { const e = document.createElementNS(NS, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
  const svg = el("svg", { viewBox: `0 0 ${vw} ${vh}`, class: "minimap", role: "img", "aria-label": `Concept map with ${nodes.length} ideas` });
  const pos = {}; nodes.forEach(n => { pos[n.id] = { x: (+n.x || 0) - minx + 10, y: (+n.y || 0) - miny + 10 }; });
  edges.forEach(e => { const a = pos[e.from], b = pos[e.to]; if (!(a && b)) return; svg.append(el("line", { x1: a.x + W / 2, y1: a.y + H / 2, x2: b.x + W / 2, y2: b.y + H / 2, stroke: "#9c4a2f", "stroke-width": 1.5 }));
    if (e.label) { const t = el("text", { x: (a.x + b.x) / 2 + W / 2, y: (a.y + b.y) / 2 + H / 2 - 4, "font-size": 12, fill: "#6b5a44", "text-anchor": "middle", "font-family": "Instrument Sans, sans-serif" }); t.textContent = String(e.label).slice(0, 28); svg.append(t); } });
  nodes.forEach(n => { const p = pos[n.id]; svg.append(el("rect", { x: p.x, y: p.y, width: W, height: H, rx: 4, fill: "#faf6ec", stroke: "#b9ab92" }));
    const t = el("text", { x: p.x + 10, y: p.y + 33, "font-size": 15, fill: "#221e1a", "font-family": "Instrument Sans, sans-serif" }); t.textContent = String(n.text || "").slice(0, 30); svg.append(t); });
  return svg;
}
function fallback(c) {
  if (typeof c === "string") return markdown(c);
  return h("details", null, h("summary", { text: "Show raw content" }), h("pre", { class: "raw", text: JSON.stringify(c, null, 2) }));
}

// ctx: { readonly, onPlaceMap(nodes,edges) }
export function renderItemBody(item, ctx = {}) {
  const c = item.content, t = item.item_type;
  if (c && typeof c === "object" && c.ai_unavailable) {   // generated while no AI provider could answer: say so, then show what exists
    const rest = renderItemBody({ ...item, content: { ...c, ai_unavailable: undefined } }, ctx);
    return h("div", { class: "stack" }, notice("warn", h("strong", { text: "AI study material unavailable. " }), h("span", { text: c.ai_unavailable })), rest);
  }
  if (c && typeof c === "object" && c.insufficient_evidence) return notice("warn", h("strong", { text: "Not enough evidence yet. " }), h("span", { text: c.message || "" }));
  try {
    if (t === "note" || t === "study_guide" || t === "presentation" || t === "brainstorm") return typeof c === "string" ? markdown(c) : fallback(c);
    if (t === "summary") return summaryBody(c, ctx);
    if (t === "concepts" || t === "definitions") return knowledgeBody(c, ctx);
    if (t === "flashcards") return (c.items || []).length ? deck(c.items, ctx) : empty("No flashcards could be built from this lecture's evidence yet.");
    if (t === "quiz") return (c.items || []).length ? quiz(c.items, ctx) : empty("No quiz questions could be built from this lecture's evidence yet.");
    if (t === "mind_map") return h("div", null, miniMap(c), !ctx.readonly && ctx.onPlaceMap && (c.nodes || []).length ? h("p", null, h("button", { class: "btn sm", type: "button", onclick: () => ctx.onPlaceMap(c.nodes, c.edges || []) }, icon("canvas"), "Place on the canvas")) : null);
  } catch (_) { /* malformed legacy content falls through to the raw view */ }
  return fallback(c);
}
