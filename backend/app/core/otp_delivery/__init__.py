from typing import Any


def delivery_succeeded(result: Any, *, allow_mock: bool = False) -> bool:
    if not isinstance(result, dict) or result.get("success") is not True:
        return False
    if result.get("mock") is True and not allow_mock:
        return False
    return True


def delivery_error(result: Any) -> str:
    if not isinstance(result, dict):
        return "Delivery provider returned no result"
    return str(
        result.get("error")
        or result.get("reason")
        or result.get("status")
        or "Delivery provider rejected the message"
    )


def delivery_status(result: Any) -> str:
    if not isinstance(result, dict):
        return "failed"
    if result.get("mock") is True:
        return "mock"
    if result.get("success") is not True:
        return "failed"
    return str(result.get("status") or "sent")
