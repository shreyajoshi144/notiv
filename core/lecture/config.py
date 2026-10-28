"""Process-wide wiring: which repository and storage backend are in use.
Everything else receives these as parameters; this is the only place that
reads deployment environment variables for them.

  NOTIV_STORAGE=local (default) | s3
  NOTIV_STORAGE_DIR / NOTIV_SIGNING_SECRET            (local)
  NOTIV_S3_BUCKET / NOTIV_S3_PREFIX / NOTIV_S3_ENDPOINT   (s3)
  NOTIV_LECTURE_DB                                    (sqlite file)
  NOTIV_LLM_PROVIDERS + provider keys (see llm.py)   (answer model for Ask Notiv / AI quiz; NOTIV_ASK_LLM=off -> quote-only Ask)
  NOTIV_EMBEDDINGS=local (default) | mistral          (retrieval vectors)
"""
import os
from typing import Optional

from .repo import LectureRepository, SqliteLectureRepository
from .storage import LocalStorage, S3Storage, StorageService

_repo: Optional[LectureRepository] = None
_storage: Optional[StorageService] = None
_llm = None
_embedder = None


def configure(repo: Optional[LectureRepository] = None, storage: Optional[StorageService] = None,
              llm=None, embedder=None) -> None:
    global _repo, _storage, _llm, _embedder
    if repo is not None:
        _repo = repo
    if storage is not None:
        _storage = storage
    if llm is not None:
        _llm = llm
    if embedder is not None:
        _embedder = embedder


def get_llm():
    """`llm(system, user) -> str` for Ask Notiv, or None = extractive (quote-only) answers. Set via configure(llm=...)
    or, by default, the provider chain (llm.py) when at least one provider has a key; NOTIV_ASK_LLM=off forces extractive."""
    if _llm is not None:
        return _llm
    if os.getenv("NOTIV_ASK_LLM", "auto") == "off":
        return None
    return get_llm_chain() or None


_chain = None
_chain_key = None


def get_llm_chain():
    """The shared provider chain (also used by the AI quiz). Rebuilt when the provider env changes, so cool-downs persist
    across requests but a changed key or order takes effect without a restart."""
    global _chain, _chain_key
    from . import llm
    key = _llm_env_key()
    if _chain is None or key != _chain_key:
        _chain, _chain_key = llm.build_chain(), key
    return _chain


_vision = None
_vision_key = None


def get_vision_chain():
    """Providers that can read page images (handwritten / scanned notes); empty chain when none is configured."""
    global _vision, _vision_key
    from . import llm
    key = _llm_env_key()
    if _vision is None or key != _vision_key:
        _vision, _vision_key = llm.build_vision_chain(), key
    return _vision


_LLM_ENV_PREFIXES = ("NOTIV_LLM_", "NOTIV_VISION_", "NOTIV_OPENAI_", "NOTIV_GROQ_", "NOTIV_MISTRAL_", "NOTIV_ANTHROPIC_",
                     "NOTIV_LOVABLE_", "OPENAI_", "GROQ_", "MISTRAL_", "ANTHROPIC_", "LOVABLE_")


def _llm_env_key() -> tuple:
    return tuple(sorted((k, v) for k, v in os.environ.items() if k.startswith(_LLM_ENV_PREFIXES)))


def get_embedder():
    if _embedder is not None:
        return _embedder
    from . import retrieval
    return retrieval.default_embedder()


def get_repo() -> LectureRepository:
    global _repo
    if _repo is None:
        _repo = SqliteLectureRepository(os.getenv("NOTIV_LECTURE_DB", os.path.join("storage", "lectures.db")))
    return _repo


def get_storage() -> StorageService:
    global _storage
    if _storage is None:
        if os.getenv("NOTIV_STORAGE", "local") == "s3":
            _storage = S3Storage(os.environ["NOTIV_S3_BUCKET"], os.getenv("NOTIV_S3_PREFIX", ""),
                                 os.getenv("NOTIV_S3_ENDPOINT") or None)
        else:
            _storage = LocalStorage(os.getenv("NOTIV_STORAGE_DIR", os.path.join("storage", "objects")))
    return _storage
