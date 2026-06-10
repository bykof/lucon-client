"""Console entry point: ``lucon-api`` launches uvicorn from ``LUCON_`` env config."""

from __future__ import annotations

import uvicorn

from lucon_api.app import create_app
from lucon_api.config import Settings


def main() -> None:
    """Build the app from environment config and serve it with uvicorn."""
    settings = Settings()  # host (and the rest) sourced from LUCON_ env
    app = create_app(settings)
    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
