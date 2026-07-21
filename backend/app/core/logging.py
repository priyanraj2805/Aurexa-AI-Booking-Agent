import logging
import sys
import structlog
from asgi_correlation_id.context import correlation_id

def add_correlation_id(logger, method_name, event_dict):
    if req_id := correlation_id.get():
        event_dict["request_id"] = req_id
    return event_dict

def setup_logging():
    # Setup structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            add_correlation_id,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format="%(message)s",
    )
    
    # Set third-party loggers to warning to reduce noise
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

