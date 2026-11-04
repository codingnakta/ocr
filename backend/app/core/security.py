"""Authentication, CSRF guard and simple rate limiting.

* AUTH_MODE=none (development only): every request acts as a single local user.
* AUTH_MODE=password: username/password -> signed, HttpOnly, SameSite=Lax cookie.
* State-changing requests must carry the `X-PB-CSRF: 1` header. Browsers cannot add
  custom headers to cross-site requests without a CORS preflight, which we do not
  allow, so this blocks CSRF even though auth is cookie based.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db, session_scope
from app.models import User

COOKIE = "pb_session"
LOCAL_USER_ID = "local"
CSRF_HEADER = "x-pb-csrf"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret.get_secret_value(), salt="pb-session")


def make_session(user: User) -> str:
    # include a fragment of the password hash: changing the password invalidates sessions
    return _serializer().dumps({"uid": user.id, "pv": (user.password_hash or "")[-10:]})


def read_session(token: str) -> dict | None:
    try:
        return _serializer().loads(token, max_age=get_settings().session_max_age_hours * 3600)
    except (BadSignature, SignatureExpired):
        return None


def ensure_bootstrap_users() -> None:
    s = get_settings()
    with session_scope() as db:
        if s.auth_mode == "none":
            if db.get(User, LOCAL_USER_ID) is None:
                db.add(User(id=LOCAL_USER_ID, username="local", is_admin=True))
        elif s.admin_username and s.admin_password:
            u = db.scalar(select(User).where(User.username == s.admin_username))
            if u is None:
                db.add(User(username=s.admin_username, password_hash=hash_password(s.admin_password.get_secret_value()),
                            is_admin=True))


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    s = get_settings()
    if s.auth_mode == "none":
        u = db.get(User, LOCAL_USER_ID)
        if u is None:
            u = User(id=LOCAL_USER_ID, username="local", is_admin=True)
            db.add(u)
            db.commit()
        return u
    token = request.cookies.get(COOKIE)
    data = read_session(token) if token else None
    if not data:
        raise HTTPException(401, "로그인이 필요합니다.")
    u = db.get(User, data.get("uid"))
    if u is None or (u.password_hash or "")[-10:] != data.get("pv"):
        raise HTTPException(401, "세션이 만료되었습니다. 다시 로그인하세요.")
    return u


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "관리자만 사용할 수 있습니다.")
    return user


# ---------------------------------------------------------- rate limit --
class RateLimiter:
    """In-process sliding window. For multi-instance deployments put a shared limiter
    (e.g. at the reverse proxy) in front as well."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> None:
        if limit <= 0:
            return
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                retry = int(window_s - (now - q[0])) + 1
                raise HTTPException(429, f"요청이 너무 많습니다. {retry}초 후 다시 시도하세요.",
                                    headers={"Retry-After": str(retry)})
            q.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
