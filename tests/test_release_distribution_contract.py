from pathlib import Path

PYPROJECT_TEXT = Path("pyproject.toml").read_text()
README_TEXT = Path("README.md").read_text()
SECURITY_TEXT = Path("SECURITY.md").read_text()
SCRIPTS_README_TEXT = Path("scripts/README.md").read_text()
PUBLISH_WORKFLOW_TEXT = Path(".github/workflows/publish.yml").read_text()
DEPLOYMENT_GUIDE_TEXT = Path("docs/deployment.md").read_text()
DEPLOY_SCRIPT_TEXT = Path("scripts/deploy.sh").read_text()
SETUP_INSTANCE_TEXT = Path("scripts/deploy/setup_instance.sh").read_text()
INSTALL_UNITS_TEXT = Path("scripts/deploy/install_units.sh").read_text()


def test_readme_documents_released_cli_installation_via_uv_tool() -> None:
    assert "uv tool install codex-a2a-server" in README_TEXT
    assert "uv tool upgrade codex-a2a-server" in README_TEXT
    assert 'uv tool install "codex-a2a-server==<version>"' in README_TEXT
    assert "Install Released CLI" in README_TEXT
    assert "CODEX_DIRECTORY=/abs/path/to/project" in README_TEXT
    assert "codex-a2a-server deploy" in README_TEXT
    assert "GH_TOKEN" not in README_TEXT
    assert "create a PR from the working branch" in README_TEXT
    assert "merge into `main` after human review" in README_TEXT
    assert "[Compatibility Guide](docs/compatibility.md)" in README_TEXT
    assert "[Contributing Guide](CONTRIBUTING.md)" in README_TEXT
    assert "single-tenant trust boundary" in README_TEXT


def test_publish_workflow_builds_and_smoke_tests_release_artifacts() -> None:
    assert 'tags:\n      - "v*"' in PUBLISH_WORKFLOW_TEXT
    assert "workflow_dispatch" in PUBLISH_WORKFLOW_TEXT
    assert "uv build --no-sources" in PUBLISH_WORKFLOW_TEXT
    assert "bash ./scripts/smoke_test_built_cli.sh" in PUBLISH_WORKFLOW_TEXT
    assert "gh-action-pypi-publish" in PUBLISH_WORKFLOW_TEXT


def test_scripts_index_exposes_built_cli_smoke_test() -> None:
    assert "smoke_test_built_cli.sh" in SCRIPTS_README_TEXT
    assert "`uv tool`" in SCRIPTS_README_TEXT
    assert "codex-a2a-server deploy" in SCRIPTS_README_TEXT
    assert "deploy_light.sh" not in SCRIPTS_README_TEXT
    assert "start_services.sh" not in SCRIPTS_README_TEXT


def test_deployment_guide_uses_published_runtime_and_single_service() -> None:
    assert "codex-a2a@<project>.service" in DEPLOYMENT_GUIDE_TEXT
    assert "codex-a2a-server deploy" in DEPLOYMENT_GUIDE_TEXT
    assert "--package-spec" in DEPLOYMENT_GUIDE_TEXT
    assert "codex-a2a-server==0.1.0" in DEPLOYMENT_GUIDE_TEXT
    assert "codex.auth.env" not in DEPLOYMENT_GUIDE_TEXT
    assert "repo_url" not in DEPLOYMENT_GUIDE_TEXT
    assert ".venv/bin/codex-a2a-server" not in DEPLOYMENT_GUIDE_TEXT
    assert "codex@.service" not in DEPLOYMENT_GUIDE_TEXT


def test_security_policy_declares_single_tenant_boundary() -> None:
    assert "single-tenant trust boundary" in SECURITY_TEXT
    assert "GH_TOKEN" not in SECURITY_TEXT


def test_released_cli_entrypoint_points_to_cli_module() -> None:
    assert 'codex-a2a-server = "codex_a2a_server.cli:main"' in PYPROJECT_TEXT


def test_deploy_scripts_no_longer_require_github_runtime_credentials() -> None:
    assert "GH_TOKEN" not in DEPLOY_SCRIPT_TEXT
    assert "repo_url" not in DEPLOY_SCRIPT_TEXT
    assert "repo_branch" not in DEPLOY_SCRIPT_TEXT
    assert "GH_TOKEN" not in SETUP_INSTANCE_TEXT
    assert "codex.auth.env" not in SETUP_INSTANCE_TEXT
    assert "gh auth login" not in SETUP_INSTANCE_TEXT
    assert "GIT_ASKPASS" not in SETUP_INSTANCE_TEXT
    assert "config/codex.auth.env" not in INSTALL_UNITS_TEXT
