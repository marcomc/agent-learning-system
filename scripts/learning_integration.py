"""Read-only verification of durable learning sources and deployed projections."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from learning_coherence import verify_coherence


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def verify_distribution(manifest: dict) -> dict:
    """Validate coverage assertions, exact payload parity and explicit file hashes.

    Ownership and semantic review belong to the caller. This function executes no
    commands and never copies files or mutates a learning store.
    """
    if manifest.get("version") != 1 or not manifest.get("rationale", "").strip():
        raise ValueError("Integration needs version 1 and a semantic review rationale.")
    ids = manifest.get("note_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("Integration needs explicit note_ids.")
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
    return {"files": fingerprints, "skills": payloads, "generated": generated}


def verify(manifest: dict) -> dict:
    distribution = verify_distribution(manifest)
    return {**distribution, "coherence": verify_coherence(manifest, distribution)}


def validate_receipt(reference: dict) -> dict:
    path = Path(reference.get("path", ""))
    if not path.is_file() or digest(path) != reference.get("sha256"):
        raise ValueError("Integration receipt is missing or changed.")
    receipt = json.loads(path.read_text())
    if verify(receipt["manifest"]) != receipt["verified"]:
        raise ValueError("Integration payload changed since verification.")
    return receipt
