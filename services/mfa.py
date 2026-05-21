"""MFA TOTP lifecycle — enrollment, verification, recovery codes.

Three responsibilities, each its own function so unit tests can drive
the rule without going through the HTTP layer:

* `generate_secret()` + `provisioning_uri()` — mint a fresh TOTP secret
  and build the otpauth:// URL the authenticator app scans.
* `verify_totp(user, code)` — check a 6-digit TOTP code against the
  user's encrypted secret. Allows ±1 step (30s) of clock drift.
* `verify_recovery_code(user, code)` — bcrypt-checks the code against
  the JSON list of unused recovery codes and flips it to used.

Encryption: TOTP secrets live in the DB encrypted with Fernet, whose
key is derived from `SECRET_KEY` via HKDF (SHA-256, fixed salt). This
is defense-in-depth against a DB-only leak. A compromised SECRET_KEY
defeats both the session signing AND the MFA secret encryption — at
which point the attacker can forge sessions outright, so TOTP decryption
is not the worst problem. The trade-off is documented in the
homologation dossier.

Rotating SECRET_KEY invalidates every enrollment (decryption fails),
which forces re-enrollment. Acceptable because SECRET_KEY rotation also
forces a global logout anyway.

No commit — callers commit.
"""

from __future__ import annotations

import base64
import datetime
import secrets
from typing import Iterable

import bcrypt
import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import config
from models import User


# Number of recovery codes minted at enrollment. Industry standard
# (Google, GitHub, AWS) is 10 — enough to survive realistic device loss
# without becoming a brute-force surface.
RECOVERY_CODE_COUNT = 10

# Each code = 8 hex chars rendered as XXXX-XXXX (35 bits of entropy).
# Enough against online brute-force given /admin/security/mfa/verify
# is rate-limited at 5/min — the keyspace exhaustion would take
# ~6 million years at 5/min.
_RECOVERY_CODE_BYTES = 4

# HKDF salt — fixed (not secret) but distinct so the MFA sub-key can
# never collide with another use of SECRET_KEY-derived material added
# later (e.g. cookie signing for another sub-system).
_HKDF_SALT = b"mfa-totp-v1"
_HKDF_INFO = b"les-traiteurs-engages mfa secret encryption"


def _fernet() -> Fernet:
    """Derive the Fernet key on demand. Caching a module-level instance
    would force every test that monkey-patches SECRET_KEY to also clear
    the cache; not worth the foot-gun for the cost of one HKDF.
    """
    kdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_HKDF_SALT,
        info=_HKDF_INFO,
    )
    key = kdf.derive(config.SECRET_KEY.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain_secret: str) -> str:
    """Encrypt a base32 TOTP secret for DB storage."""
    return _fernet().encrypt(plain_secret.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str | None:
    """Decrypt a stored TOTP secret. Returns None if the ciphertext is
    unreadable (typically: SECRET_KEY rotated since enrollment, or DB
    corruption). The caller treats None as "MFA effectively reset" —
    the user goes back through enrollment.
    """
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None


def generate_secret() -> str:
    """Mint a fresh base32 TOTP secret (160 bits of entropy, standard)."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str, issuer: str) -> str:
    """Build the otpauth:// URI an authenticator app scans from the QR.

    `account_name` is typically the user email; `issuer` shows up as
    the account label in the authenticator app — keep it consistent
    across enrollments so reuse is recognizable.
    """
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=issuer)


def verify_totp_code(plain_secret: str, code: str) -> bool:
    """Validate a 6-digit TOTP code against a plaintext secret.

    `valid_window=1` allows the code one step before AND after the
    current 30s window. That's ~90s total slack, which covers user
    typing speed + small NTP drift between the server and the phone
    without weakening security meaningfully (still 6 digits per step).
    """
    if not code or not plain_secret:
        return False
    code = code.strip().replace(" ", "")
    if len(code) != 6 or not code.isdigit():
        return False
    return pyotp.TOTP(plain_secret).verify(code, valid_window=1)


def verify_user_totp(user: User, code: str) -> bool:
    """Decrypt the user's stored secret and verify the code."""
    if not user.mfa_secret:
        return False
    plain = decrypt_secret(user.mfa_secret)
    if plain is None:
        return False
    return verify_totp_code(plain, code)


def _format_code(raw_bytes: bytes) -> str:
    """Render 4 bytes as XXXX-XXXX (8 hex chars with a dash in the middle)."""
    hex_str = raw_bytes.hex().upper()
    return f"{hex_str[:4]}-{hex_str[4:]}"


def generate_recovery_codes() -> list[str]:
    """Mint a fresh set of recovery codes — caller-displayed (plaintext)."""
    return [
        _format_code(secrets.token_bytes(_RECOVERY_CODE_BYTES))
        for _ in range(RECOVERY_CODE_COUNT)
    ]


def hash_recovery_codes(plain_codes: Iterable[str]) -> list[dict]:
    """bcrypt-hash a list of plaintext recovery codes for DB storage.

    Returns a list of `{hash, used_at}` dicts — `used_at` flips to a
    timestamp when consumed. Order isn't significant.
    """
    return [
        {
            "hash": bcrypt.hashpw(code.encode("utf-8"), bcrypt.gensalt()).decode(
                "utf-8"
            ),
            "used_at": None,
        }
        for code in plain_codes
    ]


def consume_recovery_code(user: User, code: str) -> bool:
    """Check `code` against the user's unused recovery codes; if it
    matches, flip the entry to used and return True. Mutates
    `user.mfa_recovery_codes` in place — the caller commits.

    Always pays the full bcrypt cost (iterates every unused code) so
    a wrong code takes the same time as a right one. With 10 codes the
    overhead is bounded.
    """
    if not code or not user.mfa_recovery_codes:
        return False
    # Normalize: accept "ABCD-1234", "ABCD1234", "abcd-1234" interchangeably.
    normalized = code.strip().upper().replace(" ", "").replace("-", "")
    if len(normalized) != 8:
        return False
    candidate = f"{normalized[:4]}-{normalized[4:]}".encode("utf-8")

    matched_index: int | None = None
    for idx, entry in enumerate(user.mfa_recovery_codes):
        if entry.get("used_at") is not None:
            # Don't short-circuit — keep iterating to preserve constant
            # timing wrt the number of unused codes.
            continue
        if bcrypt.checkpw(candidate, entry["hash"].encode("utf-8")):
            matched_index = idx

    if matched_index is None:
        return False

    # SQLAlchemy doesn't dirty-track in-place mutation of a JSON column
    # by default — rebind the list so the change is flushed.
    new_codes = list(user.mfa_recovery_codes)
    new_codes[matched_index] = {
        **new_codes[matched_index],
        "used_at": datetime.datetime.utcnow().isoformat(),
    }
    user.mfa_recovery_codes = new_codes
    return True


def unused_recovery_code_count(user: User) -> int:
    """How many recovery codes the user still has — surface in the UI
    so they know to regenerate when running low."""
    if not user.mfa_recovery_codes:
        return 0
    return sum(1 for entry in user.mfa_recovery_codes if entry.get("used_at") is None)


def reset_mfa(user: User) -> None:
    """Wipe MFA state for a user. Used by the disable flow and by
    administrative resets; never by self-service without password
    re-confirmation.
    """
    user.mfa_secret = None
    user.mfa_enabled = False
    user.mfa_recovery_codes = None
    user.mfa_enrolled_at = None
