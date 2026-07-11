"""HermesClawZero — modular memory management API."""

import logging

logging.basicConfig(
    level=logging.getLevelNamesMapping().get(
        __import__("os").getenv("LOG_LEVEL", "INFO").upper(), logging.INFO
    ),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("hermesclaw")
