MAIN_PY_TEMPLATE = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI
from zcore import Kernel, settings
from zcore.web import RequestLogMiddleware, ScopedDependencyMiddleware
from zcore.exceptions import app_exception_handler, AppException
from zcore.db import db_manager, register_db_event_dispatcher
from zcore.logging import setup_logging

# Initialize Structured Logging
setup_logging()

# Initialize Database Manager (SQLAlchemy Async Engine)
db_manager.init_app(
    db_url=settings.DATABASE_URL,
    pool_size=settings.POOL_SIZE,
    max_overflow=settings.MAX_OVERFLOW,
    echo=(settings.DEBUG)
)

# Initialize ZCore Kernel & Plugins
kernel = Kernel()

# Register global database event dispatcher
register_db_event_dispatcher(kernel.dispatcher)

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=kernel.lifespan
)

kernel.setup(app)

# Load Core Architectural Middlewares
app.add_middleware(RequestLogMiddleware)
app.add_middleware(ScopedDependencyMiddleware)

# Register Centralized Error Handlers
app.add_exception_handler(AppException, app_exception_handler)

@app.get("/")
async def root():
    return {
        "status": "healthy",
        "framework": "ZCore",
        "debug": settings.DEBUG
    }
"""

ENV_TEMPLATE = """# --- Local Runner / Infrastructure ---
PYTHONPATH=.
HOST=127.0.0.1
PORT=8000

# --- ZCore Application Settings ---
PROJECT_NAME="{project_name}"
DEBUG=True

DATABASE_URL=sqlite+aiosqlite:///zcore_dev.db
DATABASE_TEST_URL=sqlite+aiosqlite:///zcore_test.db
POOL_SIZE=5
MAX_OVERFLOW=10

SECRET_KEY="{secret_key}"
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7

STORAGE_PATH=./storage
REDIS_URL=
"""

REQUIREMENTS_TEMPLATE = """fastapi-zcore-framework[all]
uvicorn>=0.22.0
"""

GITIGNORE_TEMPLATE = """# Byte-compiled / optimized / DLL files
__pycache__/
*.py[cod]
*$py.class

# C extensions
*.so

# Distribution / packaging
bin/
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
share/python-wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual Environments
.venv/
venv/
ENV/
env/

# IDEs and editors
.idea/
.vscode/
*.swp
*.swo

# Local configuration & environment variables
.env
.env.local
.env.*
"""

# --- App Module Templates ---

MODEL_TEMPLATE = """from zcore import Base
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
import uuid

class {ModelName}(Base):
    __tablename__ = "{table_name}"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # TODO: Define your schema / columns below:
    # name: Mapped[str] = mapped_column(String(255), index=True)
"""

SCHEMA_TEMPLATE = """from zcore import Zchema
from pydantic import ConfigDict
import uuid

class {ModelName}Base(Zchema):
    __model__ = "{table_name}"
    # TODO: Add your shared model attributes
    pass

class {ModelName}Create({ModelName}Base):
    pass

class {ModelName}Update({ModelName}Base):
    pass

class {ModelName}Response({ModelName}Base):
    id: uuid.UUID
    
    model_config = ConfigDict(from_attributes=True)
"""

REPOSITORY_TEMPLATE = """from zcore import BaseRepository
from sqlalchemy.ext.asyncio import AsyncSession

from .models import {ModelName}
from .schemas import {ModelName}Create, {ModelName}Update

class {ModelName}Repository(BaseRepository[{ModelName}, {ModelName}Create, {ModelName}Update]):
    def __init__(self, db: AsyncSession):
        super().__init__(model={ModelName}, db=db)
"""

SERVICE_TEMPLATE = """from zcore import BaseService
from .models import {ModelName}
from .schemas import {ModelName}Create, {ModelName}Update
from .repositories import {ModelName}Repository

class {ModelName}Service(BaseService[{ModelName}, {ModelName}Create, {ModelName}Update]):
    def __init__(self, repository: {ModelName}Repository):
        super().__init__(model={ModelName}, repository=repository)
"""

ROUTER_TEMPLATE = """from typing import Any
from zcore import BaseRouter, RouteKey

from .schemas import {ModelName}Create, {ModelName}Update, {ModelName}Response
from .services import {ModelName}Service
from .models import {ModelName}

class {ModelName}Router(BaseRouter[{ModelName}Create, {ModelName}Update]):
    model = {ModelName}
    create_schema = {ModelName}Create
    update_schema = {ModelName}Update
    schema_out = {ModelName}Response
    service = {ModelName}Service
    
    prefix = "/{app_name}"
    tags = ["{ModelName}"]
    # expose_schemas = True  # Set True to auto-expose JSON Schema on endpoints

    def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
        \"\"\"Retrieve the dependencies list for the router endpoints.
        
        By overriding this method, you can dynamically inject custom authentication,
        authorization, logging, or rate-limiting dependencies for specific route keys.
        \"\"\"
        # Example: Add custom dependencies to delete operation, fallback to standard permissions for others
        if route_key == RouteKey.DELETE:
            # return [MyCustomAdminPermission()]
            pass
            
        return super().get_route_dependencies(route_key, action)

# Export the FastAPI router
router_instance = {ModelName}Router()
"""

PLUGIN_TEMPLATE = """from fastapi import FastAPI
from zcore import Plugin

class {ModelName}Plugin(Plugin):
    name = "{app_name}"
    version = "0.1.0"
    dependencies = []  # Add dependent plugin names here (e.g. ['security_plugin'])

    def setup(self, app: FastAPI) -> None:
        # Wire this module's sub-router directly to the central FastAPI app
        
        # from .routers import router_instance
        # app.include_router(router_instance.router)
        pass

    async def before_startup(self) -> None:
        # Executes before any other plugin starts
        pass

    async def on_startup(self) -> None:
        # Standard startup logic (e.g. warming up local caches)
        pass

    async def after_startup(self) -> None:
        # Post-startup cleanups
        pass

    async def on_shutdown(self) -> None:
        # Cleanup tasks for this specific module
        pass
"""

TEST_TEMPLATE = """import pytest
import uuid
from zcore.testing import ZTestClient
from main import app

@pytest.mark.asyncio
async def test_get_{app_name}_list():
    uid = uuid.uuid4()
    async with ZTestClient(app, user_id=uid, scopes=["{app_name}:listview"]) as client:
        response = await client.get("/{app_name}/")
        assert response.status_code == 200
"""
