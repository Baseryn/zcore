import argparse
import shutil
import sys
from pathlib import Path

import questionary
from questionary import Choice

from zcore.cli.commands import (
    DB_DRIVERS,
    find_zcore_projects,
    gen_env,
    gen_secret,
    init_project,
    is_zcore_project,
    run_server,
    start_app,
)
from zcore.cli.ui import (
    ZCORE_ACCENT,
    ZCORE_PRIMARY,
    console,
    custom_style,
    print_banner,
    print_cancelled,
    print_step_header,
)

LAYER_CHOICES = [
    Choice("SQLAlchemy 2.0 Model (models.py)", value="models"),
    Choice("Zchema Pydantic V2 Schemas (schemas.py)", value="schemas"),
    Choice("Base Repository Layer (repositories.py)", value="repositories"),
    Choice("Base Service Layer (services.py)", value="services"),
    Choice("FastAPI Base Router (routers.py)", value="routers"),
    Choice("Modular Lifecycle Plugin (plugin.py)", value="plugin"),
    Choice("Pytest Async Test Suite (tests.py)", value="tests"),
]


def resolve_target_project(subprojects: list[str], prompt_msg: str) -> Path | None:
    print_step_header(prompt_msg)
    chosen = questionary.select(
        "Select target project:",
        choices=[Choice(p, value=p) for p in subprojects],
        style=custom_style,
    ).ask()
    return Path(chosen) if chosen else None


def prompt_init_interactive(project_name: str | None = None, db_driver: str | None = None) -> None:
    print_step_header("Initialize New ZCore Project")

    if not project_name:
        project_name = questionary.text("Project directory name:", default="core_api", style=custom_style).ask()
        if not project_name:
            print_cancelled()
            return
        console.print("[dim]│[/dim]")

    if not db_driver:
        db_driver = questionary.select(
            "Select primary database driver:",
            choices=[
                Choice("SQLite (aiosqlite) — Lightweight Dev", value="sqlite"),
                Choice("PostgreSQL (asyncpg) — Production Ready", value="postgres"),
                Choice("MySQL (aiomysql) — Async Driver", value="mysql"),
            ],
            style=custom_style,
        ).ask()
        if not db_driver:
            print_cancelled()
            return
        console.print("[dim]│[/dim]")

    install_deps = questionary.select(
        "Initialize virtual environment (.venv) and install dependencies?",
        choices=[
            Choice("Yes — Create .venv and install packages now", value=True),
            Choice("No  — Skip, I will configure it manually later", value=False),
        ],
        style=custom_style,
    ).ask()

    pkg_manager = "pip"
    if install_deps:
        console.print("[dim]│[/dim]")
        choices = []
        if shutil.which("uv") is not None:
            choices.append(Choice("uv (Ultra-fast package manager) [Recommended]", value="uv"))
        choices.append(Choice("pip (Standard Python package installer)", value="pip"))

        pkg_manager = questionary.select(
            "Select package installer:",
            choices=choices,
            style=custom_style,
        ).ask()

    init_project(project_name, db_driver=db_driver, install_deps=bool(install_deps), pkg_manager=pkg_manager or "pip")


def prompt_startapp_interactive(app_name: str | None = None, target_dir: Path | None = None) -> None:
    print_step_header("Scaffold ZCore Domain App")

    if not app_name:
        app_name = questionary.text("Domain App / Module name (snake_case):", default="order_management", style=custom_style).ask()
        if not app_name:
            print_cancelled()
            return
        console.print("[dim]│[/dim]")

    scaffold_mode = questionary.select(
        "How would you like to scaffold the domain app?",
        choices=[
            Choice("1. Full Boilerplate  — Populate all layers with ZCore templates", value="template"),
            Choice("2. Clean / Blank     — Create clean, empty files for all layers", value="blank"),
            Choice("3. Custom / Mixed    — Select which layers have templates (others as blank files)", value="custom"),
        ],
        style=custom_style,
    ).ask()

    if not scaffold_mode:
        print_cancelled()
        return

    console.print("[dim]│[/dim]")

    if scaffold_mode in ("template", "blank"):
        selected = questionary.checkbox(
            "Select architectural layers to generate:",
            choices=[Choice(c.title, value=c.value, checked=True) for c in LAYER_CHOICES],
            style=custom_style,
        ).ask()
        if not selected:
            console.print("[dim]└[/dim]  [red]Cancelled (no layers selected).[/red]\n")
            return
        start_app(app_name, target_dir=target_dir, with_template=(scaffold_mode == "template"), selected_components=selected)

    elif scaffold_mode == "custom":
        templated = questionary.checkbox(
            "Select layers to populate with boilerplate templates (unchecked will be blank files):",
            choices=[Choice(c.title, value=c.value, checked=False) for c in LAYER_CHOICES],
            style=custom_style,
        ).ask()
        if templated is None:
            print_cancelled()
            return
        start_app(app_name, target_dir=target_dir, templated_components=templated)


def interactive_dashboard() -> None:
    print_banner()

    is_inside = is_zcore_project()
    subprojects = find_zcore_projects()

    if is_inside:
        current_name = Path.cwd().name
        console.print(f"  [dim]Context:[/dim] [bold {ZCORE_PRIMARY}]Active Project ({current_name})[/bold {ZCORE_PRIMARY}]\n")
        choices = [
            Choice("🧩  startapp   — Scaffold a modular domain app / plugin", value="startapp"),
            Choice("⚡  run        — Launch Uvicorn development server", value="run"),
            Choice("📋  genenv     — Generate template .env from Settings class", value="genenv"),
            Choice("🔑  gensecret  — Generate cryptographically secure SECRET_KEY", value="gensecret"),
            Choice("📦  init       — Scaffold another ZCore project", value="init"),
            Choice("🚪  exit       — Exit CLI", value="exit"),
        ]
    elif subprojects:
        console.print(f"  [dim]Context:[/dim] [bold {ZCORE_ACCENT}]Workspace with {len(subprojects)} detected project(s): {', '.join(subprojects)}[/bold {ZCORE_ACCENT}]\n")
        choices = [
            Choice("⚡  run        — Launch a project dev server", value="run"),
            Choice("🧩  startapp   — Scaffold a domain app inside a project", value="startapp"),
            Choice("📋  genenv     — Generate .env for a project", value="genenv"),
            Choice("📦  init       — Scaffold a new ZCore project", value="init"),
            Choice("🔑  gensecret  — Generate cryptographically secure SECRET_KEY", value="gensecret"),
            Choice("🚪  exit       — Exit CLI", value="exit"),
        ]
    else:
        choices = [
            Choice("📦  init       — Scaffold a new full ZCore project", value="init"),
            Choice("🔑  gensecret  — Generate cryptographically secure SECRET_KEY", value="gensecret"),
            Choice("🚪  exit       — Exit CLI", value="exit"),
        ]

    action = questionary.select(
        "What framework task would you like to perform?",
        choices=choices,
        style=custom_style,
    ).ask()

    if action == "init":
        prompt_init_interactive()
    elif action == "startapp":
        target_dir = resolve_target_project(subprojects, "Select project to add domain module") if not is_inside and subprojects else None
        if is_inside or target_dir or not subprojects:
            prompt_startapp_interactive(target_dir=target_dir)
    elif action == "run":
        target_dir = resolve_target_project(subprojects, "Select project to launch server") if not is_inside and subprojects else None
        if is_inside or target_dir or not subprojects:
            run_server(project_dir=target_dir)
    elif action == "gensecret":
        gen_secret()
    elif action == "genenv":
        target_dir = resolve_target_project(subprojects, "Select project to generate .env") if not is_inside and subprojects else None
        if is_inside or target_dir or not subprojects:
            print_step_header("Generate Configuration Schema (.env.example)")
            filename = questionary.text("Target output filename:", default=".env.example", style=custom_style).ask()
            if filename:
                gen_env(output_file=filename, force=True, project_dir=target_dir)
    elif action == "exit" or action is None:
        console.print("\n[dim]👋 Cancelled.[/dim]\n")
        sys.exit(0)


def main() -> None:
    try:
        parser = argparse.ArgumentParser(description="ZCore CLI Tool", prog="zc")
        parser.add_argument("--version", action="store_true", help="Show the active ZCore Framework version")

        subparsers = parser.add_subparsers(dest="command", help="Available Framework Orchestration Commands")

        init_parser = subparsers.add_parser("init", help="Initializes a new ZCore project structure")
        init_parser.add_argument("name", nargs="?", type=str, default=None, help="Name of your project directory")
        init_parser.add_argument("--db", type=str, default=None, choices=list(DB_DRIVERS.keys()), help="Target database driver")
        init_parser.add_argument("-y", "--yes", action="store_true", help="Skip interactive prompts and use defaults")

        startapp_parser = subparsers.add_parser("startapp", help="Creates a modular ZCore app inside the project")
        startapp_parser.add_argument("name", nargs="?", type=str, default=None, help="Name of the app/module")
        startapp_parser.add_argument("--template", action=argparse.BooleanOptionalAction, default=True, help="Populate with boilerplate templates")
        startapp_parser.add_argument("--test", action=argparse.BooleanOptionalAction, default=True, help="Generate test suite")
        startapp_parser.add_argument("-y", "--yes", action="store_true", help="Generate all standard architectural layers automatically")

        subparsers.add_parser("run", help="Launches the local Uvicorn development server")
        subparsers.add_parser("gensecret", help="Generates a cryptographically secure 64-character SECRET_KEY")

        genenv_parser = subparsers.add_parser("genenv", help="Generates template .env based on Settings")
        genenv_parser.add_argument("-o", "--output", type=str, default=".env.example", help="Target output filepath")
        genenv_parser.add_argument("-f", "--force", action="store_true", help="Overwrite existing file")

        args = parser.parse_args()

        if args.version:
            console.print(f"[bold {ZCORE_PRIMARY}]ZCore Framework[/bold {ZCORE_PRIMARY}] - Version [bold white]0.1.0-Beta[/bold white]")
            sys.exit(0)

        if args.command == "init":
            if args.yes:
                init_project(args.name or "core_api", db_driver=args.db or "sqlite", install_deps=False)
            else:
                prompt_init_interactive(project_name=args.name, db_driver=args.db)

        elif args.command == "startapp":
            if args.yes and args.name:
                start_app(args.name, with_template=args.template, with_test=args.test)
            else:
                prompt_startapp_interactive(app_name=args.name)

        elif args.command == "run":
            run_server()
        elif args.command == "gensecret":
            gen_secret()
        elif args.command == "genenv":
            gen_env(args.output, args.force)
        else:
            interactive_dashboard()

    except KeyboardInterrupt:
        console.print("\n[dim]👋 Operation cancelled by user.[/dim]\n")
        sys.exit(0)


if __name__ == "__main__":
    main()