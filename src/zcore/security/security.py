"""Unified Security Services Module.

This module coordinates password hashing (via Argon2id), JSON Web Token (JWT) lifecycle operations, 
secure hexadecimal token generation, and cryptographic hashing digests under a single coherent interface.
"""

import secrets
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Union
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

try:
    from argon2.exceptions import InvalidHashError
except ImportError:
    from argon2.exceptions import InvalidHash as InvalidHashError

import jwt
import structlog

from zcore.config import settings
from zcore.exceptions import AuthError

logger = structlog.get_logger()

# Dynamically construct Argon2 parameters from config settings with safe fallbacks
_memory_cost = getattr(settings, "ARGON2_MEMORY_COST", 65536)     # 64 MB
_time_cost = getattr(settings, "ARGON2_TIME_COST", 3)             # 3 iterations
_parallelism = getattr(settings, "ARGON2_PARALLELISM", 4)         # 4 threads

ph = PasswordHasher(
    memory_cost=_memory_cost,
    time_cost=_time_cost,
    parallelism=_parallelism
)


class Security:
    """Unified Cryptographic and Security Services Coordinator."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Generate a secure Argon2id hash from a plain-text password.

        Args:
            password: The plain-text password to hash.

        Returns:
            The computed Argon2id cryptographic hash string.
        """
        try:
            return ph.hash(password)
        except Exception as e:
            logger.error(f"Password hashing failed: {e}")
            raise RuntimeError("Cryptographic error while processing password.")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Verify a plain-text password against a stored Argon2id hash.

        Args:
            plain_password: The unhashed candidate password.
            hashed_password: The valid stored Argon2id hash string.

        Returns:
            True if the candidate password matches the hash, False otherwise.
        """
        try:
            return ph.verify(hashed_password, plain_password)
        except (VerifyMismatchError, InvalidHashError):
            return False
        except Exception as e:
            logger.error(f"Password verification encountered unexpected failure: {e}")
            return False

    @staticmethod
    def generate_secure_token(length: int = 32) -> str:
        """Generate a cryptographically secure hex token of specified length.

        Args:
            length: Target byte capacity to encode. Defaults to 32.

        Returns:
            A cryptographically secure hex string.
        """
        return secrets.token_hex(length)

    @staticmethod
    def hash_sha256(data: str) -> str:
        """Generate a SHA-256 hex digest of the provided string data.

        Args:
            data: Raw text parameters to digest.

        Returns:
            A 64-character hexadecimal SHA-256 hash.
        """
        return hashlib.sha256(data.encode()).hexdigest()

    @staticmethod
    def _get_signing_keys() -> tuple[Union[str, bytes], Union[str, bytes], str]:
        """Resolve active private and public signing keys from config settings."""
        private_key = getattr(settings, "JWT_PRIVATE_KEY", None)
        public_key = getattr(settings, "JWT_PUBLIC_KEY", None)
        
        if private_key and public_key:
            return private_key, public_key, settings.ALGORITHM
            
        is_prod = getattr(settings, "DEBUG", False) == False
        is_fallback = settings.SECRET_KEY == "zcore-insecure-fallback-secret-key-must-be-changed"
        
        if is_prod and is_fallback:
            raise RuntimeError(
                "FATAL SECURITY VIOLATION: You are running in PRODUCTION environment "
                "using the insecure default fallback SECRET_KEY. Application startup aborted."
            )
            
        return settings.SECRET_KEY, settings.SECRET_KEY, settings.ALGORITHM

    @classmethod
    def create_jwt(cls, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """Create a signed JWT access token.

        Args:
            data: The payload claims to encode.
            expires_delta: Optional custom lifetime duration.

        Returns:
            The signed JWT token string.
        """
        private_key, _, algorithm = cls._get_signing_keys()
        to_encode = data.copy()
        
        expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
        to_encode.update({"exp": expire})
        
        try:
            return jwt.encode(to_encode, private_key, algorithm=algorithm)
        except Exception as e:
            logger.error(f"Failed to encode JWT token: {e}")
            raise AuthError(message="Token creation failed due to internal error.")

    @classmethod
    def decode_jwt(cls, token: str) -> dict:
        """Decode and validate a signed JWT token string.

        Args:
            token: The token string to decode.

        Returns:
            Decoded payload claims dict.
        """
        _, public_key, algorithm = cls._get_signing_keys()
        try:
            return jwt.decode(token, public_key, algorithms=[algorithm])
        except jwt.ExpiredSignatureError as e:
            raise AuthError(message="Token expired") from e
        except jwt.InvalidTokenError as e:
            raise AuthError(message="Invalid token structure") from e

    @staticmethod
    def is_token_expired(token_exp: int) -> bool:
        """Check if a Unix timestamp claim has expired."""
        if not token_exp:
            return True
        expire_time = datetime.fromtimestamp(token_exp, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return now >= expire_time