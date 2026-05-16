from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from a_inf import colorize


class Args:
    mode: str | None = None
    args: list[str] = []
    groups_json: str | None = None
    json = False
    no_log = True
    print_prompt = False
    sandbox = "workspace-write"


def init_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / ".manifest.json").write_text('{"version": 1}\n', encoding="utf-8")
    return vault


def write_page(vault: Path, rel: str, tags: list[str]) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"title: {path.stem}",
                f"category: {rel.split('/', 1)[0]}",
                f"tags: [{', '.join(tags)}]",
                "sources: []",
                "created: 2026-01-01T00:00:00+00:00",
                "updated: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                f"# {path.stem}",
            ]
        ),
        encoding="utf-8",
    )


def rgb(hex_value: str) -> int:
    return int(hex_value.lstrip("#"), 16)


def test_by_tag_ranks_counts_ignores_visibility_and_ties_by_tag(tmp_path: Path) -> None:
    vault = init_vault(tmp_path)
    write_page(vault, "concepts/a.md", ["beta", "alpha", "visibility/internal"])
    write_page(vault, "concepts/b.md", ["alpha", "gamma"])
    write_page(vault, "concepts/c.md", ["beta", "gamma"])

    groups = colorize.tag_groups(vault, colorize.BUILTIN_PALETTE)

    assert [group["query"] for group in groups] == ["tag:#alpha", "tag:#beta", "tag:#gamma"]
    assert groups[0]["color"]["rgb"] == rgb("#4E79A7")
    assert all("visibility" not in group["query"] for group in groups)


def test_by_category_uses_fixed_folder_order_for_existing_markdown(tmp_path: Path) -> None:
    vault = init_vault(tmp_path)
    write_page(vault, "references/ref.md", ["api"])
    write_page(vault, "concepts/concept.md", ["idea"])
    write_page(vault, "entities/tool.md", ["tool"])

    groups = colorize.category_groups(vault, colorize.BUILTIN_PALETTE)

    assert [group["query"] for group in groups] == ['path:"concepts"', 'path:"entities"', 'path:"references"']
    assert [group["color"]["rgb"] for group in groups] == [rgb("#4E79A7"), rgb("#F28E2B"), rgb("#76B7B2")]


def test_visibility_and_combined_ordering(tmp_path: Path) -> None:
    vault = init_vault(tmp_path)
    write_page(vault, "concepts/a.md", ["ml", "visibility/pii"])

    visibility = colorize.build_color_groups("by-visibility", vault, colorize.BUILTIN_PALETTE, None)
    combined = colorize.build_color_groups("combined", vault, colorize.BUILTIN_PALETTE, None)

    assert [group["query"] for group in visibility] == [
        "tag:#visibility/pii",
        "tag:#visibility/internal",
        "tag:#visibility/public",
    ]
    assert [group["query"] for group in combined] == [
        "tag:#visibility/pii",
        "tag:#visibility/internal",
        "tag:#visibility/public",
        "tag:#ml",
    ]


def test_custom_groups_accept_json_object_named_and_hex_colors() -> None:
    raw = json.dumps({"tag:#ml": "blue", 'path:"concepts"': "#3366FF"})

    groups = colorize.custom_groups(raw, colorize.BUILTIN_PALETTE)

    assert groups == [
        {"query": "tag:#ml", "color": {"a": 1, "rgb": rgb("#4E79A7")}},
        {"query": 'path:"concepts"', "color": {"a": 1, "rgb": rgb("#3366FF")}},
    ]


def test_custom_groups_accept_json_array() -> None:
    raw = json.dumps([{"query": "tag:#ml", "color": "orange"}])

    groups = colorize.custom_groups(raw, colorize.BUILTIN_PALETTE)

    assert groups == [{"query": "tag:#ml", "color": {"a": 1, "rgb": rgb("#F28E2B")}}]


def test_custom_groups_reject_invalid_inputs() -> None:
    for raw, message in [
        (None, "--groups-json is required"),
        (json.dumps({"tag:#ml": "not-a-color"}), "unknown color"),
        (json.dumps({"tag:#ml": "#123"}), "invalid hex"),
    ]:
        try:
            colorize.custom_groups(raw, colorize.BUILTIN_PALETTE)
        except colorize.ColorizeError as exc:
            assert message in str(exc)
        else:
            raise AssertionError("expected ColorizeError")


def test_palette_config_overrides_and_adds_named_colors(tmp_path: Path) -> None:
    vault = init_vault(tmp_path)
    (vault / ".a-inf").mkdir()
    (vault / ".a-inf" / "config.toml").write_text(
        "\n".join(
            [
                'vault_path = "unused"',
                "[graph_colorize.palette]",
                'blue = "#000001"',
                'accent = "#3366FF"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    palette = colorize.load_palette(vault)

    assert colorize.color_value("blue", palette)["rgb"] == 1
    assert colorize.color_value("accent", palette)["rgb"] == rgb("#3366FF")


def test_apply_preserves_graph_settings_replaces_color_groups_and_reuses_backup(
    tmp_path: Path, monkeypatch
) -> None:
    vault = init_vault(tmp_path)
    write_page(vault, "concepts/a.md", ["ml"])
    graph_path = vault / ".obsidian" / "graph.json"
    graph_path.write_text(
        json.dumps({"search": "path:concepts", "scale": 2, "colorGroups": [{"query": "old"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(colorize, "ensure_qmd_collection", lambda *_args: True)
    monkeypatch.setattr(colorize, "sync_qmd", lambda *_args: True)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 5, 6, 12, 34, tzinfo=timezone.utc)

    monkeypatch.setattr(colorize, "datetime", FrozenDateTime)

    class ColorArgs(Args):
        no_log = False

    first = colorize.apply_colorize(ColorArgs(), vault, {})
    second = colorize.apply_colorize(ColorArgs(), vault, {})
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    backups = list((vault / ".obsidian").glob("graph.json.backup-*"))

    assert graph["search"] == "path:concepts"
    assert graph["scale"] == 2
    assert graph["colorGroups"][0]["query"] == "tag:#ml"
    assert len(backups) == 1
    assert first["backup"] == ".obsidian/graph.json.backup-20260506-1234"
    assert second["backup"] == ".obsidian/graph.json.backup-20260506-1234"
    assert "GRAPH_COLORIZE mode=by-tag groups=1 backup=graph.json.backup-20260506-1234" in (
        vault / "log.md"
    ).read_text(encoding="utf-8")


def test_clear_and_undo(tmp_path: Path, monkeypatch) -> None:
    vault = init_vault(tmp_path)
    graph_path = vault / ".obsidian" / "graph.json"
    graph_path.write_text(json.dumps({"search": "", "colorGroups": [{"query": "tag:#old"}]}), encoding="utf-8")
    monkeypatch.setattr(colorize, "ensure_qmd_collection", lambda *_args: True)
    monkeypatch.setattr(colorize, "sync_qmd", lambda *_args: True)

    class ClearArgs(Args):
        mode = "clear"

    clear_report = colorize.apply_colorize(ClearArgs(), vault, {})
    assert clear_report["groups"] == 0
    assert json.loads(graph_path.read_text(encoding="utf-8"))["colorGroups"] == []

    class UndoArgs(Args):
        mode = "undo"

    undo_report = colorize.apply_colorize(UndoArgs(), vault, {})
    restored = json.loads(graph_path.read_text(encoding="utf-8"))
    assert restored["colorGroups"] == [{"query": "tag:#old"}]
    assert undo_report["backup"].startswith(".obsidian/graph.json.backup-")


def test_missing_obsidian_returns_error_without_creating_directory(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()

    result = colorize.run_colorize(Args(), vault, {})

    assert result == 1
    assert not (vault / ".obsidian").exists()
    assert ".obsidian/ does not exist" in capsys.readouterr().err


def test_no_log_skips_log_and_qmd_sync(tmp_path: Path, monkeypatch) -> None:
    vault = init_vault(tmp_path)
    write_page(vault, "concepts/a.md", ["ml"])
    monkeypatch.setattr(colorize, "ensure_qmd_collection", lambda *_args: (_ for _ in ()).throw(AssertionError("qmd")))

    report = colorize.apply_colorize(Args(), vault, {})

    assert report["logged"] is False
    assert not (vault / "log.md").exists()


def test_print_prompt_is_non_mutating_preview(tmp_path: Path, capsys) -> None:
    vault = init_vault(tmp_path)

    class PreviewArgs(Args):
        print_prompt = True

    result = colorize.run_colorize(PreviewArgs(), vault, {})

    assert result == 0
    assert not (vault / ".obsidian" / "graph.json").exists()
    assert "No Codex prompt is used" in capsys.readouterr().out
