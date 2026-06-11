import pytest
import jwt
from datetime import timedelta, datetime, timezone
from app.core.utils import security
from app.core.config import settings

def test_password_hashing_and_verification():
    """Verify that hashing and verification work properly."""
    password = "secret_password"
    hashed = security.hash_password(password)
    
    # Hash should be non-empty and start with argon2 format
    assert hashed
    assert hashed.startswith("$argon2id$")
    
    # Correct password verification
    assert security.verify_password(password, hashed) is True
    
    # Incorrect password verification
    assert security.verify_password("wrong_password", hashed) is False

def test_jwt_token_flow():
    """Verify encoding and decoding of JWT access tokens."""
    payload = {"sub": "user_id_123", "role": "admin"}
    token = security.create_access_token(payload)
    
    # Verify token exists
    assert token
    assert isinstance(token, str)
    
    # Decode token
    decoded = security.decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "user_id_123"
    assert decoded["role"] == "admin"
    assert "exp" in decoded

def test_jwt_token_expiration():
    """Verify that custom expiration works and expired token returns None."""
    payload = {"sub": "user_id_123"}
    # Token with negative delta (expired)
    token = security.create_access_token(payload, expires_delta=timedelta(seconds=-10))
    
    assert token
    decoded = security.decode_access_token(token)
    assert decoded is None

def test_jwt_decode_invalid_token():
    """Verify that decoding invalid tokens returns None."""
    assert security.decode_access_token("invalid.token.string") is None

