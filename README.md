# 🧠 Notiv — From Voice to Value

> **AI Meeting Intelligence Platform** · Transcribe, summarise, extract, and semantically chat with any meeting recording.

---

## What It Does

Notiv ingests a YouTube link or a local audio/video file and runs a full AI pipeline on it — producing a structured executive summary, a decision log, assigned action items, open follow-ups, and a RAG-powered chat interface so you can ask anything about the meeting in plain language.

---

## Feature Overview

| Feature | Detail |
|---|---|
| **Audio ingestion** | YouTube URL via `yt-dlp` or local file (mp4, mp3, wav, etc.) |
| **Transcription** | English → OpenAI Whisper (local) · Hinglish → Sarvam AI `saaras:v2.5` (translate-to-English) |
| **Executive Summary** | Map-reduce summarisation over chunked transcript via Mistral AI |
| **Extraction** | Action items with owner + deadline, Decision log, Open follow-ups |
| **RAG Chat** | ChromaDB vector store + `all-MiniLM-L6-v2` embeddings + Mistral retrieval chain |
| **Live pipeline status** | Animated step-by-step progress in sidebar |
| **CLI mode** | `main.py` for headless terminal usage |

---

## Tech Stack

```
Frontend      Streamlit 1.58
LLM           Mistral AI  (mistral-small-latest via LangChain)
Transcription OpenAI Whisper (local) · Sarvam AI (Hinglish)
Embeddings    sentence-transformers / all-MiniLM-L6-v2
Vector DB     ChromaDB (local persist)
Orchestration LangChain LCEL (RunnablePassthrough, RunnableLambda)
Audio         pydub · ffmpeg · yt-dlp
```

---

## Project Structure

```
notiv/
├── app.py                  # Streamlit UI — main entry point
├── main.py                 # CLI entry point
├── Requirements.txt
├── .env                    # API keys (not committed)
│
├── core/
│   ├── __init__.py
│   ├── transcriber.py      # Whisper + Sarvam routing
│   ├── summarizer.py       # Map-reduce summarisation + title generation
│   ├── extractor.py        # Action items, decisions, follow-ups
│   ├── rag_engine.py       # RAG chain builder + ask_question
│   └── vector_store.py     # ChromaDB build / load / retriever
│
└── utils/
    └── audio_processor.py  # YouTube download, WAV conversion, chunking
```

---

## Quickstart

### 1. Clone & install

```bash
git clone https://github.com/your-username/notiv.git
cd notiv
pip install -r Requirements.txt
```

> Requires `ffmpeg` installed on your system (`brew install ffmpeg` / `apt install ffmpeg`).

### 2. Configure environment

Create a `.env` file in the project root:

```env
MISTRAL_API_KEY=your_mistral_key_here
SARVAM_API_KEY=your_sarvam_key_here          # only needed for Hinglish
WHISPER_MODEL=base                            # tiny / base / small / medium / large
```

### 3. Run the app

```bash
streamlit run app.py
```

Or use the CLI:

```bash
python main.py
```

---

## How the Pipeline Works

```
Input (URL / file)
       │
       ▼
 Audio Processing          yt-dlp download → WAV conversion → 10-min chunks
       │
       ▼
 Transcription             Whisper (English) or Sarvam AI (Hinglish → English)
       │
       ▼
 Title Generation          Mistral: 8-word professional title from transcript
       │
       ▼
 Executive Summary         Map-reduce: chunk → partial summaries → combined summary
       │
       ▼
 Extraction                Mistral: action items · decision log · follow-ups
       │
       ▼
 Vector Store              ChromaDB: 500-char chunks, MiniLM embeddings, persisted locally
       │
       ▼
 RAG Chat                  Retriever (top-4 chunks) + Mistral → Meeting Copilot
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MISTRAL_API_KEY` | ✅ | Mistral AI API key |
| `SARVAM_API_KEY` | Only for Hinglish | Sarvam STT-translate API key |
| `WHISPER_MODEL` | Optional | Whisper model size (default: `base`) |
| `SARVAM_STT_MODEL` | Optional | Sarvam model (default: `saaras:v2.5`) |

---

## Notes

- The ChromaDB vector store is persisted to `vector_db/` locally. Each new meeting overwrites the previous collection.
- Sarvam's sync API only accepts ≤30s audio — `transcriber.py` automatically slices chunks into 25s pieces before sending.
- YouTube downloads use browser cookies (`chrome → safari → firefox` fallback). You must be logged into YouTube in at least one of these browsers.
- For long recordings, `WHISPER_MODEL=small` or `medium` gives meaningfully better accuracy at moderate cost.


