"""Live end-to-end check for recordings (MP3 and MP4), through the real HTTP API, with REAL transcription (local
faster-whisper) and REAL answer-model calls for the study material.

    set -a; source .env; set +a
    PYTHONPATH=.:core:backend .venv/bin/python tools/e2e_media.py [mp3|mp4|both]

The speech is generated with macOS `say` from a KNOWN script, so transcription accuracy is MEASURED (word error rate
against the script), not eyeballed. Each run makes one study generation per file (a handful of text-model calls).
Checks: transcript accuracy + timestamps inside the recording, AI summary is prose with transcript timestamps, concepts /
flashcards / quiz cited to transcript moments, connected concept map, notebook items saved, another user gets 404.
Never prints keys. Exits non-zero when a check fails.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = {
    "mp3": ("Today's lecture is about caching in distributed systems. A cache stores frequently used data in fast memory so "
            "that the database does not have to answer every request. When the requested data is in the cache we call it a "
            "cache hit, and when it is missing we call it a cache miss. There are three common write policies. A write "
            "through cache writes the data to the cache and to the database at the same time, which keeps them consistent "
            "but makes every write slower. A write around cache writes only to the database, so writes are fast but the "
            "first read after a write is a miss. A write back cache writes only to the cache and copies the data to the "
            "database later, which is fast but can lose data if the cache fails. Finally, cache invalidation is the process "
            "of removing stale entries when the underlying data changes. Remember, invalidation is one of the hardest "
            "problems in system design."),
    "mp4": ("In this video we compare two ways of scaling a service. Vertical scaling means adding more CPU and memory to a "
            "single server. It is simple, but one machine eventually reaches its limit. Horizontal scaling means adding more "
            "servers and spreading the requests between them with a load balancer. A load balancer can use round robin, "
            "which sends each new request to the next server in turn, or least connections, which sends it to the server "
            "with the fewest active requests. Horizontal scaling also improves availability, because the service keeps "
            "working when one server fails. The trade off is that the application must be stateless, or it must store "
            "sessions in a shared place such as a cache or a database."),
}
TOPICS = {"mp3": ["cache hit", "write through", "write around", "write back", "invalidation"],
          "mp4": ["vertical scaling", "horizontal scaling", "load balancer", "round robin", "least connections"]}
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower().replace("-", " "))


def wer(ref, hyp):
    r, h = words(ref), words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


def make_media(kind, work):
    aiff, out = os.path.join(work, kind + ".aiff"), os.path.join(work, "talk." + kind)
    subprocess.run(["say", "-r", "170", "-o", aiff, SCRIPTS[kind]], check=True)
    if kind == "mp3":
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff, "-c:a", "libmp3lame", "-b:a", "96k", out], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x223344:s=640x360:r=10", "-i", aiff,
                        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", out], check=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", out],
                               capture_output=True, text=True, check=True).stdout.strip())
    return out, dur


def run_one(c, other, kind, work):
    print(f"\n========== {kind.upper()}")
    path, dur = make_media(kind, work)
    print(f"  media: {os.path.basename(path)} {dur:.1f}s")
    t0 = time.monotonic()
    lid = c.post("/lectures", json={"title": f"E2E {kind}"}).json()["lecture_id"]
    r = c.post(f"/lectures/{lid}/assets", files={"file": (os.path.basename(path), open(path, "rb"), "audio/mpeg" if kind == "mp3" else "video/mp4")})
    check(f"{kind}: upload accepted", r.status_code == 201, f"{r.status_code} type={r.json().get('type')}")
    c.post(f"/lectures/{lid}/process").raise_for_status()
    st = {}
    while time.monotonic() - t0 < 1800:
        st = c.get(f"/lectures/{lid}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(3)
    check(f"{kind}: processed", st.get("status") == "done", f"{st.get('status')} in {time.monotonic() - t0:.0f}s {st.get('error') or ''}")
    stages = {s["stage"]: s for s in st.get("stages") or []}
    an = ((stages.get("ANALYZE") or {}).get("output") or {}).get("ai_study") or {}
    print(f"  ai_study: {json.dumps({k: an.get(k) for k in ('status', 'provider', 'model', 'sections', 'concepts', 'flashcards', 'quiz', 'message')})}")

    tr = c.get(f"/lectures/{lid}/transcript", params={"limit": 1000}).json()
    segs = tr.get("segments") or []
    text = " ".join(s["text"] for s in segs)
    w = wer(SCRIPTS[kind], text)
    print(f"  transcript: {len(segs)} segments; WER vs script = {w:.1%}")
    print("  first lines:", " | ".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segs[:3]))
    check(f"{kind}: transcript produced", len(segs) >= 3, str(len(segs)))
    check(f"{kind}: transcript accuracy (WER <= 15%)", w <= 0.15, f"{w:.1%}")
    check(f"{kind}: timestamps ordered and inside the recording",
          all(0 <= s["start"] <= s["end"] <= dur + 0.5 for s in segs) and all(a["start"] <= b["start"] for a, b in zip(segs, segs[1:])))

    su = c.get(f"/lectures/{lid}/summary").json()
    secs = su.get("sections") or []
    print(f"  summary mode={su.get('mode')} {su.get('provider')}/{su.get('model')} sections={len(secs)}")
    print("  OVERVIEW:", (su.get("overview") or "")[:600])
    for s in secs:
        print(f"   · {s['title']} [{s.get('start')}–{s.get('end')}s]: {(s.get('body') or '')[:200]}")
    check(f"{kind}: AI summary (not excerpts)", su.get("mode") == "ai", an.get("message") or "")
    check(f"{kind}: overview is prose", len((su.get("overview") or "").split()) >= 40)
    check(f"{kind}: sections carry transcript moments", secs and all(s.get("times") and s.get("start") is not None for s in secs))
    check(f"{kind}: section times are real transcript times",
          all(set(s.get("times") or []) <= {round(x["start"], 1) for x in segs} for s in secs))
    alltext = json.dumps(su).lower()
    missing = [t for t in TOPICS[kind] if t.replace(" ", "") not in alltext.replace(" ", "").replace("-", "")]
    check(f"{kind}: covers the spoken topics", len(missing) <= 1, "missing: " + (", ".join(missing) or "none"))

    kn = c.get(f"/lectures/{lid}/knowledge").json()
    concepts = [k for k in kn.get("items") or [] if k["kind"] == "concept"]
    for k in concepts[:8]:
        print(f"   concept: {k['title']} @ {k.get('start')}s | source: {(k.get('body') or '—')[:100]} | supplementary: {(k.get('supplementary_explanation') or '')[:60]}")
    check(f"{kind}: concepts cited to transcript moments", concepts and all(k.get("evidence") and k.get("start") is not None for k in concepts), str(len(concepts)))
    c.post(f"/lectures/{lid}/study/generate", json={})
    cards = c.get(f"/lectures/{lid}/study/items", params={"kind": "flashcard", "reveal": "true"}).json().get("items") or []
    mcq = c.get(f"/lectures/{lid}/study/items", params={"kind": "mcq", "reveal": "true"}).json().get("items") or []
    for it in cards[:4]:
        print(f"   Q: {it['prompt'][:100]}\n      A: {str(it.get('answer'))[:120]}")
    check(f"{kind}: flashcards + quiz generated", len(cards) >= 3 and len(mcq) >= 2, f"{len(cards)} cards, {len(mcq)} questions")
    check(f"{kind}: every study item seeks to a transcript moment",
          all(e.get("citation", {}).get("seek_seconds") is not None for it in cards + mcq for e in it.get("evidence") or []))

    nb = c.post("/notebooks", json={"title": f"{kind} revision"}).json()
    c.post(f"/notebooks/{nb['id']}/sources", json={"source_type": "lecture", "source_id": lid}).raise_for_status()
    saved = {t: c.post(f"/notebooks/{nb['id']}/generate", json={"source_id": lid, "output_type": t}) for t in ("summary", "flashcards", "quiz", "mind_map")}
    check(f"{kind}: notebook items saved", all(r.status_code == 201 for r in saved.values()), str({t: r.status_code for t, r in saved.items()}))
    mm = saved["mind_map"].json().get("content") or {} if saved["mind_map"].status_code == 201 else {}
    rel = [e for e in mm.get("edges") or [] if e.get("label") and e["label"] != "covers"]
    names = {n["id"]: n["text"] for n in mm.get("nodes") or []}
    print(f"  map: {len(mm.get('nodes') or [])} nodes, {len(mm.get('edges') or [])} edges; relations:",
          "; ".join(f"{names.get(e['from'])} —{e['label']}→ {names.get(e['to'])}" for e in rel[:6]))
    check(f"{kind}: concept map is connected", len(mm.get("edges") or []) >= len(mm.get("nodes") or []) // 2 and rel, f"{len(rel)} relations")
    check(f"{kind}: map nodes carry transcript times", all((n.get("source") or {}).get("times") for n in mm.get("nodes") or []))
    check(f"{kind}: another user gets 404", other.get(f"/lectures/{lid}/summary").status_code == 404)
    return {"lecture_id": lid, "wer": w, "ai_study": an}


def main():
    kinds = ["mp3", "mp4"] if (len(sys.argv) < 2 or sys.argv[1] == "both") else [sys.argv[1]]
    work = tempfile.mkdtemp(prefix="notiv-e2e-media-")
    port = "8772"
    env = {**os.environ, "NOTIV_LECTURE_DB": os.path.join(work, "e2e.db"), "NOTIV_STORAGE_DIR": os.path.join(work, "obj"),
           "NOTIV_INPROCESS_WORKER": "1", "NOTIV_ENV": "development", "PYTHONPATH": f"{ROOT}:{ROOT}/core:{ROOT}/backend"}
    log = open(os.path.join(work, "server.log"), "w")
    srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/uvicorn"), "server.main:app", "--host", "127.0.0.1", "--port", port,
                            "--log-level", "warning"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        sys.path[:0] = [ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "backend")]
        from server.auth_tokens import create_token
        sec = os.environ["NOTIV_AUTH_SECRET"]
        c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600, headers={"Authorization": "Bearer " + create_token(1, sec)})
        other = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, headers={"Authorization": "Bearer " + create_token(2, sec)})
        for _ in range(60):
            try:
                if c.get("/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        print("answer model:", c.get("/readyz").json()["checks"]["answer_model"]["detail"])
        out = {k: run_one(c, other, k, work) for k in kinds}
        json.dump({"results": out, "checks": CHECKS}, open(os.path.join(work, "report.json"), "w"), indent=1, default=str)
    finally:
        srv.terminate()
        srv.wait(10)
    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed. Report + server log: {work}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
