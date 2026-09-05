"""ZCore CLI Scaffolding Templates."""

MAIN_PY_TEMPLATE = """import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI
from zcore import Kernel, db_manager, register_db_event_dispatcher, settings
from zcore.exceptions import AppException, app_exception_handler
from zcore.logging import setup_logging
from zcore.web import RequestLogMiddleware, ScopedDependencyMiddleware

setup_logging()

db_manager.init_app(config=settings.DATABASE)

kernel = Kernel()
register_db_event_dispatcher(kernel.dispatcher)

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=kernel.lifespan
)

kernel.setup(app)

app.add_middleware(RequestLogMiddleware)
app.add_middleware(ScopedDependencyMiddleware)

app.add_exception_handler(AppException, app_exception_handler)

@app.get("/")
async def root():
    return {
        "status": "healthy",
        "framework": "ZCore",
        "version": "0.1.0-beta.9",
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
TIMEZONE="UTC"

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

MODEL_TEMPLATE = """import uuid
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from zcore import Base

class {ModelName}(Base):
    __tablename__ = "{table_name}"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
"""

SCHEMA_TEMPLATE = """import uuid
from pydantic import ConfigDict
from zcore import Zchema

class {ModelName}Base(Zchema):
    __model__ = "{table_name}"

class {ModelName}Create({ModelName}Base):
    pass

class {ModelName}Update({ModelName}Base):
    pass

class {ModelName}Response({ModelName}Base):
    id: uuid.UUID
    
    model_config = ConfigDict(from_attributes=True)
"""

REPOSITORY_TEMPLATE = """from sqlalchemy.ext.asyncio import AsyncSession
from zcore import BaseRepository

from .models import {ModelName}
from .schemas import {ModelName}Create, {ModelName}Update

class {ModelName}Repository(BaseRepository[{ModelName}]):
    def __init__(self, db: AsyncSession):
        super().__init__(model={ModelName}, db=db)
"""

SERVICE_TEMPLATE = """from zcore import BaseService
from .models import {ModelName}
from .repositories import {ModelName}Repository

class {ModelName}Service(BaseService[{ModelName}]):
    def __init__(self, repository: {ModelName}Repository):
        super().__init__(model={ModelName}, repository=repository)
"""

ROUTER_TEMPLATE = """from typing import Any
from zcore import BaseRouter, RouteKey

from .models import {ModelName}
from .schemas import {ModelName}Create, {ModelName}Response, {ModelName}Update
from .services import {ModelName}Service

class {ModelName}Router(BaseRouter):
    model = {ModelName}
    create_schema = {ModelName}Create
    update_schema = {ModelName}Update
    schema_out = {ModelName}Response
    service = {ModelName}Service
    
    prefix = "/{app_name}"
    tags = ["{ModelName}"]

    def get_route_dependencies(self, route_key: RouteKey, action: str) -> list[Any]:
        return super().get_route_dependencies(route_key, action)

router_instance = {ModelName}Router()
"""

PLUGIN_TEMPLATE = """from fastapi import FastAPI
from zcore import Plugin

class {ModelName}Plugin(Plugin):
    name = "{app_name}"
    version = "0.1.0"
    dependencies = []

    def setup(self, app: FastAPI) -> None:
        pass

    async def before_startup(self) -> None:
        pass

    async def on_startup(self) -> None:
        pass

    async def after_startup(self) -> None:
        pass

    async def on_shutdown(self) -> None:
        pass
"""

TEST_TEMPLATE = """import uuid
import pytest
from main import app
from zcore.testing import ZTestClient

@pytest.mark.asyncio
async def test_get_{app_name}_list():
    uid = uuid.uuid4()
    async with ZTestClient(app, user_id=uid, scopes=["{app_name}:listview"]) as client:
        response = await client.get("/{app_name}/")
        assert response.status_code == 200
"""