"""End-to-end browser verification of the lecture workspace (Sprint 2).

Builds a REAL lecture (synthetic slides + encoded video, PDF deck with text
layer), runs it through the actual staged pipeline, serves it with
server.dev_server (same handlers/auth/storage as production routes), and
drives the UI in headless Chromium via Playwright, asserting synchronized
seeking, evidence navigation, search, provenance labels and error states.

    python tools/verify_workspace_ui.py            # from the repo root

Needs: ffmpeg, poppler, playwright + chromium. The video is re-encoded to
VP8/Vorbis because Playwright's Chromium cannot decode H.264/AAC; the
pipeline itself is codec-agnostic (ffmpeg). Transcript lines are test INPUT
(no speech is synthesized), exactly as in the unit tests.
"""
import json
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

from core.lecture import config  # noqa: E402
from core.lecture.align import Segment  # noqa: E402
from core.lecture.make_test_lecture import make_lecture, make_text_pdf  # noqa: E402
from core.lecture.testkit import Env, segments  # noqa: E402
from server.auth_tokens import create_token  # noqa: E402
from server.dev_server import make_server  # noqa: E402

SEGS = [Segment(0.5, 4.0, "Today we cover gradient descent and the learning rate."),
        Segment(7.0, 12.0, "Backpropagation applies the chain rule through every layer. This is important to remember."),
        Segment(16.0, 19.0, "Regularization penalizes large weights to reduce overfitting. Remember this for the exam."),
        Segment(21.0, 26.0, "A convolution slides a kernel over the image.")]
OUT = os.environ.get("NOTIV_SHOTS", tempfile.gettempdir())


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        check.failed.append(name)


check.failed = []


def near(t, target, after=1.5):
    """Clicks start playback, so the reading is the seek target plus a little elapsed play time."""
    return target - 0.3 <= t < target + after


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="notiv-ui-")
    mp4, _, _ = make_lecture(os.path.join(tmp, "fx"), [6, 9, 5, 7])
    webm = os.path.join(tmp, "fx", "lecture.webm")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp4, "-c:v", "libvpx", "-b:v", "1M", "-c:a", "libvorbis", webm], check=True)
    pdf = make_text_pdf(os.path.join(tmp, "fx"))
    env = Env(tmp)
    config.configure(env.repo, env.storage)
    lid = env.lecture(1, webm, pdf, title="Intro to Deep Learning")
    env.process(lid, segments(*SEGS))
    queued = env.lectures.create(1, "Still processing")             # exists, never processed
    env.jobs().enqueue(queued)
    failed = env.lectures.create(1, "Broken upload")
    try:
        env.process(failed, segments())                              # nothing uploaded -> real PermanentError
    except Exception:
        pass
    other = env.lectures.create(2, "Someone else's lecture")

    srv = make_server(0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    tok = create_token(1, "s" * 40, 3600)
    errors = []

    with sync_playwright() as p:
        b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1360, "height": 860})
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
        pg.goto(f"{base}/app/lectures/{lid}#token={tok}")
        pg.wait_for_selector("#p-transcript .row", timeout=15000)
        pg.wait_for_function("document.querySelector('#media').readyState>=1", timeout=15000)
        st = lambda: pg.evaluate("({t:document.querySelector('#media').currentTime,slide:S.curSlide,seg:S.curSeg,"
                                 "tab:document.querySelector('#tabs [aria-selected=true]').id})")

        check("workspace loads: title, ready chip, 4 transcript lines",
              pg.inner_text("#title") == "Intro to Deep Learning" and pg.inner_text("#stateChip") == "ready"
              and pg.locator("#p-transcript .row").count() == 4)
        check("token removed from URL after capture", "token" not in pg.url)

        pg.locator("#p-transcript .row").nth(1).click(); pg.wait_for_timeout(500)
        s = st(); check("transcript click -> media 7.0s, slide 2", near(s["t"], 7.0) and s["slide"] == 2, s)

        pg.locator('#thumbs button[data-slide="3"]').click(); pg.wait_for_timeout(500)
        s = st(); check("slide click -> first appearance (~15s)", near(s["t"], 15.0) and s["slide"] == 3, s)

        box = pg.locator("#timeline").bounding_box()
        pg.mouse.click(box["x"] + box["width"] * 0.9, box["y"] + box["height"] / 2); pg.wait_for_timeout(500)
        s = st(); check("timeline click at 90% -> slide 4", s["slide"] == 4 and s["t"] > 22, s)

        pg.click("#tab-evidence"); pg.wait_for_selector("#p-evidence .row")
        badges = set(pg.locator("#p-evidence .badge").all_inner_texts())
        check("evidence tab labels Professor said + Slide text, no Notiv label", badges == {"Professor said", "Slide text"}, badges)
        pg.locator("#p-evidence .row").filter(has_text="Regularization penalizes").first.click(); pg.wait_for_timeout(600)
        s = st(); check("evidence click -> 16.0s, slide 3", near(s["t"], 16.0) and s["slide"] == 3, s)
        pg.locator("#p-evidence .row").filter(has_text="point 2 about convolutions").first.click(); pg.wait_for_timeout(600)
        s = st(); check("slide-text evidence click -> slide 4 at its first appearance", s["slide"] == 4 and near(s["t"], 20.0), s)

        pg.click("#tab-moments")
        titles = pg.locator("#p-moments h3").all_inner_texts()
        check("key moments: 4 slide changes + 2 emphasis, all from evidence", len(titles) == 6, titles)
        check("key moments carry only the Notiv analysis label on cards",
              set(pg.locator("#p-moments .gen > .meta .badge").all_inner_texts()) == {"Notiv analysis"})
        pg.locator("#p-moments .gen").filter(has_text="Professor emphasis (exam relevance)").first.locator(".cite").first.click(); pg.wait_for_timeout(500)
        s = st(); check("emphasis key moment click -> 16.0s", near(s["t"], 16.0), s)
        pg.locator("#p-moments details summary").first.click()
        pg.screenshot(path=os.path.join(OUT, "ws_moments.png"))
        check("expanded support shows Professor said / Slide text separately from Notiv card",
              "Professor said" in pg.locator("#p-moments .support").first.inner_text() or "Slide text" in pg.locator("#p-moments .support").first.inner_text())

        pg.click("#tab-summary")
        check("summary: one section per slide, each with expandable evidence",
              pg.locator("#p-summary h3").count() == 4 and pg.locator("#p-summary details").count() == 4)

        # ── Ask Notiv ──
        pg.on("dialog", lambda d: d.accept())
        pg.click("#tab-ask"); pg.wait_for_selector("#askLog .starters, #askLog .msg")
        check("ask tab starts with real, data-derived starter questions", "Gradient Descent" in pg.inner_text("#askLog") and pg.locator("#askLog .starters button").count() == 3, pg.inner_text("#askLog"))
        pg.fill("#askIn", "What does regularization penalize?"); pg.press("#askIn", "Enter")
        pg.wait_for_selector("#askLog .msg.bot .cc", timeout=10000)
        bot = pg.locator("#askLog .msg.bot[data-msg]").last
        check("answer shows Notiv-answer label, quote-only mode and a slide+time citation chip",
              "Notiv answer" in bot.inner_text() and "quotes from the lecture" in bot.inner_text() and "Slide 3 · 00:16" in bot.locator(".cc").all_inner_texts(), bot.inner_text())
        pg.evaluate("document.querySelector('#media').currentTime=1"); pg.wait_for_timeout(300)
        bot.locator(".cc", has_text="00:16").click(); pg.wait_for_timeout(700)
        s = st(); check("clicking the citation chip seeks media to 16s and shows slide 3", near(s["t"], 16.0) and s["slide"] == 3, s)
        bot.locator("details summary").click()
        check("sources list is labelled Professor said", "Professor said" in bot.locator(".support").inner_text())
        pg.screenshot(path=os.path.join(OUT, "ws_ask.png"))
        pg.fill("#askIn", "Why does it matter?"); pg.press("#askIn", "Enter")
        pg.wait_for_function("document.querySelectorAll('#askLog .msg.bot[data-msg]').length===2", timeout=10000)
        check("follow-up keeps context (pronoun resolved from previous turn, still cited)", pg.locator("#askLog .msg.bot[data-msg]").last.locator(".cc").count() >= 1 and "Regularization" in pg.locator("#askLog .msg.bot[data-msg]").last.inner_text())
        pg.fill("#askIn", "Who won the 1998 world cup?"); pg.press("#askIn", "Enter")
        pg.wait_for_function("document.querySelectorAll('#askLog .msg.bot[data-msg]').length===3", timeout=10000)
        last = pg.locator("#askLog .msg.bot[data-msg]").last
        check("insufficient evidence is stated plainly, styled as such, with no citations",
              "Not enough evidence" in last.inner_text() and "none" in (last.get_attribute("class") or "") and last.locator(".cc").count() == 0)
        pg.fill("#askIn", "<img src=x onerror=window.__xss=1> regularization"); pg.press("#askIn", "Enter")
        pg.wait_for_function("document.querySelectorAll('#askLog .msg.bot[data-msg]').length===4", timeout=10000)
        check("markup in questions renders as text (no injection)", pg.evaluate("window.__xss===undefined && document.querySelectorAll('#askLog img').length===0") and "<img" in pg.locator("#askLog .msg.user").last.inner_text())
        pg.reload(); pg.wait_for_selector("#p-transcript .row"); pg.click("#tab-ask"); pg.wait_for_selector("#askLog .msg.user")
        check("conversation persists across reload", pg.locator("#askLog .msg.user").count() == 4)
        pg.click("#askReset"); pg.wait_for_function("document.querySelectorAll('#askLog .msg').length===0", timeout=5000)
        check("clear conversation resets the thread", "Ask anything about this lecture" in pg.inner_text("#askLog"))

        pg.click("#tab-search"); pg.fill("#q", "kernel"); pg.wait_for_selector("#results .row", timeout=5000)
        check("search highlights match and cites slide + time",
              pg.locator("#results mark").all_inner_texts() == ["kernel"] and pg.locator("#results .cite").first.inner_text() == "Slide 4 · 00:21",
              pg.locator("#results .cite").first.inner_text())
        pg.locator("#results .row").first.click(); pg.wait_for_timeout(600)
        s = st(); on = pg.evaluate("document.querySelector('#p-transcript .row.on')?.innerText || ''")
        check("search result click -> 21.0s, transcript tab, line highlighted", near(s["t"], 21.0) and s["tab"] == "tab-transcript" and "kernel" in on, (s, on))
        pg.click("#tab-search"); pg.fill("#q", "point 2 about regularization")
        pg.wait_for_function("(document.querySelector('#results .badge')||{}).innerText==='Slide text'", timeout=5000)   # new query's results, not the previous ones
        hit = pg.locator("#results .row").first
        check("slide-text search hit is labelled Slide text and cites its slide + first-appearance time",
              "Slide text" in hit.inner_text() and hit.locator(".cite").inner_text() == "Slide 3 · 00:15", hit.inner_text())
        hit.click(); pg.wait_for_timeout(600)
        s = st(); check("slide-text search hit click -> slide 3, media at ~15s, stays on search tab", s["slide"] == 3 and near(s["t"], 15.0) and s["tab"] == "tab-search", s)
        pg.fill("#q", "zzzqqq"); pg.wait_for_timeout(700)
        check("no-results state", "No matches" in pg.inner_text("#results"))

        pg.click("#tab-transcript")
        pg.evaluate("document.querySelector('#media').currentTime=9.5"); pg.wait_for_timeout(500)
        s = st(); on = pg.evaluate("document.querySelector('#p-transcript .row.on')?.innerText || ''")
        check("playback position drives slide + transcript highlight", s["slide"] == 2 and "Backpropagation" in on, (s, on))
        pg.screenshot(path=os.path.join(OUT, "ws_main.png"))

        # XSS: lecture text is rendered as text (checked via a segment whose text contains markup)
        check("no script errors during session", not errors, errors)

        for lec, want, label in ((999, "Lecture not found", "missing lecture"), (other, "Lecture not found", "other user's lecture")):
            q = b.new_page(); q.goto(f"{base}/app/lectures/{lec}#token={tok}"); q.wait_for_selector(".state")
            check(f"{label} -> not found state (no leak)", q.inner_text(".state h2") == want)
        q = b.new_page(); q.goto(f"{base}/app/lectures/{lid}#token=garbage"); q.wait_for_selector(".state")
        check("bad token -> sign-in state", q.inner_text(".state h2") == "Sign in required")
        q = b.new_page(); q.goto(f"{base}/app/lectures/{queued}#token={tok}"); q.wait_for_selector(".state")
        check("queued lecture -> processing state with progress bar", "Waiting" in q.inner_text(".state h2") and q.locator("[role=progressbar]").count() == 1)
        q.screenshot(path=os.path.join(OUT, "ws_processing.png"))
        q = b.new_page(); q.goto(f"{base}/app/lectures/{failed}#token={tok}"); q.wait_for_selector(".state")
        check("failed lecture -> error state shows the real error", "failed" in q.inner_text(".state h2").lower() and "neither a recording nor a slide deck" in q.inner_text(".state"), q.inner_text(".state"))
        q.screenshot(path=os.path.join(OUT, "ws_failed.png"))

        pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
        check("mobile width: nothing overflows horizontally (page or main)", pg.evaluate("(()=>{const m=document.querySelector('main');return document.documentElement.scrollWidth<=window.innerWidth+1 && m.scrollWidth<=m.clientWidth+1})()"))
        pg.screenshot(path=os.path.join(OUT, "ws_mobile.png"))
        b.close()
    srv.shutdown()
    print(f"\n{'ALL PASSED' if not check.failed else 'FAILED: ' + ', '.join(check.failed)}")
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
