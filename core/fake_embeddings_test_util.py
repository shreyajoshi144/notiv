"""
TEST-ONLY. A deterministic, dependency-free stand-in for
HuggingFaceEmbeddings so tests can exercise a REAL Chroma collection
(already installed, no torch needed) without needing
sentence-transformers/torch, which aren't installed in this sandbox.

This is a crude bag-of-words hashing vector — NOT semantically
meaningful the way a real embedding model is. It's only good enough to
retrieve a chunk that shares distinctive words with the query, which is
exactly what these tests control for. Do not use for anything but
tests.
"""

from langchain_core.embeddings import Embeddings

DIM = 64


class FakeHashingEmbeddings(Embeddings):
    def _vec(self, text: str):
        vec = [0.0] * DIM
        for word in text.lower().split():
            vec[hash(word) % DIM] += 1.0
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)
