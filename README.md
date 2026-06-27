# 🧠 Notiv - From Voice to Value

Notiv turns meeting audio or YouTube recordings into structured transcripts, executive summaries, action items, decision logs, and a semantic chat interface over the meeting content.

<img width="1693" height="929" alt="ChatGPT Image Jun 27, 2026, 01_04_50 AM" src="https://github.com/user-attachments/assets/be8f0181-a81f-4c13-98e0-3b27e067c551" />

## Why this project exists

Meetings create important decisions and follow-ups, but the value is often lost in long recordings and scattered notes. Notiv automates the workflow from audio ingestion to searchable meeting intelligence.

## What it does

- Accepts YouTube links or local audio/video files.
- Transcribes English using local Whisper.
- Transcribes Hinglish using Sarvam AI and translates it to English.
- Generates structured executive summaries with map-reduce summarization.
- Extracts action items, decision logs, and open follow-ups.
- Indexes transcripts in ChromaDB for semantic Q&A over the meeting content.

## Key highlights

- End-to-end pipeline from raw audio to searchable insights.
- Language-aware transcription routing for English and Hinglish.
- Local vector store for persistent retrieval.
- RAG-based chat with grounded answers from transcript context.
- Streamlit UI plus CLI mode for flexible usage.

## Architecture

1. Input: YouTube URL or local file.
2. Audio processing: Download, convert to WAV, split into chunks.
3. Transcription: Whisper or Sarvam depending on language.
4. Title generation: Create a short professional meeting title.
5. Summary generation: Chunked map-reduce summarization.
6. Extraction: Action items, decisions, and follow-ups.
7. Indexing: Embeddings stored in ChromaDB.
8. Chat: Retrieval-based Q&A over meeting content.

## Tech stack

- Frontend: Streamlit 1.58
- LLM: Mistral AI via LangChain
- Transcription: OpenAI Whisper, Sarvam AI
- Embeddings: sentence-transformers / all-MiniLM-L6-v2
- Vector DB: ChromaDB
- Orchestration: LangChain LCEL
- Audio tools: pydub, ffmpeg, yt-dlp

<img width="1470" height="956" alt="Screenshot 2026-06-27 at 6 28 34 PM" src="https://github.com/user-attachments/assets/6b9143c8-2836-446d-90cc-d8eac066ee8a" />

<img width="1470" height="956" alt="Screenshot 2026-06-27 at 6 54 17 PM" src="https://github.com/user-attachments/assets/4bb2f9c5-9bfc-4ae8-bd3c-1c765f4f4210" />

<img width="1470" height="956" alt="Screenshot 2026-06-27 at 6 50 02 PM" src="https://github.com/user-attachments/assets/efdb13f3-4798-4acb-b809-284c9b69b7f3" />

---

## Demo Video 

https://github.com/user-attachments/assets/3a23968d-3404-42cb-b029-6ced69aefd25

https://github.com/user-attachments/assets/19008c73-5ac5-44d1-a70e-95bebbb7e7db



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
    └── __init__.py
    └── audio_processor.py  # YouTube download, WAV conversion, chunking
```

---
## Setup

```bash
git clone https://github.com/your-username/notiv.git
cd notiv
pip install -r Requirements.txt
```

### Requirements

- ffmpeg installed locally.
- `MISTRAL_API_KEY` in `.env`.
- `SARVAM_API_KEY` required only for Hinglish transcription.
- Optional: `WHISPER_MODEL=base` or `small` for better accuracy on longer recordings.

## Environment variables

```env
MISTRAL_API_KEY=your_mistral_key_here
SARVAM_API_KEY=your_sarvam_key_here
WHISPER_MODEL=base
SARVAM_STT_MODEL=saaras:v2.5
```

## Usage

### Streamlit app

```bash
streamlit run app.py
```

### CLI mode

```bash
python main.py
```
---

## Example output

- Meeting title
- Executive summary
- Action items with owner and deadline
- Decision log
- Open follow-ups
- RAG chat interface with transcript-grounded answers
---

## Notes

- The vector store is persisted locally in `vector_db/`.
- Sarvam’s sync API only accepts short audio chunks, so audio is sliced before transcription.
- YouTube downloads use browser cookies, so you must be logged into YouTube in at least one supported browser.

## Limitations

- The current vector store setup overwrites the previous meeting collection.
- Speaker diarization is not yet included.
- Long recordings may require larger Whisper models for better accuracy.

## Future improvements

- Support multiple meetings with separate persistent collections.
- Add speaker diarization.
- Add export to PDF or Markdown.
- Add meeting history and multi-session chat.
- Improve observability and pipeline progress tracking.

---
