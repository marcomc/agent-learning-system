#!/usr/bin/env python3
"""Helpers for the local Agent Learning System."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path.home() / ".config" / "agent-learning-system" / "config.env"
DEFAULT_LEARNING_STORE_NAME = "AI Agent Learnings"
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
LOCAL_PATH_PATTERN = (
    r"/(?:Users|home)/[A-Za-z0-9._-]+(?:/[^\s`'\"<>)]*)?"
    r"|/(?:workspace|workspaces)/[A-Za-z0-9._-]+(?:/[^\s`'\"<>)]*)?"
)
PRIVATE_PATTERNS = [
    re.compile(LOCAL_PATH_PATTERN),
    re.compile(EMAIL_PATTERN),
    re.compile(r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[0-1]))(?:\.\d{1,3}){2,3}\b"),
    re.compile(SECRET_ASSIGNMENT_PATTERN),
]
SOURCE_NOTE_DIRS = ("inbox", "needs-review")
HOOK_BEGIN = "<!-- BEGIN RECORD AGENT LEARNING HOOK -->"
HOOK_END = "<!-- END RECORD AGENT LEARNING HOOK -->"
HOOK_SECTION = f"""## Agent Learning Hook

{HOOK_BEGIN}
When this review workflow produces a real reusable finding, fix, regression, or
workflow correction, run `$record-agent-learning` before the final response.
Pass this skill name as `--source-skill`. Skip capture for clean/no-op reviews
and one-off repository trivia.
{HOOK_END}
"""
HOOK_EXCLUDED_SKILLS = {"record-agent-learning", "consolidate-agent-learnings"}
REVIEW_FRONTMATTER_HINTS = (
    "review",
    "pre-pr",
    "branch-qa",
    "max-findings",
    "remediation",
    "regression-check",
    "sanitizer",
)
REVIEW_NAME_HINTS = (
    "review",
    "pre-pr",
    "branch-qa",
    "max-findings",
    "remediation",
    "regression-check",
    "sanitizer",
    "security-best-practices",
)
REVIEW_DESCRIPTION_PATTERN = re.compile(
    r"\b(?:review|reviewing)\s+(?:a|an|the|this|branches?|codebase|code|diff|project|pull|pr|issue|comments?)\b"
    r"|\breview/issue comments\b"
    r"|\bpr-style qa review\b"
)
REVIEW_BODY_HINTS = (
    "code review",
    "review finding",
    "review findings",
    "pre-pr review",
    "github codex review",
    "security review",
)

DEFAULT_AUDIT_SKILLS_DIR = Path.home() / ".agents" / "skills"
DEFAULT_AUDIT_TARGETS = (Path.home() / "AGENTS.md",)
DEFAULT_SIMILARITY_THRESHOLD = 0.92
DEFAULT_CONFLICT_THRESHOLD = 0.78
ROUTING_SCOPES = (
    "global",
    "atom",
    "project-local",
    "skill-prevention",
    "skill-detection",
    "needs-review",
)
TEMPLATE_UPSTREAM_STATUSES = ("not-applicable", "draft-created", "promoted", "deferred")
RECURRENCE_CHECKS = ("new", "duplicate-before-promotion", "duplicate-after-prevention")
STATE_LOCK_TIMEOUT_SECONDS = float(os.environ.get("AGENT_LEARNING_STATE_LOCK_TIMEOUT_SECONDS", "10.0"))
STATE_LOCK_STALE_SECONDS = float(os.environ.get("AGENT_LEARNING_STATE_LOCK_STALE_SECONDS", "60.0"))


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
    legacy_vault = config.get("AGENT_LEARNING_VAULT", "").strip()
    if legacy_vault:
        base_dir = Path(legacy_vault).expanduser()
        store_name = config.get("AGENT_LEARNING_STORE_NAME") or config.get("AGENT_LEARNING_DIR")
    else:
        base_dir = Path(require_config(config, "AGENT_LEARNING_DIR")).expanduser()
        store_name = config.get("AGENT_LEARNING_STORE_NAME")
    dirname = (store_name or DEFAULT_LEARNING_STORE_NAME).strip() or DEFAULT_LEARNING_STORE_NAME
    return base_dir / dirname


def ensure_store(root: Path) -> None:
    for rel in ["inbox", "needs-review", "reports", "state"]:
        (root / rel).mkdir(parents=True, exist_ok=True)
    state_path = root / "state" / "processed.json"
    if not state_path.exists():
        atomic_write_text(state_path, json.dumps({"processed": {}}, indent=2) + "\n")


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


def unique_values(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    return unique


def parse_json_list(value: str) -> list[str]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        try:
            payload = json.loads(value.replace('\\"', '"'))
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        parsed = parse_json_list(value)
        if parsed:
            return parsed
        stripped = value.strip()
        return [stripped] if stripped else []
    return [str(value)]


def json_list(values: list[str]) -> str:
    return json.dumps(unique_values(values))


def note_title(body: str, fallback: Path) -> str:
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or fallback.stem
    return fallback.stem


def routing_args_present(args: argparse.Namespace) -> bool:
    fields = (
        "lesson_family",
        "scope",
        "prevention_target",
        "detection_target",
        "template_upstream_status",
        "routing_rationale",
        "recurrence_check",
    )
    return any(bool(getattr(args, field, None)) for field in fields)


def routing_from_args(
    args: argparse.Namespace,
    fm: dict[str, str] | None = None,
    body: str = "",
    source: Path | None = None,
) -> dict[str, Any]:
    current = fm or {}
    fallback = source or Path("learning.md")
    family = (
        getattr(args, "lesson_family", None)
        or current.get("lesson_family")
        or slugify(note_title(body, fallback))
    )
    scope = getattr(args, "scope", None) or current.get("scope", "")
    prevention_targets = unique_values(
        coerce_list(current.get("prevention_targets")) + coerce_list(getattr(args, "prevention_target", None))
    )
    detection_targets = unique_values(
        coerce_list(current.get("detection_targets")) + coerce_list(getattr(args, "detection_target", None))
    )
    template_status = (
        getattr(args, "template_upstream_status", None)
        or current.get("template_upstream_status")
        or "not-applicable"
    )
    rationale = getattr(args, "routing_rationale", None) or current.get("routing_rationale", "")
    recurrence = getattr(args, "recurrence_check", None) or current.get("recurrence_check") or "new"

    metadata = {
        "lesson_family": str(family),
        "scope": str(scope),
        "prevention_targets": prevention_targets,
        "detection_targets": detection_targets,
        "template_upstream_status": str(template_status),
        "routing_rationale": str(rationale),
        "recurrence_check": str(recurrence),
    }
    validate_routing_values(metadata)
    return metadata


def validate_routing_values(metadata: dict[str, Any]) -> None:
    scope = str(metadata.get("scope", ""))
    template_status = str(metadata.get("template_upstream_status", ""))
    recurrence = str(metadata.get("recurrence_check", ""))
    if scope and scope not in ROUTING_SCOPES:
        raise ConfigError(f"Unsupported routing scope: {scope}")
    if template_status and template_status not in TEMPLATE_UPSTREAM_STATUSES:
        raise ConfigError(f"Unsupported template upstream status: {template_status}")
    if recurrence and recurrence not in RECURRENCE_CHECKS:
        raise ConfigError(f"Unsupported recurrence check: {recurrence}")


def enforce_routing_contract(metadata: dict[str, Any]) -> None:
    family = str(metadata.get("lesson_family", "")).strip()
    scope = str(metadata.get("scope", "")).strip()
    rationale = str(metadata.get("routing_rationale", "")).strip()
    prevention_targets = metadata.get("prevention_targets") or []
    detection_targets = metadata.get("detection_targets") or []
    if not family:
        raise ConfigError("Routing metadata requires --lesson-family.")
    if not scope:
        raise ConfigError("Routing metadata requires --scope.")
    if scope != "needs-review" and not rationale:
        raise ConfigError("Routing metadata requires --routing-rationale.")
    if scope not in {"skill-detection", "needs-review"} and not prevention_targets:
        raise ConfigError("Routing metadata requires --prevention-target for prevention-capable scopes.")
    if scope == "skill-detection" and not detection_targets:
        raise ConfigError("Routing metadata requires --detection-target for skill-detection scope.")


def apply_routing_frontmatter(fm: dict[str, str], metadata: dict[str, Any]) -> None:
    fm["lesson_family"] = str(metadata["lesson_family"])
    fm["scope"] = str(metadata["scope"])
    fm["prevention_targets"] = json_list(metadata["prevention_targets"])
    fm["detection_targets"] = json_list(metadata["detection_targets"])
    fm["template_upstream_status"] = str(metadata["template_upstream_status"])
    fm["routing_rationale"] = str(metadata["routing_rationale"])
    fm["recurrence_check"] = str(metadata["recurrence_check"])


def routing_state_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "lesson_family": metadata["lesson_family"],
        "scope": metadata["scope"],
        "prevention_targets": metadata["prevention_targets"],
        "detection_targets": metadata["detection_targets"],
        "template_upstream_status": metadata["template_upstream_status"],
        "routing_rationale": metadata["routing_rationale"],
        "recurrence_check": metadata["recurrence_check"],
    }


def routing_markdown(metadata: dict[str, Any]) -> str:
    prevention = metadata.get("prevention_targets") or []
    detection = metadata.get("detection_targets") or []
    lines = [
        "## Routing Decision",
        "",
        f"- Lesson family: `{metadata.get('lesson_family') or 'not-recorded'}`",
        f"- Scope: `{metadata.get('scope') or 'not-recorded'}`",
        "- Prevention targets:",
    ]
    lines.extend(f"  - `{item}`" for item in prevention)
    if not prevention:
        lines.append("  - `none-recorded`")
    lines.append("- Detection targets:")
    lines.extend(f"  - `{item}`" for item in detection)
    if not detection:
        lines.append("  - `none-recorded`")
    lines.extend(
        [
            f"- Template upstream status: `{metadata.get('template_upstream_status')}`",
            f"- Routing rationale: {metadata.get('routing_rationale') or 'Not recorded.'}",
            f"- Recurrence check: `{metadata.get('recurrence_check')}`",
        ]
    )
    return "\n".join(lines) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextmanager
def state_update_lock(root: Path):
    lock_dir = root / "state" / ".processed.lock"
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
    acquired = False
    while not acquired:
        try:
            lock_dir.mkdir()
            acquired = True
        except FileExistsError:
            try:
                if time.time() - lock_dir.stat().st_mtime > STATE_LOCK_STALE_SECONDS:
                    lock_dir.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise ConfigError(f"Timed out waiting for state lock: {lock_dir}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if acquired:
            try:
                lock_dir.rmdir()
            except FileNotFoundError:
                pass


def privacy_findings(text: str) -> list[str]:
    return [pattern.pattern for pattern in PRIVATE_PATTERNS if pattern.search(text)]


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
    routing = routing_from_args(
        argparse.Namespace(
            lesson_family=data.get("lesson_family") or getattr(args, "lesson_family", None),
            scope=data.get("scope") or getattr(args, "scope", None),
            prevention_target=coerce_list(data.get("prevention_targets"))
            + coerce_list(getattr(args, "prevention_target", None)),
            detection_target=coerce_list(data.get("detection_targets"))
            + coerce_list(getattr(args, "detection_target", None)),
            template_upstream_status=data.get("template_upstream_status")
            or getattr(args, "template_upstream_status", None),
            routing_rationale=data.get("routing_rationale") or getattr(args, "routing_rationale", None),
            recurrence_check=data.get("recurrence_check") or getattr(args, "recurrence_check", None),
        ),
        {},
        body,
        Path(f"{slugify(title)}.md"),
    )
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
    apply_routing_frontmatter(fm, routing)
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
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"State file is malformed: {state_path}") from exc
    return {"processed": {}}


def save_state(root: Path, state: dict[str, Any]) -> None:
    state_path = root / "state" / "processed.json"
    atomic_write_text(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def update_processed_state(root: Path, note_id: str, entry: dict[str, Any]) -> None:
    with state_update_lock(root):
        state = load_state(root)
        state.setdefault("processed", {})[note_id] = entry
        save_state(root, state)


def command_finalize_note(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    source = validate_note_source(root, Path(args.file))
    original_text = source.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(original_text)
    note_id = fm.get("id", source.stem)
    status = args.status
    routing = routing_from_args(args, fm, body, source)
    if args.enforce_routing:
        enforce_routing_contract(routing)
    apply_routing_frontmatter(fm, routing)
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

    moved = False
    final = dest
    with state_update_lock(root):
        state = load_state(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_note(source, fm, body)
        if source == dest.resolve():
            final = dest
        else:
            final = unique_path(dest)
        try:
            if source != final.resolve():
                shutil.move(str(source), str(final))
                moved = True
            state.setdefault("processed", {})[note_id] = {
                "path": str(final),
                "status": status,
                "processed_at": fm["processed_at"],
                "rationale": args.rationale,
                **routing_state_payload(routing),
            }
            save_state(root, state)
        except Exception:
            if moved and final.exists() and not source.exists():
                shutil.move(str(final), str(source))
            if source.exists():
                source.write_text(original_text, encoding="utf-8")
            raise
    print(final)
    return 0


def command_write_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    path = unique_path(root / "reports" / f"{run_id}.md")
    routing_section = ""
    if routing_args_present(args):
        routing = routing_from_args(args, {}, "", Path(f"{run_id}.md"))
        if args.enforce_routing:
            enforce_routing_contract(routing)
        routing_section = "\n" + routing_markdown(routing)
    body = textwrap.dedent(
        f"""\
        # Agent Learning Consolidation {run_id}

        - Generated: {now_iso()}

        ## Summary

        {args.summary.strip()}
        {routing_section}
        """
    )
    path.write_text(body, encoding="utf-8")
    print(path)
    return 0


def frontmatter_from_state_entry(entry: dict[str, Any]) -> dict[str, Any]:
    path_value = str(entry.get("path") or "")
    path = Path(path_value) if path_value else Path()
    if path_value and path.exists():
        fm, _body = read_note(path)
        return {
            "lesson_family": fm.get("lesson_family", entry.get("lesson_family", "")),
            "scope": fm.get("scope", entry.get("scope", "")),
            "prevention_targets": parse_json_list(fm.get("prevention_targets", "")),
            "detection_targets": parse_json_list(fm.get("detection_targets", "")),
            "template_upstream_status": fm.get(
                "template_upstream_status",
                entry.get("template_upstream_status", ""),
            ),
            "routing_rationale": fm.get("routing_rationale", entry.get("routing_rationale", "")),
            "recurrence_check": fm.get("recurrence_check", entry.get("recurrence_check", "")),
        }
    return {
        "lesson_family": entry.get("lesson_family", ""),
        "scope": entry.get("scope", ""),
        "prevention_targets": coerce_list(entry.get("prevention_targets")),
        "detection_targets": coerce_list(entry.get("detection_targets")),
        "template_upstream_status": entry.get("template_upstream_status", ""),
        "routing_rationale": entry.get("routing_rationale", ""),
        "recurrence_check": entry.get("recurrence_check", ""),
    }


def processed_entry_in_window(entry: dict[str, Any], since: str) -> bool:
    if not since:
        return True
    processed_at = str(entry.get("processed_at") or "")
    return bool(processed_at) and processed_at[:10] >= since


def validate_since_date(value: str) -> None:
    if not value:
        return
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ConfigError("--since must be a YYYY-MM-DD date.")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ConfigError("--since must be a YYYY-MM-DD date.") from exc


def command_summarize_runs(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    validate_since_date(args.since)
    state = load_state(root)
    processed = state.get("processed", {})
    by_scope: dict[str, int] = {}
    by_recurrence: dict[str, int] = {}
    by_family: dict[str, int] = {}
    duplicate_after_prevention = 0
    missing_routing = 0
    processed_count = 0

    for entry in processed.values():
        if not isinstance(entry, dict) or not processed_entry_in_window(entry, args.since):
            continue
        processed_count += 1
        routing = frontmatter_from_state_entry(entry)
        scope = str(routing.get("scope") or "not-recorded")
        recurrence = str(routing.get("recurrence_check") or "not-recorded")
        family = str(routing.get("lesson_family") or "not-recorded")
        prevention_targets = routing.get("prevention_targets") or []
        by_scope[scope] = by_scope.get(scope, 0) + 1
        by_recurrence[recurrence] = by_recurrence.get(recurrence, 0) + 1
        by_family[family] = by_family.get(family, 0) + 1
        if recurrence == "duplicate-after-prevention":
            duplicate_after_prevention += 1
        if (
            entry.get("status") == "processed"
            and scope not in {"skill-detection", "needs-review"}
            and not prevention_targets
        ):
            missing_routing += 1

    payload = {
        "learning_root": str(root),
        "since": args.since or "",
        "processed_count": processed_count,
        "queue": {
            "inbox": len(list((root / "inbox").glob("*.md"))),
            "needs_review": len(list((root / "needs-review").glob("*.md"))),
        },
        "by_scope": dict(sorted(by_scope.items())),
        "by_recurrence": dict(sorted(by_recurrence.items())),
        "lesson_families": dict(sorted(by_family.items())),
        "missing_routing_count": missing_routing,
        "duplicate_after_prevention_count": duplicate_after_prevention,
    }
    if args.format == "markdown":
        print(summary_markdown(payload))
    else:
        print(json.dumps(payload, indent=2))
    return 0


def summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Agent Learning Run Summary",
        "",
        f"- Since: {payload['since'] or 'all-time'}",
        f"- Processed: {payload['processed_count']}",
        f"- Inbox: {payload['queue']['inbox']}",
        f"- Needs review: {payload['queue']['needs_review']}",
        f"- Missing routing: {payload['missing_routing_count']}",
        f"- Duplicate after prevention: {payload['duplicate_after_prevention_count']}",
        "",
        "## Recurrence",
        "",
    ]
    for key, count in payload["by_recurrence"].items():
        lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Scope", ""])
    for key, count in payload["by_scope"].items():
        lines.append(f"- `{key}`: {count}")
    lines.extend(["", "## Lesson Families", ""])
    for key, count in payload["lesson_families"].items():
        lines.append(f"- `{key}`: {count}")
    return "\n".join(lines)


def command_create_template_draft(args: argparse.Namespace) -> int:
    template_repo = Path(args.template_repo).expanduser().resolve()
    if not (template_repo / "templates.yml").exists():
        raise ConfigError(f"Template repository is missing templates.yml: {template_repo}")
    lesson_family = args.lesson_family.strip()
    if not lesson_family:
        raise ConfigError("--lesson-family is required.")
    candidate_rule = args.candidate_rule.strip()
    if not candidate_rule:
        raise ConfigError("--candidate-rule is required.")
    if privacy_findings(candidate_rule):
        raise ConfigError("Candidate rule must be scrubbed before writing a template draft.")
    if args.privacy_verdict != "clean":
        raise ConfigError("Template draft creation requires --privacy-verdict clean.")

    metadata = {
        "lesson_family": lesson_family,
        "scope": "atom",
        "prevention_targets": unique_values(args.prevention_target or []),
        "detection_targets": unique_values(args.detection_target or []),
        "template_upstream_status": "draft-created",
        "routing_rationale": args.routing_rationale.strip(),
        "recurrence_check": args.recurrence_check,
    }
    validate_routing_values(metadata)
    if args.enforce_routing:
        enforce_routing_contract(metadata)

    source_note = Path(args.source_note).name
    refresh_triggers = unique_values(args.refresh_trigger or [])
    body = learning_upstream_draft_markdown(
        lesson_family=lesson_family,
        source_note=source_note,
        proposed_template=args.proposed_template,
        candidate_rule=candidate_rule,
        metadata=metadata,
        privacy_verdict=args.privacy_verdict,
        review_status=args.review_status,
        refresh_triggers=refresh_triggers,
    )
    if privacy_findings(body):
        raise ConfigError("Draft body must be scrubbed before writing a template draft.")
    draft_dir = template_repo / ".work" / "learning-upstream"
    path = unique_path(draft_dir / f"{slugify(lesson_family)}.md")
    atomic_write_text(path, body)
    print(path)
    return 0


def learning_upstream_draft_markdown(
    *,
    lesson_family: str,
    source_note: str,
    proposed_template: str,
    candidate_rule: str,
    metadata: dict[str, Any],
    privacy_verdict: str,
    review_status: str,
    refresh_triggers: list[str],
) -> str:
    prevention_targets = metadata.get("prevention_targets") or []
    detection_targets = metadata.get("detection_targets") or []
    lines = [
        f"# Learning Upstream Draft: {lesson_family}",
        "",
        "## Handoff",
        "",
        f"- Lesson family: `{lesson_family}`",
        f"- Source note: `{source_note}`",
        f"- Proposed template: `{proposed_template}`",
        f"- Review status: `{review_status}`",
        f"- Privacy verdict: `{privacy_verdict}`",
        f"- Recurrence check: `{metadata.get('recurrence_check')}`",
        "",
        "## Candidate Rule",
        "",
        candidate_rule,
        "",
        "## Prevention Targets",
        "",
    ]
    lines.extend(f"- `{item}`" for item in prevention_targets)
    if not prevention_targets:
        lines.append("- `none-recorded`")
    lines.extend(["", "## Detection Targets", ""])
    lines.extend(f"- `{item}`" for item in detection_targets)
    if not detection_targets:
        lines.append("- `none-recorded`")
    lines.extend(["", "## Refresh Triggers", ""])
    lines.extend(f"- `{item}`" for item in refresh_triggers)
    if not refresh_triggers:
        lines.append("- `none-recorded`")
    lines.extend(
        [
            "",
            "## Routing Rationale",
            "",
            metadata.get("routing_rationale") or "Not recorded.",
            "",
            "## Review Decision",
            "",
            "- [ ] Promote to curated atom/template",
            "- [ ] Defer for more evidence",
            "- [ ] Reject as not reusable",
            "",
        ]
    )
    return "\n".join(lines)


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


def iter_markdown_bullets(path: Path) -> list[dict[str, Any]]:
    bullets: list[dict[str, Any]] = []
    in_fence = False
    for idx, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        line = raw.rstrip("\n")
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^\s*-\s+(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith("[ ]") or value.startswith("[x]") or value.startswith("[X]"):
            continue
        if not value:
            continue
        bullets.append({"path": str(path), "line": idx, "text": value})
    return bullets


def normalize_rule(text: str) -> str:
    lowered = text.lower().replace("`", "")
    lowered = re.sub(r"[-_/]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    lowered = re.sub(r"[^a-z0-9 ]+", "", lowered)
    return lowered


def rule_polarity(text: str) -> int:
    normalized = normalize_rule(text)
    negative_phrases = ("do not", "dont", "never", "ban", "forbid", "must not", "cannot", "avoid")
    positive_phrases = ("allow", "permit", "ok to", "acceptable to", "prefer")
    negative = any(phrase in normalized for phrase in negative_phrases)
    positive = any(phrase in normalized for phrase in positive_phrases)
    if negative and not positive:
        return -1
    if positive and not negative:
        return 1
    return 0


def command_audit_rules(args: argparse.Namespace) -> int:
    paths: list[Path] = []
    if args.path:
        paths.extend(Path(p).expanduser() for p in args.path)
    else:
        paths.extend(DEFAULT_AUDIT_TARGETS)
        skills_dir = args.skills_dir.expanduser() if args.skills_dir else DEFAULT_AUDIT_SKILLS_DIR
        if skills_dir.exists():
            paths.append(skills_dir)

    markdown_files: list[Path] = []
    for path in paths:
        if path.is_dir():
            if args.all_md:
                markdown_files.extend([p for p in path.glob("**/*.md") if p.is_file()])
            else:
                markdown_files.extend([p for p in path.glob("**/SKILL.md") if p.is_file()])
                markdown_files.extend([p for p in path.glob("**/AGENTS.md") if p.is_file()])
        elif path.is_file() and path.suffix.lower() == ".md":
            markdown_files.append(path)

    rules: list[dict[str, Any]] = []
    for md_path in sorted(set(markdown_files)):
        for bullet in iter_markdown_bullets(md_path):
            bullet["norm"] = normalize_rule(bullet["text"])
            rules.append(bullet)

    by_norm: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        norm = rule["norm"]
        if len(norm) < 24:
            continue
        by_norm.setdefault(norm, []).append(rule)

    exact_duplicates = [group for group in by_norm.values() if len(group) > 1]

    similarity_threshold = float(args.similarity_threshold)
    conflict_threshold = float(args.conflict_threshold)
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for rule in rules:
        norm = rule["norm"]
        if len(norm) < 24:
            continue
        first = norm.split(" ", 1)[0] if norm else ""
        buckets.setdefault((first, len(norm) // 24), []).append(rule)

    near_duplicates: list[dict[str, Any]] = []
    potential_conflicts: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, int, str, int]] = set()

    for group in buckets.values():
        if len(group) < 2:
            continue
        for idx, left in enumerate(group):
            for right in group[idx + 1 :]:
                key = (left["path"], left["line"], right["path"], right["line"])
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                if left["norm"] == right["norm"]:
                    continue
                ratio = difflib.SequenceMatcher(None, left["norm"], right["norm"]).ratio()
                if ratio >= similarity_threshold:
                    near_duplicates.append({"ratio": ratio, "a": left, "b": right})
                if ratio >= conflict_threshold:
                    pol_a = rule_polarity(left["text"])
                    pol_b = rule_polarity(right["text"])
                    if pol_a and pol_b and pol_a != pol_b:
                        potential_conflicts.append({"ratio": ratio, "a": left, "b": right})

    payload = {
        "scanned_files": [str(path) for path in sorted(set(markdown_files))],
        "rule_count": len(rules),
        "exact_duplicates": exact_duplicates,
        "near_duplicates": sorted(near_duplicates, key=lambda item: item["ratio"], reverse=True),
        "potential_conflicts": sorted(potential_conflicts, key=lambda item: item["ratio"], reverse=True),
        "thresholds": {
            "similarity_threshold": similarity_threshold,
            "conflict_threshold": conflict_threshold,
        },
    }

    try:
        print(json.dumps(payload, indent=2))
    except BrokenPipeError:
        return 0
    return 0


def hook_review_score(path: Path, text: str) -> tuple[int, list[str]]:
    fm, body = parse_frontmatter(text)
    name = (fm.get("name") or path.parent.name).strip()
    description = fm.get("description", "")
    if name in HOOK_EXCLUDED_SKILLS:
        return 0, ["excluded"]

    frontmatter = f"{name}\n{description}".lower()
    name_text = name.lower()
    description_text = description.lower()
    body_sample = body[:4000].lower()
    score = 0
    reasons: list[str] = []

    for hint in REVIEW_NAME_HINTS:
        if hint in name_text:
            score += 4
            reasons.append(f"name mentions {hint}")
            break
    if REVIEW_DESCRIPTION_PATTERN.search(description_text):
        score += 4
        reasons.append("description frames review as primary")
    for hint in REVIEW_FRONTMATTER_HINTS:
        if hint in frontmatter and hint != "review":
            score += 3
            reasons.append(f"frontmatter mentions {hint}")
            break
    if name_text == "security-best-practices" and any(hint in body_sample for hint in REVIEW_BODY_HINTS):
        score += 3
        reasons.append("security best-practices review workflow")
    elif any(hint in body_sample for hint in REVIEW_BODY_HINTS):
        score += 1
        reasons.append("body mentions review workflow")

    return score, reasons


def is_review_skill(path: Path, text: str) -> tuple[bool, str]:
    score, reasons = hook_review_score(path, text)
    return score >= 3, "; ".join(reasons)


def append_hook_section(text: str) -> tuple[str, bool]:
    if HOOK_BEGIN in text and HOOK_END in text:
        return text, False
    return text.rstrip() + "\n\n" + HOOK_SECTION, True


def iter_skill_files(roots: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for root in roots:
        expanded = root.expanduser()
        if not expanded.exists():
            continue
        for path in sorted(expanded.glob("*/SKILL.md")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            files.append(path)
    return files


def matching_repo_skill_paths(name: str, roots: list[Path]) -> list[Path]:
    paths: list[Path] = []
    for root in roots:
        candidate = root.expanduser() / name / "SKILL.md"
        if candidate.exists():
            paths.append(candidate)
    return paths


def write_hook(path: Path, apply: bool) -> tuple[str, bool]:
    text = path.read_text(encoding="utf-8")
    updated, changed = append_hook_section(text)
    if changed and apply:
        path.write_text(updated, encoding="utf-8")
    if changed:
        return ("hooked" if apply else "would-hook"), changed
    return "already-hooked", changed


def command_hook_review_skills(args: argparse.Namespace) -> int:
    roots = args.skills_dir or [Path.home() / ".agents" / "skills"]
    repo_roots = args.repo_skills_dir or []
    actions: list[dict[str, str]] = []
    for path in iter_skill_files(roots):
        text = path.read_text(encoding="utf-8")
        fm, _body = parse_frontmatter(text)
        name = fm.get("name") or path.parent.name
        review_skill, reason = is_review_skill(path, text)
        if not review_skill:
            continue
        action, _changed = write_hook(path, args.apply)
        actions.append(
            {
                "action": action,
                "name": name,
                "path": str(path),
                "reason": reason,
            }
        )
        local_resolved = path.resolve()
        for repo_path in matching_repo_skill_paths(name, repo_roots):
            if repo_path.resolve() == local_resolved:
                continue
            repo_action, _repo_changed = write_hook(repo_path, args.apply)
            actions.append(
                {
                    "action": f"repo-{repo_action}",
                    "name": name,
                    "path": str(repo_path),
                    "reason": "matching repository skill",
                }
            )

    if args.json:
        print(json.dumps({"skills": actions}, indent=2))
        return 0

    for item in actions:
        print(f"{item['action'].upper()} {item['name']} {item['path']} [{item['reason']}]")
    if not actions:
        print("NO_REVIEW_SKILLS_FOUND")
    return 0


def add_routing_arguments(parser: argparse.ArgumentParser, *, include_enforce: bool = False) -> None:
    parser.add_argument("--lesson-family")
    parser.add_argument("--scope", choices=ROUTING_SCOPES)
    parser.add_argument("--prevention-target", action="append")
    parser.add_argument("--detection-target", action="append")
    parser.add_argument("--template-upstream-status", choices=TEMPLATE_UPSTREAM_STATUSES)
    parser.add_argument("--routing-rationale")
    parser.add_argument("--recurrence-check", choices=RECURRENCE_CHECKS)
    if include_enforce:
        parser.add_argument("--enforce-routing", action="store_true")


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
    add_routing_arguments(record)
    record.set_defaults(func=command_record)

    prepare = sub.add_parser("prepare-run")
    prepare.set_defaults(func=command_prepare_run)

    finalize = sub.add_parser("finalize-note")
    finalize.add_argument("--file", required=True)
    finalize.add_argument("--status", choices=["processed", "needs-review", "rejected"], required=True)
    finalize.add_argument("--rationale", required=True)
    finalize.add_argument("--run-id", default="")
    add_routing_arguments(finalize, include_enforce=True)
    finalize.set_defaults(func=command_finalize_note)

    report = sub.add_parser("write-report")
    report.add_argument("--run-id", default="")
    report.add_argument("--summary", required=True)
    add_routing_arguments(report, include_enforce=True)
    report.set_defaults(func=command_write_report)

    summary = sub.add_parser("summarize-runs")
    summary.add_argument("--since", default="")
    summary.add_argument("--format", choices=["json", "markdown"], default="json")
    summary.set_defaults(func=command_summarize_runs)

    draft = sub.add_parser("create-template-draft")
    draft.add_argument("--template-repo", required=True)
    draft.add_argument("--lesson-family", required=True)
    draft.add_argument("--source-note", required=True)
    draft.add_argument("--proposed-template", required=True)
    draft.add_argument("--candidate-rule", required=True)
    draft.add_argument("--prevention-target", action="append")
    draft.add_argument("--detection-target", action="append")
    draft.add_argument("--routing-rationale", default="")
    draft.add_argument("--recurrence-check", choices=RECURRENCE_CHECKS, default="new")
    draft.add_argument("--privacy-verdict", choices=["clean", "needs-scrub", "blocked"], required=True)
    draft.add_argument("--review-status", default="draft")
    draft.add_argument("--refresh-trigger", action="append")
    draft.add_argument("--enforce-routing", action="store_true")
    draft.set_defaults(func=command_create_template_draft)

    notify = sub.add_parser("notify")
    notify.add_argument("--send-msmtp", action="store_true")
    notify.set_defaults(func=command_notify)

    privacy = sub.add_parser("privacy-scan")
    privacy.add_argument("paths", nargs="+")
    privacy.set_defaults(func=command_privacy_scan)

    audit = sub.add_parser("audit-rules")
    audit.add_argument("--path", action="append")
    audit.add_argument("--skills-dir", type=Path, default=None)
    audit.add_argument("--similarity-threshold", default=str(DEFAULT_SIMILARITY_THRESHOLD))
    audit.add_argument("--conflict-threshold", default=str(DEFAULT_CONFLICT_THRESHOLD))
    audit.add_argument("--all-md", action="store_true")
    audit.set_defaults(func=command_audit_rules)

    hook = sub.add_parser("hook-review-skills")
    hook.add_argument("--skills-dir", type=Path, action="append")
    hook.add_argument("--repo-skills-dir", type=Path, action="append")
    hook.add_argument("--apply", action="store_true")
    hook.add_argument("--json", action="store_true")
    hook.set_defaults(func=command_hook_review_skills)
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
