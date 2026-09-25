import hashlib
import hmac
import secrets
import time

from .db import PanelDB, now

ROLES = ("moderator", "admin", "owner")

PERMISSIONS = {
    "view": "moderator",
    "kick": "moderator",
    "notes": "moderator",
    "ban": "admin",
    "ips": "admin",
    "power": "admin",
    "audit": "admin",
    "console": "owner",
    "users": "owner",
}

SESSION_SECONDS = 7 * 24 * 3600
MIN_PASSWORD = 10


def can(role: str, permission: str) -> bool:
    need = PERMISSIONS[permission]
    return role in ROLES and ROLES.index(role) >= ROLES.index(need)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    attempt = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(attempt.hex(), digest)


DUMMY_HASH = hash_password(secrets.token_hex(16))


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Password must be at least {MIN_PASSWORD} characters."
    return None


def temp_password() -> str:
    return secrets.token_urlsafe(9)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: PanelDB, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    t = now()
    db.write("DELETE FROM sessions WHERE expires_at < ?", t)
    db.write("INSERT INTO sessions (token_hash, user_id, csrf, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
             _token_hash(token), user_id, secrets.token_urlsafe(24), t, t + SESSION_SECONDS)
    db.write("UPDATE users SET last_login = ? WHERE id = ?", t, user_id)
    return token


def session_user(db: PanelDB, token: str | None):
    if not token:
        return None, None
    row = db.one(
        "SELECT users.*, sessions.csrf AS csrf FROM sessions JOIN users ON users.id = sessions.user_id"
        " WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.disabled = 0",
        _token_hash(token), now())
    if row is None:
        return None, None
    return row, row["csrf"]


def end_session(db: PanelDB, token: str | None):
    if token:
        db.write("DELETE FROM sessions WHERE token_hash = ?", _token_hash(token))


def end_all_sessions(db: PanelDB, user_id: int):
    db.write("DELETE FROM sessions WHERE user_id = ?", user_id)


class LoginThrottle:
    """Five wrong passwords for one account locks it for fifteen minutes."""

    def __init__(self, limit=5, window=900):
        self.limit = limit
        self.window = window
        self.failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self.failures.get(key, []) if t > cutoff]
        self.failures[key] = recent
        return recent

    def blocked(self, username: str) -> bool:
        return len(self._recent(username.lower())) >= self.limit

    def failed(self, username: str):
        self._recent(username.lower()).append(time.monotonic())

    def cleared(self, username: str):
        self.failures.pop(username.lower(), None)
