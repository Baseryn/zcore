import uuid
from typing import Any, Type
import pytest

from zcore.context.context import ctx, _request_context_store

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
    expected_exception: Type[Exception] | None
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
        
        with pytest.raises(RuntimeError):
            with ctx.scope(user_id=new_user, restricted_fields=new_fields):
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