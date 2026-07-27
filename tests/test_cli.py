import os
from pathlib import Path
import re
import sys
from unittest.mock import patch
import pytest

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