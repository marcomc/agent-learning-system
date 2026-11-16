"""Read-only verification of durable learning sources and deployed projections."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path
from learning_coherence import proposal_hash, verify_coherence


def concrete_routing_target_path(value: str) -> Path | None:
    """Resolve file targets; logical schemes accept only extensionless leaf labels."""
    schemes = {"atom", "global", "needs-review", "project-local", "skill", "skill-detection", "skill-prevention"}
    scheme, separator, target = value.partition(":")
    if separator and scheme in schemes:
        if (target and "/" not in target and "\\" not in target
                and not target.startswith((".", "~")) and not Path(target).suffix):
            return None
        if target:
            return Path(target).expanduser().resolve()
    return Path(value).expanduser().resolve()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_relocations(manifest: dict, covered_files: dict[str, str], *,
                       deadline: float | None = None) -> dict[str, dict[str, str]]:
    """Verify explicitly evidenced historical target coverage across Git worktrees."""
    mappings = manifest.get("relocations", [])
    if not isinstance(mappings, list) or len(mappings) > 128:
        raise ValueError("Relocations must be a bounded list.")
    result: dict[str, dict[str, str]] = {}
    git_cache: dict[tuple[str, ...], str] = {}
    evidence_cache: dict[tuple[Path, str], bytes] = {}
    checked_roots: set[tuple[Path, str, Path, str]] = set()
    checked_files: set[tuple[Path, str]] = set()
    checked_paths: set[tuple[Path, Path]] = set()
    impacts_by_receipt: dict[tuple[str, str], list] = {}
    note_parser = None
    reviewed_targets = {str(Path(target["path"]).expanduser().resolve())
                        for target in manifest.get("coherence", {}).get("targets", [])
                        if isinstance(target, dict) and isinstance(target.get("path"), str)}

    def read_evidence(path: Path) -> bytes:
        chunks: list[bytes] = []
        size = 0
        with path.open("rb") as handle:
            while True:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ValueError("Relocation verification budget exhausted.")
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > 16 * 1024 * 1024:
                    raise ValueError("Relocation evidence exceeds the verification limit.")
                chunks.append(chunk)
        return b"".join(chunks)

    def git(root: Path, *args: str) -> str:
        key = (str(root), *args)
        if key in git_cache:
            return git_cache[key]
        try:
            remaining = 5.0 if deadline is None else min(5.0, deadline - time.monotonic())
            if remaining <= 0:
                raise ValueError("Relocation verification budget exhausted.")
            env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
            output = subprocess.run(["git", "--no-optional-locks", "--literal-pathspecs", "-c", "core.fsmonitor=false",
                                     "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                                    check=True, capture_output=True, text=True, timeout=remaining, env=env).stdout.strip()
            git_cache[key] = output
            return output
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Relocation Git evidence is unavailable.") from exc

    for item in mappings:
        if deadline is not None and time.monotonic() >= deadline:
            raise ValueError("Relocation verification budget exhausted.")
        required = {"note_id", "source", "target", "source_root", "target_root", "source_head",
                    "target_head", "common_dir", "note", "snapshot", "prior_receipt"}
        semantic = isinstance(item, dict) and type(item.get("schema_version")) is int and item["schema_version"] == 2
        if semantic:
            required |= {"schema_version", "relation", "target_sha256", "note_impacts"}
        if (not isinstance(item, dict) or set(item) != required or not isinstance(item["note_id"], str)
                or item["note_id"] not in manifest.get("note_ids", [])):
            raise ValueError("Relocation needs an explicit note and complete evidence.")
        if semantic and (item["relation"] != "semantic-successor"
                         or manifest.get("coherence", {}).get("level") != "council"):
            raise ValueError("A semantic successor requires an explicit council proposal.")
        if any(not isinstance(item[k], str) or not Path(item[k]).is_absolute()
               for k in ("source", "target", "source_root", "target_root", "common_dir")):
            raise ValueError("Relocation paths must be absolute.")
        if any(not isinstance(item[k], str) or len(item[k]) not in (40, 64)
               or any(ch not in "0123456789abcdef" for ch in item[k]) for k in ("source_head", "target_head")):
            raise ValueError("Relocation needs exact Git HEAD identities.")
        source_root, target_root = Path(item["source_root"]).resolve(), Path(item["target_root"]).resolve()
        source, target = Path(item["source"]).resolve(), Path(item["target"]).resolve()
        if (source_root == target_root or not source.is_relative_to(source_root)
                or not target.is_relative_to(target_root)
                or source.relative_to(source_root) != target.relative_to(target_root)
                or Path(item["source"]).is_symlink() or not source.is_file()
                or Path(item["target"]).is_symlink() or not target.is_file()):
            raise ValueError("Relocation requires matching tracked paths in distinct worktrees.")
        checked_paths.update(((Path(item["source"]), source), (Path(item["target"]), target)))
        common = Path(item["common_dir"]).resolve()
        for root, expected_head in ((source_root, item["source_head"]), (target_root, item["target_head"])):
            actual_common = Path(git(root, "rev-parse", "--git-common-dir"))
            if not actual_common.is_absolute():
                actual_common = root / actual_common
            if (Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root
                    or actual_common.resolve() != common or git(root, "rev-parse", "HEAD") != expected_head):
                raise ValueError("Relocation worktree identity changed.")
            git(root, "ls-files", "--error-unmatch", "--", str(source.relative_to(source_root)))
            checked_roots.add((root, expected_head, common, str(source.relative_to(source_root))))
        registered = git(source_root, "worktree", "list", "--porcelain", "-z").split("\0")
        roots = {str(Path(x[9:]).resolve()) for x in registered if x.startswith("worktree ")}
        if str(source_root) not in roots or str(target_root) not in roots:
            raise ValueError("Relocation worktrees are not registered together.")
        evidence_bytes = []
        for field in ("snapshot", "prior_receipt", "note"):
            ref = item[field]
            if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
                    or not isinstance(ref["path"], str) or not Path(ref["path"]).is_absolute()
                    or not isinstance(ref["sha256"], str) or len(ref["sha256"]) != 64
                    or any(ch not in "0123456789abcdef" for ch in ref["sha256"])):
                raise ValueError("Relocation evidence needs absolute file and hash references.")
            path = Path(ref["path"]).resolve()
            if (path.is_relative_to(target_root) or path == source or not path.is_file()
                    or os.path.samefile(path, target) or os.path.samefile(path, source)):
                raise ValueError("Relocation evidence is missing, changed or circular.")
            key = (path, ref["sha256"])
            if key not in evidence_cache:
                raw = read_evidence(path)
                if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
                    raise ValueError("Relocation evidence is missing, changed or circular.")
                if sum(map(len, evidence_cache.values())) + len(raw) <= 32 * 1024 * 1024:
                    evidence_cache[key] = raw
            else:
                raw = evidence_cache[key]
            evidence_bytes.append(raw)
            checked_files.add((path, ref["sha256"]))
        try:
            prior = json.loads(evidence_bytes[1])
            previous_hash = prior["verified"]["files"][str(source)]
            prior_notes = prior["manifest"]["note_ids"]
            old_artifacts = {key: prior["verified"][key] for key in ("files", "skills", "generated")}
            if "relocations" in prior["verified"]:
                old_artifacts["relocations"] = prior["verified"]["relocations"]
            old_proposal = prior["verified"]["coherence"]["proposal_sha256"]
            old_reviews = prior["manifest"]["coherence"]["reviews"]
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ValueError("Relocation prior receipt does not cover the historical target.") from exc
        if (not isinstance(prior, dict) or prior.get("manifest", {}).get("version") != 1
                or prior.get("manifest", {}).get("coherence", {}).get("version") != 1
                or not isinstance(prior.get("verified_at"), str)
                or not isinstance(prior.get("verified", {}).get("coherence"), dict)
                or not isinstance(prior.get("verified", {}).get("skills"), dict)
                or not isinstance(prior.get("verified", {}).get("generated"), dict)
                or not isinstance(prior_notes, list) or item["note_id"] not in prior_notes
                or not isinstance(old_reviews, list) or not old_reviews
                or any(not isinstance(review, dict) or review.get("verdict") != "pass"
                       or review.get("proposal_sha256") != old_proposal for review in old_reviews)
                or proposal_hash(prior["manifest"], old_artifacts) != old_proposal
                or previous_hash != item["snapshot"]["sha256"] or str(target) not in reviewed_targets):
            raise ValueError("Relocation historical and candidate evidence differ.")
        successor_hash = item["target_sha256"] if semantic else previous_hash
        if (not isinstance(successor_hash, str) or len(successor_hash) != 64
                or any(ch not in "0123456789abcdef" for ch in successor_hash)
                or covered_files.get(str(target)) != successor_hash
                or hashlib.sha256(read_evidence(target)).hexdigest() != successor_hash):
            raise ValueError("Relocation successor evidence differs.")
        if semantic:
            targets = [x for x in manifest["coherence"]["targets"]
                       if str(Path(x["path"]).expanduser().resolve()) == str(target)]
            if (len(targets) != 1 or targets[0].get("operation") != "refine"
                    or targets[0].get("before") != item["snapshot"]):
                raise ValueError("Semantic successor must review the exact historical preimage.")
            impacts = item["note_impacts"]
            required_impact = {"note_id", "disposition", "reason", "note", "prevention_targets", "detection_targets"}
            if (not isinstance(impacts, list) or not impacts or len(impacts) > 128 or len(impacts) != len(prior_notes)
                    or any(not isinstance(note_id, str) for note_id in prior_notes)
                    or len(set(prior_notes)) != len(prior_notes)
                    or set(manifest["note_ids"]) != set(prior_notes)):
                raise ValueError("Semantic successor must account for every historical note.")
            receipt_key = (item["prior_receipt"]["path"], item["prior_receipt"]["sha256"])
            if impacts_by_receipt and receipt_key not in impacts_by_receipt:
                raise ValueError("Semantic successor proposals cover one historical receipt at a time.")
            if receipt_key in impacts_by_receipt and impacts_by_receipt[receipt_key] != impacts:
                raise ValueError("Semantic successor mappings disagree on note impacts.")
            impacts_by_receipt[receipt_key] = impacts
            impact_ids = set()
            for impact in impacts:
                if (not isinstance(impact, dict) or set(impact) != required_impact
                        or not isinstance(impact["note_id"], str) or impact["note_id"] in impact_ids
                        or impact["disposition"] not in {"preserved", "updated"}
                        or not isinstance(impact["reason"], str) or not impact["reason"].strip()
                        or not isinstance(impact["prevention_targets"], list)
                        or not isinstance(impact["detection_targets"], list)
                        or any(not isinstance(path, str) for path in
                               impact["prevention_targets"] + impact["detection_targets"])):
                    raise ValueError("Semantic successor needs a resolved disposition for every note.")
                note_ref = impact["note"]
                if (not isinstance(note_ref, dict) or set(note_ref) != {"path", "sha256"}
                        or not isinstance(note_ref["path"], str) or not Path(note_ref["path"]).is_absolute()
                        or not isinstance(note_ref["sha256"], str) or len(note_ref["sha256"]) != 64
                        or any(ch not in "0123456789abcdef" for ch in note_ref["sha256"])):
                    raise ValueError("Semantic successor needs current archived note evidence.")
                note_path = Path(note_ref["path"]).resolve()
                key = (note_path, note_ref["sha256"])
                if key not in evidence_cache and note_path.is_file():
                    raw = read_evidence(note_path)
                    if hashlib.sha256(raw).hexdigest() == note_ref["sha256"]:
                        evidence_cache[key] = raw
                if key not in evidence_cache:
                    raise ValueError("Semantic successor note evidence changed.")
                try:
                    if note_parser is None:
                        spec = importlib.util.spec_from_file_location(
                            "_agent_learning_relocation_parser", Path(__file__).with_name("agent_learning.py"))
                        if spec is None or spec.loader is None:
                            raise ValueError("Semantic successor note parser is unavailable.")
                        note_parser = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(note_parser)
                    frontmatter, _ = note_parser.parse_frontmatter(
                        evidence_cache[key].decode("utf-8"), require_note_identity=True)
                    if (frontmatter.get("id") != impact["note_id"]
                            or note_parser.coerce_list(frontmatter.get("prevention_targets")) != impact["prevention_targets"]
                            or note_parser.coerce_list(frontmatter.get("detection_targets")) != impact["detection_targets"]):
                        raise ValueError("Semantic successor note identity or routing differs from the archive.")
                except (UnicodeError, RuntimeError) as exc:
                    raise ValueError("Semantic successor archived note metadata is invalid.") from exc
                checked_files.add((note_path, note_ref["sha256"]))
                impact_ids.add(impact["note_id"])
            if impact_ids != set(prior_notes):
                raise ValueError("Semantic successor note dispositions are incomplete.")
            for impact in impacts:
                routed = {str(path) for value in impact["prevention_targets"] + impact["detection_targets"]
                          if (path := concrete_routing_target_path(value)) is not None}
                if str(source) in routed and not any(
                        isinstance(other, dict) and other.get("schema_version") == 2
                        and other.get("note_id") == impact["note_id"]
                        and other.get("source") == item["source"] and other.get("target") == item["target"]
                        and other.get("prior_receipt") == item["prior_receipt"] for other in mappings):
                    raise ValueError("Semantic successor needs a mapping for every note sharing the source.")
        checked_files.add((target, successor_hash))
        note = result.setdefault(item["note_id"], {})
        if str(source) in note:
            raise ValueError("Relocation mapping is ambiguous.")
        note[str(source)] = str(target)
    git_cache.clear()
    for root, expected_head, common, relative in checked_roots:
        actual_common = Path(git(root, "rev-parse", "--git-common-dir"))
        if not actual_common.is_absolute():
            actual_common = root / actual_common
        if (Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root
                or actual_common.resolve() != common or git(root, "rev-parse", "HEAD") != expected_head):
            raise ValueError("Relocation worktree identity changed during verification.")
        git(root, "ls-files", "--error-unmatch", "--", relative)
        registered = git(root, "worktree", "list", "--porcelain", "-z").split("\0")
        if str(root) not in {str(Path(x[9:]).resolve()) for x in registered if x.startswith("worktree ")}:
            raise ValueError("Relocation worktree registration changed during verification.")
    if any(path.is_symlink() or not path.is_file() or path.resolve() != expected
           for path, expected in checked_paths):
        raise ValueError("Relocation path changed during verification.")
    if any(not path.is_file() or hashlib.sha256(read_evidence(path)).hexdigest() != expected
           for path, expected in checked_files):
        raise ValueError("Relocation evidence changed during verification.")
    return result


def skill_roots(extra: list[str]) -> list[Path]:
    home = Path.home()
    roots = [home / value for value in (
        ".agents/skills", ".codex/skills", ".claude/skills", ".gemini/skills",
        ".config/opencode/skills", ".hermes/skills", ".copilot/skills",
        ".openclaw/skills", ".clawdbot/skills", ".moltbot/skills",
        ".gemini/antigravity/skills", ".gemini/antigravity-cli/skills",
    )]
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        if os.environ.get(key):
            roots.append(Path(os.environ[key]) / "skills")
    if os.environ.get("XDG_CONFIG_HOME"):
        roots.append(Path(os.environ["XDG_CONFIG_HOME"]) / "opencode/skills")
    return roots + [Path(raw).expanduser() for raw in extra]


def tree(root: Path, local_files: list[str]) -> dict[str, str]:
    if not root.is_dir() or not (root / "SKILL.md").is_file():
        raise ValueError(f"Missing skill payload: {root}")
    for name in local_files:
        if name != "config/template_repo_path.txt":
            raise ValueError(f"Unsupported installation-only file: {name}")
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {".git", "__pycache__", ".pytest_cache"} for part in relative.parts):
            continue
        if path.name == ".DS_Store" or str(relative) in local_files:
            continue
        if path.is_symlink() and not path.exists():
            raise ValueError(f"Broken payload link: {path}")
        if path.is_symlink() and path.is_dir():
            raise ValueError(f"Directory symlinks inside skill payloads are unsupported: {path}")
        if path.is_file():
            result[str(relative)] = digest(path)
    return result


def verify_distribution(manifest: dict, *, deadline: float | None = None,
                        allow_audit: bool = False) -> dict:
    """Validate coverage assertions, exact payload parity and explicit file hashes.

    Ownership and semantic review belong to the caller. This function executes no
    commands and never copies files or mutates a learning store.
    """
    if manifest.get("version") != 1 or not manifest.get("rationale", "").strip():
        raise ValueError("Integration needs version 1 and a semantic review rationale.")
    ids = manifest.get("note_ids")
    if (not isinstance(ids, list) or (not ids and not allow_audit)
            or any(not isinstance(i, str) or not i for i in ids)):
        raise ValueError("Integration needs explicit note_ids.")
    if allow_audit and (ids or not manifest.get("audit_sources")):
        raise ValueError("Audit coherence requires audit sources and no note IDs.")
    files = manifest.get("files", [])
    if not files:
        raise ValueError("Integration needs concrete file coverage, including no-op outcomes.")
    fingerprints = {}
    for item in files:
        path = Path(item["path"]).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"Missing absolute integration target: {path}")
        actual = digest(path)
        if actual != item.get("sha256"):
            raise ValueError(f"Integration target changed: {path}")
        needles = item.get("contains", [])
        if not needles or any(not isinstance(n, str) or not n.strip() for n in needles):
            raise ValueError(f"Integration needs reviewed coverage text: {path}")
        text = path.read_text()
        if any(needle not in text for needle in needles):
            raise ValueError(f"Missing reviewed rule in {path}")
        fingerprints[str(path.resolve())] = actual
    payloads = {}
    for item in manifest.get("skills", []):
        source = Path(item["source"]).expanduser()
        copies = item.get("copies", [])
        if not source.is_absolute() or not copies:
            raise ValueError("Skill verification needs an owning source and all configured copies.")
        if ".agents" in source.parts or ".codex" in source.parts:
            raise ValueError("Resolve an owning repository; installed skill paths are not sources.")
        declared = {str(Path(raw).expanduser().absolute()) for raw in copies}
        for root in skill_roots(manifest.get("skill_roots", [])):
            candidate = root / source.name
            if (candidate.exists() or candidate.is_symlink()) and str(candidate.absolute()) not in declared:
                raise ValueError(f"Integration omitted an installed skill copy: {candidate}")
        exclusions = item.get("local_files", [])
        expected = tree(source, exclusions)
        payloads[str(source.resolve())] = expected
        for raw in copies:
            destination = Path(raw).expanduser()
            if not destination.is_absolute():
                raise ValueError("Installed skill paths must be absolute.")
            actual = tree(destination, exclusions)
            if actual != expected:
                changed = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
                raise ValueError(f"Skill projection differs: {destination}: {', '.join(changed)}")
            payloads[str(destination.resolve())] = actual
    if manifest.get("template_status") in {"draft-created", "deferred", "approved"}:
        raise ValueError("A draft or deferred handoff is not integrated.")
    for item in files:
        path = Path(item["path"]).expanduser().resolve()
        owner = next((p for p in path.parents if (p / "SKILL.md").is_file()), None)
        if owner and str(owner) not in payloads:
            raise ValueError(f"Skill coverage requires owning-source and projection verification: {owner}")
    generated = {}
    for item in manifest.get("template_consumers", []):
        repo = Path(item["repository"]).expanduser().resolve()
        helper = repo / "skills/update-agents-file-templates/scripts/update_agents_templates.py"
        spec = importlib.util.spec_from_file_location("integration_template_helper", helper)
        if spec is None or spec.loader is None:
            raise ValueError("Template helper is unavailable.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        init = module.load_init_helper()
        try:
            config = module.load_manifest(repo)
            ids = item.get("template_ids", [])
            known = {row["id"]: row["path"] for row in config.get("project_types", [])}
            if not ids or any(key not in known for key in ids):
                raise ValueError("Template consumer verification needs current template IDs.")
            if any(str((repo / known[key]).resolve()) not in fingerprints for key in ids):
                raise ValueError("Integration omitted a curated template source.")
            roots = [Path(raw).expanduser().resolve() for raw in item["roots"]]
            if not roots or any(not root.is_dir() for root in roots):
                raise ValueError("Template verification needs available consumer scan roots.")
            ignored = set(config.get("scan_defaults", {}).get("ignored_dirs", []))
            def unavailable(error):
                raise ValueError(f"Consumer scan is incomplete: {error}")
            for root in roots:
                for current, directories, _ in os.walk(root, onerror=unavailable):
                    depth = len(Path(current).relative_to(root).parts)
                    directories[:] = [name for name in directories if name not in ignored]
                    if depth >= item.get("max_depth", 2) + 2:
                        directories[:] = []
            paths = module.find_agents(roots, item.get("max_depth", 2), ignored)
            for path in paths:
                text = path.read_text()
                if init.GENERATED_MARKER not in text:
                    continue
                if not set(init.generated_types(text)).intersection(item["template_ids"]):
                    continue
                project = module.project_root_for_instruction(path)
                agent = module.instruction_agent(path, text, init.AGENT_GLOBAL_PATHS)
                result = init.check_output(project, repo, [], [], agent, path.relative_to(project).as_posix())
                if result["status"] != "current":
                    raise ValueError(f"Generated instructions need refresh: {path}")
                generated[str(path.resolve())] = digest(path)
        except SystemExit as exc:
            raise ValueError(f"Template verification unavailable: {exc}") from exc
    if manifest.get("template_status") == "promoted" and not manifest.get("template_consumers"):
        if not manifest.get("global_projections"):
            raise ValueError("Template promotion requires consumer verification or explicit global projections.")
        if any(str(Path(p).expanduser().resolve()) not in fingerprints for p in manifest["global_projections"]):
            raise ValueError("Global projections require current file coverage.")
    result = {"files": fingerprints, "skills": payloads, "generated": generated}
    if "relocations" in manifest:
        covered = {**fingerprints, **generated}
        for skill_root, payload in payloads.items():
            covered.update({str(Path(skill_root) / name): value for name, value in payload.items()})
        result["relocations"] = verify_relocations(manifest, covered, deadline=deadline)
    return result


def verify(manifest: dict, *, deadline: float | None = None) -> dict:
    distribution = verify_distribution(manifest, deadline=deadline)
    return {**distribution, "coherence": verify_coherence(manifest, distribution)}


def validate_receipt(reference: dict) -> dict:
    path = Path(reference.get("path", ""))
    if not path.is_file():
        raise ValueError("Integration receipt is missing or changed.")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != reference.get("sha256"):
        raise ValueError("Integration receipt is missing or changed.")
    receipt = json.loads(raw)
    if verify(receipt["manifest"]) != receipt["verified"]:
        raise ValueError("Integration payload changed since verification.")
    if path.read_bytes() != raw:
        raise ValueError("Integration receipt changed during verification.")
    return receipt
