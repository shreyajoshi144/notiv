import os
from langchain_mistralai import ChatMistralAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from core.vector_store import build_vector_store, load_vector_store, get_retriever

def get_llm():
    return ChatMistralAI(
        model="mistral-small-latest",
        mistral_api_key=os.getenv("MISTRAL_API_KEY"),
        temperature=0.3,
    )

def format_docs(docs):
    return "\n\n".join([doc.page_content for doc in docs])

CHAT_SYSTEM_PROMPT = """You are an expert meeting assistant. Answer the user's question 
based ONLY on the meeting transcript context provided below.

If the answer is not found in the context, say: 
"I could not find this information in the meeting transcript."

Always be concise and precise. If quoting someone, mention it clearly.

Context from meeting transcript:
{context}"""


def _assemble_chain(retriever):
    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", CHAT_SYSTEM_PROMPT), ("human", "{question}")]
    )
    return (
        {"context": retriever | RunnableLambda(format_docs), "question": RunnablePassthrough()}
        | prompt | llm | StrOutputParser()
    )


def build_rag_chain(transcript: str, collection_name: str = None):
    """Builds embeddings for `transcript` (optionally into a namespaced
    `collection_name`) and returns a ready-to-use RAG chain. Use this once,
    at processing time. For repeat chat turns against an already-processed
    meeting, use build_chat_chain(collection_name) instead — it skips
    re-embedding the transcript on every question."""
    vector_store = (
        build_vector_store(transcript, collection_name=collection_name)
        if collection_name else build_vector_store(transcript)
    )
    retriever = get_retriever(vector_store, k=4)
    return _assemble_chain(retriever)


def build_chat_chain(collection_name: str, k: int = 4):
    """Loads an existing, already-embedded collection (built once during
    pipeline processing) and returns a RAG chain over it — no re-embedding.
    This is what the app should call for every chat turn after a meeting
    has finished processing."""
    vector_store = load_vector_store(collection_name=collection_name)
    retriever = get_retriever(vector_store, k=k)
    return _assemble_chain(retriever)


# load_rag_chain removed — unused and broken (get_retriever requires vector_store arg).
# Use build_rag_chain(transcript) once at processing time, and
# build_chat_chain(collection_name) for subsequent chat turns.


def ask_question(rag_chain, question:str) -> str:
    print(f"Question : {question}")
    answer = rag_chain.invoke(question)
    print(f"answer :{answer}")
    return answer


# ══════════════════════════════════════════════════════════════════════
# Phase 2C — grounded chat with evidence, bounded context/history, and a
# deterministic "not found" response when retrieval has nothing.
#
# Everything above this point (build_rag_chain / build_chat_chain /
# ask_question / CHAT_SYSTEM_PROMPT) is UNCHANGED and still used exactly
# as before by main.py's CLI flow. chat_with_sources() below is an
# additional entry point for the API layer (server/services/rag_service.py)
# that also needs the retrieved evidence and real timestamps alongside
# the answer, plus bounded conversation history for follow-up questions.
# ══════════════════════════════════════════════════════════════════════

NOT_FOUND_MESSAGE = "I could not find this information in the meeting transcript."

# Bounds the total retrieved context handed to the LLM in one turn, and
# how much prior conversation is replayed — so neither a long transcript
# (more/bigger retrieved chunks) nor a long chat session (many prior
# turns) can make a single prompt grow without bound.
MAX_CONTEXT_CHARS = 6000
MAX_HISTORY_TURNS = 3

GROUNDED_CHAT_SYSTEM_PROMPT = f"""You are an expert meeting assistant. Answer the user's \
question based ONLY on the meeting transcript context provided below.

Some context lines may start with a bracketed timestamp like [MM:SS] or [H:MM:SS] — you \
may cite that exact timestamp if it is directly relevant to your answer. NEVER invent, \
estimate, or reformat a timestamp that was not shown to you.

If the context contains conflicting statements about the same thing, point that out \
explicitly rather than silently picking one side as fact.

If the answer is not found in the context, say exactly:
"{NOT_FOUND_MESSAGE}"

Always be concise and precise. If quoting someone, mention it clearly.
{{history_block}}
Context from meeting transcript:
{{context}}"""


def _format_timestamp(seconds) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def _format_context_and_sources(docs, max_chars: int = MAX_CONTEXT_CHARS):
    """Renders retrieved docs into (context_text, sources_list). Bounded:
    once adding another doc's text would exceed `max_chars`, later docs
    are dropped from the context (but at least one doc is always
    included, even if it alone exceeds the budget, so a genuinely
    relevant top match is never withheld entirely). `sources_list` mirrors
    exactly what was actually included in `context_text` — never more —
    so "evidence shown" and "evidence used" can't drift apart. A doc's
    start/end/source are only ever included when the underlying chunk's
    metadata actually has them (see vector_store.py); never fabricated.
    """
    parts = []
    sources = []
    total = 0
    for i, doc in enumerate(docs):
        text = doc.page_content
        if parts and total + len(text) > max_chars:
            break
        meta = doc.metadata or {}
        prefix = f"{_format_timestamp(meta['start'])} " if meta.get("start") is not None else ""
        parts.append(prefix + text)
        sources.append({
            "chunk_index": meta.get("chunk_index", i),
            "text": text,
            "start": meta.get("start"),
            "end": meta.get("end"),
            "source": meta.get("source"),
        })
        total += len(text)
    return "\n\n".join(parts), sources


def _format_history_block(history, max_turns: int = MAX_HISTORY_TURNS) -> str:
    """`history` is a list of {"question": ..., "answer": ...} dicts,
    oldest first. Bounded to the last `max_turns` turns — a long chat
    session never makes this block grow without bound."""
    if not history:
        return ""
    recent = list(history)[-max_turns:]
    lines = ["Recent conversation so far (for understanding follow-up questions):"]
    for turn in recent:
        lines.append(f"Q: {turn.get('question', '')}")
        lines.append(f"A: {turn.get('answer', '')}")
    return "\n".join(lines) + "\n"


def chat_with_sources(collection_name: str, question: str, k: int = 4, history: list = None) -> dict:
    """One grounded chat turn against a single meeting's own Chroma
    collection. Returns {"answer": str, "sources": [...]} — the sources
    list mirrors exactly what evidence the answer was (or, in the
    not-found case, would have been) based on.

    - Meeting-scoped: `collection_name` pins retrieval to one meeting's
      collection; this never queries any other meeting's data.
    - No-evidence case: if the retriever finds nothing at all, the fixed
      NOT_FOUND_MESSAGE is returned directly WITHOUT calling the LLM —
      a deterministic guarantee against hallucinating an answer with
      zero grounding, and one fewer LLM call for a clearly answerless
      question.
    - Bounded: both the retrieved context (MAX_CONTEXT_CHARS) and the
      replayed conversation history (MAX_HISTORY_TURNS) are capped.
    - Timestamps are included in the context (and returned in `sources`)
      only when the underlying chunk actually has them — never invented.
    """
    vector_store = load_vector_store(collection_name=collection_name)
    retriever = get_retriever(vector_store, k=k)
    docs = retriever.invoke(question)

    if not docs:
        return {"answer": NOT_FOUND_MESSAGE, "sources": []}

    context, sources = _format_context_and_sources(docs)
    history_block = _format_history_block(history)

    llm = get_llm()
    prompt = ChatPromptTemplate.from_messages(
        [("system", GROUNDED_CHAT_SYSTEM_PROMPT), ("human", "{question}")]
    )
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "history_block": history_block, "question": question})

    return {"answer": answer, "sources": sources}
