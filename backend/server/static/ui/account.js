import { h, clear, mountShell, loadMe, signOut, icon, toast } from "/static/ui/core.js";
const PREF = "notiv.companion.v1", FOCUS = "notiv.focus.v1";
const rd = (k, d) => { try { return { ...d, ...JSON.parse(localStorage.getItem(k) || "{}") }; } catch (_) { return d; } };
const wr = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch (_) { toast("This browser wouldn't save that setting.", "err"); return false; } };
(async function () {
  const main = await mountShell("account", { narrow: true });
  let me = {}; try { me = await loadMe(); } catch (_) {}
  const P = rd(PREF, { hidden: false, calm: false }), T = rd(FOCUS, { focusMin: 25, breakMin: 5, sound: true });
  const check = (id, label, val, on) => { const i = h("input", { type: "checkbox", id, checked: !!val, onchange: e => on(e.target.checked) }); return h("label", { class: "check", for: id }, i, label); };
  const num = (id, label, val, max, on) => h("label", { class: "ft-num", for: id }, h("span", { text: label }), h("input", { id, type: "number", min: 1, max, value: val, inputmode: "numeric", onchange: e => { const v = Math.min(max, Math.round(Number(e.target.value))); if (!v || v < 1) { e.target.value = val; return; } e.target.value = v; on(v); } }));
  clear(main, h("div", { class: "pagehead" }, h("div", { class: "titles" }, h("h1", { text: "Account" }), h("p", { class: "lede", text: "Who you're signed in as, and how your desk behaves in this browser." }))),
    h("section", { class: "section", "aria-labelledby": "who-h" }, h("div", { class: "section-head" }, h("h2", { id: "who-h", text: "Signed in" })),
      h("dl", { class: "acct" }, h("div", null, h("dt", { text: "Name" }), h("dd", { text: me.display_name || "Not set" })), h("div", null, h("dt", { text: "Email" }), h("dd", { text: me.email || "Not available" }))),
      h("button", { class: "btn", type: "button", onclick: signOut }, icon("sign"), "Sign out")),
    h("section", { class: "section", "aria-labelledby": "pref-h" }, h("div", { class: "section-head" }, h("h2", { id: "pref-h", text: "Desk companion" })),
      h("p", { class: "faint", text: "These settings are saved in this browser only." }),
      h("div", { class: "stack" },
        check("p-cat", "Show the study cat", !P.hidden, v => { P.hidden = !v; wr(PREF, P); }),
        check("p-calm", "Calm the animations", P.calm, v => { P.calm = v; wr(PREF, P); }),
        check("p-snd", "Play a soft chime when a focus session ends", T.sound !== false, v => { T.sound = v; wr(FOCUS, T); }),
        h("div", { class: "ft-grid", style: { maxWidth: "320px" } }, num("p-f", "Focus (minutes)", T.focusMin, 120, v => { T.focusMin = v; if (T.mode !== "break") { T.endAt = null; T.remaining = v * 60000; } wr(FOCUS, T); }), num("p-b", "Break (minutes)", T.breakMin, 60, v => { T.breakMin = v; if (T.mode === "break") { T.endAt = null; T.remaining = v * 60000; } wr(FOCUS, T); })),
        h("p", { class: "faint", text: "Reload this page, or the next one you open, to see the cat change." }))));
})();
