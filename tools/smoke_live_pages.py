"""Check the pages and account features of a running Notiv deployment that tools/smoke_deploy.py does not cover.

    python tools/smoke_live_pages.py https://notiv.example.com

Checks: public and signed-in pages load, register, course delete removes its lectures, notebook PDF download contains
the notebook's text, forgot-password answers without a server error, notebook delete. Creates one throwaway account.
Used for the live verification recorded in docs/RESULTS_AND_TESTING.md (22 / 22 on 2026-11-22).
"""
import io
import secrets
import sys

sys.path.insert(0, "tools")
from pypdf import PdfReader
from smoke_deploy import Session


def main(base: str) -> int:
    s = Session(base, None)
    ok = []

    def chk(name, cond, detail=""):
        ok.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + name + ("" if cond else "  " + detail))

    for p in ["/", "/sign-in", "/register", "/forgot-password", "/static/help.html", "/static/docs.html",
              "/static/privacy.html", "/static/terms.html", "/static/ui/landing.js", "/static/assets/notiv.jpg"]:
        chk("page " + p, s.req("GET", p).status_code == 200)
    r = s.register(f"live-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(18))
    chk("register", r.status_code in (200, 201))
    for p in ["/app", "/app?view=library", "/app/notebooks", "/static/account.html", "/static/practice-quiz.html",
              "/static/study-companion.html"]:
        chk("app page " + p, s.req("GET", p).status_code == 200)
    cid = s.req("POST", "/courses", json={"name": "Live course"}).json()["course_id"]
    lid = s.req("POST", "/lectures", json={"title": "Live lecture", "course_id": cid}).json()["lecture_id"]
    chk("course delete", s.req("DELETE", f"/courses/{cid}").status_code == 204)
    chk("course's lecture gone", s.req("GET", f"/lectures/{lid}/overview").status_code == 404)
    nb = s.req("POST", "/notebooks", json={"title": "Live notebook"}).json()
    s.req("POST", f"/notebooks/{nb['id']}/items", json={
        "item_type": "flashcards", "title": "Cards",
        "content": {"items": [{"prompt": "What is S3?", "answer": "Object storage"}]}})
    r = s.req("GET", f"/notebooks/{nb['id']}/export")
    txt = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(r.content)).pages) if r.status_code == 200 else ""
    chk("notebook PDF download", r.status_code == 200 and r.content[:5] == b"%PDF-" and "Object storage" in txt,
        str(r.status_code))
    chk("forgot password answers clearly",
        s.req("POST", "/auth/password/forgot", json={"email": "x@example.com"}).status_code < 500)
    chk("delete notebook", s.req("DELETE", f"/notebooks/{nb['id']}").status_code == 204)
    print(f"{sum(ok)}/{len(ok)} passed")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1].rstrip("/")))
