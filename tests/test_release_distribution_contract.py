from pathlib import Path

README_TEXT = Path("README.md").read_text()
SCRIPTS_README_TEXT = Path("scripts/README.md").read_text()
PUBLISH_WORKFLOW_TEXT = Path(".github/workflows/publish.yml").read_text()
DEPLOYMENT_GUIDE_TEXT = Path("docs/deployment.md").read_text()


def test_readme_documents_released_cli_installation_via_uv_tool() -> None:
    assert "uv tool install codex-a2a-server" in README_TEXT
    assert "uv tool upgrade codex-a2a-server" in README_TEXT
    assert 'uv tool install "codex-a2a-server==<version>"' in README_TEXT
    assert "Install Released CLI" in README_TEXT
    assert "CODEX_DIRECTORY=/abs/path/to/project" in README_TEXT
    assert "create a PR from the working branch" in README_TEXT
    assert "merge into `main` after human review" in README_TEXT


def test_publish_workflow_builds_and_smoke_tests_release_artifacts() -> None:
    assert 'tags:\n      - "v*"' in PUBLISH_WORKFLOW_TEXT
    assert "workflow_dispatch" in PUBLISH_WORKFLOW_TEXT
    assert "uv build --no-sources" in PUBLISH_WORKFLOW_TEXT
    assert "bash ./scripts/smoke_test_built_cli.sh" in PUBLISH_WORKFLOW_TEXT
    assert "gh-action-pypi-publish" in PUBLISH_WORKFLOW_TEXT


def test_scripts_index_exposes_built_cli_smoke_test() -> None:
    assert "smoke_test_built_cli.sh" in SCRIPTS_README_TEXT
    assert "`uv tool`" in SCRIPTS_README_TEXT
    assert "deploy_light.sh" not in SCRIPTS_README_TEXT
    assert "start_services.sh" not in SCRIPTS_README_TEXT


def test_deployment_guide_uses_published_runtime_and_single_service() -> None:
    assert "codex-a2a@<project>.service" in DEPLOYMENT_GUIDE_TEXT
    assert "package_spec" in DEPLOYMENT_GUIDE_TEXT
    assert "codex-a2a-server==0.1.0" in DEPLOYMENT_GUIDE_TEXT
    assert ".venv/bin/codex-a2a-server" not in DEPLOYMENT_GUIDE_TEXT
    assert "codex@.service" not in DEPLOYMENT_GUIDE_TEXT
