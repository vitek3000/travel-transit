"""`python -m travel_transit`: run the service with the settings of the environment (127.0.0.1:8790 by default)."""

import logging

import uvicorn

from travel_transit.app import create_app
from travel_transit.config import Settings


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    # no access log: the service's own lines say what it needs to, with the points rounded
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
