"""Haystack-based search engine over data quality reports.

Supports two modes selected at construction time:
- **BM25** (default) — keyword search via InMemoryBM25Retriever.
  No model download required; instant startup.
- **Semantic** — dense vector search via SentenceTransformers embeddings
  + InMemoryEmbeddingRetriever. Activated when ``semantic=True``.
"""

from __future__ import annotations

import logging

from haystack import Document
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.document_stores.in_memory import InMemoryDocumentStore

logger = logging.getLogger(__name__)


class SearchEngine:
    """Keyword (BM25) or semantic search over indexed validation reports.

    Parameters
    ----------
    semantic:
        When *True*, uses SentenceTransformers dense-vector retrieval.
        Requires ``sentence-transformers`` to be installed and triggers a
        one-time model download on first ``index()`` call.
    model:
        HuggingFace model ID used when ``semantic=True``.
    """

    def __init__(
        self,
        semantic: bool = False,
        model: str = "sentence-transformers/all-MiniLM-L6-v2",
    ) -> None:
        self._semantic = semantic
        self._model = model
        self._store = InMemoryDocumentStore(
            embedding_similarity_function="cosine" if semantic else "dot_product"
        )
        self._retriever = InMemoryBM25Retriever(document_store=self._store)

        if semantic:
            self._init_semantic()

    # ------------------------------------------------------------------
    # Semantic initialisation (lazy — only runs when semantic=True)
    # ------------------------------------------------------------------

    def _init_semantic(self) -> None:
        """Import and warm up the SentenceTransformers embedders."""
        try:
            from haystack.components.embedders import (
                SentenceTransformersDocumentEmbedder,
                SentenceTransformersTextEmbedder,
            )
            from haystack.components.retrievers import (
                InMemoryEmbeddingRetriever,
            )
        except ImportError as exc:
            raise ImportError(
                "Semantic search requires 'sentence-transformers'. "
                "Install it with: uv add sentence-transformers"
            ) from exc

        self._doc_embedder = SentenceTransformersDocumentEmbedder(model=self._model)
        self._text_embedder = SentenceTransformersTextEmbedder(model=self._model)
        self._retriever = InMemoryEmbeddingRetriever(document_store=self._store)

        logger.info("Warming up semantic embedder (%s)…", self._model)
        self._doc_embedder.warm_up()
        self._text_embedder.warm_up()
        logger.info("Semantic embedder ready.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index(self, reports: list[str]) -> None:
        """Index a list of plain-text report strings as Haystack Documents."""
        if self._semantic:
            documents = [Document(content=r) for r in reports]
            result = self._doc_embedder.run(documents)
            docs_with_embeddings = result["documents"]
        else:
            docs_with_embeddings = [
                Document(content=r, id=str(i)) for i, r in enumerate(reports)
            ]

        self._store.write_documents(docs_with_embeddings, policy="overwrite")
        logger.info(
            "Indexed %d documents (mode=%s).",
            len(reports),
            "semantic" if self._semantic else "bm25",
        )

    def query(self, text: str, top_k: int = 5) -> list[str]:
        """Retrieve the top-k matching report strings for *text*."""
        if self._store.count_documents() == 0:
            logger.warning("Search index is empty — run index() first.")
            return []

        if self._semantic:
            embed_result = self._text_embedder.run(text=text)
            retrieval = self._retriever.run(
                query_embedding=embed_result["embedding"], top_k=top_k
            )
        else:
            retrieval = self._retriever.run(query=text, top_k=top_k)

        return [doc.content for doc in retrieval["documents"] if doc.content]

    def count(self) -> int:
        """Return the number of indexed documents."""
        return self._store.count_documents()

    @property
    def mode(self) -> str:
        """Return 'semantic' or 'bm25'."""
        return "semantic" if self._semantic else "bm25"


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
