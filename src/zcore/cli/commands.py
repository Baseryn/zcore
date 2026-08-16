import os
import sys
import secrets
import subprocess
from pathlib import Path
from .templates import (
    MAIN_PY_TEMPLATE,
    ENV_TEMPLATE,
    REQUIREMENTS_TEMPLATE,
    GITIGNORE_TEMPLATE,
    MODEL_TEMPLATE,
    SCHEMA_TEMPLATE,
    REPOSITORY_TEMPLATE,
    SERVICE_TEMPLATE,
    ROUTER_TEMPLATE,
    PLUGIN_TEMPLATE,
    TEST_TEMPLATE
)

def create_file(path: Path, content: str) -> None:
    if path.exists():
        print(f"⚠️  Skip: {path} already exists.")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ Created: {path}")

def init_project(project_name: str) -> None:
    project_dir = Path(project_name.lower())
    if project_dir.exists():
        print(f"❌ Error: Directory '{project_dir}' already exists.")
        sys.exit(1)

    project_dir.mkdir(parents=True)
    generated_secret = secrets.token_hex(32)
    
    files_to_create = {
        "main.py": MAIN_PY_TEMPLATE,
        ".env": ENV_TEMPLATE.format(project_name=project_name, secret_key=generated_secret),
        "requirements.txt": REQUIREMENTS_TEMPLATE,
        ".gitignore": GITIGNORE_TEMPLATE
    }

    print(f"\n🧱 Initializing ZCore Project: '{project_name}'...")
    for filename, content in files_to_create.items():
        create_file(project_dir / filename, content)
        
    db_file = project_dir / "zcore_dev.db"
    db_file.touch()
    print(f"✅ Created: {db_file}")
        
    print(f"\n🎉 Project '{project_name}' initialized successfully!")
    print(f"👉 Run: 'cd {project_name}' and run 'python -m zcore.cli startapp <app_name>' to generate a plugin!")

def start_app(app_name: str, with_template: bool = False, with_test: bool = False) -> None:
    app_dir = Path(app_name.lower())
    
    if app_dir.exists():
        print(f"❌ Error: App folder '{app_dir}' already exists.")
        sys.exit(1)

    app_dir.mkdir(parents=True)
    
    model_name = "".join(word.capitalize() for word in app_name.split("_"))
    table_name = app_name.lower()
    
    context = {
        "ModelName": model_name,
        "table_name": table_name,
        "app_name": table_name,
        "project_name": Path(os.getcwd()).name
    }

    files_to_create = {
        "__init__.py": "",
        "models.py": MODEL_TEMPLATE.format(**context) if with_template else "",
        "schemas.py": SCHEMA_TEMPLATE.format(**context) if with_template else "",
        "repositories.py": REPOSITORY_TEMPLATE.format(**context) if with_template else "",
        "services.py": SERVICE_TEMPLATE.format(**context) if with_template else "",
        "routers.py": ROUTER_TEMPLATE.format(**context) if with_template else "",
        "plugin.py": PLUGIN_TEMPLATE.format(**context)
    }

    if with_test:
        files_to_create["tests.py"] = TEST_TEMPLATE.format(**context) if with_template else ""

    print(f"\n🚀 Scaffolding ZCore Domain App: {model_name}...")
    for filename, content in files_to_create.items():
        create_file(app_dir / filename, content)
        
    print(f"\n🎉 Modular App '{app_name}' created successfully!")
    print("👉 REGISTER this plugin in your main.py:")
    print(f"   from {app_name}.plugin import {model_name}Plugin")
    print(f"   kernel.add_plugin({model_name}Plugin())")

def run_server() -> None:
    if not os.path.exists("main.py"):
        print("❌ Error: 'main.py' not found in current directory. Are you in a ZCore project root?")
        sys.exit(1)

    host = "127.0.0.1"
    port = "8000"

    if os.path.exists(".env"):
        with open(".env", "r") as env_file:
            for line in env_file:
                if line.strip() and not line.startswith("#"):
                    parts = line.strip().split("=", 1)
                    if len(parts) == 2:
                        key, val = parts[0].strip(), parts[1].strip()
                        if key == "HOST":
                            host = val
                        if key == "PORT":
                            port = val

    print(f"📡 Starting ZCore Dev Server on {host}:{port}...")
    
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f".{os.pathsep}{current_pythonpath}" if current_pythonpath else "."

    try:
        subprocess.run(
            [sys.executable, "-m", "uvicorn", "main:app", f"--host={host}", f"--port={port}", "--reload"],
            env=env,
            check=True
        )
    except KeyboardInterrupt:
        print("\n👋 Server shutdown cleanly.")
    except Exception as e:
        print(f"❌ Failed to run Uvicorn dev server: {e}")

def gen_env(output_file: str = ".env.example", force: bool = False) -> None:
    """Generates a template .env file based on the registered Settings class.
    
    This function discovers the active Settings class by trying to import the user's main
    module (supporting relative imports by using package context), resolving the settings 
    instance via IoC container or subclass tracking, and formatting the field default values.
    """
    import importlib
    cwd = Path.cwd()
    parent_dir = cwd.parent
    module_name = cwd.name

    imported = False
    if module_name.isidentifier():
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        try:
            importlib.import_module(f"{module_name}.main")
            imported = True
        except Exception:
            pass

    if not imported:
        if str(cwd) not in sys.path:
            sys.path.insert(0, str(cwd))
        try:
            import main  # noqa: F401
        except Exception:
            pass

    from zcore.config import get_settings, Settings
    from pydantic_core import PydanticUndefined

    subclasses = Settings.__subclasses__()
    if subclasses:
        settings_class = max(subclasses, key=lambda c: len(c.model_fields))
    else:
        try:
            settings_class = get_settings().__class__
        except Exception:
            settings_class = Settings

    out_path = Path(output_file)
    if out_path.exists() and not force:
        print(f"❌ Error: Output file '{out_path}' already exists. Use --force to overwrite.")
        sys.exit(1)

    env_lines = []
    for field_name, field_info in settings_class.model_fields.items():
        default_val = field_info.default
        if default_val is PydanticUndefined or default_val is None:
            env_lines.append(f"{field_name}=")
        else:
            if isinstance(default_val, bool):
                env_lines.append(f"{field_name}={str(default_val)}")
            elif isinstance(default_val, (int, float)):
                env_lines.append(f"{field_name}={default_val}")
            else:
                env_lines.append(f"{field_name}={str(default_val)}")

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(env_lines) + "\n")
        print(f"✅ Created: {out_path} based on '{settings_class.__name__}' configuration fields.")
    except Exception as e:
        print(f"❌ Failed to generate env file: {e}")
        sys.exit(1)