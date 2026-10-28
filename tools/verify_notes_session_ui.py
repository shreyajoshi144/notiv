"""Real headless-Chromium verification of the Notes tab and the sign-in / session fixes.

Real pipeline lecture + server.dev_server (same handlers/auth as production routes). Exits 0 with SKIPPED if Playwright/Chromium is missing.
    python tools/verify_notes_session_ui.py
"""
import os, subprocess, sys, tempfile, threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ["NOTIV_AUTH_SECRET"] = "s" * 40
try:
    from playwright.sync_api import sync_playwright
except Exception:
    print("SKIPPED playwright not installed (UNVERIFIED)"); sys.exit(0)
from core import study_fixtures as F
from core.lecture import config
from core.lecture.make_test_lecture import make_lecture, make_text_pdf
from core.lecture.testkit import Env, segments
from server.auth_tokens import create_token
from server.dev_server import make_server


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        check.failed.append(name)
check.failed = []


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="notiv-notes-ui-")
    mp4, _, _ = make_lecture(os.path.join(tmp, "fx"), [6, 9, 5, 7])
    webm = os.path.join(tmp, "fx", "lecture.webm")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp4, "-c:v", "libvpx", "-b:v", "1M", "-c:a", "libvorbis", webm], check=True)
    env = Env(tmp); config.configure(env.repo, env.storage)
    course = env.lectures.create_course(1, "DL")
    lid = env.lecture(1, webm, make_text_pdf(os.path.join(tmp, "fx"), slide_lines=F.DL1_SLIDES, name="DL1.pdf"), title="DL1", course_id=course)
    env.process(lid, segments(*F.DL1))
    srv = make_server(0); base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok, tok2 = create_token(1, "s" * 40, 3600), create_token(2, "s" * 40, 3600)
    errors = []
    try:
        pw = sync_playwright().start(); b = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    except Exception as e:
        print("SKIPPED chromium unavailable:", str(e)[:100], "(UNVERIFIED)"); return 0
    ctx = b.new_context(viewport={"width": 1300, "height": 900}); pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("dialog", lambda d: d.accept())

    # ── sign-in form ──
    pg.goto(f"{base}/app/lectures/{lid}")
    pg.wait_for_selector("#signin")
    check("no token -> a real <form> with a labelled password field, focused", pg.evaluate("document.activeElement.id")=="tok" and pg.locator("label[for=tok]").count()==1)
    pg.fill("#tok", "garbage"); pg.press("#tok", "Enter")
    pg.wait_for_function("document.querySelector('#signin') && /expired/.test(document.querySelector('#signin').innerText)", timeout=8000)
    check("bad token: Enter submits, returns to sign-in with a message, stored token cleared",
          pg.evaluate("sessionStorage.getItem('notiv_token')") is None, pg.inner_text("#root")[:150])
    pg.fill("#tok", tok); pg.press("#tok", "Enter")
    pg.wait_for_selector("#p-transcript .row", timeout=15000)
    check("valid token via Enter loads the lecture + Sign out visible", pg.is_visible("#signOut"))

    # ── notes ──
    pg.click("#tab-notes"); pg.wait_for_selector("#nComposer")
    check("empty state", "No notes yet" in pg.inner_text("#nList"))
    pg.click("#nSave"); check("empty note rejected with alert", "Write something" in pg.inner_text("#nStatus"))
    pg.fill("#nBody", "Remember: chain rule <b>matters</b>")
    pg.fill("#nFind", "chain rule"); pg.wait_for_selector("#nFound .row", timeout=8000)
    pg.locator("#nFound .row button").first.click()
    check("evidence chip appears after linking", pg.locator("#nDraftLinks .nChip").count()==1)
    pg.click("#nSave"); pg.wait_for_selector("#nList .nCard", timeout=8000)
    card = pg.locator("#nList .nCard").first
    check("note saved, text shown as text (markup not parsed), link shown", "<b>matters</b>" in card.inner_text() and pg.locator("#nList .nCard b").count()==0 and card.locator(".nLink").count()==1)
    pg.evaluate("document.querySelector('#media').currentTime=0")
    card.locator(".nLink .cite").click(); pg.wait_for_timeout(600)
    check("note's evidence link seeks the player", pg.evaluate("document.querySelector('#media').currentTime")>=0 and pg.evaluate("S.curSlide")!=None)
    card.get_by_role("button", name="Edit").click(); pg.fill(".nEdit", "edited note"); pg.locator("form.nCard").get_by_role("button", name="Save").click()
    pg.wait_for_selector("#nList .nCard >> text=edited note"); check("edit persists in the list", True)
    check("links survive a body-only edit", pg.locator("#nList .nCard .nLink").count()==1)
    pg.reload(); pg.wait_for_selector("#p-transcript .row"); pg.click("#tab-notes"); pg.wait_for_selector("#nList .nCard")
    check("note persists across reload", "edited note" in pg.inner_text("#nList"))
    pg.locator("#nList .nCard").first.get_by_role("button", name="Delete").click(); pg.wait_for_selector("#nList >> text=No notes yet")
    check("delete removes the note", True)

    # ── keyboard tabs ──
    pg.focus("#tab-notes"); pg.keyboard.press("End"); check("End key selects last tab", pg.get_attribute("#tab-search","aria-selected")=="true")
    pg.focus("#tab-search"); pg.keyboard.press("Home"); check("Home key selects first tab", pg.get_attribute("#tab-transcript","aria-selected")=="true")

    # ── central 401 + sign out ──
    pg.click("#tab-notes"); pg.evaluate("sessionStorage.setItem('notiv_token','expired-or-bad')")
    pg.fill("#nBody", "x"); pg.click("#nSave"); pg.wait_for_selector("#signin", timeout=8000)
    check("a 401 mid-session returns to sign-in (not a cryptic panel error)", True)
    pg.fill("#tok", tok); pg.press("#tok", "Enter"); pg.wait_for_selector("#p-transcript .row", timeout=15000)
    pg.click("#signOut"); pg.wait_for_selector("#signin")
    check("Sign out clears the token", pg.evaluate("sessionStorage.getItem('notiv_token')") is None)

    # ── privacy: other user cannot see lecture or notes ──
    pg.fill("#tok", tok2); pg.press("#tok", "Enter"); pg.wait_for_selector(".banner, h2")
    check("another user gets 'not found', not the lecture", "not found" in pg.inner_text("#root").lower() or "access" in pg.inner_text("#root").lower(), pg.inner_text("#root")[:120])

    # ── course page sign-in ──
    cp = ctx.new_page(); cp.on("pageerror", lambda e: errors.append(str(e)))
    cp.goto(f"{base}/app/courses/{course}"); cp.wait_for_selector("form input#tok")
    cp.evaluate("sessionStorage.removeItem('notiv_token')")
    cp.fill("#tok", tok); cp.press("#tok", "Enter"); cp.wait_for_selector("#root .row", timeout=10000)
    check("course page: sign-in form instead of '#token=' instruction", True)
    check("no script errors", not errors, errors)
    b.close(); pw.stop(); srv.shutdown()
    print(f"\n{'ALL PASSED' if not check.failed else 'FAILED: ' + ', '.join(check.failed)}")
    return 1 if check.failed else 0

if __name__ == "__main__":
    sys.exit(main())
