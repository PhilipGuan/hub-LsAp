"""Start the FastAPI service."""

import logging

from backend.logging_setup import configure_logging

logger = logging.getLogger("backend.main")


def main() -> None:
    configure_logging()
    import uvicorn

    host, port = "0.0.0.0", 8000
    logger.info("Starting FastAPI on %s:%d", host, port)
    uvicorn.run("backend.api.main:app", host=host, port=port, log_config=None)


if __name__ == "__main__":
    main()
