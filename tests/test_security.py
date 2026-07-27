import uuid
from typing import Any, Type
from unittest.mock import MagicMock
import pytest

from zcore.config import settings
from zcore.exceptions import AuthError, ForbiddenError
from zcore.security.security import Security
from zcore.security.permissions import HasScopes

class MockUser:
    def __init__(self, user_id: uuid.UUID, is_active: bool, is_superuser: bool, scopes: set[str]) -> None:
        self.id = user_id
        self.is_active = is_active
        self.is_superuser = is_superuser
        self.scopes = scopes

@pytest.mark.parametrize(
    "plain_pwd, candidate_pwd, is_match",
    [
        ("safe_pass123", "safe_pass123", True),
        ("safe_pass123", "wrong_pass", False),
    ]
)
def test_argon2_hashing(plain_pwd: str, candidate_pwd: str, is_match: bool) -> None:
    hashed = Security.hash_password(plain_pwd)
    assert Security.verify_password(candidate_pwd, hashed) is is_match
    assert Security.verify_password(plain_pwd, "invalid_hash_structure") is False
    assert Security.verify_password(plain_pwd, "$argon2id$v=19$m=65536,t=3,p=4$corruptpayload") is False

@pytest.mark.parametrize(
    "payload",
    [
        {"sub": str(uuid.uuid4()), "scopes": ["read:items"]},
    ]
)
def test_jwt_symmetric_flow(payload: dict[str, Any]) -> None:
    token = Security.create_jwt(payload)
    decoded = Security.decode_jwt(token)
    assert decoded["sub"] == payload["sub"]
    assert decoded["scopes"] == payload["scopes"]
    assert "exp" in decoded

def test_jwt_production_safety_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "DEBUG", False)
    monkeypatch.setattr(settings, "SECRET_KEY", "zcore-insecure-fallback-secret-key-must-be-changed")
    with pytest.raises(RuntimeError) as exc_info:
        Security._get_signing_keys()
    assert "FATAL SECURITY VIOLATION" in str(exc_info.value)

@pytest.mark.anyio
@pytest.mark.parametrize(
    "user_active, user_superuser, user_scopes, required_scopes, expected_error",
    [
        (True, False, {"read:users", "write:users"}, ["read:users"], None),
        (True, True, set(), ["read:users"], None),
        (True, False, {"read:posts"}, ["read:users"], ForbiddenError),
        (False, True, {"read:users"}, ["read:users"], AuthError),
        (False, False, set(), [], AuthError),
    ]
)
async def test_scope_permissions(
    user_active: bool,
    user_superuser: bool,
    user_scopes: set[str],
    required_scopes: list[str],
    expected_error: Type[Exception] | None
) -> None:
    permission = HasScopes(*required_scopes)
    mock_request = MagicMock()
    if expected_error == AuthError and not user_scopes and not user_active and not user_superuser:
        with pytest.raises(AuthError) as exc_info:
            await permission(mock_request, user=None)
        assert "Authentication required" in str(exc_info.value)
    else:
        user = MockUser(uuid.uuid4(), is_active=user_active, is_superuser=user_superuser, scopes=user_scopes)
        if expected_error:
            with pytest.raises(expected_error):
                await permission(mock_request, user=user)
        else:
            resolved_user = await permission(mock_request, user=user)
            assert resolved_user is user