"""Live end-to-end regression for PDF study intelligence, through the real HTTP API, with REAL provider calls.

    set -a; source .env; set +a
    PYTHONPATH=.:core:backend .venv/bin/python tools/e2e_system_design.py ~/Downloads/systemdesgin.pdf [--keep]

Starts the API on a temporary database + storage, uploads the PDF, processes it (text-quality check, vision transcription,
AI study material), then generates and SAVES every notebook item type and checks what a student would get:
  * extraction: garbled text-layer pages are detected and re-read (or excluded and reported), never used as is
  * summary: coherent prose (not copied excerpts), an overview and takeaways, every section citing real evidence/pages
  * coverage: the topics the handwritten notes actually contain (load balancing, SQL vs NoSQL, CAP, caching, ...)
  * concepts, flashcards, quiz: present, cited, quiz answers valid; source-supported vs supplementary kept separate
  * concept map: topic/concept nodes AND labelled edges
  * provider diagnostics: which providers answered / failed and why
Prints a report and exits non-zero when a check fails. Costs real provider requests. Never prints keys.
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
# topics the System Design notes cover, each with the words that show it was covered
TOPICS = {
    "load balancing": ["load balanc"], "sql vs nosql": ["nosql"], "cap theorem": ["cap theorem", "partition toleran"],
    "caching": ["cach"], "replication": ["replicat"], "sharding / partitioning": ["shard", "partition"],
    "indexes": ["index"], "proxies": ["prox"], "queues": ["queue"], "consistent hashing": ["consistent hash"],
    "long polling": ["long poll", "long-poll"], "websockets": ["websocket", "web socket"],
    "server-sent events": ["server-sent", "server sent", "sse"],
}
CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def norm(s):
    return re.sub(r"\s+", " ", (s or "").lower())


def main():
    pdf = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "~/Downloads/systemdesgin.pdf")
    work = tempfile.mkdtemp(prefix="notiv-e2e-sd-")
    port = "8771"
    env = {**os.environ, "NOTIV_LECTURE_DB": os.path.join(work, "e2e.db"), "NOTIV_STORAGE_DIR": os.path.join(work, "obj"),
           "NOTIV_INPROCESS_WORKER": "1", "NOTIV_ENV": "development", "PYTHONPATH": f"{ROOT}:{ROOT}/core:{ROOT}/backend"}
    log = open(os.path.join(work, "server.log"), "w")
    srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/uvicorn"), "server.main:app", "--host", "127.0.0.1", "--port", port,
                            "--log-level", "warning"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        return run(pdf, port, env, work)
    finally:
        srv.terminate()
        srv.wait(10)
        print(f"\nserver log: {os.path.join(work, 'server.log')}")


def run(pdf, port, env, work):
    sys.path[:0] = [ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "backend")]
    from server.auth_tokens import create_token
    from core.lecture import llm
    c = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600,
                     headers={"Authorization": "Bearer " + create_token(1, os.environ["NOTIV_AUTH_SECRET"])})
    for _ in range(60):
        try:
            if c.get("/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    print("== providers (live diagnostics)")
    for d in llm.diagnose(live=True):
        print(f"  {d['provider']:<9} {d['model']:<26} ok={d.get('ok')} reason={d.get('reason')} listed={d.get('model_listed')}")
    ready = c.get("/readyz").json()["checks"]["answer_model"]
    print("  /readyz answer_model:", ready["detail"])

    print("\n== ingest + process")
    t0 = time.monotonic()
    lid = c.post("/lectures", json={"title": "System Design (e2e)"}).json()["lecture_id"]
    r = c.post(f"/lectures/{lid}/assets", files={"file": (os.path.basename(pdf), open(pdf, "rb"), "application/pdf")})
    check("upload accepted", r.status_code == 201, str(r.status_code))
    c.post(f"/lectures/{lid}/process").raise_for_status()
    st = {}
    while time.monotonic() - t0 < 3600:
        st = c.get(f"/lectures/{lid}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(5)
    check("processing finished", st.get("status") == "done", f"{st.get('status')} in {time.monotonic() - t0:.0f}s {st.get('error') or ''}")
    stages = {s["stage"]: s for s in st.get("stages") or []}
    ps = (stages.get("PROCESS_SLIDES") or {}).get("output") or {}
    an = ((stages.get("ANALYZE") or {}).get("output") or {}).get("ai_study") or {}
    print(f"  pages={ps.get('count')} low_quality={len(ps.get('low_quality_pages') or [])} vision={len(ps.get('vision_pages') or [])} "
          f"ocr={len(ps.get('ocr_pages') or [])} unreadable={ps.get('unreadable_pages')} by={ps.get('transcribed_by')}")
    print(f"  ai_study: {json.dumps({k: an.get(k) for k in ('status', 'provider', 'model', 'sections', 'concepts', 'flashcards', 'quiz', 'dropped', 'message')})}")
    check("garbled text layer detected", len(ps.get("low_quality_pages") or []) >= 30, f"{len(ps.get('low_quality_pages') or [])} pages")
    vis = len(ps.get("vision_pages") or []) + len(ps.get("ocr_pages") or [])
    check("garbled/handwritten pages re-read from the image", vis >= 30, f"{vis} of {ps.get('count')} pages")
    ov = c.get(f"/lectures/{lid}/overview").json()
    codes = [w["code"] for w in ov.get("warnings") or []]
    check("low-quality extraction reported to the user", "low_quality_text" in codes, ", ".join(codes))
    if ps.get("unreadable_pages"):
        check("unreadable pages reported", "pages_without_text" in codes, str(ps["unreadable_pages"]))
    ev = c.get(f"/lectures/{lid}/evidence").json() if c.get(f"/lectures/{lid}/evidence").status_code == 200 else None
    check("AI study material generated", an.get("status") == "ok", an.get("message") or an.get("status") or "")

    print("\n== summary")
    su = c.get(f"/lectures/{lid}/summary").json()
    secs = su.get("sections") or []
    print(f"  mode={su.get('mode')} provider={su.get('provider')}/{su.get('model')} sections={len(secs)} takeaways={len(su.get('takeaways') or [])}")
    print("  TITLE:", su.get("title"))
    print("  OVERVIEW:", (su.get("overview") or "")[:900])
    for s in secs[:6]:
        print(f"  · {s['title']} (p. {s.get('pages')}): {(s.get('body') or '')[:220]}")
    check("summary is AI-written (not excerpts)", su.get("mode") == "ai")
    words = len((su.get("overview") or "").split())
    check("document-level overview is real prose", words >= 60 and (su.get("overview") or "").count(".") >= 4, f"{words} words")
    check("sections cite real pages", secs and all(s.get("evidence") and s.get("pages") for s in secs), f"{len(secs)} sections")
    ev_texts = []
    for s in secs:
        ev_texts += [norm(e.get("text") or "") for e in s.get("evidence") or []]
    copied = sum(1 for s in secs if s.get("body") and any(norm(s["body"])[:120] in t for t in ev_texts))
    check("section summaries are not copied excerpts", copied <= max(1, len(secs) // 10), f"{copied}/{len(secs)} copied")

    print("\n== concepts / definitions")
    kn = c.get(f"/lectures/{lid}/knowledge").json()
    items = kn.get("items") or []
    concepts = [k for k in items if k["kind"] == "concept"]
    sup = [k for k in concepts if k.get("source_supported")]
    print(f"  counts={kn.get('counts')} concepts={len(concepts)} source-supported={len(sup)}")
    for k in concepts[:12]:
        print(f"  · {k['title']} | source: {(k.get('body') or '—')[:110]} | supplementary: {(k.get('supplementary_explanation') or '')[:80]}")
    check("concepts identified", len(concepts) >= 15, str(len(concepts)))
    check("every concept is cited", all(k.get("evidence") for k in concepts))
    check("source-supported definitions kept separate from supplementary explanations",
          all("supplementary_explanation" in k for k in concepts) and len(sup) >= 5, f"{len(sup)} supported")
    junk = [k["title"] for k in concepts if re.search(r"[÷¥€]|^\W+$", k["title"])]
    check("no garbage concept names", not junk, ", ".join(junk[:5]))

    print("\n== coverage of the notes' topics")
    ai_text = norm(json.dumps([su.get("overview"), [s["title"] + " " + (s.get("body") or "") for s in secs], [k["title"] + " " + (k.get("body") or "") for k in items]]))
    missing = []
    for t, keys in TOPICS.items():
        hit = any(k in ai_text for k in keys)
        if not hit:
            missing.append(t)
    check("covers the topics in the PDF", len(missing) <= 1, "missing: " + (", ".join(missing) or "none"))

    print("\n== flashcards + quiz")
    c.post(f"/lectures/{lid}/study/generate", json={})
    cards = c.get(f"/lectures/{lid}/study/items", params={"kind": "flashcard", "reveal": "true"}).json().get("items") or []
    mcq = c.get(f"/lectures/{lid}/study/items", params={"kind": "mcq", "reveal": "true"}).json().get("items") or []
    print(f"  flashcards={len(cards)} mcq={len(mcq)}")
    for it in cards[:5]:
        print(f"  Q: {it['prompt'][:110]}\n     A: {str(it.get('answer'))[:140]}")
    for it in mcq[:2]:
        print(f"  MCQ: {it['prompt'][:110]} | options: {[o['text'][:40] for o in it.get('options') or []]} | correct: {it.get('correct_option')}")
    check("flashcards generated", len(cards) >= 20, str(len(cards)))
    check("quiz questions generated", len(mcq) >= 10, str(len(mcq)))
    check("every flashcard/question cites evidence", all(it.get("evidence") for it in cards + mcq))
    check("quiz answers are valid options", all(it.get("correct_option") in {o["id"] for o in it.get("options") or []} for it in mcq))
    check("questions are not raw OCR copies", sum(1 for it in cards if re.search(r"[÷¥€]", it["prompt"])) == 0)

    print("\n== notebook: generate + save every type")
    nb = c.post("/notebooks", json={"title": "System design revision"}).json()
    c.post(f"/notebooks/{nb['id']}/sources", json={"source_type": "lecture", "source_id": lid}).raise_for_status()
    saved = {}
    for t in ("summary", "concepts", "definitions", "flashcards", "quiz", "mind_map"):
        r = c.post(f"/notebooks/{nb['id']}/generate", json={"source_id": lid, "output_type": t})
        saved[t] = r.json() if r.status_code == 201 else {"error": r.status_code, "detail": r.text[:200]}
        check(f"notebook {t} saved", r.status_code == 201, str(r.status_code))
    mm = (saved.get("mind_map") or {}).get("content") or {}
    nodes, edges = mm.get("nodes") or [], mm.get("edges") or []
    labelled = [e for e in edges if e.get("label") and e.get("label") != "covers"]
    print(f"  mind map: {len(nodes)} nodes, {len(edges)} edges ({len(labelled)} concept relations)")
    for e in labelled[:8]:
        a = next((n["text"] for n in nodes if n["id"] == e["from"]), "?"); b = next((n["text"] for n in nodes if n["id"] == e["to"]), "?")
        print(f"    {a} —{e['label']}→ {b}")
    check("mind map has nodes and connections", len(nodes) >= 10 and len(edges) >= len(nodes) // 2, f"{len(nodes)} nodes / {len(edges)} edges")
    check("mind map has labelled concept relations", len(labelled) >= 5, str(len(labelled)))
    check("map nodes reference pages", all((n.get("source") or {}).get("pages") is not None for n in nodes))
    got = c.get(f"/notebooks/{nb['id']}").json()
    check("saved items persisted", len(got.get("items") or []) >= 6, str(len(got.get("items") or [])))
    # canvas accepts the generated map (labels kept)
    r = c.put(f"/notebooks/{nb['id']}/canvas", json={"state": {"nodes": [{**n, "text": n["text"]} for n in nodes[:60]],
                                                               "edges": [e for e in edges if e["from"] in {n['id'] for n in nodes[:60]} and e["to"] in {n['id'] for n in nodes[:60]}]}})
    check("map can be placed on the notebook canvas", r.status_code == 200, str(r.status_code))

    print("\n== isolation")
    other = httpx.Client(base_url=c.base_url, headers={"Authorization": "Bearer " + create_token(2, os.environ["NOTIV_AUTH_SECRET"])})
    check("another user cannot read the summary", other.get(f"/lectures/{lid}/summary").status_code == 404)
    check("another user cannot read the notebook", other.get(f"/notebooks/{nb['id']}").status_code == 404)

    report = {"lecture_id": lid, "pages": ps, "ai_study": an, "summary": su, "knowledge": kn, "flashcards": cards, "quiz": mcq,
              "mind_map": mm, "checks": CHECKS}
    out = os.path.join(work, "report.json")
    json.dump(report, open(out, "w"), indent=1, default=str)
    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed. Full output: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
