"""AI evaluation scorecard for Notiv: a small golden set + automatic metrics + pass thresholds, usable as a deploy gate.

    PYTHONPATH=.:core:backend .venv/bin/python tools/eval_ai_study.py            # offline: retrieval + Ask (quote mode)
    set -a; source .env; set +a
    PYTHONPATH=.:core:backend .venv/bin/python tools/eval_ai_study.py --live     # + Ask with the answer model + AI study material

What is measured (all automatic, all against the golden set below):
  Ask        retrieval hit rate     the evidence that answers the question is among what Ask cites
             answer correctness     the answer contains the expected fact (keyword match, case-insensitive)
             citation validity      every citation is real evidence of THIS lecture
             abstention accuracy    questions the lecture cannot answer get "not enough evidence" (no invention)
  Study      citation validity      every section / concept / card / question cites real evidence of the lecture
  (--live)   support rate           share of summary + definition sentences whose content words appear in their cited
                                    evidence (a lexical faithfulness proxy, NOT a human judgement)
             copy rate              share of section summaries that are verbatim excerpts (lower is better)
             topic coverage         reference topics that appear in the generated material
             quiz validity          4 distinct options, a valid answer index, cited
Each metric has a threshold; the script exits non-zero when one is missed. Live runs make a handful of provider calls.
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "core"), os.path.join(ROOT, "backend")]
os.environ.setdefault("NOTIV_AUTH_SECRET", "eval-" + "x" * 40)

GOLDEN = [
    {"title": "Caching", "pages": [
        ["Caching", "A cache stores frequently used data in memory to reduce database load.",
         "A cache hit means the data was found in the cache; a cache miss means it was not."],
        ["Write policies", "Write-through caches write to the cache and the database at the same time.",
         "Write-around caches write only to the database, so the first read after a write is a miss.",
         "Write-back caches write to the cache first and copy data to the database later."],
        ["Invalidation", "Cache invalidation removes stale entries when the underlying data changes."]],
     "topics": ["cache hit", "write-through", "write-around", "write-back", "invalidation"],
     "qa": [("What is a cache miss?", ["not"]), ("What does a write-through cache do?", ["same time", "database"]),
            ("What does cache invalidation remove?", ["stale"])],
     "unanswerable": ["Who won the 1998 football World Cup?", "What is the boiling point of mercury?"]},
    {"title": "Distributed data", "pages": [
        ["CAP theorem", "A distributed system can provide at most two of consistency, availability and partition tolerance."],
        ["Replication", "Replication keeps copies of data on several servers so the system survives a server failure."],
        ["Sharding", "Sharding splits one large table across several machines by a shard key."]],
     "topics": ["cap theorem", "replication", "sharding", "partition tolerance"],
     "qa": [("What does the CAP theorem say?", ["two"]), ("Why use replication?", ["failure"]),
            ("How does sharding split a table?", ["shard key"])],
     "unanswerable": ["What is the capital of Australia?"]},
    {"title": "Load balancing", "pages": [
        ["Load balancing", "A load balancer spreads incoming requests across several servers.",
         "Round robin sends each new request to the next server in turn.",
         "Least connections sends a request to the server with the fewest active connections."],
        ["Health checks", "The load balancer stops sending traffic to servers that fail health checks."]],
     "topics": ["round robin", "least connections", "health check"],
     "qa": [("What does round robin do?", ["next server"]), ("What happens to servers that fail health checks?", ["stops"])],
     "unanswerable": ["How many moons does Jupiter have?"]},
]

THRESHOLDS = {"ask_retrieval_hit": 0.9, "ask_answer_correct": 0.8, "ask_citation_valid": 1.0, "ask_abstention": 1.0,
              "study_citation_valid": 1.0, "study_support_rate": 0.8, "study_copy_rate_max": 0.2, "study_topic_coverage": 0.8,
              "study_quiz_valid": 1.0}
STOP = set("a an the and or of to in on by for with as at is are was were be it its this that these those from into than then "
           "so such can may will would should do does did not no only more most each which who what when where why how".split())


def words(s):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'-]*", (s or "").lower()) if w not in STOP and len(w) > 2]


def supported(sentence, evidence_text):
    """Lexical support: share of the sentence's content words (or their 5-letter stems) found in the cited evidence."""
    ws = words(sentence)
    if not ws:
        return 1.0
    ev = set(words(evidence_text))
    stems = {w[:5] for w in ev}
    return sum(1 for w in ws if w in ev or w[:5] in stems) / len(ws)


def sentences(t):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t or "") if len(s.split()) >= 4]


def build(env, g, tmp):
    from core.lecture.make_test_lecture import make_text_pdf
    pdf = make_text_pdf(os.path.join(tmp, "fx"), n=len(g["pages"]), slide_lines=g["pages"], name=g["title"].replace(" ", "_") + ".pdf")
    lid = env.lecture(deck=pdf, title=g["title"])
    env.process(lid)
    return lid


def eval_ask(env, lid, g, llm=None):
    from core.lecture.ask import AskService
    svc = AskService(env.repo, env.storage, llm=llm)
    ev = {e["id"]: e for e in env.repo.list_evidence(lid)}
    rows = []
    for q, keys in g["qa"]:
        a = svc._answer(lid, q, [])
        cited = [c if isinstance(c, int) else c.get("id", c.get("evidence_id")) for c in a.get("cited") or []]
        cited_text = " ".join(ev[i]["text"] for i in cited if i in ev).lower()
        rows.append({"q": q, "kind": "answerable", "grounded": bool(a.get("grounded")), "mode": a.get("mode"),
                     "hit": all(k.lower() in cited_text for k in keys),
                     "correct": all(k.lower() in (a.get("answer") or "").lower() for k in keys),
                     "citations_valid": all(i in ev for i in cited), "answer": (a.get("answer") or "")[:160]})
    for q in g["unanswerable"]:
        a = svc._answer(lid, q, [])
        rows.append({"q": q, "kind": "unanswerable", "abstained": not a.get("grounded"), "mode": a.get("mode"),
                     "answer": (a.get("answer") or "")[:160]})
    return rows


def eval_study(env, lid, g, chain):
    from core.lecture import ai_study
    r = ai_study.ensure(env.repo, lid, chain, force=True, max_wait=300)
    if r["status"] != "ok":
        return {"status": r["status"], "message": r.get("message")}
    ai = ai_study.load(env.repo, lid)
    ev = {e["id"]: e for e in env.repo.list_evidence(lid)}
    groups = [("sections", "evidence_ids"), ("flashcards", "evidence_ids"), ("quiz", "evidence_ids"), ("takeaways", "evidence_ids")]
    cites = [(i in ev) for grp, k in groups for it in ai[grp] for i in it[k]]
    cites += [(i in ev) for c in ai["concepts"] for i in c["mention_evidence_ids"] + c["definition_evidence_ids"]]
    claims = []
    for s in ai["sections"]:
        txt = " ".join(ev[i]["text"] for i in s["evidence_ids"] if i in ev)
        claims += [(x, supported(x, txt)) for x in sentences(s["summary"])]
    for c in ai["concepts"]:
        if c["definition"]:
            txt = " ".join(ev[i]["text"] for i in c["definition_evidence_ids"] if i in ev)
            claims += [(x, supported(x, txt)) for x in sentences(c["definition"]) or [c["definition"]]]
    all_ev = " ".join(e["text"] for e in ev.values()).lower()
    copied = [s for s in ai["sections"] if s["summary"].lower().strip(" .") in all_ev]
    blob = json.dumps(ai).lower()
    covered = [t for t in g["topics"] if t in blob]
    quiz_ok = [len(q["options"]) == 4 and len({o.lower() for o in q["options"]}) == 4 and 0 <= q["correct_index"] < 4 and q["evidence_ids"]
               for q in ai["quiz"]]
    return {"status": "ok", "provider": r.get("provider"), "model": r.get("model"),
            "citation_valid": sum(cites) / max(1, len(cites)),
            "support_rate": sum(1 for _, v in claims if v >= 0.6) / max(1, len(claims)),
            "unsupported_examples": [x for x, v in claims if v < 0.6][:3],
            "copy_rate": len(copied) / max(1, len(ai["sections"])),
            "topic_coverage": len(covered) / len(g["topics"]), "missing_topics": [t for t in g["topics"] if t not in covered],
            "quiz_valid": sum(quiz_ok) / max(1, len(quiz_ok)) if quiz_ok else 0.0,
            "counts": {k: len(ai[k]) for k in ("sections", "concepts", "flashcards", "quiz", "relations")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="use the configured answer model (makes real provider calls)")
    ap.add_argument("--json")
    a = ap.parse_args()
    from core.lecture import llm as llm_mod
    from core.lecture.testkit import Env
    tmp = tempfile.mkdtemp(prefix="notiv-eval-")
    os.environ["NOTIV_AI_STUDY"] = "auto" if a.live else "off"
    chain = None
    if a.live:
        chain = llm_mod.build_chain()
        diag = [d for d in llm_mod.diagnose(live=True) if d.get("configured")]
        print("providers:", ", ".join(f"{d['provider']}={'ok' if d.get('ok') else d.get('reason')}" for d in diag) or "none configured")
        if not any(d.get("ok") for d in diag):
            print("LIVE EVALUATION BLOCKED: no provider can answer (see reasons above). Running the offline part only.")
            chain = None
    env = Env(tmp)
    report = {"ask": [], "study": []}
    try:
        for g in GOLDEN:
            lid = build(env, g, tmp)
            report["ask"] += [{**r, "lecture": g["title"], "llm": False} for r in eval_ask(env, lid, g)]
            if chain:
                report["ask"] += [{**r, "lecture": g["title"], "llm": True} for r in eval_ask(env, lid, g, chain)]
                report["study"].append({"lecture": g["title"], **eval_study(env, lid, g, chain)})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    score = {}
    for mode in (False, True):
        rows = [r for r in report["ask"] if r["llm"] is mode]
        if not rows:
            continue
        ans, una = [r for r in rows if r["kind"] == "answerable"], [r for r in rows if r["kind"] == "unanswerable"]
        tag = "ask_llm" if mode else "ask_quotes"
        score[tag] = {"ask_retrieval_hit": sum(r["hit"] for r in ans) / len(ans), "ask_answer_correct": sum(r["correct"] for r in ans) / len(ans),
                      "ask_citation_valid": sum(r["citations_valid"] for r in ans) / len(ans), "ask_abstention": sum(r["abstained"] for r in una) / len(una)}
    st = [s for s in report["study"] if s.get("status") == "ok"]
    if st:
        avg = lambda k: sum(s[k] for s in st) / len(st)                                   # noqa: E731
        score["study"] = {"study_citation_valid": avg("citation_valid"), "study_support_rate": avg("support_rate"),
                          "study_copy_rate_max": max(s["copy_rate"] for s in st), "study_topic_coverage": avg("topic_coverage"),
                          "study_quiz_valid": avg("quiz_valid")}
    elif report["study"]:
        score["study"] = {"blocked": report["study"][0].get("message")}

    failed = []
    print("\n=== Notiv AI evaluation scorecard ===")
    for group, metrics in score.items():
        print(f"\n[{group}]")
        for k, v in metrics.items():
            if not isinstance(v, float):
                print(f"  {k}: {v}")
                continue
            th = THRESHOLDS[k]
            ok = v <= th if k.endswith("_max") else v >= th
            failed += [] if ok else [f"{group}.{k}"]
            print(f"  {k:<24} {v:6.1%}   threshold {'≤' if k.endswith('_max') else '≥'} {th:.0%}   {'PASS' if ok else 'FAIL'}")
    for r in report["ask"]:
        bad = (r["kind"] == "answerable" and not (r["hit"] and r["correct"])) or (r["kind"] == "unanswerable" and not r["abstained"])
        if bad:
            print(f"  ✗ [{'llm' if r['llm'] else 'quotes'}] {r['q']} -> {r['answer']}")
    for s in st:
        for x in s["unsupported_examples"]:
            print(f"  ✗ [study/{s['lecture']}] weakly supported: {x}")
    if "study" not in score:
        print("\n[study] not evaluated: run with --live and a working provider.")
    if a.json:
        json.dump({"score": score, "report": report, "thresholds": THRESHOLDS}, open(a.json, "w"), indent=1, default=str)
    print(f"\n{'ALL THRESHOLDS MET' if not failed else 'BELOW THRESHOLD: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
