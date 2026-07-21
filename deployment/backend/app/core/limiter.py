"""Shared rate limiter — single instance used by all endpoints."""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request
import ipaddress


def get_real_ip(request: Request) -> str:
    """
    Get the real client IP address, with proper proxy handling.
    Only trusts X-Forwarded-For from known proxy IPs (localhost/private ranges).
    This prevents IP spoofing attacks while still working behind reverse proxies.
    """
    # First get the direct remote address
    remote_ip = get_remote_address(request)

    # Check if the remote IP is from a trusted proxy (private/loopback)
    try:
        remote_addr = ipaddress.ip_address(remote_ip)
        is_trusted_proxy = remote_addr.is_private or remote_addr.is_loopback
    except ValueError:
        is_trusted_proxy = False

    # Only trust X-Forwarded-For if the remote is a trusted proxy
    if is_trusted_proxy:
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            try:
                client_ip = forwarded_for.split(",")[0].strip()
                # Validate it's a valid IP
                ipaddress.ip_address(client_ip)
                return client_ip
            except ValueError:
                pass

    # Fall back to the direct remote address
    return remote_ip


limiter = Limiter(key_func=get_real_ip, default_limits=["200/minute"])
