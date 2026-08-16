import re
import sys
import pytest

from pathlib import Path
from unittest.mock import patch

from zcore.cli import main

@pytest.fixture
def run_in_tmp_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path

@pytest.mark.parametrize(
    "project_name",
    [
        "core_api",
        "enterprise_service",
    ]
)
def test_cli_init_command(run_in_tmp_path: Path, project_name: str) -> None:
    test_args = ["zc", "init", project_name]
    
    with patch.object(sys, "argv", test_args):
        main()

    project_dir = run_in_tmp_path / project_name
    assert project_dir.is_dir()

    expected_files = ["main.py", ".env", "requirements.txt", ".gitignore"]
    for file in expected_files:
        assert (project_dir / file).is_file()

    env_content = (project_dir / ".env").read_text()
    assert f'PROJECT_NAME="{project_name}"' in env_content

    secret_key_match = re.search(r'SECRET_KEY="([a-f0-9]{64})"', env_content)
    assert secret_key_match is not None

@pytest.mark.parametrize(
    "app_name, with_template, expected_pascal_name",
    [
        ("payment_gateway", True, "PaymentGateway"),
        ("order_management", False, "OrderManagement"),
    ]
)
def test_cli_startapp_scaffolding(
    run_in_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    app_name: str,
    with_template: bool,
    expected_pascal_name: str
) -> None:
    project_name = "test_project"
    init_args = ["zc", "init", project_name]
    with patch.object(sys, "argv", init_args):
        main()

    monkeypatch.chdir(run_in_tmp_path / project_name)

    startapp_args = ["zc", "startapp", app_name]
    if with_template:
        startapp_args.append("-t")

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

    plugin_content = (app_dir / "plugin.py").read_text()
    assert f"class {expected_pascal_name}Plugin(Plugin):" in plugin_content

    models_content = (app_dir / "models.py").read_text()
    if with_template:
        assert f"class {expected_pascal_name}(Base):" in models_content
        assert f'__tablename__ = "{app_name}"' in models_content
    else:
        assert models_content == ""

@pytest.mark.parametrize(
    "command, invalid_name",
    [
        ("init", "123_invalid_project"),
        ("init", "project-with-hyphen"),
        ("init", "project.domain"),
        ("startapp", "123_invalid_app"),
        ("startapp", "app-with-hyphen"),
        ("startapp", "app.class"),
    ]
)
def test_cli_invalid_module_names(run_in_tmp_path: Path, command: str, invalid_name: str) -> None:
    test_args = ["zc", command, invalid_name]
    
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

def test_cli_version_command(capsys) -> None:
    test_args = ["zc", "--version"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "ZCore Framework - Version 0.1.0-Beta" in captured.out

def test_cli_help_command(capsys) -> None:
    test_args = ["zc"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Available Framework Orchestration Commands" in captured.out

def test_cli_init_collision(run_in_tmp_path: Path) -> None:
    project_name = "collision_project"
    project_dir = run_in_tmp_path / project_name
    project_dir.mkdir()
    test_args = ["zc", "init", project_name]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

def test_cli_startapp_collision(run_in_tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_name = "test_project"
    init_args = ["zc", "init", project_name]
    with patch.object(sys, "argv", init_args):
        main()
    monkeypatch.chdir(run_in_tmp_path / project_name)
    app_name = "collision_app"
    app_dir = run_in_tmp_path / project_name / app_name
    app_dir.mkdir()
    startapp_args = ["zc", "startapp", app_name]
    with patch.object(sys, "argv", startapp_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

@pytest.mark.parametrize("with_template", [True, False])
def test_cli_startapp_with_test(
    run_in_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_template: bool
) -> None:
    project_name = "test_project"
    init_args = ["zc", "init", project_name]
    with patch.object(sys, "argv", init_args):
        main()
    monkeypatch.chdir(run_in_tmp_path / project_name)
    app_name = "test_app"
    startapp_args = ["zc", "startapp", app_name, "--test"]
    if with_template:
        startapp_args.append("-t")
    with patch.object(sys, "argv", startapp_args):
        main()
    app_dir = run_in_tmp_path / project_name / app_name
    assert (app_dir / "tests.py").is_file()
    test_content = (app_dir / "tests.py").read_text()
    if with_template:
        assert "test_get_test_app_list" in test_content
    else:
        assert test_content == ""

def test_cli_genenv_default(run_in_tmp_path: Path) -> None:
    test_args = ["zc", "genenv"]
    with patch.object(sys, "argv", test_args):
        main()
    assert (run_in_tmp_path / ".env.example").is_file()
    content = (run_in_tmp_path / ".env.example").read_text()
    assert "DATABASE_URL=" in content
    assert "DEBUG=True" in content

def test_cli_genenv_custom_output(run_in_tmp_path: Path) -> None:
    test_args = ["zc", "genenv", "-o", "custom.env"]
    with patch.object(sys, "argv", test_args):
        main()
    assert (run_in_tmp_path / "custom.env").is_file()
    content = (run_in_tmp_path / "custom.env").read_text()
    assert "DATABASE_URL=" in content

def test_cli_genenv_collision_and_force(run_in_tmp_path: Path) -> None:
    existing_file = run_in_tmp_path / ".env.example"
    existing_file.write_text("old_content")
    test_args = ["zc", "genenv"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
    assert existing_file.read_text() == "old_content"
    
    force_args = ["zc", "genenv", "--force"]
    with patch.object(sys, "argv", force_args):
        main()
    assert existing_file.read_text() != "old_content"
    assert "DATABASE_URL=" in existing_file.read_text()

def test_cli_run_outside_project_root(run_in_tmp_path: Path) -> None:
    test_args = ["zc", "run"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1

def test_cli_run_with_uvicorn_mock(run_in_tmp_path: Path) -> None:
    (run_in_tmp_path / "main.py").touch()
    (run_in_tmp_path / ".env").write_text("HOST=127.0.0.1\nPORT=8080\n")
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

def test_cli_gensecret(capsys) -> None:
    test_args = ["zc", "gensecret"]
    with patch.object(sys, "argv", test_args):
        main()
    captured = capsys.readouterr()
    assert "Generated Cryptographic Secret Key:" in captured.out
    secret = captured.out.splitlines()[-1].strip()
    assert len(secret) == 64
    assert re.match(r"^[a-f0-9]{64}$", secret) is not None