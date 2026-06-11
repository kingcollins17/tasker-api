import jwt
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from app.core.config import settings

class Security:
    """Security utility class for password hashing/verification and JWT operations."""

    def __init__(self):
        self._hasher = PasswordHasher()

    def hash_password(self, password: str) -> str:
        """Hashes a plain text password using Argon2.

        Args:
            password: Plain text password.

        Returns:
            str: Hashed password.
        """
        return self._hasher.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies a plain text password against an Argon2 hash.

        Args:
            plain_password: Plain text password.
            hashed_password: Argon2 password hash.

        Returns:
            bool: True if verified successfully, False otherwise.
        """
        try:
            return self._hasher.verify(hashed_password, plain_password)
        except VerifyMismatchError:
            return False

    def create_access_token(
        self,
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """Creates an encoded JWT access token.

        Args:
            data: Custom payload dict to encode.
            expires_delta: Optional expiry duration. If not provided, configuration default is used.

        Returns:
            str: Encoded JWT string.
        """
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.now(timezone.utc) + expires_delta
        else:
            expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            
        to_encode.update({"exp": expire})
        return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    def decode_access_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Decodes and validates a JWT access token.

        Args:
            token: Encoded JWT string.

        Returns:
            Optional[Dict[str, Any]]: Decoded payload if valid, None otherwise.
        """
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            return payload
        except jwt.PyJWTError:
            return None
