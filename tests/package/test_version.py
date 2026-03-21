from codex_a2a_server import _resolve_version


def test_resolve_version_prefers_package_metadata(monkeypatch) -> None:
    monkeypatch.setattr("codex_a2a_server._package_version", lambda: "1.2.3")
    monkeypatch.setattr("codex_a2a_server._scm_version", lambda: "1.2.4.dev1+gabc123")

    assert _resolve_version() == "1.2.3"


def test_resolve_version_falls_back_to_scm(monkeypatch) -> None:
    monkeypatch.setattr("codex_a2a_server._package_version", lambda: None)
    monkeypatch.setattr("codex_a2a_server._scm_version", lambda: "1.2.4.dev1+gabc123")

    assert _resolve_version() == "1.2.4.dev1+gabc123"


def test_resolve_version_uses_unknown_when_metadata_missing(monkeypatch) -> None:
    monkeypatch.setattr("codex_a2a_server._package_version", lambda: None)
    monkeypatch.setattr("codex_a2a_server._scm_version", lambda: None)

    assert _resolve_version() == "0.0.0+unknown"
