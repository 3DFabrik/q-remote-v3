"""Logging utilities for Q-Remote V3.

Provides structured logging with JSON or text output based on config.
"""

import logging
import sys
from backend.config import get


def setup_logging() -> None:
    """Configure logging based on config settings."""
    level_name = get("logging.level", "INFO")
    fmt = get("logging.format", "text")
    
    level = getattr(logging, level_name.upper(), logging.INFO)
    
    if fmt == "json":
        # Structured JSON logging
        try:
            import structlog
            structlog.configure(
                processors=[
                    structlog.processors.add_log_level,
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.processors.JSONRenderer(),
                ],
                wrapper_class=structlog.make_filtering_bound_logger(level),
                context_class=dict,
                logger_factory=structlog.PrintLoggerFactory(),
            )
            logging.basicConfig(level=level)
            return
        except ImportError:
            pass  # Fall through to text logging
    
    # Standard text logging
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]  # Replace default handlers
