import uuid
import time
import pytest

from typing import Any, Type
from datetime import timedelta
from pydantic import BaseModel
from unittest.mock import MagicMock, AsyncMock, patch

from zcore.config import settings
from zcore.context import ctx
from zcore.exceptions import AuthError, ForbiddenError
from zcore.security import BaseAuth, HasScopes, Security

class MockUser:
    def __init__(self, user_id: uuid.UUID, is_active: bool, is_superuser: bool, scopes: Any, all_restricted_fields: list[str] = None) -> None:
        self.id = user_id
        self.is_active = is_active
        self.is_superuser = is_superuser
        self.scopes = scopes
        self.all_restricted_fields = all_restricted_fields or []

class SampleUserModel(BaseModel):
    id: uuid.UUID
    is_active: bool = True
    is_superuser: bool = False
    scopes: list[str] = []
    all_restricted_fields: list[str] = []
    
    model_config = {"from_attributes": True}

class MyAuth(BaseAuth[SampleUserModel]):
    async def fetch_user(self, identity: str) -> Any:
        if identity == "active_user":
            return MockUser(uuid.uuid4(), is_active=True, is_superuser=False, scopes=["read:items"])
        elif identity == "inactive_user":
            return MockUser(uuid.uuid4(), is_active=False, is_superuser=False, scopes=[])
        return None

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

@pytest.mark.anyio
async def test_base_auth_success(monkeypatch: pytest.MonkeyPatch) -> None:
    token = Security.create_jwt({"sub": "active_user", "type": "access"})
    auth_instance = MyAuth(user_schema=SampleUserModel)
    
    mock_cache = AsyncMock()
    mock_cache.get.return_value = None
    monkeypatch.setattr(auth_instance, "cache", mock_cache)
    
    mock_request = MagicMock()
    user_data = await auth_instance(mock_request, token=token)
    
    assert user_data.is_active is True
    assert ctx.user_id == user_data.id
    assert "read:items" in ctx.get("scopes")
    mock_cache.set.assert_called_once()

@pytest.mark.anyio
async def test_base_auth_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    cached_user = SampleUserModel(id=user_id, is_active=True, scopes=["read:items"])
    auth_instance = MyAuth(user_schema=SampleUserModel)
    
    mock_cache = AsyncMock()
    mock_cache.get.return_value = cached_user
    monkeypatch.setattr(auth_instance, "cache", mock_cache)
    
    token = Security.create_jwt({"sub": "active_user", "type": "access"})
    mock_request = MagicMock()
    
    spy_fetch = AsyncMock(wraps=auth_instance.fetch_user)
    monkeypatch.setattr(auth_instance, "fetch_user", spy_fetch)
    
    user_data = await auth_instance(mock_request, token=token)
    assert user_data.id == user_id
    spy_fetch.assert_not_called()

@pytest.mark.anyio
async def test_base_auth_inactive_db_user(monkeypatch: pytest.MonkeyPatch) -> None:
    token = Security.create_jwt({"sub": "inactive_user", "type": "access"})
    auth_instance = MyAuth(user_schema=SampleUserModel)
    
    mock_cache = AsyncMock()
    mock_cache.get.return_value = None
    monkeypatch.setattr(auth_instance, "cache", mock_cache)
    
    mock_request = MagicMock()
    with pytest.raises(AuthError) as exc_info:
        await auth_instance(mock_request, token=token)
    assert "User inactive" in str(exc_info.value)

@pytest.mark.anyio
async def test_base_auth_invalid_token_type() -> None:
    token = Security.create_jwt({"sub": "active_user", "type": "refresh"})
    auth_instance = MyAuth(user_schema=SampleUserModel)
    mock_request = MagicMock()
    with pytest.raises(AuthError) as exc_info:
        await auth_instance(mock_request, token=token)
    assert "Invalid token or token has expired." in str(exc_info.value)

@pytest.mark.anyio
async def test_base_auth_malformed_token() -> None:
    auth_instance = MyAuth(user_schema=SampleUserModel)
    mock_request = MagicMock()
    with pytest.raises(AuthError) as exc_info:
        await auth_instance(mock_request, token="malformed_jwt")
    assert "Invalid token" in str(exc_info.value)

def test_ctx_scope_isolation() -> None:
    ctx.user_id = None
    uid = uuid.uuid4()
    with ctx.scope(user_id=uid):
        assert ctx.user_id == uid
    assert ctx.user_id is None

def test_jwt_asymmetric_flow_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = "fake_private_key"
    public_key = "fake_public_key"
    monkeypatch.setattr(Security, "_get_signing_keys", lambda: (private_key, public_key, "RS256"))
    
    with patch("jwt.encode") as mock_encode:
        Security.create_jwt({"sub": "user1"})
        mock_encode.assert_called_once()
        args, kwargs = mock_encode.call_args
        assert args[1] == private_key
        assert kwargs["algorithm"] == "RS256"

def test_token_expiration_helper() -> None:
    now_ts = int(time.time())
    assert Security.is_token_expired(now_ts - 10) is True
    assert Security.is_token_expired(now_ts + 100) is False
    assert Security.is_token_expired(0) is True

def test_jwt_decode_expired() -> None:
    token = Security.create_jwt({"sub": "123"}, expires_delta=timedelta(seconds=-10))
    with pytest.raises(AuthError) as exc_info:
        Security.decode_jwt(token)
    assert "Token expired" in str(exc_info.value)

def test_jwt_decode_invalid_structure() -> None:
    with pytest.raises(AuthError) as exc_info:
        Security.decode_jwt("completely_invalid_token")
    assert "Invalid token structure" in str(exc_info.value)

def test_cryptographic_helpers() -> None:
    token1 = Security.generate_secure_token(16)
    token2 = Security.generate_secure_token(16)
    assert len(token1) == 32
    assert token1 != token2
    
    digest1 = Security.hash_sha256("test")
    digest2 = Security.hash_sha256("test")
    assert len(digest1) == 64
    assert digest1 == digest2

def test_argoncustom_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    from zcore.config import Settings, initialize_settings
    from zcore.kernel.di import container
    
    original_singletons = dict(container._singletons)
    
    class CustomSettings(Settings):
        ARGON2_MEMORY_COST: int = 32768
        ARGON2_TIME_COST: int = 2
        ARGON2_PARALLELISM: int = 2
        
    custom_inst = CustomSettings()
    initialize_settings(custom_inst)
    
    try:
        from importlib import reload
        import zcore.security.security
        reload(zcore.security.security)
        
        hashed = zcore.security.security.Security.hash_password("pass")
        assert zcore.security.security.Security.verify_password("pass", hashed) is True
    finally:
        container._singletons = original_singletons
        from importlib import reload
        import zcore.security.security
        reload(zcore.security.security)

@pytest.mark.anyio
@pytest.mark.parametrize(
    "coerced_scopes",
    [
        ("read:items", "write:items"),
        ["read:items", "write:items"],
        "read:items",
    ]
)
async def test_scope_formatting_coercion(coerced_scopes: Any) -> None:
    permission = HasScopes("read:items")
    mock_request = MagicMock()
    user = MockUser(uuid.uuid4(), is_active=True, is_superuser=False, scopes=coerced_scopes)
    resolved_user = await permission(mock_request, user=user)
    assert resolved_user is user

@pytest.mark.anyio
async def test_base_permission_object_level() -> None:
    class DummyPermission(HasScopes):
        pass
    perm = DummyPermission("read:items")
    mock_request = MagicMock()
    user = MockUser(uuid.uuid4(), is_active=True, is_superuser=False, scopes={"read:items"})
    assert await perm.has_object_permission(mock_request, user, {}) is True

@pytest.mark.anyio
async def test_superuser_scope_bypass_disabled() -> None:
    permission = HasScopes("read:items", allow_superuser=False)
    mock_request = MagicMock()
    user = MockUser(uuid.uuid4(), is_active=True, is_superuser=True, scopes=set())
    with pytest.raises(ForbiddenError) as exc_info:
        await permission(mock_request, user=user)
    assert "Access denied" in str(exc_info.value)