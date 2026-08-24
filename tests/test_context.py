import asyncio
import uuid
from typing import Any

import pytest

from zcore.context.context import _request_context_store, ctx


@pytest.mark.parametrize(
    "user_id_input, expected_output, expected_exception",
    [
        ("12345678-1234-5678-1234-567812345678", uuid.UUID("12345678-1234-5678-1234-567812345678"), None),
        (uuid.UUID("12345678-1234-5678-1234-567812345678"), uuid.UUID("12345678-1234-5678-1234-567812345678"), None),
        (None, None, None),
        ("invalid-uuid-string", None, ValueError),
        (12345, None, TypeError),
    ]
)
def test_set_current_user_id_validation(
    user_id_input: Any,
    expected_output: uuid.UUID | None,
    expected_exception: type[Exception] | None
) -> None:
    token = _request_context_store.set({})
    try:
        if expected_exception:
            with pytest.raises(expected_exception):
                ctx.user_id = user_id_input
        else:
            ctx.user_id = user_id_input
            assert ctx.user_id == expected_output
    finally:
        _request_context_store.reset(token)

@pytest.mark.parametrize(
    "fields_input, expected_output",
    [
        ({"password", "credit_card"}, frozenset({"password", "credit_card"})),
        (["password", "credit_card"], frozenset({"password", "credit_card"})),
        (frozenset({"password"}), frozenset({"password"})),
        (None, frozenset()),
    ]
)
def test_set_restricted_fields_immutability(
    fields_input: Any,
    expected_output: frozenset[str]
) -> None:
    token = _request_context_store.set({})
    try:
        ctx.restricted_fields = fields_input
        retrieved = ctx.restricted_fields
        assert retrieved == expected_output
        assert isinstance(retrieved, frozenset)
        assert not hasattr(retrieved, "add")
        assert not hasattr(retrieved, "remove")
    finally:
        _request_context_store.reset(token)

def test_context_managers_cleanup() -> None:
    token = _request_context_store.set({})
    try:
        initial_user = uuid.uuid4()
        initial_fields = {"email"}
        
        ctx.user_id = initial_user
        ctx.restricted_fields = initial_fields
        
        new_user = uuid.uuid4()
        new_fields = {"password"}
        
        with pytest.raises(RuntimeError), ctx.scope(user_id=new_user, restricted_fields=new_fields):
                assert ctx.user_id == new_user
                assert ctx.restricted_fields == frozenset(new_fields)
                raise RuntimeError("Error inside scope")
                
        assert ctx.user_id == initial_user
        assert ctx.restricted_fields == frozenset(initial_fields)

        with ctx.scope(custom_key="custom_value"):
            assert ctx.get("custom_key") == "custom_value"
        
        assert ctx.get("custom_key") is None
    finally:
        _request_context_store.reset(token)

def test_context_core_get_set_remove() -> None:
    token = _request_context_store.set({})
    try:
        ctx.set("theme", "dark")
        assert ctx.get("theme") == "dark"
        assert ctx.get("language", "en") == "en"
        ctx.remove("theme")
        assert ctx.get("theme") is None
        ctx.remove("nonexistent")
    finally:
        _request_context_store.reset(token)

def test_context_default_property_fallbacks() -> None:
    token = _request_context_store.set({})
    try:
        assert ctx.user_id is None
        assert ctx.restricted_fields == frozenset()
    finally:
        _request_context_store.reset(token)

@pytest.mark.anyio
async def test_context_async_task_isolation() -> None:
    token = _request_context_store.set({})
    try:
        user_id_a = uuid.uuid4()
        user_id_b = uuid.uuid4()

        async def task_a() -> None:
            _request_context_store.set({})
            ctx.user_id = user_id_a
            await asyncio.sleep(0.01)
            assert ctx.user_id == user_id_a

        async def task_b() -> None:
            _request_context_store.set({})
            ctx.user_id = user_id_b
            await asyncio.sleep(0.01)
            assert ctx.user_id == user_id_b

        await asyncio.gather(task_a(), task_b())
    finally:
        _request_context_store.reset(token)

def test_context_nested_scopes() -> None:
    token = _request_context_store.set({})
    try:
        initial_user = uuid.uuid4()
        user_u1 = uuid.uuid4()
        user_u2 = uuid.uuid4()

        ctx.user_id = initial_user
        
        with ctx.scope(user_id=user_u1, custom="V1"):
            assert ctx.user_id == user_u1
            assert ctx.get("custom") == "V1"
            
            with ctx.scope(user_id=user_u2, custom="V2"):
                assert ctx.user_id == user_u2
                assert ctx.get("custom") == "V2"
                
            assert ctx.user_id == user_u1
            assert ctx.get("custom") == "V1"
            
        assert ctx.user_id == initial_user
        assert ctx.get("custom") is None
    finally:
        _request_context_store.reset(token)

def test_context_scope_property_and_fallback_attribute() -> None:
    token = _request_context_store.set({})
    try:
        user_val = uuid.uuid4()
        with ctx.scope(user_id=user_val, tenant_id="tenant-123"):
            assert ctx.user_id == user_val
            assert ctx.get("tenant_id") == "tenant-123"
            
        assert ctx.user_id is None
        assert ctx.get("tenant_id") is None
    finally:
        _request_context_store.reset(token)

def test_context_manual_initialize_and_reset() -> None:
    token1 = _request_context_store.set({})
    try:
        ctx.set("session_id", "session-active")
        assert ctx.get("session_id") == "session-active"
        
        token2 = ctx.initialize()
        assert ctx.get("session_id") is None
        
        ctx.reset(token2)
        assert ctx.get("session_id") == "session-active"
    finally:
        _request_context_store.reset(token1)