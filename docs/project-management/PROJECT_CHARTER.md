# Notiv — Project charter

| | |
|---|---|
| Project | Notiv, an AI study workspace for lectures |
| Owner | Shreya Joshi (product owner, project manager, engineer, tester) |
| Period | 2026-06-26 (first prototype) → 2026-11-25 (closure) |
| Status | **Closed.** Live since 2026-11-22. Open items handed to the [product backlog](PRODUCT_BACKLOG.md) |
| Compiled | 2026-11-25, from project records (see [index](README.md)) |

## 1. Purpose

Students receive lectures as mixed files: slide decks, scanned or handwritten PDFs, audio recordings, screen
recordings. General AI tools will summarise them, but the student cannot tell which statements came from the lecture
and which the model made up. Before an exam, a confident wrong answer is worse than no answer.

Notiv turns one lecture into a place to study (summary, concepts, flashcards, quiz, mind map and a question-and-answer
chat) where **every answer and study item links to the page or minute it came from**. When the lecture does not
contain the answer, Notiv says so instead of guessing.

## 2. Objectives

| # | Objective | Measure of success | Result at closure |
|---|---|---|---|
| O1 | Accept the four real-world lecture formats reliably | Format matrix: every valid file processed, every invalid file rejected with a clear message | **33 / 33** ✅ |
| O2 | Ground every AI output in the lecture | Citation validity 100 %; abstention on unanswerable questions 100 % | **100 % / 100 %** (offline scorecard) ✅ |
| O3 | Transcribe recordings accurately with timestamps | Word error rate against a known script; timestamps ordered and inside the recording | **0.0 % / 0.8 %** ✅ |
| O4 | Keep working when an AI provider fails | Fallback across providers; named failure reasons; extractive output without any provider | Delivered and unit-tested ✅ |
| O5 | Keep each user's data private | Another account gets 404 on every lecture | Verified in tests, Docker and live ✅ |
| O6 | Ship a public, working demo | Deployed and verified end to end | Live 2026-11-22, **76 / 76** checks ✅ |

Results and how they were measured: [QA test report](QA_TEST_REPORT.md).

## 3. Scope

**In scope**

* Upload of PDF, PPTX, MP3 and MP4 lectures, with validation of file contents (not just file names).
* Reading scanned pages (OCR) and handwritten or garbled pages (vision model).
* Transcription with timestamps; matching video frames to slides when a deck is attached.
* AI study material: summary, concepts, flashcards, quiz, mind map, each item cited.
* Ask: answers from the lecture only, with citations, or "not enough evidence".
* Library (courses, lectures, delete), notebooks with canvas, share links and PDF download.
* Email-and-password accounts with secure sessions.
* Docker deployment, a live demo and full test and evaluation tooling.

**Out of scope** (reasons in the [decision log](DECISION_LOG.md))

* Live lecture recording in the browser (deferred).
* Importing from YouTube or web pages.
* Google or other social sign-in.
* Formats other than the four above.
* Mobile apps.

## 4. Deliverables

| Deliverable | Where |
|---|---|
| Web application (API, processing pipeline, worker, web UI) | this repository |
| Docker image definition | [`Dockerfile`](../../Dockerfile) |
| Live demo | https://notiv-production.up.railway.app |
| Test suite and evaluation tools | `core/test_*.py`, [`tools/`](../../tools) |
| Product, architecture, evaluation and deployment docs | [`docs/`](..) |
| Project management documents | this folder |

## 5. Stakeholders

| Stakeholder | Interest | How they were represented |
|---|---|---|
| Students (target users) | Trustworthy study material from their own lectures | Personas in the [product overview](../PRODUCT_OVERVIEW.md#who-it-is-for); no formal user research was run (see [closure report](PROJECT_CLOSURE_REPORT.md#6-lessons-learned)) |
| Project owner | A working, verifiable product | Sprint goals, test results, release decisions |
| Recruiters and reviewers | Evidence of real, verified work | Live demo, test log, these documents |

## 6. Constraints

* **One person**, so scope had to be cut hard (see [case study](CASE_STUDY.md#what-was-cut-and-why)).
* **Low or free AI budgets**: free-tier AI quotas ran out during testing (see [risk R3](RISK_REGISTER.md)).
* **Hosting** needs about 2 GB RAM, ffmpeg, LibreOffice and a speech model in one container, plus a persistent disk.

## 7. Assumptions

* Students already have their lecture files; uploading them is acceptable (no live capture needed for a first release).
* One working AI provider key is available; without one, the app still produces extractive (quoted) study material.
* A single-server deployment is enough for a demo; a multi-server setup exists and is tested locally for later.

## 8. High-level timeline

| Phase | Dates | Outcome |
|---|---|---|
| Prototype | 2026-06-26 → 06-27 | First MVP (a meeting-intelligence app), in git history |
| Foundation and product (Sprints 1–9) | → 2026-10-17 | Durable pipeline, Ask, study items, notebooks, accounts, redesign |
| Intelligence (Sprints 10–13) | 2026-10-23 → 10-27 | Reliable reading, AI study material, recordings, evaluation tools |
| Release (Sprint 14) | 2026-10-31 → 11-17 | Docker verified, redesign, PDF export, audit |
| Go-live (Sprint 15) | 2026-11-22 → 11-25 | Live on Railway, 76 / 76 checks, project documents |

Detail: [roadmap](ROADMAP.md) and [milestones](MILESTONES.md). Individual dates for Sprints 1–9 were not recorded.
