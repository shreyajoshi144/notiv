"""End-to-end browser verification of Sprint 4 (Study mode + course page).

Builds REAL lectures (Deep Learning I+II in one course, Databases in another) through the actual pipeline, serves them
with server.dev_server (same handlers/auth/storage as the production routes) and drives headless Chromium:
concept review with citation chips that seek the player, mark-reviewed / delete, MCQ + flashcard + short-answer
practice with reveal and supporting evidence, progress tracking, item edit/regenerate/delete, markup-as-text, mobile
layout, and the course page (cross-lecture search, shared concepts, course isolation, access errors).

    python tools/verify_study_ui.py            # from the repo root; screenshots to $NOTIV_SHOTS (default /tmp)
"""
import os
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)
os.environ["NOTIV_AUTH_SECRET"] = "s" * 40

from playwright.sync_api import sync_playwright  # noqa: E402

from core import study_fixtures as F  # noqa: E402
from core.lecture import config  # noqa: E402
from core.lecture.make_test_lecture import make_lecture, make_text_pdf  # noqa: E402
from core.lecture.testkit import Env, segments  # noqa: E402
from server.auth_tokens import create_token  # noqa: E402
from server.dev_server import make_server  # noqa: E402

OUT = os.environ.get("NOTIV_SHOTS", tempfile.gettempdir())


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        check.failed.append(name)


check.failed = []


def near(t, target, after=1.5):
    return target - 0.3 <= t < target + after


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="notiv-study-ui-")
    mp4, _, _ = make_lecture(os.path.join(tmp, "fx"), [6, 9, 5, 7])
    webm = os.path.join(tmp, "fx", "lecture.webm")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp4, "-c:v", "libvpx", "-b:v", "1M", "-c:a", "libvorbis", webm], check=True)
    env = Env(tmp)
    config.configure(env.repo, env.storage)

    def lecture(user, title, segs, slides, course):
        lid = env.lecture(user, webm, make_text_pdf(os.path.join(tmp, "fx"), slide_lines=slides, name=title + ".pdf"), title=title, course_id=course)
        env.process(lid, segments(*segs))
        return lid
    c_dl, c_db, c_bob = env.lectures.create_course(1, "Deep Learning"), env.lectures.create_course(1, "Databases"), env.lectures.create_course(2, "Bob's course")
    dl1 = lecture(1, "DL1", F.DL1, F.DL1_SLIDES, c_dl)
    dl2 = lecture(1, "DL2", F.DL2, F.DL2_SLIDES, c_dl)
    lecture(1, "DB1", F.DB, F.DB_SLIDES, c_db)

    srv = make_server(0)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    tok, tok2 = create_token(1, "s" * 40, 3600), create_token(2, "s" * 40, 3600)
    errors = []

    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1360, "height": 900})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
        pg.on("dialog", lambda d: d.accept())
        pg.goto(f"{base}/app/lectures/{dl1}#token={tok}")
        pg.wait_for_selector("#p-transcript .row", timeout=15000)
        pg.wait_for_function("document.querySelector('#media').readyState>=1", timeout=15000)
        st = lambda: pg.evaluate("({t:document.querySelector('#media').currentTime,slide:S.curSlide})")
        prog = lambda: pg.inner_text("#stProg")

        pg.click("#tab-study"); pg.wait_for_selector("#stBody .gen", timeout=10000)
        check("progress strip starts at 0 practised, 0/7 concepts reviewed", "0/" in prog() and "concepts reviewed 0/7" in prog(), prog())
        names = pg.locator("#stBody .gen h3").all_inner_texts()
        check("concept review lists the lecture's real concepts", {"Backpropagation", "Regularization", "Learning rate", "Gradient Descent"} <= set(names), names)
        bp = pg.locator("#stBody .gen", has=pg.locator("h3", has_text="Backpropagation")).first
        check("concept card shows verbatim definition, formula and warning from the lecture",
              "computes gradients by applying the chain rule" in bp.inner_text() and "dL/dw = dL/dy * dy/dw" in bp.inner_text() and "Be careful" in bp.inner_text(), bp.inner_text()[:300])
        pg.evaluate("document.querySelector('#media').currentTime=1"); pg.wait_for_timeout(300)
        bp.locator(".cite", has_text="Slide 2 · 00:06").first.click(); pg.wait_for_timeout(700)
        s = st(); check("citation chip on a concept seeks the player to 6s / slide 2", near(s["t"], 6.0) and s["slide"] == 2, s)
        pg.screenshot(path=os.path.join(OUT, "study_concepts.png"))

        bp.get_by_role("button", name="Mark reviewed").click(); pg.wait_for_timeout(500)
        check("mark reviewed updates button and progress", "Reviewed ✓ ×1" in bp.inner_text() and "concepts reviewed 1/7" in prog(), prog())
        gd = pg.locator("#stBody .gen", has=pg.locator("h3", has_text="Gradient Descent")).first
        gd.get_by_role("button", name="Delete").click(); pg.wait_for_timeout(600)
        check("deleting a note removes it", "Gradient Descent" not in pg.locator("#stBody .gen h3").all_inner_texts())
        pg.get_by_role("button", name="Re-extract from lecture").click(); pg.wait_for_timeout(900)
        check("re-extracting does not bring a deleted note back", "Gradient Descent" not in pg.locator("#stBody .gen h3").all_inner_texts())

        # ── practice: MCQ ──
        pg.click("#sv-practice"); pg.select_option("#pk", "mcq"); pg.select_option("#pn", "5"); pg.click("#stStart")
        pg.wait_for_selector("#stCard .opt", timeout=10000)
        check("MCQ shows prompt + 4 options and no answer", pg.locator("#stCard .opt").count() == 4 and "Question 1 of 5" in pg.inner_text("#stCard"))
        pg.locator("#stCard .opt").first.click(); pg.wait_for_selector("#stNext")
        txt = pg.inner_text("#stAct")
        check("answer reveals result, marks the correct option, explanation and supporting evidence",
              ("Correct" in txt or "Not quite" in txt) and "✓" in txt and "The lecture says" in txt and "Supporting lecture evidence" in txt and "Professor said" in txt, txt[:300])
        pg.screenshot(path=os.path.join(OUT, "study_practice.png"))
        pg.locator("#stAct .support .cite").first.click(); pg.wait_for_timeout(600)
        check("evidence chip in an answer seeks the player", st()["t"] > 0.2)
        for _ in range(4):
            pg.click("#stNext"); pg.wait_for_selector("#stCard .opt"); pg.locator("#stCard .opt").nth(1).click(); pg.wait_for_selector("#stNext")
        pg.click("#stNext"); pg.wait_for_selector("text=Session complete", timeout=10000)
        check("session summary shows score out of 5", "of 5 correct" in pg.inner_text("#stBody"))
        check("progress strip counts the 5 practised items", "5/" in prog() and "items practised" in prog(), prog())

        # ── practice: flashcard + short answer ──
        pg.click("text=Practice again"); pg.select_option("#pk", "flashcard"); pg.select_option("#pn", "5"); pg.click("#stStart"); pg.wait_for_selector("#stShow")
        check("flashcard hides the answer until asked", "Answer from the lecture" not in pg.inner_text("#stCard"))
        pg.click("#stShow"); pg.wait_for_selector("[data-grade=know]")
        check("flashcard reveal shows verbatim answer + evidence", "Answer from the lecture" in pg.inner_text("#stCard") and "Supporting lecture evidence" in pg.inner_text("#stCard"))
        pg.click("[data-grade=know]"); pg.wait_for_selector("#stNext")
        check("self-grade is recorded", "Marked as known" in pg.inner_text("#stAct"))
        pg.click("#stNext"); pg.click("text=End session"); pg.select_option("#pk", "short_answer"); pg.select_option("#pn", "5"); pg.click("#stStart"); pg.wait_for_selector("#stResp")
        pg.fill("#stResp", "It is a vector of partial derivatives of the loss"); pg.click("#stShow"); pg.wait_for_selector("[data-grade=unsure]")
        pg.click("[data-grade=unsure]"); pg.wait_for_selector("#stNext")
        check("short answer self-graded; key points feedback offered when the response used any",
              "Marked unsure" in pg.inner_text("#stAct"))
        pg.click("text=End session")

        # ── items: edit / regenerate / delete ──
        pg.click("#sv-items"); pg.wait_for_selector("#stList .gen")
        n0 = pg.locator("#stList .gen").count()
        check("items list shows generated items marked as checked against evidence", n0 > 30 and "checked against lecture evidence" in pg.inner_text("#stList .gen"), n0)
        row = pg.locator("#stList .gen").first
        row.get_by_role("button", name="Edit").click()
        pg.fill("#stList .gen input", "<img src=x onerror=window.__xss=1> My edited prompt")
        pg.get_by_role("button", name="Save").click(); pg.wait_for_timeout(700)
        row = pg.locator("#stList .gen").first
        check("edited item is flagged as not machine-verified and shows markup as text",
              "edited by you" in row.inner_text() and "<img" in row.inner_text() and pg.evaluate("window.__xss===undefined && document.querySelectorAll('#stList img').length===0"))
        row.get_by_role("button", name="Regenerate").click(); pg.wait_for_timeout(900)
        row = pg.locator("#stList .gen").first
        check("regenerate restores the lecture-derived version", "edited by you" not in row.inner_text() and "My edited prompt" not in row.inner_text())
        row.get_by_role("button", name="Delete").click(); pg.wait_for_timeout(500)
        check("delete removes the item", pg.locator("#stList .gen").count() == n0 - 1)
        pg.get_by_role("button", name="Refresh from notes").click(); pg.wait_for_timeout(900)
        check("refreshing does not resurrect a deleted item", pg.locator("#stList .gen").count() == n0 - 1)
        check("no script errors during the lecture session", not errors, errors)

        for tab in ("transcript", "evidence", "moments", "summary", "ask", "study", "search"):          # tab hygiene across the whole workspace
            pg.click(f"#tab-{tab}"); pg.wait_for_timeout(250)
            vis = pg.evaluate("[...document.querySelectorAll('.panel')].filter(p=>getComputedStyle(p).display!=='none').map(p=>p.id)")
            stray = pg.evaluate("[...document.querySelectorAll('.panel')].filter(p=>getComputedStyle(p).display!=='none').some(p=>/(^|\\s)(null|undefined|false)(\\s|$)/.test(p.innerText))")
            check(f"tab '{tab}': exactly one panel visible and no stray null/undefined text", vis == [f"p-{tab}"] and not stray, (vis, stray))
        pg.click("#tab-study")
        pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
        check("mobile width: Study tab does not overflow", pg.evaluate("(()=>{const m=document.querySelector('main');return document.documentElement.scrollWidth<=window.innerWidth+1 && m.scrollWidth<=m.clientWidth+1})()"))
        pg.screenshot(path=os.path.join(OUT, "study_mobile.png"))

        # ── course page ──
        cp = b.new_page(viewport={"width": 1100, "height": 900}); cp.on("pageerror", lambda e: errors.append(str(e)))
        cp.goto(f"{base}/app/courses/{c_dl}#token={tok}"); cp.wait_for_selector("text=Concepts across lectures")
        cp.wait_for_selector("#root .row", timeout=10000)
        check("course page shows its two lectures and not the other course's", cp.locator("section", has_text="Lectures").first.inner_text().count("DL") == 2 and "DB1" not in cp.inner_text("#root"))
        check("course concepts merge 'Learning rate' across both lectures", "in 2 lectures" in cp.inner_text("#root"))
        cp.check("#shared"); cp.wait_for_timeout(500)
        check("shared-only filter keeps only multi-lecture concepts", cp.locator("section", has_text="Concepts across lectures").first.locator(".row").count() == 1)
        cp.fill("#q", "momentum"); cp.wait_for_selector("#results .row", timeout=5000)
        res = cp.inner_text("#results")
        check("course search finds DL2 knowledge and evidence with a link into the lecture", "DL2" in res and "DL1" not in res and cp.locator(f"#results a[href^='/app/lectures/{dl2}']").count() >= 1, res[:200])
        cp.fill("#q", "primary key"); cp.wait_for_selector("#results >> text=No matches", timeout=5000)
        check("another course's content never appears in this course's search", "No matches" in cp.inner_text("#results"), cp.inner_text("#results"))
        cp.screenshot(path=os.path.join(OUT, "study_course.png"))
        for url, tk, want, label in ((f"{base}/app/courses/{c_dl}#token={tok2}", None, "Course not found", "another user's course"),
                                     (f"{base}/app/courses/{c_bob}#token={tok}", None, "Course not found", "a course owned by someone else"),
                                     (f"{base}/app/courses/{c_dl}#token=garbage", None, "Sign in again", "a bad token (shows a sign-in form)")):
            q = b.new_page(); q.goto(url); q.wait_for_selector(".banner, form")
            shown = q.inner_text("#root")
            check(f"course page -> error state for {label}", want in shown, shown)
        check("no script errors on course pages", not errors, errors)
        b.close()
    srv.shutdown()
    print(f"\n{'ALL PASSED' if not check.failed else 'FAILED: ' + ', '.join(check.failed)}")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
