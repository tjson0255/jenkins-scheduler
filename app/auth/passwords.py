"""管理者パスワードのハッシュ（PBKDF2-SHA256、標準ライブラリのみ）。

.env に平文のパスワードを置かずに済むよう、`python -m app --hash-password` で作った値を
ADMIN_PASSWORD_HASH に書く。形式: pbkdf2_sha256$<回数>$<salt(hex)>$<hash(hex)>
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ALGO = "pbkdf2_sha256"
ITERATIONS = 600_000


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    if not password:
        raise ValueError("パスワードが空です")
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGO}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = encoded.strip().split("$")
        if algo != ALGO:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)
