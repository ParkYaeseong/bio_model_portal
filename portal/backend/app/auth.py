import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .database import get_db
from .schemas import TokenData

settings = get_settings()
# auto_error=False so requests authenticated via the SSO identity header
# (no bearer token) are not rejected before we get a chance to inspect them.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
# use pbkdf2_sha256 to avoid bcrypt backend issues & 72-byte truncation
password_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# SSO-provisioned accounts are keyed by the OIDC subject and never log in with
# a password, so this prefix keeps them from colliding with local usernames.
SSO_USERNAME_PREFIX = "sso:"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return password_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def get_user_by_username(db: Session, username: str) -> Optional[models.User]:
    return db.query(models.User).filter(models.User.username == username).first()


def authenticate_user(db: Session, username: str, password: str) -> Optional[models.User]:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


def provision_sso_user(
    db: Session, sub: str, email: str | None = None, name: str | None = None
) -> models.User:
    """Return the local account for an SSO subject, creating it on first sight.

    The account is keyed by the OIDC ``sub`` (stable across email/name changes)
    and gets an unguessable random password hash so it can never be used for
    password login.
    """
    username = f"{SSO_USERNAME_PREFIX}{sub}"
    user = get_user_by_username(db, username)
    if user is None:
        user = models.User(username=username, password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_current_user(
    db: Session = Depends(get_db),
    token: str | None = Depends(oauth2_scheme),
    x_kbf_user: str | None = Header(default=None, alias="X-KBF-User"),
    x_kbf_email: str | None = Header(default=None, alias="X-KBF-Email"),
    x_kbf_name: str | None = Header(default=None, alias="X-KBF-Name"),
    x_kbf_auth: str | None = Header(default=None, alias="X-KBF-Auth"),
    x_kbf_admin: str | None = Header(default=None, alias="X-KBF-Admin"),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="인증 정보가 올바르지 않습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Primary path: trust the SSO identity injected by the Caddy forward_auth
    # gateway. Caddy overwrites any client-supplied value so it cannot be
    # spoofed from the browser; the X-KBF-Auth shared secret additionally
    # ensures a co-located process cannot forge it straight against 127.0.0.1.
    sub = (x_kbf_user or "").strip()
    if sub:
        expected_secret = settings.kbf_forward_auth_secret
        if expected_secret:
            if not x_kbf_auth or not hmac.compare_digest(x_kbf_auth, expected_secret):
                raise credentials_exception
        elif not settings.kbf_allow_insecure_sso_header:
            # Fail closed: no shared secret configured and the dev opt-in is off,
            # so we cannot prove the header came from the gateway. Refuse rather
            # than trust a potentially spoofed identity.
            raise credentials_exception
        # The reserved prefix is added by us, never present in a genuine OIDC
        # sub — its presence means someone is trying to smuggle an identity.
        if sub.startswith(SSO_USERNAME_PREFIX):
            raise credentials_exception
        user = provision_sso_user(db, sub, x_kbf_email, x_kbf_name)
        # Same trust boundary as the sub itself: this header only reaches us
        # once the checks above (shared secret / dev opt-in) have passed.
        user.is_admin = isinstance(x_kbf_admin, str) and x_kbf_admin.strip().lower() == "true"
        return user

    # Fallback: legacy bearer-token auth (local dev, tests, pre-SSO clients).
    if not token:
        raise credentials_exception
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError as exc:
        raise credentials_exception from exc
    user = get_user_by_username(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    user.is_admin = False
    return user
