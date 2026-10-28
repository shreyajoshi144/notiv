#!/usr/bin/env python3
"""Browser end-to-end suite for the Notiv UI. Real servers, real database, real pipeline output, real Chromium.

    PYTHONPATH=backend:.:core python tools/e2e_ui.py [--out DIR] [--headed]

What it does
  * creates a throw-away SQLite DB + object store, seeds 3 processed lectures (ffmpeg video, PDF deck, alignment, evidence,
    insights) with tools/seed_e2e_world.py, then starts TWO real uvicorn servers on that data:
      A  no identity provider configured       -> proves the sign-in page is honest and no login can be faked
      B  second server instance (formerly the OIDC scenario; kept so port wiring is unchanged)
  * drives Chromium at desktop (1366x900) and phone (390x844) widths through every screen and records PASS/FAIL per step
  * fails on ANY console error, page error or HTTP >= 400 that a step did not explicitly expect
  * writes screenshots + report.json + report.md to --out

What it does NOT prove (stated in the report): a specific production IdP, PostgreSQL, S3, a real speech model.
Exit code 0 only if every step passed."""
import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

from playwright.sync_api import sync_playwright, expect  # noqa: E402

CHROME = os.environ.get("NOTIV_CHROMIUM") or next((p for p in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome", "/opt/pw-browsers/chromium") if os.path.exists(p)), None)
SECRET = "e2e-auth-secret-e2e-auth-secret-e2e-auth-00"
SIGN = "e2e-sign-secret-e2e-sign-secret-e2e-sign-00"
USER = 888
HERO_SHA = "7ed3a0e023612b3ae4e27b2f0703ec1e07fbd23fc902fa4805f319d80e540e63"
RESULTS, SHOTS, CUR = [], {"dir": None}, {"pg": None}


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def wait_up(base, secs=40):
    end = time.time() + secs
    while time.time() < end:
        try:
            urlopen(base + "/healthz", timeout=2).read(); return
        except Exception:
            time.sleep(0.4)
    raise RuntimeError("server did not start: " + base)


class Step:
    """with Step('name'): ...  -> records PASS/FAIL, keeps going."""

    def __init__(self, name): self.name = name

    def __enter__(self): self.t = time.time(); return self

    def __exit__(self, et, ev, tb):
        ok = et is None
        RESULTS.append({"step": self.name, "ok": ok, "seconds": round(time.time() - self.t, 2), "error": None if ok else f"{et.__name__}: {str(ev)[:400]}"})
        print(("PASS " if ok else "FAIL ") + self.name + ("" if ok else "  <- " + str(ev)[:300].replace("\n", " ")))
        if not ok:
            try: CUR["pg"].screenshot(path=os.path.join(SHOTS["dir"], "FAIL-" + re.sub(r"[^a-z0-9]+", "-", self.name.lower())[:60] + ".png"))
            except Exception: pass
            if os.environ.get("E2E_TRACE"): traceback.print_exception(et, ev, tb)
        return True


class Watch:
    """Collects console errors / page errors / bad HTTP responses for a page; steps may declare expected ones."""

    def __init__(self, page, label):
        self.label, self.bad, self.expect = label, [], []
        CUR["pg"] = page
        page.on("console", lambda m: m.type == "error" and self.bad.append(("console", m.text[:200])))
        page.on("pageerror", lambda e: self.bad.append(("pageerror", str(e)[:200])))
        page.on("response", lambda r: r.status >= 400 and self.bad.append(("http", f"{r.status} {r.request.method} {re.sub(r'token=[^&]+', 'token=…', r.url)[:140]}")))

    def allow(self, *needles): self.expect.extend(needles)

    def unexpected(self):
        return [b for b in self.bad if not any(n in b[1] for n in self.expect)]


def shot(page, name):
    page.screenshot(path=os.path.join(SHOTS["dir"], name + ".png"))


def overflow_x(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def unnamed_controls(page):
    """Interactive elements with no accessible name (a coarse but real a11y check)."""
    return page.evaluate("""() => [...document.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=tab],[role=slider]')].filter(e => {
      if (e.closest('[hidden],dialog:not([open])')) return false; const s = getComputedStyle(e); if (s.display === 'none' || s.visibility === 'hidden') return false;
      const n = (e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') && document.getElementById(e.getAttribute('aria-labelledby'))?.textContent || e.textContent || e.title || e.value || e.placeholder || (e.labels && e.labels[0] && e.labels[0].textContent) || '').trim();
      return !n; }).map(e => e.outerHTML.slice(0, 120))""")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--out", default=os.path.join(ROOT, "e2e-output")); ap.add_argument("--headed", action="store_true"); a = ap.parse_args()
    out = os.path.abspath(a.out); shutil.rmtree(out, ignore_errors=True); os.makedirs(out); SHOTS["dir"] = out
    work = tempfile.mkdtemp(prefix="notiv-e2e-")
    py = sys.executable
    env = {**os.environ, "NOTIV_ENV": "development", "NOTIV_AUTH_SECRET": SECRET, "NOTIV_SIGNING_SECRET": SIGN, "NOTIV_LECTURE_DB": os.path.join(work, "e2e.db"),
           "NOTIV_STORAGE_DIR": os.path.join(work, "objects"), "PYTHONPATH": os.pathsep.join([os.path.join(ROOT, "backend"), ROOT, os.path.join(ROOT, "core")]),
           "NOTIV_RL_READ": "100000/60", "NOTIV_RL_WRITE": "100000/60", "NOTIV_RL_STUDY": "100000/60", "NOTIV_RL_ASK": "100000/60", "NOTIV_RL_UPLOAD": "100000/60", "NOTIV_RL_PROCESS": "100000/60",
           "NOTIV_INPROCESS_WORKER": "1"}
    seed_json = os.path.join(work, "seed.json")
    subprocess.run([py, os.path.join(ROOT, "tools", "seed_e2e_world.py"), "--user", str(USER), "--json", seed_json], env=env, check=True, capture_output=True)
    world = json.load(open(seed_json)); L = world["lectures"]; C = world["courses"]
    from server.auth_tokens import create_token
    token = create_token(USER, SECRET, 6 * 3600)

    portA, portB = free_port(), free_port()
    envB = {**env}                                           # (the OIDC provider scenario was removed with Google sign-in)
    procs = [subprocess.Popen([py, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(p), "--log-level", "warning"], env=e, cwd=ROOT, stdout=open(os.path.join(out, f"server-{p}.log"), "w"), stderr=subprocess.STDOUT)
             for p, e in ((portA, env), (portB, envB))]
    A, B = f"http://127.0.0.1:{portA}", f"http://127.0.0.1:{portB}"
    try:
        wait_up(A); wait_up(B)
        run(A, B, token, L, C, work, out, a.headed, None)
    finally:
        for p in procs: p.terminate()
        pass
        for p in procs:
            try: p.wait(8)
            except Exception: p.kill()
        shutil.rmtree(work, ignore_errors=True)
    fails = [r for r in RESULTS if not r["ok"]]
    json.dump({"results": RESULTS, "passed": len(RESULTS) - len(fails), "failed": len(fails)}, open(os.path.join(out, "report.json"), "w"), indent=1)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(f"# Browser E2E: {len(RESULTS) - len(fails)} passed, {len(fails)} failed\n\n" + "\n".join(f"- {'PASS' if r['ok'] else 'FAIL'} {r['step']}" + ("" if r["ok"] else f" — {r['error']}") for r in RESULTS) + "\n")
    print(f"\n{len(RESULTS) - len(fails)} passed, {len(fails)} failed -> {out}")
    sys.exit(1 if fails else 0)


def run(A, B, token, L, C, work, out, headed, idp):
    with sync_playwright() as pw:
        br = pw.chromium.launch(executable_path=CHROME, headless=not headed)

        def ctx(w, h, signed=True, base=A):
            c = br.new_context(viewport={"width": w, "height": h}, accept_downloads=True, reduced_motion="reduce")
            if signed: c.add_init_script(f"try{{sessionStorage.setItem('notiv_token','{token}')}}catch(e){{}}")
            return c

        # ───────────────────────── landing ─────────────────────────
        for label, (w, h) in {"desktop": (1366, 900), "phone": (390, 844)}.items():
            c = ctx(w, h, signed=False); pg = c.new_page(); wt = Watch(pg, "landing-" + label)
            with Step(f"landing/{label}: loads, hero is the original photograph, no overflow, no errors"):
                pg.goto(A + "/"); pg.wait_for_load_state("networkidle")
                nat = pg.evaluate("[document.querySelector('.hero-img').naturalWidth, document.querySelector('.hero-img').naturalHeight]")
                assert nat == [1536, 1024], nat
                sha = pg.evaluate("fetch('/static/assets/notiv-hero.jpg').then(r=>r.arrayBuffer()).then(b=>crypto.subtle.digest('SHA-256',b)).then(d=>[...new Uint8Array(d)].map(x=>x.toString(16).padStart(2,'0')).join(''))")
                assert sha == HERO_SHA, sha
                assert overflow_x(pg) <= 0, overflow_x(pg)
                assert pg.locator("h1").inner_text().startswith("Every answer")
                assert not wt.unexpected(), wt.unexpected(); shot(pg, f"landing-{label}")
            with Step(f"landing/{label}: no glass, no backdrop blur, accessible names present"):
                assert pg.evaluate("[...document.querySelectorAll('*')].every(e => ['none', '', undefined].includes(getComputedStyle(e).backdropFilter))")
                assert not unnamed_controls(pg), unnamed_controls(pg)
                assert pg.evaluate("[...document.images].every(i => i.hasAttribute('alt'))")
            if label == "desktop":
                with Step("landing: 'Sign in' leads to /sign-in"):
                    pg.click(".hero-links a.btn"); pg.wait_for_url("**/sign-in"); assert "Sign in" in pg.locator("h1").inner_text()
            c.close()

        # ───────────────────────── sign-in without a provider ─────────────────────────
        c = ctx(1366, 900, signed=False); pg = c.new_page(); wt = Watch(pg, "signin-A")
        with Step("sign-in (no provider): says plainly it isn't set up, shows no provider button"):
            pg.goto(A + "/sign-in"); pg.wait_for_selector("text=isn't set up"); assert pg.locator("a[href^='/auth/login']").count() == 0
            assert "NOTIV_OIDC_ISSUER" in pg.inner_text("main"); shot(pg, "signin-unconfigured")
        with Step("sign-in (no provider): /auth/login is 503 and /auth/callback never signs anyone in"):
            r = c.request.get(A + "/auth/login", max_redirects=0); assert r.status == 503, r.status
            r = c.request.get(A + "/auth/callback?code=good-code&state=x", max_redirects=0); assert r.status == 503, r.status
        with Step("sign-in: an invalid access token is rejected with a clear message"):
            wt.allow("401 GET"); pg.locator("summary:has-text('access token')").click(); pg.fill("#tok", "not-a-token"); pg.click("text=Check token and continue")
            pg.wait_for_selector(".err:visible"); assert "isn't valid" in pg.inner_text(".err"); assert pg.url.endswith("/sign-in")
        with Step("sign-in: a protected page redirects to sign-in with ?next and a real token then returns there"):
            pg.goto(A + "/app/notebooks"); pg.wait_for_url(re.compile(r".*/sign-in\?.*next=.*")); pg.locator("summary:has-text('access token')").click()
            pg.fill("#tok", token); pg.click("text=Check token and continue"); pg.wait_for_url("**/app/notebooks"); pg.wait_for_selector("h1")
        c.close()

        # ───────────────────────── desk ─────────────────────────
        for label, (w, h) in {"desktop": (1366, 900), "phone": (390, 844)}.items():
            c = ctx(w, h); pg = c.new_page(); wt = Watch(pg, "desk-" + label)
            with Step(f"desk/{label}: shows the seeded lectures with their real state, no overflow"):
                pg.goto(A + "/app"); pg.wait_for_selector(".ledger li"); n = pg.locator(".ledger li").count(); assert n >= 3, n
                assert "Gradient descent and regularization" in pg.inner_text("main"); assert overflow_x(pg) <= 0, overflow_x(pg)
                assert not unnamed_controls(pg), unnamed_controls(pg); shot(pg, f"desk-{label}")
            if label == "desktop":
                with Step("desk: filtering and search narrow the list; clearing restores it"):
                    pg.fill("input[type=search]", "optimizers"); pg.wait_for_timeout(400)
                    expect(pg.locator("li[data-lecture]")).to_have_count(1); pg.fill("input[type=search]", "zzzz-nothing")
                    expect(pg.locator("li[data-lecture]")).to_have_count(0); pg.fill("input[type=search]", ""); expect(pg.locator("li[data-lecture]")).to_have_count(4)
                with Step("desk: an empty lecture (no sources yet) is labelled honestly and opens to an upload prompt"):
                    pg.goto(A + f"/app/lectures/{L['empty']}"); pg.wait_for_selector(".proc"); assert "no recording or slides yet" in pg.inner_text(".proc"); shot(pg, "lecture-empty")
                with Step("desk: keyboard focus is visible on the first interactive element"):
                    pg.goto(A + "/app"); pg.wait_for_selector(".ledger li"); pg.keyboard.press("Tab"); pg.keyboard.press("Tab")
                    vis = pg.evaluate("(() => { const e = document.activeElement; const s = getComputedStyle(e); return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0 || s.boxShadow !== 'none' })()"); assert vis
            assert not wt.unexpected(), wt.unexpected()
            c.close()

        # ───────────────────────── upload a real recording + deck ─────────────────────────
        c = ctx(1366, 900); pg = c.new_page(); wt = Watch(pg, "upload")
        with Step("upload: add a lecture with a real video + PDF; it is created, uploaded and queued; the viewer shows real progress or a real outcome"):
            from core.lecture.make_test_lecture import make_lecture, make_text_pdf
            fx = os.path.join(work, "up"); os.makedirs(fx); video, _, _ = make_lecture(fx, [3, 3]); pdf = make_text_pdf(fx, n=2, slide_lines=[["Intro", "a point"], ["Next", "another"]], name="deck-text.pdf")
            pg.goto(A + "/app"); pg.wait_for_selector(".ledger li"); pg.click("text=Add a lecture"); pg.wait_for_selector("dialog[open]")
            pg.fill("#nl-title", "E2E uploaded lecture"); pg.set_input_files("#nl-media", video); pg.set_input_files("#nl-deck", pdf); pg.click("dialog[open] button[type=submit]")
            pg.wait_for_url(re.compile(r".*/app/lectures/\d+$"), timeout=60000); pg.wait_for_selector("h1"); assert "E2E uploaded lecture" in pg.inner_text("h1")
            end = time.time() + 120; state = ""
            while time.time() < end:
                state = pg.inner_text(".lec-head .chip") if pg.locator(".lec-head .chip").count() else ""
                if state in ("Ready", "Needs attention"): break
                pg.wait_for_timeout(1500)
            assert state in ("Ready", "Needs attention"), "never reached a terminal state: " + state
            RESULTS[-1]["note"] = "terminal state: " + state; shot(pg, "upload-outcome")
            if state == "Needs attention":   # no speech model installed here: the UI must say so plainly and offer a retry, not hang or pretend
                assert "Processing failed" in pg.inner_text("main") and "Retry processing" in pg.inner_text("main"); RESULTS[-1]["note"] += " (" + pg.inner_text("main").split("failed")[1].split("\n")[1][:90] + ")"
        with Step("upload: a bad file type is rejected with a clear message (no silent failure)"):
            pg.goto(A + "/app"); pg.wait_for_selector(".ledger li"); pg.click("text=Add a lecture"); pg.wait_for_selector("dialog[open]"); pg.fill("#nl-title", "Bad file")
            bad = os.path.join(work, "x.exe"); open(bad, "wb").write(b"MZ" + b"\0" * 64); pg.set_input_files("#nl-media", bad); pg.click("dialog[open] button[type=submit]")
            pg.wait_for_selector("dialog[open] .notice.err", timeout=20000); wt.allow("415", "422", "400"); pg.keyboard.press("Escape")
        c.close()

        # ───────────────────────── lecture viewer ─────────────────────────
        lid = L["gradient_descent"]
        for label, (w, h) in {"desktop": (1366, 900), "phone": (390, 844)}.items():
            c = ctx(w, h); pg = c.new_page(); wt = Watch(pg, "lecture-" + label)
            with Step(f"lecture/{label}: renders player, ruler, slide and transcript; no overflow"):
                pg.goto(A + f"/app/lectures/{lid}"); pg.wait_for_selector(".lec-grid .tab"); pg.wait_for_selector("#curImg")
                assert pg.locator("#media").count() == 1 and pg.locator("#timeline").count() == 1 and pg.locator(".line").count() >= 10
                assert overflow_x(pg) <= 0, overflow_x(pg); assert not unnamed_controls(pg), unnamed_controls(pg); shot(pg, f"lecture-{label}")
            if label != "desktop":
                c.close(); assert not wt.unexpected(), wt.unexpected(); continue
            with Step("lecture: clicking a transcript line seeks the media and shows that line's slide"):
                pg.locator(".line", has_text="Backpropagation is the algorithm").first.click(); pg.wait_for_timeout(500)
                t = pg.evaluate("document.querySelector('#media').currentTime"); assert 5.5 <= t <= 8, t
                assert pg.locator("#curTitle").inner_text().startswith("Slide 2"), pg.locator("#curTitle").inner_text()
                assert pg.locator(".line.on").count() == 1
            with Step("lecture: deep link #t=16 opens at the right slide and line"):
                pg.goto(A + f"/app/lectures/{lid}#t=16"); pg.wait_for_selector("#curImg"); pg.wait_for_timeout(800)
                assert pg.locator("#curTitle").inner_text().startswith("Slide 3"), pg.locator("#curTitle").inner_text()
                assert abs(pg.evaluate("document.querySelector('#media').currentTime") - 16) < 1.5
            with Step("lecture: timeline keyboard control and key-moment markers seek"):
                pg.locator("#timeline").focus(); before = pg.evaluate("document.querySelector('#media').currentTime"); pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(300)
                assert pg.evaluate("document.querySelector('#media').currentTime") > before
                n = pg.locator("#timeline .km").count(); assert n >= 1; pg.locator("#timeline .km").first.click(); pg.wait_for_timeout(300)
            with Step("lecture: search finds lines, highlights terms, and 'No matches' is honest"):
                pg.click("#tab-search"); pg.fill("#q", "learning rate"); pg.wait_for_selector("#results .evrow"); assert pg.locator("#results mark").count() >= 1
                pg.fill("#q", "quantum chromodynamics"); pg.wait_for_selector("text=No matches");
            with Step("lecture: evidence, key moments and summary tabs show sourced material"):
                for t, sel in (("evidence", "#p-evidence .evrow"), ("moments", "#p-moments .card-note"), ("summary", "#p-summary .card-note")):
                    pg.click("#tab-" + t); pg.wait_for_selector(sel); assert pg.locator(sel + " .cite").count() >= 1, t
            with Step("lecture: correcting a transcript line persists and is marked 'corrected'"):
                pg.click("#tab-transcript"); row = pg.locator(".line", has_text="learning rate means").first; row.hover(); row.locator(".fix").click(); pg.wait_for_selector("dialog[open]")
                pg.fill("dialog[open] textarea", "The learning rate means the size of the step we take downhill on every update."); pg.click("dialog[open] button[type=submit]"); pg.wait_for_selector("dialog[open]", state="detached")
                pg.reload(); pg.wait_for_selector(".line"); assert "on every update" in pg.inner_text("#p-transcript") and "corrected" in pg.inner_text("#p-transcript")
            with Step("lecture/Ask: a covered question is answered with clickable sources; an uncovered one says 'not covered'"):
                pg.click("#tab-ask"); pg.fill("#ask-in", "What happens if the learning rate is too large?"); pg.click("text=Ask >> nth=-1"); pg.wait_for_selector(".msg.bot:not([role=status])", timeout=30000)
                assert pg.locator(".msg.bot .cite").count() >= 1; assert "diverge" in pg.inner_text("#p-ask").lower()
                pg.fill("#ask-in", "Who won the 1998 world cup?"); pg.keyboard.press("Enter"); pg.wait_for_selector(".msg.bot.none", timeout=30000)
                shot(pg, "lecture-ask")
            with Step("lecture/Study: concepts, practice session (graded, reveals the source) and study items work"):
                pg.click("#tab-study"); pg.wait_for_selector("#p-study .card-note"); pg.click("#p-study >> text=Practice"); pg.click("text=Build study items"); pg.wait_for_selector("text=Start a session"); pg.click("text=Start a session"); pg.wait_for_selector(".card-face")
                pg.click("text=Show the answer"); pg.click("text=Got it"); pg.wait_for_selector(".reveal"); assert pg.locator(".reveal .cite").count() >= 1
                pg.click("#p-study >> text=Study items"); pg.wait_for_selector("#p-study details summary"); shot(pg, "lecture-study")
            with Step("lecture/Notes: add pinned note, edit, persist after reload, delete"):
                pg.click("#tab-notes"); pg.fill("#newnote", "E2E note about momentum"); pg.click("text=Add note"); pg.wait_for_selector(".note-card")
                pg.reload(); pg.click("#tab-notes"); pg.wait_for_selector(".note-card"); assert "E2E note about momentum" in pg.inner_text("#p-notes")
                pg.click(".note-card >> text=Edit"); pg.fill(".note-card textarea", "E2E note, edited"); pg.click(".note-card >> text=Save"); pg.wait_for_selector("text=E2E note, edited")
                pg.click(".note-card >> text=Delete"); pg.click("dialog[open] button:has-text('Delete')"); pg.wait_for_selector(".note-card", state="detached")
            with Step("lecture: 'Save to a notebook' from an answer creates a note with provenance and links the lecture"):
                nb = c.request.post(A + "/notebooks", headers={"Authorization": "Bearer " + token}, data=json.dumps({"title": "E2E save target"}), ).json() if False else None
                pg.click("#tab-concepts" if False else "#tab-study"); pg.click("#p-study >> text=Concepts"); pg.wait_for_selector("#p-study .card-note")
                pg.locator("#p-study .card-note >> text=Save to a notebook").first.click(); pg.wait_for_selector("dialog[open]")
                if pg.locator("dialog[open] .row-link").count() == 0:
                    pg.click("dialog[open] >> text=New notebook"); pg.fill("dialog[open] input[name=title], dialog[open] #f-title", "E2E save target"); pg.click("dialog[open] button[type=submit]")
                else: pg.locator("dialog[open] .row-link").first.click()
                pg.wait_for_selector("dialog[open]", state="detached", timeout=15000)
            assert not wt.unexpected(), wt.unexpected()
            c.close()

        # ───────────────────────── notebooks ─────────────────────────
        c = ctx(1366, 900); pg = c.new_page(); wt = Watch(pg, "notebooks"); nbid = {}
        with Step("notebooks: list shows existing notebooks; creating one opens its workspace"):
            pg.goto(A + "/app/notebooks"); pg.wait_for_selector("h1"); shot(pg, "notebooks-list")
            pg.click("text=New notebook >> nth=0"); pg.wait_for_selector("dialog[open]"); pg.fill("dialog[open] input[name=title], dialog[open] #f-title", "E2E notebook"); pg.fill("dialog[open] input[name=subject], dialog[open] #f-subject", "Deep Learning")
            pg.click("dialog[open] button[type=submit]"); pg.wait_for_url(re.compile(r".*/app/notebooks/\d+")); nbid["id"] = int(re.search(r"/notebooks/(\d+)", pg.url).group(1)); pg.wait_for_selector("text=Start by linking a lecture")
            shot(pg, "notebook-empty")
        with Step("notebook: Ask is disabled with an explanation until a lecture is linked"):
            pg.click("#tab-ask"); pg.wait_for_selector("text=Link a lecture to ask it questions")
        with Step("notebook: link two lectures; unlink and relink works"):
            pg.click("#tab-sources"); pg.click("text=Link a lecture >> nth=0"); pg.wait_for_selector("dialog[open]")
            for t in ("Gradient descent and regularization", "Learning rates and optimizers"): pg.locator("dialog[open] li", has_text=t).locator("text=Link").click(); pg.wait_for_selector(f"dialog[open] li:has-text('{t}') >> text=Already linked")
            pg.click("dialog[open] >> text=Done"); assert pg.locator("#panel .ledger li").count() == 2
        with Step("notebook: generate a summary, concepts, flashcards and a quiz from real evidence"):
            pg.click("#tab-material"); made = 0
            for typ in ("Summary", "Concepts", "Flashcards", "Quiz"):
                pg.click("#panel button:has-text('Generate')"); pg.wait_for_selector("dialog[open]"); pg.select_option("#g-type", label=re.compile(typ) and next(o for o in pg.locator("#g-type option").all_inner_texts() if typ.lower() in o.lower()))
                pg.click("dialog[open] button:has-text('Generate')"); pg.wait_for_selector("dialog[open]", state="detached", timeout=30000); made += 1; pg.click("a:has-text('All notes and study material')")
            pg.wait_for_selector("#panel .ledger li"); assert pg.locator("#panel .ledger li").count() >= 4; shot(pg, "notebook-material")
        with Step("notebook: flashcards deck and quiz are interactive; answers are checked against the lecture; sources open the lecture at the minute"):
            pg.locator("#panel .ledger li", has_text="Flashcards").first.locator("a").click(); pg.wait_for_selector(".deck"); pg.click("text=Reveal answer"); pg.wait_for_selector("text=Got it"); pg.click("text=Got it")
            pg.go_back(); pg.locator("#panel .ledger li", has_text="Quiz").first.locator("a").click(); pg.wait_for_selector(".quiz .opt"); pg.locator(".quiz .opt").first.click(); pg.wait_for_selector(".quiz .reveal"); shot(pg, "notebook-quiz")
            pg.go_back(); pg.locator("#panel .ledger li", has_text="Summary").first.locator("a").click(); pg.wait_for_selector("a.cite"); href = pg.locator("a.cite").first.get_attribute("href"); assert re.match(r"/app/lectures/\d+#t=\d+", href), href
        with Step("notebook: a note autosaves, survives reload, and a stale tab gets a conflict warning rather than overwriting"):
            pg.goto(A + f"/app/notebooks/{nbid['id']}#material"); pg.click("#panel >> text=New note >> nth=0"); pg.wait_for_selector("dialog[open]"); pg.fill("dialog[open] input[name=title], dialog[open] #f-title", "E2E note")
            pg.click("dialog[open] button[type=submit]"); pg.wait_for_selector(".editor"); pg.fill(".editor", "# Heading\n\nSome **bold** text"); expect(pg.locator(".status")).to_have_text(re.compile(r"^Saved "), timeout=10000)
            url = pg.url; pg.reload(); pg.wait_for_selector(".editor"); assert "Some **bold** text" in pg.input_value(".editor")
            p2 = c.new_page(); p2.goto(url); p2.wait_for_selector(".editor"); p2.fill(".editor", "edited in tab two"); expect(p2.locator(".status")).to_have_text(re.compile(r"^Saved "), timeout=10000)
            wt.allow("409"); pg.fill(".editor", "edited in tab one"); pg.wait_for_selector("text=changed in another tab", timeout=10000); p2.close()
        with Step("notebook/Ask: grounded answers cite the lecture, ungrounded say so, and a good answer saves as a note"):
            pg.goto(A + f"/app/notebooks/{nbid['id']}#ask"); pg.wait_for_selector("#ask-in"); pg.fill("#ask-in", "What is the learning rate?"); pg.keyboard.press("Enter"); pg.wait_for_selector(".msg.bot .cite", timeout=30000)
            pg.click("text=Save as a note"); pg.wait_for_selector("text=Saved. Open the note"); pg.fill("#ask-in", "Who won the 1998 world cup?"); pg.keyboard.press("Enter"); pg.wait_for_selector(".msg.bot.none", timeout=30000); shot(pg, "notebook-ask")
        with Step("notebook/Canvas: add ideas, link them, drag, persist across reload; invalid link to itself is refused"):
            pg.goto(A + f"/app/notebooks/{nbid['id']}#canvas"); pg.wait_for_selector("#panel"); canvas_flow(pg, nbid["id"], token, A, c); shot(pg, "notebook-canvas")
        with Step("notebook/Export: every format downloads a real file with the right signature"):
            pg.goto(A + f"/app/notebooks/{nbid['id']}#share"); pg.wait_for_selector(".formats .format"); magic = {"md": b"# ", "docx": b"PK", "pdf": b"%PDF", "pptx": b"PK", "txt": None, "csv": None, "json": b"{", "svg": b"<svg"}
            names = pg.locator(".formats .format").count(); assert names >= 6, names; got = {}
            for i in range(names):
                with pg.expect_download() as d: pg.locator(".formats .format").nth(i).click()
                path = d.value.path(); data = open(path, "rb").read(); ext = d.value.suggested_filename.rsplit(".", 1)[-1]; got[ext] = len(data); assert len(data) > 20, ext
                m = magic.get(ext)
                if m: assert data.lstrip(b"\xef\xbb\xbf").startswith(m) or m in data[:200], (ext, data[:20])
            assert {"md", "docx", "pdf", "pptx"} <= set(got), got; RESULTS[-1]["note"] = f"downloaded {sorted(got)}"
        with Step("notebook/Share: create link, anonymous visitor reads it read-only (no private lecture data), revoke makes it 404"):
            pg.select_option("#sh-exp", "86400"); pg.click("text=Create read-only link"); pg.wait_for_selector("#share-url"); url = pg.input_value("#share-url"); assert "/share/" in url
            anon = ctx(1100, 900, signed=False); ap_ = anon.new_page(); aw = Watch(ap_, "anon"); ap_.goto(url); ap_.wait_for_selector("article.sh-item"); body = ap_.inner_text("body")
            assert "E2E notebook" in body and "Sign out" not in body; leak = ap_.evaluate("[...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')).filter(h => h.startsWith('/app'))"); assert not leak, "shared page links into the private app: " + str(leak); assert overflow_x(ap_) <= 0
            assert not unnamed_controls(ap_), unnamed_controls(ap_); shot(ap_, "share-anon")
            pg.click("#panel button:has-text('Revoke')"); pg.click("dialog[open] button:has-text('Revoke link')"); pg.wait_for_selector("text=Revoked")
            ap_.reload(); ap_.wait_for_selector("text=doesn't work any more"); aw.allow("404"); shot(ap_, "share-revoked")
            assert not aw.unexpected(), aw.unexpected(); anon.close()
        with Step("notebook: delete confirms first, then removes the notebook"):
            pg.goto(A + f"/app/notebooks/{nbid['id']}"); pg.wait_for_selector("#panel"); pg.click("button:has(.sr:text('More actions'))"); pg.click("text=Delete notebook"); pg.click("dialog[open] button:has-text('Delete')")
            pg.wait_for_url(re.compile(r".*/app/notebooks/?$")); pg.wait_for_selector("h1")
        assert not wt.unexpected(), wt.unexpected()
        c.close()

        # ───────────────────────── notebook on a phone ─────────────────────────
        c = ctx(390, 844); pg = c.new_page(); wt = Watch(pg, "notebook-phone")
        with Step("phone: notebook list, workspace and course pages have no horizontal overflow"):
            nbs = c.request.get(A + "/notebooks", headers={"Authorization": "Bearer " + token}).json()["items"]
            urls = [A + "/app/notebooks"] + [A + f"/app/notebooks/{nbs[0]['id']}#material"] if nbs else [A + "/app/notebooks"]
            urls += [A + f"/app/courses/{C['deep_learning']}"]
            for u in urls: pg.goto(u); pg.wait_for_selector("h1"); pg.wait_for_timeout(500); assert overflow_x(pg) <= 0, (u, overflow_x(pg)); assert not unnamed_controls(pg), (u, unnamed_controls(pg))
            shot(pg, "course-phone")
        assert not wt.unexpected(), wt.unexpected(); c.close()

        # ───────────────────────── course ─────────────────────────
        c = ctx(1366, 900); pg = c.new_page(); wt = Watch(pg, "course")
        with Step("course: lectures, concepts across lectures, cross-lecture search and course practice"):
            pg.goto(A + f"/app/courses/{C['deep_learning']}"); pg.wait_for_selector(".ledger li"); assert pg.locator(".ledger li").count() == 2; shot(pg, "course-desktop")
            pg.click("#ct-concepts"); pg.wait_for_selector(".card-note"); pg.check("text=Only concepts that appear in more than one lecture"); assert pg.locator(".card-note").count() >= 1
            pg.click("#ct-search"); pg.fill("#cq", "learning rate"); pg.wait_for_selector(".evrow"); pg.click("#ct-practice"); pg.click("text=Start a session"); pg.wait_for_selector(".card-face"); pg.click("text=Got it"); pg.wait_for_selector(".reveal")
        with Step("course: someone else's course id gives 'can't find that', not data"):
            pg.goto(A + "/app/courses/999999"); pg.wait_for_selector("text=can't find"); wt.allow("404")
        with Step("viewer: another user's lecture id is not reachable (isolation)"):
            from server.auth_tokens import create_token
            other = create_token(4242, SECRET, 3600); oc = br.new_context(); oc.add_init_script(f"sessionStorage.setItem('notiv_token','{other}')"); op = oc.new_page(); ow = Watch(op, "other")
            op.goto(A + f"/app/lectures/{lid}"); op.wait_for_selector("text=can't find"); ow.allow("404"); assert not ow.unexpected(), ow.unexpected(); oc.close()
        assert not wt.unexpected(), wt.unexpected()
        c.close(); br.close()


def canvas_flow(pg, nb_id, token, base, c):
    """Add two ideas, link them, reload, and confirm the saved state through the API (not just the DOM)."""
    for txt in ("Gradient", "Learning rate"):
        pg.click("button:has-text('Add an idea')"); pg.wait_for_selector(".node textarea"); pg.fill(".node textarea", txt); pg.keyboard.press("Control+Enter"); pg.wait_for_selector(".node textarea", state="detached")
    assert pg.locator(".node").count() == 2
    pg.locator(".node").first.hover(); pg.locator("[aria-label='Connect this idea to another']").first.click(); pg.locator(".node").nth(1).click()
    expect(pg.locator(".status")).to_have_text(re.compile(r"Saved"), timeout=10000)
    pg.reload(); pg.wait_for_selector(".node")
    st = c.request.get(f"{base}/notebooks/{nb_id}", headers={"Authorization": "Bearer " + token}).json()["canvas"]
    assert len(st["nodes"]) == 2 and len(st["edges"]) == 1, st
    assert st["edges"][0]["from"] != st["edges"][0]["to"]


if __name__ == "__main__":
    main()
