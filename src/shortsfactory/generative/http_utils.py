"""Tiny HTTP helpers with retry/backoff for provider transport (Mission 4).

Focus: bounded retries, exponential backoff, HTTP 429 / 5xx handling, clear
auth/transport errors, and never retrying non-idempotent state after ambiguous
outcomes (video jobs are handled by the backend's submit/poll flow instead).
"""

from __future__ import annotations

import time
from typing import Any

import requests


class ApiTransportError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ApiAuthError(ApiTransportError):
    pass


class ApiRateLimitedError(ApiTransportError):
    def __init__(self, message: str, retry_after: float = 1.0) -> None:
        super().__init__(message, status=429)
        self.retry_after = retry_after


def _sleep_seconds(seconds: float) -> None:
    time.sleep(max(0.0, seconds))


def request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 60.0,
    attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 15.0,
    retryable_statuses: set[int] | None = None,
    session: requests.Session | None = None,
) -> requests.Response:
    """Perform an HTTP request with bounded exponential backoff.

    - 401/403 -> ApiAuthError immediately (no retry; nothing to gain).
    - 429 -> backoff honoring Retry-After when present.
    - 5xx -> backoff.
    - network errors -> backoff (bounded).
    - other non-2xx -> ApiTransportError.
    """
    sess = session or requests.Session()
    last_exc: Exception | None = None

    for attempt in range(max(1, attempts)):
        try:
            response = sess.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
                timeout=timeout,
            )
            if response.status_code in (401, 403):
                raise ApiAuthError(
                    f"Ошибка авторизации {response.status_code} у {url}. "
                    f"Проверьте ключ API (маскирован). {response.text[:200]}",
                    status=response.status_code,
                )
            if response.status_code == 429:
                retry_after = 1.0
                raw = response.headers.get("Retry-After")
                if raw:
                    try:
                        retry_after = float(raw)
                    except ValueError:
                        retry_after = 1.0
                retry_after = min(max(retry_after, 0.5), max_delay)
                if attempt < attempts - 1:
                    _sleep_seconds(retry_after)
                    continue
                raise ApiRateLimitedError(
                    f"Rate limit (429) на {url} после {attempts} попыток", retry_after=retry_after
                )
            if response.status_code >= 500 and attempt < attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                _sleep_seconds(delay)
                continue
            if response.status_code >= 400:
                raise ApiTransportError(
                    f"HTTP {response.status_code} на {url}: {response.text[:300]}",
                    status=response.status_code,
                )
            return response
        except (requests.RequestException, ApiTransportError) as exc:
            if isinstance(exc, ApiAuthError):
                raise
            if isinstance(exc, ApiRateLimitedError):
                raise
            last_exc = exc
            if attempt < attempts - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                _sleep_seconds(delay)

    raise ApiTransportError(f"Сбой запроса к {url} после {attempts} попыток: {last_exc}")
