from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def configure_logging(debug: bool = True) -> None:
    """Configures structured logging using structlog.

    In debug/dev mode, logs are formatted for human readability on stderr.
    Otherwise, logs are formatted as JSON.
    """
    # Root logging configuration
    root_log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=root_log_level,
    )

    # Structlog processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    processors: list[Any]
    if debug:
        # Human-friendly development logging
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(),
        ]
    else:
        # Production JSON logging
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # NOT cached: a cached module logger binds the active processor chain
        # permanently, so once the app bootstraps mid-test-suite, structlog's
        # `capture_logs` (used by the voice diagnostics tests) can no longer swap
        # the chain to intercept those loggers' events. Leaving caching off keeps
        # log-capture tests order-independent at negligible runtime cost.
        cache_logger_on_first_use=False,
    )
