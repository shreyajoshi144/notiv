import { $, h, clear, notice } from "/static/ui/core.js";

const panel = $("#panel");
const qs = new URLSearchParams(location.search);
const safeNext = v => typeof v === "string" && v.startsWith("/app") && !v.startsWith("//") && !v.includes("\\") && !/[\r\n]/.test(v) ? v : "/app";
(async function init() {
  const next = safeNext(qs.get("next"));
  try {
    const r = await fetch("/auth/password/me", { credentials: "same-origin", cache: "no-store" });
    if (r.ok) { location.replace(next); return; }
  } catch (_) {}
  clear(panel, h("h1", { text: "Sign in to Notiv" }),
    h("p", { class: "lede", text: "Sign in with your email and password." }),
    h("a", { class: "btn primary lg", href: "/sign-in" }, "Continue to sign in"));
})();
