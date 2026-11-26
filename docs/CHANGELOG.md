# Changelog

All notable changes to Notiv. Newest first. Format based on [Keep a Changelog](https://keepachangelog.com/).
Sprint context: [SPRINTS.md](SPRINTS.md). Test evidence: [RESULTS_AND_TESTING.md](RESULTS_AND_TESTING.md).

## 2026-11-26 · Password-reset email through Brevo

- Added: password-reset email can be sent through the Brevo HTTPS API (`NOTIV_BREVO_API_KEY`, `NOTIV_MAIL_FROM`).
  Railway blocks outbound SMTP on Trial and Hobby plans, so SMTP alone could not work on the live demo. SMTP still
  works where it is allowed; Brevo is used first when both are set.
- Added: a failed send is logged with its reason (`brevo send failed: HTTP …`) instead of being swallowed.
- Tests: the reset email goes to Brevo with the right sender, recipient and link; a rejected send returns 503 and the
  link is never usable.

## 2026-11-25 · Project documentation

- Added: [project management documents](project-management/README.md): charter, PRD, roadmap, milestones, product
  backlog, risk register, decision log, QA test report, go-live checklist, closure report and case study.
- Added: `tools/smoke_live_pages.py`, the page and account checks used for the live verification (previously a one-off
  script).
- Added: GitHub issue templates (bug, feature) and a pull request template.
- Fixed in docs: test count by area (workspace 98, total 638); PostgreSQL + S3 marked verified locally in Deployment.

## 2026-11-22 · Live on Railway

- Changed: the `Dockerfile` no longer declares `VOLUME /data`; Railway rejects it, and the volume is attached by the
  platform.
- Deployed: `https://notiv-production.up.railway.app`, single-instance production setup, verified at 76 / 76 live
  checks ([Results §6c](RESULTS_AND_TESTING.md#6c-live-deployment-railway), [Deployment §8](DEPLOYMENT.md#8-live-deployment-railway)).

## 2026-11-17 · Redesign: Daybreak, PDF export, pre-release audit

- Security: `.dockerignore` now excludes `*.env`, so deployment env files (API keys, secrets) are never copied into the image.
- Removed: the unused legacy meeting/RAG prototype (`core/rag_engine.py`, `vector_store.py`, `job_queue.py`, `extractor.py`, `routes_chat.py`, `services/`, `schemas/` and their never-collected tests), six outdated browser scripts, `requirements-export.txt`, `logo.svg`.
- Changed: `docker-compose.sprint5.yml` uses SeaweedFS for S3 (MinIO images are no longer published); PostgreSQL 16 + S3 verified.
- Fixed: `tools/verify_sprint5.sh` depended on the git-ignored `_build_tools/`; it now needs only requirements-test.txt.
- Changed: notebook and item downloads are PDF only, laid out per item type instead of raw JSON (`GET /notebooks/{id}/export`, `GET /notebooks/{id}/items/{item_id}/export`; other `format` values now return 422).
- Fixed: the Docker image could not export PDF (reportlab was only in `requirements-export.txt`); it is now in `requirements-prod.txt`.
- Added: `DELETE /courses/{id}` deletes the course and every lecture in it; delete buttons on library rows, course page and lecture page.
- Fixed: the course page listed only processed lectures although its count included all of them.
- Fixed: the landing top bar scrolled away; it is now pinned, turns solid past the hero and highlights the current section.

### Changed
- **Landing:** a cinematic, light story built on `notiv.jpg`:
  - layered sky light and a floating nav;
  - an Instrument Serif headline with gold-italic accents;
  - statement, vista with the Ask demo, workspace-as-canvas, editorial features, use-case cards, steps, FAQ;
  - a daybreak closing section.
- **Workspace:** no permanent sidebar. A top bar holds a segmented section nav, a context breadcrumb, a **+ New** menu
  and the account avatar. Pages sit on a dotted ivory canvas, with:
  - a floating **Layers** panel: courses → lectures (with status), the open lecture's panels as child layers, and notebooks.
    It has a filter and toggles with **L**;
  - a floating **dock**: Layers, add lecture, new notebook, focus timer, Ask;
  - on phones, a bottom tab bar and sheets.
- **Brand:** a text-only "Notiv" wordmark and a new favicon. The cat companion is replaced by a small friendly robot.
- **Auth:** the form on ivory beside the hero sky.
- **Tokens:** ivory canvas, periwinkle selection, ink primary actions, gold references. Instrument Serif for display,
  EB Garamond for reading.
- **New docs:** [Deployment](DEPLOYMENT.md) and [Design system](DESIGN_SYSTEM.md).

### Removed
- Dark mode, the interim iOS-style "Ink & Red Pencil" pass, the cat, the logo mark, the blurred background, and the
  Fraunces / Instrument Sans fonts.

## 2026-10-31 · Docker verified

### Added
- `tools/smoke_deploy.py`: drives a running deployment like a browser (cookie session + CSRF): health, readiness,
  accounts, all four formats, Ask, study items, cross-account 404; `--as-proxy` simulates an HTTPS reverse proxy.
- `docker-entrypoint.sh`.

### Changed
- Dockerfile: data lives in a `/data` volume (SQLite, uploads, Whisper weights via `HF_HOME`); listens on `$PORT`.

### Fixed
- The container could not write a root-owned volume (the app runs as uid 10001). The entrypoint now fixes ownership
  and drops privileges.
- Behind a hosting platform's HTTPS proxy every write failed the origin check, because uvicorn ignored
  `X-Forwarded-Proto` from non-local proxies. The image sets `FORWARDED_ALLOW_IPS=*`.
- Ask abstained when evidence and question wrote a compound differently ("write-through" / "write through" /
  "writethrough"). `retrieval.word_stems()` adds hyphen parts and the closed form.
- `test_media_study` relied on `NOTIV_AUTH_SECRET` being set in the shell.

## 2026-10-27 · Launch readiness

### Added
- `tools/eval_format_matrix.py`: 33 real PDF / PPTX / MP3 / MP4 variants, including broken files, run through the real pipeline.
- `tools/eval_ai_study.py`: golden-set scorecard with deploy-gating thresholds (retrieval, correctness, citations,
  abstention; `--live` adds support rate, copy rate, topic coverage, quiz validity).
- Study place on the desk: Today panel with greeting, daily goal ring, streak, week bars, goal picker, focus start and
  "continue last lecture"; ambient sound (Rain / Café / Deep) in the focus timer.
- iOS-style UI layer: 44 px targets, press feedback, segmented controls, pill tabs, switches, bottom sheets.
- `core/test_eval_regressions.py`.
- Documentation set: product overview, architecture, evaluation, results and testing, sprints, this changelog.

### Changed
- Landing page rebuilt around the hero photograph: night hero, answer card with receipts, editorial features,
  closing section. Motion is transform-only and respects reduced motion.
- Auth pages: full-bleed photo with scrim, solid card, segmented sign-in / register switch, grouped fields, password
  strength meter, Caps Lock hint.
- Production gate allows SQLite and local storage only with `NOTIV_ALLOW_SQLITE_IN_PROD=1`,
  `NOTIV_ALLOW_LOCAL_STORAGE_IN_PROD=1` and `NOTIV_SINGLE_INSTANCE=1`.
- Old per-phase reports removed (their content is in the new docs); README shortened.

### Fixed
- Ask quote mode could answer with a slide heading.
- The focus timer's "done today" count never reset at midnight.
- The landing hero could stay invisible (opacity 0) when the entrance animation did not run.
- Removed an unverified "it's free" claim and a relative `og:image` URL.

## 2026-10-26 · Recordings

### Added
- MP3 / MP4 lectures get the same AI study material as documents. Items, sections and mind-map nodes carry `times`;
  summary sections carry `start` / `end`.
- `tools/e2e_media.py`: live MP3 / MP4 check with word error rate.
- `core/test_media_study.py`.

### Fixed
- MP4 files whose picture is longer than their sound were rejected as "truncated audio". `probe()` now returns
  `audio_duration`.

## 2026-10-24 · AI study intelligence for PDF / PPTX

### Added
- `slides.text_quality()`: detects garbled text layers (scattered words, non-words, symbols).
- Vision re-read of garbled or handwritten pages (`NOTIV_VISION_PROVIDERS`, images ≤ 1200 px, backoff).
- `core/lecture/ai_study.py` (`ai-study-v3`): overview, takeaways, topics, section summaries, concepts with source
  definition vs. labelled supplementary explanation, relations, examples, comparisons, flashcards, quiz.
- Migration `0008_ai_study` (`lecture_ai_study`); served through the summary, knowledge, study and notebook APIs (`mode: "ai"`).
- Mind map with labelled relation edges.
- Groq provider; failure reasons `quota_exhausted`, `daily_limit`, `unsupported_model`; `llm.diagnose()`;
  `tools/llm_smoke.py`; `tools/e2e_system_design.py`.

### Changed
- `NOTIV_OPENAI_MODEL` / `NOTIV_OPENAI_BASE_URL` take precedence over `OPENAI_MODEL` / `OPENAI_BASE_URL`.

### Fixed
- OpenAI `insufficient_quota` was reported as "busy".
- Batch backoff gave up when providers failed for a mix of hard and transient reasons.
- Tesseract was run on handwriting-app pages and stored junk as evidence.
- Unreadable pages are now excluded and reported (`unreadable_text_excluded`) instead of stored.

## 2026-10-23 · Reliability steps 1–5

### Added
- `/readyz` reports missing processing tools and Python packages.
- Warnings for pages without extractable text.
- Tesseract OCR fallback for scanned pages, discarding text below `NOTIV_OCR_MIN_CONFIDENCE`.
- `reflow()`: re-joins hard-wrapped PDF and OCR lines; written prose mined like speech (knowledge method `extract:v2`).
- One answer-model layer (`core/lecture/llm.py`): ordered fallback OpenAI → Mistral → Anthropic, cool-downs, named
  reasons; Lovable gateway opt-in. Ask and the AI quiz use it.

## 2026-10-17 · Redesign and hardening

### Added
- New brand, desk, study companion with Pomodoro timer, question bank (migration 0007), AI quiz UI, account page,
  Documentation / Help / Privacy / Terms pages.

### Changed
- Uploads limited to PDF, PPTX, MP3 and MP4; MP4 must have audio.
- Pages rendered at a 1600 px long edge; the viewer gains zoom and fit modes.
- Definition questions are answered only from a defining passage.

### Removed
- Google / OIDC sign-in.

### Fixed
- PPTX hidden slides were dropped, shifting text onto the wrong slide images.
- Subprocesses survived timeouts; extracted audio was never length-checked; MP3 cover art was counted as video.

## Sprint 8 · Library and accounts
- Added: lecture library (`/app`), `GET /courses`, `GET /lectures`; email + password accounts (scrypt), HttpOnly cookie
  sessions, CSRF, password reset via SMTP (migration 0006).
- Changed: browser no longer stores bearer tokens; auth UI built without `innerHTML`.

## Sprint 7 · Notebooks
- Added: notebooks, sources, items, canvas, exports (JSON, MD, TXT, CSV, SVG, PDF, DOCX, PPTX), read-only share links
  (migration 0004).
- Fixed: duplicate source linking aborted PostgreSQL transactions.

## Sprint 6 · Gap closure
- Added: personal notes (migration 0003); retrieval `kind` in responses.
- Fixed: PostgreSQL connection stayed in an aborted transaction after any failed statement.

## Sprint 5 · Production hardening
- Added: PostgreSQL and S3 support, rate limiting (shared or single-instance), `/readyz`, `/metrics`, audit log,
  deletion outbox, idempotency keys, worker heartbeats, production config gate (migration 0002).
- Fixed: oversized ids returned 500 (now 404); malformed POST bodies returned 500 (now 422).

## Sprint 4 · Study intelligence
- Added: knowledge extraction, flashcards, short answers, MCQ, cloze, study sessions and progress; literal-quote link checks.

## Sprint 3 · Ask Notiv
- Added: hybrid retrieval (BM25 + vectors, RRF), sufficiency gate, prompt-injection screen, validated citations,
  per-user conversations.

## Sprint 2 · Lecture workspace
- Added: position sync, provenance, key moments, summary, workspace API and UI, `ANALYZE` stage, stdlib dev server.

## Sprint 1 · Foundation
- Added: durable staged jobs, storage keys, local / S3 storage, workspace authorization, checksummed migrations
  (0001), evidence model.
