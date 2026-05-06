from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from a_inf import dashboard


class Args:
    args: list[str] = []
    recipe: str | None = None
    folder: str | None = None
    tag: str | None = None
    view: str | None = None
    name: str | None = None
    title: str | None = None
    limit: int | None = None
    json = False
    dry_run = False
    no_log = True
    print_prompt = False
    no_codex = False
    codex_bin = "codex"
    sandbox = "workspace-write"
    add_dir: list[str] = []


def init_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "_meta").mkdir(parents=True)
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    return vault


def test_recipe_renders_current_bases_template() -> None:
    spec = dashboard.build_recipe_spec(
        "content-index",
        view="table",
        name=None,
        title=None,
        folder=None,
        tag=None,
        limit=None,
    )
    validated = dashboard.validate_spec(spec, Path("/tmp/vault"))
    yaml_text = dashboard.render_base_yaml(validated["base"])

    assert "views:" in yaml_text
    assert "order:" in yaml_text
    assert "columns:" not in yaml_text
    assert "sort:" not in yaml_text
    assert '- "file.name"' in yaml_text
    assert '- "summary"' in yaml_text


def test_recipe_overrides_name_title_filters_view_and_limit() -> None:
    spec = dashboard.build_recipe_spec(
        "entities",
        view="cards",
        name="Important Entities",
        title="Important Entities",
        folder="entities",
        tag="tool",
        limit=5,
    )
    validated = dashboard.validate_spec(spec, Path("/tmp/vault"))
    view = validated["base"]["views"][0]
    yaml_text = dashboard.render_base_yaml(validated["base"])

    assert validated["path"] == "_meta/important-entities.base"
    assert view["type"] == "cards"
    assert view["name"] == "Important Entities"
    assert view["limit"] == 5
    assert 'file.inFolder("entities")' in yaml_text
    assert 'file.hasTag("tool")' in yaml_text


def test_validation_rejects_legacy_schema_and_unsafe_paths() -> None:
    base_spec = {
        "version": 1,
        "name": "bad",
        "title": "Bad",
        "path": "_meta/bad.base",
        "filter_description": "bad",
        "base": {"views": [{"type": "table", "name": "Bad", "order": ["file.name"]}]},
    }
    for spec, message in [
        ({**base_spec, "base": {"columns": [], "views": []}}, "legacy base keys"),
        ({**base_spec, "path": "../bad.base"}, "path must be _meta"),
        ({**base_spec, "base": {"views": [{"type": "grid", "name": "Bad", "order": ["file.name"]}]}}, "invalid view type"),
        ({**base_spec, "base": {"views": [{"type": "table", "name": "Bad", "columns": []}]}}, "legacy view keys"),
        ({**base_spec, "base": {"filters": {"xor": []}, "views": [{"type": "table", "name": "Bad", "order": ["file.name"]}]}}, "one of and/or/not"),
    ]:
        try:
            dashboard.validate_spec(spec, Path("/tmp/vault"))
        except dashboard.DashboardError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("expected DashboardError")


def test_unknown_recipe_rejected() -> None:
    try:
        dashboard.infer_recipe("unknown", [])
    except dashboard.DashboardError as exc:
        assert "unknown dashboard recipe" in str(exc)
    else:
        raise AssertionError("expected DashboardError")


def test_dry_run_json_does_not_write_base_or_log(tmp_path: Path, capsys, monkeypatch) -> None:
    vault = init_vault(tmp_path)
    monkeypatch.setattr(dashboard, "ensure_qmd_collection", lambda *_args: True)
    monkeypatch.setattr(dashboard, "sync_qmd", lambda *_args: True)

    class DryRunArgs(Args):
        recipe = "projects"
        json = True
        dry_run = True
        no_log = False

    result = dashboard.run_dashboard(DryRunArgs(), vault, {})
    output = json.loads(capsys.readouterr().out)

    assert result == 0
    assert output["status"] == "planned"
    assert output["path"] == "_meta/projects-overview.base"
    assert not (vault / "_meta" / "projects-overview.base").exists()
    assert not (vault / "log.md").exists()


def test_apply_writes_base_and_log_and_syncs(tmp_path: Path, monkeypatch) -> None:
    vault = init_vault(tmp_path)
    sync_calls: list[Path] = []
    monkeypatch.setattr(dashboard, "ensure_qmd_collection", lambda vault_arg, _config: True)
    monkeypatch.setattr(dashboard, "sync_qmd", lambda vault_arg, _config: sync_calls.append(vault_arg) or True)

    class ApplyArgs(Args):
        recipe = "research"
        no_log = False

    result = dashboard.run_dashboard(ApplyArgs(), vault, {})

    assert result == 0
    assert (vault / "_meta" / "research-tracker.base").is_file()
    assert 'file.hasTag("research")' in (vault / "_meta" / "research-tracker.base").read_text(encoding="utf-8")
    assert 'WIKI_DASHBOARD name="research-tracker" view=table' in (vault / "log.md").read_text(encoding="utf-8")
    assert sync_calls == [vault]


def test_hybrid_codex_spec_is_validated_and_applied(tmp_path: Path, monkeypatch) -> None:
    vault = init_vault(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.chdir(vault)
    monkeypatch.setattr(dashboard.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(dashboard, "ensure_qmd_collection", lambda *_args: True)
    monkeypatch.setattr(dashboard, "sync_qmd", lambda *_args: True)

    def fake_call(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> int:
        calls.append(command)
        match = re.search(r"Write exactly one JSON object to this path: (.+)", command[-1])
        assert match is not None
        spec_path = Path(match.group(1))
        spec_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": "custom-tools",
                    "title": "Custom Tools",
                    "path": "_meta/custom-tools.base",
                    "filter_description": 'tag="#tool"',
                    "base": {
                        "filters": {"and": ['file.hasTag("tool")']},
                        "properties": {"file.name": {"displayName": "Page"}},
                        "views": [{"type": "table", "name": "Custom Tools", "order": ["file.name"]}],
                    },
                }
            ),
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)

    class HybridArgs(Args):
        args = ["make", "me", "a", "dashboard", "for", "tooling"]

    result = dashboard.run_dashboard(HybridArgs(), vault, {})

    assert result == 0
    assert calls
    assert (vault / "_meta" / "custom-tools.base").is_file()
    assert 'file.hasTag("tool")' in (vault / "_meta" / "custom-tools.base").read_text(encoding="utf-8")
