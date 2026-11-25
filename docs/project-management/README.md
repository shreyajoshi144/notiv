# Notiv — Project management documents

How Notiv was planned, run, tested, released and closed. Notiv is a solo project: **Shreya Joshi** was product owner,
project manager, engineer and tester.

> **How these documents were made.** They were compiled at project close (2026-11-25) from the project's own records:
> git history, [sprint notes](../SPRINTS.md), the [changelog](../CHANGELOG.md) and the
> [test log](../RESULTS_AND_TESTING.md). They record what happened; they were not written before the work started.
> Every number links back to the run that produced it.

## Read in this order

| # | Document | Answers |
|---|---|---|
| 1 | [Case study](CASE_STUDY.md) | The one-page story: problem, users, what was cut, how quality was measured |
| 2 | [Project charter](PROJECT_CHARTER.md) | Why the project exists, scope, success criteria, constraints |
| 3 | [PRD](PRD.md) | What the product must do, with acceptance criteria and delivery status |
| 4 | [Roadmap](ROADMAP.md) | Phases delivered, and what comes now, next and later |
| 5 | [Milestones](MILESTONES.md) | Each milestone's date, exit criteria and evidence |
| 6 | [Product backlog](PRODUCT_BACKLOG.md) | Delivered epics and the prioritised open items |
| 7 | [Risk register](RISK_REGISTER.md) | Risks, scores, mitigations and which ones actually happened |
| 8 | [Decision log](DECISION_LOG.md) | Key decisions and scope changes, with the reasons |
| 9 | [QA test report](QA_TEST_REPORT.md) | What was tested, results, defects found, release recommendation |
| 10 | [Go-live checklist](GO_LIVE_CHECKLIST.md) | Release readiness for the Railway launch |
| 11 | [Project closure report](PROJECT_CLOSURE_REPORT.md) | Goals vs outcomes, lessons learned, handover |

## Key facts (the same in every document)

| Fact | Value | Source |
|---|---|---|
| Live demo | https://notiv-production.up.railway.app, live since **2026-11-22** | [Deployment §8](../DEPLOYMENT.md#8-live-deployment-railway) |
| Live verification | **76 / 76** checks on the deployed site | [Results §6c](../RESULTS_AND_TESTING.md#6c-live-deployment-railway) |
| Automated tests | **636 passed, 2 skipped** (638 collected, with PostgreSQL + S3 enabled) | [Results §1](../RESULTS_AND_TESTING.md#1-scoreboard) |
| Browser end-to-end | **18 / 18**, no console errors | [Results §6](../RESULTS_AND_TESTING.md#6-browser-end-to-end) |
| File-format matrix | **33 / 33** correct outcomes across PDF, PPTX, MP3, MP4 | [Results §3](../RESULTS_AND_TESTING.md#3-format-matrix-detail-2026-10-27) |
| AI scorecard (offline, 3 test lectures) | **100 %** retrieval, answer, citation validity, abstention | [Evaluation §4](../EVALUATION.md#4-ai-scorecard-golden-set) |
| Transcription error (word error rate) | **0.0 %** MP3, **0.8 %** MP4 (~45 s synthetic speech, `small` model) | [Results §5](../RESULTS_AND_TESTING.md#5-live-recordings-toolse2e_mediapy) |
| Hard real document | **31 / 34** checks (39 handwritten pages; AI vision quota ran out) | [Results §4](../RESULTS_AND_TESTING.md#4-live-handwritten-pdf-systemdesginpdf-39-pages--run-2) |
| Deployment smoke test | **41 / 41** in Docker (SQLite) and on PostgreSQL 16 + S3 | [Results §6b](../RESULTS_AND_TESTING.md#6b-docker-deployment-smoke-test) |
| Sprints | 15 (Sprint 1 → Sprint 15) | [Sprints](../SPRINTS.md) |

If a number here and a number in another document ever differ, [Results and testing](../RESULTS_AND_TESTING.md) is the
source of truth.
