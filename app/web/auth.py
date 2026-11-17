"""Локальная аутентификация: пароль никогда не хранится в открытом виде."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

ITERATIONS = 600_000
MIN_PASSWORD = 14
SESSION_SECONDS = 4 * 3600
MAX_SESSIONS = 24
PREFIX = "pbkdf2_sha256"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < MIN_PASSWORD or len(password) > 512:
        raise ValueError(f"Пароль должен содержать от {MIN_PASSWORD} до 512 символов.")
    salt = salt or secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{PREFIX}:{ITERATIONS}:{salt.hex()}:{digest.hex()}"


def validate_hash(value: str) -> bool:
    try:
        scheme, rounds, salt, digest = value.split(":")
        return (scheme == PREFIX and int(rounds) >= ITERATIONS
                and len(bytes.fromhex(salt)) >= 16 and len(bytes.fromhex(digest)) == 32)
    except (ValueError, TypeError, AttributeError):
        return False


def verify_password(password: str, stored: str) -> bool:
    if not validate_hash(stored) or not isinstance(password, str) or len(password) > 512:
        return False
    _, rounds, salt, expected = stored.split(":")
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(candidate, bytes.fromhex(expected))


@dataclass(slots=True)
class Session:
    csrf: str
    expires_at: float
    user_id: int | None = None


class SessionStore:
    """Храним только SHA256 от случайного session-cookie; после restart вход повторный."""
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.failures: dict[str, list[float]] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def _prune(self, now: float) -> None:
        self.sessions = {key: session for key, session in self.sessions.items()
                         if session.expires_at > now}
        self.failures = {key: [t for t in values if now - t < 300]
                         for key, values in self.failures.items()
                         if any(now - t < 300 for t in values)}

    def limited(self, address: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self._prune(now)
        return len(self.failures.get(address, ())) >= 5

    def fail(self, address: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._prune(now)
        self.failures.setdefault(address, []).append(now)

    def create(self, *, now: float | None = None,
               user_id: int | None = None) -> tuple[str, Session]:
        now = time.time() if now is None else now
        self._prune(now)
        if len(self.sessions) >= MAX_SESSIONS:
            oldest = min(self.sessions, key=lambda key: self.sessions[key].expires_at)
            self.sessions.pop(oldest, None)
        token = secrets.token_urlsafe(32)
        session = Session(csrf=secrets.token_urlsafe(32), expires_at=now + SESSION_SECONDS,
                          user_id=user_id)
        self.sessions[self._key(token)] = session
        return token, session

    def get(self, token: str | None, *, now: float | None = None) -> Session | None:
        if not isinstance(token, str) or not token.isascii() or len(token) > 150:
            return None
        now = time.time() if now is None else now
        self._prune(now)
        return self.sessions.get(self._key(token))

    def revoke(self, token: str | None) -> None:
        if isinstance(token, str) and token.isascii() and len(token) <= 150:
            self.sessions.pop(self._key(token), None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор хэша пароля локальной панели.")
    parser.add_argument("command", choices=("hash",))
    parser.parse_args()
    first = getpass.getpass("Пароль панели (от 14 символов): ")
    second = getpass.getpass("Повторите пароль: ")
    if first != second:
        raise SystemExit("Пароли не совпадают.")
    try:
        print("WEB_ADMIN_PASSWORD_HASH=" + hash_password(first))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
