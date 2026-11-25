# Notiv — Product requirements document (PRD)

| | |
|---|---|
| Product | Notiv, AI study workspace |
| Owner | Shreya Joshi |
| Version | 1.0 (as released on 2026-11-22) |
| Status | Delivered; open requirements are in the [product backlog](PRODUCT_BACKLOG.md) |
| Related | [Product overview](../PRODUCT_OVERVIEW.md) · [Architecture](../ARCHITECTURE.md) · [Evaluation](../EVALUATION.md) |

## 1. Problem

Students get lectures as a pile of files: a slide deck, a scanned or handwritten PDF, an audio recording, a screen
recording. General AI tools summarise them, but the student cannot tell which part came from the lecture and which part
the model made up. In an exam, a wrong answer costs more than no answer.

## 2. Users

| Persona | Need | Key requirement |
|---|---|---|
| **Student revising** | A summary, flashcards and a quiz from this week's slides and recording | FR-8, FR-9 |
| **Student catching up** | "What did the lecturer say about X?", then jump to that minute | FR-10, FR-12 |
| **Student with handwritten or scanned notes** | Notes read correctly, or told clearly what could not be read | FR-3, FR-4 |
| **Study group** | Collect material in one place and share it read-only | FR-15, FR-16 |

## 3. Goals and non-goals

**Goals:** grounded output (every item cites the lecture), honesty (say "not enough evidence" rather than guess),
checkability (one click from a citation to the page or second), and reliability on messy real files.

**Non-goals:** live lecture recording, importing YouTube or web pages, social sign-in, formats beyond the four below,
and any claim that AI output is correct beyond what its citations support. Reasons: [decision log](DECISION_LOG.md).

## 4. Functional requirements

Priority: **M** must, **S** should, **C** could. Status: ✅ delivered and verified · ⚠️ delivered, not fully verified.

### Upload and processing

| ID | Requirement | Pri | Acceptance criteria | Status |
|---|---|---|---|---|
| FR-1 | Accept exactly PDF, PPTX, MP3 and MP4 | M | Valid files of each format process; anything else is rejected with a message naming the four formats; file *contents* are checked, so a renamed file is rejected | ✅ format matrix 33 / 33 |
| FR-2 | Enforce upload limits | M | Files over 2 GB or recordings over 8 hours are rejected with a reason | ✅ duration limit unit-tested; size limit enforced in validation |
| FR-3 | Read scanned pages | M | Pages without a text layer are read with OCR; low-confidence text is discarded, not stored | ✅ matrix (scanned PDF, image-only PPTX) |
| FR-4 | Read handwritten or garbled pages | S | A garbled text layer is detected; the page is re-read with a vision model; pages that still cannot be read are excluded and named to the user | ⚠️ 31 / 34 on a 39-page handwritten PDF (AI vision quota ran out) |
| FR-5 | Keep PPTX slides in order | M | Hidden slides are kept, and the slide count matches the deck | ✅ matrix |
| FR-6 | Transcribe recordings with timestamps | M | Transcript lines have ordered timestamps inside the recording; silence fails with "no speech" | ✅ WER 0.0 % (MP3), 0.8 % (MP4) |
| FR-7 | Link speech to slides | S | For an MP4 uploaded with its deck, each transcript line is attached to the slide on screen | ✅ unit tests; ⚠️ no live video + slides run |
| FR-8 | Show processing progress and failures | M | The library shows each lecture's state; a failed lecture shows a readable reason and can be retried | ✅ browser e2e (states shown through to a final state) |

### Study material

| ID | Requirement | Pri | Acceptance criteria | Status |
|---|---|---|---|---|
| FR-9 | Generate study material | M | Summary, takeaways, concepts, flashcards, quiz and mind map; **every item cites evidence from this lecture**; quiz questions have four distinct options and a valid answer | ✅ unit tests, live smoke; ⚠️ live scorecard not re-run |
| FR-10 | Work without an AI provider | M | With no provider configured, extractive (quoted) material still works and the UI names why AI material is missing | ✅ tests, Docker smoke |

### Ask

| ID | Requirement | Pri | Acceptance criteria | Status |
|---|---|---|---|---|
| FR-11 | Answer only from the lecture | M | Every sentence carries a citation to evidence that was shown to the model; unsupported sentences are removed; if none survive, direct quotes are shown | ✅ citation validity 100 % |
| FR-12 | Abstain when the lecture cannot answer | M | Questions whose key terms the lecture does not cover get "not enough evidence", without calling the model | ✅ abstention 100 % |
| FR-13 | Open the source from a citation | M | Clicking a citation opens the page, or seeks the recording to the second | ✅ position-sync tests (`test_workspace`); ⚠️ the click itself is not in the browser e2e |
| FR-14 | Resist prompt injection in lecture text | M | Instruction-like lecture text is withheld from the model and from quotes; output is validated | ✅ unit tests |

### Library, notebooks and accounts

| ID | Requirement | Pri | Acceptance criteria | Status |
|---|---|---|---|---|
| FR-15 | Organise and delete lectures | M | Courses and lectures listed; deleting a course deletes its lectures; deletes ask for confirmation | ✅ browser e2e, live 22 / 22 |
| FR-16 | Notebooks, sharing and download | S | Notebooks with sources, notes, generated items and a canvas; read-only share links with expiry and revocation; **one** laid-out PDF download | ✅ unit tests (share links), browser e2e and live (PDF download) |
| FR-17 | Accounts | M | Email and password sign-up and sign-in; secure session cookie; writes need a CSRF token | ✅ tests, live |
| FR-18 | Privacy between users | M | Another account gets 404 on every lecture, notebook and summary (same as "not found") | ✅ tests, Docker smoke, live |
| FR-19 | Password reset | S | A one-time, 30-minute reset link is emailed | ⚠️ built and unit-tested; no email service configured live |
| FR-20 | Study place | C | Daily goal, focus timer, streak and ambient sound | ✅ browser e2e (focus timer persists across reload) |

## 5. Non-functional requirements

| ID | Area | Requirement | Evidence |
|---|---|---|---|
| NFR-1 | Grounding | Citation validity 100 %, abstention 100 %, retrieval hit ≥ 90 %, answer correct ≥ 80 % on the scorecard; the scorecard blocks a deploy below threshold | [Evaluation §4](../EVALUATION.md#4-ai-scorecard-golden-set); offline 100 % on all four |
| NFR-2 | Reliability | Failed processing stages retry up to 3 times with exponential backoff; jobs survive restarts; AI calls fall back across providers (OpenAI, Groq, Mistral, Anthropic) | [Architecture §3, §5](../ARCHITECTURE.md) |
| NFR-3 | Security | Passwords hashed with scrypt; HttpOnly session cookie; CSRF token; app runs as a non-root user; production mode refuses to start with weak secrets or open CORS | [Architecture §9](../ARCHITECTURE.md#9-security-model) |
| NFR-4 | Honesty | Failures and missing features are named in the UI (for example "daily AI limit reached"), never shown as an empty result | [Evaluation §7](../EVALUATION.md#7-provider-failure-evaluation) |
| NFR-5 | Operability | `/healthz` and `/readyz` report readiness with reasons; JSON logs with request IDs | [Deployment §6](../DEPLOYMENT.md#6-operations) |
| NFR-6 | Deployability | Runs from one Docker image with about 2 GB RAM and a persistent volume | [Deployment §2](../DEPLOYMENT.md#2-requirements) |
| NFR-7 | Responsiveness (measured, not a target) | PDF / PPTX processed in about 3 s; 45-second recordings in 21–30 s; the first recording is slower while the speech model downloads | [Results §5, §6b](../RESULTS_AND_TESTING.md) |

## 6. Success metrics

| Metric | Target | Result |
|---|---|---|
| Format matrix | all correct | 33 / 33 |
| Citation validity / abstention | 100 % / 100 % | 100 % / 100 % (offline, 3 test lectures) |
| Word error rate | measured against a known script | 0.0 % MP3, 0.8 % MP4 |
| Live deployment checks | all pass | 76 / 76 |

Not measured yet: real student usage, live AI quality at scale, long real-speech recordings. See the
[QA test report](QA_TEST_REPORT.md#6-not-verified).
