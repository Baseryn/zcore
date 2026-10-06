import contextlib
import importlib
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from pydantic_core import PydanticUndefined
from rich.panel import Panel
from rich.tree import Tree

from zcore.cli.templates import (
    ENV_TEMPLATE,
    GITIGNORE_TEMPLATE,
    MAIN_PY_TEMPLATE,
    MODEL_TEMPLATE,
    PLUGIN_TEMPLATE,
    REPOSITORY_TEMPLATE,
    REQUIREMENTS_TEMPLATE,
    ROUTER_TEMPLATE,
    SCHEMA_TEMPLATE,
    SERVICE_TEMPLATE,
    TEST_TEMPLATE,
)
from zcore.cli.ui import (
    ZCORE_ACCENT,
    ZCORE_MUTED,
    ZCORE_PRIMARY,
    ZCORE_TEXT,
    console,
    print_step_footer,
    print_step_header,
)
from zcore.config import Settings, get_settings

DB_DRIVERS = {
    "sqlite": {"url": "sqlite+aiosqlite:///zcore_dev.db", "pkg": ""},
    "postgres": {
        "url": "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/zcore_dev",
        "pkg": "\nasyncpg>=0.29.0",
    },
    "mysql": {
        "url": "mysql+aiomysql://root:root@127.0.0.1:3306/zcore_dev",
        "pkg": "\naiomysql>=0.2.0",
    },
}

COMPONENT_TEMPLATES = {
    "models": ("models.py", MODEL_TEMPLATE),
    "schemas": ("schemas.py", SCHEMA_TEMPLATE),
    "repositories": ("repositories.py", REPOSITORY_TEMPLATE),
    "services": ("services.py", SERVICE_TEMPLATE),
    "routers": ("routers.py", ROUTER_TEMPLATE),
    "plugin": ("plugin.py", PLUGIN_TEMPLATE),
    "tests": ("test_{app_name}.py", TEST_TEMPLATE),
}


def create_file(path: Path, content: str) -> None:
    if path.exists():
        console.print(f"[yellow]⚠️  Skip:[/yellow] {path} already exists.")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def is_zcore_project(path: Path = Path.cwd()) -> bool:
    main_py = path / "main.py"
    if main_py.exists():
        try:
            content = main_py.read_text(encoding="utf-8", errors="ignore")
            return "zcore" in content or "Kernel" in content
        except Exception:
            return False
    return False


def find_zcore_projects(path: Path = Path.cwd()) -> list[str]:
    projects = []
    try:
        for item in path.iterdir():
            if item.is_dir() and not item.name.startswith((".", "_", "venv")):
                if is_zcore_project(item):
                    projects.append(item.name)
    except Exception:
        pass
    return sorted(projects)


def setup_virtualenv(project_dir: Path, pkg_manager: str = "pip") -> bool:
    try:
        if pkg_manager == "uv":
            subprocess.run(
                ["uv", "venv", ".venv"],
                cwd=project_dir,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["uv", "pip", "install", "-r", "requirements.txt"],
                cwd=project_dir,
                check=True,
                capture_output=True,
            )
        else:
            subprocess.run(
                [sys.executable, "-m", "venv", ".venv"],
                cwd=project_dir,
                check=True,
                capture_output=True,
            )
            python_bin = (
                project_dir
                / ".venv"
                / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
            )
            subprocess.run(
                [str(python_bin), "-m", "pip", "install", "-r", "requirements.txt"],
                cwd=project_dir,
                check=True,
                capture_output=True,
            )
        return True
    except Exception:
        return False


def init_project(
    project_name: str,
    db_driver: str = "sqlite",
    install_deps: bool = False,
    pkg_manager: str = "pip",
) -> None:
    project_dir = Path(project_name.lower())
    if project_dir.exists():
        console.print(f"[bold red]❌ Error:[/bold red] Directory '{project_dir}' already exists.")
        sys.exit(1)

    project_dir.mkdir(parents=True)
    generated_secret = secrets.token_hex(32)

    driver_info = DB_DRIVERS.get(db_driver, DB_DRIVERS["sqlite"])
    env_content = ENV_TEMPLATE.format(project_name=project_name, secret_key=generated_secret)
    if db_driver != "sqlite":
        env_content = env_content.replace(
            "DATABASE_URL=sqlite+aiosqlite:///zcore_dev.db",
            f"DATABASE_URL={driver_info['url']}",
        )

    requirements_content = REQUIREMENTS_TEMPLATE + driver_info["pkg"]

    files_to_create = {
        "main.py": MAIN_PY_TEMPLATE,
        ".env": env_content,
        "requirements.txt": requirements_content,
        ".gitignore": GITIGNORE_TEMPLATE,
    }

    with console.status(
        f"[bold {ZCORE_PRIMARY}]Scaffolding ZCore project in ./{project_name}...[/bold {ZCORE_PRIMARY}]",
        spinner="dots",
    ):
        time.sleep(0.4)
        for filename, content in files_to_create.items():
            create_file(project_dir / filename, content)

        if db_driver == "sqlite":
            (project_dir / "zcore_dev.db").touch()

    if install_deps:
        with console.status(
            f"[bold {ZCORE_PRIMARY}]Setting up .venv & installing dependencies via {pkg_manager}...[/bold {ZCORE_PRIMARY}]",
            spinner="dots",
        ):
            success = setup_virtualenv(project_dir, pkg_manager=pkg_manager)
            status_msg = (
                "[dim]│[/dim]  [bold green]✓[/bold green] Environment & dependencies installed successfully."
                if success
                else "[dim]│[/dim]  [yellow]⚠️  Automated installation failed. Please run pip install manually.[/yellow]"
            )
            console.print(status_msg)

    print_step_footer(f"Project '{project_name}' ready!")

    tree = Tree(f"[bold {ZCORE_PRIMARY}]📁 {project_name}/[/bold {ZCORE_PRIMARY}]")
    tree.add("📄 main.py [dim](FastAPI Lifespan & Kernel)[/dim]")
    tree.add("📄 .env [dim](Configured SECRET_KEY & Database URI)[/dim]")
    tree.add("📄 requirements.txt")
    tree.add("📄 .gitignore")
    if db_driver == "sqlite":
        tree.add("💾 zcore_dev.db [dim](Local dev database)[/dim]")
    if install_deps and (project_dir / ".venv").exists():
        tree.add("📦 .venv/ [dim](Isolated Python Environment)[/dim]")
    console.print(tree)
    console.print()

    console.print("[bold]Next steps:[/bold]")
    console.print(f"  [dim]1.[/dim] [{ZCORE_ACCENT}]cd[/{ZCORE_ACCENT}] {project_name}")
    if not install_deps:
        console.print(
            f"  [dim]2.[/dim] [{ZCORE_ACCENT}]pip install -r requirements.txt[/{ZCORE_ACCENT}]"
        )
    else:
        activate_cmd = (
            ".venv\\Scripts\\activate" if sys.platform == "win32" else "source .venv/bin/activate"
        )
        console.print(f"  [dim]2.[/dim] [{ZCORE_ACCENT}]{activate_cmd}[/{ZCORE_ACCENT}]")
    console.print(f"  [dim]3.[/dim] [{ZCORE_ACCENT}]zc startapp <module_name>[/{ZCORE_ACCENT}]")
    console.print(f"  [dim]4.[/dim] [{ZCORE_ACCENT}]zc run[/{ZCORE_ACCENT}]\n")


def start_app(
    app_name: str,
    target_dir: Path | None = None,
    with_template: bool = True,
    with_test: bool = True,
    selected_components: list[str] | None = None,
    templated_components: list[str] | None = None,
) -> None:
    base_dir = target_dir or Path.cwd()
    app_dir = base_dir / app_name.lower()

    if app_dir.exists():
        console.print(f"[bold red]❌ Error:[/bold red] App folder '{app_dir}' already exists.")
        sys.exit(1)

    app_dir.mkdir(parents=True)

    model_name = "".join(word.capitalize() for word in app_name.split("_"))
    table_name = app_name.lower()

    context = {
        "ModelName": model_name,
        "table_name": table_name,
        "app_name": table_name,
        "project_name": base_dir.name,
    }

    files_to_create = {"__init__.py": ""}

    for comp_key, (filename_pattern, raw_template) in COMPONENT_TEMPLATES.items():
        if selected_components is not None and comp_key not in selected_components:
            continue
        if comp_key == "tests" and not with_test and selected_components is None:
            continue

        resolved_filename = filename_pattern.format(**context)
        use_template = (
            comp_key in templated_components if templated_components is not None else with_template
        )
        files_to_create[resolved_filename] = raw_template.format(**context) if use_template else ""

    with console.status(
        f"[bold {ZCORE_PRIMARY}]Scaffolding ZCore domain module: '{model_name}'...[/bold {ZCORE_PRIMARY}]",
        spinner="dots",
    ):
        time.sleep(0.4)
        for filename, content in files_to_create.items():
            create_file(app_dir / filename, content)

    print_step_footer(f"Modular App '{app_name}' created successfully!")

    tree = Tree(f"[bold {ZCORE_PRIMARY}]📁 {app_name}/[/bold {ZCORE_PRIMARY}]")
    for filename in sorted(files_to_create.keys()):
        tree.add(f"📄 {filename}")
    console.print(tree)
    console.print()

    console.print(
        Panel.fit(
            f"[bold {ZCORE_TEXT}]Register this plugin in your [{ZCORE_ACCENT}]main.py[/{ZCORE_ACCENT}]:[/bold {ZCORE_TEXT}]\n\n"
            f"[dim]from[/dim] [{ZCORE_ACCENT}]{app_name}.plugin[/{ZCORE_ACCENT}] [dim]import[/dim] [bold yellow]{model_name}Plugin[/bold yellow]\n"
            f"[{ZCORE_ACCENT}]kernel.add_plugin[/{ZCORE_ACCENT}]([bold yellow]{model_name}Plugin[/bold yellow]())",
            border_style=ZCORE_PRIMARY,
            title="[bold]Plugin Registration[/bold]",
        )
    )
    console.print()


def _parse_env_file(env_path: Path) -> dict[str, str]:
    """Parse key-value pairs safely from an environment file.

    Args:
        env_path: Target .env filepath.

    Returns:
        Dictionary of environment variable keys and values.
    """
    env_vars: dict[str, str] = {}
    if not env_path.exists():
        return env_vars

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, v = stripped.split("=", 1)
                env_vars[k.strip()] = v.strip().strip("\"'")
    return env_vars


def run_server(
    app: str | None = None,
    host: str | None = None,
    port: int | None = None,
    reload: bool | None = None,
    workers: int | None = None,
    log_level: str | None = None,
    env_file: str | None = None,
    project_dir: Path | None = None,
    extra_args: list[str] | None = None,
) -> None:
    """Launch development or production server using hierarchical configuration precedence.

    Resolves server settings using the cascading hierarchy: CLI Argument > .env File > Defaults.
    Forwards any arbitrary standard Uvicorn parameters transparently.

    Args:
        app: Target application import string (e.g. 'main:app').
        host: Target network interface to bind.
        port: Target TCP port number.
        reload: Flag to enable/disable auto-reload on code modifications.
        workers: Total worker processes count.
        log_level: Logging verbosity level.
        env_file: Custom environment file path.
        project_dir: Project root directory path.
        extra_args: Additional unparsed arguments forwarded directly to Uvicorn.
    """
    work_dir = project_dir or Path.cwd()
    target_env_file = work_dir / (env_file or ".env")
    env_vars = _parse_env_file(target_env_file)

    target_app = app or env_vars.get("APP_MODULE") or env_vars.get("UVICORN_APP") or "main:app"
    target_host = host or env_vars.get("HOST") or "127.0.0.1"

    if port is not None:
        target_port = port
    elif "PORT" in env_vars and env_vars["PORT"].isdigit():
        target_port = int(env_vars["PORT"])
    else:
        target_port = 8000

    if reload is not None:
        target_reload = reload
    elif "RELOAD" in env_vars:
        target_reload = env_vars["RELOAD"].lower() in ("true", "1", "yes")
    elif "DEBUG" in env_vars:
        target_reload = env_vars["DEBUG"].lower() in ("true", "1", "yes")
    else:
        target_reload = True

    if workers is not None:
        target_workers = workers
    elif "WORKERS" in env_vars and env_vars["WORKERS"].isdigit():
        target_workers = int(env_vars["WORKERS"])
    else:
        target_workers = None

    if target_workers and target_workers > 1:
        target_reload = False

    target_log_level = (log_level or env_vars.get("LOG_LEVEL") or "info").lower()

    main_py = work_dir / "main.py"
    if ":" in target_app:
        entry_module = target_app.split(":", 1)[0]
        main_py = work_dir / f"{entry_module}.py"

    if not main_py.exists() and target_app == "main:app":
        console.print(
            f"[bold red]❌ Error:[/bold red] 'main.py' not found in '{work_dir}'. Are you in a ZCore project root?"
        )
        sys.exit(1)

    print_step_header(f"Starting ZCore Server ({work_dir.name})")
    status_info = (
        f"App: {target_app} | Host: {target_host} | Port: {target_port} | Reload: {target_reload}"
    )
    if target_workers:
        status_info += f" | Workers: {target_workers}"
    console.print(f"[dim]│[/dim]  [dim]{status_info}[/dim]")
    console.print("[dim]│[/dim]")
    print_step_footer(
        f"Uvicorn running on [bold underline {ZCORE_ACCENT}]http://{target_host}:{target_port}[/bold underline {ZCORE_ACCENT}] [dim](Press CTRL+C to quit)[/dim]"
    )

    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{work_dir!s}{os.pathsep}{current_pythonpath}" if current_pythonpath else str(work_dir)
    )

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        target_app,
        f"--host={target_host}",
        f"--port={target_port}",
        f"--log-level={target_log_level}",
    ]

    if target_reload:
        cmd.append("--reload")

    if target_workers and target_workers > 1:
        cmd.append(f"--workers={target_workers}")

    if extra_args:
        cmd.extend(extra_args)

    try:
        subprocess.run(
            cmd,
            cwd=work_dir,
            env=env,
            check=True,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]👋 Server stopped cleanly.[/dim]\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[bold red]❌ Server terminated with error code:[/bold red] {e.returncode}")
    except Exception as e:
        console.print(f"[bold red]❌ Failed to run Uvicorn dev server:[/bold red] {e}")


def gen_secret() -> None:
    print_step_header("Generating Cryptographic Secret Key")
    secret = secrets.token_hex(32)
    print_step_footer("Generated 64-character SECRET_KEY:")
    console.print(
        Panel(
            f"[bold {ZCORE_ACCENT}]{secret}[/bold {ZCORE_ACCENT}]",
            border_style=ZCORE_PRIMARY,
        )
    )
    console.print(
        f"[{ZCORE_MUTED}]💡 Paste this into your production .env file under SECRET_KEY[/{ZCORE_MUTED}]\n"
    )


def gen_env(
    output_file: str = ".env.example",
    force: bool = False,
    project_dir: Path | None = None,
) -> None:
    cwd = project_dir or Path.cwd()
    parent_dir = cwd.parent
    module_name = cwd.name

    if module_name.isidentifier():
        if str(parent_dir) not in sys.path:
            sys.path.insert(0, str(parent_dir))
        with contextlib.suppress(Exception):
            importlib.import_module(f"{module_name}.main")

    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))

    subclasses = Settings.__subclasses__()
    settings_class = (
        max(subclasses, key=lambda c: len(c.model_fields))
        if subclasses
        else get_settings().__class__
    )

    out_path = cwd / output_file if not Path(output_file).is_absolute() else Path(output_file)
    if out_path.exists() and not force:
        console.print(
            f"[bold red]❌ Error:[/bold red] Output file '{out_path}' already exists. Use --force to overwrite."
        )
        sys.exit(1)

    env_lines = []
    for field_name, field_info in settings_class.model_fields.items():
        default_val = field_info.default
        if default_val is PydanticUndefined or default_val is None:
            env_lines.append(f"{field_name}=")
        else:
            env_lines.append(f"{field_name}={default_val!s}")

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(env_lines) + "\n")
        print_step_footer(
            f"Created '{out_path.name}' based on '{settings_class.__name__}' configuration fields."
        )
    except Exception as e:
        console.print(f"[bold red]❌ Failed to generate env file:[/bold red] {e}")
        sys.exit(1)
