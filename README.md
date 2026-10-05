<p align="center">
  <img src="https://raw.githubusercontent.com/Baseryn/zcore-docs/master/public/banner.png" alt="FastAPI ZCore Framework Logo" width="620">
</p>

<p align="center">
  <strong>A composable architectural foundation for building scalable applications on top of FastAPI.</strong><br>
  <em>Standardize your structure, manage atomic transactions, and protect sensitive data — without sacrificing FastAPI's flexibility.</em>
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

**FastAPI is an exceptional microframework for HTTP routing, request parsing, and asynchronous I/O.**

However, as applications grow into medium and large systems, teams inevitably encounter an architectural gap:
* Database sessions leak across asynchronous tasks or background jobs.
* Business workflows spanning multiple repositories lack reliable, all-or-nothing transaction boundaries.
* Teams end up writing dozens of nearly identical DTO schemas just to hide internal fields (like profit margins, supplier terms, or sensitive IDs) from different user roles.
* Domain modules become tightly coupled through circular imports and tangled dependency trees.

Heavy enterprise frameworks solve these challenges by enforcing rigid conventions, proprietary ORMs, and heavy abstractions that eliminate your freedom.

**ZCore is the middle ground:**
ZCore provides a structured, modular layer that sits directly on top of standard FastAPI, SQLAlchemy 2.0, and Pydantic V2. It introduces proven architectural patterns (Unit of Work, IoC Dependency Injection, Field-Level Security, and Domain Event Dispatching) as **composable building blocks**. 

> **Your code remains 100% standard FastAPI.** Adopt ZCore piece by piece where it solves real problems, or use the full architectural stack — with zero vendor lock-in.

---

## 🛍️ Reference Showcase: ZShop

To see ZCore's architectural concepts in action before writing a single line of code, explore **[ZShop](https://github.com/Baseryn/zshop)** — the official Modular Monolith e-commerce reference implementation:

* **Interactive Live Inspector Dock:** Monitor active request context, correlation IDs (`x-request-id`), execution latencies, and real-time SSE stream events.
* **1-Click RBAC Persona Switcher:** Switch between SuperAdmin, Store Manager, Customer, and Guest to watch confidential data fields automatically vanish from responses via `Zchema`.
* **Atomic Rollback Demonstration:** An interactive deficit checkout button verifying that stock adjustments and order persistence roll back atomically when any invariant fails.
* **Decoupled Architecture:** Strict inter-module communication using Python Protocols (`ProductContract`) without cross-domain database coupling.

👉 **[Explore the ZShop Showcase Repository](https://github.com/Baseryn/zshop)**

---

## ⚖️ Why ZCore? (An Honest Comparison)

| Architectural Concern | Plain FastAPI | Full-Stack Monoliths (e.g. Django) | With ZCore on FastAPI |
| :--- | :--- | :--- | :--- |
| **Application Philosophy** | Minimalist microframework; no opinion on database, folders, or architecture. | Opinionated, batteries-included; tightly coupled to its own ORM and template engine. | **Composable layer;** provides enterprise architecture patterns while keeping FastAPI completely standard. |
| **Role-Based Data Exposure** | Writing multiple Pydantic models per role (`UserPublic`, `UserAdmin`) or manual `if/else` checks. | Model-level permissions or custom serializer logic. | **Single-schema context shielding (`Zchema`);** forbidden fields are pruned dynamically from validation, serialization, and OpenAPI. |
| **Multi-Repository Operations** | Scattered `session.commit()` calls; high risk of partial writes on failure. | Managed transaction blocks (`transaction.atomic()`). | **Re-entrant `UnitOfWork`;** coordinated all-or-nothing boundaries with domain events deferred until physical commit succeeds. |
| **Background Processing** | Passing request sessions to background tasks often leads to `Session is closed` errors. | Requires external queue workers (Celery, RQ) even for simple background jobs. | **Dedicated `@background_task`;** automatically isolates DB sessions and IoC lifecycles from completed HTTP requests. |
| **Domain Modularity** | Standard APIRouters; easily slips into spaghetti code and circular imports at scale. | Django Apps with rigid settings and global app registries. | **DAG-driven `Plugin` system;** topological startup sorting and pure Python Protocol contracts for zero-coupling. |
| **Dataset Pagination** | Manual `offset / limit` SQL slicing that degrades on large tables. | Built-in offset paginators. | **Keyset Cursor Pagination (Base64);** zero-drift pagination and dynamic AST-like JSON search engine. |
| **Testing Isolation** | Manual DB cleanup routines or slow schema recreation per test file. | TransactionTestCase rolling back transactions per test. | **Zero-side-effect `ZTestClient`;** automated savepoint rollbacks, context mocking, and container sandboxing. |

---

## 🚀 Quick Start (in Under 60 Seconds)

### 1. Installation

```bash
pip install "fastapi-zcore-framework[all]"
```

### 2. Scaffold a New Project

The interactive `zc` CLI configures your environment, sets up a virtual environment (`uv` or `pip`), and provisions your database driver:

```bash
# Interactive mode (recommended)
zc init core_api && cd core_api

# Generate your first domain module
zc startapp tasks
```

### 3. Define Your Domain Model

Open `tasks/models.py` (standard SQLAlchemy 2.0):

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

Your API is running at `http://127.0.0.1:8000` with **8 production-ready endpoints** out of the box:
* `POST /tasks/` — Create entity
* `GET /tasks/{id}` — Fetch single record (with automatic PK type inspection)
* `GET /tasks/` — List records (with cursor or offset pagination)
* `POST /tasks/search` — Dynamic nested JSON filtering engine
* `POST /tasks/lookup` — Lightweight field-projected endpoint for dropdowns
* `PUT /tasks/{id}` & `PATCH /tasks/{id}` — Full and partial updates
* `DELETE /tasks/{id}` — Soft delete (or permanent purge via `?force=true`)
* `GET /tasks/?schema=true` — Dynamic role-aware frontend form schema

---

## 🏛️ Core Architectural Patterns

<details>
<summary><strong>1. Context Shielding & Role-Based Field Pruning (<code>Zchema</code>)</strong></summary>

### The Problem
In enterprise applications, different roles require different views of the same data. A Customer must not see wholesale cost prices or internal supplier notes, while a Store Manager requires full financial transparency. Maintaining separate DTOs (`ProductPublic`, `ProductManager`, `ProductCreate`, `ProductAdminUpdate`) creates boilerplate and maintenance overhead.

### The ZCore Solution
Define your model once inheriting from `Zchema`. When a user's security context marks a field as restricted, ZCore automatically prunes it across **input parsing** (preventing mass assignment), **response serialization** (preventing data leakage), and **OpenAPI schema generation**:

```python
import uuid
from decimal import Decimal
from pydantic import Field
from zcore import Zchema

class ProductResponse(Zchema):
    __model__ = "products"

    id: uuid.UUID
    name: str
    price: Decimal
    cost_price: Decimal | None = None      # 🔒 Sensitive business margin
    supplier_notes: str | None = None  # 🔒 Internal agreement details
```

* **When a Customer views the product:** `cost_price` and `supplier_notes` are completely omitted from the JSON output. ZCore automatically attaches a `Vary: Authorization, Cookie` header to protect downstream CDNs from cache poisoning.
* **When a Manager views the product:** All fields are returned transparently.
</details>

<details>
<summary><strong>2. Atomic Unit of Work & Deferred Domain Events (<code>UnitOfWork</code>)</strong></summary>

### The Problem
In distributed or multi-repository architectures, business actions involve multiple persistence steps (e.g., deducting inventory, recording a purchase order, and generating an invoice). If step 2 fails, step 1 must be reverted. Furthermore, if you send an email or emit a domain event *before* the database commit succeeds, a subsequent failure leaves external systems out of sync.

### The ZCore Solution
ZCore implements a re-entrant, depth-aware `UnitOfWork`. Nested transactions safely flush changes to savepoints without prematurely committing, while domain events are buffered and dispatched **only after the root commit succeeds**:

```python
from zcore import UnitOfWork

async with UnitOfWork(session=self.session, dispatcher=self.dispatcher) as uow:
    # 1. Adjust inventory
    await self.inventory_service.adjust_stock(product_id, -quantity)
    
    # 2. Persist order
    self.order_repo.add(new_order)
    await self.session.flush()

    # 3. Buffer domain event
    # If the transaction rolls back, this event is discarded immediately!
    uow.register_event("order.created", {"order_id": str(new_order.id)})
```
</details>

<details>
<summary><strong>3. Contract-Driven Decoupling via Inversion of Control (<code>IoC Container</code>)</strong></summary>

### The Problem
Modular monoliths frequently suffer from circular imports when domain A (e.g., Orders) needs data from domain B (e.g., Catalog). Importing repositories or models across module boundaries creates tight database coupling.

### The ZCore Solution
Domains communicate strictly through pure Python `Protocols` (Contracts). The IoC Container auto-wires dependencies at runtime using constructor type hints with signature caching:

```python
# contracts/catalog.py (No database imports!)
from typing import Protocol, runtime_checkable

@runtime_checkable
class ProductContract(Protocol):
    async def adjust_stock(self, product_id: str, delta: int) -> None: ...
```

```python
# apps/orders/services.py
from contracts.catalog import ProductContract

class OrderService(BaseService[Orders]):
    # Pure Python constructor: Auto-wired by ZCore's IoC container!
    def __init__(self, repository: OrderRepository, inventory: ProductContract):
        super().__init__(model=Orders, repository=repository)
        self.inventory = inventory
```
</details>

<details>
<summary><strong>4. Isolated Background Scopes (<code>@background_task</code>)</strong></summary>

### The Problem
FastAPI's built-in `BackgroundTasks` executes callables after sending the HTTP response. If you pass an active request-scoped database session to a background task, the session is closed by the time the worker executes, causing `InterfaceError: Session is closed`.

### The ZCore Solution
The `@background_task` decorator creates an independent execution scope, provisions a dedicated database session, binds an isolated logging task ID, and auto-resolves annotated dependencies from the container:

```python
from zcore import background_task

@background_task
async def generate_invoice_task(
    order_id: uuid.UUID,
    order_repo: OrderRepository,  # Automatically injected with an isolated session!
) -> None:
    order = await order_repo.get(id=order_id)
    # Safely executes without relying on completed HTTP request lifecycles
```
</details>

<details>
<summary><strong>5. Zero-Side-Effect Testing Engine (<code>ZTestClient</code>)</strong></summary>

### The Problem
Integration tests that write to a database require tedious teardown scripts or slow table recreation between test cases, leading to flaky test suites.

### The ZCore Solution
`ZTestClient` executes tests inside **transactional savepoint rollbacks**. Changes are rolled back immediately upon test completion, keeping test runs fast and deterministic:

```python
import pytest
from zcore.testing import ZTestClient
from main import app

@pytest.mark.asyncio
async def test_order_placement_atomicity():
    async with ZTestClient(
        app,
        user_id="customer-uuid",
        scopes=["orders:create", "products:view"]
    ) as client:
        response = await client.post("/checkout/orders/", json={...})
        assert response.status_code == 201
    # Database is automatically rolled back to its exact pre-test state!
```
</details>

---

## 🛠️ Interactive Developer Tooling (`zc` CLI)

ZCore includes an interactive terminal user interface (TUI) powered by Questionary and Rich:

```text
$ zc

⚡ ZCore Framework v0.1.0-rc.2 • Modern Modular Monolith
 FastAPI • SQLAlchemy 2.0 • Pydantic V2

? What framework task would you like to perform? 
  ❯ 🧩 startapp   — Scaffold a modular domain app / plugin
    ⚡ run        — Launch Uvicorn development server
    📋 genenv     — Generate template .env from Settings class
    🔑 gensecret  — Generate cryptographically secure SECRET_KEY
    📦 init       — Scaffold a new ZCore project
    🚪 exit       — Exit CLI
```

### CLI Command Summary

| Command | Key Flags | Purpose |
| :--- | :--- | :--- |
| `zc` | — | Open interactive terminal dashboard |
| `zc init [name]` | `--db [sqlite\|postgres\|mysql]`, `-y` | Bootstrap project with `.env`, `.venv`, and driver dependencies |
| `zc startapp [name]` | `--template / --no-template`, `--test`, `-y` | Generate domain module layers (models, schemas, repos, services, routers) |
| `zc run [app]` | `--host`, `--port`, `--reload`, `--workers`, `--env-file` | Run server with cascading config hierarchy (`CLI > .env > Defaults`) |
| `zc gensecret` | — | Generate a 64-character cryptographic `SECRET_KEY` |
| `zc genenv` | `-o <output>`, `-f / --force` | Introspect active Pydantic `Settings` and scaffold `.env.example` |

---

## 📢 Community Testing & Feedback (Release Candidate `rc.2`)

We are currently validating **`v0.1.0-rc.2`** before tagging the official `v1.0.0` stable release. 

We built ZCore to solve architectural pain points we faced in production. Before declaring the API stable, **we need your critical engineering feedback:**

* **Try the Showcase:** Clone [ZShop](https://github.com/Baseryn/zshop), run the seed script, and test the endpoints.
* **Test the Edge Cases:** Push the boundaries of `UnitOfWork`, test complex nested queries with `SearchEngine`, or inspect field pruning with `Zchema`.
* **Break It & Report:** If an abstraction feels awkward, if a type hint breaks, or if an async session leaks, please open an issue!

👉 **[Submit Issues & Architectural Feedback on GitHub](https://github.com/Baseryn/zcore/issues)**

---

## 📚 Documentation Index

Comprehensive guides, architectural deep-dives, and complete API references are available at our official documentation portal:

* **[Quick Start Tutorial](https://zcore.baseryn.com/docs/quick-start)** — From zero to a working API in 60 seconds.
* **[10-Step Quick Learn](https://zcore.baseryn.com/docs/quick-learn/step-1)** — Systematic layer-by-layer walkthrough.
* **[Architectural Comparisons](https://zcore.baseryn.com/docs/comparisons)** — Honest comparisons with plain FastAPI, Django, and other frameworks.
* **[How-To Guides](https://zcore.baseryn.com/docs/how-to)** — Practical recipes for Multi-tenancy, Keyset Pagination, File Uploads, and Caching.
* **[Core Concepts](https://zcore.baseryn.com/docs/core-concepts/context)** — Deep dives into DI auto-wiring, Unit of Work, Kernel DAG, and Context shielding.
* **[API Reference](https://zcore.baseryn.com/docs/api-reference/repository)** — Detailed signatures and class specifications.

---

## 📄 License

ZCore Framework is open-source software licensed under the **Apache License 2.0**.  
See the [LICENSE](https://github.com/Baseryn/zcore/blob/master/LICENSE) file for details.

---

<p align="center">
  <sub>Developed with architectural rigor and care by <a href="https://github.com/alialfostovar">Ali Alf Ostovar</a> / <a href="https://github.com/Baseryn">Baseryn</a>.</sub>
</p>