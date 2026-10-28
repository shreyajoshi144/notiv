"""Live check of each configured answer-model provider, one tiny call each (costs a fraction of a cent per provider).

    set -a; source .env; set +a; PYTHONPATH=.:core:backend .venv/bin/python tools/llm_smoke.py

For every provider in NOTIV_LLM_PROVIDERS that has a key: one plain-text call and one JSON-schema call, then the full
chain once. Prints provider, model, outcome and latency only — never keys, prompts or raw errors."""
import json
import sys
import time

from core.lecture import llm

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "evidence_ids"],
          "properties": {"answer": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "integer"}}}}
SYSTEM = "Answer ONLY from the evidence. Cite evidence ids like [E1]."
USER = "Evidence:\n[E1] Photosynthesis converts light into chemical energy.\n\nQuestion: What does photosynthesis convert?"


def timed(fn):
    t = time.monotonic()
    try:
        return fn(), None, time.monotonic() - t
    except Exception as e:                                   # noqa: BLE001 — reported as a reason code only
        return None, llm.classify(e), time.monotonic() - t


def main() -> int:
    st = llm.status()
    print("order:", " → ".join(st["order"]), "| active:", " → ".join(st["active"]) or "NONE")
    for r in st["providers"]:
        if not (r["configured"] and r["installed"]):
            print(f"  {r['provider']:<9} {r['model']:<24} skipped ({'no key' if not r['configured'] else 'SDK missing'})")
    ok = True
    for p in llm.build_chain().providers:
        text, err, dt = timed(lambda: p.complete(SYSTEM, USER, None, "answer"))
        good = err is None and "[E1]" in (text or "")
        print(f"  {p.name:<9} {p.model:<24} text: {'ok' if good else 'FAIL ' + (err or 'no [E1] citation')} ({dt:.1f}s)")
        out, err, dt = timed(lambda: json.loads(p.complete(SYSTEM, USER, SCHEMA, "answer")))
        good = err is None and isinstance(out, dict) and 1 in (out.get("evidence_ids") or [])
        print(f"  {'':<9} {'':<24} json: {'ok' if good else 'FAIL ' + (err or 'schema/citation mismatch')} ({dt:.1f}s)")
        ok = ok and good
    if st["active"]:
        done, err, dt = timed(lambda: llm.build_chain().complete(SYSTEM, USER))
        print(f"chain: {'answered by ' + done.provider if done else 'FAIL ' + str(err)} ({dt:.1f}s)")
    return 0 if ok and st["active"] else 1


if __name__ == "__main__":
    sys.exit(main())
