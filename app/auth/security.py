"""Password hashing and checking. Standard library only."""

import hashlib
import hmac
import os

# How many times the hash function runs. Higher is slower and safer.
PBKDF2_ROUNDS = 100_000

# Stored format: pbkdf2_sha256$<rounds>$<salt hex>$<key hex>
ALGORITHM = "pbkdf2_sha256"


def hash_password(plain):
    """Hash a password with a fresh random salt. The salt is stored inside the hash."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"{ALGORITHM}${PBKDF2_ROUNDS}${salt.hex()}${key.hex()}"


def verify_password(plain, stored):
    """True when the plain password matches the stored hash."""
    try:
        algorithm, rounds, salt_hex, key_hex = stored.split("$")
        if algorithm != ALGORITHM:
            return False
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, int(rounds))
    except (ValueError, AttributeError):
        # A malformed hash must never log anybody in.
        return False
    return hmac.compare_digest(key.hex(), key_hex)
