"""
Background pipeline execution.

Design: one long-lived daemon thread per app process, fed by a
queue.Queue, wrapped in `@st.cache_resource` so Streamlit gives every
session in this process the *same* worker instead of spawning a new one
per rerun. Job status is written to SQLite (core.db) rather than
Streamlit session state, so it's visible from any browser tab/session and
survives page reloads.

Scaling note: this is a single-process worker. It's the right amount of
infrastructure for one Streamlit instance. If this app is ever deployed
as multiple replicas (so "the worker" needs to be shared across
processes, not just across sessions in one process), replace this file
with an RQ or Celery producer that pushes to Redis — `enqueue()` is the
only call site the rest of the app depends on, so that swap doesn't
touch app.py.
"""

import os
import re
import time
import queue
import threading

import streamlit as st

from core import db

STAGE_SEQUENCE = [
    ("audio", "Downloading / converting audio", 8),
    ("transcript", "Transcribing", 35),
    ("title", "Generating title", 45),
    ("summary", "Summarizing", 60),
    # Phase 2B: action items / decisions / open questions used to be three
    # separate stages (three separate full-transcript LLM calls). They're
    # now extracted together in one bounded call — see extract_findings()
    # in core/extractor.py — so this is one stage instead of three.
    ("findings", "Extracting action items, decisions & open questions", 80),
    ("index", "Building vector index", 95),
    ("done", "Done", 100),
]

AUDIO_STORAGE_DIR = os.getenv("NOTIV_AUDIO_DIR", os.path.join("storage", "audio"))


class JobWorker:
    def __init__(self):
        self._q: "queue.Queue[tuple[int, str, str]]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="notiv-job-worker")
        self._thread.start()

    def enqueue(self, meeting_id: int, source: str, language: str):
        db.update_job_status(meeting_id, status="queued", stage="Queued", progress_pct=0)
        self._q.put((meeting_id, source, language))

    def _loop(self):
        while True:
            meeting_id, source, language = self._q.get()
            try:
                self._process(meeting_id, source, language)
            except Exception as e:  # keep the worker alive across bad jobs
                db.mark_meeting_failed(meeting_id, str(e))
            finally:
                self._q.task_done()

    def _process(self, meeting_id: int, source: str, language: str):
        # Imported lazily: heavy deps (whisper, torch, etc.) only load once
        # a job actually runs, not at Streamlit import time.
        from utils.audio_processor import process_input
        from core.transcriber import transcribe_all_with_timestamps
        from core.summarizer import summarize, generate_title
        from core.extractor import extract_findings, get_findings_section, build_timestamped_transcript
        from core.vector_store import build_vector_store
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from pydub import AudioSegment

        def stage(key, pct=None):
            label = dict((k, l) for k, l, _ in STAGE_SEQUENCE)[key]
            pct = pct if pct is not None else dict((k, p) for k, _, p in STAGE_SEQUENCE)[key]
            status = "done" if key == "done" else "running"
            db.update_job_status(meeting_id, status=status, stage=label, progress_pct=pct)

        t0 = time.time()

        stage("audio")
        chunks = process_input(source)
        self._persist_audio(meeting_id, chunks)

        stage("transcript")
        # transcribe_all_with_timestamps() gives us the real Whisper/Sarvam
        # segment timing alongside the transcript text. `transcript` below
        # stays a plain str — same as the old transcribe_all() contract —
        # so every existing use of it (title/summary/vector-store/db
        # columns/word_count) is completely unchanged. The segments are
        # only additionally used to build a timestamped rendering for the
        # findings extraction step below; never invented if unavailable.
        transcript_result = transcribe_all_with_timestamps(chunks, language)
        transcript = transcript_result.text

        stage("title")
        title = generate_title(transcript)
        db.log_usage(meeting_id, "title", transcript[:2000], title)

        stage("summary")
        summary = summarize(transcript)
        db.log_usage(meeting_id, "summary", transcript, summary)

        stage("findings")
        # One bounded call instead of three (extract_action_items /
        # extract_key_decisions / extract_questions) — see extract_findings()
        # in core/extractor.py. Fed the timestamped rendering of the
        # transcript when real segment timing is available, so an item can
        # be cited with a genuine timestamp; falls back to plain transcript
        # text when it isn't (never fabricates timing).
        timestamped_transcript = build_timestamped_transcript(transcript_result)
        structured_findings = extract_findings(timestamped_transcript)
        action_items = get_findings_section(structured_findings, "Action Items")
        decisions = get_findings_section(structured_findings, "Decisions")
        questions = get_findings_section(structured_findings, "Open Questions")
        db.log_usage(meeting_id, "findings", timestamped_transcript, structured_findings)

        stage("index")
        collection_name = f"meeting_{meeting_id}"
        # Phase 2C: pass the real segment timestamps through so retrieval
        # chunks carry genuine start/end/source metadata (see
        # core/vector_store.py's build_vector_store `segments` param).
        # Falls back to build_vector_store's original plain-text splitting
        # automatically when there are no segments — no behavior change
        # for a transcript with no timing available.
        build_vector_store(transcript, collection_name=collection_name, segments=transcript_result.segments)
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        chunk_count = len(splitter.split_text(transcript))

        latency_sec = time.time() - t0
        cost_total = db.meeting_cost_total(meeting_id)

        db.complete_meeting(
            meeting_id,
            title=title.strip(),
            transcript=transcript,
            summary=summary,
            action_items_raw=action_items,
            decisions_raw=decisions,
            questions_raw=questions,
            word_count=len(transcript.split()),
            chunk_count=chunk_count,
            collection_name=collection_name,
            latency_sec=round(latency_sec, 2),
            cost_usd=cost_total,
        )
        stage("done")

    @staticmethod
    def _persist_audio(meeting_id: int, chunk_paths: list):
        """Best-effort: copy the source WAV into durable per-meeting storage
        instead of leaving it in the ephemeral downloads/ folder. Never
        raises — audio persistence failing shouldn't fail the whole job."""
        try:
            if not chunk_paths:
                return
            m = re.match(r"^(.*)_chunk_\d+\.wav$", chunk_paths[0])
            source_wav = m.group(1) + ".wav" if m else None
            if not source_wav or not os.path.exists(source_wav):
                return
            from pydub import AudioSegment
            dest_dir = os.path.join(AUDIO_STORAGE_DIR, str(meeting_id))
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, "recording.wav")
            audio = AudioSegment.from_wav(source_wav)
            audio.export(dest_path, format="wav")
            duration_sec = len(audio) / 1000.0
            db.set_audio_path(meeting_id, dest_path, duration_sec)
        except Exception:
            pass


@st.cache_resource(show_spinner=False)
def get_worker() -> JobWorker:
    return JobWorker()
