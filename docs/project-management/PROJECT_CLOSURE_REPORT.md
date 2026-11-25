# Notiv — Project closure report

| | |
|---|---|
| Project | Notiv, AI study workspace |
| Owner | Shreya Joshi |
| Period | 2026-06-26 → 2026-11-25 |
| Closure date | 2026-11-25 |
| Final state | Live at https://notiv-production.up.railway.app since 2026-11-22; 76 / 76 live checks |

## 1. Summary

Notiv set out to turn lecture files into study material that students can trust, with every answer linked to the page
or minute it came from. It shipped: all four formats work, AI output is grounded and checked, and the product runs
publicly. The main open work is to measure the live setup on real, long recordings and with the live AI scorecard
before real students rely on it.

## 2. Objectives vs outcomes

| # | Objective ([charter](PROJECT_CHARTER.md#2-objectives)) | Outcome | Met |
|---|---|---|---|
| O1 | Reliable handling of PDF, PPTX, MP3, MP4 | Format matrix 33 / 33 | ✅ |
| O2 | Grounded AI output | Citation validity 100 %, abstention 100 % (offline scorecard, 3 test lectures) | ✅ |
| O3 | Accurate transcription with timestamps | WER 0.0 % (MP3), 0.8 % (MP4) on ~45-second synthetic speech | ✅ (real speech: B-03) |
| O4 | Keep working when an AI provider fails | Fallback across 4 providers; extractive output with none | ✅ |
| O5 | Per-user privacy | 404 for other accounts in tests, Docker and live | ✅ |
| O6 | Public, verified demo | Live 2026-11-22; 76 / 76 | ✅ |

## 3. Deliverables

| Deliverable | Status |
|---|---|
| Web application, processing pipeline, worker, UI | ✅ delivered |
| Docker image, verified on SQLite and PostgreSQL + S3 | ✅ delivered |
| Live demo | ✅ delivered |
| 638 automated tests and the evaluation tools | ✅ delivered |
| Product, architecture, evaluation, deployment and design docs | ✅ delivered |
| Project management documents | ✅ delivered (this folder) |

## 4. Scope: planned vs delivered

| Change | Type | Reason | Ref |
|---|---|---|---|
| Live lecture recording | Deferred | Needs streaming infrastructure; uploads cover the need | D-05 |
| Formats limited to four | Cut | Test four properly rather than many partly | D-06 |
| Google sign-in | Removed | Setup and attack surface for little value | D-07 |
| Downloads: 8 formats → 1 PDF | Cut | Too many options, several poor; less to test | D-10 |
| Prototype code | Removed | Unused code is risk and confusion | D-11 |
| Course delete, pinned landing navigation | Added late (Sprint 14) | Found missing in owner review | [Changelog](../CHANGELOG.md) |

## 5. Quality summary

From the [QA test report](QA_TEST_REPORT.md): 636 tests passed (2 skipped), browser e2e 18 / 18, format matrix 33 / 33,
offline AI scorecard 100 %, deployment smoke test 41 / 41 on two setups, live checks 76 / 76. Nineteen defects were
found by these runs and fixed; none are known to be open. Six items are labelled **not verified**
([QA §6](QA_TEST_REPORT.md#6-not-verified)).

## 6. Lessons learned

**What went well**

* **Measure before claiming.** Every number is reported only after its run, with "not verified" written otherwise.
  The one unmeasured marketing claim found ("it's free") was removed.
* **Evaluation found real bugs.** The scorecard, format matrix, Docker smoke test and live runs found 19 defects that
  unit tests had missed, including two critical deployment blockers.
* **A durable base early.** The Sprint 1 evidence model and job system were never rewritten; later features built on
  them.
* **Hard cuts kept a solo project shippable.** Four formats, one export format, no social sign-in.

**What did not go well**

* **No user research.** Personas came from the owner's own experience as a student; no other students were interviewed
  or observed.
* **Built before validating.** Eight export formats were built and later cut to one.
* **Free AI quotas ran out mid-test**, leaving the handwritten run at 31 / 34.
* **Deployment settings caused five failures on launch day** (rejected Dockerfile line, undeployed variables, port
  mismatch, URL typos, full disk).

**What I would do differently**

* Talk to 5 students before Sprint 1 and run a usability test before the redesign.
* Prototype an export with users before building eight.
* Budget paid test credits for live AI runs.
* Write the go-live checklist before deploying, not after (now done: [checklist](GO_LIVE_CHECKLIST.md)).
* Record dates for every sprint.

## 7. Handover

| Item | Owner | Where |
|---|---|---|
| Open work, prioritised with acceptance criteria | Shreya Joshi | [Product backlog](PRODUCT_BACKLOG.md) (B-01 → B-16) |
| Open risks | Shreya Joshi | [Risk register](RISK_REGISTER.md) (R9, R11 open) |
| How to run, deploy and verify | — | [README](../../README.md), [Deployment](../DEPLOYMENT.md) |
| Hosting credit | Shreya Joshi | Railway: add a payment method or move host before the credit ends (R9) |

## 8. Sign-off

Closed by Shreya Joshi, product owner, on 2026-11-25.
