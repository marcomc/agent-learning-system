"""Persistent handoffs and presentation for the scheduled learning pipeline."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


OPEN = {"candidate", "investigate", "needs-user"}
STATUSES = OPEN | {"resolved", "justified"}
RUNNERS = {"midnight", "noon", "unknown"}
HEARTBEAT_RUNNERS = {
    "agent-learning-midnight-consolidation": "midnight",
    "agent-learning-noon-consolidation": "noon",
}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
THREAD = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")
RELEASE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
INITIAL_ASSESSMENT_TIMEOUT = 3
GOAL_ADVANCE_TIMEOUT = 45
RULE_PARSER_VERSION = 2


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def issue_notification_revision(issue: dict[str, Any]) -> str:
    """Fingerprint current issue details without rendering user-authored text in English email."""
    fields = ("id", "kind", "status", "title", "description", "question", "options", "sources", "targets",
              "evidence", "resolution", "drifts", "revalidation_required", "post_release")
    defaults = {"question": "", "options": [], "sources": [], "targets": [], "evidence": [], "drifts": [],
                "resolution": "", "revalidation_required": False}
    return fingerprint({key: issue.get(key, defaults.get(key)) for key in fields})


def run_notification_revision(run: dict[str, Any]) -> str:
    """Fingerprint run-alert details without rendering free-form summaries in English email."""
    fields = ("id", "runner", "requested_runner", "dispatch_status", "dispatch", "dispatch_history",
              "start_tool_call_id", "goal_mode", "status", "started_at", "finished_at", "summary", "report")
    return fingerprint({key: run.get(key) for key in fields})


def captured_heartbeat(raw: bytes, expected_thread: str, now: datetime) -> dict[str, Any]:
    """Extract a bounded app-tool capture; this is an attestation, not a signature."""
    capture = json.loads(raw)
    if (not isinstance(capture, dict) or capture.get("schema") != "app-thread-event-v1"
            or not THREAD.fullmatch(expected_thread) or capture.get("thread") != expected_thread
            or capture.get("status") != "inProgress" or not THREAD.fullmatch(str(capture.get("turn", "")))
            or type(capture.get("startedAt")) is not int):
        raise ValueError("Invalid current thread capture.")
    item = capture.get("event")
    if (not isinstance(item, dict) or item.get("type") != "functionCallOutput"
            or item.get("namespace") != "codex_app" or item.get("name") != "automation_update"
            or not isinstance(item.get("id"), str) or not item["id"].startswith("fco_")
            or not isinstance(item.get("output"), dict) or item["output"].get("truncated") is not False):
        raise ValueError("Invalid or truncated app heartbeat.")
    text = item["output"].get("text")
    if not isinstance(text, str):
        raise ValueError("Missing app heartbeat text.")
    match = re.match(r"\A<heartbeat>\s*<automation_id>([^<]+)</automation_id>\s*"
                     r"<current_time_iso>([^<]+)</current_time_iso>", text)
    if not match or match.group(1) not in HEARTBEAT_RUNNERS:
        raise ValueError("Unknown app heartbeat.")
    event_time = datetime.fromisoformat(match.group(2).replace("Z", "+00:00"))
    if (event_time.utcoffset() is None or now.utcoffset() is None
            or abs(event_time.timestamp() - capture["startedAt"]) > 60
            or not 0 <= now.timestamp() - event_time.timestamp() <= 1800):
        raise ValueError("Stale or inconsistent app heartbeat.")
    return {"runner": HEARTBEAT_RUNNERS[match.group(1)], "automation_id": match.group(1),
            "thread_id": expected_thread, "turn_id": capture["turn"], "event_id": item["id"]}


def clean(value: Any) -> str:
    """Keep untrusted text inside a single presentation field."""
    return " ".join(str(value).replace("\x00", "").split())


def post_release_basis(issue: dict[str, Any]) -> str:
    return fingerprint({key: issue.get(key) for key in
                        ("id", "kind", "status", "title", "description", "targets", "sources", "post_release")})


def candidate_git_environment() -> dict[str, str]:
    """Remove ambient Git repository/index/config overrides for candidate checks."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def git_checkout_identity(directory: Path, timeout: float = 5,
                          env: dict[str, str] | None = None) -> dict[str, str]:
    """Read a candidate's worktree, shared repository and commit with a deadline."""
    try:
        output = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel",
                                 "--git-common-dir", "HEAD"], capture_output=True, text=True,
                                check=True, timeout=timeout, env=env).stdout.splitlines()
        if len(output) != 3 or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", output[2]):
            raise ValueError("Unexpected Git checkout identity.")
        root = Path(output[0]).resolve(strict=True)
        common = Path(output[1])
        if not common.is_absolute():
            common = directory / common
        return {"root": str(root), "common_dir": str(common.resolve(strict=True)), "head": output[2]}
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Candidate Git checkout identity is unavailable.") from exc


def github_remote_identity(directory: Path, env: dict[str, str] | None = None) -> tuple[dict[str, str], str]:
    try:
        env = env or {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        remote = subprocess.run(["git", "-C", str(directory), "remote", "get-url", "origin"],
                                capture_output=True, text=True, check=True, timeout=5, env=env).stdout.strip()
        match = re.fullmatch(r"(?:git@(?P<ssh>[A-Za-z0-9.-]+):|https://(?P<https>[A-Za-z0-9.-]+)/|"
                             r"ssh://git@(?P<ssh_url>[A-Za-z0-9.-]+)/)"
                             r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?", remote)
        if not match:
            raise ValueError("Unsupported GitHub remote.")
        return ({"host": match["ssh"] or match["https"] or match["ssh_url"],
                 "repository": f"{match['owner']}/{match['repo']}"}, remote)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Candidate GitHub remote identity is unavailable.") from exc


def candidate_release_identity(directory: Path) -> dict[str, Any]:
    env = candidate_git_environment()
    identity = git_checkout_identity(directory, env=env)
    identity["remote"] = github_remote_identity(directory, env=env)[0]
    return identity


def candidate_file_snapshot(root: Path, name: str, status: str) -> tuple[str | None, int | None]:
    """Hash one candidate path through no-follow directory descriptors."""
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                  | getattr(os, "O_CLOEXEC", 0))
    root_fd = os.open(os.sep, directory_flags)
    directory_fd = root_fd
    file_fd = None
    parts = Path(name).parts
    try:
        for component in root.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            if directory_fd != root_fd:
                os.close(directory_fd)
            directory_fd = next_fd
        for component in parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            if directory_fd != root_fd:
                os.close(directory_fd)
            directory_fd = next_fd
        try:
            file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        except FileNotFoundError:
            if "D" in status:
                return None, None
            raise ValueError("Candidate path disappeared during snapshot; retry the assessment.")
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Release assessment does not accept symlink or special-file changes.")
        digest = hashlib.sha256()
        while True:
            block = os.read(file_fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(file_fd)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                           before.st_ctime_ns, stat.S_IMODE(before.st_mode))
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                          after.st_ctime_ns, stat.S_IMODE(after.st_mode))
        if before_identity != after_identity:
            raise ValueError("Candidate file changed while it was being snapshotted; retry the assessment.")
        return digest.hexdigest(), stat.S_IMODE(after.st_mode)
    except OSError as exc:
        raise ValueError("Candidate path could not be opened without following symlinks; retry the assessment.") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd != root_fd:
            os.close(directory_fd)
        os.close(root_fd)


def candidate_change_snapshot(directory: Path, base_ref: str = "origin/main") -> dict[str, Any]:
    """Bind a release assessment to every committed and uncommitted candidate path."""
    root = Path(directory).resolve(strict=True)
    identity = candidate_release_identity(root)
    if base_ref != "origin/main":
        raise ValueError("Release assessments must use the verified origin/main target.")
    env = candidate_git_environment()

    def git(*args: str) -> bytes:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True,
                              timeout=8, env={**env, "GIT_LITERAL_PATHSPECS": "1"}).stdout

    try:
        target_commit = git("rev-parse", "--verify", "refs/remotes/origin/main^{commit}").decode().strip()
        base_commit = git("merge-base", target_commit, identity["head"]).decode().strip()
        committed = git("diff", "--name-status", "-z", base_commit, identity["head"])
        unmerged = git("ls-files", "--unmerged", "-z")
        if unmerged:
            raise ValueError("Release candidate has unmerged index entries; resolve conflicts before assessment.")
        working = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    except (OSError, subprocess.SubprocessError, UnicodeError) as exc:
        raise ValueError("Release target or candidate changes are unavailable.") from exc
    names: dict[str, str] = {}
    fields = committed.split(b"\0")
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index].decode("ascii", "strict")
        index += 1
        if status.startswith(("R", "C")):
            raise ValueError("Rename/copy changes need a separately reviewed release assessment.")
        if index >= len(fields):
            raise ValueError("Committed candidate diff is malformed.")
        name = os.fsdecode(fields[index]); index += 1
        names[name] = "committed:" + status
    fields = working.split(b"\0")
    index = 0
    while index < len(fields) and fields[index]:
        entry = fields[index]
        if len(entry) < 4 or entry[2:3] != b" ":
            raise ValueError("Working candidate status is malformed.")
        status = entry[:2].decode("ascii", "strict")
        index += 1
        if "R" in status or "C" in status:
            raise ValueError("Rename/copy changes need a separately reviewed release assessment.")
        name = os.fsdecode(entry[3:])
        names[name] = status
    rows = []
    for name, status in sorted(names.items()):
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Candidate diff contains an unsafe repository path.")
        try:
            base_entries = [entry for entry in git("ls-tree", "-z", base_commit, "--", name).split(b"\0") if entry]
            if len(base_entries) > 1:
                raise ValueError("Literal base lookup returned multiple candidate paths.")
            if base_entries:
                base_header, base_path = base_entries[0].split(b"\t", 1)
                if os.fsdecode(base_path) != name:
                    raise ValueError("Literal base lookup returned a different candidate path.")
                base_mode, _base_kind, base_blob = base_header.decode().split()
            else:
                base_mode, base_blob = None, None
            index_entries = [entry for entry in git("ls-files", "--stage", "-z", "--", name).split(b"\0")
                             if entry]
            if len(index_entries) > 1:
                raise ValueError("Candidate index lookup returned multiple stages or paths.")
            if index_entries:
                index_header, index_path = index_entries[0].split(b"\t", 1)
                if os.fsdecode(index_path) != name:
                    raise ValueError("Literal index lookup returned a different candidate path.")
                index_mode, index_blob, index_stage = index_header.decode().split()
                if index_stage != "0":
                    raise ValueError("Release candidate has an unmerged index entry.")
            else:
                index_mode, index_blob = None, None
        except (UnicodeError, ValueError):
            raise
        except subprocess.CalledProcessError as exc:
            raise ValueError("Candidate base or index path could not be verified.") from exc
        try:
            worktree_hash, mode = candidate_file_snapshot(root, name, status)
        except ValueError:
            raise
        rows.append({"path": name, "status": status, "base_mode": base_mode, "base_blob": base_blob,
                     "index_mode": index_mode, "index_blob": index_blob,
                     "worktree_sha256": worktree_hash, "mode": mode})
    return {"root": identity["root"], "common_dir": identity["common_dir"], "head": identity["head"],
            "remote": identity["remote"], "base_ref": base_ref, "target_commit": target_commit,
            "base_commit": base_commit,
            "files": rows, "change_sha256": fingerprint(rows)}


def release_issue_evidence_paths(issue: dict[str, Any]) -> list[dict[str, str]]:
    """List referenced evidence without opening or hydrating any saved reports."""
    evidence = issue.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > 32:
        raise ValueError("Issue evidence must be a bounded list of saved report paths.")
    for raw in evidence:
        if not isinstance(raw, str) or not Path(raw).expanduser().is_absolute():
            raise ValueError("Issue evidence must use absolute report paths in the learning store.")
    return [{"path": raw, "sha256": ""} for raw in evidence]


def release_assessment_skeleton(state: dict[str, Any], directory: Path,
                                base_ref: str = "origin/main") -> dict[str, Any]:
    records = []
    for key, issue in sorted(state["issues"].items()):
        if issue.get("status") in OPEN or issue.get("revalidation_required") or COHORT_FIELDS.intersection(issue):
            records.append({"issue_id": key, "issue_sha256": fingerprint(issue),
                            "evidence": release_issue_evidence_paths(issue),
                            "classification": "", "rationale": "", "reviewer": "",
                            "candidate_relation": "", "dependency_review": "",
                            "audit_deferral": ({"independent_review": {"path": "", "sha256": ""},
                                                 "authorization": {"path": "", "sha256": ""}}
                                                if issue.get("kind") == "audit" and issue.get("status") in OPEN
                                                else None),
                            "review_deferral": ({"independent_review": {"path": "", "sha256": ""},
                                                  "authorization": {"path": "", "sha256": ""}}
                                                 if issue.get("kind") == "review" and issue.get("status") in OPEN
                                                 else None), "candidate_integration": None})
    return {"version": 1, "candidate": candidate_change_snapshot(directory, base_ref),
            "assessments": records}


def validate_candidate_mapping(issue: dict[str, Any], post: dict[str, Any], candidate_git: dict[str, str]) -> None:
    """Bind each affected source to the same relative path in the candidate."""
    targets, candidate_targets = issue.get("targets"), post.get("candidate_targets")
    if not isinstance(targets, list) or not targets or not isinstance(candidate_targets, list) or not candidate_targets:
        raise ValueError("Candidate mapping needs affected source and candidate targets.")
    if len(targets) > 32 or len(candidate_targets) > 32:
        raise ValueError("Candidate mapping exceeds its bounded target count.")
    mapped = {}
    cache = {}
    deadline = time.monotonic() + 8
    for target in targets:
        source = Path(target["path"]).expanduser().resolve(strict=True)
        parent = source.parent
        if parent not in cache:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Candidate source mapping timed out.")
            cache[parent] = git_checkout_identity(parent, timeout=min(5, remaining),
                                                  env=candidate_git_environment())
        source_git = cache[parent]
        if source_git["common_dir"] != candidate_git["common_dir"]:
            raise ValueError("The affected target belongs to a different Git repository.")
        try:
            relative = source.relative_to(Path(source_git["root"]))
        except ValueError as exc:
            raise ValueError("The affected target is outside its Git worktree.") from exc
        mapped[str((Path(candidate_git["root"]) / relative).resolve())] = target["sha256"]
    candidate_refs = {str(Path(ref["path"]).resolve()): ref["sha256"] for ref in candidate_targets}
    if len(candidate_refs) != len(candidate_targets) or candidate_refs != mapped:
        raise ValueError("Each candidate path must match the affected target's repository-relative path and hash.")


def candidate_changelog_version(path: Path, expected_sha256: str) -> str:
    """Read real top-level release headings, ignoring comments and code fences."""
    with path.open("rb") as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Candidate changelog changed or is too large.")
    fence = None
    comment = False
    versions = []
    for line in raw.decode("utf-8").splitlines():
        if fence:
            closing = re.match(r"^ {0,3}([`~]{3,})[ \t]*$", line)
            if closing and closing[1][0] == fence[0] and len(closing[1]) >= fence[1] and len(set(closing[1])) == 1:
                fence = None
            continue
        if (("`" in line and ("<!--" in line or "-->" in line))
                or "\\<!--" in line or "\\-->" in line):
            raise ValueError("Ambiguous inline comment syntax in candidate changelog.")
        visible = ""
        rest = line
        while rest:
            if comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                    break
                comment, rest = False, rest[end + 3:]
            else:
                start = rest.find("<!--")
                if start < 0:
                    visible += rest
                    break
                visible += rest[:start]
                comment, rest = True, rest[start + 4:]
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", visible)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            fence = (opening[1][0], len(opening[1]))
            continue
        if re.match(r"^ {0,3}<", visible):
            raise ValueError("Raw HTML blocks are unsupported in a candidate changelog.")
        match = re.match(r"^##[ \t]+\[v?((?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))\](?:[ \t]|$)", visible)
        if match:
            versions.append(match[1])
    if fence or comment or not versions or len(set(versions)) != len(versions):
        raise ValueError("Candidate changelog has missing or duplicate release headings.")
    return versions[0]


def bounded_candidate_version(path: Path, expected_sha256: str) -> str:
    worker = ("import json,sys; from pathlib import Path; "
              "from learning_pipeline import candidate_changelog_version; "
              "print(json.dumps(candidate_changelog_version(Path(sys.argv[1]),sys.argv[2])))")
    try:
        result = subprocess.run([sys.executable, "-c", worker, str(path), expected_sha256],
                                cwd=str(Path(__file__).resolve().parent), capture_output=True,
                                text=True, check=True, timeout=5)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ValueError("Candidate changelog version is unavailable or ambiguous.") from exc


def verified_published_release(post: dict[str, Any]) -> dict[str, Any]:
    """Read back a published GitHub release and its exact remote tag commit."""
    try:
        candidate = post["candidate_git"]
        root = Path(candidate["root"])
        if candidate_release_identity(root) != candidate:
            raise ValueError("Candidate checkout changed.")
        version = post["version"]
        if not isinstance(version, str) or not RELEASE_VERSION.fullmatch(version):
            raise ValueError("Invalid release version.")
        env = candidate_git_environment()
        for ref in post["candidate_targets"] + [post["candidate_changelog"]]:
            relative = Path(ref["path"]).resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
            revision_path = candidate["head"] + ":" + relative
            size = subprocess.run(["git", "-C", str(root), "cat-file", "-s", revision_path],
                                  capture_output=True, text=True, check=True, timeout=8, env=env).stdout.strip()
            if not size.isdigit() or int(size) > 16 * 1024 * 1024:
                raise ValueError("Published candidate file is unavailable or too large.")
            blob = subprocess.run(["git", "-C", str(root), "cat-file", "blob", revision_path],
                                  capture_output=True, check=True, timeout=8, env=env).stdout
            if len(blob) != int(size) or hashlib.sha256(blob).hexdigest() != ref["sha256"]:
                raise ValueError("Published candidate content differs from the reviewed target.")
        host = candidate["remote"]["host"]
        owner, repo = candidate["remote"]["repository"].split("/", 1)
        tag = "v" + version
        remote_identity, remote_url = github_remote_identity(root, env=env)
        if remote_identity != candidate["remote"]:
            raise ValueError("GitHub remote changed.")
        api_path = f"repos/{owner}/{repo}/releases/tags/{tag}"
        result = subprocess.run(["gh", "api", "--hostname", host, api_path],
                                capture_output=True, text=True, check=True, timeout=15, env=env)
        release = json.loads(result.stdout)
        expected_url = f"https://{host}/{owner}/{repo}/releases/tag/{tag}"
        if (not isinstance(release, dict) or type(release.get("id")) is not int or release["id"] <= 0
                or release.get("tag_name") != tag or release.get("draft") is not False
                or release.get("prerelease") is not False or not isinstance(release.get("published_at"), str)
                or not release["published_at"] or release.get("html_url") != expected_url):
            raise ValueError("Published release readback is incomplete.")
        tag_ref = "refs/tags/" + tag
        result = subprocess.run(["git", "-C", str(root), "ls-remote", "--tags", remote_url, tag_ref, tag_ref + "^{}"],
                                capture_output=True, text=True, check=True, timeout=15, env=env)
        refs = {}
        for line in result.stdout.splitlines():
            sha, ref = line.split("\t", 1)
            if ref in refs or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", sha):
                raise ValueError("Invalid remote tag readback.")
            refs[ref] = sha
        if tag_ref not in refs or refs.get(tag_ref + "^{}", refs[tag_ref]) != candidate["head"]:
            raise ValueError("Published tag does not point to the candidate commit.")
        if candidate_release_identity(root) != candidate:
            raise ValueError("Candidate checkout changed during publication readback.")
        return {"host": host, "repository": f"{owner}/{repo}", "tag": tag,
                "tag_object": refs[tag_ref], "commit": candidate["head"], "release_id": release["id"],
                "url": expected_url, "published_at": release["published_at"],
                "observed_at": datetime.now().astimezone().isoformat()}
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("The published release and exact tag commit could not be verified.") from exc


def chat_url(thread_id: str) -> str:
    if not THREAD.fullmatch(thread_id):
        raise ValueError("A verified local Codex thread UUID is required.")
    return f"https://chatgpt.com/codex/open-app?codex_thread_id={thread_id}"


def native_chat_url(thread_id: str) -> str:
    if not THREAD.fullmatch(thread_id):
        raise ValueError("A verified local Codex thread UUID is required.")
    return f"codex://threads/{thread_id}"


def snapshot(raw: str) -> dict[str, str]:
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Expected a file: {path}")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_regular_file_nofollow(path: Path, max_bytes: int, timeout: float = 8) -> bytes:
    """Read an absolute path without following any path-component symlinks."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Evidence path must be absolute and normalized.")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    root_fd = os.open(os.sep, directory_flags)
    directory_fd = root_fd
    file_fd = None
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            if directory_fd != root_fd:
                os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(path.parts[-1], file_flags, dir_fd=directory_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError("Evidence must be a bounded regular file.")
        chunks = []
        total = 0
        deadline = time.monotonic() + timeout
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("Evidence read timed out.")
            chunk = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Evidence exceeds its size limit.")
        after = os.fstat(file_fd)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                           before.st_ctime_ns, stat.S_IMODE(before.st_mode))
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                          after.st_ctime_ns, stat.S_IMODE(after.st_mode))
        if before_identity != after_identity or total != after.st_size:
            raise ValueError("Evidence changed while being read.")
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError("Evidence path is unavailable without following symlinks.") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd != root_fd:
            os.close(directory_fd)
        os.close(root_fd)


def current_release_ref(ref: dict[str, Any], timeout: float = 8) -> bool:
    """Verify canonical snapshot evidence through a bounded no-follow read."""
    try:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            return False
        digest = ref.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            return False
        raw = read_regular_file_nofollow(Path(ref["path"]), 16 * 1024 * 1024, timeout)
        return hashlib.sha256(raw).hexdigest() == digest
    except (OSError, TypeError, ValueError):
        return False


def verify_release_issue_evidence(issue: dict[str, Any], manifest_refs: Any, reports_root: Path,
                                  cache: dict[tuple[str, str], bool], deadline: float
                                  ) -> list[dict[str, str]]:
    expected = release_issue_evidence_paths(issue)
    if not isinstance(manifest_refs, list) or len(manifest_refs) != len(expected):
        raise ValueError("Every referenced issue report needs an explicit reviewed hash.")
    root = reports_root.resolve(strict=True)
    verified = []
    for expected_ref, ref in zip(expected, manifest_refs):
        if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
                or ref.get("path") != expected_ref["path"]
                or not isinstance(ref.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])):
            raise ValueError("Issue evidence review needs its exact path and current SHA-256.")
        try:
            canonical = Path(ref["path"]).expanduser().resolve(strict=True)
            if not canonical.is_relative_to(root):
                raise ValueError("Issue evidence must resolve inside the learning store reports directory.")
        except (OSError, RuntimeError) as exc:
            raise ValueError("Issue evidence path is unavailable; reassess it.") from exc
        key = (str(canonical), ref["sha256"])
        if key not in cache:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not current_release_ref({"path": key[0], "sha256": key[1]}, remaining):
                raise ValueError("Issue evidence is stale, unavailable or exceeded the review budget.")
            cache[key] = True
        verified.append({"path": ref["path"], "resolved_path": key[0], "sha256": key[1]})
    return verified


def current_release_issue_evidence(ref: dict[str, str], reports_root: Path, timeout: float = 8) -> bool:
    try:
        canonical = Path(ref["path"]).expanduser().resolve(strict=True)
        if (str(canonical) != ref["resolved_path"]
                or not canonical.is_relative_to(reports_root.resolve(strict=True))):
            return False
        return current_release_ref({"path": str(canonical), "sha256": ref["sha256"]}, timeout)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False


def verified_release_artifact(ref: Any, reports_root: Path, cache: dict, deadline: float,
                              label: str, prefix: str = "Audit") -> tuple[dict[str, str], dict[str, Any]]:
    if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
            or not isinstance(ref.get("path"), str) or not Path(ref["path"]).expanduser().is_absolute()
            or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256", "")))):
        raise ValueError(f"{prefix} {label} needs a saved report path and SHA-256.")
    try:
        canonical = Path(ref["path"]).expanduser().resolve(strict=True)
        if not canonical.is_relative_to(reports_root.resolve(strict=True)):
            raise ValueError(f"{prefix} {label} must be stored under the learning store reports directory.")
    except (OSError, RuntimeError) as exc:
            raise ValueError(f"{prefix} {label} report is unavailable; reassess it.") from exc
    key = (str(canonical), ref["sha256"])
    if key not in cache:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not _secure_scoped_ref({"path": key[0], "sha256": key[1]}, remaining):
            raise ValueError(f"{prefix} {label} report is stale, unavailable or exceeded the review budget.")
        raw = read_regular_file_nofollow(canonical, 1024 * 1024, max(0.01, deadline - time.monotonic()))
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError(f"{prefix} {label} report changed while being read.")
        try:
            value = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{prefix} {label} report is invalid JSON.") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{prefix} {label} report must contain a JSON object.")
        cache[key] = value
    return {"path": ref["path"], "resolved_path": key[0], "sha256": key[1]}, cache[key]


def audit_issue_matches_report(issue: dict[str, Any], evidence_refs: list[dict[str, str]],
                               artifact_cache: dict, reports_root: Path, deadline: float) -> bool:
    targets = issue.get("targets")
    if not isinstance(targets, list) or not targets or any(not isinstance(ref, dict) for ref in targets):
        return False
    target_map = {ref.get("path"): ref.get("sha256") for ref in targets
                  if isinstance(ref.get("path"), str) and isinstance(ref.get("sha256"), str)}
    if len(target_map) != len(targets):
        return False
    if COHORT_FIELDS.intersection(issue):
        cohort_targets = sorted(target_map)
        latest = issue.get("latest_audit")
        if (issue.get("id") != "audit-" + fingerprint(["exact-file-pair-v1", cohort_targets])[:24]
                or not isinstance(latest, dict)
                or not any(ref.get("path") == latest.get("path") and ref.get("sha256") == latest.get("sha256")
                           for ref in evidence_refs)
                or not current_scoped_cohorts([issue])):
            return False
        return any(ref.get("path") == latest.get("path") and ref.get("sha256") == latest.get("sha256")
                   for ref in evidence_refs)
    issue_id = issue.get("id")
    for ref in evidence_refs:
        try:
            _resolved_ref, saved = verified_release_artifact(
                {"path": ref["path"], "sha256": ref["sha256"]}, reports_root, artifact_cache, deadline,
                "finding evidence")
            audit = saved.get("audit")
            if not isinstance(audit, dict) or not isinstance(audit.get("file_snapshots"), list):
                continue
            snapshots = audit["file_snapshots"]
            if any(sum(item.get("path") == path and item.get("sha256") == digest for item in snapshots) != 1
                   for path, digest in target_map.items()):
                continue
            groups = [("exact", group) for group in audit.get("exact_duplicates", []) if isinstance(group, list)]
            groups.extend(("similar", [pair.get("a"), pair.get("b")])
                          for pair in audit.get("near_duplicates", []) if isinstance(pair, dict))
            groups.extend(("conflict", [pair.get("a"), pair.get("b")])
                          for pair in audit.get("potential_conflicts", []) if isinstance(pair, dict))
            for kind, members in groups:
                if any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
                       or not isinstance(item.get("text"), str) for item in members):
                    continue
                identity = sorted({(item["path"], item["text"]) for item in members})
                if "audit-" + fingerprint([kind, identity])[:24] != issue_id:
                    continue
                if {path for path, _text in identity} == set(target_map):
                    return True
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return False
    return False


def verify_audit_deferral(issue: dict[str, Any], record: dict[str, Any], candidate: dict[str, Any],
                          evidence_refs: list[dict[str, str]], reports_root: Path, artifact_cache: dict,
                          deadline: float) -> list[dict[str, str]]:
    issue_id = issue.get("id", "")
    if (issue.get("kind") != "audit" or issue.get("status") not in {"candidate", "investigate"}
            or issue.get("revalidation_required") or issue.get("status") == "needs-user"
            or issue_id.startswith(("audit-removal-", "audit-parser-", "audit-coherence-", "goal-reconcile-"))):
        raise ValueError("Only current, unblocked audit findings may be deferred for this candidate.")
    if not audit_issue_matches_report(issue, evidence_refs, artifact_cache, reports_root, deadline):
        raise ValueError("Audit deferral requires a current saved report that proves the exact finding and targets.")
    receipt = record.get("audit_deferral")
    if not isinstance(receipt, dict) or set(receipt) != {"independent_review", "authorization"}:
        raise ValueError("Audit deferral requires independent review and candidate-specific user authorization reports.")
    review_ref, review = verified_release_artifact(receipt["independent_review"], reports_root,
                                                    artifact_cache, deadline, "independent review")
    authorization_ref, authorization = verified_release_artifact(receipt["authorization"], reports_root,
                                                                  artifact_cache, deadline, "authorization")
    candidate_hash = fingerprint(candidate)
    target_refs = sorted(({"path": ref["path"], "sha256": ref["sha256"]}
                          for ref in issue.get("targets", [])), key=lambda ref: ref["path"])
    issue_evidence = sorted(({"path": ref["path"], "sha256": ref["sha256"]}
                             for ref in evidence_refs), key=lambda ref: ref["path"])
    review_fields = {"schema_version", "review_kind", "decision", "issue_id", "issue_sha256",
                     "candidate_sha256", "reviewer", "assessment_author", "rationale",
                     "candidate_relation", "dependency_review", "targets", "evidence"}
    if (set(review) != review_fields or review.get("schema_version") != 1
            or review.get("review_kind") != "candidate-audit-relevance"
            or review.get("decision") != "unrelated" or review.get("issue_id") != issue_id
            or review.get("issue_sha256") != fingerprint(issue)
            or review.get("candidate_sha256") != candidate_hash
            or not isinstance(review.get("reviewer"), str) or len(review["reviewer"].strip()) < 3
            or review.get("reviewer", "").strip().casefold() == record.get("reviewer", "").strip().casefold()
            or review.get("assessment_author") != record.get("reviewer")
            or review.get("rationale") != record.get("rationale")
            or review.get("candidate_relation") != record.get("candidate_relation")
            or review.get("dependency_review") != record.get("dependency_review")
            or review.get("targets") != target_refs or review.get("evidence") != issue_evidence):
        raise ValueError("Independent audit review must approve the exact current finding and candidate as unrelated.")
    auth_fields = {"schema_version", "decision", "scope", "issue_id", "issue_sha256", "candidate_sha256",
                   "independent_review", "user_request_reference", "thread_id", "recorded_at"}
    recorded_at = authorization.get("recorded_at")
    try:
        timestamp = datetime.fromisoformat(recorded_at)
    except (TypeError, ValueError):
        timestamp = None
    if (set(authorization) != auth_fields or authorization.get("schema_version") != 1
            or authorization.get("decision") != "approved"
            or authorization.get("scope") != "candidate-audit-deferral"
            or authorization.get("issue_id") != issue_id
            or authorization.get("issue_sha256") != fingerprint(issue)
            or authorization.get("candidate_sha256") != candidate_hash
            or authorization.get("independent_review") != {"path": review_ref["path"], "sha256": review_ref["sha256"]}
            or not isinstance(authorization.get("user_request_reference"), str)
            or len(authorization["user_request_reference"].strip()) < 8
            or not isinstance(authorization.get("thread_id"), str)
            or not THREAD.fullmatch(authorization["thread_id"])
            or timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() is None):
        raise ValueError("Audit deferral authorization must bind the exact issue, candidate and independent review.")
    return [review_ref, authorization_ref]


def verify_issue_deferral(issue: dict[str, Any], record: dict[str, Any], candidate: dict[str, Any],
                          evidence_refs: list[dict[str, str]], reports_root: Path, artifact_cache: dict,
                          deadline: float) -> list[dict[str, str]]:
    """Verify candidate-specific, independently reviewed authorization for an open non-audit issue."""
    issue_id = issue.get("id", "")
    if (issue.get("kind") != "review" or issue.get("status") not in {"candidate", "investigate"}
            or issue.get("revalidation_required")
            or issue_id.startswith(("goal-reconcile-", "audit-", "audit-removal-", "audit-parser-", "audit-coherence-"))
            or issue.get("post_release") is not None or issue.get("post_release_binding") is not None
            or any(isinstance(entry, dict) and entry.get("status") in {"candidate", "needs-user"}
                   for entry in issue.get("history", []))):
        raise ValueError("Only current, unprotected review issues may be deferred for this candidate.")
    receipt = record.get("review_deferral")
    if not isinstance(receipt, dict) or set(receipt) != {"independent_review", "authorization"}:
        raise ValueError("Review deferral requires independent review and candidate-specific user authorization reports.")
    review_ref, review = verified_release_artifact(receipt["independent_review"], reports_root,
                                                    artifact_cache, deadline, "independent issue review", "Review")
    authorization_ref, authorization = verified_release_artifact(receipt["authorization"], reports_root,
                                                                  artifact_cache, deadline, "issue authorization", "Review")
    candidate_hash = fingerprint(candidate)

    def current_refs(field: str) -> list[dict[str, str]]:
        refs = issue.get(field)
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"Review deferral requires current {field} evidence.")
        normalized = []
        seen = set()
        for ref in refs:
            if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                    or not isinstance(ref.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])):
                raise ValueError(f"Review deferral requires current {field} evidence.")
            normalized_ref = {"path": ref["path"], "sha256": ref["sha256"]}
            if normalized_ref["path"] in seen or not _secure_scoped_ref(normalized_ref, max(0.01, deadline - time.monotonic())):
                raise ValueError(f"Review deferral requires current {field} evidence.")
            seen.add(normalized_ref["path"])
            normalized.append(normalized_ref)
        return sorted(normalized, key=lambda ref: ref["path"])

    source_refs = current_refs("sources")
    target_refs = current_refs("targets")
    if not evidence_refs:
        raise ValueError("Review deferral requires current issue evidence.")
    issue_evidence = sorted(({"path": ref["path"], "sha256": ref["sha256"]}
                             for ref in evidence_refs), key=lambda ref: ref["path"])
    review_fields = {"schema_version", "review_kind", "decision", "issue_id", "issue_sha256",
                     "candidate_sha256", "reviewer", "assessment_author", "rationale",
                     "candidate_relation", "dependency_review", "sources", "targets", "evidence"}
    if (set(review) != review_fields or review.get("schema_version") != 1
            or review.get("review_kind") != "candidate-issue-relevance"
            or review.get("decision") != "unrelated" or review.get("issue_id") != issue_id
            or review.get("issue_sha256") != fingerprint(issue)
            or review.get("candidate_sha256") != candidate_hash
            or not isinstance(review.get("reviewer"), str) or len(review["reviewer"].strip()) < 3
            or review["reviewer"].strip().casefold() == record.get("reviewer", "").strip().casefold()
            or review.get("assessment_author") != record.get("reviewer")
            or review.get("rationale") != record.get("rationale")
            or review.get("candidate_relation") != record.get("candidate_relation")
            or review.get("dependency_review") != record.get("dependency_review")
            or review.get("sources") != source_refs or review.get("targets") != target_refs
            or review.get("evidence") != issue_evidence):
        raise ValueError("Independent review must approve the exact current review issue and candidate as unrelated.")
    auth_fields = {"schema_version", "decision", "scope", "issue_id", "issue_sha256", "candidate_sha256",
                   "independent_review", "user_request_reference", "thread_id", "recorded_at"}
    recorded_at = authorization.get("recorded_at")
    try:
        timestamp = datetime.fromisoformat(recorded_at)
    except (TypeError, ValueError):
        timestamp = None
    if (set(authorization) != auth_fields or authorization.get("schema_version") != 1
            or authorization.get("decision") != "approved"
            or authorization.get("scope") != "candidate-review-release-deferral"
            or authorization.get("issue_id") != issue_id
            or authorization.get("issue_sha256") != fingerprint(issue)
            or authorization.get("candidate_sha256") != candidate_hash
            or authorization.get("independent_review") != {"path": review_ref["path"], "sha256": review_ref["sha256"]}
            or not isinstance(authorization.get("user_request_reference"), str)
            or len(authorization["user_request_reference"].strip()) < 8
            or not isinstance(authorization.get("thread_id"), str)
            or not THREAD.fullmatch(authorization["thread_id"])
            or timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() is None):
        raise ValueError("Review deferral authorization must bind the exact issue, candidate and independent review.")
    return [review_ref, authorization_ref]


def verify_unrelated_issue(issue: dict[str, Any], record: dict[str, Any], candidate: dict[str, Any],
                           candidate_root: Path,
                           expected_paths: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """Validate a reviewer attestation without opening external target or source files."""
    issue_id = issue.get("id", "")
    if (issue.get("kind") not in {"review", "operational", "audit"} or issue.get("status") != "investigate"
            or issue.get("revalidation_required") or COHORT_FIELDS.intersection(issue)
            or issue.get("post_release") is not None or issue.get("post_release_binding") is not None
            or issue_id.startswith(("goal-reconcile-", "audit-parser-", "audit-removal-", "audit-coherence-"))
            or any(isinstance(entry, dict) and entry.get("status") in {"candidate", "needs-user"}
                   for entry in issue.get("history", []))):
        raise ValueError("Only unprotected, current investigations may be marked unrelated.")

    try:
        root = str(Path(candidate_root).resolve(strict=True))
    except (OSError, RuntimeError) as exc:
        raise ValueError("Candidate path is ambiguous during unrelated review.") from exc
    resolved_paths = []
    for field in ("sources", "targets"):
        refs = issue.get(field)
        if not isinstance(refs, list) or (field == "targets" and not refs):
            raise ValueError("An unrelated finding needs explicit, well-formed source and target references.")
        seen = set()
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
                    or not isinstance(ref.get("path"), str) or not Path(ref["path"]).is_absolute()
                    or not isinstance(ref.get("sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", ref["sha256"])):
                raise ValueError("An unrelated finding needs explicit, well-formed source and target references.")
            try:
                path = str(Path(ref["path"]).resolve(strict=False))
            except (OSError, RuntimeError) as exc:
                raise ValueError("An unrelated finding has an ambiguous path reference.") from exc
            if path in seen:
                raise ValueError("An unrelated finding has ambiguous duplicate references.")
            seen.add(path)
            try:
                if os.path.commonpath((root, path)) == root:
                    raise ValueError("A finding that references the candidate cannot be classified unrelated.")
            except ValueError as exc:
                if "cannot be classified unrelated" in str(exc):
                    raise
                raise ValueError("An unrelated finding has an ambiguous path reference.") from exc
            resolved_paths.append({"path": ref["path"], "resolved_path": path})

    if expected_paths is not None and resolved_paths != expected_paths:
        raise ValueError("An unrelated finding path changed during release verification.")

    if record.get("evidence") != []:
        raise ValueError("Unrelated findings must not trigger reads of external issue evidence.")
    review = record.get("scope_review")
    review_fields = {"decision", "issue_sha256", "candidate_sha256", "reviewer", "assessment_author",
                     "rationale", "candidate_relation", "dependency_review"}
    if (not isinstance(review, dict) or set(review) != review_fields
            or review.get("decision") != "unrelated"
            or review.get("issue_sha256") != fingerprint(issue)
            or review.get("candidate_sha256") != fingerprint(candidate)
            or not isinstance(review.get("reviewer"), str) or len(review["reviewer"].strip()) < 3
            or review["reviewer"].strip().casefold() == record.get("reviewer", "").strip().casefold()
            or review.get("assessment_author") != record.get("reviewer")
            or any(not isinstance(review.get(field), str) or len(clean(review[field])) < minimum
                   for field, minimum in (("rationale", 40), ("candidate_relation", 20),
                                          ("dependency_review", 20)))):
        raise ValueError("Unrelated classification needs a distinct reviewer attestation bound to this issue and candidate.")
    return resolved_paths


def verify_candidate_integration(issue: dict[str, Any], record: dict[str, Any], candidate: dict[str, Any],
                                 reports_root: Path, artifact_cache: dict, deadline: float,
                                 learning_root: Path, issue_evidence: list[dict[str, str]]) -> list[dict[str, str]]:
    """Verify current candidate-only integration without recording historical integration state."""
    if (issue.get("kind") != "operational" or issue.get("status") != "investigate"
            or issue.get("revalidation_required")
            or issue.get("status") == "needs-user"
            or issue.get("post_release") is not None or issue.get("post_release_binding") is not None
            or any(isinstance(item, dict) and item.get("status") in {"candidate", "needs-user"}
                   for item in issue.get("history", []))
            or issue.get("id", "").startswith(("goal-reconcile-", "audit-"))):
        raise ValueError("Candidate-only verification is limited to current operational investigations.")
    targets = issue.get("targets")
    if (not isinstance(targets, list) or not targets or len(targets) > 32
            or any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                   or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256", "")))
                   or not _secure_scoped_ref(ref, max(0.01, deadline - time.monotonic())) for ref in targets)):
        raise ValueError("Candidate-only verification needs current nonempty issue targets.")
    receipt = record.get("candidate_integration")
    if (not isinstance(receipt, dict) or set(receipt) != {"manifests", "independent_review"}
            or not isinstance(receipt.get("manifests"), list) or not receipt["manifests"]
            or len(receipt["manifests"]) > 8):
        raise ValueError("Candidate verification needs bounded integration manifests and an independent review.")
    refs, manifests = [], []
    seen_manifest_paths = set()
    note_ids = []
    for raw_ref in receipt["manifests"]:
        ref, manifest = verified_release_artifact(raw_ref, reports_root, artifact_cache, deadline,
                                                 "candidate integration manifest", "Candidate")
        if ref["resolved_path"] in seen_manifest_paths:
            raise ValueError("Candidate integration manifests must be unique.")
        seen_manifest_paths.add(ref["resolved_path"])
        ids = manifest.get("note_ids")
        if (manifest.get("version") != 1 or not isinstance(ids, list) or not ids or len(ids) > 128
                or any(not isinstance(item, str) or not item for item in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("Candidate integration manifest needs current archived note IDs.")
        refs.append(ref)
        manifests.append(manifest)
        note_ids.extend(ids)
    if len(set(note_ids)) != len(note_ids):
        raise ValueError("Candidate integration manifests must cover distinct notes.")
    evidenced_note_ids = set()
    for ref in issue_evidence:
        raw = read_regular_file_nofollow(Path(ref["resolved_path"]), 1024 * 1024,
                                         max(0.01, deadline - time.monotonic()))
        if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            raise ValueError("Candidate issue evidence changed while resolving note IDs.")
        try:
            payload = json.loads(raw)
        except (UnicodeError, json.JSONDecodeError):
            continue
        pending = [payload]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for name, child in value.items():
                    if name == "note_id" and isinstance(child, str):
                        evidenced_note_ids.add(child)
                    elif name == "note_ids" and isinstance(child, list):
                        evidenced_note_ids.update(item for item in child if isinstance(item, str))
                    pending.append(child)
            elif isinstance(value, list):
                pending.extend(value)
            if len(evidenced_note_ids) > 512:
                raise ValueError("Candidate issue evidence contains too many note identifiers.")
    if not note_ids or not set(note_ids).issubset(evidenced_note_ids):
        raise ValueError("Candidate note IDs must appear in current issue evidence.")
    review_ref, review = verified_release_artifact(receipt["independent_review"], reports_root,
                                                    artifact_cache, deadline, "candidate integration review", "Candidate")
    review_fields = {"schema_version", "review_kind", "decision", "issue_id", "issue_sha256",
                     "candidate_sha256", "reviewer", "assessment_author", "manifests", "note_ids",
                     "note_hashes", "evidence", "rationale"}
    expected_manifest_refs = [{"path": raw["path"], "sha256": raw["sha256"]}
                              for raw in receipt["manifests"]]
    expected_evidence = sorted(({"path": ref["path"], "sha256": ref["sha256"]}
                                for ref in issue_evidence), key=lambda ref: ref["path"])
    coherence_reviewers = set()
    for manifest in manifests:
        coherence = manifest.get("coherence", {})
        coherence_reviewers.add(str(coherence.get("editor", "")).strip().casefold())
        coherence_reviewers.update(str(item.get("reviewer", "")).strip().casefold()
                                   for item in coherence.get("reviews", []) if isinstance(item, dict))
    independent_reviewer = str(review.get("reviewer", "")).strip().casefold()
    if (set(review) != review_fields or review.get("schema_version") != 1
            or review.get("review_kind") != "candidate-integration"
            or review.get("decision") != "verified"
            or review.get("issue_id") != issue.get("id")
            or review.get("issue_sha256") != fingerprint(issue)
            or review.get("candidate_sha256") != fingerprint(candidate)
            or review.get("manifests") != expected_manifest_refs
            or review.get("note_ids") != sorted(note_ids)
            or review.get("evidence") != expected_evidence
            or not isinstance(review.get("reviewer"), str) or len(review["reviewer"].strip()) < 3
            or independent_reviewer == record.get("reviewer", "").strip().casefold()
            or independent_reviewer in coherence_reviewers
            or review.get("assessment_author") != record.get("reviewer")
            or not isinstance(review.get("rationale"), str) or len(review["rationale"].strip()) < 40):
        raise ValueError("Independent review must bind the exact issue, candidate and integration manifests.")
    refs.append(review_ref)

    worker = r'''import hashlib,json,os,sys,time
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from agent_learning import load_state, validate_relocation_bindings, verify_manifest_bounded, integration_entries, parse_frontmatter
from learning_integration import concrete_routing_target_path
from learning_pipeline import candidate_release_identity, git_checkout_identity, read_regular_file_nofollow, snapshot
root=Path(sys.argv[1]).resolve()
candidate=json.loads(sys.argv[2])
manifests=json.load(sys.stdin)
state=load_state(root, deadline=time.monotonic()+8)
expected_identity={k:candidate[k] for k in ("root","common_dir","head","remote")}
actual_identity=candidate_release_identity(Path(candidate["root"]))
if actual_identity != expected_identity: raise ValueError("Candidate checkout identity changed.")
all_covered=set(); mapped_targets={}; note_hashes={}
for manifest in manifests:
    validate_relocation_bindings(root, manifest, state, deadline=time.monotonic()+8)
    verified=verify_manifest_bounded(manifest, timeout=25)
    covered_hashes=dict(verified.get("files",{}))
    covered_hashes.update(verified.get("generated",{}))
    for skill_root,payload in verified.get("skills",{}).items():
        covered_hashes.update({str(Path(skill_root)/name):value for name,value in payload.items()})
    for note_id,entry in zip(manifest["note_ids"], integration_entries(root,state,manifest["note_ids"])):
        note_raw=Path(entry["path"])
        if note_raw.is_symlink(): raise ValueError("Candidate verification archive cannot be a symbolic link.")
        note_path=note_raw.resolve(strict=True)
        if not note_path.is_relative_to((root/"processed").resolve(strict=True)):
            raise ValueError("Candidate verification note is outside the processed archive.")
        note_raw_bytes=read_regular_file_nofollow(note_path,16*1024*1024)
        observed_note_sha=hashlib.sha256(note_raw_bytes).hexdigest()
        if entry.get("archive_sha256") and observed_note_sha!=entry["archive_sha256"]:
            raise ValueError("Candidate verification note changed.")
        fm,_body=parse_frontmatter(note_raw_bytes.decode("utf-8"),require_note_identity=True)
        if fm.get("id")!=note_id: raise ValueError("Candidate verification archive identity differs from processed state.")
        for field in ("prevention_targets","detection_targets"):
            try: frontmatter_targets=json.loads(fm.get(field,"[]"))
            except (TypeError,ValueError): raise ValueError("Candidate archive routing metadata is malformed.")
            if frontmatter_targets!=entry.get(field,[]): raise ValueError("Candidate archive routing metadata differs from processed state.")
        note_hashes[note_id]=observed_note_sha
        mapped_this_note=False
        for raw in entry.get("prevention_targets",[])+entry.get("detection_targets",[]):
            source=concrete_routing_target_path(raw)
            if source is None: continue
            source=source.resolve(strict=True)
            source_identity=git_checkout_identity(source.parent)
            if source_identity["common_dir"]==candidate["common_dir"]:
                relative=source.relative_to(Path(source_identity["root"]))
                target=Path(candidate["root"])/relative
                target_key=str(target.resolve())
                if target_key not in covered_hashes:
                    raise ValueError("Candidate manifest omits a mapped note target.")
                actual=snapshot(str(target))['sha256']
                if actual!=covered_hashes[target_key]: raise ValueError("Candidate mapped target changed.")
                mapped_targets[target_key]=actual
                mapped_this_note=True
            elif str(source) not in covered_hashes:
                raise ValueError("Candidate manifest omits an archived note target.")
            else:
                mapped_this_note=True
        if not mapped_this_note: raise ValueError("Candidate verification note has no bound target.")
    all_covered.update(covered_hashes)
issue=json.loads(sys.argv[4])
if not issue.get("targets"): raise ValueError("Candidate-only verification needs current nonempty issue targets.")
for ref in issue["targets"]:
    path=str(Path(ref["path"]).resolve(strict=True))
    if path not in mapped_targets or mapped_targets[path]!=ref.get("sha256"):
        raise ValueError("Candidate integration does not cover the current operational target.")
print(json.dumps({"note_ids":sorted({i for m in manifests for i in m["note_ids"]}),
                  "note_hashes":note_hashes,"targets":sorted(all_covered)}))'''
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("Candidate integration verification budget exhausted.")
    try:
        result = subprocess.run([sys.executable, "-c", worker, str(learning_root),
                                 json.dumps(candidate), str(Path(__file__).resolve().parent),
                                 json.dumps(issue)], input=json.dumps(manifests), text=True,
                                capture_output=True, check=True, timeout=min(35, remaining),
                                env=candidate_git_environment())
        verified = json.loads(result.stdout)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()
        raise ValueError(detail[-1][:500] if detail else "Candidate integration verification failed.") from exc
    except (OSError, subprocess.SubprocessError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Candidate integration verification is unavailable or timed out.") from exc
    if verified.get("note_ids") != sorted(note_ids):
        raise ValueError("Candidate integration note coverage changed during verification.")
    if review.get("note_hashes") != verified.get("note_hashes"):
        raise ValueError("Independent review must bind the current archived note hashes.")
    return refs


def current(ref: dict[str, str]) -> bool:
    try:
        return snapshot(ref["path"])["sha256"] == ref["sha256"]
    except (OSError, KeyError, ValueError):
        return False


def cohort_members(findings: list[list[dict[str, Any]]]) -> list:
    return sorted([sorted((item["path"], str(item.get("line", "")), item["text"])
                          for item in group) for group in findings])


def exact_issue_id(group: list[dict[str, Any]]) -> str:
    identity = sorted({(item["path"], item["text"]) for item in group})
    return "audit-" + fingerprint(["exact", identity])[:24]


def verified_legacy_identities(issue: dict[str, Any]) -> list[list[tuple[str, str]]]:
    """Recover only memberships independently bound by the legacy issue ID."""
    prefix = "Lexical discovery requiring semantic assessment: "
    description = issue.get("description", "")
    if not isinstance(description, str) or not description.startswith(prefix):
        return []
    paths = sorted({ref["path"] for ref in issue.get("targets", [])
                    if isinstance(ref, dict) and isinstance(ref.get("path"), str)})
    texts = description[len(prefix):].split(" | ")
    if not paths or not texts or len(texts) > 1000:
        return []
    candidates = []
    if len(paths) == 1:
        candidates.append([(paths[0], text) for text in texts])
    elif len(set(texts)) == 1:
        candidates.append([(path, texts[0]) for path in paths])
    elif len(paths) == 2 and len(texts) == 2:
        candidates.extend([list(zip(paths, texts)), list(zip(paths, reversed(texts)))])
    result = []
    for candidate in candidates:
        identity = sorted(set(candidate))
        if any("audit-" + fingerprint([kind, identity])[:24] == issue.get("id")
               for kind in ("exact", "similar", "conflict")):
            result.append(identity)
    return result


COHORT_FIELDS = {"cohort_version", "member_hash", "latest_audit", "excluded_member_ids", "coverage_incomplete"}


def _bounded_file_matches(path: str, digest: str) -> bool:
    try:
        raw = read_regular_file_nofollow(Path(path), 16 * 1024 * 1024)
        return hashlib.sha256(raw).hexdigest() == digest
    except (OSError, TypeError, ValueError):
        return False


def _normalized_os_alias_path(path: Path) -> Path | None:
    """Normalize only macOS's OS-owned /var alias; reject other path aliases."""
    if not path.is_absolute() or ".." in path.parts:
        return None
    if path.parts[:2] != (os.sep, "var"):
        return path
    try:
        if not stat.S_ISLNK(os.lstat("/var").st_mode):
            return path
        target = Path(os.readlink("/var"))
        if not target.is_absolute():
            target = Path("/") / target
        if target != Path("/private/var") or Path("/var").resolve(strict=True) != target:
            return None
        return target.joinpath(*path.parts[2:])
    except (OSError, RuntimeError):
        return None


def _secure_scoped_ref(ref: dict[str, Any], timeout: float = 8) -> bool:
    """Hash a scoped evidence path without following aliases beyond macOS /var."""
    try:
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            return False
        digest = ref.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            return False
        lexical = Path(ref["path"])
        canonical = _normalized_os_alias_path(lexical)
        if canonical is None:
            return False
        raw = read_regular_file_nofollow(canonical, 16 * 1024 * 1024, timeout)
        return (hashlib.sha256(raw).hexdigest() == digest
                and _normalized_os_alias_path(lexical) == canonical)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


def current_parser_snapshot_refs(refs: list[dict[str, Any]]) -> bool:
    """Verify parser snapshot refs with safe support for the OS-owned /var alias."""
    if not isinstance(refs, list) or not refs:
        return False
    return all(_secure_scoped_ref(ref) for ref in refs)


def _current_cohort(issue: dict[str, Any], cache: dict, scoped: bool = False) -> bool:
    """Verify one cohort in a bounded worker; share source reads across cohorts."""
    if not COHORT_FIELDS.intersection(issue):
        return True
    if (type(issue.get("cohort_version")) is not int or issue.get("cohort_version") != 1
            or not re.fullmatch(r"[a-f0-9]{64}", str(issue.get("member_hash", "")))
            or not isinstance(issue.get("excluded_member_ids"), list)
            or not isinstance(issue.get("targets"), list)
            or len(issue["targets"]) != 2
            or any(not isinstance(ref, dict) for ref in issue["targets"])):
        return False
    if issue.get("coverage_incomplete"):
        return False
    for target in issue["targets"]:
        key = ("target", target.get("path"), target.get("sha256"))
        if key not in cache:
            cache[key] = (_secure_scoped_ref(target) if scoped
                          else _bounded_file_matches(target.get("path"), target.get("sha256")))
        if not cache[key]:
            return False
    ref = issue.get("latest_audit", {})
    if not isinstance(ref, dict):
        return False
    try:
        key = ("report", ref["path"], ref["sha256"])
        if key not in cache:
            raw = read_regular_file_nofollow(Path(ref["path"]), 16 * 1024 * 1024)
            if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
                return False
            cache[key] = json.loads(raw)
        report = cache[key]
        paths = {target["path"] for target in issue["targets"]}
        if len(paths) != 2:
            return False
        snapshots = report["audit"]["file_snapshots"]
        if any(sum(ref.get("path") == target["path"] and ref.get("sha256") == target["sha256"]
                   for ref in snapshots) != 1 for target in issue["targets"]):
            return False
        excluded = set(issue.get("excluded_member_ids", []))
        groups = [group for group in report["audit"]["exact_duplicates"]
                  if len(group) == 2 and {item["path"] for item in group} == paths
                  and exact_issue_id(group) not in excluded]
        return fingerprint(cohort_members(groups)) == issue["member_hash"]
    except (OSError, KeyError, TypeError, ValueError):
        return False


def _cohorts_current_in_worker(issues: list[dict[str, Any]]) -> bool:
    cache: dict = {}
    if not all(_current_cohort(issue, cache) for issue in issues):
        return False
    # A shared file may have changed while other cohorts were being checked.
    return all(_bounded_file_matches(key[1], key[2]) for key in cache)


def _scoped_cohorts_current_in_worker(issues: list[dict[str, Any]]) -> bool:
    cache: dict = {}
    if not all(_current_cohort(issue, cache, scoped=True) for issue in issues):
        return False
    return all(_secure_scoped_ref({"path": key[1], "sha256": key[2]}) for key in cache)


def current_cohorts(issues: list[dict[str, Any]], timeout: float = 8) -> bool:
    """Check cohort reports and targets in one deadline-bound worker."""
    selected = [issue for issue in issues if COHORT_FIELDS.intersection(issue)]
    if not selected:
        return True
    worker = ("import json,sys; from learning_pipeline import _cohorts_current_in_worker; "
              "print(json.dumps(_cohorts_current_in_worker(json.load(sys.stdin))))")
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps(selected), text=True, capture_output=True,
                                check=True, timeout=timeout)
        return json.loads(result.stdout) is True
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False


def current_scoped_cohorts(issues: list[dict[str, Any]], timeout: float = 8) -> bool:
    """Check cohort evidence for a scoped release assessment with OS-alias support."""
    selected = [issue for issue in issues if COHORT_FIELDS.intersection(issue)]
    if not selected:
        return True
    worker = ("import json,sys; from learning_pipeline import _scoped_cohorts_current_in_worker; "
              "print(json.dumps(_scoped_cohorts_current_in_worker(json.load(sys.stdin))))")
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps(selected), text=True, capture_output=True,
                                check=True, timeout=timeout)
        return json.loads(result.stdout) is True
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False


def current_cohort(issue: dict[str, Any]) -> bool:
    return current_cohorts([issue])


def current_audit_sources(root: Path, sources: list[dict], state: dict,
                          preimages: dict[str, dict], timeout: float = 8) -> bool:
    """Bind audit identities and target snapshots to registered, intact audit reports."""
    anchors = [value.get("snapshot") for run in state.get("runs", {}).values()
               for value in (run.get("weekly_audit"), run.get("post_audit")) if isinstance(value, dict)]
    worker = r'''import hashlib,json,os,stat,sys
from pathlib import Path
from learning_pipeline import COHORT_FIELDS, _bounded_file_matches, _current_cohort, fingerprint
d=json.load(sys.stdin); root=Path(d['root']).resolve(); reports=(root/'reports').resolve(); anchors=d['anchors']; cache={}; total=0; selected={}
if not reports.is_relative_to(root): print('false'); sys.exit(0)
base=os.open(reports,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); base_stat=os.fstat(base)
def read_report(path):
 relative=path.relative_to(reports); parts=relative.parts
 if not parts or any(x in ('','.','..') for x in parts): raise ValueError('invalid report path')
 parent=os.dup(base)
 try:
  for part in parts[:-1]:
   child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent); os.close(parent); parent=child
  fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
  with os.fdopen(fd,'rb') as stream:
   st=os.fstat(stream.fileno())
   if not stat.S_ISREG(st.st_mode): raise ValueError('not a regular report')
   raw=stream.read(16*1024*1024+1)
  return raw,(st.st_dev,st.st_ino)
 finally: os.close(parent)
for source in d['sources']:
 ref=source['report']; path=Path(ref['path']).resolve(strict=True); digest=ref['sha256']; issue=d['issues'][source['id']]
 if path!=Path(ref['path']) or not path.is_relative_to(reports) or ref not in anchors: print('false'); sys.exit(0)
 key=(str(path),digest)
 if key not in cache:
  if len(cache)>=8: print('false'); sys.exit(0)
  raw,identity=read_report(path)
  total+=len(raw)
  if len(raw)>16*1024*1024 or total>32*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest: print('false'); sys.exit(0)
  cache[key]=(json.loads(raw),identity)
 audit=cache[key][0]['audit']; targets=issue['targets']; snapshots=audit['file_snapshots']
 if any(sum(s.get('path')==t['path'] and s.get('sha256')==t['sha256'] for s in snapshots)!=1 for t in targets):
  print('false'); sys.exit(0)
 if COHORT_FIELDS.intersection(issue):
  if issue.get('latest_audit')!=ref: print('false'); sys.exit(0)
  cohort_cache={('report',ref['path'],digest):cache[key][0]}
  for target in targets:
   name=str(Path(target['path']).expanduser().resolve()); pre=d['preimages'].get(name,{})
   if pre.get('sha256')!=target.get('sha256') or not _bounded_file_matches(pre.get('path'),pre.get('sha256')):
    print('false'); sys.exit(0)
   cohort_cache[('target',target['path'],target['sha256'])]=True; selected[name]=pre
  if not _current_cohort(issue,cohort_cache): print('false'); sys.exit(0)
 else:
  groups=[('exact',g) for g in audit.get('exact_duplicates',[])]
  groups += [(kind,[p['a'],p['b']]) for kind,name in [('similar','near_duplicates'),('conflict','potential_conflicts')] for p in audit.get(name,[])]
  def matches(kind,group):
   identity=sorted({(x['path'],x['text']) for x in group}); ident='audit-'+fingerprint([kind,identity])[:24]
   return ident==issue['id'] and {x['path'] for x in group}=={t['path'] for t in targets}
  if not any(matches(kind,group) for kind,group in groups): print('false'); sys.exit(0)
current=os.stat(reports,follow_symlinks=False)
if (current.st_dev,current.st_ino)!=(base_stat.st_dev,base_stat.st_ino): print('false'); sys.exit(0)
for (name,digest),(_,identity) in cache.items():
 path=Path(name)
 if path.resolve(strict=True)!=path: print('false'); sys.exit(0)
 raw,again=read_report(path)
 if identity!=again or len(raw)>16*1024*1024 or hashlib.sha256(raw).hexdigest()!=digest: print('false'); sys.exit(0)
if not all(_bounded_file_matches(r['path'],r['sha256']) for r in selected.values()): print('false'); sys.exit(0)
os.close(base)
print('true')'''
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps({"root": str(root), "sources": sources,
                                                  "issues": state.get("issues", {}), "anchors": anchors,
                                                  "preimages": preimages}),
                                text=True, capture_output=True, check=True, timeout=timeout)
        return json.loads(result.stdout) is True
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False


def current_refs(refs: list[dict[str, str]], timeout: float = 8) -> bool:
    """Recheck a set of bounded file references under one shared deadline."""
    worker = ("import json,sys; from learning_pipeline import _bounded_file_matches; "
              "refs=json.load(sys.stdin); "
              "print(json.dumps(all(_bounded_file_matches(x['path'], x['sha256']) for x in refs) "
              "and all(_bounded_file_matches(x['path'], x['sha256']) for x in refs)))")
    try:
        unique = {(ref["path"], ref["sha256"]) for ref in refs}
        refs = [{"path": path, "sha256": digest} for path, digest in sorted(unique)]
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps(refs), text=True, capture_output=True,
                                check=True, timeout=timeout)
        return json.loads(result.stdout) is True
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        return False


def target_paths_match(paths: Any, refs: Any) -> bool:
    if (not isinstance(paths, list) or not paths or not isinstance(refs, list) or not refs
            or any(not isinstance(path, str) or not path for path in paths)
            or any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref["path"]
                   for ref in refs)):
        return False
    try:
        return ({str(Path(path).expanduser().resolve()) for path in paths}
                == {str(Path(ref["path"]).expanduser().resolve()) for ref in refs})
    except (OSError, TypeError, ValueError):
        return False


def _parser_reconciliation_current(report_ref: dict, affected: list[str], target_paths: dict, version: int,
                                   refs: list[dict[str, str]]) -> bool:
    if not current_refs([report_ref, *refs]):
        return False
    # Called only inside a deadline-bound worker, after checking the digest.
    try:
        with Path(report_ref["path"]).open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != report_ref["sha256"]:
            return False
        report = json.loads(raw)
        audit = report["audit"]
        if (type(audit.get("rule_parser_version")) is not int or audit.get("rule_parser_version") != version
                or audit.get("comparison_scope") != "full" or audit.get("parser_reparse") is not True
                or report["parser_reconciliation"].get("version") != version
                or report["parser_reconciliation"]["affected_issue_ids"] != affected
                or report["parser_reconciliation"].get("affected_target_paths") != target_paths):
            return False
        captured = {(str(Path(x["path"]).expanduser().resolve()), x["sha256"]) for x in audit["file_snapshots"]}
        return (all((str(Path(x["path"]).expanduser().resolve()), x["sha256"]) in captured for x in refs)
                and current_refs([report_ref, *refs], timeout=3))
    except (OSError, KeyError, TypeError, ValueError):
        return False


def current_parser_reconciliation(report_ref: dict, affected: list[str], target_paths: dict, version: int,
                                  refs: list[dict[str, str]], timeout: float = 12) -> bool:
    worker = ("import json,sys; from learning_pipeline import _parser_reconciliation_current; "
              "d=json.load(sys.stdin); "
              "print(json.dumps(_parser_reconciliation_current(d['report'], d['affected'], d['target_paths'], d['version'], d['refs'])))")
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps({"report": report_ref, "affected": affected, "target_paths": target_paths,
                                                  "version": version, "refs": refs}),
                                text=True, capture_output=True, check=True, timeout=timeout)
        return json.loads(result.stdout) is True
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False


def _parser_snapshot_valid(ref: dict, version: int, excluded=()) -> bool:
    try:
        with Path(ref["path"]).open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
            return False
        audit = json.loads(raw)["audit"]
        snapshots = audit.get("file_snapshots")
        if (type(audit.get("rule_parser_version")) is not int
                or audit["rule_parser_version"] != version or audit.get("comparison_scope") != "full"
                or not isinstance(snapshots, list)
                or any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
                       or not Path(item["path"]).is_absolute()
                       or not isinstance(item.get("sha256"), str)
                       or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                       for item in snapshots)):
            return False
        required = [item for item in snapshots if item["path"] not in excluded]
        return (current_parser_snapshot_refs(required)
                and _bounded_file_matches(ref["path"], ref["sha256"]))
    except (OSError, KeyError, TypeError, ValueError):
        return False


def current_parser_snapshot(ref: dict, version: int, timeout: float = 12, *, excluded=()) -> bool:
    worker = ("import json,sys; from learning_pipeline import _parser_snapshot_valid; "
              "d=json.load(sys.stdin); print(json.dumps(_parser_snapshot_valid(d['ref'],d['version'],d['excluded'])))")
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps({"ref": ref, "version": version, "excluded": sorted(excluded)}), text=True,
                                capture_output=True, check=True, timeout=timeout)
        return json.loads(result.stdout) is True
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False


def read_store_state(root: Path, name: str, timeout: float, *, probe_only: bool = False) -> bytes | None:
    worker = r'''import base64,json,os,stat,sys
from pathlib import Path
v=json.load(sys.stdin)
root=Path(v["root"]).resolve()
name=v["name"]
if name not in {"pipeline.json","processed.json"}: raise ValueError("Invalid state name.")
try:
    state_dir=root/"state"
    if state_dir.is_symlink(): raise OSError("State directory is a symbolic link.")
    flags=os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0)
    directory=os.open(state_dir,flags)
    try:
        try:
            fd=os.open(name,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0),dir_fd=directory)
        except FileNotFoundError:
            try:
                os.stat(name,dir_fd=directory,follow_symlinks=False)
            except FileNotFoundError:
                print(json.dumps({"status":"missing"})); sys.exit(0)
            raise OSError("State entry exists but cannot be opened.")
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise OSError("Expected a regular state file.")
            if v.get("probe_only"):
                print(json.dumps({"status":"present"})); sys.exit(0)
            chunks=[]; total=0
            while True:
                chunk=os.read(fd,min(1048576,16777217-total))
                if not chunk: break
                total+=len(chunk)
                if total>16777216: raise OSError("State exceeds size limit.")
                chunks.append(chunk)
            print(json.dumps({"status":"current","content":base64.b64encode(b"".join(chunks)).decode("ascii")}))
        finally:
            os.close(fd)
    finally:
        os.close(directory)
except OSError as exc:
    print(json.dumps({"status":"unavailable","reason":type(exc).__name__}))'''
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps({"root": str(root), "name": name, "probe_only": probe_only}), text=True, capture_output=True,
                                check=True, timeout=timeout)
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get("status") not in {"missing", "present", "current"}:
            raise ValueError("State is unavailable; retry after storage recovery.")
        if value["status"] == "missing":
            return None
        if value["status"] == "present" and probe_only:
            return b""
        if value["status"] != "current" or probe_only:
            raise ValueError("Unexpected state verifier response.")
        return base64.b64decode(value["content"], validate=True)
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("State is unavailable or timed out; retry after storage recovery.") from exc


def check_sealed_file(root: Path, item_id: str, kind: str, ref: dict, timeout: float) -> tuple[bool, dict | None, Any]:
    worker = r'''import base64,hashlib,json,os,stat,sys
from pathlib import Path
from agent_learning import note_summary
v=json.load(sys.stdin)
path=Path(os.path.abspath(v["path"]))
root_input=Path(os.path.abspath(v["root"]))
root=root_input.resolve()
kind=v["kind"]
allowed={"archive-processed":"processed","archive-review":"needs-review","review":"needs-review"}.get(kind,"reports")
class Redirected(Exception): pass
try:
    relative=None
    for candidate in (root_input,root):
        try:
            relative=path.relative_to(candidate); break
        except ValueError: pass
    if relative is None or not relative.parts or relative.parts[0]!=allowed:
        out={"status":"outside"}
    else:
        flags=os.O_RDONLY | getattr(os,"O_DIRECTORY",0) | getattr(os,"O_NOFOLLOW",0)
        directory=os.open(root,flags)
        try:
            try:
                os.stat(allowed,dir_fd=directory,follow_symlinks=False)
            except FileNotFoundError:
                raise OSError("Evidence directory unavailable")
            for part in relative.parts[:-1]:
                item=os.stat(part,dir_fd=directory,follow_symlinks=False)
                if stat.S_ISLNK(item.st_mode): raise Redirected()
                child=os.open(part,flags,dir_fd=directory)
                os.close(directory)
                directory=child
            item=os.stat(relative.parts[-1],dir_fd=directory,follow_symlinks=False)
            if stat.S_ISLNK(item.st_mode): raise Redirected()
            fd=os.open(relative.parts[-1],os.O_RDONLY | getattr(os,"O_NOFOLLOW",0),dir_fd=directory)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode): raise OSError("Expected a regular file.")
                chunks=[]; total=0
                while True:
                    chunk=os.read(fd,min(1048576,16777217-total))
                    if not chunk: break
                    total+=len(chunk)
                    if total>16777216: raise OSError("Evidence exceeds size limit.")
                    chunks.append(chunk)
                raw=b"".join(chunks)
            finally:
                os.close(fd)
        finally:
            os.close(directory)
        digest=hashlib.sha256(raw).hexdigest()
        if v["expected"] is not None and digest!=v["expected"]:
            out={"status":"changed","sha256":digest}
        else:
            out={"status":"current","sha256":digest}
            if kind=="review": out["note"]=note_summary(path,raw)
            elif kind in {"review-receipt","goal-receipt"}: out["content"]=base64.b64encode(raw).decode("ascii")
except Redirected:
    out={"status":"redirected"}
except FileNotFoundError:
    out={"status":"missing"}
except (OSError,ValueError,UnicodeError) as exc:
    out={"status":"unavailable","reason":type(exc).__name__}
print(json.dumps(out))'''
    payload = {"path": ref["path"], "root": str(root), "kind": kind, "expected": ref["sha256"]}
    try:
        proc = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                              input=json.dumps(payload), text=True, capture_output=True, check=True, timeout=timeout)
        result = json.loads(proc.stdout)
        if not isinstance(result, dict) or result.get("status") not in {"current", "outside", "missing", "changed", "redirected", "unavailable"}:
            raise ValueError("Invalid sealed evidence result.")
        if result["status"] == "unavailable":
            raise ValueError("Sealed evidence is unavailable; retry: " + str(result.get("reason", "unknown")))
        if result["status"] != "current":
            reason = {"outside": "The evidence path is outside its expected learning-store directory.",
                      "redirected": "The evidence path contains a symbolic link.",
                      "missing": "The previously verified file is missing.",
                      "changed": "The previously verified file changed."}[result["status"]]
            return False, {"id": item_id, "kind": kind, "path": ref["path"], "expected_sha256": ref["sha256"],
                           "observed_sha256": result.get("sha256"), "reason": reason}, None
        value = result.get("note") if kind == "review" else base64.b64decode(result["content"], validate=True) if kind in {"review-receipt", "goal-receipt"} else result.get("sha256") if kind == "report-snapshot" else None
        return True, None, value
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Sealed evidence is unavailable or timed out; retry.") from exc


def check_external_ref(ref: dict, timeout: float) -> bool:
    """Check an already-authorized target without blocking the caller on file I/O."""
    worker = r'''import hashlib,json,os,stat,sys
from pathlib import Path
v=json.load(sys.stdin)
path=Path(v["path"])
if not path.is_absolute(): raise ValueError("Expected an absolute target.")
fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode): raise ValueError("Expected a regular file.")
    digest=hashlib.sha256(); total=0
    while True:
        chunk=os.read(fd,1048576)
        if not chunk: break
        total+=len(chunk)
        if total>16777216: raise ValueError("Evidence exceeds size limit.")
        digest.update(chunk)
    print(json.dumps({"sha256":digest.hexdigest()}))
finally:
    os.close(fd)'''
    try:
        result = subprocess.run([sys.executable, "-c", worker], cwd=str(Path(__file__).resolve().parent),
                                input=json.dumps(ref), text=True, capture_output=True, check=True, timeout=timeout)
        value = json.loads(result.stdout)
        return isinstance(value, dict) and value.get("sha256") == ref.get("sha256")
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        return False


def current_external_refs(refs: list[dict], timeout: float = 8) -> bool:
    deadline = time.monotonic() + timeout
    return all(check_external_ref(ref, max(0, deadline - time.monotonic())) for ref in refs)


class Pipeline:
    def __init__(self, root: Path, lock, read_finalized, verify_sealed, verify_recovery=None):
        self.root_lexical = root.absolute()
        self.root = root.resolve()
        self.lock = lock
        self.read_finalized = read_finalized
        self.verify_sealed = verify_sealed
        self.verify_recovery = verify_recovery

    def inside(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Pipeline state/report resolves outside the learning store.")
        return path

    def write(self, path: Path, value: Any) -> None:
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Pipeline output is outside the learning store.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def load(self, raw: bytes | None = None, *, timeout: float = 8) -> dict[str, Any]:
        if raw is None:
            try:
                raw = read_store_state(self.root, "pipeline.json", timeout)
            except ValueError as exc:
                raise ValueError("Pipeline state is unavailable or timed out; retry after storage recovery.") from exc
            if raw is None:
                return {"version": 1, "runs": {}, "issues": {}, "weekly": {},
                        "threads": {}, "sent": {}, "outbox": None}
        state = json.loads(raw)
        if (not isinstance(state, dict) or state.get("version") != 1
                or any(not isinstance(state.get(key), dict)
                       for key in ("runs", "issues", "weekly", "threads", "sent"))
                or (state.get("outbox") is not None and not isinstance(state["outbox"], dict))):
            raise ValueError("Malformed pipeline state; preserve it for reconciliation.")
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.write(self.inside("state/pipeline.json"), state)

    @staticmethod
    def check_reconciliation_ledger(state: dict[str, Any], *, repair: bool = False) -> bool:
        changed = False
        marker = state.get("goal_cycle") or {}
        if marker.get("status") == "needs-reconciliation" and not any(
                item.get("run_id") == marker.get("run_id") and item.get("sealed_at") == marker.get("sealed_at")
                and item.get("status") == "needs-reconciliation" for item in state.get("goal_cycle_history", [])):
            raise ValueError("Goal reconciliation history is missing; reconcile before continuing.")
        for cycle in state.get("goal_cycle_history", []):
            if cycle.get("status") != "needs-reconciliation":
                continue
            records = cycle.get("reconciliation_records")
            if not isinstance(records, dict) or set(records) != set(cycle.get("reconciliation_issues", [])):
                raise ValueError("Goal reconciliation ledger is incomplete; reconcile before continuing.")
            for issue_id, expected in records.items():
                if (not isinstance(expected, dict) or expected.get("id") != issue_id
                        or expected.get("kind") != "operational" or expected.get("status") != "investigate"
                        or not isinstance(expected.get("drifts"), list) or not expected["drifts"]):
                    raise ValueError("Goal reconciliation ledger is malformed; reconcile before continuing.")
                actual = state["issues"].get(issue_id)
                if actual is None and repair:
                    state["issues"][issue_id] = expected
                    changed = True
                elif (not isinstance(actual, dict) or actual.get("id") != issue_id or actual.get("kind") != "operational"
                      or actual.get("status") not in STATUSES or actual.get("drifts") != expected.get("drifts")
                      or (actual.get("status") in {"resolved", "justified"}
                          and (not clean(actual.get("resolution", "")) or not actual.get("evidence")
                               or not isinstance(actual.get("resolution_evidence"), list)
                               or not actual["resolution_evidence"]
                               or any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                                      or not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256", "")))
                                      for ref in actual["resolution_evidence"])))):
                    raise ValueError("Goal reconciliation issue is missing or changed; reconcile before continuing.")
        return changed

    @staticmethod
    def cycle(now: datetime) -> str:
        return (now.date() - timedelta(days=now.weekday())).isoformat()

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        with self.lock():
            state = self.load()
            return {**state, "cycle": self.cycle(now),
                    "weekly_due": state["weekly"].get("cycle") != self.cycle(now)}

    def configure(self, ordinary: str, release: str, language: str = "en") -> None:
        chat_url(ordinary)
        chat_url(release)
        if language not in {"en", "it"}:
            raise ValueError("Digest language must be en or it.")
        with self.lock():
            state = self.load()
            state["threads"] = {"ordinary": ordinary, "release": release, "language": language}
            self.save(state)

    def begin(self, run_id: str, runner: str, targets: list[dict[str, str]] | None = None,
              initial_inbox: list[dict[str, str]] | None = None,
              unavailable: list[dict[str, str]] | None = None,
              goal_mode: str | None = None, dispatch_evidence: str | None = None) -> dict[str, Any]:
        if not ID.fullmatch(run_id) or runner not in RUNNERS:
            raise ValueError("Invalid pipeline run ID or runner.")
        if goal_mode not in (None, "scheduled", "manual"):
            raise ValueError("Invalid Goal intent mode.")
        with self.lock():
            state = self.load()
            self.check_reconciliation_ledger(state)
            existing = state["runs"].get(run_id)
            if existing:
                if existing.get("requested_runner", existing["runner"]) != runner or existing["status"] != "running":
                    raise ValueError("Run ID already belongs to another or finished run.")
                if goal_mode is not None and existing.get("goal_mode") != goal_mode:
                    raise ValueError("Run ID already has a different or unknown Goal intent mode.")
                cycle = state.get("goal_cycle") or {}
                if (cycle.get("status") == "active" and (initial_inbox is None or unavailable)):
                    raise ValueError("A Goal continuation requires a complete inbox inventory; retry after storage recovery.")
                if initial_inbox is not None and (existing.get("initial_inbox") != initial_inbox
                                                   or existing.get("initial_unavailable", []) != (unavailable or [])):
                    raise ValueError("Run ID already has a different initial inbox snapshot.")
                return existing
            if any(run["status"] == "running" for run in state["runs"].values()):
                raise ValueError("A consolidation is still running; reconcile it before starting another.")
            if goal_mode == "scheduled" and any(
                    run.get("goal_attempt") and not run.get("goal_baseline")
                    for run in state["runs"].values()):
                raise ValueError("An earlier Goal attempt is unbound; reconcile it before a new scheduled run.")
            now = datetime.now().astimezone()
            requested_runner = runner
            dispatch = None
            if goal_mode == "scheduled":
                runner = "unknown"
                if dispatch_evidence:
                    try:
                        capture_path = Path(os.path.abspath(Path(dispatch_evidence).expanduser()))
                        thread = os.environ.get("CODEX_THREAD_ID", "")
                        if thread != state.get("threads", {}).get("ordinary"):
                            raise ValueError("Capture thread is not the configured ordinary chat.")
                        valid, _, raw = check_sealed_file(self.root_lexical, run_id, "goal-receipt",
                                                          {"path": str(capture_path), "sha256": None}, 3)
                        if not valid:
                            raise ValueError("App capture is unavailable.")
                        dispatch = captured_heartbeat(raw, thread, now)
                        if any(isinstance(value.get("dispatch"), dict)
                               and (value["dispatch"].get("event_id") == dispatch["event_id"]
                                    or value["dispatch"].get("turn_id") == dispatch["turn_id"])
                               for value in state["runs"].values()):
                            raise ValueError("App heartbeat has already been used.")
                        dispatch["receipt"] = {"path": str(capture_path),
                                               "sha256": hashlib.sha256(raw).hexdigest()}
                        valid, _, checked = check_sealed_file(self.root_lexical, run_id, "goal-receipt",
                                                              dispatch["receipt"], 3)
                        if not valid or checked != raw:
                            raise ValueError("App capture changed while starting the run.")
                    except (OSError, TypeError, ValueError, KeyError, RecursionError, OverflowError):
                        dispatch = None
            elif dispatch_evidence:
                raise ValueError("Only a scheduled run can use app heartbeat evidence.")
            run = {"id": run_id, "runner": runner, "status": "running",
                   "started_at": now.isoformat(), "cycle": self.cycle(now),
                   "integration_scope_version": 1,
                   "weekly_due": state["weekly"].get("cycle") != self.cycle(now),
                   "initial_targets": targets or [],
                   "verified_targets": []}
            if goal_mode == "scheduled":
                run["requested_runner"] = requested_runner
                run["dispatch_status"] = "attested" if dispatch else "unknown"
                tool_call_id = os.environ.get("CODEX_TOOL_CALL_ID", "")
                if (tool_call_id.startswith("exec-") and THREAD.fullmatch(tool_call_id[5:])
                        and not any(value.get("start_tool_call_id") == tool_call_id
                                    for value in state["runs"].values())):
                    run["start_tool_call_id"] = tool_call_id
                if dispatch:
                    run["dispatch"] = dispatch
            if goal_mode is not None:
                run["goal_mode"] = goal_mode
            if initial_inbox is not None:
                if len({n["id"] for n in initial_inbox}) != len(initial_inbox):
                    raise ValueError("Initial inbox has duplicate note IDs.")
                run["initial_inbox"] = initial_inbox
                run["initial_unavailable"] = unavailable or []
                run["initial_fingerprint"] = fingerprint({"inbox": initial_inbox, "unavailable": unavailable or []})
            cycle = state.get("goal_cycle")
            if cycle and cycle.get("status") == "sealed":
                raise ValueError("The sealed Goal must be confirmed complete and advanced before another run.")
            if cycle and cycle.get("status") == "active":
                if initial_inbox is None or unavailable:
                    raise ValueError("A Goal continuation requires a complete inbox inventory; retry after storage recovery.")
                baseline = state["runs"].get(cycle.get("run_id"), {})
                if (baseline.get("initial_fingerprint") != cycle.get("initial_fingerprint")
                        or fingerprint({"inbox": baseline.get("initial_inbox"),
                                        "unavailable": baseline.get("initial_unavailable", [])}) != cycle.get("initial_fingerprint")):
                    raise ValueError("Active Goal baseline is unavailable or changed; reconcile before starting another run.")
                run["goal_baseline"] = {"run_id": cycle["run_id"], "initial_fingerprint": cycle["initial_fingerprint"]}
            state["runs"][run_id] = run
            self.save(state)
            return run

    def bind_dispatch(self, run_id: str, evidence_path: str) -> dict[str, Any]:
        """Correlate a started run with its current app heartbeat and start command."""
        with self.lock():
            state = self.load()
            run = state["runs"].get(run_id)
            if not run or run.get("status") != "running" or run.get("goal_mode") != "scheduled":
                raise ValueError("Dispatch correlation requires an active scheduled run.")
            start_id = run.get("start_tool_call_id", "")
            if not isinstance(start_id, str) or not start_id.startswith("exec-") or not THREAD.fullmatch(start_id[5:]):
                raise ValueError("Run start has no supported tool-call identity; keep runner unknown.")
            thread = os.environ.get("CODEX_THREAD_ID", "")
            if thread != state.get("threads", {}).get("ordinary"):
                raise ValueError("Current thread does not match the configured ordinary chat.")
            path = str(Path(os.path.abspath(Path(evidence_path).expanduser())))
            valid, _, raw = check_sealed_file(self.root_lexical, run_id, "goal-receipt", {"path": path, "sha256": None}, 3)
            if not valid:
                raise ValueError("App capture is unavailable.")
            try:
                dispatch = captured_heartbeat(raw, thread, datetime.now().astimezone())
                capture = json.loads(raw)
            except (RecursionError, OverflowError) as exc:
                raise ValueError("App capture is malformed; keep runner unknown.") from exc
            command = capture.get("command")
            if (not isinstance(command, dict) or command.get("type") != "commandExecution"
                    or command.get("id") != start_id or command.get("status") != "completed"
                    or type(command.get("exitCode")) is not int or command["exitCode"] != 0):
                raise ValueError("App capture does not contain this run's completed start command.")
            if any(key != run_id and isinstance(value.get("dispatch"), dict)
                   and (value["dispatch"].get("turn_id") == dispatch["turn_id"]
                        or value["dispatch"].get("event_id") == dispatch["event_id"])
                   for key, value in state["runs"].items()):
                raise ValueError("App heartbeat was already associated with another run.")
            ref = {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
            valid, _, checked = check_sealed_file(self.root_lexical, run_id, "goal-receipt", ref, 3)
            if not valid or checked != raw:
                raise ValueError("App capture changed during correlation.")
            previous = run.get("dispatch")
            if run.get("dispatch_status") == "correlated":
                if (not isinstance(previous, dict) or not isinstance(previous.get("receipt"), dict)
                        or previous["receipt"].get("sha256") != ref["sha256"]
                        or previous.get("event_id") != dispatch["event_id"]):
                    raise ValueError("A different dispatch was already correlated; reconcile before changing it.")
                return {"id": run_id, "runner": run["runner"], "dispatch_status": "correlated", "dispatch": previous}
            if isinstance(previous, dict):
                run.setdefault("dispatch_history", []).append({"status": run.get("dispatch_status"), "dispatch": previous})
            dispatch["receipt"] = ref
            dispatch["start_tool_call_id"] = start_id
            run["dispatch"] = dispatch
            run["dispatch_status"] = "correlated"
            run["runner"] = dispatch["runner"]
            self.save(state)
            return {"id": run_id, "runner": run["runner"], "dispatch_status": run["dispatch_status"],
                    "dispatch": dispatch}

    def mark_goal_attempt(self, run_id: str, goal_thread_id: str) -> dict[str, Any]:
        """Persist a possible native Goal creation before invoking the external tool."""
        if not THREAD.fullmatch(goal_thread_id):
            raise ValueError("Goal attempt requires the exact local thread ID.")
        with self.lock():
            state = self.load()
            run = state["runs"].get(run_id)
            if not run or run.get("status") != "running" or run.get("goal_mode") != "scheduled":
                raise ValueError("Goal attempt needs an active scheduled run.")
            if any(key != run_id and value.get("goal_attempt") and not value.get("goal_baseline")
                   for key, value in state["runs"].items()):
                raise ValueError("Another Goal attempt is unbound; reconcile it before a new attempt.")
            if "initial_inbox" not in run or run.get("initial_unavailable"):
                raise ValueError("Goal attempt needs a complete initial inbox inventory.")
            initial_fingerprint = fingerprint({"inbox": run["initial_inbox"], "unavailable": run.get("initial_unavailable", [])})
            if run.get("initial_fingerprint") != initial_fingerprint:
                raise ValueError("Goal attempt initial inbox fingerprint changed; reconcile before retrying.")
            cycle = state.get("goal_cycle") or {}
            if cycle.get("status") in {"active", "sealed"}:
                raise ValueError("An existing Goal cycle must be reconciled before a new attempt.")
            previous = run.get("goal_attempt")
            if previous is not None:
                if (not isinstance(previous, dict) or previous.get("thread_id") != goal_thread_id
                        or previous.get("initial_fingerprint") != initial_fingerprint):
                    raise ValueError("Goal attempt ownership differs; reconcile before retrying.")
                return previous
            attempt = {"thread_id": goal_thread_id, "initial_fingerprint": initial_fingerprint,
                       "recorded_at": datetime.now().astimezone().isoformat()}
            run["goal_attempt"] = attempt
            self.save(state)
            return attempt

    def goal_evidence(self, receipt_path: str, reference: dict[str, str], goal_thread_id: str, status: str, *, deadline: float | None = None) -> tuple[dict[str, str], dict]:
        path = Path(os.path.abspath(Path(receipt_path).expanduser()))
        def remaining() -> float:
            seconds = 3 if deadline is None else min(3, deadline - time.monotonic())
            if seconds <= 0:
                raise ValueError("Native Goal evidence timed out; retry.")
            return seconds
        valid, _, raw = check_sealed_file(self.root_lexical, "native-goal", "goal-receipt",
                                          {"path": str(path), "sha256": None}, remaining())
        if not valid:
            raise ValueError("Native Goal evidence must be an available saved report in this store.")
        digest = hashlib.sha256(raw).hexdigest()
        valid, _, current_bytes = check_sealed_file(self.root_lexical, "native-goal", "goal-receipt",
                                                    {"path": str(path), "sha256": digest}, remaining())
        if not valid or raw != current_bytes:
            raise ValueError("Native Goal evidence changed or is unavailable; retry.")
        evidence = json.loads(raw)
        goal = evidence.get("goal") if isinstance(evidence, dict) else None
        objective = goal.get("objective") if isinstance(goal, dict) else None
        identity_pattern = (r"(?<![A-Za-z0-9._-])Agent Learning baseline\s+([A-Za-z0-9][A-Za-z0-9._-]{0,127})"
                            r"\s+(?:initial_fingerprint\s+)?([a-f0-9]{64})(?![A-Za-z0-9._-])")
        identities = re.findall(identity_pattern, objective) if isinstance(objective, str) else []
        if (not isinstance(goal, dict) or goal.get("status") != status or goal.get("threadId") != goal_thread_id
                or type(goal.get("createdAt")) is not int or goal["createdAt"] <= 0
                or not isinstance(objective, str) or not re.match(identity_pattern, objective)
                or identities != [(reference.get("run_id"), reference.get("initial_fingerprint"))]):
            raise ValueError("Native Goal evidence does not match the requested cycle and status.")
        ref = {"path": str(path), "sha256": digest}
        return ref, goal

    def bind_goal(self, run_id: str, initial_fingerprint: str, goal_thread_id: str, receipt_path: str) -> dict[str, Any]:
        """Bind the current Goal cycle to an immutable initial inbox snapshot."""
        if not THREAD.fullmatch(goal_thread_id):
            raise ValueError("Goal binding requires the exact local thread ID.")
        with self.lock():
            state = self.load()
            run = state["runs"].get(run_id)
            if not run or "initial_inbox" not in run:
                raise ValueError("Goal baseline run or its initial inbox is unavailable.")
            if run.get("goal_mode") == "manual" and not run.get("goal_baseline"):
                raise ValueError("A manual run cannot be adopted as a scheduled Goal baseline.")
            if any(key != run_id and value.get("goal_attempt") and not value.get("goal_baseline")
                   for key, value in state["runs"].items()):
                raise ValueError("Another Goal attempt is unbound; reconcile it before binding this baseline.")
            if run.get("initial_unavailable"):
                raise ValueError("Goal baseline contains unavailable sources; reconcile before binding.")
            attempt = run.get("goal_attempt")
            if run.get("goal_mode") == "scheduled" and attempt is None:
                raise ValueError("Scheduled Goal binding requires the pre-creation attempt marker.")
            if attempt is not None and (not isinstance(attempt, dict) or attempt.get("thread_id") != goal_thread_id):
                raise ValueError("Goal attempt belongs to a different or unknown thread.")
            actual = fingerprint({"inbox": run["initial_inbox"], "unavailable": run.get("initial_unavailable", [])})
            if actual != initial_fingerprint or run.get("initial_fingerprint") != actual:
                raise ValueError("Goal baseline fingerprint does not match the saved initial inbox.")
            if attempt is not None and attempt.get("initial_fingerprint") != actual:
                raise ValueError("Goal attempt initial inbox fingerprint changed; reconcile before binding.")
            if any(value.get("status") == "running" and key != run_id for key, value in state["runs"].items()):
                raise ValueError("Another run is active; reconcile its ownership before binding the Goal.")
            reference = {"run_id": run_id, "initial_fingerprint": actual}
            receipt, goal = self.goal_evidence(receipt_path, reference, goal_thread_id, "active")
            if attempt is not None:
                try:
                    recorded = datetime.fromisoformat(attempt["recorded_at"])
                    if recorded.utcoffset() is None or int(recorded.timestamp()) >= goal["createdAt"]:
                        raise ValueError("Goal attempt marker timestamp is not earlier than native creation; reconcile ordering and clocks.")
                except (KeyError, TypeError, OverflowError) as exc:
                    raise ValueError("Goal attempt marker has an invalid timestamp.") from exc
            existing = state.get("goal_cycle")
            if existing and existing.get("status") == "active":
                if ({key: existing.get(key) for key in reference} != reference
                        or existing.get("goal_thread_id") != goal_thread_id
                        or existing.get("goal_created_at") != goal["createdAt"]):
                    raise ValueError("Another Goal cycle is active; reconcile it before binding a new one.")
                if run.get("goal_baseline") != reference:
                    raise ValueError("Goal marker and baseline disagree; reconcile before continuing.")
                return existing
            if existing and existing.get("status") == "sealed":
                raise ValueError("The sealed Goal must be confirmed complete and advanced before binding another.")
            cycle = {**reference, "goal_thread_id": goal_thread_id, "goal_created_at": goal["createdAt"],
                     "binding_receipt": receipt, "status": "active"}
            baseline_started = datetime.fromisoformat(run["started_at"])
            for later in state["runs"].values():
                try:
                    if datetime.fromisoformat(later["started_at"]) < baseline_started:
                        continue
                    if later is not run and not later.get("goal_baseline"):
                        continue
                    later_hash = fingerprint({"inbox": later["initial_inbox"],
                                              "unavailable": later.get("initial_unavailable", [])})
                    if later.get("initial_fingerprint") != later_hash:
                        raise ValueError("Unbound initial snapshot")
                    if later.get("initial_unavailable"):
                        raise ValueError("A later run has an incomplete inbox inventory")
                    if later.get("goal_baseline") not in (None, reference):
                        raise ValueError("A later run belongs to another Goal cycle")
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("A later run needs reconciliation before binding this Goal.") from exc
                later["goal_baseline"] = reference
            state["goal_cycle"] = cycle
            self.save(state)
            return cycle

    def advance_goal(self, run_id: str, initial_fingerprint: str, sealed_at: str, receipt_path: str) -> dict[str, Any]:
        """Record native Goal completion before admitting another cycle."""
        with self.lock():
            deadline = time.monotonic() + GOAL_ADVANCE_TIMEOUT
            state = self.load()
            cycle = state.get("goal_cycle", {})
            if (cycle.get("run_id") != run_id or cycle.get("initial_fingerprint") != initial_fingerprint
                    or cycle.get("sealed_at") != sealed_at):
                cycle = next((item for item in reversed(state.get("goal_cycle_history", []))
                              if item.get("run_id") == run_id and item.get("initial_fingerprint") == initial_fingerprint
                              and item.get("sealed_at") == sealed_at), {})
            if (cycle.get("run_id") != run_id or cycle.get("initial_fingerprint") != initial_fingerprint
                    or cycle.get("sealed_at") != sealed_at):
                raise ValueError("Goal cycle changed; refresh before advancing.")
            reference = {"run_id": run_id, "initial_fingerprint": initial_fingerprint}
            ref, goal = self.goal_evidence(receipt_path, reference, cycle.get("goal_thread_id", ""), "complete", deadline=deadline)
            if goal["createdAt"] != cycle.get("goal_created_at"):
                raise ValueError("Native Goal identity changed; reconcile before advancing.")
            if cycle.get("status") in {"closed", "needs-reconciliation"} and cycle.get("completion_receipt") == ref:
                if self.check_reconciliation_ledger(state, repair=True):
                    self.save(state)
                    state = self.load()
                    self.check_reconciliation_ledger(state)
                return cycle
            if cycle.get("status") != "sealed":
                raise ValueError("Goal cycle must be sealed before advancing.")
            drifts = self.verify_sealed(self, state, cycle, deadline)
            issue_ids = []
            grouped: dict[str, list[dict]] = {}
            for drift in sorted(drifts, key=lambda item: (item["id"], item["kind"], item.get("path", ""))):
                grouped.setdefault(drift["id"], []).append(drift)
            for note_id, findings in sorted(grouped.items()):
                issue_id = "goal-reconcile-" + fingerprint([run_id, sealed_at, note_id])[:24]
                issue_ids.append(issue_id)
                state["issues"][issue_id] = {
                    "id": issue_id, "kind": "operational", "status": "investigate",
                    "title": "Sealed learning evidence needs reconciliation",
                    "description": f"Source {note_id}: " + " ".join(item["reason"] for item in findings)
                                   + " Reconcile the saved seal and current evidence before claiming verification.",
                    "sources": [], "targets": [], "options": [], "question": "", "evidence": [],
                    "resolution": "", "drifts": findings, "revalidation_required": False,
                    "updated_at": datetime.now().astimezone().isoformat(),
                }
            cycle.update(status="needs-reconciliation" if drifts else "closed", completion_receipt=ref,
                         reconciliation_issues=issue_ids,
                         reconciliation_records={key: state["issues"][key] for key in issue_ids},
                         advanced_at=datetime.now().astimezone().isoformat())
            state.setdefault("goal_cycle_history", []).append(dict(cycle))
            self.save(state)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Goal cycle was saved but readback timed out; retry the same advance.")
            saved = self.load(timeout=remaining)
            if saved.get("goal_cycle") != cycle or any(issue not in saved["issues"] for issue in issue_ids):
                raise ValueError("Goal cycle readback failed; reconcile the saved state.")
            return cycle

    def reopen_goal(self, run_id: str, initial_fingerprint: str, sealed_at: str, reason: str, receipt_path: str) -> dict[str, Any]:
        """Reopen a sealed pipeline cycle after its still-active Goal needs more work."""
        if not clean(reason):
            raise ValueError("Reopening a Goal cycle requires a reason.")
        with self.lock():
            state = self.load()
            cycle = state.get("goal_cycle", {})
            if (cycle.get("status") != "sealed" or cycle.get("run_id") != run_id
                    or cycle.get("initial_fingerprint") != initial_fingerprint or cycle.get("sealed_at") != sealed_at):
                raise ValueError("The sealed Goal cycle changed; refresh it before reopening.")
            if any(value.get("status") == "running" for value in state["runs"].values()):
                raise ValueError("Another run is active; reconcile ownership before reopening the Goal cycle.")
            receipt, goal = self.goal_evidence(receipt_path, {"run_id": run_id, "initial_fingerprint": initial_fingerprint},
                                               cycle.get("goal_thread_id", ""), "active")
            if goal["createdAt"] != cycle.get("goal_created_at"):
                raise ValueError("Native Goal identity changed; reconcile before reopening.")
            cycle.setdefault("history", []).append({"sealed_at": sealed_at, "reopened_at": datetime.now().astimezone().isoformat(),
                                                     "reason": clean(reason), "goal_receipt": receipt})
            cycle.update(status="active")
            cycle.pop("sealed_at", None)
            self.save(state)
            return cycle

    def active(self, state: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = state["runs"].get(run_id)
        if not run or run["status"] != "running":
            raise ValueError("A running consolidation is required.")
        return run

    def initial_assessment(self, run: dict[str, Any], *, timeout: float | None = None, processed: dict | None = None) -> dict[str, Any]:
        """Describe initial sources without treating unreadable state as completion."""
        initial = run.get("initial_inbox", [])
        try:
            if processed is None:
                raw = read_store_state(self.root, "processed.json", INITIAL_ASSESSMENT_TIMEOUT if timeout is None else timeout)
                if raw is None:
                    raise ValueError("Processed index is unavailable.")
                processed = json.loads(raw).get("processed", {})
            if not isinstance(processed, dict):
                raise ValueError("Invalid processed index.")
            pending = []
            eligible = []
            for source in initial:
                entry = processed.get(source["id"], {})
                target = Path(entry.get("path", "")).expanduser().resolve()
                try:
                    in_run = datetime.fromisoformat(entry.get("processed_at", "")) >= datetime.fromisoformat(run["started_at"]).replace(microsecond=0)
                except (TypeError, ValueError):
                    in_run = False
                status = entry.get("status")
                destination = self.inside("needs-review") if status == "needs-review" else self.inside("processed")
                in_destination = target.is_relative_to(destination) and target.is_file()
                lineage = entry.get("source_lineage", [])
                same_source = any(isinstance(ref, dict) and ref.get("sha256") == source.get("sha256")
                                  and ref.get("path") and Path(ref["path"]).resolve() == Path(source["path"]).resolve()
                                  for ref in lineage)
                candidate = (status in {"processed", "needs-review", "rejected"} and in_run and same_source
                             and not Path(source["path"]).exists() and in_destination)
                if candidate:
                    eligible.append((source, target, status, entry.get("archive_sha256")))
                else:
                    pending.append(source)
            executor = ThreadPoolExecutor(max_workers=8)
            try:
                futures = {executor.submit(self.read_finalized, target): (source, status, archive_hash)
                           for source, target, status, archive_hash in eligible}
                done, unfinished = wait(futures, timeout=INITIAL_ASSESSMENT_TIMEOUT if timeout is None else timeout)
                pending.extend(futures[future][0] for future in unfinished)
                for future in done:
                    source, status, archive_hash = futures[future]
                    try:
                        observed = future.result()
                    except Exception:
                        observed = None
                    if (not observed or observed.get("id") != source["id"] or observed.get("status") != status
                            or observed.get("sha256") != archive_hash):
                        pending.append(source)
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            pending_ids = {item["id"] for item in pending}
            return {"known": True, "unassessed": [item for item in initial if item["id"] in pending_ids],
                    "unavailable": run.get("initial_unavailable", [])}
        except (OSError, ValueError, TypeError, AttributeError, KeyError, RuntimeError):
            return {"known": False, "unassessed": initial, "unavailable": run.get("initial_unavailable", [])}

    def audit(self, run_id: str, payload: dict[str, Any], roots: list[str], weekly: bool,
              explicit_targets: list[str] | None = None) -> dict[str, Any]:
        if not current_parser_snapshot_refs(payload.get("file_snapshots")):
            raise ValueError("Audit targets changed or could not be captured completely.")
        with self.lock():
            state = self.load()
            run = self.active(state, run_id)
            if weekly and state["weekly"].get("cycle") == run["cycle"]:
                return state["weekly"]
            name = f"pipeline-{run_id}-{'weekly' if weekly else 'post'}-{uuid.uuid4().hex[:8]}.json"
            report = self.inside("reports/" + name)
            record = {"run_id": run_id, "cycle": run["cycle"], "roots": roots,
                      "created_at": datetime.now().astimezone().isoformat(), "audit": payload}
            affected: list[str] = []
            affected_paths: dict[str, list[str]] = {}
            if payload.get("parser_reparse"):
                if (payload.get("comparison_scope") != "full"
                        or type(payload.get("rule_parser_version")) is not int
                        or payload["rule_parser_version"] < 2):
                    raise ValueError("Parser migration needs complete versioned audit coverage.")
                fragments: dict[str, list[str]] = {}
                for change in payload.get("legacy_parse_changes", []):
                    if (not isinstance(change, dict) or not isinstance(change.get("path"), str)
                            or not isinstance(change.get("text"), str)):
                        raise ValueError("Parser migration contains invalid legacy evidence.")
                    name = str(Path(change["path"]).expanduser().resolve())
                    fragments.setdefault(name, []).append(clean(change["text"]))
                for issue_id, issue in state["issues"].items():
                    if issue.get("kind") != "audit":
                        continue
                    historical_targets = list(issue.get("targets") or []) + list(issue.get("previous_targets") or [])
                    for history in issue.get("history", []):
                        if isinstance(history, dict):
                            historical_targets.extend(history.get("targets") or [])
                    relevant = {str(Path(ref["path"]).expanduser().resolve()) for ref in historical_targets
                                if isinstance(ref, dict) and ref.get("path")} & fragments.keys()
                    if not relevant:
                        continue
                    identities = verified_legacy_identities(issue)
                    # An edited or ambiguous description is never trusted. A
                    # reconstructed membership is usable only when its hash
                    # reproduces the immutable discovery ID.
                    if not identities or any(
                            clean(text) in fragments.get(str(Path(path).expanduser().resolve()), [])
                            for identity in identities for path, text in identity):
                        affected.append(issue_id)
                        paths = {ref["path"] for ref in historical_targets
                                 if isinstance(ref, dict) and isinstance(ref.get("path"), str)}
                        paths.update(path for identity in identities for path, _ in identity)
                        affected_paths[issue_id] = sorted({str(Path(path).expanduser().resolve()) for path in paths})
                migration_id = f"audit-parser-v{payload['rule_parser_version']}-reconciliation"
                previous_migration = state["issues"].get(migration_id)
                if previous_migration:
                    prior_ids = previous_migration.get("affected_issue_ids")
                    prior_paths = previous_migration.get("affected_target_paths")
                    if (not isinstance(prior_ids, list) or any(not isinstance(key, str) for key in prior_ids)
                            or not isinstance(prior_paths, dict) or set(prior_paths) != set(prior_ids)
                            or any(not isinstance(value, list) or any(not isinstance(path, str) for path in value)
                                   for value in prior_paths.values())):
                        raise ValueError("Prior parser reconciliation coverage is invalid.")
                    affected = sorted(set(affected) | set(prior_ids))
                    for key, paths in prior_paths.items():
                        affected_paths[key] = sorted({str(Path(path).expanduser().resolve())
                                                      for path in paths + affected_paths.get(key, [])})
                affected.sort()
                record["parser_reconciliation"] = {"version": payload["rule_parser_version"],
                                                   "affected_issue_ids": affected,
                                                   "affected_target_paths": affected_paths}
            self.write(report, record)
            report_ref = snapshot(str(report))
            refs = {ref["path"]: {"path": ref["path"], "sha256": ref["sha256"]}
                    for ref in payload["file_snapshots"]}
            for raw in payload.get("removed_files", []):
                removed = str(Path(raw).expanduser().resolve())
                issue_id = "audit-removal-" + fingerprint(removed)[:24]
                old = state["issues"].get(issue_id)
                if old:
                    if old.get("status") in {"resolved", "justified"}:
                        old.setdefault("history", []).append({"status": old["status"],
                            "resolution": old.get("resolution", ""), "evidence": old.get("evidence", [])})
                        old.update(status="investigate", resolution="", evidence=[str(report)])
                    old["last_seen"] = record["created_at"]
                else:
                    state["issues"][issue_id] = {
                        "id": issue_id, "kind": "operational", "status": "investigate",
                        "title": "Instruction audit coverage disappeared",
                        "description": "A previously audited instruction is missing or outside current audit roots; reconcile the removal separately.",
                        "removed_path": removed, "targets": [], "sources": [], "options": [],
                        "question": "", "evidence": [str(report)], "resolution": "",
                        "discovered_run": run_id, "last_seen": record["created_at"],
                    }
            # A lexical audit may find dozens of repeated rules in the same two
            # files. Keep every finding, but offer their shared context for one
            # assessment. Groups with extra occurrences remain independent.
            pairs: dict[tuple[str, str], list[list[dict[str, Any]]]] = {}
            single_exact = []
            for group in payload.get("exact_duplicates", []):
                paths = sorted({item["path"] for item in group})
                if len(group) == 2 and len(paths) == 2:
                    pairs.setdefault((paths[0], paths[1]), []).append(group)
                else:
                    single_exact.append(group)
            groups = [("exact", group) for group in single_exact]
            cohorts = []
            existing_cohorts = {}
            for key, issue in state["issues"].items():
                targets = issue.get("targets")
                if (not COHORT_FIELDS.intersection(issue) or not isinstance(targets, list) or len(targets) != 2
                        or any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str) for ref in targets)):
                    continue
                paths = tuple(sorted(ref["path"] for ref in targets))
                if paths[0] != paths[1] and key == "audit-" + fingerprint(["exact-file-pair-v1", paths])[:24]:
                    existing_cohorts[paths] = issue
            for paths, findings in pairs.items():
                # Keep legacy individual issues and their decisions intact.
                legacy = [group for group in findings if exact_issue_id(group) in state["issues"]]
                groups.extend(("exact", group) for group in legacy)
                novel = [group for group in findings if exact_issue_id(group) not in state["issues"]]
                if paths in existing_cohorts or len(novel) >= 2:
                    cohorts.append((paths, novel, sorted(exact_issue_id(group) for group in legacy)))
                else:
                    groups.extend(("exact", group) for group in novel)
            changed = set(payload.get("changed_files", []))
            # Weekly scans can also be baseline-limited; only the payload's
            # actual comparison scope proves full coverage.
            complete = payload.get("comparison_scope") == "full"
            for paths, issue in existing_cohorts.items():
                if paths in pairs:
                    continue
                if all(path in refs for path in paths) and (complete or changed.intersection(paths)):
                    cohorts.append((paths, [], []))
                elif any(set(paths).issubset({item["path"] for item in group})
                         for group in payload.get("exact_duplicates", [])):
                    # A delta involving a third file is not complete coverage of
                    # the pair, but it invalidates the previous assessment.
                    issue["revalidation_required"] = True
                    issue["coverage_incomplete"] = True
            groups += [(kind, [pair["a"], pair["b"]])
                       for kind, key in (("similar", "near_duplicates"), ("conflict", "potential_conflicts"))
                       for pair in payload.get(key, [])]
            for kind, members in groups:
                identity = sorted({(item["path"], item["text"]) for item in members})
                issue_id = "audit-" + fingerprint([kind, identity])[:24]
                targets = [refs[path] for path in sorted({item["path"] for item in members})]
                old = state["issues"].get(issue_id)
                if old:
                    if old["targets"] != targets:
                        old.setdefault("previous_targets", old["targets"])
                        old["revalidation_required"] = True
                    old.update(targets=targets, last_seen=record["created_at"])
                    continue
                state["issues"][issue_id] = {
                    "id": issue_id, "kind": "audit", "status": "candidate",
                    "title": {"exact": "Repeated instruction", "similar": "Similar instructions",
                              "conflict": "Potential instruction conflict"}[kind],
                    "description": "Lexical discovery requiring semantic assessment: "
                                   + " | ".join(clean(item["text"]) for item in members),
                    "targets": targets, "sources": [], "options": [], "question": "",
                    "evidence": [str(report)], "discovered_run": run_id,
                    "last_seen": record["created_at"], "resolution": "",
                }
            for paths, findings, excluded in cohorts:
                issue_id = "audit-" + fingerprint(["exact-file-pair-v1", paths])[:24]
                targets = [refs[path] for path in paths]
                member_hash = fingerprint(cohort_members(findings))
                description = (f"Lexical discovery: {len(findings)} repeated instructions across the same "
                               "two files. Assess every finding in latest_audit.")
                old = state["issues"].get(issue_id)
                if old:
                    if (type(old.get("cohort_version")) is not int or old.get("cohort_version") != 1
                            or old["targets"] != targets
                            or old.get("member_hash") != member_hash
                            or not old.get("latest_audit")
                            or old.get("excluded_member_ids", []) != excluded):
                        old.setdefault("previous_targets", old["targets"])
                        old["revalidation_required"] = True
                    old.update(targets=targets, description=description, member_hash=member_hash,
                               cohort_version=1, excluded_member_ids=excluded, latest_audit=report_ref,
                               coverage_incomplete=False, last_seen=record["created_at"])
                    if old["status"] == "candidate":
                        old["evidence"] = [str(report)]
                    continue
                state["issues"][issue_id] = {
                    "id": issue_id, "kind": "audit", "status": "candidate",
                    "title": "Repeated instructions across two files", "description": description,
                    "targets": targets, "sources": [], "options": [], "question": "",
                    "evidence": [str(report)], "latest_audit": report_ref,
                    "cohort_version": 1, "member_hash": member_hash, "excluded_member_ids": excluded,
                    "discovered_run": run_id,
                    "last_seen": record["created_at"], "resolution": "",
                }
            for issue in state["issues"].values():
                refs = issue.get("targets", []) + (issue.get("sources", []) if issue["status"] in OPEN else [])
                if (issue["kind"] == "audit" or issue["status"] in OPEN) and any(not current(ref) for ref in refs):
                    issue["revalidation_required"] = True
            if affected:
                version = payload["rule_parser_version"]
                issue_id = f"audit-parser-v{version}-reconciliation"
                old = state["issues"].get(issue_id)
                if old:
                    old.setdefault("history", []).append({"status": old.get("status"),
                        "resolution": old.get("resolution", ""), "evidence": old.get("evidence", []),
                        "affected_issue_ids": old.get("affected_issue_ids", [])})
                state["issues"][issue_id] = {
                    "id": issue_id, "kind": "operational", "status": "investigate",
                    "title": "Reconcile findings affected by the rule parser update",
                    "description": f"The parser update changed the text or visibility of rules related to {len(affected)} historical findings. "
                                   "Review those findings with current complete rule context; retain their existing decisions until reassessed.",
                    "targets": [], "sources": [], "options": [], "question": "", "resolution": "",
                    "evidence": [str(report)], "affected_issue_ids": affected, "affected_target_paths": affected_paths,
                    "parser_version": version,
                    "parser_reconciliation_report": report_ref,
                    "history": old.get("history", []) if old else [], "discovered_run": run_id,
                    "last_seen": record["created_at"], "last_assessed_run": run_id,
                }
            if (type(payload.get("rule_parser_version")) is int
                    and payload["rule_parser_version"] >= 2 and payload.get("comparison_scope") == "full"):
                state["audit_rule_parser_version"] = payload["rule_parser_version"]
                state["audit_rule_parser_snapshot"] = report_ref
            # Absence from a delta is not evidence of resolution.
            reference = {"cycle": run["cycle"], "run_id": run_id,
                         "snapshot": report_ref, "roots": roots}
            observed = set(run.get("audited_changed_targets", []))
            observed.update(str(Path(path).expanduser().resolve()) for path in (explicit_targets or []))
            if weekly:
                state["weekly"] = reference
            else:
                observed.update(str(Path(path).expanduser().resolve()) for path in payload.get("changed_files", []))
            run["audited_changed_targets"] = sorted(observed)
            run["post_audit" if not weekly else "weekly_audit"] = reference
            self.save(state)
            return reference

    def update_issue(self, issue: dict[str, Any], run_id: str | None = None) -> dict[str, Any]:
        if not isinstance(issue, dict) or not ID.fullmatch(str(issue.get("id", ""))):
            raise ValueError("Issue needs a stable ID.")
        if "email_en" in issue:
            raise ValueError("email_en is unsupported; English notifications link to the current issue in chat.")
        post_release_requested = "post_release" in issue
        if "post_release_binding" in issue or "publication_proof" in issue:
            raise ValueError("Post-release evidence binding is generated by the pipeline.")
        requested_parser_review = issue.get("parser_reviewed_version")
        with self.lock():
            loaded = self.load()["issues"]
            previous = loaded.get(issue["id"], {})
            parser_ledger = loaded.get(f"audit-parser-v{requested_parser_review}-reconciliation", {}) if requested_parser_review is not None else {}
        if issue["id"].startswith("audit-parser-v") and any(
                key in issue and issue[key] != previous.get(key)
                for key in ("affected_issue_ids", "affected_target_paths", "parser_version", "parser_reconciliation_report")):
            raise ValueError("Only a parser reparse can change reconciliation coverage.")
        if "parser_reviewed_targets" in issue and issue["parser_reviewed_targets"] != previous.get("parser_reviewed_targets"):
            raise ValueError("Parser review targets are bound by the assessment, not supplied directly.")
        if "member_hash" in previous:
            if any(key in issue and issue[key] != previous.get(key)
                   for key in ("targets", "member_hash", "latest_audit", "excluded_member_ids",
                               "cohort_version", "coverage_incomplete")):
                raise ValueError("Only a new audit can change cohort discovery evidence.")
            if not current_cohort(previous):
                raise ValueError("Cohort audit or membership changed or is unavailable; retry or rerun the audit.")
        issue = {**previous, **issue}
        if (previous.get("post_release") and previous.get("status") not in OPEN
                and issue.get("status") in OPEN and not post_release_requested):
            issue["post_release_binding"] = None
        if issue.get("status") not in STATUSES or issue.get("kind") not in {"audit", "review", "operational"}:
            raise ValueError("Invalid issue kind/status.")
        if post_release_requested and issue.get("post_release") is not None:
            item = issue["post_release"]
            if (issue["kind"] != "operational" or issue["status"] != "investigate"
                    or not previous or previous.get("status") != "investigate"
                    or any(isinstance(entry, dict) and entry.get("status") in {"candidate", "needs-user"}
                           for entry in previous.get("history", []))
                    or issue["id"].startswith(("goal-reconcile-", "audit-"))):
                raise ValueError("A post-release check must be an operational investigation.")
            if (not isinstance(item, dict) or set(item) != {"version", "reason", "evidence", "authorization",
                                                               "candidate_changelog", "candidate_targets"}
                    or not isinstance(item["version"], str) or not RELEASE_VERSION.fullmatch(item["version"])
                    or not isinstance(item["reason"], str) or not clean(item["reason"])
                    or not isinstance(item["candidate_changelog"], dict)
                    or not isinstance(item["candidate_targets"], list) or not item["candidate_targets"]
                    or len(item["candidate_targets"]) > 32
                    or any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                           or not Path(ref["path"]).is_absolute()
                           for ref in item["candidate_targets"])
                    or not isinstance(item["candidate_changelog"].get("path"), str)
                    or not Path(item["candidate_changelog"]["path"]).is_absolute()
                    or not current_external_refs(item["candidate_targets"] + [item["candidate_changelog"]])):
                raise ValueError("Post-release classification needs a version, rationale and current candidate evidence.")
            if (not isinstance(issue.get("targets"), list) or not issue["targets"] or len(issue["targets"]) > 32
                    or any(not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str)
                           or not isinstance(ref.get("path"), str) or not Path(ref["path"]).is_absolute()
                           for ref in issue["targets"])
                    or not current_external_refs(issue["targets"])
                    or not {ref.get("sha256") for ref in issue["targets"]}.issubset(
                    {ref.get("sha256") for ref in item["candidate_targets"]})):
                raise ValueError("The candidate must cover every affected target at its current hash.")
            changelog = Path(item["candidate_changelog"]["path"]).expanduser()
            if (not changelog.is_absolute() or changelog.name != "CHANGELOG.md"
                    or any(not Path(ref["path"]).resolve().is_relative_to(changelog.resolve().parent)
                           for ref in item["candidate_targets"])):
                raise ValueError("The candidate changelog must be a bounded absolute file.")
            candidate_git = candidate_release_identity(changelog.resolve().parent)
            if candidate_git["root"] != str(changelog.resolve().parent):
                raise ValueError("The candidate changelog must belong to the Git worktree root.")
            validate_candidate_mapping(issue, item, candidate_git)
            authorization_path = Path(item["authorization"]).expanduser() if isinstance(item["authorization"], str) else Path()
            if not authorization_path.is_absolute():
                raise ValueError("Post-release classification needs a saved user authorization receipt.")
            valid, _, raw_authorization = check_sealed_file(self.root_lexical, issue["id"], "review-receipt",
                                                             {"path": str(authorization_path), "sha256": None}, 8)
            try:
                authorization = json.loads(raw_authorization) if valid else None
            except (TypeError, ValueError):
                authorization = None
            if (not isinstance(authorization, dict) or type(authorization.get("schema_version")) is not int
                    or authorization["schema_version"] != 1
                    or authorization.get("decision") != "approved"
                    or authorization.get("verification_kind") != "published-release-readback"
                    or authorization.get("issue_id") != issue["id"]
                    or authorization.get("release_version") != item["version"]
                    or authorization.get("candidate_git") != candidate_git
                    or not isinstance(authorization.get("thread_id"), str)
                    or not THREAD.fullmatch(authorization["thread_id"])
                    or not isinstance(authorization.get("recorded_at"), str)
                    or not isinstance(authorization.get("user_request_reference"), str)
                    or not clean(authorization["user_request_reference"])):
                raise ValueError("Post-release authorization does not cover this issue and candidate.")
            try:
                if datetime.fromisoformat(authorization["recorded_at"]).utcoffset() is None:
                    raise ValueError("Missing authorization timezone.")
            except ValueError as exc:
                raise ValueError("Post-release authorization needs a timestamp with timezone.") from exc
            if bounded_candidate_version(changelog, item["candidate_changelog"]["sha256"]) != item["version"]:
                raise ValueError("The post-release version must match the current candidate changelog.")
            evidence_path = Path(item["evidence"]).expanduser() if isinstance(item["evidence"], str) else Path()
            if not evidence_path.is_absolute():
                raise ValueError("Post-release evidence must be a saved report in this store.")
            valid, _, digest = check_sealed_file(self.root_lexical, issue["id"], "report-snapshot",
                                                  {"path": str(evidence_path), "sha256": None}, 8)
            if not valid:
                raise ValueError("Post-release evidence must be a saved report in this store.")
            issue["post_release"] = {**item, "reason": clean(item["reason"]),
                                     "evidence": {"path": str(evidence_path.resolve()), "sha256": digest},
                                     "authorization": {"path": str(authorization_path.resolve()),
                                                       "sha256": hashlib.sha256(raw_authorization).hexdigest()},
                                     "candidate_git": candidate_git}
            issue["post_release_binding"] = post_release_basis(issue)
        elif post_release_requested:
            if previous.get("post_release"):
                raise ValueError("A post-release check cannot be removed while it remains unverified.")
            issue["post_release_binding"] = None
        if requested_parser_review is not None and (
                type(requested_parser_review) is not int or requested_parser_review < 2
                or issue["kind"] != "audit" or issue["status"] not in {"resolved", "justified", "investigate", "needs-user"}):
            raise ValueError("Parser revalidation needs an explicit version and a classified audit assessment.")
        for key in ("title", "description"):
            if not isinstance(issue.get(key), str) or not clean(issue[key]):
                raise ValueError(f"Issue needs {key}.")
        for key in ("targets", "sources"):
            refs = issue.get(key, [])
            if not isinstance(refs, list) or any(not isinstance(ref, dict) or not current(ref) for ref in refs):
                raise ValueError("Issue references must match current file hashes.")
        options = issue.get("options", [])
        if not isinstance(options, list):
            raise ValueError("Options must be a list.")
        if issue["status"] == "needs-user":
            if not clean(issue.get("question", "")) or len(options) < 2:
                raise ValueError("A user decision needs a question and at least two described options.")
            if any(not isinstance(item, dict) or any(not clean(item.get(key, ""))
                   for key in ("id", "label", "description")) for item in options):
                raise ValueError("Each option needs an ID, label and description.")
            if len({item["id"] for item in options}) != len(options):
                raise ValueError("Option IDs must be unique.")
        if issue["status"] in {"resolved", "justified"}:
            if not clean(issue.get("resolution", "")) or not issue.get("evidence"):
                raise ValueError("Closing an issue requires a rationale and verification evidence.")
            if "member_hash" in issue and issue["latest_audit"]["path"] not in issue["evidence"]:
                raise ValueError("Cohort resolution must include the current audit report.")
            if issue["id"].startswith(("goal-reconcile-", "audit-removal-")):
                deadline = time.monotonic() + 8
                references = []
                for raw in issue["evidence"]:
                    path = Path(raw).expanduser()
                    if not path.is_absolute():
                        raise ValueError("Verification evidence must be an absolute report path.")
                    evidence_current, _, digest = check_sealed_file(self.root_lexical, issue["id"], "report-snapshot",
                                                           {"path": str(path), "sha256": None},
                                                           max(0, deadline - time.monotonic()))
                    if not evidence_current:
                        raise ValueError("Verification evidence must be a saved report in this store.")
                    references.append({"path": str(path), "sha256": digest})
                issue["resolution_evidence"] = references
            else:
                for raw in issue["evidence"]:
                    path = Path(raw).expanduser().resolve()
                    if not path.is_relative_to(self.inside("reports")) or not path.is_file():
                        raise ValueError("Verification evidence must be a saved report in this store.")
        if requested_parser_review is not None:
            parser_report = parser_ledger.get("parser_reconciliation_report", {})
            affected = parser_ledger.get("affected_issue_ids", [])
            bound_paths = parser_ledger.get("affected_target_paths", {})
            old_targets = previous.get("targets", [])
            new_targets = issue.get("targets", [])
            if (not isinstance(old_targets, list) or not old_targets or not isinstance(new_targets, list)
                    or any(not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                           for ref in old_targets + new_targets)
                    or {ref["path"] for ref in old_targets} != {ref["path"] for ref in new_targets}):
                raise ValueError("Parser assessment must retain the historical target paths; reconcile removals separately.")
            if (not isinstance(parser_report, dict) or not isinstance(affected, list) or not isinstance(bound_paths, dict)
                    or parser_ledger.get("status") not in OPEN or issue["id"] not in affected
                    or not target_paths_match(bound_paths.get(issue["id"]), new_targets)
                    or parser_report.get("path") not in issue.get("evidence", [])
                    or not current_parser_reconciliation(parser_report, affected, bound_paths, requested_parser_review,
                                                         issue.get("targets", []))):
                raise ValueError("Parser assessment needs the current reconciliation report and covered targets.")
        if (previous.get("post_release") and previous.get("status") in OPEN
                and issue["status"] in {"resolved", "justified"}):
            post = previous["post_release"]
            if previous.get("post_release_binding") != post_release_basis(previous):
                raise ValueError("Reassess the post-release classification before closing it.")
            validate_candidate_mapping(previous, post, post["candidate_git"])
            if (not all(check_external_ref(ref, 8) for ref in post["candidate_targets"] + [post["candidate_changelog"]])
                    or not check_sealed_file(self.root_lexical, issue["id"], "report-snapshot", post["evidence"], 8)[0]
                    or not check_sealed_file(self.root_lexical, issue["id"], "report-snapshot", post["authorization"], 8)[0]
                    or bounded_candidate_version(Path(post["candidate_changelog"]["path"]),
                                                 post["candidate_changelog"]["sha256"]) != post["version"]):
                raise ValueError("Post-release evidence changed or is unavailable; reassess before closure.")
            proof = verified_published_release(post)
            reread = verified_published_release(post)
            if ({key: value for key, value in proof.items() if key != "observed_at"}
                    != {key: value for key, value in reread.items() if key != "observed_at"}):
                raise ValueError("Published release readback changed; reconcile before closure.")
            proof = reread
            report = self.inside("reports/post-release-" + issue["id"] + "-" + uuid.uuid4().hex[:8] + ".json")
            self.write(report, {"issue_id": issue["id"], "candidate_git": post["candidate_git"],
                                "authorization": post["authorization"], "release": proof})
            issue["publication_proof"] = snapshot(str(report))
        with self.lock():
            state = self.load()
            if state["issues"].get(issue["id"], {}) != previous:
                raise ValueError("Issue changed during assessment; refresh it before updating.")
            if (previous.get("post_release") and previous.get("status") in OPEN
                    and issue["status"] in {"resolved", "justified"}):
                post = previous["post_release"]
                deadline = time.monotonic() + 8
                refs = post["candidate_targets"] + [post["candidate_changelog"]] + issue.get("targets", []) + issue.get("sources", [])
                if (candidate_release_identity(Path(post["candidate_git"]["root"])) != post["candidate_git"]
                        or any(not check_external_ref(ref, max(0, deadline - time.monotonic())) for ref in refs)
                        or any(not check_sealed_file(self.root_lexical, issue["id"], "report-snapshot", ref,
                                                     max(0, deadline - time.monotonic()))[0]
                               for ref in [post["evidence"], post["authorization"], issue["publication_proof"]])):
                    raise ValueError("Post-release evidence changed during closure; retry.")
            if post_release_requested and issue.get("post_release"):
                post = issue["post_release"]
                deadline = time.monotonic() + 8
                if (not current_external_refs(post["candidate_targets"] + [post["candidate_changelog"]],
                                              max(0, deadline - time.monotonic()))
                        or not check_sealed_file(self.root_lexical, issue["id"], "report-snapshot",
                                                 post["evidence"], max(0, deadline - time.monotonic()))[0]
                        or not check_sealed_file(self.root_lexical, issue["id"], "report-snapshot",
                                                 post["authorization"], max(0, deadline - time.monotonic()))[0]):
                    raise ValueError("Post-release evidence changed during assessment; retry.")
            if requested_parser_review is not None:
                latest_ledger = state["issues"].get(f"audit-parser-v{requested_parser_review}-reconciliation", {})
                if (latest_ledger != parser_ledger
                        or not current_parser_reconciliation(parser_report, affected, bound_paths, requested_parser_review,
                                                             issue.get("targets", []))):
                    raise ValueError("Parser reconciliation changed during assessment; retry.")
            if not current_cohort(previous):
                raise ValueError("Cohort audit or membership changed or is unavailable during assessment; retry.")
            if issue["id"].startswith("audit-parser-v") and issue["status"] in {"resolved", "justified"}:
                version = previous.get("parser_version")
                affected = previous.get("affected_issue_ids")
                bound_paths = previous.get("affected_target_paths")
                report_ref = previous.get("parser_reconciliation_report")
                if (type(version) is not int or not isinstance(affected, list) or not affected
                        or not isinstance(bound_paths, dict) or set(bound_paths) != set(affected)
                        or not isinstance(report_ref, dict)):
                    raise ValueError("Parser reconciliation ledger is incomplete.")
                refs = [report_ref]
                for key in affected:
                    finding = state["issues"].get(key, {})
                    if (finding.get("status") not in {"resolved", "justified", "investigate", "needs-user"}
                            or type(finding.get("parser_reviewed_version")) is not int
                            or finding.get("parser_reviewed_version") != version
                            or finding.get("parser_reviewed_targets") != finding.get("targets")
                            or not target_paths_match(bound_paths.get(key), finding.get("targets", []))
                            or finding.get("revalidation_required")):
                        raise ValueError("Review every affected legacy finding under the current parser before closure.")
                    refs.extend(finding.get("targets", []))
                if not current_parser_reconciliation(report_ref, affected, bound_paths, version, refs[1:]):
                    raise ValueError("Parser reconciliation evidence changed or is unavailable; retry.")
            merged = {**previous, "sources": [], "targets": [], "options": [], "question": "",
                      "resolution": "", "evidence": [], **issue,
                      "revalidation_required": False,
                      "updated_at": datetime.now().astimezone().isoformat()}
            if requested_parser_review is not None:
                merged["parser_reviewed_targets"] = issue.get("targets", [])
            merged.pop("last_assessed_run", None)
            if run_id:
                self.active(state, run_id)
                merged["last_assessed_run"] = run_id
            elif previous.get("last_assessed_run"):
                merged["last_assessed_run"] = previous["last_assessed_run"]
            if previous:
                prior = {key: previous.get(key) for key in ("status", "resolution", "question", "options", "sources", "targets", "evidence", "updated_at", "parser_reviewed_version", "parser_reviewed_targets")}
                prior.update({key: previous[key] for key in ("post_release", "post_release_binding", "publication_proof")
                              if key in previous})
                merged["history"] = previous.get("history", []) + [
                    prior
                ]
            state["issues"][issue["id"]] = merged
            self.save(state)
            return merged

    def reconcile_run(self, run_id: str, followup_ids: list[str], report_path: str) -> dict[str, Any]:
        """Record recovery separately from a run's immutable terminal outcome."""
        if (not ID.fullmatch(run_id) or not isinstance(followup_ids, list) or not followup_ids
                or len(followup_ids) > 32 or any(not isinstance(item, str) or not ID.fullmatch(item)
                                                  or item == run_id for item in followup_ids)
                or len(set(followup_ids)) != len(followup_ids)):
            raise ValueError("Recovery requires distinct valid run IDs (at most 32 follow-ups).")
        if self.verify_recovery is None:
            raise ValueError("Recovery verification is unavailable.")
        report = str(Path(os.path.abspath(Path(report_path).expanduser())))
        with self.lock():
            state = self.load()
            if not isinstance(state.get("run_recoveries", {}), dict):
                raise ValueError("Recovery ledger is malformed; preserve it for reconciliation.")
            original = state["runs"].get(run_id)
            followups = {key: state["runs"].get(key) for key in followup_ids}
            if not isinstance(original, dict) or any(not isinstance(item, dict) for item in followups.values()):
                raise ValueError("Recovery runs are unavailable.")
            record = {"run_id": run_id, "followup_ids": followup_ids,
                      "run_fingerprint": fingerprint(original),
                      "followup_fingerprints": {key: fingerprint(value) for key, value in followups.items()},
                      "report": {"path": report, "sha256": None}}
            record = self.verify_recovery(self, state, record, time.monotonic() + 3)
            state.setdefault("run_recoveries", {})[run_id] = record
            self.save(state)
            return record

    def finish(self, run_id: str, status: str, summary: str, report: str, targets: list[str],
               inventory: list[dict[str, str]] | None = None, blocker_scope: str | None = None) -> dict[str, Any]:
        if status not in {"completed", "partial", "failed"} or not clean(summary):
            raise ValueError("Run finish needs a status and summary.")
        if (blocker_scope not in {None, "notes", "operational", "mixed", "unknown"}
                or (status == "completed" and blocker_scope is not None)):
            raise ValueError("Blocker scope applies only to partial or failed runs.")
        proof = None
        if report:
            path = Path(report).expanduser().resolve()
            if not path.is_relative_to(self.inside("reports")):
                raise ValueError("Run report must be in this store's reports directory.")
            proof = snapshot(str(path))
        target_refs = [snapshot(raw) for raw in targets]
        with self.lock():
            state = self.load()
            run = self.active(state, run_id)
            if status == "completed":
                self.check_reconciliation_ledger(state)
                if not proof or list(self.inside("state/finalizations").glob("*.json")):
                    raise ValueError("Completion requires a report and no pending finalizations.")
                if run["weekly_due"] and state["weekly"].get("cycle") != run["cycle"]:
                    raise ValueError("The weekly discovery is still due; retry it before completion.")
                if "initial_inbox" not in run:
                    raise ValueError("Completion requires an initial inbox snapshot; reconcile this legacy run.")
                if run.get("initial_unavailable"):
                    raise ValueError("Initial inbox was incomplete; retry unavailable sources in a new run.")
                if any(key.startswith("audit-removal-") and issue.get("status") in OPEN
                       for key, issue in state["issues"].items()):
                    raise ValueError("Reconcile disappeared instruction audit coverage before completion.")
                deadline = time.monotonic() + 8
                for key, issue in state["issues"].items():
                    if key.startswith("audit-removal-") and issue.get("status") in {"resolved", "justified"}:
                        references = issue.get("resolution_evidence", [])
                        if not references or any(not check_sealed_file(
                                self.root_lexical, key, "report-snapshot", ref,
                                max(0, deadline - time.monotonic()))[0] for ref in references):
                            raise ValueError("Instruction removal reconciliation evidence changed or is unavailable.")
                assessment = self.initial_assessment(run)
                if not assessment["known"] or assessment["unassessed"]:
                    raise ValueError("Every initial inbox note needs a matching verified source finalization before completion.")
                raw = read_store_state(self.root, "processed.json", INITIAL_ASSESSMENT_TIMEOUT)
                if raw is None:
                    raise ValueError("Processed state is unavailable; retry before completion.")
                processed = json.loads(raw).get("processed", {})
                if not isinstance(processed, dict):
                    raise ValueError("Processed state is invalid; retry before completion.")
                note_ids = {item["id"] for item in run["initial_inbox"]}
                note_ids.update(key for key, value in processed.items()
                                if isinstance(value, dict) and value.get("consolidation_run") == run_id)
                target_paths = {str(Path(ref["path"]).resolve()) for ref in target_refs}
                def in_scope(issue):
                    return (issue.get("last_assessed_run") == run_id
                            or any(ref.get("note_id") in note_ids for ref in issue.get("sources", []) if isinstance(ref, dict))
                            or any(str(Path(ref["path"]).expanduser().resolve()) in target_paths
                                   for ref in issue.get("targets", []) if isinstance(ref, dict) and ref.get("path")))
                scoped = [issue for issue in state["issues"].values() if in_scope(issue)]
                if not current_cohorts(scoped):
                    raise ValueError("Current-scope cohort audit evidence changed or is unavailable; retry or rerun the audit.")
                if any(issue["status"] == "candidate" for issue in scoped):
                    raise ValueError("Classify every current-scope audit candidate before completing consolidation.")
                if any(issue.get("revalidation_required") or (
                    issue["status"] in OPEN and any(not current(ref) for ref in issue.get("sources", []) + issue.get("targets", []))
                ) for issue in scoped):
                    raise ValueError("Revalidate changed current-scope issue contexts while preserving saved decisions.")
                initial = {ref["path"]: ref for ref in run.get("initial_targets", [])}
                if any(not Path(raw).is_file() for raw in initial):
                    raise ValueError("A managed instruction file disappeared; removal/uninstall needs a separate workflow.")
                changed = {raw for raw, ref in initial.items() if not current(ref)}
                if inventory is not None:
                    changed.update(ref["path"] for ref in inventory if ref["path"] not in initial)
                changed = {str(Path(path).resolve()) for path in changed}
                changed.update(run.get("audited_changed_targets", []))
                audit = run.get("post_audit")
                captured = []
                if audit:
                    if not current(audit["snapshot"]):
                        raise ValueError("Post-consolidation audit is unavailable or changed.")
                    audit_payload = json.loads(Path(audit["snapshot"]["path"]).read_text())["audit"]
                    captured = audit_payload["file_snapshots"]
                    changed.update(str(Path(path).resolve()) for path in audit_payload.get("changed_files", []))
                if not changed.issubset(target_paths):
                    raise ValueError("Changed instruction targets were omitted from completion.")
                if targets:
                    if not audit:
                        raise ValueError("Changed targets require a saved post-consolidation audit.")
                    if any(not any(str(Path(x["path"]).resolve()) == str(Path(ref["path"]).resolve()) and x["sha256"] == ref["sha256"]
                                   for x in captured) for ref in target_refs):
                        raise ValueError("Post-audit does not cover the current changed targets.")
            if status in {"partial", "failed"} and "initial_inbox" in run:
                run["initial_assessment"] = self.initial_assessment(run)
            if status in {"partial", "failed"}:
                run["blocker_scope"] = blocker_scope or "unknown"
                run["operational_blocker_ids"] = sorted(
                    key for key, issue in state["issues"].items()
                    if issue.get("kind") == "operational" and issue.get("last_assessed_run") == run_id
                    and (issue.get("status") in OPEN or issue.get("revalidation_required")))
                if blocker_scope == "notes":
                    assessment = run.get("initial_assessment", {})
                    if (not assessment.get("known") or not assessment.get("unassessed")
                            or assessment.get("unavailable") or run["operational_blocker_ids"]):
                        raise ValueError("A note-only blocker needs known unassessed sources and no unavailable inventory.")
            if status == "completed":
                outstanding = {key: issue for key, issue in state["issues"].items()
                               if issue.get("status") in OPEN or issue.get("revalidation_required")}
                run["outstanding_issue_ids"] = sorted(outstanding)
                run["deferred_issue_ids"] = sorted(key for key, issue in outstanding.items() if not in_scope(issue))
            run.update(status=status, summary=clean(summary), report=proof,
                       verified_targets=target_refs, finished_at=datetime.now().astimezone().isoformat())
            self.save(state)
            return run

    def weekly_release_status(self, release_version: str | None = None,
                              candidate_root: Path | None = None,
                              assessment_path: str | None = None,
                              base_ref: str = "origin/main") -> dict[str, Any]:
        state = self.status()
        self.check_reconciliation_ledger(state)
        if any(key.startswith("goal-reconcile-") and issue.get("status") in OPEN
               for key, issue in state["issues"].items()):
            raise ValueError("Resolve Goal reconciliation issues before preparing a release.")
        followups = []
        release_cohorts = []
        deferred_references = []
        assessment_digest = None
        assessment_file = None
        candidate = None
        post_release_references = []
        audit_deferral_refs = []
        verified_audit_deferral_ids = set()
        verified_candidate_issue_ids = set()
        unrelated_issue_ids = set()
        unrelated_reference_paths = {}
        candidate_integration_refs = []
        if assessment_path:
            if candidate_root is None:
                raise ValueError("A candidate checkout is required for a scoped release assessment.")
            raw_assessment_file = Path(assessment_path).expanduser()
            if raw_assessment_file.is_symlink():
                raise ValueError("Release assessment must not be a symbolic link.")
            assessment_file = raw_assessment_file.resolve(strict=True)
            assessment_raw = read_regular_file_nofollow(assessment_file, 1024 * 1024)
            assessment_digest = hashlib.sha256(assessment_raw).hexdigest()
            try:
                assessment = json.loads(assessment_raw)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("Release assessment is invalid JSON.") from exc
            if (not isinstance(assessment, dict) or set(assessment) != {"version", "candidate", "assessments"}
                    or type(assessment.get("version")) is not int or assessment["version"] != 1
                    or not isinstance(assessment.get("candidate"), dict)
                    or not isinstance(assessment.get("assessments"), list)):
                raise ValueError("Release assessment has an unsupported schema.")
            candidate = candidate_change_snapshot(candidate_root, base_ref)
            if assessment["candidate"] != candidate:
                raise ValueError("Release assessment candidate, target, or complete diff changed; regenerate it.")
            expected = {key: issue for key, issue in state["issues"].items()
                        if issue.get("status") in OPEN or issue.get("revalidation_required")
                        or COHORT_FIELDS.intersection(issue)}
            records = {}
            required = {"issue_id", "issue_sha256", "evidence", "classification", "rationale", "reviewer",
                        "candidate_relation", "dependency_review", "audit_deferral"}
            for record in assessment["assessments"]:
                if (not isinstance(record, dict)
                        or frozenset(record) not in {frozenset(required), frozenset(required | {"review_deferral"}),
                                                     frozenset(required | {"candidate_integration"}),
                                                     frozenset(required | {"review_deferral", "candidate_integration"}),
                                                     frozenset(required | {"scope_review"}),
                                                     frozenset(required | {"scope_review", "review_deferral",
                                                                            "candidate_integration"})}):
                    raise ValueError("Every assessed issue needs its full semantic review record.")
                key = record["issue_id"]
                if not isinstance(key, str) or key in records or key not in expected:
                    raise ValueError("Release assessment has an unknown or duplicate issue.")
                issue = expected[key]
                if issue.get("id") != key:
                    raise ValueError("Release assessment issue key and identity differ; reconcile the state key.")
                if record["issue_sha256"] != fingerprint(issue):
                    raise ValueError("Release assessment issue changed; reassess it.")
                if any(not isinstance(record[field], str) or len(clean(record[field])) < minimum
                       for field, minimum in (("rationale", 40), ("reviewer", 3),
                                              ("candidate_relation", 20), ("dependency_review", 20))):
                    raise ValueError("Release assessment needs reviewer, candidate relation, dependency review and rationale.")
                records[key] = record
            if set(records) != set(expected):
                raise ValueError("Release assessment must cover every open issue and saved cohort record.")
            reports_root = self.inside("reports")
            evidence_cache = {}
            evidence_deadline = time.monotonic() + 30
            evidence_by_issue = {}
            allowed_classifications = {"block", "defer", "post_release", "candidate_verified", "unrelated"}
            for key, issue in expected.items():
                classification = records[key]["classification"]
                if not isinstance(classification, str) or classification not in allowed_classifications:
                    raise ValueError("Release assessment has an unknown issue classification.")
                if classification == "unrelated":
                    unrelated_reference_paths[key] = verify_unrelated_issue(
                        issue, records[key], candidate, candidate_root)
                    evidence_by_issue[key] = []
                    unrelated_issue_ids.add(key)
                else:
                    if records[key].get("scope_review") is not None:
                        raise ValueError("Unrelated review is only valid for an unrelated classification.")
                    evidence_by_issue[key] = verify_release_issue_evidence(
                        issue, records[key]["evidence"], reports_root, evidence_cache, evidence_deadline)
            assessment_evidence_refs = [ref for refs in evidence_by_issue.values() for ref in refs]
            if len(assessment_evidence_refs) > 512:
                raise ValueError("Release assessment exceeds the bounded issue-evidence reference count.")
            blocking = []
            audit_artifact_cache = {}
            for key, issue in expected.items():
                record = records[key]
                classification = record["classification"]
                audit_open = issue.get("kind") == "audit" and issue.get("status") in OPEN
                reserved_reconciliation = key.startswith(("goal-reconcile-", "audit-removal-",
                                                            "audit-parser-", "audit-coherence-"))
                protected = (issue.get("status") == "needs-user" or issue.get("revalidation_required")
                             or reserved_reconciliation
                             or (key.startswith("audit-") and issue.get("kind") != "audit"))
                if protected and classification != "block":
                    raise ValueError("User decisions and evidence-integrity findings must remain blocking.")
                if classification == "defer" and (issue.get("post_release") is not None
                                                   or issue.get("post_release_binding") is not None):
                    raise ValueError("A bound post-release obligation must remain blocking or be reported as a follow-up.")
                if classification == "defer" and issue.get("status") in OPEN and issue.get("kind") != "audit":
                    if issue.get("kind") != "review":
                        raise ValueError("Only open review findings may be deferred; resolve other issues or use an authorized post-release follow-up.")
                if audit_open and classification == "defer":
                    verified_refs = verify_audit_deferral(
                        issue, record, candidate, evidence_by_issue[key], reports_root, audit_artifact_cache,
                        evidence_deadline)
                    audit_deferral_refs.extend(verified_refs)
                    assessment_evidence_refs.extend(verified_refs)
                    verified_audit_deferral_ids.add(key)
                elif not audit_open and record["audit_deferral"] is not None:
                    raise ValueError("Audit deferral authorization is only valid for an open audit finding.")
                review_open = issue.get("kind") == "review" and issue.get("status") in OPEN
                if review_open and classification == "defer":
                    review_refs = verify_issue_deferral(
                        issue, record, candidate, evidence_by_issue[key], reports_root, audit_artifact_cache,
                        evidence_deadline)
                    assessment_evidence_refs.extend(review_refs)
                elif record.get("review_deferral") not in (None, {
                        "independent_review": {"path": "", "sha256": ""},
                        "authorization": {"path": "", "sha256": ""}}):
                    raise ValueError("Review deferral authorization is only valid for an open review finding classified defer.")
                if classification == "candidate_verified":
                    candidate_refs = verify_candidate_integration(
                        issue, record, candidate, reports_root, audit_artifact_cache, evidence_deadline,
                        self.root, evidence_by_issue[key])
                    candidate_integration_refs.extend(candidate_refs)
                    assessment_evidence_refs.extend(candidate_refs)
                    verified_candidate_issue_ids.add(key)
                elif record.get("candidate_integration") is not None:
                    raise ValueError("Candidate integration proof is only valid for candidate_verified classification.")
                if classification == "post_release":
                    post = issue.get("post_release")
                    eligible = (issue.get("kind") == "operational" and issue.get("status") == "investigate"
                                and not key.startswith(("goal-reconcile-", "audit-"))
                                and not any(isinstance(entry, dict) and entry.get("status") in {"candidate", "needs-user"}
                                            for entry in issue.get("history", []))
                                and isinstance(post, dict) and issue.get("post_release_binding") == post_release_basis(issue))
                    if not eligible:
                        raise ValueError("Only an already authorized and bound post-release check may use that classification.")
                    followups.append(issue)
                elif classification == "block":
                    blocking.append(key)
                elif classification == "defer":
                    refs = issue.get("sources", []) + issue.get("targets", [])
                    if not refs or any(not isinstance(ref, dict) or not _secure_scoped_ref(ref) for ref in refs):
                        raise ValueError("A deferred issue needs current source and target evidence.")
                    deferred_references.extend(refs)
                elif classification == "unrelated":
                    pass
                if COHORT_FIELDS.intersection(issue):
                    release_cohorts.append(issue)
            if len(assessment_evidence_refs) > 512:
                raise ValueError("Release assessment exceeds the bounded evidence reference count.")
            if blocking:
                raise ValueError("Release assessment still has blocking issues: " + ", ".join(sorted(blocking)))
        else:
            blocking = []
            for issue in state["issues"].values():
                if issue.get("revalidation_required"):
                    blocking.append(issue)
                    continue
                if issue.get("status") not in OPEN:
                    continue
                post = issue.get("post_release")
                if (issue.get("kind") == "operational" and issue.get("status") == "investigate"
                        and not str(issue.get("id", "")).startswith(("goal-reconcile-", "audit-"))
                        and not any(isinstance(entry, dict) and entry.get("status") in {"candidate", "needs-user"}
                                    for entry in issue.get("history", []))
                        and isinstance(post, dict) and issue.get("post_release_binding") == post_release_basis(issue)):
                    followups.append(issue)
                else:
                    blocking.append(issue)
            if blocking:
                raise ValueError("Classify or revalidate outstanding issues before preparing a release.")
            release_cohorts = list(state["issues"].values())
        if followups and (not isinstance(release_version, str) or not RELEASE_VERSION.fullmatch(release_version)
                          or any(issue["post_release"].get("version") != release_version for issue in followups)):
            raise ValueError("Supply the matching release version for open post-release checks.")
        if followups and (candidate_root is None or any(
                not isinstance(issue["post_release"].get("candidate_changelog"), dict)
                or not isinstance(issue["post_release"]["candidate_changelog"].get("path"), str)
                or Path(issue["post_release"]["candidate_changelog"]["path"]).resolve().parent
                != candidate_root.resolve() for issue in followups)):
            raise ValueError("Run the release check from the bound candidate checkout.")
        if followups:
            candidate_git = candidate_release_identity(candidate_root)
            if any(issue["post_release"].get("candidate_git") != candidate_git for issue in followups):
                raise ValueError("The candidate checkout identity changed; reassess post-release evidence.")
            for issue in followups:
                validate_candidate_mapping(issue, issue["post_release"], candidate_git)
                ref = issue["post_release"]["candidate_changelog"]
                if bounded_candidate_version(Path(ref["path"]), ref["sha256"]) != release_version:
                    raise ValueError("The candidate changelog release version changed; reassess post-release evidence.")
        cohorts_current = current_scoped_cohorts if assessment_path else current_cohorts
        if not cohorts_current(release_cohorts):
            raise ValueError("Cohort audit evidence changed or is unavailable; retry or reassess before preparing a release.")
        runs = [run for run in state["runs"].values() if run["cycle"] == state["cycle"]]
        latest = max(runs, key=lambda x: x["started_at"]) if runs else None
        if (state["weekly_due"] or any(run["status"] == "running" for run in state["runs"].values())
                or not latest or latest["status"] != "completed"):
            raise ValueError("A verified completed cycle is required before preparing the release.")
        if (type(state.get("audit_rule_parser_version")) is not int
                or state.get("audit_rule_parser_version") != RULE_PARSER_VERSION):
            raise ValueError("Reparse audit coverage with the current rule parser before preparing a release.")
        excluded_parser_paths = set()
        if assessment_path:
            ledger_members = {member for key, issue in state["issues"].items()
                              if key.startswith("audit-parser-")
                              for member in issue.get("affected_issue_ids", [])}
            scoped_audits = {key for key in unrelated_issue_ids
                             if expected[key].get("kind") == "audit" and key not in ledger_members}
            protected_paths = {Path(ref["path"]).resolve()
                               for key, issue in expected.items() if key not in scoped_audits
                               for ref in issue.get("sources", []) + issue.get("targets", [])
                               if isinstance(ref, dict) and isinstance(ref.get("path"), str)}
            excluded_parser_paths = {
                ref["path"] for key in scoped_audits for ref in unrelated_reference_paths[key]
                if ref["path"] == ref["resolved_path"] and Path(ref["path"]) not in protected_paths
            }
        if not current_parser_snapshot(state.get("audit_rule_parser_snapshot", {}), RULE_PARSER_VERSION,
                                       excluded=excluded_parser_paths):
            raise ValueError("Current rule parser audit evidence changed or is unavailable; rerun a full audit and retry.")
        for key, issue in state["issues"].items():
            if not key.startswith("audit-parser-v") or issue.get("status") not in {"resolved", "justified"}:
                continue
            version = issue.get("parser_version")
            affected = issue.get("affected_issue_ids")
            bound_paths = issue.get("affected_target_paths")
            report_ref = issue.get("parser_reconciliation_report")
            if (type(version) is not int or not isinstance(affected, list) or not affected
                    or not isinstance(bound_paths, dict) or set(bound_paths) != set(affected)
                    or not isinstance(report_ref, dict)):
                raise ValueError("Parser reconciliation ledger is incomplete; reassess before release.")
            refs = []
            for item_id in affected:
                finding = state["issues"].get(item_id, {})
                current_scoped_deferral = (assessment_path is not None
                                           and finding.get("status") == "investigate"
                                           and item_id in verified_audit_deferral_ids
                                           and not finding.get("revalidation_required"))
                if ((finding.get("status") not in {"resolved", "justified"} and not current_scoped_deferral)
                        or type(finding.get("parser_reviewed_version")) is not int
                        or finding.get("parser_reviewed_version") != version
                        or finding.get("parser_reviewed_targets") != finding.get("targets")
                        or not target_paths_match(bound_paths.get(item_id), finding.get("targets", []))):
                    raise ValueError("Parser reconciliation assessment changed; reassess before release.")
                refs.extend(finding.get("targets", []))
            if not current_parser_reconciliation(report_ref, affected, bound_paths, version, refs):
                raise ValueError("Parser reconciliation evidence changed or is unavailable; retry before release.")
        deadline = time.monotonic() + 8
        references = {}
        def add_reference(kind, ref):
            key = json.dumps(ref, sort_keys=True)
            item = references.setdefault(key, {"ref": ref, "kinds": set()})
            item["kinds"].add(kind)
        for key, issue in state["issues"].items():
            if key.startswith(("goal-reconcile-", "audit-removal-")) and issue.get("status") in {"resolved", "justified"}:
                for ref in issue.get("resolution_evidence", []):
                    add_reference("resolution" if key.startswith("goal-reconcile-") else "removal-resolution", ref)
            if issue.get("post_release") and issue.get("status") in {"resolved", "justified"}:
                if not isinstance(issue.get("publication_proof"), dict):
                    raise ValueError("Published release evidence is missing; reassess before release.")
                add_reference("post-release-report", issue["publication_proof"])
        for ref in [state["weekly"].get("snapshot", {}), latest.get("report") or {}]:
            if not ref.get("path") or not ref.get("sha256"):
                raise ValueError("A verified completed cycle is required before preparing the release.")
            add_reference("report", ref)
        for ref in latest.get("verified_targets", []):
            add_reference("target", ref)
        for issue in followups:
            post = issue["post_release"]
            if (not isinstance(post.get("candidate_changelog"), dict)
                    or not isinstance(post.get("candidate_targets"), list) or not post["candidate_targets"]
                    or not isinstance(post.get("evidence"), dict) or not isinstance(post.get("authorization"), dict)):
                raise ValueError("Post-release evidence is incomplete; reassess before release.")
            for ref in post["candidate_targets"] + [post["candidate_changelog"]] + issue.get("targets", []) + issue.get("sources", []):
                add_reference("post-release-target", ref)
                post_release_references.append(ref)
            add_reference("post-release-report", post["evidence"])
            add_reference("post-release-report", post["authorization"])
        def check(item):
            seconds = max(0, deadline - time.monotonic())
            if not item["kinds"].intersection({"resolution", "removal-resolution", "report", "post-release-report"}):
                return check_external_ref(item["ref"], seconds)
            return check_sealed_file(self.root, "release", "resolution-evidence", item["ref"], seconds)[0]
        executor = ThreadPoolExecutor(max_workers=8)
        try:
            futures = {executor.submit(check, item): item for item in references.values()}
            done, unfinished = wait(futures, timeout=max(0, deadline - time.monotonic()))
            if unfinished:
                raise ValueError("Release evidence timed out; retry the check.")
            failed = [futures[future] for future in done if not future.result()]
            if any("resolution" in item["kinds"] for item in failed):
                raise ValueError("Goal reconciliation evidence changed; reassess before release.")
            if any("removal-resolution" in item["kinds"] for item in failed):
                raise ValueError("Instruction removal evidence changed; reassess before release.")
            if any(item["kinds"].intersection({"post-release-target", "post-release-report"}) for item in failed):
                raise ValueError("Post-release evidence changed or is unavailable; reassess before release.")
            if failed:
                raise ValueError("A verified completed cycle is required before preparing the release.")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if assessment_path:
            final_candidate_refs = []
            for key in sorted(verified_candidate_issue_ids):
                final_candidate_refs.extend(verify_candidate_integration(
                    expected[key], records[key], candidate, reports_root, {}, time.monotonic() + 30,
                    self.root, evidence_by_issue[key]))
            current_candidate = candidate_change_snapshot(candidate_root, base_ref)
            current_raw = read_regular_file_nofollow(assessment_file, 1024 * 1024)
            if (current_candidate != candidate or hashlib.sha256(current_raw).hexdigest() != assessment_digest
                    or any(verify_unrelated_issue(expected[key], records[key], candidate, candidate_root,
                                                  unrelated_reference_paths[key])
                           != unrelated_reference_paths[key]
                           for key in unrelated_issue_ids)
                    or any(not _secure_scoped_ref(ref) for ref in deferred_references)
                    or any(not current_release_issue_evidence(ref, reports_root)
                           for ref in assessment_evidence_refs + final_candidate_refs)
                    or not current_scoped_cohorts(release_cohorts)):
                raise ValueError("Candidate or release assessment changed during verification; retry.")
        if followups and candidate_release_identity(candidate_root) != candidate_git:
            raise ValueError("The candidate checkout identity changed during release verification; retry.")
        if followups and any(not current_release_ref(ref) for ref in post_release_references):
            raise ValueError("Post-release target or changelog evidence changed during release verification; retry.")
        result = {"weekly": state["weekly"], "consolidation": latest,
                  "release_assessment": ({"sha256": assessment_digest,
                                          "candidate_change_sha256": candidate["change_sha256"],
                                        "deferred_issue_ids": sorted(record["issue_id"] for record in records.values()
                                                                      if record["classification"] == "defer"
                                                                      and expected[record["issue_id"]].get("status") in OPEN),
                                          "deferred_audit_issue_ids": sorted(record["issue_id"] for record in records.values()
                                                                             if record["classification"] == "defer"
                                                                             and expected[record["issue_id"]].get("kind") == "audit"
                                                                             and expected[record["issue_id"]].get("status") in OPEN),
                                          "deferred_review_issue_ids": sorted(record["issue_id"] for record in records.values()
                                                                              if record["classification"] == "defer"
                                                                              and expected[record["issue_id"]].get("kind") == "review"
                                                                              and expected[record["issue_id"]].get("status") in OPEN),
                                          "unrelated_issue_ids": sorted(unrelated_issue_ids),
                                          "candidate_verified_issue_ids": sorted(verified_candidate_issue_ids),
                                        "reviewed_closed_cohort_ids": sorted(
                                            key for key, issue in expected.items()
                                            if COHORT_FIELDS.intersection(issue)
                                            and issue.get("status") in {"resolved", "justified"})}
                                         if assessment_path else None),
                  "post_release_checks": [{"id": issue["id"], "title": issue.get("title"),
                                            "version": issue["post_release"]["version"],
                                            "reason": issue["post_release"]["reason"],
                                            "candidate_git": issue["post_release"]["candidate_git"],
                                            "candidate_changelog": issue["post_release"]["candidate_changelog"],
                                            "candidate_targets": issue["post_release"]["candidate_targets"]}
                                           for issue in followups]}
        with self.lock():
            fresh = self.load()
            initial = {key: value for key, value in state.items() if key not in {"cycle", "weekly_due"}}
            if fingerprint(fresh) != fingerprint(initial) or self.cycle(datetime.now().astimezone()) != state["cycle"]:
                raise ValueError("Pipeline state changed during release verification; retry.")
            return result

    def digest(self, state: dict[str, Any], notes: list[dict[str, str]], now: datetime | None = None,
               inbox: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
        now = now or datetime.now().astimezone()
        # Email notifications are always English. Keep the configured language for
        # other pipeline behavior, but do not let a historical `it` setting produce
        # localized subjects or chat calls to action in the email digest.
        language = "en"
        ordinary = state["threads"].get("ordinary", "")
        url = chat_url(ordinary) if ordinary else ""
        live = {note["id"]: note for note in notes}
        items = []
        aged, unknown = [], []
        for note in inbox or []:
            try:
                created = datetime.fromisoformat(note.get("created_at", ""))
                if created.tzinfo is None:
                    raise ValueError("Missing timezone")
                if created > now:
                    raise ValueError("Future creation time")
                if now - created >= timedelta(days=1):
                    aged.append((created, note["id"]))
            except (ValueError, TypeError, KeyError):
                unknown.append(note.get("id", "unknown"))
        if aged or unknown:
            oldest = min(aged)[0].isoformat() if aged else "unknown"
            items.append({"id": "inbox-backlog", "status": "investigate", "title": "Inbox backlog requires assessment",
                          "description": f"{len(aged)} inbox note(s) are at least 24 hours old; oldest: {oldest}. "
                                         f"{len(unknown)} note(s) have an unknown creation time.",
                          "question": "", "options": [], "chat_url": url,
                          "note_ids": sorted([note_id for _, note_id in aged] + unknown)})
        covered = set()
        for issue in state["issues"].values():
            if issue["status"] not in OPEN and not issue.get("revalidation_required"):
                continue
            sources = issue.get("sources", [])
            if any(ref.get("note_id") and ref["note_id"] not in live for ref in sources):
                continue  # A source was archived or the user made an explicit checkbox decision.
            covered.update(ref.get("note_id") for ref in sources)
            stale = issue.get("revalidation_required") or any(not current(ref) for ref in issue.get("targets", []) + sources)
            if stale:
                if language == "it":
                    item = {"id": issue["id"], "status": "investigate", "title": "Revalidation required",
                            "description": clean(issue["title"]) + ": source or target changed; the agent must reassess it.",
                            "options": [], "question": ""}
                else:
                    item = {"id": issue["id"], "status": "investigate", "title": "Reassessment required",
                            "description": (f"Issue {issue['id']}: a source or target changed. Open the linked chat for current details."
                                            if url else f"Issue {issue['id']}: a source or target changed. Run `python3 scripts/agent_learning.py pipeline-status` in the configured checkout for details."),
                            "options": [], "question": ""}
            elif issue["id"].startswith("goal-reconcile-"):
                if language == "it":
                    item = {"id": issue["id"], "status": "investigate", "title": clean(issue["title"]),
                            "description": clean(issue["description"]), "options": [], "question": ""}
                else:
                    item = {"id": issue["id"], "status": "investigate",
                            "title": "Run reconciliation required",
                            "description": (f"Issue {issue['id']}: a previous run needs evidence-based reconciliation. Open the linked chat for details."
                                            if url else f"Issue {issue['id']}: a previous run needs evidence-based reconciliation. Run `python3 scripts/agent_learning.py pipeline-status` in the configured checkout for details."),
                            "options": [], "question": ""}
            elif issue["status"] == "needs-user":
                if not url:
                    raise ValueError("Configure a verified ordinary chat before sending decision requests.")
                if language == "it":
                    item = {key: issue.get(key, "") for key in
                            ("id", "status", "title", "description", "options", "question")}
                else:
                    item = {"id": issue["id"], "status": issue["status"],
                            "title": "Decision requires your review",
                            "description": f"Decision {issue['id']}: the original question and options are in the linked chat. Review them there and respond.",
                            "options": [], "question": "", "decision_details_unavailable": True}
            else:
                continue  # Technical candidates are input for consolidation, not human decisions.
            if language == "en":
                item["content_revision"] = issue_notification_revision(issue)
            items.append({**item, "chat_url": url})
        unclassified = set(live) - covered
        if unclassified:
            items.append({"id": "unclassified-notes", "status": "investigate", "title": "Assessment pending",
                          "description": f"{len(unclassified)} note(s) still need agent classification. This is not an approval request.",
                          "question": "", "options": [], "chat_url": url,
                          "note_ids": sorted(unclassified)})
        latest = {}
        unknown_attention = []
        correlated_attention = []
        unknown_attribution = []
        recovery_deadline = time.monotonic() + 3
        for run in state["runs"].values():
            ledger = state.get("run_recoveries", {})
            recovery = ledger.get(run.get("id")) if isinstance(ledger, dict) else None
            recovered = False
            if (isinstance(recovery, dict) and recovery.get("run_id") == run.get("id")
                    and self.verify_recovery is not None
                    and time.monotonic() < recovery_deadline):
                try:
                    self.verify_recovery(self, state, recovery, recovery_deadline)
                    recovered = True
                except (OSError, TypeError, ValueError, KeyError, RuntimeError, RecursionError, OverflowError):
                    pass  # Evidence unavailable or changed: keep the original alert open.
            if recovered:
                if run.get("runner") == "unknown":
                    unknown_attribution.append(run)
                continue
            if run.get("dispatch_status") == "correlated":
                overdue = run["status"] == "running" and now - datetime.fromisoformat(run["started_at"]) > timedelta(hours=2)
                if run["status"] in {"failed", "partial"} or overdue:
                    correlated_attention.append(run)
                continue
            if run["runner"] == "unknown":
                overdue = run["status"] == "running" and now - datetime.fromisoformat(run["started_at"]) > timedelta(hours=2)
                if run["status"] in {"failed", "partial"} or overdue:
                    unknown_attention.append(run)
                continue
            if run["runner"] not in latest or run["started_at"] > latest[run["runner"]]["started_at"]:
                latest[run["runner"]] = run
        if unknown_attention:
            run_ids = sorted(run["id"] for run in unknown_attention)
            shown = ", ".join(run_ids[:5])
            remainder = f" and {len(run_ids) - 5} more" if len(run_ids) > 5 else ""
            item = {"id": "runner-unknown", "status": "error", "title": "Consolidations with unknown runner: attention required",
                    "description": f"{len(run_ids)} unverified run(s) require reconciliation. Opaque run IDs (not runner evidence): {shown}{remainder}. Inspect pipeline status for details.",
                    "question": "", "options": [], "chat_url": url,
                    "run_ids": run_ids}
            if language == "en":
                item["content_revision"] = fingerprint([run_notification_revision(run)
                                                         for run in sorted(unknown_attention, key=lambda entry: entry["id"])])
            items.append(item)
        if correlated_attention:
            run_ids = sorted(run["id"] for run in correlated_attention)
            shown = ", ".join(run_ids[:5])
            remainder = f" and {len(run_ids) - 5} more" if len(run_ids) > 5 else ""
            item = {"id": "runner-correlated", "status": "error",
                    "title": "Agent-attested consolidations: attention required",
                    "description": f"{len(run_ids)} run(s) require reconciliation. Runner attribution is an agent attestation; receipt integrity is not rechecked here. Run IDs: {shown}{remainder}.",
                    "question": "", "options": [], "chat_url": url, "run_ids": run_ids}
            if language == "en":
                item["content_revision"] = fingerprint([run_notification_revision(run)
                                                         for run in sorted(correlated_attention, key=lambda entry: entry["id"])])
            items.append(item)
        if unknown_attribution:
            run_ids = sorted(run["id"] for run in unknown_attribution)
            shown = ", ".join(run_ids[:5])
            remainder = f" and {len(run_ids) - 5} more" if len(run_ids) > 5 else ""
            item = {"id": "runner-unknown-attribution", "status": "investigate",
                    "title": "Recovered runs with unknown attribution",
                    "description": f"Recovery was reviewed against saved evidence, but the runner remains unknown for: {shown}{remainder}.",
                    "question": "", "options": [], "chat_url": url, "run_ids": run_ids}
            if language == "en":
                item["content_revision"] = fingerprint([run_notification_revision(run)
                                                         for run in sorted(unknown_attribution, key=lambda entry: entry["id"])])
            items.append(item)
        for runner, run in latest.items():
            overdue = run["status"] == "running" and now - datetime.fromisoformat(run["started_at"]) > timedelta(hours=2)
            if run["status"] in {"failed", "partial"} or overdue:
                summary = run.get("summary", "Run still has no verified completion; the agent must reconcile it.")
                if language == "en":
                    summary = ("The consolidation needs attention. Open the linked chat for details."
                               if url else "The consolidation needs attention. Run `python3 scripts/agent_learning.py pipeline-status` in the configured checkout for details.")
                item = {"id": f"runner-{runner}", "status": "error", "title": f"Consolidation {runner}: attention required",
                        "description": summary,
                        "question": "", "options": [], "chat_url": url}
                if language == "en":
                    item["content_revision"] = run_notification_revision(run)
                items.append(item)
        legacy_signatures = {}
        for item in items:
            # Historical issue/run signatures cannot prove that unrendered references still match.
            # Send one migration notice for them; keep compatibility where the old item itself
            # fully represented the content shown to the user.
            revision_bound = "content_revision" in item
            old_link_signature = None
            legacy_item = {key: value for key, value in item.items() if key != "content_revision"}
            if item.get("chat_url"):
                native = native_chat_url(ordinary)
                if not revision_bound:
                    old_link_signature = fingerprint({**legacy_item, "chat_url": native})
                item["native_chat_url"] = native
            accepted_legacy = set()
            if not revision_bound:
                accepted_legacy.update({fingerprint(item), fingerprint(legacy_item)})
                if old_link_signature:
                    accepted_legacy.add(old_link_signature)
                    accepted_legacy.add(fingerprint({**legacy_item, "native_chat_url": native}))
            legacy_signatures[item["id"]] = accepted_legacy
            item["email_language"] = language
            item["signature"] = fingerprint(item)
        due = []
        for item in items:
            sent = state["sent"].get(item["id"], {})
            last = datetime.fromisoformat(sent["at"]) if sent.get("at") else None
            if (sent.get("signature") not in ({item["signature"]} | legacy_signatures.get(item["id"], set()))
                    or last is None or now - last >= timedelta(days=7)):
                due.append(item)
        if not due:
            return None
        digest_id = fingerprint([(item["id"], item["signature"]) for item in due])
        subject = f"Agent Learning: {sum(i['status'] == 'needs-user' for i in due)} decision(s), {sum(i['status'] != 'needs-user' for i in due)} issue(s)"
        if language == "it":
            subject = f"Agent Learning: {sum(i['status'] == 'needs-user' for i in due)} decisioni, {sum(i['status'] != 'needs-user' for i in due)} segnalazioni"
        body, markup = render(due, language)
        return {"digest_id": digest_id, "subject": subject, "body": body, "html": markup, "items": due}

    def reserve(self, digest: dict[str, Any], refresh=None) -> None:
        with self.lock():
            state = self.load()
            if state["outbox"]:
                raise ValueError("An earlier notification has no delivery receipt; reconcile it before sending again.")
            if refresh is not None:
                latest = refresh(state)
                if latest is None or latest["digest_id"] != digest["digest_id"]:
                    raise ValueError("Notification inputs changed before reservation; prepare again.")
            elif all(state["sent"].get(item["id"], {}).get("signature") == item["signature"]
                     and datetime.now().astimezone() - datetime.fromisoformat(state["sent"][item["id"]]["at"]) < timedelta(days=7)
                     for item in digest["items"]):
                raise ValueError("This notification was already acknowledged.")
            state["outbox"] = {"digest_id": digest["digest_id"], "items": digest["items"],
                               "created_at": datetime.now().astimezone().isoformat()}
            self.save(state)

    def acknowledge(self, digest_id: str, receipt: str, delivered: bool = True) -> None:
        if not clean(receipt):
            raise ValueError("Provider delivery evidence or a verified non-delivery reason is required.")
        with self.lock():
            state = self.load()
            pending = state["outbox"]
            if not pending or pending["digest_id"] != digest_id:
                raise ValueError("Notification does not match the pending submission.")
            if delivered:
                for item in pending["items"]:
                    state["sent"][item["id"]] = {"signature": item["signature"],
                                                 "at": datetime.now().astimezone().isoformat(), "receipt": clean(receipt)}
            state["outbox"] = None
            self.save(state)


def render(items: list[dict[str, Any]], language: str = "en") -> tuple[str, str]:
    italian = language == "it"
    language = "it" if italian else "en"
    heading = "Decisioni e stato delle esecuzioni" if italian else "Decisions and operational status"
    intro = ("Apri la chat collegata per consultare lo stato aggiornato e rispondere."
             if italian else "Open the linked chat for the current state and to respond.")
    decision_label = "Decisione" if italian else "Decision"
    link_label = "Apri la chat per rispondere" if italian else "Open the chat to respond"
    text = ["Agent Learning — " + heading, "", intro, ""]
    blocks = []
    esc = lambda value: html.escape(clean(value), quote=True)
    for item in items:
        text += [clean(item["title"]), clean(item["description"]), ""]
        content = f'<h2 style="font-size:20px;line-height:1.4;margin:0 0 12px">{esc(item["title"])}</h2>'
        content += f'<p style="margin:0 0 16px">{esc(item["description"])}</p>'
        if item["status"] == "needs-user":
            if item.get("decision_details_unavailable"):
                fallback = ("Apri la chat collegata per leggere la domanda e le opzioni originali, poi rispondi lì."
                            if italian else "The original question and options are available in the linked chat; review them and reply there.")
                text += [decision_label + ": " + fallback, ""]
                content += f'<p style="margin:0 0 16px"><strong>{decision_label}:</strong> {esc(fallback)}</p>'
            else:
                text += [decision_label + ": " + clean(item["question"]), ""]
                content += f'<p style="margin:0 0 16px"><strong>{decision_label}:</strong> {esc(item["question"])}</p>'
                content += '<ul style="padding-left:24px;margin:0 0 20px">'
                for option in item["options"]:
                    label = clean(option["id"]) + ". " + clean(option["label"])
                    if option.get("recommended"):
                        label += " (consigliata)" if italian else " (recommended)"
                    text += [label, "  " + clean(option["description"]), ""]
                    content += f'<li style="margin:0 0 14px"><strong>{esc(label)}</strong><br>{esc(option["description"])}</li>'
                content += '</ul>'
                instruction = (f'Rispondi nella chat con "{clean(item["id"])}: ID opzione" ed eventuali vincoli.' if italian
                               else f'Reply in the chat with "{clean(item["id"])}: option ID" and any constraints.')
                text += [instruction, ""]
                content += f'<p style="margin:0 0 16px">{esc(instruction)}</p>'
        if item.get("chat_url"):
            text += [link_label + ": " + item["chat_url"], ""]
            content += f'<p style="margin:0"><a style="color:#175cd3;text-decoration:underline" href="{esc(item["chat_url"])}">{link_label}</a></p>'
            fallback = ("If your mail app blocks the link, run this command in Terminal on the Mac running Codex:"
                        if not italian else "Se la posta blocca il link, esegui questo comando nel Terminale del Mac con Codex:")
            command = "open '" + item["native_chat_url"] + "'"
            text += [fallback, command, ""]
            content += f'<p>{fallback}<br><code>{esc(command)}</code></p>'
        blocks.append('<section style="padding:24px 0;border-bottom:1px solid #dfe3e8;overflow-wrap:anywhere;word-break:break-word">' + content + '</section>')
    markup = ('<!doctype html><html lang="' + language + '"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
              '<body style="margin:0;background:#ffffff;color:#202124">'
              '<main style="max-width:640px;margin:0 auto;padding:24px 20px;font-family:Arial,sans-serif;'
              'font-size:16px;line-height:1.6;overflow-wrap:anywhere;word-break:break-word">'
              '<h1 style="font-size:24px;line-height:1.3;margin:0 0 16px">Agent Learning</h1>'
              '<p style="margin:0 0 8px">' + heading + '. ' + intro + '</p>'
              + ''.join(blocks) + '</main></body></html>')
    return "\n".join(text).strip() + "\n", markup
