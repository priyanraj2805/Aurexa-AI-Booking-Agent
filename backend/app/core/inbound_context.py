"""Ambient inbound-message context.

The provider message id (Meta ``wamid`` / Baileys ``key.id``) identifies the
exact inbound message a transcript row came from. Threading that id as a
parameter through every persistence helper is easy to forget — several call
sites persisted user turns with no id at all, which silently broke
traceability while every test still passed.

A ContextVar makes it structural instead of a convention: the inbound handler
scopes the id once, and any ``crud_chat.save_message`` call made while
handling that message picks it up automatically. Explicit arguments still win,
so a deliberate override is always possible.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_current_inbound_message_id: ContextVar[Optional[str]] = ContextVar(
    "current_inbound_message_id", default=None
)


def set_inbound_message_id(message_id: Optional[str]) -> None:
    cleaned = str(message_id or "").strip()
    _current_inbound_message_id.set(cleaned or None)


def get_inbound_message_id() -> Optional[str]:
    """Provider id for the inbound message currently being handled, if any."""
    return _current_inbound_message_id.get()


@contextmanager
def inbound_message(message_id: Optional[str]) -> Iterator[None]:
    """Scope the inbound id to the handling of one message.

    Restores the previous value on exit so a worker never leaks one
    conversation's id into the next.
    """
    token = _current_inbound_message_id.set(str(message_id or "").strip() or None)
    try:
        yield
    finally:
        _current_inbound_message_id.reset(token)
