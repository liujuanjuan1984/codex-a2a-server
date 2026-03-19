from pathlib import Path

PYPROJECT_TEXT = Path("pyproject.toml").read_text()
README_TEXT = Path("README.md").read_text()
SECURITY_TEXT = Path("SECURITY.md").read_text()
SCRIPTS_README_TEXT = Path("scripts/README.md").read_text()
PUBLISH_WORKFLOW_TEXT = Path(".github/workflows/publish.yml").read_text()
DEPLOYMENT_GUIDE_TEXT = Path("docs/deployment.md").read_text()
SMOKE_TEST_SCRIPT_TEXT = Path("scripts/smoke_test_built_cli.sh").read_text()
SYNC_CODEX_DOCS_TEXT = Path("scripts/sync_codex_docs.sh").read_text()


def test_readme_documents_released_cli_installation_via_uv_tool() -> None:
    assert "uv tool install codex-a2a-server" in README_TEXT
    assert "uv tool upgrade codex-a2a-server" in README_TEXT
    assert 'uv tool install "codex-a2a-server==<version>"' in README_TEXT
    assert "Install Released CLI" in README_TEXT
    assert "CODEX_WORKSPACE_ROOT=/abs/path/to/workspace" in README_TEXT  # pragma: allowlist secret
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
    assert "Repository-maintainer scripts live here." in SCRIPTS_README_TEXT
    assert "uninstall flows are out of scope" in SCRIPTS_README_TEXT
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
    assert "./scripts/init_system.sh" not in DEPLOYMENT_GUIDE_TEXT
    assert "./scripts/uninstall.sh" not in DEPLOYMENT_GUIDE_TEXT


def test_security_policy_declares_single_tenant_boundary() -> None:
    assert "single-tenant trust boundary" in SECURITY_TEXT
    assert "GH_TOKEN" not in SECURITY_TEXT


def test_released_cli_entrypoint_points_to_cli_module() -> None:
    assert 'codex-a2a-server = "codex_a2a_server.cli:main"' in PYPROJECT_TEXT


def test_deploy_scripts_no_longer_require_github_runtime_credentials() -> None:
    deploy_asset = Path("src/codex_a2a_server/assets/scripts/deploy.sh").read_text()
    setup_asset = Path("src/codex_a2a_server/assets/scripts/deploy/setup_instance.sh").read_text()
    install_units_asset = Path(
        "src/codex_a2a_server/assets/scripts/deploy/install_units.sh"
    ).read_text()

    assert "GH_TOKEN" not in deploy_asset
    assert "repo_url" not in deploy_asset
    assert "repo_branch" not in deploy_asset
    assert "GH_TOKEN" not in setup_asset
    assert "codex.auth.env" not in setup_asset
    assert "gh auth login" not in setup_asset
    assert "GIT_ASKPASS" not in setup_asset
    assert "config/codex.auth.env" not in install_units_asset


def test_repository_removes_redundant_deploy_wrappers() -> None:
    assert not Path("scripts/deploy.sh").exists()
    assert not Path("scripts/deploy").exists()
    assert not Path("scripts/shell_helpers.sh").exists()
    assert not Path("scripts/init_system.sh").exists()
    assert not Path("scripts/uninstall.sh").exists()


def test_repository_wrappers_only_keep_remaining_user_or_maintainer_entrypoints() -> None:
    assert "assets/scripts/smoke_test_built_cli.sh" in SMOKE_TEST_SCRIPT_TEXT
    assert "assets/scripts/sync_codex_docs.sh" in SYNC_CODEX_DOCS_TEXT
