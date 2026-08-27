import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from zcore.cli import main
from zcore.cli.commands import find_zcore_projects, is_zcore_project


@pytest.fixture
def run_in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "project_name, db_driver",
    [
        ("core_api", "sqlite"),
        ("enterprise_service", "postgres"),
        ("mysql_service", "mysql"),
    ],
)
def test_cli_init_command_non_interactive(
    run_in_tmp_path: Path, project_name: str, db_driver: str
) -> None:
    test_args = ["zc", "init", project_name, "--db", db_driver, "-y"]

    with patch.object(sys, "argv", test_args):
        main()

    project_dir = run_in_tmp_path / project_name
    assert project_dir.is_dir()

    expected_files = ["main.py", ".env", "requirements.txt", ".gitignore"]
    for file in expected_files:
        assert (project_dir / file).is_file()

    env_content = (project_dir / ".env").read_text(encoding="utf-8")
    assert f'PROJECT_NAME="{project_name}"' in env_content

    secret_key_match = re.search(r'SECRET_KEY="([a-f0-9]{64})"', env_content)
    assert secret_key_match is not None

    reqs_content = (project_dir / "requirements.txt").read_text(encoding="utf-8")
    if db_driver == "postgres":
        assert "asyncpg" in reqs_content
    elif db_driver == "mysql":
        assert "aiomysql" in reqs_content


def test_cli_init_collision(run_in_tmp_path: Path) -> None:
    project_name = "collision_project"
    project_dir = run_in_tmp_path / project_name
    project_dir.mkdir()
    test_args = ["zc", "init", project_name, "-y"]

    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


@pytest.mark.parametrize(
    "app_name, with_template, with_test, expected_pascal_name",
    [
        ("payment_gateway", True, True, "PaymentGateway"),
        ("order_management", False, True, "OrderManagement"),
        ("auth_service", True, False, "AuthService"),
    ],
)
def test_cli_startapp_scaffolding(
    run_in_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_name: str,
    with_template: bool,
    with_test: bool,
    expected_pascal_name: str,
) -> None:
    project_name = "test_project"
    init_args = ["zc", "init", project_name, "-y"]
    with patch.object(sys, "argv", init_args):
        main()

    monkeypatch.chdir(run_in_tmp_path / project_name)

    startapp_args = ["zc", "startapp", app_name, "-y"]
    if not with_template:
        startapp_args.append("--no-template")
    if not with_test:
        startapp_args.append("--no-test")

    with patch.object(sys, "argv", startapp_args):
        main()

    app_dir = run_in_tmp_path / project_name / app_name
    assert app_dir.is_dir()

    expected_files = [
        "__init__.py",
        "models.py",
        "schemas.py",
        "repositories.py",
        "services.py",
        "routers.py",
        "plugin.py",
    ]
    for file in expected_files:
        assert (app_dir / file).is_file()

    if with_test:
        assert (app_dir / "tests.py").is_file()
    else:
        assert not (app_dir / "tests.py").exists()

    plugin_content = (app_dir / "plugin.py").read_text(encoding="utf-8")
    models_content = (app_dir / "models.py").read_text(encoding="utf-8")

    if with_template:
        assert f"class {expected_pascal_name}Plugin(Plugin):" in plugin_content
        assert f"class {expected_pascal_name}(Base):" in models_content
        assert f'__tablename__ = "{app_name}"' in models_content
    else:
        assert models_content == ""


def test_cli_startapp_collision(run_in_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_name = "test_project"
    init_args = ["zc", "init", project_name, "-y"]
    with patch.object(sys, "argv", init_args):
        main()

    monkeypatch.chdir(run_in_tmp_path / project_name)
    app_name = "collision_app"
    app_dir = run_in_tmp_path / project_name / app_name
    app_dir.mkdir()

    startapp_args = ["zc", "startapp", app_name, "-y"]
    with patch.object(sys, "argv", startapp_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_version_command() -> None:
    test_args = ["zc", "--version"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0


def test_cli_gensecret(capsys: pytest.CaptureFixture) -> None:
    test_args = ["zc", "gensecret"]
    with patch.object(sys, "argv", test_args):
        main()
    captured = capsys.readouterr()
    secret_match = re.search(r"([a-f0-9]{64})", captured.out)
    assert secret_match is not None


def test_cli_genenv_default(run_in_tmp_path: Path) -> None:
    test_args = ["zc", "genenv"]
    with patch.object(sys, "argv", test_args):
        main()
    assert (run_in_tmp_path / ".env.example").is_file()
    content = (run_in_tmp_path / ".env.example").read_text(encoding="utf-8")
    assert "DATABASE_URL=" in content
    assert "DEBUG=True" in content


def test_cli_genenv_custom_output(run_in_tmp_path: Path) -> None:
    test_args = ["zc", "genenv", "-o", "custom.env"]
    with patch.object(sys, "argv", test_args):
        main()
    assert (run_in_tmp_path / "custom.env").is_file()
    content = (run_in_tmp_path / "custom.env").read_text(encoding="utf-8")
    assert "DATABASE_URL=" in content


def test_cli_genenv_collision_and_force(run_in_tmp_path: Path) -> None:
    existing_file = run_in_tmp_path / ".env.example"
    existing_file.write_text("old_content", encoding="utf-8")
    test_args = ["zc", "genenv"]

    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
    assert existing_file.read_text(encoding="utf-8") == "old_content"

    force_args = ["zc", "genenv", "--force"]
    with patch.object(sys, "argv", force_args):
        main()
    assert existing_file.read_text(encoding="utf-8") != "old_content"
    assert "DATABASE_URL=" in existing_file.read_text(encoding="utf-8")


def test_cli_run_outside_project_root(run_in_tmp_path: Path) -> None:
    test_args = ["zc", "run"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1


def test_cli_run_with_uvicorn_mock(run_in_tmp_path: Path) -> None:
    (run_in_tmp_path / "main.py").touch()
    (run_in_tmp_path / ".env").write_text("HOST=127.0.0.1\nPORT=8080\n", encoding="utf-8")
    test_args = ["zc", "run"]

    with patch("subprocess.run") as mock_run, patch.object(sys, "argv", test_args):
        main()
        mock_run.assert_called_once()
        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert any("uvicorn" in item for item in cmd)
        assert any("main:app" in item for item in cmd)
        assert any("--host=127.0.0.1" in item for item in cmd)
        assert any("--port=8080" in item for item in cmd)
        assert any("--reload" in item for item in cmd)
        assert "PYTHONPATH" in kwargs.get("env", {})


def test_project_detection_helpers(run_in_tmp_path: Path) -> None:
    assert not is_zcore_project(run_in_tmp_path)
    assert find_zcore_projects(run_in_tmp_path) == []

    p1 = run_in_tmp_path / "service_a"
    p1.mkdir()
    (p1 / "main.py").write_text("from zcore import Kernel\n", encoding="utf-8")

    p2 = run_in_tmp_path / "service_b"
    p2.mkdir()
    (p2 / "main.py").write_text("import fastapi\n", encoding="utf-8")

    assert is_zcore_project(p1)
    assert not is_zcore_project(p2)
    assert find_zcore_projects(run_in_tmp_path) == ["service_a"]


def test_interactive_dashboard_cancel(run_in_tmp_path: Path) -> None:
    test_args = ["zc"]
    with patch("questionary.select") as mock_select, patch.object(sys, "argv", test_args):
        mock_select.return_value.ask.return_value = "exit"
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0