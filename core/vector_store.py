import os 
from langchain_chroma import Chroma 
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

CHROMA_DIR = "vector_db"
COLLECTION_NAME = "meeting_transcript"  # fallback default — kept for backward compatibility
EMBEDDING_MODEL  = "all-MiniLM-L6-v2"

# Phase 2C: bounds how big a single "grouped by segment" chunk can get
# before we start a new one — same size as the plain-text splitter below,
# so retrieval granularity is comparable either way.
SEGMENT_CHUNK_CHARS = 500

def get_embeddings():
    return HuggingFaceEmbeddings(
        model_name = EMBEDDING_MODEL,
        model_kwargs = {"device" : 'cpu'}
    )


def _plain_split_docs(transcript: str) -> list:
    """Original behavior, byte-for-byte: split the flat transcript text
    with no timestamp metadata. Used whenever no segments are supplied
    (e.g. main.py's CLI flow via build_rag_chain(transcript)), so that
    existing callers see no behavior change at all."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size = 500,
        chunk_overlap = 50
    )
    chunks = splitter.split_text(transcript)
    return [
        Document(page_content=chunk, metadata={'chunk_index': i})
        for i, chunk in enumerate(chunks)
    ]


def _group_segments_into_docs(segments, chunk_chars: int = SEGMENT_CHUNK_CHARS) -> list:
    """Builds Documents directly out of transcriber.TranscriptSegment
    objects instead of re-splitting the flattened transcript text. Since
    segments carry no character offsets into the flattened text (see
    transcriber.TranscriptSegment), building chunks FROM the segments —
    rather than trying to re-align split text against them afterwards —
    guarantees each chunk's start/end metadata is exactly correct by
    construction, with no fuzzy text matching involved.

    Consecutive segments are grouped until adding the next one would
    exceed `chunk_chars`; a chunk's metadata is only given a "start"/
    "end"/"source" key when at least one of its segments actually has
    that value — a segment with start=None (timing genuinely unknown,
    e.g. some Sarvam edge cases) never produces a fabricated timestamp.
    """
    docs = []
    texts, seg_start, seg_end, sources = [], None, None, set()

    def flush():
        if not texts:
            return
        meta = {}
        if seg_start is not None:
            meta["start"] = seg_start
        if seg_end is not None:
            meta["end"] = seg_end
        if len(sources) == 1:
            meta["source"] = next(iter(sources))
        elif len(sources) > 1:
            meta["source"] = "mixed"
        docs.append((" ".join(texts), meta))

    cur_len = 0
    for seg in segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        if texts and cur_len + len(text) + 1 > chunk_chars:
            flush()
            texts, seg_start, seg_end, sources = [], None, None, set()
            cur_len = 0
        if seg_start is None and seg.start is not None:
            seg_start = seg.start
        if seg.end is not None:
            seg_end = seg.end
        if seg.source:
            sources.add(seg.source)
        texts.append(text)
        cur_len += len(text) + 1
    flush()

    return [
        Document(page_content=text, metadata={**meta, 'chunk_index': i})
        for i, (text, meta) in enumerate(docs)
    ]


def build_vector_store(transcript: str, collection_name: str = COLLECTION_NAME, segments=None) -> Chroma:
    """Builds a Chroma collection for one meeting.

    `collection_name` should be unique per meeting (e.g. f"meeting_{meeting_id}") —
    Chroma supports many named collections inside the same persist_directory,
    so namespacing this way means processing a new meeting no longer wipes
    out the previous one's embeddings. The old fixed-name default is kept
    only so existing callers (e.g. main.py's CLI flow) don't break.

    `segments` (Phase 2C, optional): a list of transcriber.TranscriptSegment
    with real start/end/source timing. When provided (and non-empty),
    chunks are built directly from the segments so each one carries
    genuine start/end/source metadata for grounded retrieval — see
    _group_segments_into_docs(). When omitted (the default — every
    pre-2C caller), behavior is byte-for-byte the same as before:
    the flat transcript text is split with no timestamp metadata.
    """
    print(f"Building vector store — collection '{collection_name}'")

    docs = _group_segments_into_docs(segments) if segments else []
    if not docs:
        docs = _plain_split_docs(transcript)

    embeddings = get_embeddings()
    vector_store = Chroma.from_documents(
        documents= docs,
        embedding=embeddings,
        collection_name=collection_name,
        persist_directory=CHROMA_DIR
    )

    return vector_store



def load_vector_store(collection_name: str = COLLECTION_NAME) -> Chroma:
    embeddings = get_embeddings()
    vector_store = Chroma(
        collection_name=collection_name,
        embedding_function= embeddings,
        persist_directory=CHROMA_DIR
    )

    return vector_store

def get_retriever(vector_store : Chroma, k :int = 4):
    return vector_store.as_retriever(
        search_type = 'similarity',
        search_kwargs = {"k":k}
    )
