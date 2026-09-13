import base64
import hashlib
import hmac
import os
import struct
import time
from typing import Optional
from urllib.parse import quote


def generate_mfa_secret() -> str:
    """Generates a secure random 20-byte Base32-encoded secret string for MFA (TOTP)."""
    random_bytes = os.urandom(20)
    return base64.b32encode(random_bytes).decode("utf-8").rstrip("=")


def get_mfa_provisioning_uri(secret: str, email: str, issuer: str = "Tasker Platform") -> str:
    """Constructs an otpauth:// provisioning URI for QR code generation and authenticator apps."""
    label = quote(f"{issuer}:{email}")
    issuer_quoted = quote(issuer)
    return f"otpauth://totp/{label}?secret={secret}&issuer={issuer_quoted}&algorithm=SHA1&digits=6&period=30"


def _get_totp_code_for_interval(secret: str, interval: int) -> str:
    """Calculates 6-digit TOTP code for a specific interval using RFC 6238 HMAC-SHA1."""
    # Ensure base32 padding
    missing_padding = len(secret) % 8
    if missing_padding:
        secret_padded = secret + "=" * (8 - missing_padding)
    else:
        secret_padded = secret

    key = base64.b32decode(secret_padded, casefold=True)
    msg = struct.pack(">Q", interval)
    mac = hmac.new(key, msg, hashlib.sha1).digest()
    
    offset = mac[-1] & 0x0F
    binary_code = (
        ((mac[offset] & 0x7F) << 24)
        | ((mac[offset + 1] & 0xFF) << 16)
        | ((mac[offset + 2] & 0xFF) << 8)
        | (mac[offset + 3] & 0xFF)
    )
    otp = binary_code % 1000000
    return f"{otp:06d}"


def verify_mfa_code(secret: str, code: str, window: int = 1) -> bool:
    """Verifies a 6-digit TOTP code against the secret within allowed time window steps."""
    if not secret or not code:
        return False
    
    clean_code = code.strip()
    if len(clean_code) != 6 or not clean_code.isdigit():
        return False

    current_interval = int(time.time()) // 30
    for offset in range(-window, window + 1):
        target_interval = current_interval + offset
        try:
            expected_code = _get_totp_code_for_interval(secret, target_interval)
            if hmac.compare_digest(expected_code, clean_code):
                return True
        except Exception:
            continue
            
    return False
