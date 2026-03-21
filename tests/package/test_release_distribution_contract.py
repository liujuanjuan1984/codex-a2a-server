from pathlib import Path

PYPROJECT_TEXT = Path("pyproject.toml").read_text()
README_TEXT = Path("README.md").read_text()
CONTRIBUTING_TEXT = Path("CONTRIBUTING.md").read_text()
SECURITY_TEXT = Path("SECURITY.md").read_text()
SCRIPTS_README_TEXT = Path("scripts/README.md").read_text()
CI_WORKFLOW_TEXT = Path(".github/workflows/ci.yml").read_text()
PUBLISH_WORKFLOW_TEXT = Path(".github/workflows/publish.yml").read_text()
SMOKE_TEST_SCRIPT_TEXT = Path("scripts/smoke_test_built_cli.sh").read_text()
RUNTIME_MATRIX_SCRIPT_TEXT = Path("scripts/validate_runtime_matrix.sh").read_text()
SYNC_CODEX_DOCS_TEXT = Path("scripts/sync_codex_docs.sh").read_text()


def test_readme_documents_released_cli_installation_via_uv_tool() -> None:
    assert "uv tool install codex-a2a-server" in README_TEXT
    assert "uv tool upgrade codex-a2a-server" in README_TEXT
    assert 'uv tool install "codex-a2a-server==<version>"' in README_TEXT
    assert "Self-start the released CLI against a workspace root:" in README_TEXT
    assert "## Development From Source" not in README_TEXT
    assert "## Development From Source" in CONTRIBUTING_TEXT
    assert (
        "CODEX_WORKSPACE_ROOT=/abs/path/to/workspace uv run codex-a2a-server" in CONTRIBUTING_TEXT
    )
    assert "http://127.0.0.1:8000/.well-known/agent-card.json" in CONTRIBUTING_TEXT
    assert "Install and verify the local `codex` CLI itself." in README_TEXT
    assert "does not provision Codex providers, login state, or API keys for you" in README_TEXT
    assert "Startup fails fast if the local `codex` runtime is missing" in README_TEXT
    assert "CODEX_WORKSPACE_ROOT=/abs/path/to/workspace" in README_TEXT  # pragma: allowlist secret
    assert "codex-a2a-server deploy" not in README_TEXT
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


def test_ci_workflow_deduplicates_full_gate_and_runtime_matrix() -> None:
    assert "quality-gate:" in CI_WORKFLOW_TEXT
    assert 'python-version: "3.13"' in CI_WORKFLOW_TEXT
    assert "bash ./scripts/validate_baseline.sh" in CI_WORKFLOW_TEXT
    assert "runtime-matrix:" in CI_WORKFLOW_TEXT
    assert 'python-version: ["3.11", "3.12"]' in CI_WORKFLOW_TEXT
    assert "bash ./scripts/validate_runtime_matrix.sh" in CI_WORKFLOW_TEXT


def test_scripts_index_exposes_built_cli_smoke_test() -> None:
    assert "validate_runtime_matrix.sh" in SCRIPTS_README_TEXT
    assert "smoke_test_built_cli.sh" in SCRIPTS_README_TEXT
    assert "`uv tool`" in SCRIPTS_README_TEXT
    assert "runtime entrypoints live in the released `codex-a2a-server` CLI" in SCRIPTS_README_TEXT
    assert "Repository-maintainer scripts live here." in SCRIPTS_README_TEXT
    assert "deploy_light.sh" not in SCRIPTS_README_TEXT
    assert "start_services.sh" not in SCRIPTS_README_TEXT


def test_runtime_docs_no_longer_publish_deployment_guide() -> None:
    assert not Path("docs/deployment.md").exists()
    assert "[Deployment Guide](docs/deployment.md)" not in README_TEXT


def test_security_policy_declares_single_tenant_boundary() -> None:
    assert "single-tenant trust boundary" in SECURITY_TEXT
    assert "GH_TOKEN" not in SECURITY_TEXT


def test_released_cli_entrypoint_points_to_cli_module() -> None:
    assert 'codex-a2a-server = "codex_a2a_server.cli:main"' in PYPROJECT_TEXT
    assert "[tool.setuptools.package-data]" not in PYPROJECT_TEXT


def test_repository_no_longer_ships_deploy_assets() -> None:
    assert not Path("src/codex_a2a_server/assets").exists()


def test_repository_removes_redundant_deploy_wrappers() -> None:
    assert not Path("scripts/deploy.sh").exists()
    assert not Path("scripts/deploy").exists()
    assert not Path("scripts/shell_helpers.sh").exists()
    assert not Path("scripts/init_system.sh").exists()
    assert not Path("scripts/uninstall.sh").exists()


def test_repository_wrappers_only_keep_remaining_user_or_maintainer_entrypoints() -> None:
    assert "uv tool install" in SMOKE_TEST_SCRIPT_TEXT
    assert '--python "${python_bin}"' in SMOKE_TEST_SCRIPT_TEXT
    assert "--python 3.13" not in SMOKE_TEST_SCRIPT_TEXT
    assert "uv run pytest --no-cov" in RUNTIME_MATRIX_SCRIPT_TEXT
    assert 'CODEX_CLI_BIN="${fake_codex_bin}"' in SMOKE_TEST_SCRIPT_TEXT
    assert 'cat >"${fake_codex_bin}"' in SMOKE_TEST_SCRIPT_TEXT
    assert "git clone --depth 1 https://github.com/openai/codex.git" in SYNC_CODEX_DOCS_TEXT
