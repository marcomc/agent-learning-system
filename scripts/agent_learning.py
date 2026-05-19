#!/usr/bin/env python3
"""Helpers for the local Agent Learning System."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path.home() / ".config" / "agent-learning-system" / "config.env"
DEFAULT_LEARNING_DIR = "AI Agent Learnings"
REVIEW_BEGIN = "<!-- BEGIN AGENT LEARNING REVIEW -->"
REVIEW_END = "<!-- END AGENT LEARNING REVIEW -->"
REVIEW_BLOCK = f"""## Review Decision

{REVIEW_BEGIN}
- [ ] Approve: promote this rule
- [ ] Retry: I edited the rule, reprocess it
- [ ] Reject: do not promote this rule
{REVIEW_END}
"""
EMAIL_PATTERN = (
    r"[A-Za-z0-9._%+-]+@"
    r"(?!example\.(?:com|org|net|test)\b)"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)
PLACEHOLDER_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9._%+-]+@example\.(?:com|org|net|test)$",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_PATTERN = (
    "(?i)" + r"\b(?:token|secret|password|cookie|api[_-]?key)\b\s*[:=]"
)
PRIVATE_PATTERNS = [
    re.compile("/" + r"Users/[A-Za-z0-9._-]+"),
    re.compile(EMAIL_PATTERN),
    re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[0-1]))(?:\.\d{1,3}){2,3}\b"),
    re.compile(SECRET_ASSIGNMENT_PATTERN),
]
SOURCE_NOTE_DIRS = ("inbox", "needs-review")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_config(path: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            config[key.strip()] = value
    for key, value in os.environ.items():
        if key.startswith("AGENT_LEARNING_"):
            config[key] = value
    return config


def require_config(config: dict[str, str], key: str) -> str:
    value = config.get(key, "").strip()
    if not value:
        raise ConfigError(f"Missing required config value: {key}")
    return value


def learning_root(config: dict[str, str]) -> Path:
    vault = Path(require_config(config, "AGENT_LEARNING_VAULT")).expanduser()
    dirname = config.get("AGENT_LEARNING_DIR", DEFAULT_LEARNING_DIR).strip() or DEFAULT_LEARNING_DIR
    return vault / dirname


def ensure_store(root: Path) -> None:
    for rel in ["inbox", "needs-review", "reports", "state"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    state_path = root / "state" / "processed.json"
    if not state_path.exists():
        state_path.write_text(json.dumps({"processed": {}}, indent=2) + "\n", encoding="utf-8")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:64] or "learning"


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw_fm = text[4:end]
    body = text[end + 5 :]
    data: dict[str, str] = {}
    for raw in raw_fm.splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key.strip()] = value
    return data, body


def render_frontmatter(data: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key in sorted(data):
        value = data[key]
        safe = value.replace('"', '\\"')
        lines.append(f'{key}: "{safe}"')
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")


def read_note(path: Path) -> tuple[dict[str, str], str]:
    return parse_frontmatter(path.read_text(encoding="utf-8"))


def write_note(path: Path, data: dict[str, str], body: str) -> None:
    path.write_text(render_frontmatter(data, body), encoding="utf-8")


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True


def validate_note_source(root: Path, source: Path) -> Path:
    try:
        resolved_source = source.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ConfigError(f"Note file does not exist: {source}") from exc

    allowed_dirs = [(root / dirname).resolve() for dirname in SOURCE_NOTE_DIRS]
    if not any(is_relative_to(resolved_source, allowed) for allowed in allowed_dirs):
        allowed = ", ".join(str(path) for path in allowed_dirs)
        raise ConfigError(f"Refusing to finalize note outside learning store: {source}; allowed: {allowed}")
    return resolved_source


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not create unique path for {path}")


def read_json_input(source: str) -> dict[str, Any]:
    if source == "-":
        return json.load(sys.stdin)
    with Path(source).expanduser().open(encoding="utf-8") as handle:
        return json.load(handle)


def section(title: str, value: str) -> str:
    return f"## {title}\n\n{value.strip() or 'Not recorded.'}\n"


def command_init_store(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    print(root)
    return 0


def command_record(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)

    data = read_json_input(args.from_json) if args.from_json else {}
    note_id = str(uuid.uuid4())
    title = str(data.get("title") or args.title or "Agent learning")
    project_path = str(data.get("project_path") or args.project_path or Path.cwd())
    source_skill = str(data.get("source_skill") or args.source_skill or "unknown")
    learning_type = str(data.get("learning_type") or args.learning_type)
    genericity = str(data.get("genericity") or args.genericity)

    fields = {
        "Problem": str(data.get("problem") or args.problem or ""),
        "Root Cause": str(data.get("root_cause") or args.root_cause or ""),
        "Fix": str(data.get("fix") or args.fix or ""),
        "Verification": str(data.get("verification") or args.verification or ""),
        "Future Detection": str(data.get("future_detection") or args.future_detection or ""),
        "Future Prevention": str(data.get("future_prevention") or args.future_prevention or ""),
        "Promotion Decision": "Pending consolidation.",
    }
    body = f"# {title}\n\n" + "\n".join(section(name, value) for name, value in fields.items())
    fm = {
        "id": note_id,
        "created_at": now_iso(),
        "project_path": project_path,
        "source_skill": source_skill,
        "learning_type": learning_type,
        "status": "inbox",
        "genericity": genericity,
        "promoted_targets": "[]",
    }
    filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title)}-{note_id[:8]}.md"
    path = unique_path(root / "inbox" / filename)
    write_note(path, fm, body)
    print(path)
    return 0


def checkbox_decision(text: str) -> tuple[str, list[str]]:
    if REVIEW_BEGIN not in text or REVIEW_END not in text:
        return "pending", []
    block = text.split(REVIEW_BEGIN, 1)[1].split(REVIEW_END, 1)[0]
    checked: list[str] = []
    for raw in block.splitlines():
        line = raw.strip()
        match = re.match(r"- \[[xX]\]\s*([^:]+)", line)
        if match:
            checked.append(match.group(1).strip().lower())
    if not checked:
        return "pending", []
    if len(checked) > 1:
        return "ambiguous", checked
    value = checked[0]
    if value.startswith("approve"):
        return "approved", checked
    if value.startswith("retry"):
        return "retry", checked
    if value.startswith("reject"):
        return "rejected", checked
    return "ambiguous", checked


def ensure_review_block(body: str) -> str:
    if REVIEW_BEGIN in body and REVIEW_END in body:
        return body
    return body.rstrip() + "\n\n" + REVIEW_BLOCK


def note_summary(path: Path) -> dict[str, str]:
    fm, body = read_note(path)
    decision, checked = checkbox_decision(body)
    return {
        "path": str(path),
        "id": fm.get("id", path.stem),
        "title": body.splitlines()[0].lstrip("# ").strip() if body.splitlines() else path.stem,
        "project_path": fm.get("project_path", ""),
        "source_skill": fm.get("source_skill", ""),
        "status": fm.get("status", ""),
        "review_decision": decision,
        "checked": ", ".join(checked),
        "review_reason": fm.get("review_reason", ""),
    }


def command_prepare_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    inbox = [note_summary(path) for path in sorted((root / "inbox").glob("*.md"))]
    review = [note_summary(path) for path in sorted((root / "needs-review").glob("*.md"))]
    payload = {
        "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "learning_root": str(root),
        "inbox": inbox,
        "needs_review": review,
    }
    print(json.dumps(payload, indent=2))
    return 0


def load_state(root: Path) -> dict[str, Any]:
    state_path = root / "state" / "processed.json"
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"processed": {}}


def save_state(root: Path, state: dict[str, Any]) -> None:
    state_path = root / "state" / "processed.json"
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_finalize_note(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    source = validate_note_source(root, Path(args.file))
    fm, body = read_note(source)
    note_id = fm.get("id", source.stem)
    status = args.status
    fm["status"] = status
    fm["processed_at"] = now_iso()
    fm["processing_rationale"] = args.rationale
    if args.run_id:
        fm["consolidation_run"] = args.run_id

    if status == "needs-review":
        fm["review_reason"] = args.rationale
        body = ensure_review_block(body)
        dest = root / "needs-review" / source.name
    elif status in {"processed", "rejected"}:
        today = datetime.now()
        dest = root / "processed" / f"{today.year:04d}" / f"{today.month:02d}" / source.name
    else:
        raise SystemExit(f"Unsupported status: {status}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    write_note(source, fm, body)
    if source == dest.resolve():
        final = dest
    else:
        final = unique_path(dest)
    if source != final.resolve():
        shutil.move(str(source), str(final))

    state = load_state(root)
    state.setdefault("processed", {})[note_id] = {
        "path": str(final),
        "status": status,
        "processed_at": fm["processed_at"],
        "rationale": args.rationale,
    }
    save_state(root, state)
    print(final)
    return 0


def command_write_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = unique_path(root / "reports" / f"{run_id}.md")
    body = textwrap.dedent(
        f"""\
        # Agent Learning Consolidation {run_id}

        - Generated: {now_iso()}

        ## Summary

        {args.summary.strip()}
        """
    )
    path.write_text(body, encoding="utf-8")
    print(path)
    return 0


def pending_review_notes(root: Path) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    for path in sorted((root / "needs-review").glob("*.md")):
        summary = note_summary(path)
        if summary["review_decision"] in {"pending", "ambiguous"}:
            notes.append(summary)
    return notes


def notification_body(config: dict[str, str], root: Path) -> tuple[str, str] | None:
    notes = pending_review_notes(root)
    if not notes:
        return None
    subject = f"Agent learning review needed: {len(notes)} note(s)"
    lines = [
        "There are agent-learning notes waiting for review.",
        "",
        f"Folder: {root / 'needs-review'}",
        "",
    ]
    for note in notes:
        reason = note["review_reason"] or "No review reason recorded"
        project = note["project_path"] or "Unknown project"
        lines.extend(
            [
                f"- {note['title']}",
                f"  Project: {project}",
                f"  Reason: {reason}",
                f"  Path: {note['path']}",
            ]
        )
    lines.extend(
        [
            "",
            "In Obsidian, check exactly one Review Decision box:",
            "- Approve: promote this rule",
            "- Retry: edited and reprocess",
            "- Reject: archive without promotion",
        ]
    )
    _ = config
    return subject, "\n".join(lines)


def send_msmtp(recipient: str, subject: str, body: str) -> None:
    if not shutil.which("msmtp"):
        raise SystemExit("msmtp is not available; configure Gmail connector or install msmtp.")
    message = f"To: {recipient}\nSubject: {subject}\n\n{body}\n"
    subprocess.run(["msmtp", "--read-envelope-from", "-t"], input=message, text=True, check=True)


def is_placeholder_email(value: str) -> bool:
    return bool(PLACEHOLDER_EMAIL_PATTERN.match(value.strip()))


def command_notify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    payload = notification_body(config, root)
    if payload is None:
        print("NO_PENDING_REVIEW")
        return 0
    subject, body = payload
    if args.send_msmtp:
        recipient = config.get("AGENT_LEARNING_EMAIL", "").strip()
        if not recipient or is_placeholder_email(recipient):
            raise SystemExit("AGENT_LEARNING_EMAIL is not configured for sending.")
        send_msmtp(recipient, subject, body)
        print(f"SENT {recipient}")
        return 0
    print(json.dumps({"subject": subject, "body": body}, indent=2))
    return 0


def command_privacy_scan(args: argparse.Namespace) -> int:
    failed = False
    for raw_path in args.paths:
        path = Path(raw_path).expanduser()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PRIVATE_PATTERNS:
            if pattern.search(text):
                print(f"{path}: private or secret-shaped material matched {pattern.pattern}")
                failed = True
                break
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    init_store = sub.add_parser("init-store")
    init_store.set_defaults(func=command_init_store)

    record = sub.add_parser("record")
    record.add_argument("--from-json")
    record.add_argument("--project-path")
    record.add_argument("--source-skill", default="unknown")
    record.add_argument("--learning-type", default="finding_fix")
    record.add_argument("--genericity", default="unknown")
    record.add_argument("--title")
    record.add_argument("--problem")
    record.add_argument("--root-cause")
    record.add_argument("--fix")
    record.add_argument("--verification")
    record.add_argument("--future-detection")
    record.add_argument("--future-prevention")
    record.set_defaults(func=command_record)

    prepare = sub.add_parser("prepare-run")
    prepare.set_defaults(func=command_prepare_run)

    finalize = sub.add_parser("finalize-note")
    finalize.add_argument("--file", required=True)
    finalize.add_argument("--status", choices=["processed", "needs-review", "rejected"], required=True)
    finalize.add_argument("--rationale", required=True)
    finalize.add_argument("--run-id", default="")
    finalize.set_defaults(func=command_finalize_note)

    report = sub.add_parser("write-report")
    report.add_argument("--run-id", default="")
    report.add_argument("--summary", required=True)
    report.set_defaults(func=command_write_report)

    notify = sub.add_parser("notify")
    notify.add_argument("--send-msmtp", action="store_true")
    notify.set_defaults(func=command_notify)

    privacy = sub.add_parser("privacy-scan")
    privacy.add_argument("paths", nargs="+")
    privacy.set_defaults(func=command_privacy_scan)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
