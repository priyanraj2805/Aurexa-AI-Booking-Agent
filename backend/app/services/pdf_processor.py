"""Legacy shim — kept so existing imports keep working.

All real extraction now lives in ``app.services.document_processor``.
"""

from app.services.document_processor import (  # noqa: F401
    chunk_text,
    compute_content_hash,
    process_document,
    process_pdf,
)