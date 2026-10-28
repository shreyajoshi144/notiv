"""
Chat + semantic search.

Wraps `core.rag_engine` (the LangChain RAG chain) and `core.vector_store`
(Chroma) exactly as `app.py` uses them — no reimplementation. A meeting
must have finished processing (status == "done", collection_name set)
before it can be chatted with or searched, since that's when its
transcript was embedded into its own Chroma collection.

Phase 2C: chat() now returns the retrieved evidence alongside the
answer (core.rag_engine.chat_with_sources), and accepts a bounded
conversation history for follow-up questions. search() now also
surfaces each hit's start/end/source timing when the underlying chunk
has it.
"""

from core.rag_engine import build_chat_chain, ask_question, chat_with_sources
from core.vector_store import load_vector_store, get_retriever


class MeetingNotReadyError(Exception):
    pass


def _require_collection(meeting: dict) -> str:
    collection_name = meeting.get("collection_name")
    if meeting.get("status") != "done" or not collection_name:
        raise MeetingNotReadyError(
            "This meeting hasn't finished processing yet — chat and search "
            "are available once its status is 'done'."
        )
    return collection_name


def chat(meeting: dict, question: str, history: list = None) -> dict:
    """Returns {"answer": str, "sources": [...]} — meeting-scoped (only
    ever queries this meeting's own Chroma collection), grounded, with a
    deterministic "not found" answer (and empty sources) when retrieval
    turns up nothing at all. `history` is a list of
    {"question": ..., "answer": ...} dicts, oldest first; bounded
    internally to the last few turns."""
    collection_name = _require_collection(meeting)
    return chat_with_sources(collection_name, question, history=history)


def search(meeting: dict, query: str, k: int = 5) -> list[dict]:
    collection_name = _require_collection(meeting)
    vector_store = load_vector_store(collection_name=collection_name)
    retriever = get_retriever(vector_store, k=k)
    docs = retriever.invoke(query)
    return [
        {
            "chunk_index": doc.metadata.get("chunk_index", i),
            "text": doc.page_content,
            "start": doc.metadata.get("start"),
            "end": doc.metadata.get("end"),
            "source": doc.metadata.get("source"),
        }
        for i, doc in enumerate(docs)
    ]
