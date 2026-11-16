#!/usr/bin/env python3
"""Helpers for the local Agent Learning System."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import redirect_stdout
from contextlib import contextmanager
import difflib
import errno
import hashlib
import json
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Any

# Support direct CLI execution and file-based imports used by the test harness.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from learning_pipeline import Pipeline, RULE_PARSER_VERSION, chat_url, check_sealed_file, current_audit_sources, current_refs, fingerprint, read_store_state, release_assessment_skeleton, snapshot
from learning_integration import concrete_routing_target_path

try:
    import fcntl
except ImportError:  # pragma: no cover - the installer/runtime targets POSIX systems.
    fcntl = None


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
GOAL_SEAL_TIMEOUT_SECONDS = 6.0


class ConfigError(RuntimeError):
    """Raised when required configuration is missing."""


class EvidenceUnavailable(ConfigError):
    """A bounded evidence read could not establish the file contents."""


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
    directories = [store_directory(root, root / rel) for rel in ["inbox", "needs-review", "reports", "state"]]
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    state_path = root / "state" / "processed.json"
    with state_update_lock(root):
        try:
            current = read_store_state(root, "processed.json", 8, probe_only=True)
        except ValueError as exc:
            raise EvidenceUnavailable("Processed state is unavailable; retry after storage recovery.") from exc
        if current is None:
            atomic_write_text(state_path, json.dumps({"processed": {}}, indent=2) + "\n")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:64] or "learning"


def parse_frontmatter(text: str, *, require_note_identity: bool = False) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    marker_prefix = "# agent-learning-frontmatter:"
    if end == -1:
        if any(line.strip().startswith(marker_prefix) for line in text[4:].splitlines()):
            raise ConfigError("Truncated encoded agent learning frontmatter.")
        return {}, text
    raw_fm = text[4:end]
    body = text[end + 5 :]
    data: dict[str, str] = {}
    lines = raw_fm.splitlines()
    marker = "# agent-learning-frontmatter: json-v2"
    markers = [line.strip() for line in lines if line.strip().startswith(marker_prefix)]
    if markers and markers != [marker]:
        raise ConfigError("Unsupported agent learning frontmatter encoding.")
    encoded = bool(markers)
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" not in raw:
            if encoded:
                raise ConfigError("Malformed encoded agent learning frontmatter.")
            continue
        key, value = raw.split(":", 1)
        key, value = key.strip(), value.strip()
        if encoded:
            if not key or key in data:
                raise ConfigError("Malformed encoded agent learning frontmatter.")
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ConfigError("Malformed encoded agent learning frontmatter.") from exc
            if not isinstance(value, str):
                raise ConfigError("Malformed encoded agent learning frontmatter.")
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            # The legacy renderer escaped quotes but left other backslashes literal.
            value = value[1:-1].replace('\\"', '"')
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        data[key] = value
    if encoded and require_note_identity and not data.get("id", "").strip():
        raise ConfigError("Encoded learning note requires a nonempty identity.")
    return data, body


def render_frontmatter(data: dict[str, str], body: str) -> str:
    lines = ["---", "# agent-learning-frontmatter: json-v2"]
    for key in sorted(data):
        value = data[key]
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
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


def store_directory(root: Path, directory: Path) -> Path:
    resolved = directory.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ConfigError(f"Learning directory resolves outside configured store: {directory}")
    return resolved


def validate_note_source(root: Path, source: Path) -> Path:
    try:
        resolved_source = source.expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ConfigError(f"Note file does not exist: {source}") from exc

    allowed_dirs = [store_directory(root, root / dirname) for dirname in SOURCE_NOTE_DIRS]
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


def parse_json_list_value(value: str) -> list[str] | None:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        try:
            payload = json.loads(value.replace('\\"', '"'))
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, list):
        return None
    return [str(item) for item in payload if str(item).strip()]


def parse_json_list(value: str) -> list[str]:
    return parse_json_list_value(value) or []


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        parsed = parse_json_list_value(value)
        if parsed is not None:
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
    lock_path = store_directory(root, root / "state") / ".processed.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        raise ConfigError("State locking requires POSIX file locking support.")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except IsADirectoryError as exc:
        raise ConfigError(f"Legacy directory state lock needs operator cleanup: {lock_path}") from exc
    except OSError as exc:
        raise ConfigError(f"Unable to open state lock: {lock_path}: {exc}") from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ConfigError(f"State lock must be a regular file: {lock_path}")
    lock_file = os.fdopen(fd, "r+", encoding="utf-8")
    deadline = time.monotonic() + STATE_LOCK_TIMEOUT_SECONDS
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise ConfigError(f"Unable to acquire state lock: {lock_path}: {exc}") from exc
                if time.monotonic() >= deadline:
                    raise ConfigError(f"Timed out waiting for state lock: {lock_path}") from exc
                time.sleep(0.05)
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


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
    source_run = str(data.get("source_run_id") or args.source_run_id or "").strip()
    application_status = str(data.get("application_status") or args.application_status)
    if application_status not in {"proposed", "applied"}:
        raise ConfigError("application_status must be proposed or applied.")
    if source_run and not (data.get("lesson_family") or args.lesson_family):
        raise ConfigError("A source run requires an explicit lesson family for capture identity.")

    fields = {
        "Problem": str(data.get("problem") or args.problem or ""),
        "Root Cause": str(data.get("root_cause") or args.root_cause or ""),
        "Fix": str(data.get("fix") or args.fix or ""),
        "Verification": str(data.get("verification") or args.verification or ""),
        "Future Detection": str(data.get("future_detection") or args.future_detection or ""),
        "Future Prevention": str(data.get("future_prevention") or args.future_prevention or ""),
        "Promotion Decision": (
            "Already applied; verify current coverage before archival, without applying it again."
            if application_status == "applied" else "Proposed; pending consolidation and applicable authority."
        ),
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
        "application_status": application_status,
        "source_run_id": source_run,
    }
    targets = unique_values(coerce_list(data.get("applied_targets")) + (args.applied_target or []))
    evidence = str(data.get("application_evidence") or args.application_evidence or "").strip()
    if application_status == "applied":
        if not targets or not evidence or not fields["Verification"].strip():
            raise ConfigError("Applied learning requires targets, application evidence and verification.")
        snapshots = []
        for value in targets + [evidence]:
            target = Path(value).expanduser().resolve()
            if not target.is_file():
                raise ConfigError(f"Application evidence/target is not a file: {target}")
            snapshots.append({"path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        fm["applied_targets"] = json.dumps(snapshots[:-1], sort_keys=True)
        fm["application_evidence"] = json.dumps(snapshots[-1], sort_keys=True)
    elif targets or evidence:
        raise ConfigError("Applied targets/evidence require application_status=applied.")
    apply_routing_frontmatter(fm, routing)
    identity = [str(Path(project_path).expanduser().resolve()), source_run, routing["lesson_family"]]
    capture_key = hashlib.sha256(json.dumps(identity).encode()).hexdigest() if source_run else ""
    # Producer names and generated IDs are not part of a shared terminal event.
    content = {key: value for key, value in fm.items() if key not in {"id", "created_at", "source_skill"}}
    content["project_path"] = identity[0]
    digest = hashlib.sha256(json.dumps([content, body], sort_keys=True).encode()).hexdigest()
    filename = f"{slugify(title)}-{capture_key}.md" if capture_key else f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slugify(title)}-{note_id[:8]}.md"
    if capture_key:
        fm.update(capture_key=capture_key, capture_digest=digest)
    with state_update_lock(root):
        state = load_state(root)
        if (not isinstance(state, dict) or not isinstance(state.get("captures", {}), dict)
                or not isinstance(state.get("processed", {}), dict)):
            raise ConfigError("Capture state must contain an object mapping.")
        recover_finalizations(root, state)
        captures = state.setdefault("captures", {})
        existing = captures.get(capture_key) if capture_key else None
        if existing is not None:
            if not isinstance(existing, dict) or not all(isinstance(existing.get(key), str) for key in ("id", "path", "digest")):
                raise ConfigError("Existing capture state is malformed.")
            if existing.get("digest") != digest:
                raise ConfigError("Capture identity already exists with different content; reconcile the existing note.")
            entry = state.get("processed", {}).get(existing["id"], existing)
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ConfigError("Existing capture location is malformed.")
            path = Path(entry["path"])
            resolved_path = path.resolve()
            if not resolved_path.is_relative_to(root.resolve()):
                raise ConfigError("Existing capture is outside the learning store; reconcile state.")
            if not resolved_path.is_file():
                existing = None
            elif read_note(resolved_path)[0].get("id") != existing["id"]:
                raise ConfigError("Existing capture identity does not match its note; reconcile state.")
            else:
                print(path)
                return 0
        if capture_key:
            # The stable filename and metadata survive a crash before index commit.
            candidates = [path for name in SOURCE_NOTE_DIRS for path in (root / name).glob(f"*-{capture_key}.md")]
            archive_dir = store_directory(root, root / "processed")
            candidates.extend(archive_dir.glob(f"*/*/*-{capture_key}.md"))
            recovered = [path for path in candidates if path.exists()]
            if len(recovered) > 1:
                raise ConfigError("Multiple notes share capture identity; reconcile existing captures.")
            if recovered:
                path = recovered[0]
                if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
                    raise ConfigError("Recovered capture is outside the learning store.")
                previous, _ = read_note(path)
                if previous.get("capture_key") != capture_key or previous.get("capture_digest") != digest or not previous.get("id"):
                    raise ConfigError("Capture identity already exists with different content; reconcile the existing note.")
                captures[capture_key] = {"id": previous["id"], "path": str(path), "digest": digest}
                save_state(root, state)
                print(path)
                return 0
            if capture_key in captures:
                raise ConfigError("Existing capture is missing; reconcile state.")
        path = store_directory(root, root / "inbox") / filename
        if not capture_key:
            path = unique_path(path)
        atomic_write_text(path, render_frontmatter(fm, body))
        if capture_key:
            captures[capture_key] = {"id": note_id, "path": str(path), "digest": digest}
            try:
                save_state(root, state)
            except Exception:
                path.unlink()
                raise
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


def review_stable_digest(text: str) -> str | None:
    """Bind a review note while allowing edits only inside its decision block."""
    if text.count(REVIEW_BEGIN) != 1 or text.count(REVIEW_END) != 1:
        return None
    start = text.index(REVIEW_BEGIN) + len(REVIEW_BEGIN)
    end = text.find(REVIEW_END, start)
    if end < 0:
        return None
    stable = text[:start] + text[end:]
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def note_summary(path: Path, raw: bytes | None = None) -> dict[str, str]:
    raw = path.read_bytes() if raw is None else raw
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    fm, body = parse_frontmatter(text, require_note_identity=True)
    note_id = fm.get("id", path.stem)
    if not note_id.strip():
        raise ConfigError("Learning note requires a nonempty identity.")
    decision, checked = checkbox_decision(body)
    return {
        "path": str(path),
        "id": note_id,
        "created_at": fm.get("created_at", ""),
        "title": body.splitlines()[0].lstrip("# ").strip() if body.splitlines() else path.stem,
        "project_path": fm.get("project_path", ""),
        "source_skill": fm.get("source_skill", ""),
        "status": fm.get("status", ""),
        "review_decision": decision,
        "checked": ", ".join(checked),
        "review_reason": fm.get("review_reason", ""),
        "lesson_family": fm.get("lesson_family", ""),
        "application_status": fm.get("application_status", "proposed"),
        "source_run_id": fm.get("source_run_id", ""),
        "applied_targets": fm.get("applied_targets", "[]"),
        "application_evidence": fm.get("application_evidence", ""),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def read_note_bounded(path: Path) -> bytes:
    try:
        result = subprocess.run(["/bin/cat", str(path)], capture_output=True, check=True, timeout=8)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("Note read timed out.") from exc
    if not result.stdout:
        raise OSError("Note is empty or not hydrated.")
    return result.stdout


def bounded_note_inventory(root: Path, paths: list[Path], *, timeout: float | None = None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    def inspect(path: Path) -> tuple[dict[str, str] | None, dict[str, str] | None]:
        try:
            resolved = validate_note_source(root, path)
            return note_summary(path, read_note_bounded(resolved)), None
        except (ConfigError, OSError, UnicodeError, subprocess.CalledProcessError):
            return None, {"path": str(path), "reason": "unavailable or unreadable"}
    executor = ThreadPoolExecutor(max_workers=8)
    try:
        if timeout is None:
            results = list(executor.map(inspect, paths))
        else:
            futures = [executor.submit(inspect, path) for path in paths]
            done, _ = wait(futures, timeout=timeout)
            results = []
            for path, future in zip(paths, futures):
                try:
                    results.append(future.result() if future in done else (None, {"path": str(path), "reason": "unavailable or unreadable"}))
                except Exception:
                    results.append((None, {"path": str(path), "reason": "unavailable or unreadable"}))
    finally:
        executor.shutdown(wait=timeout is None, cancel_futures=True)
    return ([note for note, _ in results if note is not None],
            [failure for _, failure in results if failure is not None])


def review_policy(config: dict[str, str]) -> dict[str, Any]:
    enabled = config.get("AGENT_LEARNING_AUTONOMOUS_REVIEW", "0").strip()
    if enabled not in {"0", "1"}:
        raise ConfigError("AGENT_LEARNING_AUTONOMOUS_REVIEW must be 0 or 1.")
    try:
        threshold = int(config.get("AGENT_LEARNING_REVIEW_GROUP_THRESHOLD", "8"))
    except ValueError as exc:
        raise ConfigError("AGENT_LEARNING_REVIEW_GROUP_THRESHOLD must be a positive integer.") from exc
    if threshold < 1:
        raise ConfigError("AGENT_LEARNING_REVIEW_GROUP_THRESHOLD must be a positive integer.")
    return {"autonomous_instructions": enabled == "1", "group_threshold": threshold}


def review_analysis(config: dict[str, str], notes: list[dict[str, str]]) -> dict[str, Any]:
    policy = review_policy(config)
    pending = [note for note in notes if note["review_decision"] in {"pending", "ambiguous"}]
    contexts: dict[str, list[dict[str, str]]] = {}
    for note in pending:
        project = note["project_path"].strip()
        context = str(Path(project).expanduser().resolve()) if project else "unknown-project"
        contexts.setdefault(context, []).append(note)
    fingerprint = hashlib.sha256(
        json.dumps(sorted((note["id"], note["sha256"]) for note in pending)).encode()
    ).hexdigest()
    return {
        **policy,
        "pending_count": len(pending),
        "group_review_due": len(pending) >= policy["group_threshold"],
        "fingerprint": fingerprint,
        "candidate_contexts": [
            {"project_path": context, "count": len(members), "note_ids": [n["id"] for n in members],
             "lesson_families": sorted({n["lesson_family"] for n in members if n["lesson_family"]})}
            for context, members in sorted(contexts.items())
        ],
        "autonomous_candidates": [n["id"] for n in pending if n["review_decision"] == "pending"],
        "human_precedence": [n["id"] for n in notes if n["review_decision"] != "pending"],
        "notice": "Contexts and shared families are discovery hints, not approval or proof of equivalence. Read full notes and compare evidence before deciding.",
    }


def inbox_analysis(notes: list[dict[str, str]], review: list[dict[str, str]] | None = None) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    projects: dict[str, list[dict[str, str]]] = {}
    eligible_review = [n for n in (review or []) if n["review_decision"] == "pending"]
    review_ids = {n["id"] for n in eligible_review}
    for note in [*notes, *eligible_review]:
        project = note.get("project_path", "").strip()
        source = note.get("source_run_id", "").strip()
        if project:
            projects.setdefault(project, []).append(note)
        if project and source:
            groups.setdefault((project, source), []).append(note)
    cohorts = []
    for (project, source), members in groups.items():
        if len(members) < 2 or all(n["id"] in review_ids for n in members):
            continue
        members.sort(key=lambda n: (n.get("created_at", ""), n["id"]))
        cohorts.append({"project_path": project, "source_run_id": source,
                        "note_ids": [n["id"] for n in members],
                        "review_note_ids": [n["id"] for n in members if n["id"] in review_ids],
                        "oldest": members[0].get("created_at", "")})
    cohorts.sort(key=lambda c: (c["oldest"], c["project_path"], c["source_run_id"]))
    contexts = [{"project_path": project, "note_ids": [n["id"] for n in members],
                 "review_note_ids": [n["id"] for n in members if n["id"] in review_ids]}
                for project, members in sorted(projects.items()) if len(members) > 1
                and not all(n["id"] in review_ids for n in members)]
    return {"candidate_cohorts": cohorts, "candidate_contexts": contexts,
            "notice": "Cohorts are discovery hints only; inspect every source and independently verify evidence and decisions."}


def command_prepare_run(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    with state_update_lock(root):
        inbox, unavailable_inbox = bounded_note_inventory(root, sorted((root / "inbox").glob("*.md")))
        review, unavailable_review = bounded_note_inventory(root, sorted((root / "needs-review").glob("*.md")))
        payload = {
            "run_id": datetime.now().strftime("%Y%m%d-%H%M%S"),
            "learning_root": str(root),
            "inbox": inbox,
            "inventory_complete": not (unavailable_inbox or unavailable_review),
            "unavailable_sources": unavailable_inbox + unavailable_review,
            "inbox_analysis": inbox_analysis(inbox, review),
            "needs_review": review,
            "review_analysis": review_analysis(config, review),
            "pending_finalizations": [str(path) for path in finalization_journals(root)],
        }
    print(json.dumps(payload, indent=2))
    return 0


def load_state(root: Path, *, deadline: float | None = None) -> dict[str, Any]:
    state_path = root / "state" / "processed.json"
    try:
        if deadline is None:
            deadline = time.monotonic() + 8
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceUnavailable("Processed state timed out; retry after storage recovery.")
        raw = read_store_state(root, "processed.json", remaining)
        return json.loads(raw) if raw is not None else {"processed": {}}
    except json.JSONDecodeError as exc:
        raise ConfigError(f"State file is malformed: {state_path}") from exc
    except ValueError as exc:
        raise EvidenceUnavailable("Processed state is unavailable or timed out; retry after storage recovery.") from exc


def save_state(root: Path, state: dict[str, Any]) -> None:
    state_path = root / "state" / "processed.json"
    atomic_write_text(state_path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def update_processed_state(root: Path, note_id: str, entry: dict[str, Any]) -> None:
    with state_update_lock(root):
        state = load_state(root)
        state.setdefault("processed", {})[note_id] = entry
        save_state(root, state)


def finalization_journals(root: Path) -> list[Path]:
    return sorted(store_directory(root, root / "state" / "finalizations").glob("*.json"))


def recover_finalizations(
    root: Path, state: dict[str, Any], *, rollback: bool = False, only: Path | None = None,
) -> list[dict[str, str]]:
    """Reconcile prepared filesystem/state commits while the caller holds the lock."""
    if not isinstance(state, dict) or not isinstance(state.get("processed", {}), dict):
        raise ConfigError("Finalization state must contain a processed object mapping.")
    recovered = []
    for journal in [only] if only else finalization_journals(root):
        if not journal.resolve().is_relative_to(root.resolve()):
            raise ConfigError("Finalization journal is outside the learning store.")
        try:
            data = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Unreadable finalization journal: {journal}") from exc
        if (not isinstance(data, dict) or data.get("version") != 1
                or any(not isinstance(data.get(key), str) for key in
                       ("note_id", "source", "destination", "original_text", "result_text"))
                or not isinstance(data.get("entry"), dict)):
            raise ConfigError("Malformed finalization journal; manual reconciliation required.")
        source, dest = Path(data["source"]).resolve(), Path(data["destination"]).resolve()
        entry, note_id = data["entry"], data["note_id"]
        allowed = [store_directory(root, root / name) for name in SOURCE_NOTE_DIRS]
        if (not any(source.is_relative_to(path) for path in allowed)
                or not dest.is_relative_to(root.resolve())
                or Path(str(entry.get("path", ""))).resolve() != dest
                or entry.get("status") not in {"processed", "rejected", "needs-review"}):
            raise ConfigError("Finalization journal has invalid store paths or status.")
        expected_dir = root / ("needs-review" if entry["status"] == "needs-review" else "processed")
        if not dest.is_relative_to(store_directory(root, expected_dir)):
            raise ConfigError("Finalization destination does not match its status.")
        original = data["original_text"].encode()
        result = data["result_text"].encode()
        if (parse_frontmatter(data["result_text"])[0].get("id", source.stem) != note_id
                or not note_id):
            raise ConfigError("Finalization journal note identity is invalid.")
        current = state.get("processed", {}).get(note_id)
        previous = data.get("previous_entry")
        if current != previous and current != entry:
            raise ConfigError("Finalization state changed; manual reconciliation required.")
        src_bytes = source.read_bytes() if source.is_file() else None
        dst_bytes = dest.read_bytes() if dest.is_file() else None
        completed_move = dst_bytes == result and (source == dest or src_bytes is None)
        if current == entry and completed_move:
            outcome = "committed"
        elif current == entry and current != previous:
            raise ConfigError("Committed finalization does not match its note; reconcile state.")
        elif completed_move and not rollback:
            state.setdefault("processed", {})[note_id] = entry
            save_state(root, state)
            outcome = "committed"
        else:
            allowed_dest = (None, original, result) if source == dest else (None, result)
            if src_bytes not in (None, original, result) or dst_bytes not in allowed_dest:
                raise ConfigError("Finalization note changed; preserve it for manual reconciliation.")
            if src_bytes is None and dst_bytes is None:
                raise ConfigError("Finalization source and destination are missing; reconcile journal.")
            if src_bytes != original:
                store_directory(root, source.parent)
                atomic_write_text(source, data["original_text"])
            if source != dest and dest.exists():
                dest.unlink()
            outcome = "rolled-back"
        journal.unlink()
        recovered.append({"note_id": note_id, "outcome": outcome})
    return recovered


def command_recover_finalizations(args: argparse.Namespace) -> int:
    root = learning_root(load_config(args.config))
    ensure_store(root)
    with state_update_lock(root):
        recovered = recover_finalizations(root, load_state(root))
    print(json.dumps({"recovered": recovered}))
    return 0


def instruction_target(path: Path, root: Path) -> bool:
    if path.is_relative_to(root.resolve()) or path.suffix != ".md":
        return False
    if path.name in {"AGENTS.md", "CLAUDE.md", "GEMINI.md", "SKILL.md", "copilot-instructions.md"}:
        return True
    if path.name.endswith(".instructions.md"):
        return True
    if path.name == "agent-procedures.md" and path.parent.name == "docs":
        project_rules = path.parent.parent / "AGENTS.md"
        return project_rules.is_file() and "docs/agent-procedures.md" in project_rules.read_text(encoding="utf-8")
    return any(
        parent.name == "references" and (parent.parent / "SKILL.md").is_file()
        for parent in path.parents
    )


def validate_autonomous_review(
    config: dict[str, str], root: Path, source: Path, note_id: str,
    body: str, args: argparse.Namespace, state: dict[str, Any], routing: dict[str, Any],
) -> dict[str, str]:
    """Check delegated authority and receipt integrity, not semantic truth."""
    if not review_policy(config)["autonomous_instructions"]:
        raise ConfigError("Autonomous review requires explicit AGENT_LEARNING_AUTONOMOUS_REVIEW=1 authorization.")
    if source.parent.resolve() != (root / "needs-review").resolve():
        raise ConfigError("Autonomous review only finalizes configured needs-review notes.")
    if args.status != "processed" or not args.enforce_routing:
        raise ConfigError("Autonomous review requires --status processed and --enforce-routing.")
    if checkbox_decision(body)[0] != "pending":
        raise ConfigError("Explicit or ambiguous human review decisions take precedence.")
    receipt = Path(args.review_evidence).expanduser().resolve()
    reports = (root / "reports").resolve()
    if not receipt.is_relative_to(reports) or not receipt.is_file():
        raise ConfigError("Review evidence must be a saved JSON receipt inside configured reports.")
    raw = receipt.read_bytes()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigError("Invalid autonomous review receipt JSON.") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("scope") != "instructions-only":
        raise ConfigError("Review receipt requires version 1 and scope instructions-only.")
    snapshots = data.get("sources")
    decisions = data.get("decisions")
    if not isinstance(snapshots, list) or len(snapshots) < 2 or not isinstance(decisions, list):
        raise ConfigError("Joint review requires at least two source snapshots and a decision list.")
    digest = hashlib.sha256(raw).hexdigest()
    source_ids: set[str] = set()
    source_paths: set[Path] = set()
    selected_snapshot = None
    for item in snapshots:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("id", "path", "sha256")):
            raise ConfigError("Each source snapshot requires id, path and sha256.")
        path = Path(item["path"]).expanduser().resolve()
        if path.parent != (root / "needs-review").resolve() or path.suffix != ".md":
            raise ConfigError("Review source snapshot is outside configured needs-review.")
        if item["id"] in source_ids or path in source_paths:
            raise ConfigError("Review source snapshots must have unique IDs and paths.")
        source_ids.add(item["id"])
        source_paths.add(path)
        if path.exists():
            fm, _ = read_note(path)
            if fm.get("id", path.stem) != item["id"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise ConfigError("Stale or mismatched review source snapshot.")
        else:
            entry = state.get("processed", {}).get(item["id"], {})
            archived = Path(entry.get("path", "")).resolve()
            if (entry.get("status") != "processed" or entry.get("review_receipt_sha256") != digest
                    or entry.get("review_source_sha256") != item["sha256"]
                    or not archived.is_relative_to((root / "processed").resolve()) or not archived.is_file()
                    or hashlib.sha256(archived.read_bytes()).hexdigest() != entry.get("review_archive_sha256")):
                raise ConfigError("A joint source is missing without a matching archive receipt.")
        if path == source and item["id"] == note_id:
            selected_snapshot = item
    if selected_snapshot is None:
        raise ConfigError("Current note is absent from review source snapshots.")
    matching = [d for d in decisions if isinstance(d, dict) and d.get("note_id") == note_id]
    if len(matching) != 1:
        raise ConfigError("Exactly one autonomous decision is required for the current note.")
    decision = matching[0]
    if decision.get("classification") not in {"technical", "already-covered"}:
        raise ConfigError("Only technical or already-covered decisions can be archived autonomously.")
    if decision.get("open_choices") != []:
        raise ConfigError("Unresolved user choices prohibit autonomous archival.")
    evidence = decision.get("evidence_refs")
    if (not isinstance(evidence, list) or note_id not in evidence
            or any(not isinstance(ref, str) or ref not in source_ids for ref in evidence)):
        raise ConfigError("Decision evidence must reference known sources including the current note.")
    if not isinstance(decision.get("rationale"), str) or not decision["rationale"].strip():
        raise ConfigError("Autonomous decision requires a grounded rationale.")
    targets = decision.get("targets")
    if not isinstance(targets, list) or not targets:
        raise ConfigError("Autonomous decision requires verified instruction targets.")
    verified_targets: set[Path] = set()
    for target in targets:
        if not isinstance(target, dict) or not isinstance(target.get("path"), str):
            raise ConfigError("Each instruction target requires path and sha256.")
        path = Path(target["path"]).expanduser().resolve()
        if not instruction_target(path, root) or not path.is_file():
            raise ConfigError("Autonomous target must be an instruction Markdown file outside the learning store.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != target.get("sha256"):
            raise ConfigError("Stale instruction target in autonomous review receipt.")
        verified_targets.add(path)
    original_fm, _ = read_note(source)
    original_routing = routing_from_args(argparse.Namespace(), original_fm, body, source)
    original_paths = {p for value in original_routing["prevention_targets"] + original_routing["detection_targets"]
                      if (p := concrete_routing_target_path(value)) is not None}
    implementation_paths: set[Path] = set()
    implementation_evidence = decision.get("implementation_evidence", [])
    if not isinstance(implementation_evidence, list):
        raise ConfigError("Invalid implementation evidence.")
    project = Path(original_fm.get("project_path", "")).expanduser().resolve()
    for ref in implementation_evidence:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise ConfigError("Invalid implementation evidence.")
        path = Path(ref["path"]).expanduser().resolve()
        if (not original_fm.get("project_path") or not project.is_dir() or project == project.parent
                or path not in original_paths or not path.is_relative_to(project)
                or not path.is_file() or instruction_target(path, root)
                or hashlib.sha256(path.read_bytes()).hexdigest() != ref.get("sha256")):
            raise ConfigError("Stale or invalid implementation evidence.")
        implementation_paths.add(path)
    for value in routing["prevention_targets"] + routing["detection_targets"]:
        path = concrete_routing_target_path(value)
        if path is not None and path not in verified_targets and path not in implementation_paths:
            raise ConfigError("Concrete routing targets must match verified receipt targets.")
    return {
        "review_authority": "autonomous-instructions",
        "review_classification": decision["classification"],
        "review_receipt": str(receipt),
        "review_receipt_sha256": digest,
        "review_source_sha256": selected_snapshot["sha256"],
    }


def command_finalize_note(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    with state_update_lock(root):
        state = load_state(root)
        recover_finalizations(root, state)
        source = validate_note_source(root, Path(args.file))
        original_bytes = source.read_bytes()
        original_text = original_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        fm, body = parse_frontmatter(original_text, require_note_identity=True)
        note_id = fm.get("id", source.stem)
        if not note_id.strip():
            raise ConfigError("Learning note requires a nonempty identity.")
        fm.setdefault("id", note_id)
        status = args.status
        pipeline_state = pipeline_store(root).load()
        active_runs = [r for r in pipeline_state["runs"].values() if r.get("status") == "running"]
        if len(active_runs) > 1:
            raise ConfigError("Multiple active runs need reconciliation before finalization.")
        active = active_runs[0] if active_runs else None
        if args.run_id and (not active or active.get("id") != args.run_id):
            raise ConfigError("Finalization run ID must identify the active consolidation.")
        if active and not args.run_id:
            raise ConfigError("Finalization during an active consolidation requires the active --run-id.")
        routing = routing_from_args(args, fm, body, source)
        if args.enforce_routing:
            enforce_routing_contract(routing)
        apply_routing_frontmatter(fm, routing)
        fm["status"] = status
        fm["processed_at"] = now_iso()
        fm["processing_rationale"] = args.rationale
        if args.run_id:
            fm["consolidation_run"] = args.run_id
        else:
            fm.pop("consolidation_run", None)
        if status == "needs-review":
            fm["review_reason"] = args.rationale
            body = ensure_review_block(body)
            dest = root / "needs-review" / source.name
        elif status in {"processed", "rejected"}:
            today = datetime.now()
            dest = root / "processed" / f"{today.year:04d}" / f"{today.month:02d}" / source.name
        else:
            raise ConfigError(f"Unsupported status: {status}")
        review_metadata: dict[str, str] = {}
        if args.review_evidence:
            review_metadata = validate_autonomous_review(
                config, root, source, note_id, body, args, state, routing
            )
            fm.update(review_metadata)
        store_directory(root, dest.parent).mkdir(parents=True, exist_ok=True)
        final = dest if source == dest.resolve() else unique_path(dest)
        result_text = render_frontmatter(fm, body)
        source_hash = hashlib.sha256(original_bytes).hexdigest()
        previous = state.get("processed", {}).get(note_id, {})
        lineage = []
        same_archive = previous.get("archive_sha256") == source_hash
        decision_only = (previous.get("status") == "needs-review"
                         and previous.get("archive_review_stable_sha256")
                         and previous["archive_review_stable_sha256"] == review_stable_digest(original_text))
        if ((same_archive or decision_only) and previous.get("path")
                and Path(previous["path"]).expanduser().resolve() == source):
            lineage = list(previous.get("source_lineage", []))
        source_ref = {"path": str(source), "sha256": source_hash}
        if source_ref not in lineage:
            lineage.append(source_ref)
        entry = {
            "path": str(final), "status": status, "processed_at": fm["processed_at"],
            "source_path": str(source), "source_sha256": source_hash, "source_lineage": lineage,
            "archive_sha256": hashlib.sha256(result_text.encode("utf-8")).hexdigest(),
            "rationale": args.rationale, **routing_state_payload(routing), **review_metadata,
        }
        if args.run_id:
            entry["consolidation_run"] = args.run_id
        prior_runs = previous.get("consolidation_runs", [])
        prior_run = previous.get("consolidation_run", "") if previous.get("status") == "processed" else ""
        if (not isinstance(prior_runs, list) or any(not isinstance(value, str) for value in prior_runs)
                or not isinstance(prior_run, str)):
            raise ConfigError("Stored consolidation membership is invalid; reconcile before finalization.")
        run_history = list(dict.fromkeys([*prior_runs, *([prior_run] if prior_run else []),
                                        *([args.run_id] if args.run_id and status == "processed" else [])]))
        if run_history:
            entry["consolidation_runs"] = run_history
        if status == "processed":
            entry["integration_status"] = "pending"
        if status == "needs-review":
            entry["archive_review_stable_sha256"] = review_stable_digest(result_text)
        for key in ("capture_key", "capture_digest"):
            if key in fm:
                entry[key] = fm[key]
        if review_metadata:
            entry["review_archive_sha256"] = hashlib.sha256(result_text.encode()).hexdigest()
        journal_dir = store_directory(root, root / "state" / "finalizations")
        journal = journal_dir / f"{hashlib.sha256(note_id.encode()).hexdigest()}.json"
        transaction = {
            "version": 1, "note_id": note_id, "source": str(source), "destination": str(final),
            "original_text": original_text, "result_text": result_text, "entry": entry,
            "previous_entry": state.get("processed", {}).get(note_id),
        }
        atomic_write_text(journal, json.dumps(transaction, sort_keys=True) + "\n")
        try:
            atomic_write_text(source, result_text)
            if source != final.resolve():
                shutil.move(str(source), str(final))
            state.setdefault("processed", {})[note_id] = entry
            save_state(root, state)
        except Exception:
            recover_finalizations(root, load_state(root), rollback=True, only=journal)
            raise
        journal.unlink()
    print(final)
    return 0


def read_before_deadline(path: Path, deadline: float | None, *, max_bytes: int | None = None) -> bytes:
    if deadline is None:
        return path.read_bytes()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise EvidenceUnavailable("Evidence revalidation timed out; retry the check.")
    try:
        command = ["/usr/bin/head", "-c", str(max_bytes + 1), str(path)] if max_bytes is not None else ["/bin/cat", str(path)]
        content = subprocess.run(command, capture_output=True, check=True, timeout=remaining).stdout
        if max_bytes is not None and len(content) > max_bytes:
            raise EvidenceUnavailable("Evidence exceeds the bounded read limit; reconcile before retrying.")
        return content
    except (OSError, subprocess.SubprocessError) as exc:
        raise EvidenceUnavailable("Evidence is unavailable or timed out; retry the check.") from exc


def validate_residual_review(root: Path, notes: list[dict[str, str]], evidence: str, *, deadline: float | None = None, content: bytes | None = None) -> dict[str, int]:
    """Require an accounted-for residual queue; semantic judgments remain with the reviewer."""
    receipt = Path(evidence).expanduser().resolve()
    if not receipt.is_relative_to((root / "reports").resolve()) or not receipt.is_file():
        raise ConfigError("Review evidence must be a saved JSON receipt inside configured reports.")
    try:
        data = json.loads(content if content is not None else read_before_deadline(receipt, deadline))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ConfigError("Invalid residual review JSON.") from exc
    if (not isinstance(data, dict) or data.get("version") != 1 or data.get("scope") != "instructions-only"
            or not isinstance(data.get("sources"), list) or not isinstance(data.get("decisions"), list)):
        raise ConfigError("Residual review requires a version 1 instructions-only receipt with sources and decisions.")
    counts: dict[str, int] = {}
    for note in notes:
        if note["review_decision"] not in {"pending", "ambiguous"}:
            continue
        snapshots = [s for s in data["sources"] if isinstance(s, dict) and s.get("id") == note["id"]]
        decisions = [d for d in data["decisions"] if isinstance(d, dict) and d.get("note_id") == note["id"]]
        if len(snapshots) != 1 or len(decisions) != 1:
            raise ConfigError("Every pending note requires exactly one source snapshot and residual decision.")
        snapshot, decision = snapshots[0], decisions[0]
        if snapshot.get("path") != note["path"] or snapshot.get("sha256") != note["sha256"]:
            raise ConfigError("Stale residual review source snapshot; reassess the changed note.")
        if not isinstance(decision.get("rationale"), str) or not decision["rationale"].strip():
            raise ConfigError("Residual decision requires a grounded rationale.")
        classification = decision.get("classification")
        if classification == "needs-user":
            choices = decision.get("open_choices")
            if not isinstance(choices, list) or not choices or any(not isinstance(c, str) or not c.strip() for c in choices):
                raise ConfigError("needs-user residuals require an answerable open choice.")
        elif classification == "investigate":
            blocker = decision.get("blocker")
            targets = decision.get("targets")
            if (not isinstance(blocker, dict)
                    or any(not isinstance(blocker.get(k), str) or not blocker[k].strip() for k in ("overlap", "next_action"))
                    or not isinstance(targets, list) or not targets):
                raise ConfigError("Investigate requires exact instruction overlap, next action and target snapshots; dirty status alone is insufficient.")
            for target in targets:
                if not isinstance(target, dict) or not isinstance(target.get("path"), str):
                    raise ConfigError("Residual target requires path and sha256.")
                path = Path(target["path"]).expanduser().resolve()
                if not path.is_file() or not instruction_target(path, root):
                    raise ConfigError("Residual target must be an existing instruction file outside the learning store.")
                if hashlib.sha256(read_before_deadline(path, deadline)).hexdigest() != target.get("sha256"):
                    raise ConfigError("Stale residual instruction target; reassess ownership and exact overlap.")
        else:
            raise ConfigError("Finalize resolved technical notes before reporting; pending notes need investigate or needs-user decisions.")
        counts[classification] = counts.get(classification, 0) + 1
    return counts


def command_write_report(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    with state_update_lock(root):
        if finalization_journals(root):
            raise ConfigError("Pending finalizations must be reconciled before a completion report.")
        review = [note_summary(path) for path in sorted((root / "needs-review").glob("*.md"))]
        analysis = review_analysis(config, review)
        evidence = args.review_evidence
        if analysis["group_review_due"] and analysis["autonomous_instructions"] and not evidence:
            raise ConfigError("Joint review is due: finalize resolved notes or supply --review-evidence accounting for every residual note.")
        residuals = validate_residual_review(root, review, evidence) if evidence else {}
        run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
        path = unique_path(root / "reports" / f"{run_id}.md")
        routing_section = ""
        if routing_args_present(args):
            routing = routing_from_args(args, {}, "", Path(f"{run_id}.md"))
            if args.enforce_routing:
                enforce_routing_contract(routing)
            routing_section = routing_markdown(routing)
        lines = [
            f"# Agent Learning Consolidation {run_id}",
            "",
            f"- Generated: {now_iso()}",
            f"- Queue: inbox={len(list((root / 'inbox').glob('*.md')))}, pending-review={analysis['pending_count']}.",
            f"- Joint review threshold: {analysis['group_threshold']}; due: {str(analysis['group_review_due']).lower()}.",
            "",
            "## Summary",
            "",
            args.summary.strip(),
        ]
        if routing_section.strip():
            lines.extend(["", routing_section.strip()])
        if evidence:
            lines.extend(["", "## Residual review", "", f"- Receipt: {Path(evidence).expanduser().resolve()}."])
            lines.extend(f"- {key}: {count}." for key, count in sorted(residuals.items()))
        body = "\n".join(lines) + "\n"
        atomic_write_text(path, body)
    print(path)
    return 0


def frontmatter_from_state_entry(entry: dict[str, Any], read_archives: bool = False) -> dict[str, Any]:
    path_value = str(entry.get("path") or "")
    path = Path(path_value) if path_value else Path()
    if read_archives and path_value and path.exists():
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
    with state_update_lock(root):
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
            routing = frontmatter_from_state_entry(entry, args.read_archives)
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
            "pending_finalizations": len(finalization_journals(root)),
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
        f"- Pending finalizations: {payload.get('pending_finalizations', 0)}",
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
    notes, unavailable = bounded_note_inventory(root, sorted((root / "needs-review").glob("*.md")))
    if unavailable:
        raise ConfigError("Review inventory is incomplete; retry unavailable notes before notification.")
    return [note for note in notes if note["review_decision"] in {"pending", "ambiguous"}]


def inbox_notes(root: Path) -> list[dict[str, str]]:
    notes, unavailable = bounded_note_inventory(root, sorted((root / "inbox").glob("*.md")))
    if unavailable:
        raise ConfigError("Inbox inventory is incomplete; retry unavailable notes before notification.")
    return notes


def notification_body(config: dict[str, str], root: Path) -> tuple[str, str] | None:
    pipeline = pipeline_store(root)
    with state_update_lock(root):
        payload = pipeline.digest(pipeline.load(), pending_review_notes(root), inbox=inbox_notes(root))
    return (payload["subject"], payload["body"]) if payload else None


def send_msmtp(recipient: str, subject: str, body: str, html_body: str = "", sender: str = "") -> None:
    if not shutil.which("msmtp"):
        raise SystemExit("msmtp is not available; configure Gmail connector or install msmtp.")
    if not re.fullmatch(r"[^\s<>;,\r\n]+@[^\s<>;,\r\n]+\.[^\s<>;,\r\n]+", recipient):
        raise ConfigError("Expected one configured email recipient.")
    if sender and not re.fullmatch(r"[^\s<>;,\r\n]+@[^\s<>;,\r\n]+\.[^\s<>;,\r\n]+", sender):
        raise ConfigError("Expected one configured email sender.")
    message = EmailMessage(policy=SMTP)
    if sender:
        message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body, cte="base64")
    if html_body:
        message.add_alternative(html_body, subtype="html", cte="base64")
    command = ["msmtp", "--from", sender] if sender else ["msmtp"]
    subprocess.run([*command, "--", recipient], input=message.as_string(),
                   text=True, check=True, timeout=60)


def is_placeholder_email(value: str) -> bool:
    return bool(PLACEHOLDER_EMAIL_PATTERN.match(value.strip()))


def command_notify(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    pipeline = pipeline_store(root)
    with state_update_lock(root):
        state = pipeline.load()
        if state["outbox"] and (not args.check_digest or state["outbox"]["digest_id"] != args.check_digest):
            raise ConfigError("A pending notification needs provider reconciliation before another send.")
        payload = pipeline.digest(state, pending_review_notes(root), inbox=inbox_notes(root))
    if payload is None:
        print("NO_ACTION_REQUIRED")
        return 0
    if args.check_digest and args.check_digest != payload["digest_id"]:
        raise ConfigError("Digest changed; prepare the current state before sending.")
    if args.send_msmtp:
        recipient = config.get("AGENT_LEARNING_EMAIL", "").strip()
        if not recipient or is_placeholder_email(recipient):
            raise SystemExit("AGENT_LEARNING_EMAIL is not configured for sending.")
        if not shutil.which("msmtp"):
            raise ConfigError("msmtp is unavailable; no notification was reserved.")
        sender = config.get("AGENT_LEARNING_EMAIL_FROM", "").strip()
        if sender and (is_placeholder_email(sender) or not re.fullmatch(r"[^\s<>;,\r\n]+@[^\s<>;,\r\n]+\.[^\s<>;,\r\n]+", sender)):
            raise ConfigError("AGENT_LEARNING_EMAIL_FROM must be a valid configured sender.")
        pipeline.reserve(payload, lambda value: pipeline.digest(value, pending_review_notes(root), inbox=inbox_notes(root)))
        # An exception preserves the outbox: provider acceptance may be uncertain.
        send_msmtp(recipient, payload["subject"], payload["body"], payload["html"], sender)
        pipeline.acknowledge(payload["digest_id"], "msmtp exited successfully")
        print(f"SENT {recipient}")
        return 0
    if args.prepare:
        pipeline.reserve(payload, lambda value: pipeline.digest(value, pending_review_notes(root), inbox=inbox_notes(root)))
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


def command_send_email(args: argparse.Namespace) -> int:
    """Send an explicitly prepared plain-text operational email through msmtp."""
    config = load_config(args.config)
    recipient = config.get("AGENT_LEARNING_EMAIL", "").strip()
    if not recipient or is_placeholder_email(recipient):
        raise SystemExit("AGENT_LEARNING_EMAIL is not configured for sending.")
    body_path = Path(args.body_file).expanduser()
    if not body_path.is_file():
        raise SystemExit(f"Email body file is missing: {body_path}")
    body = body_path.read_text(encoding="utf-8")
    send_msmtp(recipient, args.subject, body)
    print(f"SENT {recipient}")
    return 0


def pipeline_store(root: Path) -> Pipeline:
    def read_finalized(path: Path) -> dict[str, str] | None:
        try:
            return note_summary(path, read_note_bounded(path))
        except (OSError, UnicodeError, subprocess.CalledProcessError):
            return None
    def verify_sealed(pipeline: Pipeline, state: dict, cycle: dict, deadline: float) -> list[dict]:
        def remaining() -> float:
            result = deadline - time.monotonic()
            if result <= 0:
                raise ValueError("Sealed evidence is unavailable or timed out; retry.")
            return result
        snapshot = cycle.get("sealed_snapshot")
        if not isinstance(snapshot, dict):
            return [{"id": cycle["run_id"], "kind": "legacy-seal", "expected_sha256": None,
                     "observed_sha256": fingerprint(cycle), "reason": "The legacy seal lacks per-source evidence."}]
        reference = {"run_id": cycle["run_id"], "initial_fingerprint": cycle["initial_fingerprint"]}
        runs = {key: value for key, value in state["runs"].items() if value.get("goal_baseline") == reference}
        run_fingerprint = fingerprint(runs)
        raw = read_store_state(root, "processed.json", remaining())
        if raw is None:
            raise ValueError("Processed state is unavailable; retry before advancing.")
        index = json.loads(raw)
        entries = index.get("processed")
        if not isinstance(entries, dict):
            raise ValueError("Processed state is malformed; reconcile before advancing.")
        drifts: list[dict] = []
        if run_fingerprint != snapshot.get("runs_fingerprint"):
            drifts.append({"id": cycle["run_id"], "kind": "run-record", "expected_sha256": snapshot.get("runs_fingerprint"),
                           "observed_sha256": run_fingerprint, "reason": "The sealed run records changed."})
        def check_file(item_id: str, kind: str, ref: dict) -> tuple[bool, dict | None, Any]:
            return check_sealed_file(root, item_id, kind, ref, remaining())
        def parallel(tasks: dict, function) -> dict:
            executor = ThreadPoolExecutor(max_workers=8)
            try:
                futures = {executor.submit(function, *args): key for key, args in tasks.items()}
                done, unfinished = wait(futures, timeout=remaining())
                if unfinished:
                    raise ValueError("Sealed evidence is unavailable or timed out; retry.")
                return {futures[future]: future.result() for future in done}
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        file_tasks = {}
        active_entries = {}
        for note_id, original in snapshot["outcomes"].items():
            entry = entries.get(note_id)
            expected = original["entry_fingerprint"]
            observed = fingerprint(entry)
            if observed != expected:
                drifts.append({"id": note_id, "kind": "outcome", "expected_sha256": expected,
                               "observed_sha256": observed, "reason": "The saved outcome changed."})
                continue
            active_entries[note_id] = entry
            archive_kind = "archive-review" if entry.get("status") == "needs-review" else "archive-processed"
            file_tasks[(note_id, "archive")] = (note_id, archive_kind, original["archive"])
            if entry.get("status") == "processed":
                file_tasks[(note_id, "integration-receipt")] = (note_id, "integration-receipt", entry.get("integration_receipt", {}))
        for note_id, review in snapshot["reviews"].items():
            file_tasks[(note_id, "review")] = (note_id, "review", {"path": review["path"], "sha256": review["sha256"]})
        bindings = snapshot.get("review_issues", {})
        binding_version = snapshot.get("review_binding_version")
        anchor_version = cycle.get("review_binding_version")
        # Entirely unmarked snapshots retain legacy behavior; this local state is not authenticated.
        if (("review_binding_version" in snapshot or "review_binding_version" in cycle or "review_issues" in snapshot)
                and (type(binding_version) is not int or binding_version != 1
                     or type(anchor_version) is not int or anchor_version != 1)):
            raise ValueError("Unknown sealed review binding version; reconcile the cycle.")
        if (not isinstance(bindings, dict) or (binding_version == 1
                and set(bindings) != set(snapshot["reviews"]))):
            raise ValueError("Sealed review issue bindings are incomplete; reconcile the cycle.")
        for note_id, binding in bindings.items():
            issue = state.get("issues", {}).get(binding["id"])
            observed = fingerprint(issue)
            if observed != binding["fingerprint"]:
                drifts.append({"id": note_id, "kind": "review-issue", "expected_sha256": binding["fingerprint"],
                               "observed_sha256": observed, "reason": "The linked review classification changed."})
        receipt = snapshot.get("review_receipt")
        if snapshot["reviews"] and not receipt:
            raise ValueError("Sealed review receipt is missing; reconcile the cycle.")
        if receipt:
            file_tasks[("review-receipt", "review-receipt")] = ("review-receipt", "review-receipt", receipt)
        file_results = parallel(file_tasks, check_file)
        drifts.extend(result[1] for result in file_results.values() if result[1] is not None)
        receipt_tasks = {}
        note_receipts = {}
        for note_id, entry in active_entries.items():
            if entry.get("status") != "processed" or not file_results[(note_id, "archive")][0] or not file_results[(note_id, "integration-receipt")][0]:
                continue
            ref = entry["integration_receipt"]
            key = json.dumps(ref, sort_keys=True)
            note_receipts[note_id] = key
            receipt_tasks[key] = (ref,)
        def inspect(ref: dict) -> dict:
            return inspect_receipt_bounded(ref, timeout=min(8, remaining()))
        receipt_results = parallel(receipt_tasks, inspect)
        for note_id, key in note_receipts.items():
            result = receipt_results[key]
            if result["status"] == "unavailable":
                raise ValueError("Sealed integration evidence is unavailable; retry.")
            if result["status"] != "verified" or note_id not in result.get("note_ids", []):
                ref = active_entries[note_id]["integration_receipt"]
                drifts.append({"id": note_id, "kind": "integration", "expected_sha256": ref.get("sha256"),
                               "observed_sha256": ref.get("sha256"), "reason": "The unchanged integration receipt no longer verifies.",
                               "detail": result.get("reason", "Receipt does not cover this note.")})
        reviews_current = all(file_results[(note_id, "review")][0] for note_id in snapshot["reviews"])
        receipt_current = file_results[("review-receipt", "review-receipt")][0] if receipt else True
        if reviews_current and receipt_current and receipt:
            receipt_bytes = file_results[("review-receipt", "review-receipt")][2]
            notes = [file_results[(note_id, "review")][2] for note_id in snapshot["reviews"]]
            for note in notes:
                try:
                    validate_residual_review(root, [note], receipt["path"], deadline=deadline, content=receipt_bytes)
                except EvidenceUnavailable:
                    raise
                except ConfigError as exc:
                    drifts.append({"id": note["id"], "kind": "review-contract", "expected_sha256": receipt["sha256"],
                                   "observed_sha256": None, "reason": "The residual review evidence no longer verifies.", "detail": str(exc)})
            valid, _, _ = check_file("review-receipt", "review-receipt", receipt)
            if not valid:
                raise ValueError("Review receipt changed during verification; retry.")
        remaining()
        return drifts
    def verify_recovery(pipeline: Pipeline, state: dict, record: dict, deadline: float) -> dict:
        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise ValueError("Recovery evidence timed out; retry.")
            return value
        def parallel_check(items: list, fn) -> None:
            if not items:
                return
            executor = ThreadPoolExecutor(max_workers=min(8, len(items)))
            try:
                futures = [executor.submit(fn, item) for item in items]
                done, pending = wait(futures, timeout=remaining())
                if pending:
                    raise ValueError("Recovery evidence timed out; retry.")
                for future in done:
                    future.result()
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        run_id, followup_ids = record.get("run_id"), record.get("followup_ids")
        run = state.get("runs", {}).get(run_id)
        if (not isinstance(run_id, str) or not isinstance(followup_ids, list) or not followup_ids
                or len(followup_ids) > 32 or any(not isinstance(key, str) or key == run_id for key in followup_ids)
                or len(set(followup_ids)) != len(followup_ids) or not isinstance(run, dict)
                or run.get("id") != run_id or run.get("status") not in {"partial", "failed"}
                or fingerprint(run) != record.get("run_fingerprint")
                or not isinstance(record.get("followup_fingerprints"), dict)
                or set(record["followup_fingerprints"]) != set(followup_ids)):
            raise ValueError("Recovery requires unchanged terminal and completed follow-up runs.")
        followups = {key: state.get("runs", {}).get(key) for key in followup_ids}
        for key, followup in followups.items():
            if (not isinstance(followup, dict) or followup.get("id") != key
                    or followup.get("status") != "completed"
                    or fingerprint(followup) != record["followup_fingerprints"][key]):
                raise ValueError("Recovery requires unchanged terminal and completed follow-up runs.")
            try:
                if datetime.fromisoformat(followup["started_at"]) < datetime.fromisoformat(run["finished_at"]):
                    raise ValueError("The follow-up began before the interrupted run finished.")
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Recovery needs a later completed follow-up.") from exc
        initial = run.get("initial_inbox")
        unavailable = run.get("initial_unavailable", [])
        if (not isinstance(initial, list) or unavailable
                or run.get("initial_fingerprint") != fingerprint({"inbox": initial, "unavailable": unavailable})):
            raise ValueError("Recovery needs a complete unchanged original inbox snapshot.")
        prior_assessment = run.get("initial_assessment")
        if (not isinstance(prior_assessment, dict) or not prior_assessment.get("known")
                or prior_assessment.get("unavailable") or not isinstance(prior_assessment.get("unassessed"), list)):
            raise ValueError("Original completion assessment is unavailable; reconcile its scope first.")
        if (any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in initial)
                or len({item["id"] for item in initial}) != len(initial)
                or any(item not in initial for item in prior_assessment["unassessed"])):
            raise ValueError("Original completion assessment does not match its inbox snapshot.")
        if run.get("weekly_due"):
            weekly = state.get("weekly", {})
            if (weekly.get("cycle") != run.get("cycle") or weekly.get("run_id") not in [run_id, *followup_ids]
                    or not isinstance(weekly.get("snapshot"), dict)):
                raise ValueError("The interrupted run's weekly audit is not verified.")
            valid, _, weekly_raw = check_sealed_file(root, run_id, "goal-receipt", weekly["snapshot"], remaining())
            try:
                weekly_payload = json.loads(weekly_raw) if valid else None
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("The interrupted run's weekly audit is not verified.") from exc
            if (not isinstance(weekly_payload, dict) or weekly_payload.get("run_id") != weekly["run_id"]
                    or weekly_payload.get("cycle") != weekly["cycle"]):
                raise ValueError("The interrupted run's weekly audit is not verified.")
        if any(issue.get("last_assessed_run") == run_id
               and (issue.get("status") in {"candidate", "investigate"} or issue.get("revalidation_required"))
               for issue in state.get("issues", {}).values() if isinstance(issue, dict)):
            raise ValueError("The interrupted run still has unresolved technical findings.")
        valid, _, raw = check_sealed_file(root, run_id, "goal-receipt", record["report"], remaining())
        if not valid:
            raise ValueError("Recovery report is missing or changed.")
        try:
            report = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise ValueError("Recovery report is malformed.") from exc
        if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
                or report["schema_version"] != 1
                or report.get("run_id") != run_id or report.get("followup_ids") != followup_ids
                or report.get("run_fingerprint") != record["run_fingerprint"]
                or not isinstance(report.get("resolution"), str) or not report["resolution"].strip()
                or not isinstance(report.get("evidence"), list) or not report["evidence"]
                or len(report["evidence"]) > 32 or not isinstance(report.get("blocker_issue_ids"), list)
                or len(report["blocker_issue_ids"]) > 32
                or (not report["blocker_issue_ids"]
                    and (run.get("blocker_scope") != "notes" or not prior_assessment["unassessed"]))
                or any(not isinstance(key, str) for key in report["blocker_issue_ids"])
                or len(set(report["blocker_issue_ids"])) != len(report["blocker_issue_ids"])):
            raise ValueError("Recovery report must bind the runs, explain the original blocker and cite evidence.")
        def check_reference(ref: dict) -> None:
            if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                    or not isinstance(ref.get("sha256"), str)
                    or not check_sealed_file(root, run_id, "report-snapshot", ref, remaining())[0]):
                raise ValueError("Recovery supporting evidence is unavailable or changed.")
        parallel_check(report["evidence"], check_reference)
        evidence_paths = {str(Path(ref["path"]).resolve()) for ref in report["evidence"]}
        at_finish = run.get("operational_blocker_ids", [])
        if not isinstance(at_finish, list) or any(not isinstance(key, str) for key in at_finish):
            raise ValueError("The original blocker snapshot is malformed.")
        known_blockers = set(at_finish) | {key for key, issue in state.get("issues", {}).items()
                                          if isinstance(issue, dict) and issue.get("kind") == "operational"
                                          and issue.get("recovery_run_id") == run_id}
        if set(report["blocker_issue_ids"]) != known_blockers:
            raise ValueError("Recovery report must include every known original operational blocker.")
        for issue_id in report["blocker_issue_ids"]:
            issue = state.get("issues", {}).get(issue_id)
            if (not isinstance(issue, dict) or issue.get("kind") != "operational"
                    or issue.get("status") not in {"resolved", "justified"}
                    or issue.get("recovery_run_id") != run_id
                    or issue.get("recovery_run_fingerprint") != record["run_fingerprint"]
                    or issue.get("revalidation_required") or not str(issue.get("resolution", "")).strip()
                    or not isinstance(issue.get("evidence"), list) or not issue["evidence"]
                    or any(not isinstance(item, str) or str(Path(item).resolve()) not in evidence_paths
                           for item in issue["evidence"])):
                raise ValueError("Each original blocker needs a resolved run-bound issue and current evidence.")
        for key, followup in followups.items():
            followup_report = followup.get("report")
            if (not isinstance(followup_report, dict)
                    or not check_sealed_file(root, key, "report-snapshot", followup_report, remaining())[0]):
                raise ValueError("A completed follow-up report is unavailable or changed.")
        original_report = run.get("report")
        if (original_report is not None and (not isinstance(original_report, dict)
                or not check_sealed_file(root, run_id, "report-snapshot", original_report, remaining())[0])):
            raise ValueError("The original run report is unavailable or changed.")
        raw_index = read_store_state(root, "processed.json", remaining())
        if raw_index is None:
            raise ValueError("Processed index is unavailable.")
        index = json.loads(raw_index)
        if not isinstance(index, dict) or not isinstance(index.get("processed", {}), dict):
            raise ValueError("Processed index is malformed.")
        entries = index.get("processed", {})
        assessment = pipeline.initial_assessment(run, timeout=remaining(), processed=entries)
        if not assessment["known"] or assessment["unassessed"] or assessment["unavailable"]:
            raise ValueError("Original sources remain unassessed or unavailable.")
        for source in prior_assessment["unassessed"]:
            if (not isinstance(source, dict) or source.get("id") not in entries
                    or entries[source["id"]].get("consolidation_run") not in followup_ids):
                raise ValueError("The follow-up does not cover each previously unassessed source.")
        for source in initial:
            entry = entries[source["id"]]
            if entry.get("status") != "processed" and (not isinstance(entry.get("rationale"), str)
                                                        or not entry["rationale"].strip()):
                raise ValueError("Original review or rejection lacks a rationale.")
        integrated = [source for source in initial if entries[source["id"]].get("status") == "processed"]
        groups: dict[str, list[dict]] = {}
        for source in integrated:
            key = json.dumps(entries[source["id"]].get("integration_receipt", {}), sort_keys=True)
            groups.setdefault(key, []).append(source)
        def check_integration(group: list[dict]) -> None:
            cache: dict = {}
            for source in group:
                problem = integration_problem(entries[source["id"]], source["id"], cache,
                                              timeout=min(8, remaining()), state=index)
                if problem:
                    raise ValueError("Original source integration is not current: " + problem)
        parallel_check(list(groups.values()), check_integration)
        ref = {"path": record["report"]["path"], "sha256": hashlib.sha256(raw).hexdigest()}
        refs = [ref, *report["evidence"], *(value["report"] for value in followups.values())]
        if original_report is not None:
            refs.append(original_report)
        if run.get("weekly_due"):
            refs.append(state["weekly"]["snapshot"])
        parallel_check(refs, check_reference)
        if fingerprint(pipeline.initial_assessment(run, timeout=remaining(), processed=entries)) != fingerprint(assessment):
            raise ValueError("Original source evidence changed during recovery verification; retry.")
        parallel_check(list(groups.values()), check_integration)
        parallel_check(refs, check_reference)
        if fingerprint(pipeline.initial_assessment(run, timeout=remaining(), processed=entries)) != fingerprint(assessment):
            raise ValueError("Original source evidence changed during recovery verification; retry.")
        latest = read_store_state(root, "processed.json", remaining())
        if latest != raw_index:
            raise ValueError("Processed outcomes changed during recovery verification; retry.")
        return {**record, "report": ref}

    return Pipeline(root, lambda: state_update_lock(root), read_finalized, verify_sealed, verify_recovery)


def pipeline_roots(root: Path, added: list[str] | None = None) -> list[str]:
    paths = [str(path.expanduser().resolve()) for path in DEFAULT_AUDIT_TARGETS]
    paths.append(str(DEFAULT_AUDIT_SKILLS_DIR.resolve()))
    with state_update_lock(root):
        indexed = load_state(root).get("processed", {})
        for entry in indexed.values():
            if not isinstance(entry, dict):
                continue
            for value in coerce_list(entry.get("prevention_targets")) + coerce_list(entry.get("detection_targets")):
                target = concrete_routing_target_path(value)
                if target is not None and target.is_file() and instruction_target(target, root):
                    paths.append(str(target))
    for value in added or []:
        target = Path(value).expanduser().resolve(strict=True)
        if not target.is_file() or not instruction_target(target, root):
            raise ConfigError("Changed targets must be existing instruction files outside the store.")
        paths.append(str(target))
    paths = sorted(set(paths))
    if any(not Path(value).exists() for value in paths):
        raise ConfigError("Audit roots are unavailable; this is not a clean scan.")
    return paths


def pipeline_inventory(root: Path) -> list[dict[str, str]]:
    files: set[Path] = set()
    for value in pipeline_roots(root):
        path = Path(value)
        if path.is_dir():
            files.update(path.glob("**/SKILL.md"))
            files.update(path.glob("**/AGENTS.md"))
        else:
            files.add(path)
    return [snapshot(str(path)) for path in sorted(files) if path.is_file()]


def command_pipeline(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    pipeline = pipeline_store(root)
    def pipeline_before(deadline: float) -> dict:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceUnavailable("Pipeline state timed out; retry.")
        raw = read_store_state(root, "pipeline.json", remaining)
        if raw is None:
            raise EvidenceUnavailable("Pipeline state is unavailable; retry.")
        return pipeline.load(raw)
    operation = args.command
    if operation == "pipeline-configure":
        pipeline.configure(args.ordinary_thread, args.release_thread, args.language)
        result = {"ordinary": chat_url(args.ordinary_thread), "release": chat_url(args.release_thread)}
    elif operation == "pipeline-status":
        result = pipeline.status()
    elif operation == "pipeline-begin":
        initial_inbox, unavailable = bounded_note_inventory(root, sorted((root / "inbox").glob("*.md")), timeout=10)
        if unavailable and not initial_inbox:
            raise ConfigError("Inbox sources are present but none are readable; retry the next run after storage recovery.")
        result = pipeline.begin(args.run_id, args.runner, pipeline_inventory(root),
                                [{"id": n["id"], "path": n["path"], "sha256": n["sha256"]}
                                 for n in initial_inbox], unavailable, getattr(args, "goal_mode", None),
                                getattr(args, "dispatch_evidence", None))
    elif operation == "pipeline-dispatch-bind":
        result = pipeline.bind_dispatch(args.run_id, args.dispatch_evidence)
    elif operation == "pipeline-goal-attempt":
        result = pipeline.mark_goal_attempt(args.run_id, args.goal_thread_id)
        recorded_second = int(datetime.fromisoformat(result["recorded_at"]).timestamp())
        deadline = time.monotonic() + 3
        while int(time.time()) <= recorded_second:
            if time.monotonic() >= deadline:
                raise ConfigError("Goal attempt was saved, but the clock did not advance; reconcile before creation.")
            time.sleep(0.05)
    elif operation == "pipeline-goal-bind":
        result = pipeline.bind_goal(args.run_id, args.initial_fingerprint, args.goal_thread_id, args.goal_receipt)
    elif operation == "pipeline-goal-reopen":
        result = pipeline.reopen_goal(args.run_id, args.initial_fingerprint, args.sealed_at, args.reason, args.goal_receipt)
    elif operation == "pipeline-goal-advance":
        result = pipeline.advance_goal(args.run_id, args.initial_fingerprint, args.sealed_at, args.goal_receipt)
    elif operation == "pipeline-run-reconcile":
        result = pipeline.reconcile_run(args.run_id, args.followup_run_id, args.report)
    elif operation == "pipeline-goal-status":
        with state_update_lock(root):
            state_deadline = time.monotonic() + GOAL_SEAL_TIMEOUT_SECONDS
            pipeline_state = pipeline_before(state_deadline)
            pipeline.check_reconciliation_ledger(pipeline_state)
            run = pipeline_state["runs"].get(args.run_id)
            if not run or "initial_inbox" not in run:
                raise ConfigError("Goal baseline run or its initial inbox is unavailable.")
            baseline = fingerprint({"inbox": run["initial_inbox"], "unavailable": run.get("initial_unavailable", [])})
            if baseline != args.initial_fingerprint or run.get("initial_fingerprint") != baseline:
                raise ConfigError("Goal baseline fingerprint does not match the saved initial inbox.")
            index = load_state(root, deadline=state_deadline)
            state_hash = fingerprint(index)
            pipeline_hash = fingerprint(pipeline_state)
            reference = {"run_id": args.run_id, "initial_fingerprint": baseline}
            if run.get("goal_baseline") != reference:
                raise ConfigError("Goal baseline has not been bound to a pipeline cycle.")
            scopes, unknown_runs, deadline = [], [], time.monotonic() + 3
            cycle = pipeline_state.get("goal_cycle", {})
            active_cycle = cycle.get("status") == "active" and all(cycle.get(k) == v for k, v in reference.items())
            baseline_started = datetime.fromisoformat(run["started_at"])
            for scope_id, scope in pipeline_state["runs"].items():
                if scope.get("goal_baseline") != reference:
                    if scope.get("goal_mode") == "manual" and not scope.get("goal_baseline"):
                        continue
                    if active_cycle:
                        try:
                            if datetime.fromisoformat(scope["started_at"]) >= baseline_started:
                                unknown_runs.append(scope_id)
                        except (KeyError, TypeError, ValueError):
                            unknown_runs.append(scope_id)
                    continue
                try:
                    scope_hash = fingerprint({"inbox": scope["initial_inbox"], "unavailable": scope.get("initial_unavailable", [])})
                    if scope.get("initial_fingerprint") != scope_hash:
                        raise ValueError("Unbound initial snapshot")
                    status = scope["status"]
                    if status not in {"running", "completed", "partial", "failed"}:
                        raise ValueError("Unknown run status")
                    if status != "running" and datetime.fromisoformat(scope["finished_at"]) < datetime.fromisoformat(scope["started_at"]):
                        raise ValueError("Invalid run finish time")
                except (KeyError, TypeError, ValueError):
                    unknown_runs.append(scope_id)
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    assessed = {"known": False, "unassessed": scope["initial_inbox"],
                                "unavailable": scope.get("initial_unavailable", [])}
                else:
                    assessed = pipeline.initial_assessment(scope, timeout=remaining, processed=index.get("processed", {}))
                scopes.append({"run_id": scope_id, "started_at": scope["started_at"], "status": scope["status"], "initial_fingerprint": scope_hash,
                               "assessment": assessed, "sources": scope["initial_inbox"]})
            scopes.sort(key=lambda item: datetime.fromisoformat(item["started_at"]))
        unassessed = {item["id"] for scope in scopes for item in scope["assessment"]["unassessed"]}
        receipts, review, rejected, cache = [], [], [], {}
        receipt_deadline = time.monotonic() + 30
        sources = {source["id"]: source for scope in scopes for source in scope["sources"]}
        for source in sources.values():
            note_id = source["id"]
            if note_id in unassessed:
                continue
            entry = index.get("processed", {}).get(note_id, {})
            if entry.get("status") == "processed":
                problem = integration_problem(entry, note_id, cache, timeout=min(8, receipt_deadline - time.monotonic()),
                                              state=index)
                receipts.append({"note_id": note_id, "status": "pending" if problem else "verified", "reason": problem})
            elif entry.get("status") == "needs-review":
                review.append({"note_id": note_id, "rationale": entry.get("rationale", "")})
            elif entry.get("status") == "rejected":
                rejected.append({"note_id": note_id, "rationale": entry.get("rationale", "")})
        with state_update_lock(root):
            state_deadline = time.monotonic() + GOAL_SEAL_TIMEOUT_SECONDS
            if fingerprint(load_state(root, deadline=state_deadline)) != state_hash:
                raise ConfigError("Processed state changed during Goal verification; retry the check.")
            if fingerprint(pipeline_before(state_deadline)) != pipeline_hash:
                raise ConfigError("Pipeline state changed during Goal verification; retry the check.")
        for scope in scopes:
            scope.pop("sources")
        result = {"run_id": args.run_id, "initial_fingerprint": baseline, "runs": scopes,
                  "unknown_runs": unknown_runs,
                  "accounting_complete": bool(scopes) and not unknown_runs and all(
                      scope["status"] != "running" and scope["assessment"]["known"]
                      and not scope["assessment"]["unassessed"] and not scope["assessment"]["unavailable"]
                      for scope in scopes),
                  "receipts": receipts, "needs_review": review, "rejected": rejected,
                  "notice": "This checks bound note outcomes only; it does not establish semantic approval, resolve review decisions, or certify pipeline completion."}
        if args.seal:
            if (not result["accounting_complete"] or any(item["status"] != "verified" for item in receipts)
                    or any(not str(item.get("rationale", "")).strip() for item in review + rejected)):
                raise ConfigError("Goal cycle still has unaccounted sources, running runs, or pending integration receipts.")
            with state_update_lock(root):
                seal_deadline = time.monotonic() + GOAL_SEAL_TIMEOUT_SECONDS
                def seal_budget() -> float:
                    remaining = seal_deadline - time.monotonic()
                    if remaining <= 0:
                        raise ConfigError("Goal sealing timed out; retry the check.")
                    return remaining
                current_state = pipeline_before(seal_deadline)
                if fingerprint(current_state) != pipeline_hash or fingerprint(load_state(root, deadline=seal_deadline)) != state_hash:
                    raise ConfigError("Goal evidence changed before sealing; retry the check.")
                cycle = current_state.get("goal_cycle", {})
                if cycle.get("status") not in {"active", "sealed"} or any(cycle.get(k) != v for k, v in reference.items()):
                    raise ConfigError("The requested Goal cycle is not active.")
                if not args.goal_receipt:
                    raise ConfigError("Sealing requires a current active native Goal receipt.")
                goal_ref, native_goal = pipeline.goal_evidence(args.goal_receipt, reference, cycle.get("goal_thread_id", ""), "active", deadline=seal_deadline)
                if native_goal["createdAt"] != cycle.get("goal_created_at"):
                    raise ConfigError("Native Goal identity changed; reconcile before sealing.")
                for scope in scopes:
                    fresh = pipeline.initial_assessment(current_state["runs"][scope["run_id"]], timeout=seal_budget(),
                                                        processed=index.get("processed", {}))
                    if fingerprint(fresh) != fingerprint(scope["assessment"]):
                        raise ConfigError("Goal source evidence changed during sealing; retry the check.")
                if receipts:
                    audit_command = [sys.executable, __file__, "--config", str(args.config),
                                     "integration-audit", "--require-verified"]
                    for item in receipts:
                        audit_command.extend(["--note-id", item["note_id"]])
                    try:
                        subprocess.run(audit_command, capture_output=True, check=True, timeout=seal_budget())
                    except (OSError, subprocess.SubprocessError) as exc:
                        raise ConfigError("Goal integration evidence changed or timed out during sealing; retry the check.") from exc
                review_paths = sorted((root / "needs-review").glob("*.md"))
                review_notes, unavailable = bounded_note_inventory(root, review_paths, timeout=seal_budget())
                if unavailable:
                    raise ConfigError("Review inventory is incomplete; retry before sealing the Goal.")
                if any(note["review_decision"] not in {"pending", "ambiguous"} for note in review_notes):
                    raise ConfigError("Explicit review decisions must be processed before sealing the Goal.")
                policy = review_analysis(config, review_notes)
                selected = review_notes if policy["group_review_due"] else [
                    note for note in review_notes if note["id"] in {item["note_id"] for item in review}]
                review_issues = {}
                if selected:
                    if not args.review_evidence:
                        raise ConfigError("Goal review requires a current residual review receipt.")
                    review_path = Path(args.review_evidence).expanduser().resolve()
                    review_deadline = seal_deadline
                    review_bytes = read_before_deadline(review_path, review_deadline)
                    review_ref = {"path": str(review_path), "sha256": hashlib.sha256(review_bytes).hexdigest()}
                    validate_residual_review(root, selected, args.review_evidence, deadline=review_deadline, content=review_bytes)
                    decisions = {item["note_id"]: item["classification"]
                                 for item in json.loads(review_bytes)["decisions"]
                                 if isinstance(item, dict) and isinstance(item.get("note_id"), str)
                                 and isinstance(item.get("classification"), str)}
                    for note in selected:
                        if note["review_decision"] not in {"pending", "ambiguous"}:
                            continue
                        linked = next((issue for _, issue in sorted(current_state.get("issues", {}).items())
                            if isinstance(issue, dict)
                            and issue.get("status") == decisions[note["id"]]
                            and not issue.get("revalidation_required")
                            and any(ref.get("note_id") == note["id"]
                                    and isinstance(ref.get("path"), str)
                                    and Path(ref["path"]).resolve() == Path(note["path"]).resolve()
                                    and ref.get("sha256") == note["sha256"]
                                    for ref in issue.get("sources", []) if isinstance(ref, dict))), None)
                        if linked is None:
                            raise ConfigError(f"Persist residual classification for {note['id']} as a source-linked issue before sealing.")
                        review_issues[note["id"]] = {"id": linked["id"], "fingerprint": fingerprint(linked)}
                    if read_before_deadline(review_path, review_deadline) != review_bytes:
                        raise ConfigError("Goal review evidence changed during sealing; retry the check.")
                    if cycle["status"] == "sealed" and cycle.get("review_receipt") != review_ref:
                        raise ConfigError("Sealed Goal review evidence changed; reopen the cycle to reassess.")
                    cycle["review_receipt"] = review_ref
                    result["review_receipt"] = review_ref
                latest_notes, unavailable = bounded_note_inventory(root, sorted((root / "needs-review").glob("*.md")), timeout=seal_budget())
                if unavailable or fingerprint([(n["id"], n["path"], n["sha256"], n["review_decision"]) for n in latest_notes]) != fingerprint(
                        [(n["id"], n["path"], n["sha256"], n["review_decision"]) for n in review_notes]):
                    raise ConfigError("Review sources changed or became unavailable during Goal sealing; retry the check.")
                if cycle["status"] == "active":
                    seal_budget()
                    outcomes = {}
                    for note_id in sorted(sources):
                        entry = index["processed"][note_id]
                        outcomes[note_id] = {"entry_fingerprint": fingerprint(entry),
                                             "archive": {"path": entry["path"], "sha256": entry["archive_sha256"]}}
                    snapshot = {"runs_fingerprint": fingerprint({scope["run_id"]: current_state["runs"][scope["run_id"]] for scope in scopes}),
                                "outcomes": outcomes,
                                "reviews": {n["id"]: {"path": n["path"], "sha256": n["sha256"], "review_decision": n["review_decision"]} for n in selected},
                                "review_binding_version": 1,
                                "review_issues": review_issues,
                                "review_receipt": cycle.get("review_receipt")}
                    cycle.update(status="sealed", sealed_at=now_iso(), seal_goal_receipt=goal_ref,
                                 review_binding_version=1, sealed_snapshot=snapshot)
                    pipeline.save(current_state)
                result["sealed"] = True
                result["sealed_at"] = cycle["sealed_at"]
                result["goal_update_required"] = True
    elif operation == "pipeline-issue":
        issue = read_json_input(args.from_json)
        for ref in issue.get("sources", []):
            if not isinstance(ref, dict) or not ref.get("note_id"):
                raise ConfigError("Review sources need an explicit note ID.")
            source = validate_note_source(root, Path(ref["path"]))
            summary = note_summary(source)
            if summary["id"] != ref["note_id"] or (
                summary["review_decision"] not in {"pending", "ambiguous"}
                and issue.get("status") not in {"resolved", "justified"}
            ):
                raise ConfigError("Explicit human decisions take precedence over pipeline classifications.")
        result = pipeline.update_issue(issue, getattr(args, "run_id", None))
    elif operation == "pipeline-finish":
        if args.status == "completed":
            if (getattr(args, "require_integration", False)
                    or config.get("AGENT_LEARNING_REQUIRE_INTEGRATION", "0") == "1"):
                run = pipeline.active(pipeline.load(), args.run_id)
                require_run_integrations(root, run)
            for folder in ("inbox", "needs-review"):
                _, unavailable = bounded_note_inventory(root, sorted((root / folder).glob("*.md")))
                if unavailable:
                    raise ConfigError("Current note inventory is incomplete; completion is not verified.")
        inventory = pipeline_inventory(root) if args.status == "completed" else None
        result = pipeline.finish(args.run_id, args.status, args.summary, args.report, args.target or [], inventory,
                                 getattr(args, "blocker_scope", None))
    elif operation == "pipeline-release-status":
        result = pipeline.weekly_release_status(getattr(args, "release_version", None), Path.cwd(),
                                                getattr(args, "release_assessment", None),
                                                getattr(args, "release_base", "origin/main"))
        if config.get("AGENT_LEARNING_REQUIRE_INTEGRATION", "0") == "1":
            require_run_integrations(root, result["consolidation"])
    elif operation == "pipeline-release-assessment":
        result = release_assessment_skeleton(pipeline.status(), Path.cwd(), args.release_base)
    elif operation == "notify-ack":
        pipeline.acknowledge(args.digest_id, args.receipt, not args.not_delivered)
        result = {"acknowledged": args.digest_id, "delivered": not args.not_delivered}
    else:
        raise ConfigError("Unsupported pipeline operation.")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def command_pipeline_audit(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    root = learning_root(config)
    ensure_store(root)
    pipeline = pipeline_store(root)
    state = pipeline.status()
    pipeline.active(state, args.run_id)
    if (getattr(args, "reconcile_parser", False) or getattr(args, "full", False)) and args.weekly:
        raise ConfigError("Run a forced full or parser-reconciliation audit without --weekly.")
    if args.weekly and not state["weekly_due"]:
        print(json.dumps(state["weekly"]))
        return 0
    paths = pipeline_roots(root, args.target)
    baseline = None
    previous = state["weekly"]
    run = state["runs"][args.run_id]
    parser_version = state.get("audit_rule_parser_version")
    if parser_version is None:
        parser_version = 1 if previous or any(i.get("kind") == "audit" for i in state["issues"].values()) else RULE_PARSER_VERSION
    if type(parser_version) is not int or not 1 <= parser_version <= RULE_PARSER_VERSION:
        raise ConfigError("Pipeline audit parser version is invalid or unsupported.")
    if getattr(args, "reconcile_parser", False):
        migration = state["issues"].get(f"audit-parser-v{RULE_PARSER_VERSION}-reconciliation")
        if not migration or migration.get("status") not in {"candidate", "investigate", "needs-user", "resolved", "justified"}:
            raise ConfigError("No parser reconciliation is available to refresh.")
        parser_version = 1
    if not args.weekly and isinstance(run.get("initial_targets"), list):
        baseline = pipeline.inside("reports/.pipeline-baseline-" + uuid.uuid4().hex + ".json")
        pipeline.write(baseline, {"rule_parser_version": parser_version, "file_snapshots": [
            {"path": ref["path"], "sha256": ref["sha256"], "words": None}
            for ref in run["initial_targets"]]})
    elif previous:
        ref = previous["snapshot"]
        if snapshot(ref["path"])["sha256"] != ref["sha256"]:
            raise ConfigError("Previous audit snapshot changed; preserve and reconcile it.")
        previous_payload = json.loads(Path(ref["path"]).read_text())["audit"]
        previous_payload["rule_parser_version"] = parser_version
        # The audit helper expects its own payload, not the pipeline envelope.
        baseline = pipeline.inside("reports/.pipeline-baseline-" + uuid.uuid4().hex + ".json")
        pipeline.write(baseline, previous_payload)
    elif parser_version != RULE_PARSER_VERSION:
        baseline = pipeline.inside("reports/.pipeline-baseline-" + uuid.uuid4().hex + ".json")
        pipeline.write(baseline, {"rule_parser_version": parser_version, "file_snapshots": [
            {"path": ref["path"], "sha256": ref["sha256"], "words": None}
            for ref in run.get("initial_targets", [])]})
    capture = io.StringIO()
    try:
        with redirect_stdout(capture):
            command_audit_rules(argparse.Namespace(path=paths, skills_dir=None, all_md=False,
                               baseline=baseline, similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
                               conflict_threshold=DEFAULT_CONFLICT_THRESHOLD,
                               force_full=getattr(args, "full", False),
                               new_targets=None if args.weekly else (args.target or [])))
        payload = json.loads(capture.getvalue())
    finally:
        if baseline and baseline.exists():
            baseline.unlink()
    result = pipeline.audit(args.run_id, payload, paths, args.weekly, args.target)
    print(json.dumps(result, indent=2))
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


def iter_markdown_bullets(path: Path, text: str | None = None) -> list[dict[str, Any]]:
    bullets: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    pending_content_indent = 0
    list_indents: list[tuple[int, int]] = []
    fence: tuple[str, int, int, int] | None = None
    code_indent: int | None = None

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            bullets.append(pending)
            pending = None

    for idx, raw in enumerate((text if text is not None else path.read_text(encoding="utf-8", errors="ignore")).splitlines(), start=1):
        line = raw.expandtabs(4)
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if code_indent is not None:
            if stripped and indent < code_indent:
                code_indent = None
            else:
                continue
        fence_match = re.match(r"^(`{3,}|~{3,})(.*)$", stripped)
        if fence:
            if stripped and indent < fence[2]:
                fence = None  # The enclosing list ended; process this line again.
            elif (fence_match and fence_match.group(1)[0] == fence[0]
                    and len(fence_match.group(1)) >= fence[1] and fence[2] <= indent <= fence[3]
                    and not fence_match.group(2).strip()):
                fence = None
                continue
            else:
                continue
        parents = [content for _, content in list_indents if content <= indent <= content + 3]
        parent = max(parents) if parents else None
        if indent >= 4 and parent is None:
            flush()
            list_indents.clear()
            continue  # An indented code block is not a fenced block or list.
        if pending is not None and indent >= pending_content_indent + 4:
            flush()  # Indented code inside a list is not rule prose.
            code_indent = pending_content_indent + 4
            continue
        valid_fence = (fence_match and (indent <= 3 or parent is not None)
                       and not (fence_match.group(1)[0] == "`" and "`" in fence_match.group(2)))
        if valid_fence:
            min_indent = parent if parent is not None else 0
            max_indent = parent + 3 if parent is not None else 3
            flush()
            if parent is None:
                list_indents.clear()
            fence = (fence_match.group(1)[0], len(fence_match.group(1)), min_indent, max_indent)
            continue
        other_list = re.match(r"^(\s*)([*+]|\d{1,9}[.)])(\s+)(.*)$", line)
        if other_list:
            flush()
            while list_indents and list_indents[-1][0] >= indent:
                list_indents.pop()
            content_indent = other_list.start(4) if len(other_list.group(3)) <= 4 else indent + len(other_list.group(2)) + 1
            list_indents.append((indent, content_indent))
            if len(other_list.group(3)) > 4:
                code_indent = content_indent + 4
                continue
            inline_fence = re.match(r"^(`{3,}|~{3,})(.*)$", other_list.group(4))
            if inline_fence and not (inline_fence.group(1)[0] == "`" and "`" in inline_fence.group(2)):
                fence = (inline_fence.group(1)[0], len(inline_fence.group(1)), content_indent, content_indent + 3)
            continue
        match = re.match(r"^(\s*)-(\s+)(.*)$", line)
        if match:
            flush()
            while list_indents and list_indents[-1][0] >= indent:
                list_indents.pop()
            content_indent = match.start(3) if len(match.group(2)) <= 4 else indent + 2
            list_indents.append((indent, content_indent))
            if len(match.group(2)) > 4:
                code_indent = content_indent + 4
                continue
            value = match.group(3).strip()
            if value.startswith(("[ ]", "[x]", "[X]")) or not value:
                continue
            inline_fence = re.match(r"^(`{3,}|~{3,})(.*)$", value)
            if inline_fence and not (inline_fence.group(1)[0] == "`" and "`" in inline_fence.group(2)):
                fence = (inline_fence.group(1)[0], len(inline_fence.group(1)), content_indent, content_indent + 3)
                continue
            pending = {"path": str(path), "line": idx, "text": value}
            pending_content_indent = content_indent
            continue
        if pending is None:
            if stripped and indent == 0:
                list_indents.clear()
            continue
        if (not stripped or indent >= pending_content_indent + 4
                or re.match(r"^(?:#{1,6}(?:\s|$)|>|(?:[*+]|\d+[.)])\s+|(?:-{3,}|\*{3,}|_{3,})\s*$)", stripped)):
            flush()
            continue
        pending["text"] += " " + stripped
    flush()
    return bullets


def legacy_markdown_bullets(path: Path, text: str) -> list[dict[str, Any]]:
    """Reconstruct version 1 discoveries only when migrating a baseline."""
    bullets = []
    in_fence = False
    for idx, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^\s*-\s+(.*)$", line)
        if match:
            value = match.group(1).strip()
            if value and not value.startswith(("[ ]", "[x]", "[X]")):
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
    baseline: dict[str, dict[str, Any]] | None = None
    parser_reparse = False
    if args.baseline:
        try:
            previous = json.loads(args.baseline.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"Cannot read audit baseline: {exc}") from exc
        snapshots = previous.get("file_snapshots") if isinstance(previous, dict) else None
        previous_version = previous.get("rule_parser_version", 1) if isinstance(previous, dict) else None
        if (type(previous_version) is not int or previous_version < 1
                or previous_version > RULE_PARSER_VERSION):
            raise ConfigError("Audit baseline has an unsupported rule parser version.")
        parser_reparse = previous_version != RULE_PARSER_VERSION
        if not isinstance(snapshots, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("path"), str)
            or (item.get("words") is not None and (not isinstance(item["words"], int) or item["words"] < 0))
            or not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", "")))
            for item in snapshots
        ):
            raise ConfigError("Audit baseline needs valid file_snapshots with paths, hashes and word counts.")
        baseline = {}
        spellings: set[str] = set()
        for item in snapshots:
            spelling = str(Path(item["path"]).expanduser())
            key = str(Path(spelling).resolve())
            previous_item = baseline.get(key)
            if spelling in spellings or (previous_item is not None and any(
                previous_item.get(field) != item.get(field) for field in ("sha256", "words", "lines")
            )):
                raise ConfigError("Audit baseline contains duplicate or conflicting file paths.")
            spellings.add(spelling)
            baseline.setdefault(key, item)
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

    # Preserve the first discovered spelling while comparing actual file paths.
    # Multiple roots can otherwise scan a symlinked file twice and produce a
    # baseline that this command itself rejects as having duplicate paths.
    unique_files: dict[Path, Path] = {}
    for md_path in sorted(set(markdown_files)):
        canonical_path = md_path.resolve(strict=True)
        unique_files.setdefault(canonical_path, md_path)
    markdown_files = list(unique_files.values())

    rules: list[dict[str, Any]] = []
    legacy_changes: list[dict[str, Any]] = []
    file_snapshots: list[dict[str, Any]] = []
    changed_files: list[str] = []
    unbaselined_files: list[str] = []
    new_targets = getattr(args, "new_targets", None)
    explicit_new = ({str(Path(path).expanduser().resolve()) for path in new_targets}
                    if new_targets is not None else None)
    for md_path in sorted(set(markdown_files)):
        raw = md_path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        snapshot = {"path": str(md_path), "sha256": hashlib.sha256(raw).hexdigest(),
                    "words": len(text.split()), "lines": len(text.splitlines())}
        previous_snapshot = (baseline or {}).get(str(md_path.resolve()))
        snapshot["word_delta"] = (snapshot["words"] - previous_snapshot["words"]
                                  if previous_snapshot and isinstance(previous_snapshot.get("words"), int) else None)
        file_snapshots.append(snapshot)
        if baseline is not None and previous_snapshot is None and explicit_new is not None:
            unbaselined_files.append(str(md_path))
        if ((previous_snapshot is None and (explicit_new is None or str(md_path.resolve()) in explicit_new))
                or (previous_snapshot is not None and previous_snapshot["sha256"] != snapshot["sha256"])):
            changed_files.append(str(md_path))
        parsed = iter_markdown_bullets(md_path, text)
        if parser_reparse:
            by_line = {item["line"]: item["text"] for item in parsed}
            for old in legacy_markdown_bullets(md_path, text):
                current_text = by_line.get(old["line"])
                if current_text != old["text"]:
                    legacy_changes.append({**old, "current_text": current_text})
        for bullet in parsed:
            bullet["norm"] = normalize_rule(bullet["text"])
            rules.append(bullet)

    by_norm: dict[str, list[dict[str, Any]]] = {}
    for rule in rules:
        norm = rule["norm"]
        if len(norm) < 24:
            continue
        by_norm.setdefault(norm, []).append(rule)

    exact_duplicates = [group for group in by_norm.values() if len(group) > 1]
    changed_set = set(changed_files)
    full_comparison = parser_reparse or bool(getattr(args, "force_full", False))
    if baseline is not None and not full_comparison:
        exact_duplicates = [group for group in exact_duplicates if any(rule["path"] in changed_set for rule in group)]

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
                if (baseline is not None and not full_comparison
                        and left["path"] not in changed_set and right["path"] not in changed_set):
                    continue
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
        "rule_parser_version": RULE_PARSER_VERSION,
        "scanned_files": [str(path) for path in sorted(set(markdown_files))],
        "rule_count": len(rules),
        "file_snapshots": file_snapshots,
        "changed_files": changed_files,
        "unbaselined_files": unbaselined_files,
        "removed_files": sorted(item["path"] for key, item in (baseline or {}).items()
                                if key not in {str(path.resolve()) for path in markdown_files}),
        "comparison_scope": "changed-targets" if baseline is not None and not full_comparison else "full",
        "parser_reparse": parser_reparse,
        "legacy_parse_changes": legacy_changes,
        "semantic_approval": False,
        "check_effectiveness": "not-evaluated",
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


def integration_entries(root: Path, state: dict, note_ids: list[str]) -> list[dict]:
    entries = []
    for note_id in note_ids:
        entry = state.get("processed", {}).get(note_id, {})
        target = Path(entry.get("path", "")).expanduser().resolve()
        if (entry.get("status") != "processed" or not target.is_file()
                or not target.is_relative_to((root / "processed").resolve())):
            raise ConfigError(f"Integration requires an archived note in the configured store: {note_id}")
        entries.append(entry)
    return entries


def inspect_receipt_bounded(reference: dict, *, timeout: float) -> dict:
    """Distinguish I/O unavailability from an independently invalid receipt."""
    worker = '''import json,sys
from pathlib import Path
from learning_integration import validate_receipt
ref=json.load(sys.stdin)
try:
    Path(ref.get("path", "")).stat()
    result=validate_receipt(ref)
    out={"status":"verified", "note_ids":result["manifest"]["note_ids"]}
except OSError as exc:
    out={"status":"unavailable", "reason":type(exc).__name__}
except (ValueError,KeyError,TypeError) as exc:
    out={"status":"invalid", "reason":str(exc)}
print(json.dumps(out))'''
    if timeout <= 0:
        return {"status": "unavailable", "reason": "Verification budget exhausted."}
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps(reference), text=True, capture_output=True, check=True, timeout=timeout)
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("status") not in {"verified", "unavailable", "invalid"}:
            raise ValueError("Invalid receipt verifier response.")
        return value
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"status": "unavailable", "reason": "Receipt verifier unavailable or timed out."}


def integration_problem(entry: dict, note_id: str, cache: dict | None = None, *, timeout: float = 8,
                        state: dict | None = None) -> str:
    if entry.get("integration_status") != "verified":
        return "No verified integration receipt; processed is archival status only."
    key = ""
    try:
        reference = entry.get("integration_receipt", {})
        key = json.dumps(reference, sort_keys=True)
        if cache is not None and key in cache:
            receipt = cache[key]
            if isinstance(receipt, str):
                return receipt
        else:
            if timeout <= 0:
                raise ValueError("Integration verification budget exhausted before this receipt was checked; retry.")
            worker = ("import json,sys; from pathlib import Path; from learning_integration import validate_receipt; "
                      "r=validate_receipt(json.load(sys.stdin)); v=r['verified']; "
                      "covered=set(v['files'])|set(v['generated']); "
                      "[covered.update(str(Path(root)/name) for name in payload) "
                      "for root,payload in v['skills'].items()]; "
                      "print(json.dumps({'manifest': {'note_ids': r['manifest']['note_ids'], "
                      "'relocations': r['manifest'].get('relocations',[])}, "
                      "'coverage': sorted(covered), 'relocations': v.get('relocations',{})}))")
            try:
                result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                        input=json.dumps(reference), text=True, capture_output=True,
                                        check=True, timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("Integration verification timed out; retry when storage is available.") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or "").strip().splitlines()
                raise ValueError(detail[-1][:500] if detail else "Integration verification failed.") from exc
            receipt = json.loads(result.stdout)
            if cache is not None:
                cache[key] = receipt
        if note_id not in receipt["manifest"]["note_ids"]:
            return "Integration receipt does not cover this note."
        relocated = receipt.get("relocations", {}).get(note_id, {})
        semantic_group = any(item.get("schema_version") == 2
                             for item in receipt["manifest"].get("relocations", []))
        if relocated or semantic_group:
            recorded = {str(target) for raw in entry.get("prevention_targets", []) + entry.get("detection_targets", [])
                        if (target := concrete_routing_target_path(raw)) is not None}
            covered = set(receipt.get("coverage", []))
            if (not set(relocated).issubset(recorded)
                    or any(path not in covered and relocated.get(path) not in covered for path in recorded)):
                return "Relocation no longer covers the note's recorded routing targets."
            for item in receipt["manifest"].get("relocations", []):
                if item.get("note_id") == note_id and (
                        Path(item["note"]["path"]).resolve() != Path(entry.get("path", "")).resolve()
                        or item["note"]["sha256"] != entry.get("archive_sha256")):
                    return "Relocation no longer matches the archived note binding."
                if item.get("schema_version") == 2:
                    if not isinstance(state, dict):
                        return "Semantic successor needs the current note group state."
                    for impact in item["note_impacts"]:
                        current = state.get("processed", {}).get(impact["note_id"], {})
                        if (current.get("status") != "processed" or current.get("integration_status") != "verified"
                                or current.get("integration_receipt") != entry.get("integration_receipt")
                                or Path(current.get("path", "")).resolve() != Path(impact["note"]["path"]).resolve()
                                or current.get("archive_sha256") != impact["note"]["sha256"]
                                or current.get("prevention_targets", []) != impact["prevention_targets"]
                                or current.get("detection_targets", []) != impact["detection_targets"]):
                            return "Semantic successor note group state changed."
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if cache is not None and key:
            cache[key] = str(exc)
        return str(exc)
    return ""


def verify_manifest_bounded(manifest: dict, *, proposal: bool = False, audit: bool = False,
                            timeout: float = 30) -> dict:
    """Bound verification of files and consumers that may reside on slow storage."""
    worker = ("import json,sys,time; from learning_integration import verify,verify_distribution; "
              "from learning_coherence import verify_coherence; m=json.load(sys.stdin); "
              "deadline=time.monotonic()+float(sys.argv[2]); "
              "audit=sys.argv[3]=='audit'; "
              "d=verify_distribution(m,deadline=deadline,allow_audit=audit) if audit or sys.argv[1]=='proposal' else None; "
              "r=verify_coherence(m,d,require_reviews=False) if sys.argv[1]=='proposal' "
              "else ({**d,'coherence':verify_coherence(m,d)} if audit else verify(m,deadline=deadline)); "
              "print(json.dumps(r))")
    try:
        result = subprocess.run([sys.executable, "-c", worker, "proposal" if proposal else "verify", str(timeout),
                                 "audit" if audit else "notes"],
                                cwd=str(Path(__file__).resolve().parent), input=json.dumps(manifest),
                                text=True, capture_output=True, check=True, timeout=timeout)
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise ConfigError("Integration verification timed out; retry when storage is available.") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").strip().splitlines()
        raise ConfigError(details[-1][:500] if details else "Integration verification failed.") from exc
    except OSError as exc:
        raise ConfigError("Unable to start integration verification; retry.") from exc


def run_processed_entries(state: dict, run: dict) -> list[tuple[str, dict]]:
    initial = run.get("initial_inbox")
    if run.get("integration_scope_version") != 1 or not isinstance(initial, list) or not run.get("id"):
        raise ConfigError("Legacy run lacks exact integration attribution; audit explicit note IDs or all history.")
    ids = {item["id"] for item in initial}
    selected = []
    for note_id, entry in state.get("processed", {}).items():
        memberships = entry.get("consolidation_runs", [])
        if not isinstance(memberships, list) or any(not isinstance(value, str) for value in memberships):
            raise ConfigError("Stored consolidation membership is invalid; reconcile before auditing.")
        belongs = entry.get("consolidation_run") == run["id"]
        if run["id"] in memberships and (not belongs or entry.get("status") != "processed"):
            raise ConfigError("A prior run outcome was superseded; reconcile its original evidence before auditing.")
        if entry.get("status") != "processed":
            continue
        if note_id in ids and not belongs:
            raise ConfigError("Initial source outcome lacks exact run attribution; reconcile before auditing.")
        if belongs:
            selected.append((note_id, entry))
    return selected


def require_run_integrations(root: Path, run: dict) -> None:
    failures, cache = [], {}
    deadline = time.monotonic() + 30
    state = load_state(root)
    for note_id, entry in run_processed_entries(state, run):
        problem = integration_problem(entry, note_id, cache, timeout=min(8, deadline - time.monotonic()), state=state)
        if problem:
            failures.append(f"{note_id}: {problem}")
    if failures:
        raise ConfigError("Integration is incomplete: " + "; ".join(failures))


def chained_relocation_matches(item: dict, receipt: dict, *, deadline: float | None = None,
                               review_cache: dict[int, list[tuple[str, str, str]]] | None = None) -> bool:
    """Match a successor to the exact relocation recorded in the current receipt."""
    from learning_coherence import proposal_hash

    if type(item.get("schema_version")) is not int or item["schema_version"] != 2:
        return False
    core = {"note_id", "source", "target", "source_root", "target_root", "source_head", "target_head",
            "common_dir", "note", "snapshot", "prior_receipt"}
    manifest, verified = receipt["manifest"], receipt["verified"]
    if (manifest.get("version") != 1 or not isinstance(receipt.get("verified_at"), str)
            or item["note_id"] not in manifest["note_ids"]):
        return False
    if review_cache is None or id(receipt) not in review_cache:
        artifacts = {key: verified[key] for key in ("files", "skills", "generated")}
        if "relocations" in verified:
            artifacts["relocations"] = verified["relocations"]
        proposal = verified["coherence"]["proposal_sha256"]
        coherence = manifest["coherence"]
        reviews = coherence["reviews"]
        if (not isinstance(reviews, list) or not reviews
                or any(not isinstance(review, dict) or review.get("verdict") != "pass"
                       or review.get("proposal_sha256") != proposal for review in reviews)
                or proposal_hash(manifest, artifacts) != proposal):
            return False
        level = coherence.get("level")
        expected_roles = {"policy", "ergonomics"} if level == "council" else {"reviewer"}
        if (level not in {"local", "peer", "council"} or len(reviews) != len(expected_roles)
                or not isinstance(coherence.get("editor"), str) or not coherence["editor"].strip()
                or coherence["editor"] != coherence["editor"].strip()):
            return False
        roles, reviewers, checked_reports = set(), set(), []
        for review in reviews:
            role, reviewer, ref = review.get("role"), review.get("reviewer"), review.get("evidence")
            if (role not in expected_roles or role in roles or not isinstance(reviewer, str) or not reviewer.strip()
                    or reviewer != reviewer.strip()
                    or (level != "local" and (reviewer == coherence.get("editor") or reviewer in reviewers))
                    or not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                    or not Path(ref["path"]).is_absolute() or not isinstance(ref.get("sha256"), str)):
                return False
            evidence = read_before_deadline(Path(ref["path"]), deadline, max_bytes=16 * 1024 * 1024)
            if hashlib.sha256(evidence).hexdigest() != ref["sha256"] or proposal.encode() not in evidence:
                return False
            checked_reports.append((ref["path"], ref["sha256"], proposal))
            roles.add(role)
            reviewers.add(reviewer)
        if roles != expected_roles:
            return False
        if review_cache is not None:
            review_cache[id(receipt)] = checked_reports
    matches = [old for old in manifest.get("relocations", [])
               if isinstance(old, dict) and all(old.get(key) == item.get(key) for key in core)]
    if len(matches) != 1:
        return False
    previous = matches[0]
    if set(previous) == core:
        previous_hash = previous["snapshot"]["sha256"]
    elif (set(previous) == core | {"schema_version", "relation", "target_sha256", "note_impacts"}
          and type(previous["schema_version"]) is int and previous["schema_version"] == 2
          and previous["relation"] == "semantic-successor"):
        previous_hash = previous["target_sha256"]
    else:
        return False
    return verified["files"].get(str(Path(item["target"]).resolve())) == previous_hash


def validate_relocation_bindings(root: Path, manifest: dict, state: dict, *,
                                 deadline: float | None = None) -> None:
    """Bind historical mappings to the actual archived notes and recorded receipts."""
    mappings = manifest.get("relocations", [])
    if not mappings:
        return
    if not isinstance(mappings, list):
        raise ConfigError("Relocations must be a list.")
    if deadline is None:
        deadline = time.monotonic() + 8
    receipt_cache: dict[tuple[str, str], dict] = {}
    chain_review_cache: dict[int, list[tuple[str, str, str]]] = {}
    entries = dict(zip(manifest.get("note_ids", []), integration_entries(root, state, manifest.get("note_ids", []))))
    receipt_dir = store_directory(root, root / "reports" / "integrations").resolve()
    for item in mappings:
        if not isinstance(item, dict) or item.get("schema_version") != 2:
            continue
        if not isinstance(item.get("source"), str) or not isinstance(item.get("note_impacts"), list):
            raise ConfigError("Semantic successor needs complete source and note evidence.")
        source = str(Path(item["source"]).resolve())
        for impact in item["note_impacts"]:
            if not isinstance(impact, dict) or not isinstance(impact.get("note_id"), str):
                raise ConfigError("Semantic successor note evidence is invalid.")
            paths = impact.get("prevention_targets"), impact.get("detection_targets")
            if any(not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                   for values in paths):
                raise ConfigError("Semantic successor routing evidence is invalid.")
            routed = {str(path) for value in paths[0] + paths[1]
                      if (path := concrete_routing_target_path(value)) is not None}
            if source in routed and not any(
                    isinstance(other, dict) and other.get("schema_version") == 2
                    and other.get("note_id") == impact["note_id"]
                    and other.get("source") == item["source"] and other.get("target") == item.get("target")
                    and other.get("prior_receipt") == item.get("prior_receipt") for other in mappings):
                raise ConfigError("Semantic successor needs a mapping for every note sharing the source.")
    for item in mappings:
        if not isinstance(item, dict) or not isinstance(item.get("note_id"), str) or item["note_id"] not in entries:
            raise ConfigError("Relocation must identify an archived note.")
        entry = entries[item["note_id"]]
        if item.get("schema_version") == 2:
            impacts = item.get("note_impacts")
            if not isinstance(impacts, list) or not impacts or len(impacts) > 128:
                raise ConfigError("Semantic successor needs the complete historical note group.")
            for impact in impacts:
                if (not isinstance(impact, dict) or not isinstance(impact.get("note_id"), str)
                        or not isinstance(impact.get("note"), dict)):
                    raise ConfigError("Semantic successor note evidence is invalid.")
                group_entry = state.get("processed", {}).get(impact.get("note_id"), {})
                if (group_entry.get("status") != "processed" or group_entry.get("integration_status") != "verified"
                        or group_entry.get("integration_receipt") not in (item.get("prior_receipt"), entry.get("integration_receipt"))
                        or not isinstance(group_entry.get("path"), str)
                        or not isinstance(impact["note"].get("path"), str)
                        or Path(group_entry["path"]).resolve() != Path(impact["note"]["path"]).resolve()
                        or group_entry.get("archive_sha256") != impact["note"].get("sha256")
                        or group_entry.get("prevention_targets", []) != impact.get("prevention_targets")
                        or group_entry.get("detection_targets", []) != impact.get("detection_targets")):
                    raise ConfigError("Semantic successor note group state changed.")
        prior, note = item.get("prior_receipt"), item.get("note")
        if (not isinstance(prior, dict) or not isinstance(prior.get("path"), str)
                or Path(prior["path"]).expanduser().resolve().parent != receipt_dir
                or not isinstance(note, dict) or not isinstance(note.get("path"), str)
                or Path(note["path"]).expanduser().resolve() != Path(entry["path"]).resolve()
                or note.get("sha256") != entry.get("archive_sha256")):
            raise ConfigError("Relocation must bind the archived note and its receipt store.")
        targets = {str(target) for raw in entry.get("prevention_targets", []) + entry.get("detection_targets", [])
                   if (target := concrete_routing_target_path(raw)) is not None}
        if not isinstance(item.get("source"), str) or str(Path(item["source"]).resolve()) not in targets:
            raise ConfigError("Relocation must refer to a recorded concrete target.")
        current = entry.get("integration_receipt")
        if (entry.get("integration_status") != "verified" or not isinstance(current, dict)
                or not isinstance(current.get("path"), str)
                or Path(current["path"]).expanduser().resolve().parent != receipt_dir):
            raise ConfigError("Relocation needs the note's recorded integration receipt.")
        if current == prior:
            continue
        try:
            key = (current["path"], current["sha256"])
            if key not in receipt_cache:
                raw = read_before_deadline(Path(current["path"]), deadline, max_bytes=16 * 1024 * 1024)
                if hashlib.sha256(raw).hexdigest() != current["sha256"]:
                    raise ValueError("Current receipt changed.")
                receipt_cache[key] = json.loads(raw)
            receipt = receipt_cache[key]
            if (item["note_id"] not in receipt["manifest"]["note_ids"]
                    or (item not in receipt["manifest"].get("relocations", [])
                        and not chained_relocation_matches(item, receipt, deadline=deadline,
                                                           review_cache=chain_review_cache))):
                raise ValueError("Current receipt does not contain this mapping.")
        except (TypeError, KeyError, OSError, ValueError) as exc:
            raise ConfigError("Relocation must preserve the note's prior integration receipt.") from exc
    for path, expected, proposal in {item for refs in chain_review_cache.values() for item in refs}:
        evidence = read_before_deadline(Path(path), deadline, max_bytes=16 * 1024 * 1024)
        if hashlib.sha256(evidence).hexdigest() != expected or proposal.encode() not in evidence:
            raise ConfigError("Intermediate relocation review evidence changed during validation.")


def audit_coherence_basis(issue: dict) -> str:
    """Bind a review to audit discovery, targets and its current decision state."""
    return fingerprint({key: issue.get(key) for key in
                        ("id", "kind", "status", "title", "description", "targets", "latest_audit",
                         "member_hash", "excluded_member_ids")})


def audit_coherence_bindings(root: Path, manifest: dict) -> dict[str, str]:
    """Match every reviewed preimage to the exact currently recorded audit finding."""
    sources = manifest.get("audit_sources")
    if (manifest.get("note_ids") != [] or not isinstance(sources, list) or not sources
            or len(sources) > 100 or manifest.get("relocations")):
        raise ConfigError("Audit coherence needs audit sources, no note IDs and no note relocations.")
    pipeline = pipeline_store(root)
    state = pipeline.status()
    expected = {}
    for source in sources:
        if (not isinstance(source, dict) or set(source) != {"id", "fingerprint", "report"}
                or not isinstance(source.get("report"), dict)
                or set(source["report"]) != {"path", "sha256"}):
            raise ConfigError("Audit sources need an issue ID, discovery fingerprint and registered report.")
        issue_id = source["id"]
        if not isinstance(issue_id, str) or not issue_id:
            raise ConfigError("Audit source issue ID must be a nonempty string.")
        issue = state["issues"].get(issue_id)
        if (issue_id in expected or not isinstance(issue, dict)
                or issue.get("kind") != "audit" or not issue_id.startswith("audit-")
                or issue.get("status") not in {"candidate", "investigate"}
                or issue.get("revalidation_required") or issue.get("coverage_incomplete")
                or source["fingerprint"] != audit_coherence_basis(issue)):
            raise ConfigError("Audit issue is missing, duplicated or changed; refresh the proposal.")
        targets = issue.get("targets")
        if not isinstance(targets, list) or not targets:
            raise ConfigError("Audit issue needs recorded targets.")
        expected[issue_id] = issue
    coherence = manifest.get("coherence")
    targets = coherence.get("targets") if isinstance(coherence, dict) else None
    if not isinstance(targets, list) or not targets:
        raise ConfigError("Audit coherence needs explicit target preimages.")
    actual_targets = {}
    for item in targets:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ConfigError("Audit coherence needs explicit target preimages.")
        path = str(Path(item["path"]).expanduser().resolve())
        before = item.get("before")
        if path in actual_targets or not isinstance(before, dict) or not isinstance(before.get("sha256"), str):
            raise ConfigError("Audit coherence target preimages are incomplete or duplicated.")
        actual_targets[path] = before
    discovered = {}
    for issue in expected.values():
        for ref in issue["targets"]:
            if not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not isinstance(ref.get("sha256"), str):
                raise ConfigError("Audit issue targets are malformed.")
            path = str(Path(ref["path"]).expanduser().resolve())
            if path in discovered and discovered[path] != ref["sha256"]:
                raise ConfigError("Audit issues disagree on a target preimage.")
            discovered[path] = ref["sha256"]
    if discovered != {path: ref["sha256"] for path, ref in actual_targets.items()}:
        raise ConfigError("Audit coherence must cover every exact discovery target and preimage.")
    if not current_audit_sources(root, sources, state, actual_targets):
        raise ConfigError("Audit discovery report or cohort evidence is missing, unregistered, changed or incomplete.")
    return {key: audit_coherence_basis(issue) for key, issue in expected.items()}


def command_integration(args: argparse.Namespace) -> int:
    root = learning_root(load_config(args.config))
    if args.command == "audit-coherence-source":
        state = pipeline_store(root).status()
        issue = state["issues"].get(args.issue_id)
        if (not isinstance(issue, dict) or issue.get("kind") != "audit"
                or issue.get("status") not in {"candidate", "investigate"}
                or issue.get("revalidation_required") or issue.get("coverage_incomplete")
                or not issue.get("targets")
                or not args.issue_id.startswith("audit-")):
            raise ConfigError("Audit issue is missing or is not an audit discovery.")
        path = Path(args.audit_report).expanduser()
        valid, _, digest = check_sealed_file(root, "audit-source", "report-snapshot",
                                             {"path": str(path), "sha256": None}, 8)
        if not valid:
            raise ConfigError("Audit report is missing or outside the learning store.")
        source = {"id": args.issue_id, "fingerprint": audit_coherence_basis(issue),
                  "report": {"path": str(path.resolve()), "sha256": digest}}
        preimages = {str(Path(ref["path"]).expanduser().resolve()): ref for ref in issue["targets"]}
        if not current_audit_sources(root, [source], state, preimages):
            raise ConfigError("Audit discovery report is missing, unregistered, changed or does not contain the finding.")
        if not current_refs(issue["targets"]):
            raise ConfigError("Audit targets changed or are unavailable; reassess before review.")
        fresh = pipeline_store(root).status()
        if (fresh["issues"].get(args.issue_id) != issue
                or not current_audit_sources(root, [source], fresh, preimages)):
            raise ConfigError("Audit evidence changed while capturing its source; retry.")
        print(json.dumps(source, indent=2))
        return 0
    if args.command in {"audit-coherence-proposal", "audit-coherence-check"}:
        manifest = json.loads(read_before_deadline(Path(args.manifest), time.monotonic() + 8, max_bytes=16 * 1024 * 1024))
        binding = audit_coherence_bindings(root, manifest)
        proposal = args.command == "audit-coherence-proposal"
        result = verify_manifest_bounded(manifest, proposal=proposal, audit=True)
        if audit_coherence_bindings(root, manifest) != binding:
            raise ConfigError("Audit evidence changed during coherence verification; retry.")
        print(json.dumps({"status": "awaiting-review" if proposal else "verified",
                          "audit_sources": binding, "result": result}, indent=2))
        return 0
    if args.command == "coherence-proposal":
        manifest = json.loads(read_before_deadline(Path(args.manifest), time.monotonic() + 8, max_bytes=16 * 1024 * 1024))
        validate_relocation_bindings(root, manifest, load_state(root))
        result = verify_manifest_bounded(manifest, proposal=True)
        print(json.dumps({"status": "awaiting-review", **result}, indent=2))
        return 0
    if args.command == "integration-check":
        manifest = json.loads(read_before_deadline(Path(args.manifest), time.monotonic() + 8, max_bytes=16 * 1024 * 1024))
        validate_relocation_bindings(root, manifest, load_state(root))
        result = verify_manifest_bounded(manifest)
        print(json.dumps({"status": "verified", "verified": result}, indent=2))
        return 0
    if args.command == "integration-record":
        manifest = json.loads(read_before_deadline(Path(args.manifest), time.monotonic() + 8, max_bytes=16 * 1024 * 1024))
        validate_relocation_bindings(root, manifest, load_state(root))
        verified = verify_manifest_bounded(manifest)
        with state_update_lock(root):
            deadline = time.monotonic() + 6
            state = load_state(root, deadline=deadline)
            validate_relocation_bindings(root, manifest, state, deadline=deadline)
            entries = integration_entries(root, state, manifest["note_ids"])
            covered_files = set(verified["files"]) | set(verified["generated"])
            for skill_root, payload in verified["skills"].items():
                covered_files.update(str(Path(skill_root) / name) for name in payload)
            for note_id, entry in zip(manifest["note_ids"], entries):
                relocated = verified.get("relocations", {}).get(note_id, {})
                recorded = {str(target) for raw in entry.get("prevention_targets", []) + entry.get("detection_targets", [])
                            if (target := concrete_routing_target_path(raw)) is not None}
                if not set(relocated).issubset(recorded):
                    raise ConfigError("Relocation must refer to a recorded concrete target.")
                for raw in entry.get("prevention_targets", []) + entry.get("detection_targets", []):
                    target = concrete_routing_target_path(raw)
                    if (target is not None and str(target) not in covered_files
                            and relocated.get(str(target)) not in covered_files):
                        raise ConfigError(f"Integration omitted a recorded concrete target: {raw}")
            if any(e.get("template_upstream_status") in {"draft-created", "deferred"} for e in entries):
                if manifest.get("template_status") != "promoted":
                    raise ConfigError("Resolve the template handoff before recording complete integration.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConfigError("Integration verification timed out; retry when storage is available.")
            if verify_manifest_bounded(manifest, timeout=remaining) != verified:
                raise ConfigError("Integration targets changed while preparing the receipt.")
            receipt = {"manifest": manifest, "verified": verified, "verified_at": now_iso()}
            receipt_path = store_directory(root, root / "reports" / "integrations") / f"{uuid.uuid4().hex}.json"
            atomic_write_text(receipt_path, json.dumps(receipt, indent=2) + "\n")
            reference = {"path": str(receipt_path), "sha256": hashlib.sha256(read_before_deadline(receipt_path, deadline)).hexdigest()}
            for entry in entries:
                entry.update(integration_status="verified", integration_receipt=reference)
                if (manifest.get("template_status") == "promoted"
                        and entry.get("template_upstream_status") in {"draft-created", "deferred", "promoted"}):
                    entry["template_upstream_status"] = "promoted"
            save_state(root, state)
        print(json.dumps({"status": "verified", "receipt": reference, "note_ids": manifest["note_ids"]}, indent=2))
        return 0
    requested = set(args.note_id or [])
    if sum(bool(value) for value in (args.run_id, requested, args.all_history)) > 1:
        raise ConfigError("Choose one of --run-id, --note-id or --all-history for an integration audit.")
    run_id = args.run_id
    if args.require_verified and not (run_id or requested or args.all_history):
        active = [run for run in pipeline_store(root).load()["runs"].values()
                  if run.get("status") == "running"]
        if len(active) > 1:
            raise ConfigError("Multiple active runs need reconciliation before an implicit audit.")
        if active:
            run_id = active[0]["id"]
        else:
            raise ConfigError("Start a pipeline run or explicitly select --run-id, --note-id or --all-history for a strict audit.")
    state = load_state(root)
    entries = list(state.get("processed", {}).items())
    if run_id:
        run = pipeline_store(root).load()["runs"].get(run_id)
        if not run:
            raise ConfigError("Unknown pipeline run for integration audit.")
        entries = run_processed_entries(state, run)
    rows, cache = [], {}
    deadline = time.monotonic() + 30
    for note_id, entry in entries:
        if entry.get("status") != "processed" or (requested and note_id not in requested):
            continue
        problem = integration_problem(entry, note_id, cache, timeout=min(8, deadline - time.monotonic()), state=state)
        rows.append({"note_id": note_id, "status": "pending" if problem else "verified", "reason": problem})
    found = {row["note_id"] for row in rows}
    rows.extend({"note_id": key, "status": "pending", "reason": "Unknown or unprocessed note."} for key in requested - found)
    scope = {"kind": "run", "run_id": run_id} if run_id else {"kind": "notes" if requested else "history"}
    print(json.dumps({"scope": scope, "notes": rows, "verified": sum(r["status"] == "verified" for r in rows),
                      "pending": sum(r["status"] != "verified" for r in rows)}, indent=2))
    return 1 if args.require_verified and any(r["status"] != "verified" for r in rows) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("coherence-proposal", "audit-coherence-source", "audit-coherence-proposal", "audit-coherence-check",
                 "integration-check", "integration-record", "integration-audit"):
        integration = sub.add_parser(name)
        if name == "integration-audit":
            integration.add_argument("--run-id", help="Audit processed notes from this pipeline run only")
            integration.add_argument("--note-id", action="append")
            integration.add_argument("--all-history", action="store_true", help="Explicitly audit all historical notes, including during an active run")
            integration.add_argument("--require-verified", action="store_true")
        elif name == "audit-coherence-source":
            integration.add_argument("--issue-id", required=True)
            integration.add_argument("--audit-report", required=True)
        else:
            integration.add_argument("--manifest", required=True)
        integration.set_defaults(func=command_integration)

    init_store = sub.add_parser("init-store")
    init_store.set_defaults(func=command_init_store)

    recover = sub.add_parser("recover-finalizations", help="Reconcile interrupted note/state commits under the store lock")
    recover.set_defaults(func=command_recover_finalizations)

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
    record.add_argument("--source-run-id", default="")
    record.add_argument("--application-status", choices=["proposed", "applied"], default="proposed")
    record.add_argument("--applied-target", action="append")
    record.add_argument("--application-evidence", help="Saved verification artifact for an already-applied improvement")
    add_routing_arguments(record)
    record.set_defaults(func=command_record)

    prepare = sub.add_parser("prepare-run")
    prepare.set_defaults(func=command_prepare_run)

    finalize = sub.add_parser("finalize-note")
    finalize.add_argument("--file", required=True)
    finalize.add_argument("--status", choices=["processed", "needs-review", "rejected"], required=True)
    finalize.add_argument("--rationale", required=True)
    finalize.add_argument("--run-id", default="")
    finalize.add_argument("--review-evidence", help="Approved autonomous instruction-only decision receipt under configured reports")
    add_routing_arguments(finalize, include_enforce=True)
    finalize.set_defaults(func=command_finalize_note)

    report = sub.add_parser("write-report")
    report.add_argument("--run-id", default="")
    report.add_argument("--summary", required=True)
    report.add_argument("--review-evidence", help="Saved per-note receipt accounting for a due Joint Review's residual queue")
    add_routing_arguments(report, include_enforce=True)
    report.set_defaults(func=command_write_report)

    summary = sub.add_parser("summarize-runs")
    summary.add_argument("--since", default="")
    summary.add_argument("--format", choices=["json", "markdown"], default="json")
    summary.add_argument("--read-archives", action="store_true", help="Explicit legacy index reconstruction; ordinary summaries use state only")
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
    mode = notify.add_mutually_exclusive_group()
    mode.add_argument("--send-msmtp", action="store_true")
    mode.add_argument("--prepare", action="store_true", help="Reserve the digest before connector submission")
    mode.add_argument("--check-digest", help="Revalidate a reserved digest immediately before connector submission")
    notify.set_defaults(func=command_notify)

    configure = sub.add_parser("pipeline-configure")
    configure.add_argument("--ordinary-thread", required=True)
    configure.add_argument("--release-thread", required=True)
    configure.add_argument("--language", choices=["en", "it"], default="en")
    configure.set_defaults(func=command_pipeline)
    sub.add_parser("pipeline-status").set_defaults(func=command_pipeline)
    recovery = sub.add_parser("pipeline-run-reconcile", help="Verify recovery without rewriting the original run")
    recovery.add_argument("--run-id", required=True)
    recovery.add_argument("--followup-run-id", action="append", required=True)
    recovery.add_argument("--report", required=True)
    recovery.set_defaults(func=command_pipeline)
    release_status = sub.add_parser("pipeline-release-status")
    release_status.add_argument("--release-version", help="Exact candidate version for open post-release checks")
    release_status.add_argument("--release-assessment", help="Complete candidate-bound semantic issue assessment JSON")
    release_status.add_argument("--release-base", default="origin/main", help="Target ref bound by a scoped release assessment")
    release_status.set_defaults(func=command_pipeline)
    release_assessment = sub.add_parser("pipeline-release-assessment",
                                       help="Print a private candidate-bound assessment template for every open issue/cohort")
    release_assessment.add_argument("--release-base", default="origin/main")
    release_assessment.set_defaults(func=command_pipeline)
    begin = sub.add_parser("pipeline-begin")
    begin.add_argument("--run-id", required=True)
    begin.add_argument("--runner", required=True, choices=["midnight", "noon"])
    begin.add_argument("--goal-mode", required=True, choices=["scheduled", "manual"],
                       help="Record Goal intent before the run; existing legacy runs retain unknown provenance")
    begin.add_argument("--dispatch-evidence", help="Saved current app heartbeat capture; absent or invalid leaves scheduled runner unknown")
    begin.set_defaults(func=command_pipeline)
    dispatch_bind = sub.add_parser("pipeline-dispatch-bind", help="Correlate this run's start with a current app heartbeat")
    dispatch_bind.add_argument("--run-id", required=True)
    dispatch_bind.add_argument("--dispatch-evidence", required=True)
    dispatch_bind.set_defaults(func=command_pipeline)
    goal_attempt = sub.add_parser("pipeline-goal-attempt", help="Record possible Goal creation before calling the native tool")
    goal_attempt.add_argument("--run-id", required=True)
    goal_attempt.add_argument("--goal-thread-id", required=True)
    goal_attempt.set_defaults(func=command_pipeline)
    goal_bind = sub.add_parser("pipeline-goal-bind", help="Bind pipeline runs to the current Goal baseline")
    goal_bind.add_argument("--run-id", required=True)
    goal_bind.add_argument("--initial-fingerprint", required=True)
    goal_bind.add_argument("--goal-thread-id", required=True)
    goal_bind.add_argument("--goal-receipt", required=True)
    goal_bind.set_defaults(func=command_pipeline)
    goal_reopen = sub.add_parser("pipeline-goal-reopen", help="Reopen a sealed pipeline cycle for a still-active Goal")
    goal_reopen.add_argument("--run-id", required=True)
    goal_reopen.add_argument("--initial-fingerprint", required=True)
    goal_reopen.add_argument("--sealed-at", required=True)
    goal_reopen.add_argument("--reason", required=True)
    goal_reopen.add_argument("--goal-receipt", required=True)
    goal_reopen.set_defaults(func=command_pipeline)
    goal_advance = sub.add_parser("pipeline-goal-advance", help="Confirm native Goal completion and allow a new cycle")
    goal_advance.add_argument("--run-id", required=True)
    goal_advance.add_argument("--initial-fingerprint", required=True)
    goal_advance.add_argument("--sealed-at", required=True)
    goal_advance.add_argument("--goal-receipt", required=True)
    goal_advance.set_defaults(func=command_pipeline)
    goal_status = sub.add_parser("pipeline-goal-status", help="Recheck a Goal's original inbox across later runs")
    goal_status.add_argument("--run-id", required=True)
    goal_status.add_argument("--initial-fingerprint", required=True)
    goal_status.add_argument("--seal", action="store_true", help="Seal a fully accounted Goal cycle before marking the Goal complete")
    goal_status.add_argument("--goal-receipt", help="Saved current active native Goal result, required for sealing")
    goal_status.add_argument("--review-evidence", help="Current residual review receipt when review is due")
    goal_status.set_defaults(func=command_pipeline)
    issue = sub.add_parser("pipeline-issue")
    issue.add_argument("--from-json", required=True)
    issue.add_argument("--run-id", help="Explicitly associate this assessment with an active run")
    issue.set_defaults(func=command_pipeline)
    audit_cycle = sub.add_parser("pipeline-audit")
    audit_cycle.add_argument("--run-id", required=True)
    audit_cycle.add_argument("--weekly", action="store_true")
    audit_cycle.add_argument("--reconcile-parser", action="store_true",
                             help="Refresh parser migration with full current coverage")
    audit_cycle.add_argument("--full", action="store_true",
                             help="Run a full comparison while retaining actual changed-file accounting")
    audit_cycle.add_argument("--target", action="append")
    audit_cycle.set_defaults(func=command_pipeline_audit)
    finish = sub.add_parser("pipeline-finish")
    finish.add_argument("--run-id", required=True)
    finish.add_argument("--status", required=True, choices=["completed", "partial", "failed"])
    finish.add_argument("--blocker-scope", choices=["notes", "operational", "mixed", "unknown"],
                        help="Classify a partial/failed run; omit to retain unknown provenance")
    finish.add_argument("--summary", required=True)
    finish.add_argument("--report", default="")
    finish.add_argument("--target", action="append")
    finish.add_argument("--require-integration", action="store_true")
    finish.set_defaults(func=command_pipeline)
    ack = sub.add_parser("notify-ack")
    ack.add_argument("--digest-id", required=True)
    ack.add_argument("--receipt", required=True)
    ack.add_argument("--not-delivered", action="store_true", help="Clear only after verified non-delivery")
    ack.set_defaults(func=command_pipeline)

    send_email = sub.add_parser("send-email")
    send_email.add_argument("--subject", required=True)
    send_email.add_argument("--body-file", required=True)
    send_email.set_defaults(func=command_send_email)

    privacy = sub.add_parser("privacy-scan")
    privacy.add_argument("paths", nargs="+")
    privacy.set_defaults(func=command_privacy_scan)

    audit = sub.add_parser("audit-rules")
    audit.add_argument("--path", action="append")
    audit.add_argument("--skills-dir", type=Path, default=None)
    audit.add_argument("--similarity-threshold", default=str(DEFAULT_SIMILARITY_THRESHOLD))
    audit.add_argument("--conflict-threshold", default=str(DEFAULT_CONFLICT_THRESHOLD))
    audit.add_argument("--all-md", action="store_true")
    audit.add_argument("--baseline", type=Path, help="Prior audit JSON; compare pairs touching changed targets only")
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
    except (ConfigError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
