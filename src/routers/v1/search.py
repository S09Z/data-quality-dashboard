"""Search routes — v1."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from src.schemas import SearchResponse

router = APIRouter(tags=["search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "example": {
                        "query": "null values",
                        "results": [
                            "File: sales.csv\nTotal rows: 10\nValid rows: 7\n"
                            "Invalid rows: 3\nStatus: FAILED\n"
                            "  - Column 'unit_price' failed check 'not_nullable'"
                            " at row 8 (value: None)"
                        ],
                        "total": 1,
                    }
                }
            }
        }
    },
)
def search(
    request: Request,
    q: str = Query(
        ...,
        min_length=1,
        max_length=500,
        description="Search query over validation reports.",
    ),
    top_k: int = Query(
        default=5, ge=1, le=20, description="Number of results to return."
    ),
) -> SearchResponse:
    """
    BM25 keyword search over indexed data quality reports.

    Returns matching report snippets ranked by relevance.
    """
    results = request.app.state.engine.query(text=q, top_k=top_k)
    return SearchResponse(query=q, results=results, total=len(results))
