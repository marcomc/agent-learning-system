"""Validate input-bound evidence of proportionate instruction review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


OPERATIONS = {"already-covered", "clarify", "refine", "merge", "replace", "relocate", "add"}
CHECKS = {"hierarchy", "consistency", "coverage", "placement", "cost"}


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_ref(ref: dict) -> Path:
    if not isinstance(ref, dict):
        raise ValueError("Coherence evidence must be a file/hash object.")
    path = Path(ref.get("path", "")).expanduser()
    if not path.is_absolute() or not path.is_file() or file_hash(path) != ref.get("sha256"):
        raise ValueError(f"Coherence evidence is missing or stale: {path}")
    return path


def proposal_hash(manifest: dict, verified: dict) -> str:
    assessment = {k: v for k, v in manifest["coherence"].items() if k != "reviews"}
    inputs = {k: v for k, v in manifest.items() if k != "coherence"}
    raw = json.dumps({"manifest": inputs, "assessment": assessment, "artifacts": verified}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def verify_coherence(manifest: dict, verified: dict, *, require_reviews: bool = True) -> dict:
    """Require review evidence, not a claim that hashes establish semantic truth."""
    review = manifest.get("coherence")
    if not isinstance(review, dict) or review.get("version") != 1:
        raise ValueError("Coherence review is required; distribution-only receipts are historical proof.")
    if review.get("unresolved") != []:
        raise ValueError("Resolve coherence findings before integration.")
    checks = review.get("checks", {})
    if set(checks) != CHECKS or any(not isinstance(v, str) or not v.strip() for v in checks.values()):
        raise ValueError("Coherence review requires hierarchy, consistency, coverage, placement and cost reasoning.")
    context = review.get("context", [])
    if (not isinstance(context, list) or not context or any(not isinstance(item, dict) for item in context)
            or not any(item.get("role") == "policy" for item in context)):
        raise ValueError("Review the governing policy context before integration.")
    context_paths = {str(checked_ref(item).resolve()) for item in context}
    targets = review.get("targets", [])
    if not isinstance(targets, list) or not targets or any(not isinstance(item, dict) for item in targets):
        raise ValueError("Coherence review needs explicit owning targets and preimages.")
    observed = dict(verified["files"])
    for root, payload in verified["skills"].items():
        observed.update({str(Path(root) / name): value for name, value in payload.items()})
    sizes, paths = [], set()
    council = False
    changed_targets = 0
    normative = False
    for item in targets:
        path = Path(item["path"]).expanduser().resolve()
        if str(path) in paths:
            raise ValueError("A coherence target must appear once.")
        paths.add(str(path))
        if str(path) not in observed or not path.is_file() or file_hash(path) != observed[str(path)]:
            raise ValueError(f"Coherence target is not in current integration coverage: {path}")
        operation = item.get("operation")
        if operation not in OPERATIONS or not item.get("rationale", "").strip():
            raise ValueError("Record the integration operation and rationale for every target.")
        before = checked_ref(item.get("before", {}))
        if before.resolve() == path:
            raise ValueError("Preserve a distinct before snapshot; the live target is not its preimage.")
        old, new = before.read_text(), path.read_text()
        changed = old != new
        changed_targets += changed
        if operation == "already-covered" and old != new:
            raise ValueError("Already-covered requires an unchanged target.")
        if changed and (operation in {"merge", "replace", "relocate"} or item.get("kind") == "global"):
            council = True
        normative |= changed and operation not in {"already-covered", "clarify"}
        if (len(new.split()) > len(old.split()) or len(new) > len(old)) and not item.get("growth_rationale", "").strip():
            raise ValueError("Instruction growth requires an explicit benefit and routing rationale.")
        sizes.append({"path": str(path), "before_words": len(old.split()), "after_words": len(new.split()),
                      "before_characters": len(old), "after_characters": len(new)})
    if not set(verified["files"]).issubset(paths | context_paths):
        raise ValueError("Every covered file must be a reviewed target or explicit context.")
    risks = review.get("risks")
    if not isinstance(risks, dict) or set(risks) != {"authority_change", "resolved_conflict", "material_growth"}:
        raise ValueError("Classify authority changes, resolved conflicts and material context growth.")
    if any(type(value) is not bool for value in risks.values()):
        raise ValueError("Coherence risk classifications must be booleans.")
    council |= changed_targets > 1 or any(risks.values())
    minimum_level = "council" if council else ("peer" if normative else "local")
    levels = {"local": 0, "peer": 1, "council": 2}
    required_level = review.get("level")
    if required_level not in levels or levels[required_level] < levels[minimum_level]:
        raise ValueError(f"This change requires at least {minimum_level} review.")
    if not review.get("editor", "").strip():
        raise ValueError("Coherence review needs one accountable editor.")
    proposal = proposal_hash(manifest, verified)
    context_words = sum(len(Path(path).read_text().split()) for path in context_paths - paths)
    result = {"level": required_level, "proposal_sha256": proposal, "sizes": sizes,
              "minimum_level": minimum_level, "context_words": context_words}
    if not require_reviews:
        return result
    reviews = review.get("reviews", [])
    if not isinstance(reviews, list) or any(not isinstance(item, dict) for item in reviews):
        raise ValueError("Coherence reviews must be evidence objects.")
    roles, identities = set(), set()
    for item in reviews:
        if item.get("proposal_sha256") != proposal or item.get("verdict") != "pass":
            raise ValueError("Review evidence must approve the exact current proposal.")
        role, identity = item.get("role"), item.get("reviewer", "")
        if role in roles or not identity.strip():
            raise ValueError("Review roles must be unique and identify their reviewer.")
        if required_level != "local" and (identity == review["editor"] or identity in identities):
            raise ValueError("Independent review requires distinct reviewers, separate from the editor.")
        report = checked_ref(item.get("evidence", {}))
        if proposal not in report.read_text():
            raise ValueError("The review report must name its frozen proposal hash.")
        roles.add(role)
        identities.add(identity)
    expected = {"policy", "ergonomics"} if required_level == "council" else {"reviewer"}
    if roles != expected:
        raise ValueError(f"Coherence review requires these roles: {', '.join(sorted(expected))}")
    return result
