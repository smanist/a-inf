from __future__ import annotations

import json
from pathlib import Path

from a_inf.cli import cmd_init


class Args:
    def __init__(self, path: Path, skills_source: Path, copy_skills: bool = False) -> None:
        self.path = str(path)
        self.skills_source = skills_source
        self.copy_skills = copy_skills
        self.no_agents = False
        self.no_gitignore = False
        self.write_global_config = False


def test_init_creates_vault_structure_and_local_skill_links(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    skills_source = Path(__file__).resolve().parents[1] / ".skills"

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
        "_archives",
        "_raw",
        "_meta",
        ".obsidian",
        ".skills",
    ]:
        assert (vault / dirname).is_dir()

    assert (vault / ".a-inf" / "config.toml").is_file()
    assert (vault / ".env").is_file()
    assert (vault / "index.md").is_file()
    assert (vault / "log.md").is_file()
    assert (vault / "hot.md").is_file()
    assert (vault / "_meta" / "taxonomy.md").is_file()
    assert (vault / "AGENTS.md").read_text(encoding="utf-8").count("<!-- BEGIN A-INF -->") == 1
    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert "# a-inf local configuration" in gitignore
    assert ".a-inf/runs/" in gitignore

    manifest = json.loads((vault / ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert manifest["sources"] == {}

    skill_links = list((vault / ".skills").iterdir())
    assert skill_links
    assert all(path.is_symlink() for path in skill_links)
    assert not (vault / ".skills" / "wiki-setup").exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    skills_source = Path(__file__).resolve().parents[1] / ".skills"
    args = Args(vault, skills_source)

    assert cmd_init(args) == 0
    assert cmd_init(args) == 0

    agents = (vault / "AGENTS.md").read_text(encoding="utf-8")
    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert agents.count("<!-- BEGIN A-INF -->") == 1
    assert gitignore.count("# a-inf local configuration") == 1


def test_init_upgrades_existing_gitignore_section(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".gitignore").write_text(
        "# a-inf local configuration\n.env\n.a-inf/config.toml\n",
        encoding="utf-8",
    )
    skills_source = Path(__file__).resolve().parents[1] / ".skills"

    assert cmd_init(Args(vault, skills_source)) == 0

    gitignore = (vault / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count("# a-inf local configuration") == 1
    assert ".a-inf/runs/" in gitignore
