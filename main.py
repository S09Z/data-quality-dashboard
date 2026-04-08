"""Entry point — starts the FastAPI server with uvicorn."""

from __future__ import annotations

import uvicorn

from src.config import settings
from src.logging_config import configure_logging


def main() -> None:
    configure_logging(log_level=settings.log_level, log_json=settings.log_json)
    uvicorn.run(
        "src.api:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
