"""Login brute-force protection: per-IP rate limiting with temporary lockout."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import Request

from backend.config import get

logger = logging.getLogger(__name__)

_attempts: dict[str, list[float]] = {}
_lockouts: dict[str, float] = {}


def _cfg() -> dict[str, Any]:
    return {
        "enabled": bool(get("auth.login_rate_limit.enabled", True)),
        "max_attempts": int(get("auth.login_rate_limit.max_attempts", 5)),
        "window_seconds": int(get("auth.login_rate_limit.window_seconds", 900)),
        "lockout_seconds": int(get("auth.login_rate_limit.lockout_seconds", 900)),
        "delay_seconds": float(get("auth.login_rate_limit.delay_seconds", 0.75)),
        "trusted_proxies": get("auth.login_rate_limit.trusted_proxies", ["127.0.0.1", "::1"]),
    }


def get_client_ip(request: Request) -> str:
    """Client IP; honor X-Forwarded-For only from trusted reverse proxies."""
    cfg = _cfg()
    trusted = set(cfg["trusted_proxies"] or [])
    direct = request.client.host if request.client else "unknown"

    if direct in trusted:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

    return direct


def _prune(timestamps: list[float], cutoff: float) -> list[float]:
    return [t for t in timestamps if t > cutoff]


def check_login_allowed(ip: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds). retry_after is 0 when allowed."""
    cfg = _cfg()
    if not cfg["enabled"]:
        return True, 0

    now = time.time()
    lock_until = _lockouts.get(ip)
    if lock_until is not None:
        if now < lock_until:
            return False, max(1, int(lock_until - now))
        _lockouts.pop(ip, None)
        _attempts.pop(ip, None)

    cutoff = now - cfg["window_seconds"]
    recent = _prune(_attempts.get(ip, []), cutoff)
    _attempts[ip] = recent

    if len(recent) >= cfg["max_attempts"]:
        _lockouts[ip] = now + cfg["lockout_seconds"]
        logger.warning(
            "Login rate limit: locked %s for %ss (%s failures in window)",
            ip,
            cfg["lockout_seconds"],
            len(recent),
        )
        return False, cfg["lockout_seconds"]

    return True, 0


def record_login_failure(ip: str) -> None:
    cfg = _cfg()
    if not cfg["enabled"]:
        return

    now = time.time()
    cutoff = now - cfg["window_seconds"]
    attempts = _prune(_attempts.setdefault(ip, []), cutoff)
    attempts.append(now)
    _attempts[ip] = attempts

    if len(attempts) >= cfg["max_attempts"]:
        _lockouts[ip] = now + cfg["lockout_seconds"]
        logger.warning(
            "Login rate limit: locked %s for %ss after failed attempt",
            ip,
            cfg["lockout_seconds"],
        )


def record_login_success(ip: str) -> None:
    _attempts.pop(ip, None)
    _lockouts.pop(ip, None)


async def apply_login_delay() -> None:
    delay = _cfg()["delay_seconds"]
    if delay > 0:
        await asyncio.sleep(delay)


def format_retry_message(retry_after_seconds: int) -> str:
    minutes = max(1, (retry_after_seconds + 59) // 60)
    if minutes == 1:
        return "Too many login attempts. Please try again in about 1 minute."
    return f"Too many login attempts. Please try again in about {minutes} minutes."
