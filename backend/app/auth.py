"""Single-user login with short-lived bearer tokens held only by this process."""
from collections import deque
from hashlib import sha256
from secrets import token_urlsafe
from threading import Lock
from time import monotonic

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError


class LoginDenied(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


class Auth:
    ttl = 3600
    window = 300

    def __init__(self, password_hash: str):
        self.password_hash = password_hash
        self.attempts: deque[tuple[float, str]] = deque()
        self.tokens: dict[bytes, float] = {}
        self.lock = Lock()
        self.hasher = PasswordHasher()

    def login(self, password: str, client: str) -> str:
        if not self.password_hash.startswith("$argon2id$"):
            raise LoginDenied(503, "请先在后端初始化管理员密码")
        # Reserve a slot before Argon2 runs to bound parallel verification work.
        with self.lock:
            now = monotonic()
            while self.attempts and self.attempts[0][0] <= now - self.window:
                self.attempts.popleft()
            if len(self.attempts) >= 25 or sum(ip == client for _, ip in self.attempts) >= 5:
                raise LoginDenied(429, "登录尝试过多，请在五分钟后重试")
            attempt = (now, client)
            self.attempts.append(attempt)
        try:
            self.hasher.verify(self.password_hash, password)
        except InvalidHashError:
            raise LoginDenied(503, "管理员密码配置无效") from None
        except VerificationError:
            raise LoginDenied(401, "密码错误") from None
        token = token_urlsafe(32)
        with self.lock:
            if attempt in self.attempts:
                self.attempts.remove(attempt)
            self.tokens = {key: expiry for key, expiry in self.tokens.items() if expiry > monotonic()}
            self.tokens[self.digest(token)] = monotonic() + self.ttl
        return token

    @staticmethod
    def digest(token: str) -> bytes:
        return sha256(token.encode()).digest()

    def valid(self, token: str) -> bool:
        with self.lock:
            return self.tokens.get(self.digest(token), 0) > monotonic()

    def revoke(self, token: str) -> None:
        with self.lock:
            self.tokens.pop(self.digest(token), None)
