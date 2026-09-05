<p align="center">
  <img src="https://raw.githubusercontent.com/Baseryn/zcore-docs/master/public/banner.png" alt="ZCore Logo" width="620">
</p>

<p align="center">
  <strong>A pragmatic and complementary architectural layer built on top of FastAPI.</strong><br>
  <em>Standardize your structure, protect your data, and manage atomic transactions—without losing your development freedom.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/fastapi-zcore-framework/">
    <img src="https://img.shields.io/pypi/v/fastapi-zcore-framework?label=PyPI&color=teal" alt="PyPI">
  </a>
  <a href="https://github.com/Baseryn/zcore/blob/master/LICENSE">
    <img src="https://img.shields.io/github/license/Baseryn/zcore?color=purple" alt="License">
  </a>
  <a href="https://github.com/Baseryn/zcore/actions/workflows/test.yml">
    <img src="https://github.com/Baseryn/zcore/actions/workflows/test.yml/badge.svg" alt="CI">
  </a>
  <a href="https://baseryn.github.io/zcore-docs/">
    <img src="https://img.shields.io/badge/docs-online-purple" alt="Documentation">
  </a>
  <a href="https://pypi.org/project/fastapi-zcore-framework/">
    <img src="https://img.shields.io/pypi/pyversions/fastapi-zcore-framework?color=teal" alt="Python Versions">
  </a>
</p>

---

## What is ZCore?

**ZCore is not a framework that hides FastAPI — it is the chassis that stabilizes it.**

While FastAPI provides a high-performance engine for HTTP, it leaves the architecture of medium-to-large applications entirely to the developer. ZCore fills that gap with:

- **🔐 Context-Aware Data Masking** — Write one schema; sensitive fields are automatically pruned per-user across validation, serialization, and OpenAPI specs.
- **🔗 Atomic Unit of Work** — Coordinate multi-repository operations into all-or-nothing transactions with deferred event dispatching.
- **⚡ Scoped Dependency Injection** — High-performance constructor auto-wiring for Singleton, Transient, and request-scoped dependencies.
- **🏗️ Modular Plugin Architecture** — Organize business domains into decoupled plugins with topological dependency ordering.
- **🔍 Secure Dynamic Search Engine** — Nested JSON filters, eager loading, cursor/offset pagination, and column-level access controls.
- **📦 Interactive TUI & Scaffolding Engine** — Context-aware terminal dashboard, lightning-fast `uv` virtualenv orchestration, and granular layer-by-layer domain generators.

---

## Why ZCore?

| Concern | Raw FastAPI | With ZCore |
|---------|-------------|------------|
| **Endpoint Scaffolding** | Manually write 7+ routes and handlers per model | One `BaseRouter` class → 7 secure endpoints out-of-the-box |
| **Data Leakage** | Multiple Pydantic models per role; manual conditionals | `Zchema` auto-prunes restricted fields per active context |
| **Database Transactions** | Scattered `commit()` / `rollback()` calls | `UnitOfWork` guarantees atomicity + post-commit domain events |
| **Dependency Wiring** | Deeply nested, verbose `Depends()` parameter chains | Clean constructor auto-wiring via IoC container + `Inject[T]` |
| **Search & Pagination** | Hand-crafted SQL parsing per endpoint | Declarative JSON filters + keyset cursor and offset pagination |
| **Project Tooling & Layout** | Manual folder creation, fragmented glue scripts | `zc` interactive TUI with auto `uv` setup & granular layer picking |
| **Startup Orchestration** | Fragile `@app.on_event` chains | `Plugin` protocol with dependency DAG → topological sorting |

---

## ⚡ Quick Start

### 1. Install

```bash
pip install fastapi-zcore-framework[all]
```

### 2. Scaffold

Launch the interactive wizard or run directly:

```bash
# Interactive mode (prompts for DB driver, uv/.venv installation, etc.)
zc init my_app && cd my_app
zc startapp tasks

# Or instant non-interactive scaffolding with defaults
zc init my_app -y && cd my_app
zc startapp tasks -y
```

### 3. Define

Open `tasks/models.py`:

```python
import uuid
from zcore import Base
from sqlalchemy.orm import Mapped, mapped_column

class Task(Base):
    __tablename__ = "tasks"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str]
    is_completed: Mapped[bool] = mapped_column(default=False)
```

### 4. Run

```bash
zc run
```

Your API is live at **`http://127.0.0.1:8000`** with 7 secure endpoints (CRUD + Dynamic Search) ready.

> 📖 **Full walkthrough:** [Quick Start Guide](https://baseryn.github.io/zcore-docs/docs/quick-start)

---

## 🏛️ Core Pillars

<details>
<summary><strong>🔐 Context Shielding (Zchema)</strong></summary>
<br>

Write a single schema. ZCore dynamically prunes input fields (preventing mass assignment) and output fields (preventing data leakage) based on the active user's permission scopes.

```python
import uuid
from zcore import Zchema

class TaskResponse(Zchema):
    __model__ = "tasks"
    
    id: uuid.UUID
    title: str
    salary: float  # Automatically pruned if unauthorized
```

When an authenticated user lacks view permissions for `tasks.salary`, the field vanishes from both JSON serialization and the OpenAPI schema — with zero manual `if` statements.
</details>

<details>
<summary><strong>🔗 Atomic Transactions (Unit of Work)</strong></summary>
<br>

Group multiple repository operations into a single atomic unit. Events are buffered and dispatched **only after** a successful database commit. If any operation fails, the entire transaction rolls back and pending events are discarded.

```python
from zcore import UnitOfWork

async with UnitOfWork(session, dispatcher) as uow:
    order = await order_repo.create(order_data)
    await inventory_repo.decrement_stock(product_id, quantity)
    
    # Queued safely: Dispatches ONLY after the DB commit succeeds!
    uow.register_event("order.completed", {"order_id": str(order.id)})
```
</details>

<details>
<summary><strong>⚡ Scoped Dependency Injection</strong></summary>
<br>

Inject services, repositories, and infrastructure dependencies via standard constructor type hints. ZCore's IoC container auto-wires them dynamically and purges request-scoped instances after each response.

```python
from zcore import BaseService

class TaskService(BaseService[Task]):
    # Pure Python constructor: Auto-wired by ZCore's DI container
    def __init__(self, repository: TaskRepo):
        super().__init__(model=Task, repository=repository)
```
</details>

<details>
<summary><strong>🏗️ Modular Plugin System</strong></summary>
<br>

Each domain is an isolated `Plugin` conforming to a strict lifecycle contract. The Kernel resolves dependencies topologically using a Directed Acyclic Graph (DAG) to guarantee deterministic startup and shutdown sequences.

```python
from fastapi import FastAPI
from zcore import Plugin
from .routers import router_instance

class TasksPlugin(Plugin):
    name = "tasks"
    version = "0.1.0"
    dependencies = ["auth"]  # Requires AuthPlugin to start first

    def setup(self, app: FastAPI) -> None:
        app.include_router(router_instance.router)

    async def on_startup(self) -> None:
        pass
```
</details>

<details>
<summary><strong>🔍 Secure Dynamic Search Engine</strong></summary>
<br>

A dynamic query builder that translates nested JSON filters into safe SQLAlchemy 2.0 AST queries — featuring relation eager-loading, keyset cursor pagination, and depth-limit protection against DoS attacks.

```json
{
  "filters": [
    {
      "op": "and",
      "items": [
        { "field": "is_completed", "op": "eq", "value": false },
        { "field": "title", "op": "ilike", "value": "urgent" }
      ]
    }
  ],
  "include": ["assignee"],
  "sort": [
    { "field": "created_at", "order": "desc" }
  ],
  "page": 1,
  "size": 20
}
```
</details>

<details>
<summary><strong>📦 Interactive TUI & Scaffolding Engine</strong></summary>
<br>

The `zc` CLI gives you a rich, interactive Terminal User Interface (TUI) powered by Questionary and Rich. It features:

* **Workspace & Context Awareness:** Detects whether you are inside an active project, in a root workspace containing multiple services, or starting fresh.
* **Automated `uv` / `.venv` Setup:** Initializes isolated virtual environments and installs dependencies automatically using `uv` (recommended) or standard `pip`.
* **Multi-Engine Driver Bootstrapping:** Configures SQLite (`aiosqlite`), PostgreSQL (`asyncpg`), or MySQL (`aiomysql`) in `.env` and `requirements.txt` out-of-the-box.
* **Granular Architectural Scaffolding:** Generates modular domain apps in 3 modes (*Full Boilerplate*, *Clean/Blank*, or *Custom Layer Selection* across Models, Schemas, Repositories, Services, Routers, Plugins, and Pytest suites).

```text
$ zc

⚡ ZCore Framework v0.1.0-beta.9 • Modern Modular Monolith
 FastAPI • SQLAlchemy 2.0 • Pydantic V2

? What framework task would you like to perform? 📦 init — Scaffold a new full ZCore project
┌  Initialize New ZCore Project
│
? Project directory name: core_api
? Select primary database driver: PostgreSQL (asyncpg) — Production Ready
? Initialize virtual environment (.venv) and install dependencies? Yes (uv)
│  ✓ Environment & dependencies installed successfully.
└  Project 'core_api' ready!
```

#### CLI Command Reference

| Command | Interactive | Non-Interactive Flags | Purpose |
|---------|:-----------:|-----------------------|---------|
| `zc` | ✅ | — | Launch interactive orchestration dashboard |
| `zc init [name]` | ✅ | `--db [sqlite\|postgres\|mysql]`, `-y` | Bootstrap a new project with settings, `.env`, and dependencies |
| `zc startapp [name]` | ✅ | `--template / --no-template`, `--test / --no-test`, `-y` | Scaffold domain modules with granular layer selection |
| `zc run` | ✅ | — | Launch local Uvicorn dev server with auto-reload |
| `zc gensecret` | — | — | Generate a 64-character cryptographically secure `SECRET_KEY` |
| `zc genenv` | ✅ | `-o <output>`, `-f / --force` | Introspect active `Settings` classes and scaffold `.env.example` |
</details>

---

## 📖 Documentation

| Resource | Description |
|----------|-------------|
| [🚀 Quick Start](https://baseryn.github.io/zcore-docs/docs/quick-start) | Build a complete Task Manager API from scratch |
| [📚 10-Step Quick Learn](https://baseryn.github.io/zcore-docs/docs/quick-learn/step-1) | Deep dive into each architectural layer step-by-step |
| [🔧 How-To Guides](https://baseryn.github.io/zcore-docs/docs/how-to) | Pagination, search, file uploads, caching, and testing |
| [🏛️ Core Concepts](https://baseryn.github.io/zcore-docs/docs/core-concepts/context) | Deep dive into DI, Kernel, Security, UoW, and Zchema |
| [📜 API Reference](https://baseryn.github.io/zcore-docs/docs/api-reference/repository) | Complete class and method specifications |
| [🕒 Changelog](https://baseryn.github.io/zcore-docs/docs/changelog) | Release notes, breaking changes, and migrations |

---

## 🤝 Contributing

Contributions are welcome! Please read our guidelines before submitting a PR.

- **Issues:** Bug reports and feature requests via [GitHub Issues](https://github.com/Baseryn/zcore/issues)
- **PRs:** Open a pull request with a clear description of the change
- **Local Setup:** `pip install -e ".[all,dev]"` and run `hatch test` to verify

---

## 📄 License

ZCore is licensed under the **Apache License 2.0**.  
See [LICENSE](https://github.com/Baseryn/zcore/blob/master/LICENSE) for details.

---

<p align="center">
  <sub>Built with ☕ and architectural rigor by <a href="https://github.com/alialfostovar">Ali Alf Ostovar</a> / <a href="https://github.com/Baseryn">Baseryn</a>.</sub>
</p>