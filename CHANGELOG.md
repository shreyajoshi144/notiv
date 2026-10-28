# Changelog

All notable changes to Notiv. Newest first. Format based on [Keep a Changelog](https://keepachangelog.com/).
Sprint context: [docs/SPRINTS.md](docs/SPRINTS.md). Test evidence: [docs/RESULTS_AND_TESTING.md](docs/RESULTS_AND_TESTING.md).

## [Unreleased] — 2026-10-27 · Launch readiness

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
