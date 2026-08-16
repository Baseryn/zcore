import argparse
import secrets
import sys

from zcore.cli.commands import gen_env, init_project, run_server, start_app


def main() -> None:
    """
    The main entry point for the ZCore CLI.
    This function is bound to the 'zc' shell command.
    """
    parser = argparse.ArgumentParser(description="ZCore CLI Tool", prog="zc")

    parser.add_argument(
        "--version", action="store_true", help="Show the active ZCore Framework version"
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available Framework Orchestration Commands"
    )

    init_parser = subparsers.add_parser(
        "init", help="Initializes a new ZCore project structure"
    )
    init_parser.add_argument(
        "name", type=str, help="Name of your project directory (e.g., core_api)"
    )

    startapp_parser = subparsers.add_parser(
        "startapp",
        help="Creates a modular ZCore app (domain) inside the current project",
    )
    startapp_parser.add_argument(
        "name", type=str, help="Name of the app/module (e.g., payment_gateway)"
    )

    startapp_parser.add_argument(
        "-t",
        "--template",
        action="store_true",
        help="Generate files populated with ZCore boilerplate templates",
    )

    startapp_parser.add_argument(
        "--test", action="store_true", help="Generate test files for the app"
    )

    subparsers.add_parser("run", help="Launches the local Uvicorn development server")

    subparsers.add_parser(
        "gensecret",
        help="Generates a cryptographically secure 64-character hex string for production SECRET_KEY",
    )

    genenv_parser = subparsers.add_parser(
        "genenv",
        help="Generates a template .env file based on the active Settings class fields",
    )
    genenv_parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=".env.example",
        help="Target output filepath (default: .env.example)",
    )
    genenv_parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Overwrite the file if it already exists",
    )

    args = parser.parse_args()

    if args.version:
        print("ZCore Framework - Version 0.1.0-Beta")
        sys.exit(0)

    if args.command == "init":
        project_name = args.name
        if not project_name.isidentifier():
            print(
                f"❌ Error: '{project_name}' is not a valid directory name in Python."
            )
            sys.exit(1)
        init_project(project_name)

    elif args.command == "startapp":
        app_name = args.name
        if not app_name.isidentifier():
            print(f"❌ Error: '{app_name}' is not a valid Python module name.")
            print("👉 Recommendation: Use snake_case names (e.g. order_management)")
            sys.exit(1)
        start_app(app_name, with_template=args.template, with_test=args.test)

    elif args.command == "run":
        run_server()

    elif args.command == "gensecret":
        print("🔑 Generated Cryptographic Secret Key:")
        print(secrets.token_hex(32))

    elif args.command == "genenv":
        gen_env(args.output, args.force)

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
