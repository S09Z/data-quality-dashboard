"""Haystack-based search engine over data quality reports.

Design
------
Retrieval behaviour is encapsulated behind the **Strategy** pattern:

* ``_BM25Strategy``     — keyword search (default, no model download).
* ``_SemanticStrategy`` — dense-vector search via SentenceTransformers.

``SearchEngine`` selects the right strategy at construction time and
delegates all ``index`` / ``query`` calls to it.  Adding a new backend
(e.g. Elasticsearch) requires only a new ``_RetrievalStrategy`` subclass.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from haystack import Document
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.document_stores.in_memory import InMemoryDocumentStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Strategy ABC + concrete implementations
# ---------------------------------------------------------------------------


class _RetrievalStrategy(ABC):
    """Abstract base for a retrieval backend."""

    @abstractmethod
    def index(self, store: InMemoryDocumentStore, reports: list[str]) -> None:
        """Write *reports* into *store*."""

    @abstractmethod
    def query(self, store: InMemoryDocumentStore, text: str, top_k: int) -> list[str]:
        """Return up to *top_k* matching report strings for *text*."""


class _BM25Strategy(_RetrievalStrategy):
    """Keyword-based BM25 retrieval — no model required."""

    def __init__(self) -> None:
        # Retriever is recreated per store reference; kept as instance attr
        # so SearchEngine can swap the store later if needed.
        self._retriever: InMemoryBM25Retriever | None = None

    def _get_retriever(self, store: InMemoryDocumentStore) -> InMemoryBM25Retriever:
        if self._retriever is None:
            self._retriever = InMemoryBM25Retriever(document_store=store)
        return self._retriever

    def index(self, store: InMemoryDocumentStore, reports: list[str]) -> None:
        docs = [Document(content=r, id=str(i)) for i, r in enumerate(reports)]
        store.write_documents(docs, policy="overwrite")

    def query(self, store: InMemoryDocumentStore, text: str, top_k: int) -> list[str]:
        retriever = self._get_retriever(store)
        result = retriever.run(query=text, top_k=top_k)
        return [doc.content for doc in result["documents"] if doc.content]


class _SemanticStrategy(_RetrievalStrategy):
    """Dense-vector retrieval via SentenceTransformers."""

    def __init__(self, model: str) -> None:
        self._model = model
        self._doc_embedder = None
        self._text_embedder = None
        self._retriever = None
        self._warm = False

    def _init(self, store: InMemoryDocumentStore) -> None:
        """Import and warm up embedders (runs once)."""
        try:
            from haystack.components.embedders import (
                SentenceTransformersDocumentEmbedder,
                SentenceTransformersTextEmbedder,
            )
            from haystack.components.retrievers import InMemoryEmbeddingRetriever
        except ImportError as exc:
            raise ImportError(
                "Semantic search requires 'sentence-transformers'. "
                "Install it with: uv add sentence-transformers"
            ) from exc

        self._doc_embedder = SentenceTransformersDocumentEmbedder(model=self._model)
        self._text_embedder = SentenceTransformersTextEmbedder(model=self._model)
        self._retriever = InMemoryEmbeddingRetriever(document_store=store)

        logger.info("Warming up semantic embedder (%s)…", self._model)
        self._doc_embedder.warm_up()
        self._text_embedder.warm_up()
        logger.info("Semantic embedder ready.")
        self._warm = True

    def index(self, store: InMemoryDocumentStore, reports: list[str]) -> None:
        if not self._warm:
            self._init(store)
        documents = [Document(content=r) for r in reports]
        result = self._doc_embedder.run(documents)
        store.write_documents(result["documents"], policy="overwrite")

    def query(self, store: InMemoryDocumentStore, text: str, top_k: int) -> list[str]:
        if not self._warm:
            self._init(store)
        embed_result = self._text_embedder.run(text=text)
        retrieval = self._retriever.run(
            query_embedding=embed_result["embedding"], top_k=top_k
        )
        return [doc.content for doc in retrieval["documents"] if doc.content]


# ---------------------------------------------------------------------------
# SearchEngine — delegates to the chosen strategy
# ---------------------------------------------------------------------------


class SearchEngine:
    """Keyword (BM25) or semantic search over indexed validation reports.

    Parameters
    ----------
    semantic:
        When *True*, uses SentenceTransformers dense-vector retrieval.
    model:
        HuggingFace model ID used when ``semantic=True``.
    """

    def __init__(
        self,
        semantic: bool = False,
        model: str = "sentence-transformers/all-MiniLM-L6-v2",
    ) -> None:
        self._semantic = semantic
        self._store = InMemoryDocumentStore(
            embedding_similarity_function="cosine" if semantic else "dot_product"
        )
        self._strategy: _RetrievalStrategy = (
            _SemanticStrategy(model) if semantic else _BM25Strategy()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index(self, reports: list[str]) -> None:
        """Index a list of plain-text report strings."""
        self._strategy.index(self._store, reports)
        logger.info("Indexed %d documents (mode=%s).", len(reports), self.mode)

    def query(self, text: str, top_k: int = 5) -> list[str]:
        """Retrieve the top-k matching report strings for *text*."""
        if self._store.count_documents() == 0:
            logger.warning("Search index is empty — run index() first.")
            return []
        return self._strategy.query(self._store, text, top_k)

    def count(self) -> int:
        """Return the number of indexed documents (delegates to ``__len__``)."""
        return len(self)

    @property
    def mode(self) -> str:
        """Return ``'semantic'`` or ``'bm25'``."""
        return "semantic" if self._semantic else "bm25"

    # ------------------------------------------------------------------
    # Dunder methods
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return self._store.count_documents()

    def __repr__(self) -> str:
        return f"SearchEngine(mode={self.mode!r}, docs={len(self)})"


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def build_report_from_validation(result: object) -> str:
    """Convert a ValidationResult into a human-readable report string."""
    lines: list[str] = [
        f"File: {result.file}",  # type: ignore[attr-defined]
        f"Total rows: {result.total_rows}",  # type: ignore[attr-defined]
        f"Valid rows: {result.valid_rows}",  # type: ignore[attr-defined]
        f"Invalid rows: {result.invalid_rows}",  # type: ignore[attr-defined]
        f"Status: {'PASSED' if result.is_valid else 'FAILED'}",  # type: ignore[attr-defined]
    ]
    for err in result.errors:  # type: ignore[attr-defined]
        lines.append(
            f"  - Column '{err.column}' failed check '{err.check}'"
            + (f" at row {err.row_index}" if err.row_index is not None else "")
            + (f" (value: {err.failure_case})" if err.failure_case is not None else "")
        )
    return "\n".join(lines)
