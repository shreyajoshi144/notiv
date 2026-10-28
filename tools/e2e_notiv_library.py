"""Real Chromium smoke E2E for empty library -> upload -> lecture opening.

Run from repository root: PYTHONPATH=backend:. python tools/e2e_notiv_library.py
Uses a temporary SQLite database and a short-lived test token. Requires Playwright + Chromium.
"""
import json, os, socket, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SECRET = "notiv-e2e-temporary-secret-32-bytes-minimum"


def main():
    with tempfile.TemporaryDirectory(prefix="notiv-e2e-") as td:
        env = os.environ.copy()
        env.update({"PYTHONPATH": f"{ROOT / 'backend'}:{ROOT}", "NOTIV_AUTH_SECRET": SECRET,
                    "NOTIV_LECTURE_DB": str(Path(td) / "e2e.db"), "NOTIV_STORAGE_DIR": str(Path(td) / "objects"),
                    "NOTIV_INPROCESS_WORKER": "0", "NOTIV_ENV": "development"})
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", str(port)],
                                  cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Notiv server exited before startup: " + server.stderr.read().decode(errors="replace")[-2000:])
                try:
                    with urlopen(base + "/healthz", timeout=1) as r:
                        if r.status == 200: break
                except Exception: time.sleep(.2)
            else: raise RuntimeError("Notiv server did not become healthy")
            from server.auth_tokens import create_token
            token = create_token(777, SECRET, 3600)
            # A real upload must be a structurally accepted PDF. A tiny one-page PDF is enough to exercise API integration.
            pdf = Path(td) / "intro.pdf"
            pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]/Resources<<>>/Contents 4 0 R>>endobj\n4 0 obj<</Length 0>>stream\n\nendstream endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
            same_origin_errors = []
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium", args=["--no-sandbox"])
                page = browser.new_page(viewport={"width": 1365, "height": 900})
                page.add_init_script("sessionStorage.setItem(\'notiv_token\', " + json.dumps(token) + ")")
                page.on("pageerror", lambda e: same_origin_errors.append("pageerror: " + str(e)))
                page.on("requestfailed", lambda r: same_origin_errors.append(f"requestfailed {r.url}: {r.failure}" ) if r.url.startswith(base) else None)
                bad_responses=[]
                page.on("response", lambda r: bad_responses.append(f"{r.status} {r.url}") if r.url.startswith(base) and r.status >= 400 else None)
                page.goto(base + "/app", wait_until="domcontentloaded")
                page.get_by_role("heading", name="Make every lecture count.").wait_for(timeout=10000)
                page.get_by_role("heading", name="Your library starts here").wait_for(timeout=10000)
                page.locator("#uploadBtn").click()
                page.locator("#lectureTitle").fill("E2E lecture")
                page.locator("#lectureFile").set_input_files(str(pdf))
                page.get_by_role("button", name="Create lecture").click()
                page.wait_for_url("**/app/lectures/*", timeout=20000)
                page.locator("#title").filter(has_text="E2E lecture").wait_for(timeout=10000)
                # The viewer may report queued/processing/failed if optional OCR/transcription tools are absent;
                # opening the real persisted lecture and exposing its actual state is the tested contract.
                page.wait_for_timeout(1000)
                if page.locator("body").inner_text().strip() == "": raise AssertionError("Lecture viewer rendered empty body")
                if same_origin_errors: raise AssertionError("Browser runtime/request errors: " + " | ".join(same_origin_errors))
                if bad_responses: raise AssertionError("Same-origin HTTP errors: " + " | ".join(bad_responses))
                print("PASS: empty workspace rendered; real PDF uploaded; processing request accepted; navigated into persisted lecture viewer.")
                print("NOTE: processing completion is not asserted; worker/transcription availability is deployment-dependent.")
                browser.close()
        finally:
            server.terminate()
            try: server.wait(timeout=5)
            except subprocess.TimeoutExpired: server.kill()

if __name__ == "__main__": main()
