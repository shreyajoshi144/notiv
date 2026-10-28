// Read-only view of a shared notebook. No sign-in; the link itself is the credential. Everything is drawn from text nodes.
import { h, clear, icon, plural, when } from "/static/ui/core.js";
import { renderItemBody, TYPE_LABEL, miniMap } from "/static/ui/render.js";

const token = decodeURIComponent((location.pathname.match(/\/share\/([^/]+)/) || [])[1] || "");
const root = h("div");
document.body.append(h("a", { class: "skip", href: "#main", text: "Skip to content" }), h("header", { class: "sh-top" }, h("a", { class: "wordmark", href: "/", "aria-label": "Notiv home" }, "notiv", h("i", { text: "." })), h("span", { class: "faint", text: "Shared, read-only" })), root);
root.className = "sh-wrap"; root.id = "main"; root.tabIndex = -1;

function message(title, body, retry) {
  clear(root, h("div", { class: "empty", role: "alert" }, h("h1", { text: title }), h("p", { text: body }), retry ? h("button", { class: "btn", onclick: load }, icon("refresh"), "Try again") : h("a", { class: "btn", href: "/" }, "Go to Notiv")));
}
async function load() {
  clear(root, h("div", { class: "skeleton block" }), h("div", { class: "skeleton block" }));
  let r;
  try { r = await fetch("/shared/notebooks/" + encodeURIComponent(token), { headers: { Accept: "application/json" }, cache: "no-store", referrerPolicy: "no-referrer" }); }
  catch (_) { return message("Couldn't reach Notiv", "Check your connection and try again.", true); }
  if (r.status === 404 || r.status === 410) return message("This link doesn't work any more", "It may have been revoked or expired, or the address was copied incompletely. Ask the person who shared it for a new link.");
  if (r.status === 429) return message("Too many requests", "Wait a minute, then try again.", true);
  if (!r.ok) return message("Something went wrong", "The notebook couldn't be loaded (status " + r.status + ").", true);
  let d; try { d = await r.json(); } catch (_) { return message("Something went wrong", "The server's reply couldn't be read.", true); }
  document.title = (d.title || "Shared notebook") + " — Notiv";
  const canvasNodes = (d.canvas && d.canvas.nodes) || [];
  clear(root, h("h1", { text: d.title }), d.subject ? h("p", { class: "faint", text: d.subject }) : null, d.description ? h("p", { class: "lede", text: d.description }) : null,
    h("div", { class: "sh-banner" }, icon("link"), h("span", { text: "You're viewing a read-only copy. Links to lecture moments are shown but only open for the notebook's owner." })),
    d.sources && d.sources.length ? h("section", { "aria-labelledby": "src-h" }, h("h2", { id: "src-h", text: "Sources" }), h("ul", { class: "sh-sources" }, ...d.sources.map(s => h("li", null, h("strong", { text: s.label || "Lecture" }), " ", h("span", { class: "faint", text: s.source_type || "" }))))) : null,
    ...(d.items || []).map(it => h("article", { class: "sh-item" }, h("header", null, h("h2", { text: it.title }), h("span", { class: "chip plain", text: TYPE_LABEL[it.item_type] || it.item_type }), it.updated_at ? h("span", { class: "faint", text: "Updated " + when(it.updated_at) }) : null), renderItemBody(it, { readonly: true }))),
    !(d.items || []).length ? h("div", { class: "empty" }, h("p", { text: "This notebook has no saved items yet." })) : null,
    canvasNodes.length ? h("section", { class: "sh-item", "aria-labelledby": "cv-h" }, h("header", null, h("h2", { id: "cv-h", text: "Brainstorm canvas" }), h("span", { class: "faint", text: plural(canvasNodes.length, "idea") })), miniMap(d.canvas)) : null,
    h("p", { class: "sh-foot", text: "Shared from Notiv. The owner can revoke this link at any time." }));
}
load();
