import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from zcore.kernel.di import container
from zcore.kernel.engine import Kernel
from zcore.kernel.events import EventDispatcher


class TrackedPlugin:
    def __init__(
        self,
        name: str,
        dependencies: list[str],
        log: list[str],
        fail_phase: str | None = None
    ) -> None:
        self.name = name
        self.version = f"1.0.{uuid.uuid4().hex[:4]}"
        self.dependencies = dependencies
        self.log = log
        self.fail_phase = fail_phase

    def setup(self, app: FastAPI) -> None:
        if self.fail_phase == "setup":
            raise ValueError("setup failure")
        self.log.append(f"{self.name}:setup")

    async def before_startup(self) -> None:
        if self.fail_phase == "before_startup":
            raise ValueError("before_startup failure")
        self.log.append(f"{self.name}:before_startup")

    async def on_startup(self) -> None:
        if self.fail_phase == "on_startup":
            raise ValueError("on_startup failure")
        self.log.append(f"{self.name}:on_startup")

    async def after_startup(self) -> None:
        if self.fail_phase == "after_startup":
            raise ValueError("after_startup failure")
        self.log.append(f"{self.name}:after_startup")

    async def on_shutdown(self) -> None:
        if self.fail_phase == "on_shutdown":
            raise ValueError("on_shutdown failure")
        self.log.append(f"{self.name}:on_shutdown")


@pytest.mark.parametrize(
    "graph",
    [
        {"C": ["B"], "B": ["A"], "A": []},
        {"D": ["B", "C"], "C": ["A"], "B": ["A"], "A": []},
    ]
)
def test_topological_sort_order(graph: dict[str, list[str]]) -> None:
    kernel = Kernel()
    log: list[str] = []
    for name, deps in graph.items():
        kernel.add_plugin(TrackedPlugin(name, deps, log))

    resolved = kernel._resolve_dependencies()
    resolved_names = [p.name for p in resolved]

    for name, deps in graph.items():
        name_idx = resolved_names.index(name)
        for dep in deps:
            assert resolved_names.index(dep) < name_idx


@pytest.mark.parametrize(
    "graph",
    [
        {"A": ["B"], "B": ["A"]},
        {"A": ["B"], "B": ["C"], "C": ["A"]},
    ]
)
def test_cycle_dependency_error(graph: dict[str, list[str]]) -> None:
    kernel = Kernel()
    log: list[str] = []
    for name, deps in graph.items():
        kernel.add_plugin(TrackedPlugin(name, deps, log))

    with pytest.raises(RuntimeError) as exc_info:
        kernel._resolve_dependencies()
    assert "Cyclic dependency" in str(exc_info.value)


@pytest.mark.parametrize(
    "graph",
    [
        {"A": ["B"]},
        {"A": ["B"], "B": ["C"]},
    ]
)
def test_missing_dependency_error(graph: dict[str, list[str]]) -> None:
    kernel = Kernel()
    log: list[str] = []
    for name, deps in graph.items():
        kernel.add_plugin(TrackedPlugin(name, deps, log))

    with pytest.raises(RuntimeError) as exc_info:
        kernel._resolve_dependencies()
    assert "Missing dependency" in str(exc_info.value)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "graph",
    [
        {"C": ["B"], "B": ["A"], "A": []},
    ]
)
async def test_plugin_lifespan_lifecycle(graph: dict[str, list[str]]) -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()

    for name, deps in graph.items():
        kernel.add_plugin(TrackedPlugin(name, deps, log))

    kernel.setup(app)

    assert log == ["A:setup", "B:setup", "C:setup"]
    log.clear()

    async with kernel.lifespan(app):
        assert log == [
            "A:before_startup", "B:before_startup", "C:before_startup",
            "A:on_startup", "B:on_startup", "C:on_startup",
            "A:after_startup", "B:after_startup", "C:after_startup",
        ]
        log.clear()

    assert log == [
        "C:on_shutdown", "B:on_shutdown", "A:on_shutdown"
    ]


@pytest.mark.anyio
async def test_empty_kernel() -> None:
    kernel = Kernel()
    app = FastAPI()
    kernel.setup(app)
    assert kernel._resolve_dependencies() == []
    async with kernel.lifespan(app):
        pass


def test_self_dependency_error() -> None:
    kernel = Kernel()
    log: list[str] = []
    kernel.add_plugin(TrackedPlugin("A", ["A"], log))
    with pytest.raises(RuntimeError) as exc_info:
        kernel._resolve_dependencies()
    assert "Cyclic dependency" in str(exc_info.value)


def test_multiple_independent_chains() -> None:
    kernel = Kernel()
    log: list[str] = []
    kernel.add_plugin(TrackedPlugin("B", ["A"], log))
    kernel.add_plugin(TrackedPlugin("A", [], log))
    kernel.add_plugin(TrackedPlugin("D", ["C"], log))
    kernel.add_plugin(TrackedPlugin("C", [], log))
    resolved = kernel._resolve_dependencies()
    names = [p.name for p in resolved]
    assert names.index("A") < names.index("B")
    assert names.index("C") < names.index("D")


def test_redundant_dependencies() -> None:
    kernel = Kernel()
    log: list[str] = []
    kernel.add_plugin(TrackedPlugin("A", [], log))
    kernel.add_plugin(TrackedPlugin("B", ["A"], log))
    kernel.add_plugin(TrackedPlugin("C", ["A", "B"], log))
    resolved = kernel._resolve_dependencies()
    names = [p.name for p in resolved]
    assert names == ["A", "B", "C"]


@pytest.mark.anyio
async def test_exception_in_before_startup() -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()
    kernel.add_plugin(TrackedPlugin("A", [], log))
    kernel.add_plugin(TrackedPlugin("B", ["A"], log, fail_phase="before_startup"))
    kernel.setup(app)
    with pytest.raises(ValueError, match="before_startup failure"):
        async with kernel.lifespan(app):
            pass
    assert "A:before_startup" in log
    assert "B:before_startup" not in log
    assert "A:on_startup" not in log


@pytest.mark.anyio
async def test_exception_in_on_startup() -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()
    kernel.add_plugin(TrackedPlugin("A", [], log, fail_phase="on_startup"))
    kernel.setup(app)
    with pytest.raises(ValueError, match="on_startup failure"):
        async with kernel.lifespan(app):
            pass
    assert "A:before_startup" in log
    assert "A:on_startup" not in log
    assert "A:after_startup" not in log


@pytest.mark.anyio
async def test_exception_in_after_startup() -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()
    kernel.add_plugin(TrackedPlugin("A", [], log, fail_phase="after_startup"))
    kernel.setup(app)
    with pytest.raises(ValueError, match="after_startup failure"):
        async with kernel.lifespan(app):
            pass
    assert "A:on_startup" in log
    assert "A:after_startup" not in log


@pytest.mark.anyio
async def test_shutdown_failures_robustness() -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()
    kernel.add_plugin(TrackedPlugin("A", [], log))
    kernel.add_plugin(TrackedPlugin("B", ["A"], log, fail_phase="on_shutdown"))
    kernel.setup(app)
    with pytest.raises(ValueError, match="on_shutdown failure"):
        async with kernel.lifespan(app):
            pass
    assert "B:on_shutdown" not in log
    assert "A:on_shutdown" not in log


def test_event_dispatcher_registration() -> None:
    kernel = Kernel()
    app = FastAPI()
    kernel.setup(app)
    resolved_dispatcher = container.resolve(EventDispatcher)
    assert resolved_dispatcher is kernel.dispatcher


@pytest.mark.anyio
async def test_lifespan_lazy_resolution() -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()
    kernel.add_plugin(TrackedPlugin("A", [], log))
    async with kernel.lifespan(app):
        assert "A:before_startup" in log


@pytest.mark.anyio
async def test_inter_plugin_event_dispatching() -> None:
    kernel = Kernel()
    log: list[str] = []
    app = FastAPI()

    class EventPlugin:
        def __init__(self, name: str, kernel_inst: Kernel, log_list: list[str]) -> None:
            self.name = name
            self.version = "1.0.0"
            self.dependencies: list[str] = []
            self.kernel = kernel_inst
            self.log = log_list

        def setup(self, app_inst: FastAPI) -> None:
            pass

        async def before_startup(self) -> None:
            pass

        async def on_startup(self) -> None:
            await self.kernel.dispatcher.dispatch("custom_event", "event_payload")

        async def after_startup(self) -> None:
            pass

        async def on_shutdown(self) -> None:
            pass

    async def handler(payload: str) -> None:
        log.append(payload)

    kernel.dispatcher.subscribe("custom_event", handler)
    kernel.add_plugin(EventPlugin("A", kernel, log))
    kernel.setup(app)

    async with kernel.lifespan(app):
        assert log == ["event_payload"]


@pytest.mark.anyio
async def test_kernel_lifespan_closes_db_and_cache_on_shutdown() -> None:
    kernel = Kernel()
    app = FastAPI()
    kernel.setup(app)

    with patch("zcore.cache.base.close_cache", new_callable=AsyncMock) as mock_close_cache, \
         patch("zcore.db.setup.db_manager.close", new_callable=AsyncMock) as mock_db_close:

        async with kernel.lifespan(app):
            mock_close_cache.assert_not_called()
            mock_db_close.assert_not_called()

        mock_close_cache.assert_called_once()
        mock_db_close.assert_called_once()