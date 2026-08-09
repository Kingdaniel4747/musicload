"""Authenticate Musicload users against Navidrome."""

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from collections import defaultdict, deque
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

import httpx


class AuthenticationError(Exception):
    """Raised when Navidrome rejects a login."""


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    is_admin: bool


_attempts: dict[str, deque[float]] = defaultdict(deque)
_MAX_ATTEMPTS = 8
_WINDOW_SECONDS = 60


class SignedSessionMiddleware:
    """Small signed-cookie session middleware with no optional dependencies."""

    def __init__(
        self,
        app,
        secret_key: str,
        session_cookie: str = "musicload_session",
        max_age: int = 604800,
        https_only: bool = True,
    ):
        self.app = app
        self.secret = secret_key.encode("utf-8")
        self.cookie_name = session_cookie
        self.max_age = max_age
        self.https_only = https_only

    def _decode(self, value: str) -> dict[str, Any]:
        try:
            encoded, signature = value.rsplit(".", 1)
            expected = hmac.new(
                self.secret, encoded.encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return {}
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(urlsafe_b64decode(encoded + padding))
            if int(payload["expires"]) < int(time.time()):
                return {}
            data = payload["data"]
            return data if isinstance(data, dict) else {}
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {}

    def _encode(self, session: dict[str, Any]) -> str:
        payload = json.dumps(
            {"expires": int(time.time()) + self.max_age, "data": session},
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        signature = hmac.new(
            self.secret, encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cookie = SimpleCookie()
        for key, value in scope.get("headers", []):
            if key.lower() == b"cookie":
                cookie.load(value.decode("latin-1"))
        existing = cookie.get(self.cookie_name)
        session = self._decode(existing.value) if existing else {}
        original = dict(session)
        scope["session"] = session

        async def send_with_cookie(message):
            if message["type"] == "http.response.start" and (
                session != original or (existing and not session)
            ):
                if session:
                    value = self._encode(session)
                    header = (
                        f"{self.cookie_name}={value}; Path=/; Max-Age={self.max_age}; "
                        "HttpOnly; SameSite=Lax"
                    )
                else:
                    header = (
                        f"{self.cookie_name}=null; Path=/; Max-Age=0; "
                        "HttpOnly; SameSite=Lax"
                    )
                if self.https_only:
                    header += "; Secure"
                message.setdefault("headers", []).append(
                    (b"set-cookie", header.encode("latin-1"))
                )
            await send(message)

        await self.app(scope, receive, send_with_cookie)


def check_login_rate_limit(client: str) -> None:
    """Limit repeated authentication attempts per client address."""
    now = time.monotonic()
    attempts = _attempts[client]
    while attempts and now - attempts[0] > _WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= _MAX_ATTEMPTS:
        raise AuthenticationError("Too many login attempts. Please wait one minute.")
    attempts.append(now)


def clear_login_attempts(client: str) -> None:
    _attempts.pop(client, None)


async def authenticate_navidrome(
    server_url: str, username: str, password: str
) -> AuthenticatedUser:
    """Validate credentials through Navidrome's Subsonic-compatible API."""
    salt = secrets.token_hex(16)
    token = hashlib.md5(
        f"{password}{salt}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    parameters = {
        "u": username,
        "t": token,
        "s": salt,
        "v": "1.16.1",
        "c": "Musicload",
        "f": "json",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.get(
                f"{server_url.rstrip('/')}/rest/getUser.view",
                params={**parameters, "username": username},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise AuthenticationError("Navidrome is unavailable.") from exc

    try:
        payload = response.json()["subsonic-response"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthenticationError("Invalid response from Navidrome.") from exc

    if payload.get("status") != "ok":
        raise AuthenticationError("Invalid username or password.")

    user = payload.get("user")
    if not isinstance(user, dict):
        raise AuthenticationError("Invalid username or password.")
    return AuthenticatedUser(
        username=str(user.get("username") or username),
        is_admin=bool(user.get("adminRole", False)),
    )
