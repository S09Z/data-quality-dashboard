"""v1 router package — aggregates all v1 sub-routers."""

from __future__ import annotations

from fastapi import APIRouter

from src.routers.v1 import meta, pipeline, search

router = APIRouter()

router.include_router(meta.router)
router.include_router(pipeline.router)
router.include_router(search.router)
