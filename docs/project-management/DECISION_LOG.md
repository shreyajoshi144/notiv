# Notiv — Decision log

Key product, scope and technical decisions, with the alternatives and the reason. Scope changes (cuts and
deferrals) are marked **Scope**. Sources: [sprint notes](../SPRINTS.md), [changelog](../CHANGELOG.md),
[product overview](../PRODUCT_OVERVIEW.md#what-notiv-deliberately-does-not-do).

| ID | When | Decision | Alternatives considered | Reason | Outcome |
|---|---|---|---|---|---|
| D-01 | Sprint 1 | **Rebuild on a durable, multi-user base**, focused on lectures; quarantine the June prototype instead of extending it | Extend the meeting-intelligence prototype | The prototype had no per-user scoping, was tied to Streamlit, and lost its job queue on restart | Sprint 1 base still in use at closure; prototype code removed 2026-11-17 (D-11) |
| D-02 | Sprint 1–2 | **Everything is built from "evidence" rows** (slide text, transcript lines with page or time) | Generate from raw text and attach sources afterwards | Citations can then be checked mechanically against stored evidence | Every feature (Ask, study items, notebooks) cites evidence IDs |
| D-03 | Sprint 3 | **Abstain before calling the model** when the lecture does not cover the question's key terms | Always call the model and hope it declines | Cheaper, and a model asked to answer tends to answer | Abstention 100 % on the scorecard |
| D-04 | Sprint 3, 6 | **Keyword + word-piece search**, combined by rank fusion; the search type is reported in every response | Paid embedding service for meaning-based search | No external dependency or cost; lecture terms are mostly exact; reporting the type keeps the claim honest | Retrieval hit 100 % on the scorecard; meaning-based search stays optional |
| D-05 | Sprint 6 | **Scope:** defer live lecture recording; uploads only | Record in the browser | Needs streaming infrastructure; students already have files | In backlog as Won't (now), B-14 |
| D-06 | Sprint 9 | **Scope:** accept exactly four formats (PDF, PPTX, MP3, MP4) | Accept many formats | Each format must be tested properly; four cover slides, documents and recordings | 33 / 33 on the format matrix |
| D-07 | Sprint 9 | **Scope:** remove Google sign-in; keep email and password with secure cookie sessions | Keep social sign-in | Extra configuration and attack surface for little value at this stage | B-16 |
| D-08 | Sprint 10 | **One AI provider layer with fallback** (OpenAI, Groq, Mistral, Anthropic) | Depend on one provider | Free tiers run out; one failure should not stop the product | Fallback and named failure reasons verified in tests (risk R3) |
| D-09 | Sprint 11 | **Vision model before OCR for garbled pages; exclude pages that still cannot be read** | OCR everything; keep whatever text comes out | OCR produced junk on handwriting; an honest gap beats invented content | Junk evidence stopped; unreadable pages named to the user |
| D-10 | Sprint 14 | **Scope:** downloads reduced from 8 formats to one laid-out PDF | Keep JSON, Markdown, TXT, CSV, SVG, PDF, DOCX, PPTX | Review found too many options, and several produced poor files; one good format is less to test | PDF download verified in browser e2e and live |
| D-11 | 2026-11-17 | **Remove unused prototype code and outdated scripts before release** | Leave them in the repository | Unused code is a maintenance and security risk and confuses reviewers | Removed in the pre-release audit |
| D-12 | 2026-11-22 | **Host the demo on Railway, single instance** | Render / serverless / free tiers; a multi-server setup | Needs ffmpeg, LibreOffice and a speech model in one container, about 2 GB RAM and a persistent disk; Railway runs the Docker image as-is | Live, 76 / 76 checks |
| D-13 | 2026-11-22 | **Use the smaller `base` speech model on the live demo** | Larger disk; keep `small` | The model cache filled the volume; `base` keeps recordings working within the plan | Recordings pass live; accuracy of `base` still to measure (B-02) |
| D-14 | Throughout | **Report a number only after the run that produced it; label gaps "not verified"** | Report targets or expected results | Credibility; every claim can be reproduced | [Evaluation §8](../EVALUATION.md#8-rules-for-honest-reporting) |
| D-15 | Sprint 5 | **Production mode refuses to start with unsafe settings** (weak secrets, open CORS, SQLite without explicit override) | Warn and continue | A misconfigured deploy should fail loudly, not run insecurely | Used on the live demo with explicit single-instance overrides |
