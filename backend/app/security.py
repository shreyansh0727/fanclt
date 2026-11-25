import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass

from .errors import ApiError

_PAIR_CODE_JUNK = re.compile(r"[^A-Z0-9]")


@dataclass(frozen=True)
class User:
    uid: str
    email: str | None = None
    name: str | None = None


def normalize_pair_code(code: str) -> str:
    return _PAIR_CODE_JUNK.sub("", code.upper())


def hash_pair_code(code: str, pepper: str = "fanctl-pair-v1") -> str:
    normalized = normalize_pair_code(code)
    return hashlib.sha256(f"{pepper}:{normalized}".encode()).hexdigest()


def new_device_id() -> str:
    return "dev_" + secrets.token_hex(8)


def new_device_key() -> str:
    return "dk_" + secrets.token_urlsafe(32)


def keys_equal(stored: str | None, provided: str | None) -> bool:
    if not stored or not provided:
        return False
    return hmac.compare_digest(stored.encode(), provided.encode())


def hash_secret(secret: str, pepper: str = "fanctl-dev-secret-v1") -> str:
    return hashlib.sha256(f"{pepper}:{secret}".encode()).hexdigest()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unsigned_dev_token(project_id: str, uid: str, claims: dict) -> str:
    """Custom-token shaped JWT the Auth emulator accepts without a service
    account key (it does not verify the signature). Never used in production."""
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": project_id,
                "sub": uid,
                "aud": project_id,
                "iat": now,
                "exp": now + 3600,
                "uid": uid,
                "claims": claims,
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = _b64url(hmac.new(b"fanctl-emulator", signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def mint_device_token(project_id: str, device_id: str, emulator: bool) -> str:
    """Firebase custom token whose `dev` claim scopes the device to its node."""
    uid = f"device:{device_id}"
    claims = {"dev": device_id}
    if emulator:
        return _unsigned_dev_token(project_id, uid, claims)
    from firebase_admin import auth

    token = auth.create_custom_token(uid, claims)
    return token.decode() if isinstance(token, bytes) else token


def verify_firebase_token(token: str) -> User:
    """Verify a Firebase ID token. Imported lazily so tests can skip the SDK."""
    from firebase_admin import auth

    try:
        decoded = auth.verify_id_token(token, check_revoked=False)
    except Exception as exc:  # expired, malformed, revoked, wrong project
        raise ApiError(401, "invalid_token", "Firebase ID token rejected") from exc

    return User(uid=decoded["uid"], email=decoded.get("email"), name=decoded.get("name"))
