"""JSON Web Token (JWT) Utility Functions.

This module manages JSON Web Token operations, supporting both Symmetric (HMAC) 
and Asymmetric (RSA/ECDSA) signature algorithms. It includes automated security 
checks to prevent production starts with insecure secret configurations.
"""

import jwt
import structlog
from datetime import datetime, timezone, timedelta
from typing import Union

from zcore.config import settings
from zcore.exceptions.base import AuthError

logger = structlog.get_logger()


def _get_signing_keys() -> tuple[Union[str, bytes], Union[str, bytes], str]:
    """Resolve active private and public signing keys from config settings.

    Verifies configured environments. Aborts application startup in production settings 
    if default fallback secrets are active.

    Returns:
        A tuple of (private_key, public_key, algorithm_name) used for signing operations.

    Raises:
        RuntimeError: If a production context is configured with the default, insecure 
            fallback secret key.
    """
    # If Asymmetric keys are provided via settings
    private_key = getattr(settings, "JWT_PRIVATE_KEY", None)
    public_key = getattr(settings, "JWT_PUBLIC_KEY", None)
    
    if private_key and public_key:
        return private_key, public_key, settings.ALGORITHM
        
    # Default to Symmetric signing
    is_prod = getattr(settings, "DEBUG", False) == False
    is_fallback = settings.SECRET_KEY == "zcore-insecure-fallback-secret-key-must-be-changed"
    
    if is_prod and is_fallback:
        raise RuntimeError(
            "FATAL SECURITY VIOLATION: You are running in PRODUCTION environment "
            "using the insecure default fallback SECRET_KEY. Application startup aborted."
        )
        
    return settings.SECRET_KEY, settings.SECRET_KEY, settings.ALGORITHM


def create_token(data: dict, expires_delta: timedelta|None = None) -> str:
    """Create a signed JWT access token.

    Supports HMAC symmetric signing or asymmetric cryptographic keypair signatures, 
    adding standard expiration metadata to token claims.

    Args:
        data: The payload claims to encode into the token.
        expires_delta: Optional custom lifetime duration. Defaults to settings configurations.

    Returns:
        The encoded and signed JWT string.

    Raises:
        AuthError: If encoding fails due to internal errors.
    """
    private_key, _, algorithm = _get_signing_keys()
    to_encode = data.copy()
    
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    
    try:
        return jwt.encode(to_encode, private_key, algorithm=algorithm)
    except Exception as e:
        logger.error(f"Failed to encode JWT token: {e}")
        raise AuthError(message="Token creation failed due to internal error.")


def decode_token(token: str) -> dict:
    """Decode and validate a signed JWT token string.

    Args:
        token: The signed JWT token string to evaluate.

    Returns:
        A dictionary of decoded payload claims.

    Raises:
        AuthError: If the token signature is expired or structurally invalid.
    """
    _, public_key, algorithm = _get_signing_keys()
    try:
        return jwt.decode(token, public_key, algorithms=[algorithm])
    except jwt.ExpiredSignatureError as e:
        raise AuthError(message="Token expired") from e
    except jwt.InvalidTokenError as e:
        raise AuthError(message="Invalid token structure") from e
    

def is_token_expired(token_exp: int) -> bool:   
    """Check if a Unix timestamp claim has expired.

    Args:
        token_exp: Unix epoch timestamp representing expiration claim.

    Returns:
        True if the current time matches or exceeds the timestamp, False otherwise.
    """
    if not token_exp:
        return True
    
    expire_time = datetime.fromtimestamp(token_exp, tz=timezone.utc)
    now = datetime.now(timezone.utc)
    
    return now >= expire_time