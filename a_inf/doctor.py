from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from a_inf import fixlink, lint, tags
from a_inf.ingest import render_page, write_json
from a_inf.managed_files import A_INF_TAG, ensure_managed_tag, managed_tags
from a_inf.runs import timestamped_run_dir


@dataclass(frozen=True)
class DoctorRun:
    run_dir: Path
    packet_path: Path
    report_path: Path


def run_doctor(args: Any, vault: Path, config: dict[str, str] | None = None) -> int:
    config = config or {}
    if getattr(args, "print_prompt", False):
        print(render_plan(args, vault, preview_run(vault)))
        return 0

    run = create_run(vault)
    phases: list[dict[str, Any]] = []
    phases.append({"name": "preflight", "status": "completed", "summary": preflight_summary(vault)})

    initial = run_lint_phase(args, vault, config, name="lint_initial")
    phases.append(initial)

    remove_broken = run_child_phase(
        name="remove_broken",
        runner=fixlink.run_fixlink,
        args=child_args(
            args,
            json=True,
            dry_run=getattr(args, "dry_run", False),
            remove_broken=True,
            no_codex=True,
            no_log=getattr(args, "no_log", False),
        ),
        vault=vault,
        config=config,
    )
    phases.append(remove_broken)

    if should_run_semantic_fixlink(args):
        semantic_fixlink = run_child_phase(
            name="fixlink",
            runner=fixlink.run_fixlink,
            args=child_args(
                args,
                json=True,
                dry_run=getattr(args, "dry_run", False),
                remove_broken=False,
                no_log=getattr(args, "no_log", False),
            ),
            vault=vault,
            config=config,
        )
    else:
        semantic_fixlink = skipped_phase(
            "fixlink",
            "semantic link repair is enabled by --fix or --full",
        )
    phases.append(semantic_fixlink)

    tag_audit = run_child_phase(
        name="tags",
        runner=tags.run_tags,
        args=child_args(
            args,
            json=True,
            fix=False,
            plan=None,
            no_codex=not getattr(args, "full", False) or getattr(args, "no_codex", False),
            no_log=True,
        ),
        vault=vault,
        config=config,
    )
    phases.append(tag_audit)

    if should_apply_tags(args):
        tag_fix = run_child_phase(
            name="tags_apply",
            runner=tags.run_tags,
            args=child_args(
                args,
                json=True,
                fix=True,
                plan=selected_tag_plan(tag_audit),
                no_log=getattr(args, "no_log", False),
            ),
            vault=vault,
            config=config,
        )
    else:
        tag_fix = skipped_phase(
            "tags_apply",
            "tag plans are only applied with --apply-tags",
        )
    phases.append(tag_fix)

    final = run_lint_phase(args, vault, config, name="lint_final")
    phases.append(final)

    packet = build_doctor_packet(args, vault, run, phases)
    write_json(run.packet_path, packet)
    markdown = render_markdown(packet)
    run.report_path.write_text(render_report_page(packet, markdown), encoding="utf-8")
    if not getattr(args, "no_log", False):
        append_log(vault, packet)

    if getattr(args, "json", False):
        print(json.dumps(packet, indent=2, sort_keys=True))
    else:
        print(markdown)
        print(f"Saved doctor report to {run.report_path.relative_to(vault)}")
    return 1 if packet["status"] == "failed" else 0


def run_lint_phase(args: Any, vault: Path, config: dict[str, str], *, name: str) -> dict[str, Any]:
    return run_child_phase(
        name=name,
        runner=lint.run_lint,
        args=child_args(
            args,
            json=True,
            no_codex=True,
            no_log=True,
            save=False,
            output=None,
            semantic_scope=getattr(args, "semantic_scope", "one-hop"),
        ),
        vault=vault,
        config=config,
    )


def run_child_phase(
    *,
    name: str,
    runner: Callable[[Any, Path, dict[str, str]], int],
    args: Any,
    vault: Path,
    config: dict[str, str],
) -> dict[str, Any]:
    stream = io.StringIO()
    try:
        with redirect_stdout(stream):
            code = runner(args, vault, config)
    except Exception as exc:  # pragma: no cover - defensive boundary around child workflows
        return {
            "name": name,
            "status": "failed",
            "exit_code": 1,
            "summary": {},
            "warnings": [str(exc)],
        }
    output = stream.getvalue().strip()
    report = parse_json_output(output)
    status = str(report.get("status") or ("completed" if code == 0 else "failed"))
    if code != 0 and status not in {"failed", "invalid"}:
        status = "failed"
    return {
        "name": name,
        "status": status,
        "exit_code": code,
        "summary": report.get("summary", {}),
        "warnings": report.get("warnings", []),
        "report": report,
    }


def parse_json_output(output: str) -> dict[str, Any]:
    if not output:
        return {}
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return {"raw_output": output}
    return value if isinstance(value, dict) else {"value": value}


def child_args(base: Any, **overrides: Any) -> SimpleNamespace:
    values = {
        "args": [],
        "json": False,
        "dry_run": False,
        "remove_broken": False,
        "fix": False,
        "plan": None,
        "no_codex": getattr(base, "no_codex", False),
        "print_prompt": False,
        "no_log": getattr(base, "no_log", False),
        "save": False,
        "output": None,
        "semantic_scope": getattr(base, "semantic_scope", "one-hop"),
        "codex_bin": getattr(base, "codex_bin", "codex"),
        "sandbox": getattr(base, "sandbox", "workspace-write"),
        "add_dir": getattr(base, "add_dir", []),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def should_run_semantic_fixlink(args: Any) -> bool:
    return bool((getattr(args, "fix", False) or getattr(args, "full", False)) and not getattr(args, "no_codex", False))


def should_apply_tags(args: Any) -> bool:
    return bool(getattr(args, "apply_tags", False))


def selected_tag_plan(tag_audit: dict[str, Any]) -> str | None:
    if tag_audit.get("status") != "planned":
        return None
    plan = tag_audit.get("report", {}).get("tag_plan_path")
    return str(plan) if plan else None


def skipped_phase(name: str, reason: str) -> dict[str, Any]:
    return {"name": name, "status": "skipped", "exit_code": 0, "summary": {}, "warnings": [reason], "report": {}}


def build_doctor_packet(args: Any, vault: Path, run: DoctorRun, phases: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [phase for phase in phases if int(phase.get("exit_code", 0)) != 0 or phase.get("status") in {"failed", "invalid"}]
    initial = phase_by_name(phases, "lint_initial")
    final = phase_by_name(phases, "lint_final")
    remove_broken = phase_by_name(phases, "remove_broken")
    fixlink_phase = phase_by_name(phases, "fixlink")
    tags_phase = phase_by_name(phases, "tags")
    tags_apply = phase_by_name(phases, "tags_apply")
    return {
        "version": 1,
        "generated_at": now_iso(),
        "vault": str(vault),
        "run_dir": str(run.run_dir),
        "status": "failed" if failed else "completed",
        "mode": {
            "dry_run": bool(getattr(args, "dry_run", False)),
            "fix": bool(getattr(args, "fix", False)),
            "full": bool(getattr(args, "full", False)),
            "apply_tags": bool(getattr(args, "apply_tags", False)),
            "no_codex": bool(getattr(args, "no_codex", False)),
            "semantic_scope": getattr(args, "semantic_scope", "one-hop"),
        },
        "summary": {
            "issues_found_before": nested_get(initial, "summary", "issues_found", default=0),
            "issues_found_after": nested_get(final, "summary", "issues_found", default=0),
            "broken_wikilinks_before": nested_get(remove_broken, "summary", "broken_wikilinks_before", default=0),
            "broken_wikilinks_after": nested_get(remove_broken, "summary", "broken_wikilinks_after", default=0),
            "links_removed": nested_get(remove_broken, "summary", "links_removed", default=0),
            "links_added": nested_get(fixlink_phase, "summary", "links_added", default=0),
            "tag_plan_status": tags_phase.get("status", "unknown"),
            "tag_pages_modified": nested_get(tags_apply, "summary", "pages_modified", default=0),
            "failed_phases": [phase["name"] for phase in failed],
        },
        "phases": phases,
    }


def nested_get(source: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = source
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def phase_by_name(phases: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next((phase for phase in phases if phase.get("name") == name), {})


def preflight_summary(vault: Path) -> dict[str, Any]:
    return {
        "manifest_exists": (vault / ".manifest.json").exists(),
        "index_exists": (vault / "index.md").exists(),
        "log_exists": (vault / "log.md").exists(),
        "hot_exists": (vault / "hot.md").exists(),
        "agents_exists": (vault / "AGENTS.md").exists(),
        "git_dirty": git_dirty(vault),
    }


def git_dirty(vault: Path) -> bool | None:
    if not (vault / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=vault,
            check=False,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def render_markdown(packet: dict[str, Any]) -> str:
    summary = packet["summary"]
    lines = [
        "## Wiki Doctor Report",
        "",
        f"- **Status:** {packet['status']}",
        f"- **Issues found:** {summary['issues_found_before']} -> {summary['issues_found_after']}",
        f"- **Broken wikilinks:** {summary['broken_wikilinks_before']} -> {summary['broken_wikilinks_after']}",
        f"- **Links removed:** {summary['links_removed']}",
        f"- **Links added:** {summary['links_added']}",
        f"- **Tag plan:** {summary['tag_plan_status']}",
        f"- **Tag pages modified:** {summary['tag_pages_modified']}",
        "",
        "### Phases",
        "",
    ]
    for phase in packet["phases"]:
        lines.append(f"- **{phase['name']}:** {phase['status']}")
        for warning in phase.get("warnings", [])[:3]:
            lines.append(f"  - {warning}")
    failed = summary.get("failed_phases") or []
    if failed:
        lines.extend(["", "### Failed Phases", ""])
        lines.extend(f"- {name}" for name in failed)
    return "\n".join(lines).rstrip() + "\n"


def render_report_page(packet: dict[str, Any], markdown: str) -> str:
    now = datetime.now(timezone.utc)
    return render_page(
        {
            "title": f"Doctor Report - {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "category": "query",
            "tags": [A_INF_TAG],
            "sources": ["a-inf doctor"],
            "created": now.date().isoformat(),
            "updated": now.date().isoformat(),
        },
        "\n".join(
            [
                "# Doctor Report",
                "",
                f"**Command:** `a-inf doctor`",
                f"**Generated:** {packet['generated_at']}",
                "",
                markdown.strip(),
            ]
        ),
    )


def append_log(vault: Path, packet: dict[str, Any]) -> None:
    log_path = vault / "log.md"
    summary = packet["summary"]
    line = (
        f"- [{now_iso()}] DOCTOR status={packet['status']} "
        f"issues={summary['issues_found_before']}->{summary['issues_found_after']} "
        f"broken_links={summary['broken_wikilinks_before']}->{summary['broken_wikilinks_after']} "
        f"links_removed={summary['links_removed']} links_added={summary['links_added']} "
        f"tag_pages_modified={summary['tag_pages_modified']}\n"
    )
    if log_path.exists():
        ensure_managed_tag(log_path, "Wiki Log")
        current = log_path.read_text(encoding="utf-8")
        log_path.write_text(current.rstrip() + "\n" + line, encoding="utf-8")
    else:
        log_path.write_text(f"---\ntitle: Wiki Log\ntags: {managed_tags()}\n---\n\n# Wiki Log\n\n" + line, encoding="utf-8")


def render_plan(args: Any, vault: Path, run: DoctorRun) -> str:
    phases = ["preflight", "lint_initial", "remove_broken"]
    if should_run_semantic_fixlink(args):
        phases.append("fixlink")
    phases.append("tags")
    if should_apply_tags(args):
        phases.append("tags_apply")
    phases.append("lint_final")
    return json.dumps(
        {
            "command": "a-inf doctor",
            "vault": str(vault),
            "run_dir": str(run.run_dir),
            "dry_run": bool(getattr(args, "dry_run", False)),
            "phases": phases,
        },
        indent=2,
        sort_keys=True,
    )


def create_run(vault: Path) -> DoctorRun:
    run_dir = timestamped_run_dir(vault, "doctor")
    return DoctorRun(run_dir=run_dir, packet_path=run_dir / "packet.json", report_path=run_dir / "doctor-report.md")


def preview_run(vault: Path) -> DoctorRun:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = vault / "_runs" / f"doctor-{stamp}"
    return DoctorRun(run_dir=run_dir, packet_path=run_dir / "packet.json", report_path=run_dir / "doctor-report.md")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
