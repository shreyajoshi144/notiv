# Notiv — Architecture

Notiv is a single Python codebase with two processes: a **FastAPI web app** that serves the API and the static UI, and one
or more **workers** that process lectures through a durable, staged job pipeline. Both share one database (SQLite in
development, PostgreSQL in production) and one object store (local files or S3).

## 1. System context

```mermaid
flowchart TB
    student([Student's browser])
    subgraph notiv[Notiv]
        api[FastAPI app<br/>backend/server/main.py<br/>API + static UI]
        worker["Worker(s)<br/>core/lecture/worker.py<br/>staged job pipeline"]
        db[(Database<br/>SQLite / PostgreSQL)]
        store[(Object store<br/>local / S3)]
    end
    subgraph tools[Local processing tools]
        ff[ffmpeg / ffprobe]
        pop[poppler]
        lo[LibreOffice]
        tess[Tesseract OCR]
        whisper[faster-whisper]
    end
    subgraph llm[Answer-model providers · ordered fallback]
        oai[OpenAI]
        groq[Groq]
        mis[Mistral]
        ant[Anthropic]
    end
    smtp[SMTP relay<br/>password reset]

    student -- HTTPS, cookie session + CSRF --> api
    api --> db
    api --> store
    api -- Ask, AI quiz --> llm
    api -- reset mail --> smtp
    worker --> db
    worker --> store
    worker --> tools
    worker -- vision re-read, AI study --> llm
```

## 2. Code layout

| Path | Responsibility |
|---|---|
| `backend/server/main.py` | FastAPI app, page routes, static files, middleware (`api/guards.py`: CSP, request id, size limits, rate limits) |
| `backend/server/api/routes_*.py` | HTTP routes: lectures, workspace, Ask, study, AI quiz, notes, notebooks, question bank, auth |
| `backend/server/api/*_handlers.py` | Framework-free handlers shared by FastAPI and the stdlib `dev_server.py` used in tests |
| `backend/server/auth_sessions.py` | Opaque server-side sessions, CSRF tokens, password reset tokens (stored hashed) |
| `backend/server/static/` | UI: `index.html` (landing), `auth.html`, desk, lecture, notebooks; ES modules under `ui/`; self-hosted fonts |
| `core/lecture/pipeline.py`, `jobs.py`, `worker.py` | Staged pipeline, durable jobs with leases and heartbeats, worker loop |
| `core/lecture/media.py`, `transcribe.py`, `slides.py`, `slide_match.py`, `align.py` | Extraction: probe, audio, transcription, slide rendering + text, frame matching, alignment |
| `core/lecture/evidence.py`, `passages.py`, `retrieval.py` | Evidence rows, passages, hybrid retrieval |
| `core/lecture/ask.py`, `qa.py` | Ask Notiv: sufficiency gate, injection screen, extractive or model answers, citation validation |
| `core/lecture/llm*.py` | One provider layer: chain, fallback, failure classification, adapters |
| `core/lecture/ai_study.py` | AI study material: chunked map calls, synthesis, validation |
| `core/lecture/knowledge.py`, `study.py`, `study_store.py`, `workspace.py` | Concepts, study items, sessions, summary, mind map |
| `core/lecture/store.py`, `repo.py`, `pg.py`, `migrations/` | Persistence, PostgreSQL translation, checksummed migrations 0001–0008 |
| `core/lecture/settings.py`, `health.py`, `obs.py`, `ratelimit.py`, `quotas.py` | Production config gate, `/readyz`, metrics, limits |
| `tools/` | Evaluation and end-to-end scripts (see [Evaluation](EVALUATION.md)) |

## 3. Lecture processing pipeline

A lecture is processed by one job. Each stage is idempotent, records its own result in `job_stages`, and reports
progress. Stages that do not apply (no recording → no transcription) finish immediately.

```mermaid
flowchart LR
    V[VALIDATE] --> P[PROBE] --> EA[EXTRACT_AUDIO] --> T[TRANSCRIBE]
    T --> PS[PROCESS_SLIDES] --> EF[EXTRACT_FRAMES] --> DS[DETECT_SLIDE_CHANGES]
    DS --> AL[ALIGN] --> BE[BUILD_EVIDENCE] --> IX[INDEX] --> AN[ANALYZE]
    AN --> R((READY))
```

| Stage | What it does | Fails with a named reason when… |
|---|---|---|
| VALIDATE | checks type, signature, size; rejects encrypted / damaged PDFs and slide-less PPTX | the file is not one of the four formats or is broken |
| PROBE | ffprobe: duration, streams, separate `audio_duration`; ignores MP3 cover art | there is no audio track |
| EXTRACT_AUDIO | 16 kHz WAV; duration checked against the audio track (MP3 5 %, MP4 1 %) | the decode is truncated |
| TRANSCRIBE | faster-whisper `small`; segments checked for order, bounds, overlap, NaN | no speech is found or the transcript is invalid |
| PROCESS_SLIDES | renders pages; picks the best text source per page (see §4) | every page is unreadable |
| EXTRACT_FRAMES / DETECT_SLIDE_CHANGES | samples video frames; OpenCV matching with boundary bisection | (skipped without video + deck) |
| ALIGN | maps transcript lines to slides | |
| BUILD_EVIDENCE | writes `lecture_evidence` rows: slide text and transcript lines | |
| INDEX | FTS5 / BM25 index plus vectors | |
| ANALYZE | AI study material (§6), insights, knowledge and study items | never fails the lecture: without a provider, extractive output is kept and the reason is shown |

### Jobs and workers

```mermaid
stateDiagram-v2
    [*] --> queued: POST /lectures/{id}/process
    queued --> running: worker takes a lease
    running --> running: heartbeat every 30 s
    running --> completed: all stages done
    running --> retrying: stage error, or worker died (stale heartbeat)
    retrying --> running: after exponential backoff
    running --> failed: attempts exhausted
    failed --> queued: POST /jobs/{id}/retry
    running --> cancelled: POST /jobs/{id}/cancel
    completed --> [*]
```

Leases are fenced: a worker that lost its lease stops writing. Jobs are created once per lecture (idempotent), so a double
click on "Process" does not start two runs.

## 4. Reading a page: text-quality routing

Text layers are scored, not trusted. Handwriting apps often embed a "recognised" text layer of scattered single words;
storing that as evidence would poison every later step.

```mermaid
flowchart TD
    A[Rendered page + text layer] --> Q{text_quality}
    Q -- good --> RF[reflow: re-join hard-wrapped lines] --> E[Evidence]
    Q -- "none (scan) or garbled (handwriting app)" --> VIS{Vision provider<br/>configured?}
    VIS -- yes --> VR[Vision model re-read<br/>image ≤ 1200 px, backoff]
    VR -- text returned --> RF
    VR -- failed --> NONE{Page had no<br/>text layer?}
    VIS -- no --> NONE
    NONE -- yes --> OCR[Tesseract OCR]
    OCR -- "confidence ≥ NOTIV_OCR_MIN_CONFIDENCE<br/>and text passes quality" --> RF
    OCR -- low confidence --> X
    NONE -- "no (garbled layer)" --> X[Excluded from evidence<br/>warning shown on the lecture page]
```

Vision is tried first because it reads handwriting; Tesseract only runs on pages with no text layer. Garbled-layer pages
are never sent to Tesseract: on handwriting it produces confident-looking junk.

## 5. Answer-model layer

Every AI feature (Ask, general-knowledge follow-ups, AI quiz, AI study material, page re-reading) goes through one
`LLMChain` in `core/lecture/llm.py`.

```mermaid
flowchart LR
    call[Feature call] --> chain{LLMChain}
    chain --> p1[OpenAI]
    p1 -- ok --> out[Result]
    p1 -- failed --> cls1[classify reason]
    cls1 --> p2[Groq] -- ok --> out
    p2 -- failed --> cls2[classify] --> p3[Mistral] -- ok --> out
    p3 -- failed --> cls3[classify] --> p4[Anthropic] -- ok --> out
    p4 -- failed --> err[LLMUnavailable<br/>with every provider's named reason]
```

* Order: `NOTIV_LLM_PROVIDERS` (default `openai,groq,mistral,anthropic`); vision: `NOTIV_VISION_PROVIDERS`
  (default `openai,groq,anthropic`). Providers without a key are skipped. Lovable is opt-in.
* Each failure is classified (`llm.REASON_TEXT`): `rate_limited`, `timeout`, `unavailable`, `auth`, `quota_exhausted`,
  `daily_limit`, `unsupported_model`, `not_installed`, `refusal`, `truncated`, `empty`, …. Hard reasons (bad key, no credits, unsupported model, daily limit) put the
  provider on a 10-minute cool-down; transient ones respect `Retry-After` or 30 s.
* Batch work (AI study, vision) uses `call_with_backoff`: it waits out transient limits up to
  `NOTIV_AI_MAX_WAIT_SECONDS` and stops at once when every provider failed for a hard reason.
* `describe()` turns reasons into user-facing text ("OpenAI has no credits left") and HTTP codes (429 / 503 / 502 / 403).
* `NOTIV_*_MODEL` wins over the SDK-style `OPENAI_MODEL` / `OPENAI_BASE_URL`, so a stray variable cannot route OpenAI
  calls to another vendor's model name.

## 6. AI study material

```mermaid
flowchart TD
    EV[(lecture_evidence)] --> CH[Chunk related pages / transcript<br/>≤ 7000 chars]
    CH --> M1[Map call per chunk:<br/>sections, concepts, relations,<br/>examples, comparisons]
    CH --> M2[Study call per chunk:<br/>flashcards, quiz]
    M1 --> S[Synthesis call:<br/>title, overview, takeaways, topics]
    M1 & M2 & S --> VAL{Validate every item}
    VAL -- cited id not shown to model --> DROP[Drop]
    VAL -- concept name not in cited text --> DROP
    VAL -- ok --> DER[Derive pages / times<br/>from cited evidence only]
    DER --> DB[(lecture_ai_study<br/>version ai-study-v3)]
    DB --> API[summary · knowledge · study ·<br/>notebook · mind map APIs<br/>mode: ai]
```

* The model never chooses page numbers or timestamps: they are read from the evidence it cited.
* A concept keeps the lecture's own definition (`source_supported`) separate from any `supplementary_explanation`,
  which the UI labels.
* Without a provider, the extractive summary, knowledge and study items remain, and the UI shows why AI material is
  missing (`ai_unavailable`).

## 7. Ask Notiv

```mermaid
flowchart TD
    Q[Question] --> G[Validate, rate-limit, quota]
    G --> R[Hybrid retrieval:<br/>BM25 FTS5 + vectors → RRF k=60<br/>+ definition boost]
    R --> SUF{Sufficiency gate:<br/>does evidence cover the question?}
    SUF -- no --> NE["'Not enough evidence'<br/>model never called"]
    SUF -- yes --> INJ[Injection screen:<br/>instruction-like evidence withheld]
    INJ --> MODE{Answer model available?}
    MODE -- yes --> LLM[Model answer from cited passages]
    MODE -- no --> EXT[Extractive answer: quoted sentences<br/>headings skipped; degraded_reason set]
    LLM --> CV{Citations valid?<br/>quotes are literal substrings}
    CV -- no --> EXT
    CV -- yes --> A[Answer + citations → page / second]
    EXT --> A
```

A separate, explicitly labelled **"Explain from general knowledge"** action exists for when the student wants an answer
beyond the lecture.

## 8. Data model (main tables)

```mermaid
erDiagram
    users ||--o{ workspace_members : joins
    workspaces ||--o{ workspace_members : has
    workspaces ||--o{ courses : has
    courses ||--o{ lectures : has
    lectures ||--o{ assets : "uploaded files"
    lectures ||--o{ jobs : "processed by"
    jobs ||--o{ job_stages : records
    lectures ||--o{ lecture_slides : has
    lectures ||--o{ lecture_segments : "transcript lines"
    lectures ||--o{ lecture_evidence : "slide + transcript rows"
    lectures ||--o| lecture_ai_study : "AI study material"
    lecture_evidence ||--o{ knowledge_evidence : cited_by
    knowledge_items ||--o{ knowledge_evidence : cites
    lecture_evidence ||--o{ study_item_evidence : cited_by
    study_items ||--o{ study_item_evidence : cites
    study_sessions ||--o{ study_attempts : records
    chat_conversations ||--o{ chat_messages : has
    chat_messages ||--o{ chat_message_evidence : cites
    users ||--o{ user_notes : writes
    users ||--o{ notebooks : owns
    notebooks ||--o{ notebook_items : has
    notebooks ||--o| notebook_canvas_state : has
    notebooks ||--o{ notebook_shares : "read-only links"
    users ||--o{ auth_sessions : "signed in"
```

Migrations (`core/lecture/migrations/{sqlite,postgres}/`), applied automatically and checksummed:

| # | Adds |
|---|---|
| 0001 | baseline: workspaces, courses, lectures, assets, jobs, slides, segments, timeline, evidence, insights, embeddings, chat, knowledge, study |
| 0002 | production: idempotency keys, rate limits, audit log, storage deletion outbox, worker heartbeats, job stages |
| 0003 | personal notes and their evidence links |
| 0004 | notebooks, sources, items, canvas, shares |
| 0005 | auth identities (kept; unused since OIDC removal, because applied migrations are checksummed) |
| 0006 | users, password auth, sessions, password resets |
| 0007 | manual question bank |
| 0008 | `lecture_ai_study` |

## 9. Security model

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as API
    participant D as Database
    B->>A: POST /auth/password/login (email, password)
    A->>D: verify scrypt hash, create session (SHA-256 of id stored)
    A-->>B: Set-Cookie notiv_session (HttpOnly, SameSite=Lax) + notiv_csrf
    B->>A: POST /lectures (cookie + X-CSRF-Token)
    A->>A: check Origin, CSRF cookie == header == stored hash
    A->>D: authorise by workspace membership
    A-->>B: 201 / 404 if not yours
```

* Resources that are missing and resources that are not yours both return **404**, so ids cannot be probed.
* CSP `script-src 'self'`: no inline scripts; UI built with DOM APIs and `textContent`.
* Lecture text is treated as data: instruction-like passages are withheld from the model and from quotes.
* Share links store only a SHA-256 hash of the token; they expire and can be revoked.
* `NOTIV_ENV=production` refuses to start without a strong secret, PostgreSQL, private S3, shared or single-instance rate
  limiting, no `*` CORS and a metrics token (or explicit single-instance SQLite/local-storage overrides).

## 10. Deployment shape

```mermaid
flowchart LR
    U([Users]) --> LB[HTTPS reverse proxy]
    LB --> API1[API replica]
    LB --> API2[API replica]
    API1 & API2 --> PG[(PostgreSQL 14+)]
    API1 & API2 --> S3[(Private S3 bucket)]
    W1[Worker] & W2[Worker] --> PG
    W1 & W2 --> S3
    API1 -. /readyz /metrics .-> MON[Monitoring]
```

* `/healthz` is liveness only. `/readyz` returns 503 until the database is at migration head, storage is reachable and
  private, config is valid, and it reports processing tools, Python packages and the answer model.
* Rate limiting: `NOTIV_RATELIMIT_BACKEND=db` for multiple replicas, or `NOTIV_SINGLE_INSTANCE=1`.
* Workers scale horizontally; leases keep two workers off the same job.
