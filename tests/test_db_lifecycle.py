import uuid
import pytest

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Type
from unittest.mock import AsyncMock, MagicMock, patch

from zcore.db.setup import DatabaseManager
from zcore.db.uow import UnitOfWork

@pytest.mark.parametrize(
    "db_url, pool_size, max_overflow",
    [
        ("sqlite+aiosqlite:///:memory:", 5, 10),
        ("sqlite+aiosqlite:///:memory:", 10, 20),
    ]
)
@pytest.mark.anyio
async def test_db_manager_init(db_url: str, pool_size: int, max_overflow: int) -> None:
    manager = DatabaseManager()
    manager.init_app(
        db_url=db_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=False
    )
    
    assert manager._engine is not None
    assert manager._session_factory is not None
    
    await manager.close()

@pytest.mark.parametrize(
    "exception_class",
    [
        ValueError,
        RuntimeError,
        TypeError,
    ]
)
@pytest.mark.anyio
async def test_db_session_rollback_on_error(exception_class: Type[Exception]) -> None:
    manager = DatabaseManager()
    mock_session = AsyncMock()
    
    @asynccontextmanager
    async def mock_session_ctx() -> AsyncGenerator[AsyncMock, None]:
        yield mock_session

    manager._session_factory = mock_session_ctx

    with pytest.raises(exception_class):
        async with manager.session() as session:
            assert session is mock_session
            raise exception_class("Simulated database error")

    mock_session.rollback.assert_called_once()

@pytest.mark.parametrize(
    "events_to_register",
    [
        [("user.created", {"id": str(uuid.uuid4())})],
        [
            ("order.created", {"id": str(uuid.uuid4())}),
            ("inventory.decremented", {"sku": "SKU-123", "qty": 1})
        ],
        []
    ]
)
@pytest.mark.anyio
async def test_uow_commit_emits_events(events_to_register: list[tuple[str, dict[str, Any]]]) -> None:
    session = AsyncMock()
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)
    
    call_order: list[str] = []
    
    async def track_commit() -> None:
        call_order.append("commit")
        
    async def track_dispatch(event: str, payload: Any) -> list[Any]:
        call_order.append(f"dispatch:{event}")
        return []

    session.commit.side_effect = track_commit
    dispatcher.dispatch.side_effect = track_dispatch

    for event_name, payload in events_to_register:
        uow.register_event(event_name, payload)

    await uow.commit()

    if events_to_register:
        assert call_order[0] == "commit"
        for i, (event_name, _) in enumerate(events_to_register):
            assert call_order[i + 1] == f"dispatch:{event_name}"
    else:
        assert call_order == ["commit"]

@pytest.mark.parametrize(
    "exception_class, events_to_register",
    [
        (ValueError, [("payment.failed", {"amount": 100})]),
        (RuntimeError, [("log.error", {"msg": "failure"}), ("alert.sent", {})]),
    ]
)
@pytest.mark.anyio
async def test_uow_rollback_clears_events(
    exception_class: Type[Exception],
    events_to_register: list[tuple[str, dict[str, Any]]]
) -> None:
    session = AsyncMock()
    session.info = {}
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)

    for event_name, payload in events_to_register:
        uow.register_event(event_name, payload)

    await uow.rollback()
    assert len(uow._pending_events) == 0
    dispatcher.dispatch.assert_not_called()

    for event_name, payload in events_to_register:
        uow.register_event(event_name, payload)

    with pytest.raises(exception_class):
        async with uow:
            raise exception_class("Simulated processing error")

    assert len(uow._pending_events) == 0
    dispatcher.dispatch.assert_not_called()
    session.rollback.assert_called()

@pytest.mark.anyio
async def test_db_manager_uninitialized_access() -> None:
    manager = DatabaseManager()
    with pytest.raises(RuntimeError):
        async with manager.session() as session:
            pass

@pytest.mark.anyio
async def test_db_manager_sql_logger_timing() -> None:
    listeners = {}
    
    def mock_listens_for(target, identifier):
        def decorator(fn):
            listeners[identifier] = fn
            return fn
        return decorator

    with patch("sqlalchemy.event.listens_for", mock_listens_for), \
         patch("structlog.get_logger") as mock_get_logger:
        
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger
        
        manager = DatabaseManager()
        sync_engine_mock = MagicMock()
        sync_engine_mock.dialect.name = "postgresql"
        
        manager._register_query_logger(sync_engine_mock)
        
        assert "before_cursor_execute" in listeners
        assert "after_cursor_execute" in listeners
        
        mock_context = MagicMock()
        listeners["before_cursor_execute"](None, None, "SELECT 1", {}, mock_context, False)
        assert hasattr(mock_context, "_query_start_time")
        
        listeners["after_cursor_execute"](None, None, "SELECT 1", {}, mock_context, False)
        mock_logger.info.assert_called_once()
        
        mock_logger.reset_mock()
        listeners["after_cursor_execute"](None, None, "SELECT * FROM pg_catalog.pg_tables", {}, mock_context, False)
        mock_logger.info.assert_not_called()

@pytest.mark.anyio
async def test_db_manager_session_propagates_exception() -> None:
    manager = DatabaseManager()
    mock_session = AsyncMock()
    
    @asynccontextmanager
    async def mock_session_ctx() -> AsyncGenerator[AsyncMock, None]:
        yield mock_session

    manager._session_factory = mock_session_ctx

    with pytest.raises(ValueError, match="Internal Error"):
        async with manager.session() as session:
            raise ValueError("Internal Error")
            
    mock_session.rollback.assert_called_once()

@pytest.mark.anyio
async def test_uow_event_handler_failure_gracefulness() -> None:
    session = AsyncMock()
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)
    
    uow.register_event("e1", {})
    uow.register_event("e2", {})
    
    call_order = []
    
    async def mock_dispatch(event: str, payload: Any) -> list[Any]:
        call_order.append(event)
        if event == "e1":
            raise ValueError("E1 handler failed")
        return []
        
    dispatcher.dispatch.side_effect = mock_dispatch
    
    await uow.commit()
    
    assert call_order == ["e1", "e2"]
    assert len(uow._pending_events) == 0

@pytest.mark.anyio
async def test_uow_db_commit_failure() -> None:
    session = AsyncMock()
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)
    
    session.commit.side_effect = Exception("DB error")
    uow.register_event("e1", {})
    
    with pytest.raises(Exception, match="DB error"):
        await uow.commit()
        
    session.rollback.assert_called_once()
    dispatcher.dispatch.assert_not_called()
    assert len(uow._pending_events) == 1

@pytest.mark.anyio
async def test_uow_managed_flag() -> None:
    session = AsyncMock()
    session.info = {}
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)
    
    assert session.info.get("uow_managed") is not True
    
    async with uow:
        assert session.info.get("uow_managed") is True
        
    assert session.info.get("uow_managed") is False

@pytest.mark.anyio
async def test_uow_context_manager_auto_commit() -> None:
    session = AsyncMock()
    session.info = {}
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)
    
    uow.register_event("e1", {})
    
    async with uow:
        pass
        
    session.commit.assert_called_once()
    dispatcher.dispatch.assert_called_once_with("e1", {})

@pytest.mark.anyio
async def test_uow_context_manager_auto_rollback() -> None:
    session = AsyncMock()
    session.info = {}
    dispatcher = AsyncMock()
    uow = UnitOfWork(session, dispatcher)
    
    uow.register_event("e1", {})
    
    with pytest.raises(ValueError, match="Err"):
        async with uow:
            raise ValueError("Err")
            
    session.rollback.assert_called_once()
    dispatcher.dispatch.assert_not_called()
    assert len(uow._pending_events) == 0