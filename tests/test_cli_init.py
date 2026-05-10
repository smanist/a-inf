from __future__ import annotations

import json
from pathlib import Path

from a_inf import cli
from a_inf.cli import cmd_init


class Args:
    def __init__(self, path: Path, skills_source: Path, copy_skills: bool = False) -> None:
        self.path = str(path)
        self.skills_source = skills_source
        self.copy_skills = copy_skills
        self.no_agents = False
        self.no_gitignore = False
        self.write_global_config = False


def test_init_creates_vault_structure_and_local_skill_links(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    skills_source = Path(__file__).resolve().parents[1] / ".skills"

    def fake_ensure_qmd_collection(qmd_vault: Path, config: dict[str, str]) -> bool:
        assert qmd_vault == vault
        assert config["QMD_WIKI_COLLECTION"] == vault.name
        assert config["QMD_PAPERS_COLLECTION"] == vault.name
        return True

    monkeypatch.setattr(cli, "ensure_qmd_collection", fake_ensure_qmd_collection)
    result = cmd_init(Args(vault, skills_source))

    assert result == 0
    for dirname in [
        "concepts",
        "entities",
        "skills",
        "references",
        "synthesis",
        "journal",
        "projects",
        "ideas",
        "_archives",
        "_raw",
        "_meta",
        ".a-inf/sources",
        ".obsidian",
        ".agents",
        ".agents/skills",
    ]:
        assert (vault / dirname).is_dir()

    assert (vault / ".a-inf" / "config.toml").is_file()
    assert (vault / ".env").is_file()
    env = (vault / ".env").read_text(encoding="utf-8")
    assert f"QMD_WIKI_COLLECTION={vault.name}" in env
    assert f"QMD_PAPERS_COLLECTION={vault.name}" in env
    assert "A_INF_ARCHIVE_SOURCES=true" in env
    assert "A_INF_SOURCE_ARCHIVE_DIR=.a-inf/sources" in env
    assert "A_INF_QUERY_SOURCE_DETAIL=auto" in env
    graph = json.loads((vault / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    assert graph["search"] == "-tag:#a-inf"
    assert graph["collapse-filter"] is True
    assert (vault / "index.md").is_file()
    assert (vault / "log.md").is_file()
    assert (vault / "hot.md").is_file()
    assert (vault / "_meta" / "taxonomy.md").is_file()
    assert "tags: [a-inf]" in (vault / "index.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in (vault / "log.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in (vault / "hot.md").read_text(encoding="utf-8")
    assert "tags: [taxonomy, a-inf]" in (vault / "_meta" / "taxonomy.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert (vault / "AGENTS.md").read_text(encoding="utf-8").count("<!-- BEGIN A-INF -->") == 1
    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert "# a-inf local configuration" in gitignore
    assert ".DS_Store" in gitignore
    assert "_raw/" in gitignore
    assert ".env" in gitignore
    assert ".a-inf/" in gitignore

    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["sources"] == {}

    skill_links = list((vault / ".agents" / "skills").iterdir())
    assert skill_links
    assert all(path.is_symlink() for path in skill_links)
    assert (vault / ".agents" / "skills" / "wiki-colorize").is_symlink()
    assert (vault / ".agents" / "skills" / "wiki-fixlink").is_symlink()
    assert (vault / ".agents" / "skills" / "wiki-tags").is_symlink()
    assert not (vault / ".agents" / "skills" / "graph-colorize").exists()
    assert not (vault / ".agents" / "skills" / "tag-taxonomy").exists()
    assert not (vault / ".agents" / "skills" / "cross-linker").exists()
    assert not (vault / ".agents" / "skills" / "wiki-setup").exists()


def test_init_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    skills_source = Path(__file__).resolve().parents[1] / ".skills"
    args = Args(vault, skills_source)
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda *_args, **_kwargs: True)

    assert cmd_init(args) == 0
    assert cmd_init(args) == 0

    agents = (vault / "AGENTS.md").read_text(encoding="utf-8")
    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert agents.count("<!-- BEGIN A-INF -->") == 1
    assert gitignore.count("# a-inf local configuration") == 1


def test_init_upgrades_existing_gitignore_section(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".gitignore").write_text(
        "# a-inf local configuration\n.env\n.a-inf/config.toml\n",
        encoding="utf-8",
    )
    skills_source = Path(__file__).resolve().parents[1] / ".skills"
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda *_args, **_kwargs: True)

    assert cmd_init(Args(vault, skills_source)) == 0

    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count("# a-inf local configuration") == 1
    assert ".DS_Store" in gitignore
    assert "_raw/" in gitignore
    assert ".env" in gitignore
    assert ".a-inf/" in gitignore


def test_init_does_not_overwrite_existing_graph_settings(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    (vault / ".obsidian" / "graph.json").write_text('{"search": "path:concepts"}\n', encoding="utf-8")
    skills_source = Path(__file__).resolve().parents[1] / ".skills"
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda *_args, **_kwargs: True)

    assert cmd_init(Args(vault, skills_source)) == 0

    graph = json.loads((vault / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
    assert graph == {"search": "path:concepts"}


def test_init_adds_managed_tag_to_existing_support_files(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    (vault / "_meta").mkdir(parents=True)
    (vault / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (vault / "log.md").write_text("---\ntitle: Wiki Log\n---\n\n# Wiki Log\n", encoding="utf-8")
    (vault / "hot.md").write_text("---\ntitle: Hot Cache\ntags: [session]\n---\n\n# Hot Cache\n", encoding="utf-8")
    (vault / "_insights.md").write_text("# Wiki Insights\n", encoding="utf-8")
    (vault / "_meta" / "taxonomy.md").write_text(
        "---\ntitle: Tag Taxonomy\ntags: [taxonomy]\n---\n\n# Tag Taxonomy\n",
        encoding="utf-8",
    )
    (vault / "AGENTS.md").write_text("# Existing Instructions\n", encoding="utf-8")
    skills_source = Path(__file__).resolve().parents[1] / ".skills"
    monkeypatch.setattr(cli, "ensure_qmd_collection", lambda *_args, **_kwargs: True)

    assert cmd_init(Args(vault, skills_source)) == 0

    assert "tags: [a-inf]" in (vault / "index.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in (vault / "log.md").read_text(encoding="utf-8")
    assert "tags: [session, a-inf]" in (vault / "hot.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in (vault / "_insights.md").read_text(encoding="utf-8")
    assert "tags: [taxonomy, a-inf]" in (vault / "_meta" / "taxonomy.md").read_text(encoding="utf-8")
    assert "tags: [a-inf]" in (vault / "AGENTS.md").read_text(encoding="utf-8")
    assert "# Existing Instructions" in (vault / "AGENTS.md").read_text(encoding="utf-8")
