"""Server command-line entry point."""

import uvicorn

from c2_relay.api.app import create_app
from c2_relay.core.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
    )
