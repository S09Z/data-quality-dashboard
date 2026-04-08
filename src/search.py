"""Haystack-based search engine over data quality reports.

Uses InMemoryDocumentStore with BM25 retrieval — no model download needed.
"""

from __future__ import annotations

import logging

from haystack import Document
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.document_stores.in_memory import InMemoryDocumentStore

logger = logging.getLogger(__name__)


class SearchEngine:
    """BM25 keyword search over indexed validation reports."""

    def __init__(self) -> None:
        self._store = InMemoryDocumentStore()
        self._retriever = InMemoryBM25Retriever(document_store=self._store)

    def index(self, reports: list[str]) -> None:
        """Index a list of plain-text report strings as Haystack Documents."""
        documents = [
            Document(content=report, id=str(i)) for i, report in enumerate(reports)
        ]
        # Write with policy=OVERWRITE so re-indexing is safe
        self._store.write_documents(documents, policy="overwrite")
        logger.info("Indexed %d documents into the search engine.", len(documents))

    def query(self, text: str, top_k: int = 5) -> list[str]:
        """Run BM25 search and return the top-k matching report strings."""
        if self._store.count_documents() == 0:
            logger.warning("Search index is empty — run index() first.")
            return []
        results = self._retriever.run(query=text, top_k=top_k)
        return [doc.content for doc in results["documents"] if doc.content]

    def count(self) -> int:
        """Return the number of indexed documents."""
        return self._store.count_documents()


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
