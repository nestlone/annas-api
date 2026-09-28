"""Password hashing and secret generation, standard library only."""

import hashlib
import hmac
import secrets

# scrypt is the default because it is memory-hard; pbkdf2 is the fallback for
# builds whose OpenSSL lacks it (hashlib.scrypt then raises ValueError).
_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SALT_BYTES = 16
_PBKDF2_ITERATIONS = 240_000

API_KEY_PREFIX = "annas_"
API_KEY_PREFIX_LENGTH = 12


def hash_password(password):
    """Return a self-describing hash string, e.g. ``scrypt$16384$8$1$<salt>$<hash>``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    encoded = password.encode("utf-8")
    try:
        digest = hashlib.scrypt(encoded, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
        return "scrypt${}${}${}${}${}".format(
            _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, salt.hex(), digest.hex()
        )
    except (AttributeError, ValueError):
        digest = hashlib.pbkdf2_hmac("sha256", encoded, salt, _PBKDF2_ITERATIONS)
        return "pbkdf2${}${}${}".format(_PBKDF2_ITERATIONS, salt.hex(), digest.hex())


def verify_password(password, stored):
    if not stored:
        return False
    parts = stored.split("$")
    encoded = password.encode("utf-8")
    try:
        if parts[0] == "scrypt" and len(parts) == 6:
            _, n, r, p, salt_hex, expected = parts
            digest = hashlib.scrypt(
                encoded, salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p)
            )
        elif parts[0] == "pbkdf2" and len(parts) == 4:
            _, iterations, salt_hex, expected = parts
            digest = hashlib.pbkdf2_hmac(
                "sha256", encoded, bytes.fromhex(salt_hex), int(iterations)
            )
        else:
            return False
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


def generate_api_key():
    """Return ``(raw, prefix, sha256hex)``; only the raw value is shown once."""
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, raw[:API_KEY_PREFIX_LENGTH], hash_api_key(raw)


def hash_api_key(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_session_token():
    return secrets.token_urlsafe(32)


def hash_session_token(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
