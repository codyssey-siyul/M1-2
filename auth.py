"""Single evaluator account, opaque sessions, CSRF and persistent call budgets."""
from dataclasses import dataclass
import hashlib
import hmac
import math
import os
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import HTTPException


def password_hash(password, salt=None):
    """Offline helper; caller must not print passwords or generated verifiers."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


@dataclass(frozen=True)
class AuthConfig:
    username: str
    verifier: str
    secret: bytes
    origins: tuple
    production: bool = True
    version: str = "1"
    absolute: int = 28800
    idle: int = 3600
    login_ip: int = 10
    login_global: int = 60
    ai_session: int = 3
    ai_minute: int = 5
    ai_day: int = 100

    @classmethod
    def from_env(cls):
        try:
            env = os.getenv("APP_ENV", "production")
            if env not in {"production", "development"}:
                raise ValueError
            user = os.getenv("EVAL_USERNAME", "")
            verifier = os.getenv("EVAL_PASSWORD_HASH", "")
            secret = os.getenv("AUTH_SECRET", "").encode()
            origins = tuple(origin.strip() for origin in os.getenv("AUTH_ALLOWED_ORIGINS", "").split(",") if origin.strip())
            if not user.strip() or len(user) > 100 or len(secret) < 32 or not origins:
                raise ValueError
            parts = verifier.split("$")
            if len(parts) != 6 or parts[:4] != ["scrypt", "16384", "8", "1"]:
                raise ValueError
            if len(bytes.fromhex(parts[4])) != 16 or len(bytes.fromhex(parts[5])) != 32:
                raise ValueError
            for origin in origins:
                parsed = urlsplit(origin)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username or "*" in origin:
                    raise ValueError
                if env == "production" and parsed.scheme != "https":
                    raise ValueError

            def number(name, default, lower, upper):
                value = int(os.getenv(name, str(default)))
                if not lower <= value <= upper:
                    raise ValueError
                return value

            version = os.getenv("AUTH_VERSION", "1")
            if not version or len(version) > 100:
                raise ValueError
            return cls(user, verifier, secret, origins, env == "production", version,
                       number("SESSION_ABSOLUTE_SECONDS", 28800, 300, 86400),
                       number("SESSION_IDLE_SECONDS", 3600, 60, 14400),
                       number("LOGIN_IP_LIMIT", 10, 1, 50), number("LOGIN_GLOBAL_LIMIT", 60, 1, 200),
                       number("AI_SESSION_MINUTE_LIMIT", 3, 1, 10), number("AI_GLOBAL_MINUTE_LIMIT", 5, 1, 20),
                       number("AI_GLOBAL_DAY_LIMIT", 100, 1, 500))
        except (ValueError, TypeError):
            raise HTTPException(503, "평가 계정 인증 설정이 필요합니다.") from None

    @property
    def cookie(self):
        return "__Host-m1_session" if self.production else "m1_session"


class SecurityService:
    def __init__(self, config, store, clock=time.time):
        self.config = config
        self.store = store
        self.clock = clock

    def digest(self, purpose, value):
        return hmac.new(self.config.secret, (purpose + ":" + value).encode(), hashlib.sha256).hexdigest()

    def csrf(self, token):
        return self.digest("csrf", token)

    def origin(self, origin):
        if origin not in self.config.origins:
            raise HTTPException(403, "허용되지 않은 요청 출처입니다.")

    def _session(self, tx, id, now):
        row = tx.get("auth_sessions", id)
        if (not row or row.get("version") != self.config.version
                or now >= row["expires_at"] or now - row["last_active"] >= self.config.idle):
            raise HTTPException(401, "로그인이 필요하거나 세션이 만료되었습니다.")
        return row

    def authenticate(self, token, touch=False):
        if not token or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise HTTPException(401, "로그인이 필요하거나 세션이 만료되었습니다.")
        id = self.digest("session", token)
        now = self.clock()

        def work(tx):
            row = self._session(tx, id, now)
            if touch:
                tx.set("auth_sessions", id, dict(row, last_active=now))
            return dict(row, id=id)

        return self.store.atomic(work)

    def check_csrf(self, token, submitted):
        if not submitted or not re.fullmatch(r"[a-f0-9]{64}", submitted) or not hmac.compare_digest(self.csrf(token), submitted):
            raise HTTPException(403, "요청 검증에 실패했습니다. 새로고침 후 다시 시도해 주세요.")

    @staticmethod
    def limited(seconds):
        raise HTTPException(429, "사용 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
                            headers={"Retry-After": str(max(1, math.ceil(seconds)))})

    def login(self, username, password, ip, previous=None):
        now = self.clock()
        bucket = int(now // 600)
        keys = ["login_global", self.digest("login-ip", ip)]

        def attempts(tx):
            rows = [tx.get("auth_limits", key) or {} for key in keys]
            counts = [row.get("count", 0) if row.get("bucket") == bucket else 0 for row in rows]
            for count, limit in zip(counts, [self.config.login_global, self.config.login_ip]):
                if count >= limit:
                    self.limited((bucket + 1) * 600 - now)
            for key, count in zip(keys, counts):
                tx.set("auth_limits", key, {"bucket": bucket, "count": count + 1})

        self.store.atomic(attempts)
        parts = self.config.verifier.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(parts[4]), n=16384, r=8, p=1, dklen=32)
        correct_password = hmac.compare_digest(digest, bytes.fromhex(parts[5]))
        correct_username = hmac.compare_digest(username.encode(), self.config.username.encode())
        if not (correct_password and correct_username):
            raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
        token = secrets.token_urlsafe(32)
        id = self.digest("session", token)
        row = {"username": self.config.username, "created_at": now, "last_active": now,
               "expires_at": now + self.config.absolute, "version": self.config.version}

        def create(tx):
            if previous and re.fullmatch(r"[A-Za-z0-9_-]{43}", previous):
                tx.delete("auth_sessions", self.digest("session", previous))
            tx.set("auth_sessions", id, row)

        self.store.atomic(create)
        return token, row

    def logout(self, token):
        self.store.atomic(lambda tx: tx.delete("auth_sessions", self.digest("session", token)))

    def reserve_ai(self, session):
        now = self.clock()
        minute, day = int(now // 60), int(now // 86400)
        owner = secrets.token_hex(16)

        def reserve(tx):
            self._session(tx, session["id"], now)
            glob = tx.get("auth_limits", "ai_global") or {}
            local = tx.get("auth_limits", "ai_" + session["id"]) or {}
            minute_count = glob.get("minute_count", 0) if glob.get("minute") == minute else 0
            day_count = glob.get("day_count", 0) if glob.get("day") == day else 0
            session_count = local.get("count", 0) if local.get("minute") == minute else 0
            if glob.get("lease_until", 0) > now:
                self.limited(glob["lease_until"] - now)
            if minute_count >= self.config.ai_minute or session_count >= self.config.ai_session:
                self.limited((minute + 1) * 60 - now)
            if day_count >= self.config.ai_day:
                self.limited((day + 1) * 86400 - now)
            tx.set("auth_limits", "ai_global", {"minute": minute, "minute_count": minute_count + 1,
                   "day": day, "day_count": day_count + 1, "lease_owner": owner, "lease_until": now + 120})
            tx.set("auth_limits", "ai_" + session["id"], {"minute": minute, "count": session_count + 1})

        self.store.atomic(reserve)
        return owner

    def release_ai(self, owner):
        def release(tx):
            row = tx.get("auth_limits", "ai_global")
            if row and row.get("lease_owner") == owner:
                tx.set("auth_limits", "ai_global", dict(row, lease_until=0, lease_owner=""))
        try:
            self.store.atomic(release)
        except HTTPException:
            # Leave the lease to expire; never refund uncertain paid attempts.
            pass
