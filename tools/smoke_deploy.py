"""Smoke test a running Notiv deployment over HTTP, the way a browser uses it (cookie session + CSRF).

    python tools/smoke_deploy.py http://localhost:8080              # a local container
    python tools/smoke_deploy.py https://notiv.example.com          # a real deployment
    python tools/smoke_deploy.py http://localhost:8080 --as-proxy https://notiv.example.com
        # pretend to be an HTTPS reverse proxy in front of a plain-HTTP container (sends X-Forwarded-* and an https Origin)

Checks: health, readiness, register, sign in, upload + process PDF / PPTX / MP3 / MP4, transcript, summary, Ask with a
citation, study items, and that another account gets 404. Creates two throwaway accounts on the target; nothing else.
"""
import argparse, os, secrets, subprocess, sys, tempfile, time
from urllib.parse import urlparse

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "tools")]
from eval_format_matrix import pdf_reportlab, pptx_make, ff, _video  # noqa: E402

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return ok


def make_files(d):
    pages = [["Caching", "A cache is a small fast store that keeps frequently used data in memory.",
              "Caching means the database answers fewer requests.", "Latency is the time a request takes to complete."],
             ["Write-through caching", "A write through cache updates the cache and the database together.",
              "Write-back caching is a policy that writes to the database later.", "Important: stale data is the main risk of caching."],
             ["Eviction", "Eviction is the removal of old entries when the cache is full.",
              "LRU stands for least recently used.", "For example, a browser cache stores images so pages load faster."]]
    pdf = os.path.join(d, "caching.pdf"); pdf_reportlab(pdf, pages)
    pptx = os.path.join(d, "caching.pptx"); pptx_make(pptx, [(p[0], p[1]) for p in pages])
    wav = os.path.join(d, "talk.aiff")
    subprocess.run(["say", "-r", "170", "-o", wav, " ".join(" ".join(p[1:]) for p in pages)], check=True)
    mp3 = os.path.join(d, "caching.mp3"); ff("-i", wav, "-c:a", "libmp3lame", "-b:a", "96k", mp3)
    mp4 = _video(wav, os.path.join(d, "caching.mp4"))
    return {"pdf": (pdf, "application/pdf"),
            "pptx": (pptx, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            "mp3": (mp3, "audio/mpeg"), "mp4": (mp4, "video/mp4")}


class Session:
    def __init__(self, base, proxy_origin):
        headers = {}
        self.origin = base.rstrip("/")
        if proxy_origin:
            u = urlparse(proxy_origin)
            headers = {"X-Forwarded-Proto": u.scheme, "X-Forwarded-Host": u.netloc, "Host": u.netloc}
            self.origin = proxy_origin.rstrip("/")
        self.c = httpx.Client(base_url=base, timeout=120, headers=headers)
        self.plain = base.startswith("http://")  # Secure cookies are not sent over plain HTTP by httpx; carry them

    def _csrf(self):
        for ck in self.c.cookies.jar:
            if ck.name == "notiv_csrf":
                return ck.value
        return ""

    def _sync_cookies(self, r):
        if self.plain:
            for k, v in r.cookies.items():
                self.c.cookies.set(k, v)

    def req(self, method, path, **kw):
        h = kw.pop("headers", {})
        if method not in ("GET", "HEAD"):
            h = {"Origin": self.origin, "X-CSRF-Token": self._csrf(), **h}
        if self.plain and self.c.cookies:
            h["Cookie"] = "; ".join(f"{ck.name}={ck.value}" for ck in self.c.cookies.jar)
        r = self.c.request(method, path, headers=h, **kw)
        self._sync_cookies(r)
        return r

    def register(self, email, password):
        return self.req("POST", "/auth/register", json={"name": "Smoke Test", "email": email, "password": password,
                                                        "confirmation": password})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--as-proxy", metavar="ORIGIN")
    ap.add_argument("--only", default="pdf,pptx,mp3,mp4")
    ap.add_argument("--timeout", type=int, default=1200, help="seconds to wait for each lecture to process")
    a = ap.parse_args()
    kinds = [k for k in a.only.split(",") if k]

    print("== service")
    s = Session(a.base, a.as_proxy)
    check("healthz", s.req("GET", "/healthz").status_code == 200)
    rz = s.req("GET", "/readyz")
    body = rz.json()
    check("readyz ready", rz.status_code == 200 and body.get("status") == "ready",
          "; ".join(f"{k}: {v['detail']}" for k, v in body.get("checks", {}).items() if not v.get("ok")))
    print("  answer model:", body.get("checks", {}).get("answer_model", {}).get("detail"))
    check("landing page", s.req("GET", "/").status_code == 200)

    print("== accounts")
    pw = secrets.token_urlsafe(18)
    email = f"smoke-{secrets.token_hex(4)}@example.com"
    r = s.register(email, pw)
    check("register", r.status_code in (200, 201), f"{r.status_code} {r.text[:120]}")
    me = s.req("GET", "/auth/me")
    check("signed in after register", me.status_code == 200 and me.json().get("email") == email, str(me.status_code))
    s2 = Session(a.base, a.as_proxy)
    s2.register(f"smoke-{secrets.token_hex(4)}@example.com", secrets.token_urlsafe(18))
    r = s.req("POST", "/lectures", json={"title": "csrf probe"}, headers={"X-CSRF-Token": "wrong"})
    check("write without valid CSRF token is refused", r.status_code == 403, str(r.status_code))

    with tempfile.TemporaryDirectory() as d:
        files = make_files(d)
        for kind in kinds:
            print(f"== {kind}")
            path, mime = files[kind]
            t0 = time.monotonic()
            r = s.req("POST", "/lectures", json={"title": f"Smoke {kind}"})
            if not check(f"{kind}: create lecture", r.status_code in (200, 201), f"{r.status_code} {r.text[:160]}"):
                continue
            lid = r.json()["lecture_id"]
            with open(path, "rb") as fh:
                r = s.req("POST", f"/lectures/{lid}/assets", files={"file": (os.path.basename(path), fh, mime)})
            if not check(f"{kind}: upload", r.status_code == 201, f"{r.status_code} {r.text[:160]}"):
                continue
            r = s.req("POST", f"/lectures/{lid}/process")
            check(f"{kind}: start processing", r.status_code in (200, 202), str(r.status_code))
            st = {}
            while time.monotonic() - t0 < a.timeout:
                st = s.req("GET", f"/lectures/{lid}").json()
                if st.get("status") in ("done", "failed"):
                    break
                time.sleep(3)
            check(f"{kind}: processed", st.get("status") == "done",
                  f"{st.get('status')} in {time.monotonic() - t0:.0f}s {st.get('error') or ''}")
            if st.get("status") != "done":
                continue
            if kind in ("mp3", "mp4"):
                segs = s.req("GET", f"/lectures/{lid}/transcript").json().get("segments") or []
                text = " ".join(x["text"] for x in segs).lower()
                check(f"{kind}: transcript", len(segs) >= 1 and "cache" in text, f"{len(segs)} segments")
            su = s.req("GET", f"/lectures/{lid}/summary")
            check(f"{kind}: summary", su.status_code == 200 and (su.json().get("sections") or su.json().get("overview")),
                  f"mode={su.json().get('mode')}")
            r = s.req("POST", f"/lectures/{lid}/ask", json={"question": "What does a write through cache update?"})
            ans = r.json() if r.status_code == 200 else {}
            check(f"{kind}: Ask answers with a citation", r.status_code == 200 and ans.get("citations"),
                  f"{r.status_code} {str(ans.get('answer') or r.text)[:100]}")
            g = s.req("POST", f"/lectures/{lid}/study/generate", json={})
            items = s.req("GET", f"/lectures/{lid}/study/items").json().get("items") or []
            check(f"{kind}: study items", len(items) >= 1, f"{len(items)}; generate: {g.status_code} {g.text[:200]}")
            check(f"{kind}: another account gets 404", s2.req("GET", f"/lectures/{lid}").status_code == 404)

    r = s.req("POST", "/auth/password/logout")
    check("sign out", r.status_code in (200, 204), str(r.status_code))
    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed" + (": failed " + ", ".join(failed) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
