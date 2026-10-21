<p align="center">
  <img src="https://raw.githubusercontent.com/Baseryn/zcore-docs/master/public/banner.png" alt="FastAPI ZCore Framework Logo" width="620">
</p>

<p align="center">
  <strong>An architectural chassis for building modular applications on top of FastAPI.</strong><br>
  <em>Structuring transactional boundaries, constructor dependency injection, and field-level access controls while preserving native FastAPI semantics.</em>
</p>

<p align="center">
  <a href="https://pypi.org/project/fastapi-zcore-framework/">
    <img src="https://img.shields.io/pypi/v/fastapi-zcore-framework?label=PyPI&color=teal" alt="PyPI Version">
  </a>
  <a href="https://github.com/Baseryn/zcore/blob/master/LICENSE">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License">
  </a>
  <a href="https://github.com/Baseryn/zcore/actions/workflows/test.yml">
    <img src="https://github.com/Baseryn/zcore/actions/workflows/test.yml/badge.svg" alt="Continuous Integration">
  </a>
  <a href="https://zcore.baseryn.com">
    <img src="https://img.shields.io/badge/docs-online-purple" alt="Documentation">
  </a>
  <a href="https://pypi.org/project/fastapi-zcore-framework/">
    <img src="https://img.shields.io/pypi/pyversions/fastapi-zcore-framework?color=teal" alt="Python Versions">
  </a>
</p>

---

## 🧭 Overview

FastAPI provides an efficient, ergonomic foundation for HTTP handling, asynchronous I/O, and data validation using Pydantic. However, as codebases grow beyond simple APIs into larger services or modular monoliths, engineering teams frequently face common structural challenges:

* **Session Lifetime & Leaks:** Database sessions can inadvertently leak across concurrent coroutines or terminate prematurely during background tasks.
* **Transaction Coordination:** Workflows that update multiple repositories often lack a unified, atomic transaction boundary, risking partial writes when a middle step fails.
* **Schema Proliferation:** Writing and maintaining separate Pydantic models (`UserPublic`, `UserInternal`, `UserAdminResponse`) solely to conceal specific fields across user roles introduces repetitive boilerplate.
* **Module Coupling:** Large domain modules frequently develop tangled dependency graphs and circular imports when interacting with shared databases.

Full-stack monolithic frameworks solve these issues by introducing comprehensive conventions, coupled ORMs, and centralized registries. While effective, this approach can reduce architectural flexibility.

**ZCore is designed as an intermediate layer.** It does not hide FastAPI or replace its router. Instead, it provides a set of decoupled architectural primitives—such as a re-entrant Unit of Work, constructor-based Inversion of Control (IoC), context-aware schema filtering (`Zchema`), and lifecycle plugins—built on top of standard **FastAPI**, **SQLAlchemy 2.0 (Async)**, and **Pydantic V2**.

You can adopt individual components where they solve concrete architectural needs or use the structured chassis as a cohesive pattern for modular application design.

---

## 🛍️ Reference Implementation: ZShop

To examine how these architectural patterns interact in an end-to-end codebase, refer to **[ZShop](https://github.com/Baseryn/zshop)**, a modular monolith reference application:

* **Context & Security Inspection:** Demonstrates active correlation IDs (`x-request-id`), execution profiling, and role-based data projection.
* **Role-Based Schema Pruning:** Displays how confidential attributes (such as supplier costs) are dynamically excluded based on the authenticated context via `Zchema`.
* **Atomic Rollback Demonstration:** Validates multi-repository rollback guarantees by simulating invariant violations during order checkout.
* **Contract-Driven Decoupling:** Demonstrates cross-domain communication via Python `Protocols` (`ProductContract`) rather than direct database model imports.

👉 **[Explore the ZShop Reference Codebase](https://github.com/Baseryn/zshop)**

---

## ⚖️ Architectural Comparison

| Area | Plain FastAPI | Heavy Monoliths (e.g., Django) | FastAPI with ZCore |
| :--- | :--- | :--- | :--- |
| **Philosophy** | Minimalist microframework; architectural organization is left entirely to the developer. | Batteries-included, opinionated; coupled to built-in ORM and global application registries. | **Composable layer;** provides structured architectural patterns while keeping routers and schemas standard. |
| **Field-Level Security** | Manual `if/else` checks or maintaining distinct Pydantic DTOs for each access level. | Model-level field permissions or specialized serializer layers. | **Context-aware pruning (`Zchema`);** dynamically filters restricted fields across validation, serialization, and dynamic schema introspection. |
| **Transaction Management** | Manual `session.commit()` calls across routes and services; potential for partial writes. | Built-in transaction blocks (`transaction.atomic()`). | **Re-entrant `UnitOfWork`;** depth-aware coordination with domain events buffered until root commit succeeds. |
| **Background Processing** | Request-scoped sessions passed to `BackgroundTasks` often encounter closed session errors. | Relies on external task queue workers (e.g., Celery) even for simple background steps. | **Scoped `@background_task`;** isolates session allocation and IoC lifecycles from completed request scopes. |
| **Modular Domain Design** | Standard `APIRouter` instances; vulnerable to circular imports as features expand. | Strict app layout and global settings configuration. | **DAG-driven `Plugin` system;** topological startup sorting and pure Python Protocol contracts for interface decoupling. |
| **Dataset Pagination** | Manual `offset / limit` SQL slicing, which may suffer performance degradation on large tables. | Built-in offset paginators. | **Keyset Cursor Pagination;** compound keyset traversal with URL-safe base64 cursors, alongside dynamic JSON filtering. |
| **Test Isolation** | Manual database cleanup or recreating schema fixtures between test runs. | Transactional test cases rolling back writes per test method. | **Rollback harness via `ZTestClient`;** automated savepoint rollbacks, container isolation, and context mocking. |

---

## 🚀 Quick Start

### 1. Installation

```bash
pip install "fastapi-zcore-framework[all]"
```

### 2. Initialize a Project

The interactive `zc` CLI configures your workspace, sets up a virtual environment (`uv` or `pip`), and bootstraps the appropriate asynchronous database driver:

```bash
# Interactive setup
zc init core_api && cd core_api

# Scaffold a domain module
zc startapp tasks
```

### 3. Define Your Domain Model

In `tasks/models.py` (standard SQLAlchemy 2.0):

```python
import uuid
from sqlalchemy.orm import Mapped, mapped_column
from zcore import Base, SoftDeleteMixin

class Task(Base, SoftDeleteMixin):
    __tablename__ = "tasks"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(index=True)
    is_completed: Mapped[bool] = mapped_column(default=False)
```

### 4. Run the Development Server

```bash
zc run
```

The application starts at `http://127.0.0.1:8000` with 8 standard endpoints managed by `BaseRouter`:
* `POST /tasks/` — Entity creation
* `GET /tasks/{id}` — Single-record retrieval with primary key inspection
* `GET /tasks/` — List retrieval supporting offset or keyset cursor pagination
* `POST /tasks/search` — Dynamic nested JSON filtering and sorting
* `POST /tasks/lookup` — Lightweight, column-projected endpoint for relational references
* `PUT /tasks/{id}` & `PATCH /tasks/{id}` — Complete and partial updates
* `DELETE /tasks/{id}` — Soft delete (or physical purge via `?force=true`)
* `GET /tasks/_schema` — Role-aware schema introspection (all actions or filtered via `?action=create|update|view|lookup`)

---

## 🏛️ Core Architectural Components

<details>
<summary><strong>1. Context Shielding & Field-Level Access Control (<code>Zchema</code>)</strong></summary>

### Problem
Managing multiple versions of the same schema (`ProductCustomerView`, `ProductInternalView`, `ProductAdminUpdate`) to hide sensitive attributes (such as purchase cost or internal remarks) introduces redundancy and increases the risk of accidental data leaks.

### Solution
Define the schema once by inheriting from `Zchema`. When fields are marked as restricted within the current request context (`ctx.restricted_fields`), ZCore prunes them across:
1. **Input validation:** Silently discards restricted fields to prevent mass assignment.
2. **Output serialization:** Removes restricted attributes before JSON encoding.
3. **Schema introspection:** Omits restricted properties dynamically when querying the `/_schema` endpoint.

```python
import uuid
from decimal import Decimal
from zcore import Zchema

class ProductResponse(Zchema):
    __model__ = "products"

    id: uuid.UUID
    name: str
    price: Decimal
    cost_price: Decimal | None = None      # Internal margin
    supplier_notes: str | None = None      # Vendor agreement notes
```

When restricted fields are pruned from a response, ZCore appends a `Vary: Authorization, Cookie` header to prevent shared HTTP caches or CDNs from caching privileged data representations for unprivileged clients.
</details>

<details>
<summary><strong>2. Atomic Unit of Work & Deferred Domain Events (<code>UnitOfWork</code>)</strong></summary>

### Problem
When business operations touch multiple repositories, issuing separate commits introduces the risk of partial database writes if an intermediate step fails. Additionally, publishing domain events (or sending notifications) before a database transaction physically commits can lead to state inconsistency if the transaction ultimately rolls back.

### Solution
ZCore implements a depth-aware, re-entrant `UnitOfWork`. Nested scopes flush changes safely without premature commits, while domain events remain buffered and are dispatched only after the root commit succeeds:

```python
from zcore import UnitOfWork

async with UnitOfWork(session=self.session, dispatcher=self.dispatcher) as uow:
    # 1. Update inventory
    await self.inventory_service.adjust_stock(product_id, -quantity)
    
    # 2. Persist order entity
    self.order_repo.add(new_order)
    await self.session.flush()

    # 3. Buffer domain event
    # If any error occurs before commit completes, this event is discarded.
    uow.register_event("order.created", {"order_id": str(new_order.id)})
```
</details>

<details>
<summary><strong>3. Constructor-Based Dependency Injection (<code>IoC Container</code>)</strong></summary>

### Problem
In complex applications, deep chains of FastAPI `Depends(...)` can become verbose. Furthermore, domain modules risk circular imports when cross-referencing concrete repository implementations.

### Solution
The built-in IoC container supports constructor-based auto-wiring via type annotations, caching constructor signatures to minimize reflection overhead. It supports Singleton, Scoped, and Transient lifecycles, and automatically skips parameters with default values unless explicitly registered:

```python
# Pure Python contract (contracts/catalog.py)
from typing import Protocol, runtime_checkable

@runtime_checkable
class ProductContract(Protocol):
    async def adjust_stock(self, product_id: str, delta: int) -> None: ...
```

```python
# Domain implementation (apps/orders/services.py)
from contracts.catalog import ProductContract

class OrderService(BaseService[Orders]):
    # Dependencies are resolved by constructor type hints
    def __init__(self, repository: OrderRepository, inventory: ProductContract):
        super().__init__(model=Orders, repository=repository)
        self.inventory = inventory
```
</details>

<details>
<summary><strong>4. Isolated Background Execution (<code>@background_task</code>)</strong></summary>

### Problem
FastAPI executes background tasks after completing the HTTP response. If a task receives an `AsyncSession` bound to the finished request lifecycle, the session is often closed by the time execution begins, causing connection errors.

### Solution
The `@background_task` decorator creates an independent execution scope. It provisions a dedicated database session, isolates context variables, and auto-wires type-annotated dependencies directly from the container:

```python
import uuid
from zcore import background_task

@background_task
async def generate_invoice_task(
    order_id: uuid.UUID,
    order_repo: OrderRepository,  # Injected with an independent session
) -> None:
    order = await order_repo.get(id=order_id)
    # Operates safely outside of the HTTP request lifecycle
```
</details>

<details>
<summary><strong>5. Composite Primary Keys & Keyset Pagination</strong></summary>

### Capabilities
* **Composite Primary Keys:** `BaseRepository` natively handles composite models and PostgreSQL partitioned tables (e.g., `(id, operation_date)`). Operations use tuple-based lookups (`tuple_(*pks).in_(...)`), allowing PostgreSQL to perform partition pruning without full table scans.
* **Compound Keyset Pagination:** `CursorPagination` supports non-primary-key sorting columns using compound keyset conditions `(col < val) OR (col == val AND pk < last_pk)`. It handles type coercion for `date`, `datetime`, `UUID`, and numeric types.
* **Mutual Exclusivity:** `SearchRequest` validates against conflicting pagination modes, raising a 422 error if both offset (`page > 1`) and keyset (`cursor`) parameters are supplied simultaneously.
</details>

<details>
<summary><strong>6. Isolated Testing Harness (<code>ZTestClient</code>)</strong></summary>

### Problem
Integration tests that write directly to a database typically require slow schema teardowns or manual cleanup scripts to prevent test state leakage.

### Solution
`ZTestClient` coordinates tests within transactional savepoints. Database modifications are automatically rolled back upon test completion, while container bindings and event listeners are restored from snapshots:

```python
import pytest
from zcore.testing import ZTestClient
from main import app

@pytest.mark.asyncio
async def test_order_placement_atomicity():
    async with ZTestClient(
        app,
        user_id="test-user-id",
        scopes=["orders:create", "products:view"]
    ) as client:
        response = await client.post("/checkout/orders/", json={...})
        assert response.status_code == 201
    # Transaction changes are rolled back automatically upon context exit
```
</details>

---

## 🛠️ Developer Tooling (`zc` CLI)

ZCore provides an interactive command-line interface powered by Questionary and Rich:

```text
$ zc

⚡ ZCore Framework v0.1.0-rc.3 • Modern Modular Monolith
 FastAPI • SQLAlchemy 2.0 • Pydantic V2

? What framework task would you like to perform? 
  ❯ 🧩 startapp   — Scaffold a modular domain app / plugin
    ⚡ run        — Launch Uvicorn development server
    📋 genenv     — Generate template .env from Settings class
    🔑 gensecret  — Generate cryptographically secure SECRET_KEY
    📦 init       — Scaffold a new ZCore project
    🚪 exit       — Exit CLI
```

### Command Reference

| Command | Key Options | Purpose |
| :--- | :--- | :--- |
| `zc` | — | Launches interactive dashboard |
| `zc init [name]` | `--db [sqlite\|postgres\|mysql]`, `-y` | Scaffolds project files, `.env`, and initializes virtual environment |
| `zc startapp [name]` | `--template / --no-template`, `--test`, `-y` | Generates architectural layers (models, schemas, repos, services, routers) |
| `zc run [app]` | `--host`, `--port`, `--reload`, `--workers`, `--env-file` | Starts Uvicorn with hierarchical configuration (`CLI > .env > Defaults`) |
| `zc gensecret` | — | Generates a 64-character cryptographic `SECRET_KEY` |
| `zc genenv` | `-o <output>`, `-f / --force` | Introspects active Pydantic `Settings` and outputs a template `.env` |

---

## 📢 Testing & Community Validation (Release Candidate `rc.3`)

We are currently evaluating **`v0.1.0-rc.3`** across real-world workloads prior to finalizing the `v1.0.0` stable release.

ZCore was created to address architectural friction points encountered in production environments. We welcome technical feedback, bug reports, and discussion on edge cases:

* **Evaluate the Showcase:** Review [ZShop](https://github.com/Baseryn/zshop) to observe how components interact in a concrete project.
* **Test Architectural Boundaries:** Evaluate `UnitOfWork` behavior under nested operations, verify complex queries with `SearchEngine`, or test composite key models.
* **Report Issues:** If an abstraction feels rigid, if a type hint fails strict static analysis, or if an edge case is unhandled, please open a detailed issue on GitHub.

👉 **[Submit Issues & Technical Feedback on GitHub](https://github.com/Baseryn/zcore/issues)**

---

## 📚 Documentation

Detailed guides, design explanations, and API references are available at our documentation portal:

* **[Quick Start Tutorial](https://zcore.baseryn.com/docs/quick-start)** — Basic project initialization and endpoint setup.
* **[Layer-by-Layer Guide](https://zcore.baseryn.com/docs/quick-learn/step-1)** — Walkthrough of model, repository, service, and router separation.
* **[Architectural Comparisons](https://zcore.baseryn.com/docs/comparisons)** — Technical comparisons with plain FastAPI and Django.
* **[How-To Guides](https://zcore.baseryn.com/docs/how-to)** — Practical patterns for Keyset Pagination, File Storage, and Caching.
* **[Core Concepts](https://zcore.baseryn.com/docs/core-concepts/context)** — Deep dives into DI auto-wiring, Unit of Work, Kernel DAG, and Context shielding.
* **[API Reference](https://zcore.baseryn.com/docs/api-reference/repository)** — Method signatures and parameter details.

---

## 📄 License

ZCore Framework is open-source software licensed under the **Apache License 2.0**.  
See the [LICENSE](https://github.com/Baseryn/zcore/blob/master/LICENSE) file for details.

---

<p align="center">
  <sub>Maintained by <a href="https://github.com/alialfostovar">Ali Alf Ostovar</a> and the <a href="https://github.com/Baseryn">Baseryn</a> open-source community.</sub>
</p>