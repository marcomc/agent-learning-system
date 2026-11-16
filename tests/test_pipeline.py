from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from email import policy
from email.parser import Parser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_agent_learning as support


ORDINARY = "11111111-1111-1111-1111-111111111111"
RELEASE = "22222222-2222-2222-2222-222222222222"


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.app = support.load_agent_learning_module()
        self.config = support.AgentLearningTests().write_config(self.directory)
        self.root = self.app.learning_root(self.app.load_config(self.config))
        self.app.ensure_store(self.root)
        self.pipeline = self.app.pipeline_store(self.root)
        self.pipeline.configure(ORDINARY, RELEASE)
        self.target = self.directory / "AGENTS.md"
        self.target.write_text("# Instructions\n\n- Require a verified current revision before publication.\n")
        self.report = self.root / "reports" / "proof.md"
        self.report.write_text("# Verification\n\nThe complete instruction contract is preserved.\n")
        self.note = self.root / "needs-review" / "decision.md"
        self.note.write_text('---\nid: "note-1"\nstatus: "needs-review"\n---\n# Decision\n\n' + self.app.REVIEW_BLOCK)
        self.goal_created_times = {}

    def ref(self, path):
        return self.app.snapshot(str(path))

    def release_candidate(self):
        subprocess.run(["git", "init", "-q", str(self.directory)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.directory), "add", "AGENTS.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.directory), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.directory), "remote", "add", "origin",
                        "https://github.com/example/project.git"], check=True, capture_output=True)
        root = self.directory / "release-candidate"
        subprocess.run(["git", "-C", str(self.directory), "worktree", "add", "--detach", str(root)],
                       check=True, capture_output=True)
        changelog = root / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [1.4.0] - 2026-11-15\n\n- Candidate.\n")
        subprocess.run(["git", "-C", str(self.directory), "update-ref", "refs/remotes/origin/main",
                        subprocess.check_output(["git", "-C", str(self.directory), "rev-parse", "HEAD"], text=True).strip()],
                       check=True, capture_output=True)
        return root, changelog, root / "AGENTS.md"

    def release_assessment(self, candidate_root, classification="defer"):
        payload = self.app.release_assessment_skeleton(self.pipeline.status(), candidate_root)
        for item in payload["assessments"]:
            for evidence in item["evidence"]:
                evidence["sha256"] = hashlib.sha256(Path(evidence["path"]).read_bytes()).hexdigest()
            item.update(classification=classification, reviewer="independent reviewer",
                        rationale="The exact issue was reviewed against this candidate and has no blocking dependency.",
                        candidate_relation="Its current target and source are outside the changed candidate contract.",
                        dependency_review="Its current consumers and runtime dependencies do not depend on this candidate.")
        path = self.root / "reports" / "release-assessment.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return payload, path

    def mark_unrelated(self, payload, issue_id):
        from learning_pipeline import fingerprint

        record = next(item for item in payload["assessments"] if item["issue_id"] == issue_id)
        issue = self.pipeline.status()["issues"][issue_id]
        record["classification"] = "unrelated"
        record["evidence"] = []
        record["scope_review"] = {
            "decision": "unrelated", "issue_sha256": fingerprint(issue),
            "candidate_sha256": fingerprint(payload["candidate"]),
            "reviewer": "independent scope reviewer", "assessment_author": record["reviewer"],
            "rationale": "The current finding concerns a separate project and its own runtime behavior.",
            "candidate_relation": "Its recorded source and target paths are outside this candidate checkout.",
            "dependency_review": "The candidate diff has no consumer or dependency relationship to that project.",
        }
        return record

    def candidate_integration_proof(self, candidate_root, issue, note_id="candidate-note", include_archive_sha=False,
                                    issue_targets_source=False):
        import learning_coherence
        import learning_integration
        from learning_pipeline import candidate_change_snapshot, fingerprint

        candidate_file = candidate_root / "AGENTS.md"
        if issue_targets_source:
            issue["targets"] = [self.ref(self.target)]
        archived = self.root / "processed" / "candidate-note.md"
        archived.parent.mkdir(exist_ok=True)
        archived.write_text(self.app.render_frontmatter(
            {"id": note_id, "prevention_targets": json.dumps([str(self.target)]),
             "detection_targets": "[]"}, "# Candidate integration note\n"), encoding="utf-8")
        issue_evidence_path = self.root / "reports" / "candidate-issue-evidence.json"
        issue_evidence_path.write_text(json.dumps({"note_id": note_id,
                                                    "targets": [ref["path"] for ref in issue.get("targets", [])]}),
                                       encoding="utf-8")
        issue["evidence"] = [str(issue_evidence_path)]
        if note_id == "candidate-note":
            state = self.app.load_state(self.root)
            state.setdefault("processed", {})[note_id] = {
                "path": str(archived), "status": "processed",
                "prevention_targets": [str(self.target)], "detection_targets": []}
            if include_archive_sha:
                state["processed"][note_id]["archive_sha256"] = hashlib.sha256(archived.read_bytes()).hexdigest()
            self.app.save_state(self.root, state)

        manifest = {"version": 1, "note_ids": [note_id],
                    "rationale": "The candidate target retains the archived note's reviewed instruction.",
                    "files": [{**self.ref(candidate_file), "contains": ["verified current revision"]}],
                    "coherence": {"version": 1, "editor": "fixture-editor", "level": "local",
                                  "unresolved": [], "risks": {"authority_change": False,
                                                               "resolved_conflict": False,
                                                               "material_growth": False},
                                  "checks": {key: "Reviewed the complete instruction contract."
                                             for key in learning_coherence.CHECKS},
                                  "context": [{**self.ref(self.report), "role": "policy"}],
                                  "targets": [{"path": str(candidate_file), "kind": "automation",
                                               "operation": "already-covered",
                                               "rationale": "The candidate preserves the verified behavior.",
                                               "growth_rationale": "No target growth occurred.",
                                               "before": self.ref(self.target)}], "reviews": []}}
        if issue_targets_source:
            manifest["files"].append({**self.ref(self.target), "contains": ["verified current revision"]})
            manifest["coherence"]["context"].append({**self.ref(self.target), "role": "reference"})
        verified = learning_integration.verify_distribution(manifest)
        proposal = learning_coherence.proposal_hash(manifest, verified)
        coherence_report = self.root / "reports" / "candidate-coherence-review.txt"
        coherence_report.write_text("Reviewed proposal " + proposal + "\n")
        manifest["coherence"]["reviews"] = [{"role": "reviewer", "reviewer": "fixture-editor",
                                                "proposal_sha256": proposal, "verdict": "pass",
                                                "evidence": self.ref(coherence_report)}]
        manifest_path = self.root / "reports" / "candidate-integration-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_ref = self.ref(manifest_path)
        candidate = candidate_change_snapshot(candidate_root)
        record = {"reviewer": "release assessor", "candidate_integration": {
            "manifests": [manifest_ref], "independent_review": {"path": "", "sha256": ""}}}
        proof = {"schema_version": 1, "review_kind": "candidate-integration", "decision": "verified",
                 "issue_id": issue["id"], "issue_sha256": fingerprint(issue),
                 "candidate_sha256": fingerprint(candidate), "reviewer": "separate candidate reviewer",
                 "assessment_author": record["reviewer"], "manifests": [manifest_ref],
                 "note_ids": [note_id], "evidence": [{"path": str(issue_evidence_path.resolve()),
                                                         "sha256": hashlib.sha256(issue_evidence_path.read_bytes()).hexdigest()}],
                 "note_hashes": {note_id: hashlib.sha256(archived.read_bytes()).hexdigest()},
                 "rationale": "The mapped archived source and candidate target were independently verified."}
        review_path = self.root / "reports" / "candidate-integration-review.json"
        review_path.write_text(json.dumps(proof), encoding="utf-8")
        record["candidate_integration"]["independent_review"] = self.ref(review_path)
        return record, candidate

    def candidate_issue_evidence(self, issue):
        path = Path(issue["evidence"][0])
        return [{**self.ref(path), "resolved_path": str(path.resolve())}]

    def open_audit_finding(self, run_id):
        self.target.write_text(
            "# Instructions\n\n- Require a verified current revision before publication.\n\n"
            "- Require a verified current revision before publication.\n", encoding="utf-8")
        self.pipeline.begin(run_id, "noon", initial_inbox=[])
        self.pipeline.audit(run_id, self.audit_payload(), [str(self.target)], True)
        issue = next(issue for issue in self.pipeline.status()["issues"].values()
                     if issue.get("kind") == "audit" and issue.get("status") == "candidate")
        self.pipeline.finish(run_id, "completed", "Verified", str(self.report), [])
        return issue

    def attach_audit_deferral(self, record, issue, candidate):
        from learning_pipeline import fingerprint

        record["reviewer"] = "release assessor"
        evidence_refs = sorted(({"path": item["path"], "sha256": item["sha256"]}
                                for item in record["evidence"]), key=lambda ref: ref["path"])
        target_refs = sorted(({"path": item["path"], "sha256": item["sha256"]}
                              for item in issue["targets"]), key=lambda ref: ref["path"])
        candidate_sha = fingerprint(candidate)
        review_path = self.root / "reports" / f"{issue['id']}-independent-review.json"
        review = {"schema_version": 1, "review_kind": "candidate-audit-relevance", "decision": "unrelated",
                  "issue_id": issue["id"], "issue_sha256": fingerprint(issue), "candidate_sha256": candidate_sha,
                  "reviewer": "separate audit reviewer", "assessment_author": record["reviewer"],
                  "rationale": record["rationale"], "candidate_relation": record["candidate_relation"],
                  "dependency_review": record["dependency_review"], "targets": target_refs,
                  "evidence": evidence_refs}
        review_path.write_text(json.dumps(review), encoding="utf-8")
        review_ref = {"path": str(review_path), "sha256": hashlib.sha256(review_path.read_bytes()).hexdigest()}
        authorization_path = self.root / "reports" / f"{issue['id']}-user-authorization.json"
        authorization = {"schema_version": 1, "decision": "approved", "scope": "candidate-audit-deferral",
                         "issue_id": issue["id"], "issue_sha256": fingerprint(issue),
                         "candidate_sha256": candidate_sha, "independent_review": review_ref,
                         "user_request_reference": "fixture-user-request-reference-001",
                         "thread_id": ORDINARY, "recorded_at": datetime.now().astimezone().isoformat()}
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        authorization_ref = {"path": str(authorization_path),
                             "sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest()}
        record["audit_deferral"] = {"independent_review": review_ref, "authorization": authorization_ref}
        return review_path, authorization_path

    def attach_review_deferral(self, record, issue, candidate):
        from learning_pipeline import fingerprint

        record["reviewer"] = "release assessor"
        evidence_refs = sorted(({"path": item["path"], "sha256": item["sha256"]}
                                for item in record["evidence"]), key=lambda ref: ref["path"])
        source_refs = sorted(({"path": item["path"], "sha256": item["sha256"]}
                              for item in issue["sources"]), key=lambda ref: ref["path"])
        target_refs = sorted(({"path": item["path"], "sha256": item["sha256"]}
                              for item in issue["targets"]), key=lambda ref: ref["path"])
        candidate_sha = fingerprint(candidate)
        review_path = self.root / "reports" / f"{issue['id']}-independent-review.json"
        review = {"schema_version": 1, "review_kind": "candidate-issue-relevance", "decision": "unrelated",
                  "issue_id": issue["id"], "issue_sha256": fingerprint(issue), "candidate_sha256": candidate_sha,
                  "reviewer": "separate review assessor", "assessment_author": record["reviewer"],
                  "rationale": record["rationale"], "candidate_relation": record["candidate_relation"],
                  "dependency_review": record["dependency_review"], "sources": source_refs,
                  "targets": target_refs, "evidence": evidence_refs}
        review_path.write_text(json.dumps(review), encoding="utf-8")
        review_ref = {"path": str(review_path), "sha256": hashlib.sha256(review_path.read_bytes()).hexdigest()}
        authorization_path = self.root / "reports" / f"{issue['id']}-user-authorization.json"
        authorization = {"schema_version": 1, "decision": "approved", "scope": "candidate-review-release-deferral",
                         "issue_id": issue["id"], "issue_sha256": fingerprint(issue),
                         "candidate_sha256": candidate_sha, "independent_review": review_ref,
                         "user_request_reference": "fixture-user-request-reference-001",
                         "thread_id": ORDINARY, "recorded_at": datetime.now().astimezone().isoformat()}
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        authorization_ref = {"path": str(authorization_path),
                             "sha256": hashlib.sha256(authorization_path.read_bytes()).hexdigest()}
        record["review_deferral"] = {"independent_review": review_ref, "authorization": authorization_ref}
        return review_path, authorization_path

    def audit_assessment(self, candidate_root, run_id):
        issue = self.open_audit_finding(run_id)
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_audit_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        return issue, payload, assessment

    def parser_ledger_open_finding(self, run_id):
        self.pipeline.begin(run_id, "midnight", initial_inbox=[])
        legacy = self.audit_payload()
        legacy.pop("rule_parser_version")
        legacy.pop("comparison_scope")
        legacy_report = self.pipeline.audit(run_id, legacy, [], False)["snapshot"]["path"]
        issue = next(issue for issue in self.pipeline.status()["issues"].values()
                     if issue.get("kind") == "audit")
        self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                    "resolution": "The prior audit report justified this historical finding.",
                                    "evidence": [str(self.report)]})
        parsed = self.audit_payload(False)
        parsed.update(rule_parser_version=2, parser_reparse=True, comparison_scope="full",
                      legacy_parse_changes=[{"path": str(self.target), "line": 3,
                                             "text": "Require a verified current revision before publication.",
                                             "current_text": "Require a verified current revision before publication. Retain proof."}])
        self.pipeline.audit(run_id, parsed, [], True)
        state = self.pipeline.status()
        ledger = state["issues"]["audit-parser-v2-reconciliation"]
        issue_report = ledger["parser_reconciliation_report"]["path"]
        self.pipeline.update_issue({"id": issue["id"], "status": "investigate",
                                    "description": "Reviewed the current parser context; further semantic review remains.",
                                    "parser_reviewed_version": 2,
                                    "evidence": [legacy_report, str(self.report), issue_report]})
        self.pipeline.update_issue({"id": ledger["id"], "status": "resolved",
                                    "resolution": "Every affected legacy finding was reviewed under parser v2.",
                                    "evidence": [str(self.report)]})
        self.pipeline.finish(run_id, "completed", "Verified", str(self.report), [])
        final = self.pipeline.status()
        return final["issues"][issue["id"]], final["issues"][ledger["id"]]

    def parser_ledger_scoped_assessment(self, run_id, classification="defer", attach_receipt=True,
                                        mutate_issue=None):
        issue, ledger = self.parser_ledger_open_finding(run_id)
        if mutate_issue is not None:
            with self.pipeline.lock():
                state = self.pipeline.load()
                mutate_issue(state["issues"][issue["id"]])
                self.pipeline.save(state)
            issue = self.pipeline.status()["issues"][issue["id"]]
        candidate_root, _changelog, _candidate_file = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root, classification=classification)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        review_path = authorization_path = None
        if attach_receipt and classification == "defer":
            review_path, authorization_path = self.attach_audit_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        return issue, ledger, candidate_root, payload, assessment, review_path, authorization_path

    def resolved_audit_cohort(self, run_id):
        other = self.directory / "other.md"
        self.target.write_text("# Rules\n\n- Keep the shared rule.\n- Retain the same evidence.\n", encoding="utf-8")
        other.write_text("# Other\n\n- Keep the shared rule.\n- Retain the same evidence.\n", encoding="utf-8")
        refs = [self.ref(self.target), self.ref(other)]
        groups = [[{"path": ref["path"], "line": line, "text": text} for ref in refs]
                  for line, text in ((3, "Keep the shared rule."), (4, "Retain the same evidence."))]
        payload = {"rule_parser_version": 2, "comparison_scope": "full", "file_snapshots": refs,
                   "exact_duplicates": groups, "near_duplicates": [], "potential_conflicts": []}
        self.pipeline.begin(run_id, "noon", initial_inbox=[])
        self.pipeline.audit(run_id, payload, [], True)
        issue = next(issue for issue in self.pipeline.status()["issues"].values()
                     if issue.get("cohort_version") == 1)
        self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                    "resolution": "The paired audit findings were reviewed together.",
                                    "evidence": [str(self.report), issue["latest_audit"]["path"]]})
        self.pipeline.finish(run_id, "completed", "Verified", str(self.report), [])
        return issue, other

    def open_release_review_issue(self, run_id, issue_id):
        self.pipeline.begin(run_id, "noon", initial_inbox=[])
        self.pipeline.audit(run_id, self.audit_payload(False), [], True)
        self.pipeline.finish(run_id, "completed", "Verified", str(self.report), [])
        issue = {"id": issue_id, "kind": "review", "status": "investigate",
                 "title": "Review external package compatibility",
                 "description": "Review an independent package contract and its current consumers.",
                 "question": "Which compatibility policy should be documented?", "options": [],
                 "sources": [{**self.ref(self.note), "note_id": "note-1"}],
                 "targets": [self.ref(self.target)], "evidence": [str(self.report)]}
        self.pipeline.update_issue(issue)
        return self.pipeline.status()["issues"][issue_id]

    def authorize_post_release(self, issue_id, candidate_root, version="1.4.0"):
        from learning_pipeline import candidate_release_identity
        path = self.root / "reports" / (issue_id + "-authorization.json")
        path.write_text(json.dumps({"schema_version": 1, "decision": "approved",
                                    "verification_kind": "published-release-readback",
                                    "issue_id": issue_id, "release_version": version,
                                    "candidate_git": candidate_release_identity(candidate_root),
                                    "thread_id": ORDINARY, "recorded_at": "2026-11-15T12:00:00+01:00",
                                    "user_request_reference": "fixture user authorization"}))
        return str(path)

    def goal_receipt(self, run_id, initial_fingerprint, status="active"):
        path = self.root / "reports" / f"goal-{run_id}-{status}.json"
        run = self.pipeline.status()["runs"].get(run_id, {})
        marker = run.get("goal_attempt", {}).get("recorded_at")
        minimum = int(datetime.fromisoformat(marker).timestamp()) + 1 if marker else 1
        created_at = self.goal_created_times.setdefault(run_id, max(int(time.time()), minimum))
        path.write_text(json.dumps({"goal": {"threadId": ORDINARY, "status": status, "createdAt": created_at,
                                            "objective": f"Agent Learning baseline {run_id} {initial_fingerprint}"}}))
        return str(path)

    def bind_goal(self, run_id, initial_fingerprint):
        return self.pipeline.bind_goal(run_id, initial_fingerprint, ORDINARY,
                                       self.goal_receipt(run_id, initial_fingerprint))

    def decision(self):
        return {"id": "decision-1", "kind": "review", "status": "needs-user",
                "title": "Choose the retention policy", "description": "The source data can be retained or discarded after verification.",
                "question": "Which retention policy should the agent apply?",
                "sources": [{**self.ref(self.note), "note_id": "note-1"}],
                "targets": [self.ref(self.target)], "options": [
                    {"id": "A", "label": "Retain", "description": "Keep the source for later reconciliation.", "recommended": True},
                    {"id": "B", "label": "Discard", "description": "Remove it after successful verification."}]}

    def notes(self):
        return self.app.pending_review_notes(self.root)

    def recovery_issue(self, run):
        from learning_pipeline import fingerprint
        self.pipeline.update_issue({"id": "run-blocker", "kind": "operational", "status": "resolved",
                                    "title": "Original blocker", "description": "The original run stopped.",
                                    "recovery_run_id": run["id"], "recovery_run_fingerprint": fingerprint(run),
                                    "resolution": "The follow-up addressed the blocker.", "evidence": [str(self.report)]})
        return ["run-blocker"]

    def audit_payload(self, duplicate=True):
        ref = self.ref(self.target)
        members = [{"path": ref["path"], "line": 3, "text": "Require a verified current revision before publication."},
                   {"path": ref["path"], "line": 5, "text": "Require a verified current revision before publication."}]
        return {"rule_parser_version": self.app.RULE_PARSER_VERSION, "comparison_scope": "full",
                "file_snapshots": [{**ref, "words": 10, "lines": 3}],
                "exact_duplicates": [members] if duplicate else [], "near_duplicates": [], "potential_conflicts": []}

    def test_incremental_absence_does_not_close_a_finding_and_context_drift_reopens_it(self):
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        self.pipeline.audit("run-1", self.audit_payload(), [str(self.target)], True)
        issue = next(iter(self.pipeline.status()["issues"].values()))
        self.assertEqual(issue["discovered_run"], "run-1")
        self.pipeline.update_issue({"id": issue["id"], "status": "justified", "resolution": "Both independent entry points load this rule.",
                                    "evidence": [str(self.report)]})
        self.pipeline.audit("run-1", self.audit_payload(False), [str(self.target)], False)
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "justified")
        self.target.write_text(self.target.read_text() + "\n## Additional context\n")
        self.pipeline.audit("run-1", self.audit_payload(), [str(self.target)], False)
        saved = self.pipeline.status()["issues"][issue["id"]]
        self.assertEqual(saved["status"], "justified")
        self.assertTrue(saved["revalidation_required"])
        self.assertEqual(saved["resolution"], "Both independent entry points load this rule.")

    def test_cohort_review_checks_saved_preimages_and_original_membership(self):
        from learning_pipeline import current_audit_sources
        other = self.directory / "other.md"
        other.write_text("# Other\n\n- Retain the shared rule.\n- Keep all evidence.\n")
        self.target.write_text("# Rules\n\n- Retain the shared rule.\n- Keep all evidence.\n")
        refs = [self.ref(self.target), self.ref(other)]
        groups = [[{"path": ref["path"], "line": line, "text": text} for ref in refs]
                  for line, text in ((3, "Retain the shared rule."), (4, "Keep all evidence."))]
        payload = {"rule_parser_version": 2, "comparison_scope": "full", "file_snapshots": refs,
                   "exact_duplicates": groups, "near_duplicates": [], "potential_conflicts": []}
        self.pipeline.begin("cohort-proposal", "midnight", [], [], [])
        report = self.pipeline.audit("cohort-proposal", payload, [], False)["snapshot"]
        state = self.pipeline.status()
        issue = next(issue for issue in state["issues"].values() if issue.get("cohort_version") == 1)
        sources = [{"id": issue["id"], "report": report}]
        preimages = {}
        for index, ref in enumerate(refs):
            saved = self.directory / f"preimage-{index}.md"
            saved.write_bytes(Path(ref["path"]).read_bytes())
            preimages[ref["path"]] = self.ref(saved)
        self.target.write_text("# Rules\n\n- The proposed edited rule.\n")
        self.assertTrue(current_audit_sources(self.root, sources, state, preimages))
        state["issues"][issue["id"]]["member_hash"] = "0" * 64
        self.assertFalse(current_audit_sources(self.root, sources, state, preimages))
        state = self.pipeline.status()
        Path(preimages[refs[0]["path"]]["path"]).write_text("Changed preimage.\n")
        self.assertFalse(current_audit_sources(self.root, sources, state, preimages))
        with patch("learning_pipeline.subprocess.run", side_effect=subprocess.TimeoutExpired("cohorts", 1)) as worker:
            self.assertFalse(current_audit_sources(self.root, sources * 2, state, preimages, timeout=1))
            self.assertEqual(worker.call_count, 1)

    def test_parser_reparse_preserves_legacy_decisions_and_records_affected_findings(self):
        other = self.directory / "other.md"
        other.write_text("# Other instruction\n")
        self.pipeline.begin("parser-migration", "midnight", initial_inbox=[])
        self.pipeline.audit("parser-migration", self.audit_payload(), [], False)
        original = next(iter(self.pipeline.status()["issues"].values()))
        self.pipeline.update_issue({"id": original["id"], "status": "justified",
                                    "description": "Lexical discovery requiring semantic assessment: the original text was summarized.",
                                    "resolution": "Prior review used the legacy discovery.",
                                    "evidence": [str(self.report)]})
        payload = self.audit_payload(False)
        payload["file_snapshots"].append(self.ref(other))
        payload.update(rule_parser_version=2, parser_reparse=True, comparison_scope="full",
                       legacy_parse_changes=[{"path": str(self.target), "line": 3,
                                              "text": "Require a verified current revision before publication.",
                                              "current_text": "Require a verified current revision before publication. Retain proof."}])
        result = self.pipeline.audit("parser-migration", payload, [], True)
        state = self.pipeline.status()
        self.assertEqual(state["issues"][original["id"]]["status"], "justified")
        migration = state["issues"]["audit-parser-v2-reconciliation"]
        self.assertEqual(migration["status"], "investigate")
        self.assertEqual(migration["affected_issue_ids"], [original["id"]])
        self.assertEqual(migration["affected_target_paths"][original["id"]], [str(self.target.resolve())])
        report = json.loads(Path(result["snapshot"]["path"]).read_text())
        self.assertEqual(report["parser_reconciliation"]["affected_issue_ids"], [original["id"]])
        self.assertEqual(report["parser_reconciliation"]["affected_target_paths"], migration["affected_target_paths"])
        with self.assertRaisesRegex(ValueError, "Review every affected legacy finding"):
            self.pipeline.update_issue({"id": migration["id"], "status": "resolved",
                                        "resolution": "Attempted early closure.", "evidence": [str(self.report)]})
        with self.assertRaisesRegex(ValueError, "explicit version"):
            self.pipeline.update_issue({"id": original["id"], "status": "justified",
                                        "parser_reviewed_version": 2.0})
        with self.assertRaisesRegex(ValueError, "retain the historical target paths"):
            self.pipeline.update_issue({"id": original["id"], "status": "justified", "targets": [],
                                        "parser_reviewed_version": 2, "resolution": "Omitted target.",
                                        "evidence": [str(self.report), migration["parser_reconciliation_report"]["path"]]})
        self.pipeline.update_issue({"id": original["id"], "status": "justified", "targets": [self.ref(other)],
                                    "resolution": "Attempted target substitution.", "evidence": [str(self.report)]})
        with self.assertRaisesRegex(ValueError, "current reconciliation report"):
            self.pipeline.update_issue({"id": original["id"], "status": "justified", "targets": [self.ref(other)],
                                        "parser_reviewed_version": 2, "resolution": "Attempted substitution.",
                                        "evidence": [str(self.report), migration["parser_reconciliation_report"]["path"]]})
        self.pipeline.update_issue({"id": original["id"], "status": "justified", "targets": [self.ref(self.target)],
                                    "resolution": "Restored historical target.", "evidence": [str(self.report)]})
        self.pipeline.update_issue({"id": original["id"], "status": "investigate",
                                    "parser_reviewed_version": 2,
                                    "description": "Reviewed parser context; underlying issue still needs investigation.",
                                    "evidence": [str(self.report), migration["parser_reconciliation_report"]["path"]]})
        before = self.target.read_text()
        self.target.write_text(before + "Changed after review.\n")
        with self.assertRaisesRegex(ValueError, "evidence changed or is unavailable"):
            self.pipeline.update_issue({"id": migration["id"], "status": "resolved",
                                        "resolution": "Reviewed legacy findings.", "evidence": [str(self.report)]})
        self.target.write_text(before)
        self.pipeline.update_issue({"id": migration["id"], "status": "resolved",
                                    "resolution": "Reviewed legacy findings.", "evidence": [str(self.report)]})
        self.assertEqual(self.pipeline.status()["issues"][migration["id"]]["status"], "resolved")
        self.assertEqual(self.pipeline.status()["issues"][original["id"]]["status"], "investigate")
        self.pipeline.update_issue({"id": original["id"], "status": "justified",
                                    "resolution": "The separate investigation is now resolved.",
                                    "evidence": [str(self.report)]})
        self.pipeline.audit("parser-migration", self.audit_payload(False), [], False)
        self.pipeline.finish("parser-migration", "completed", "Verified", str(self.report), [str(self.target)])
        self.assertEqual(self.pipeline.weekly_release_status()["consolidation"]["id"], "parser-migration")
        migration_report = Path(migration["parser_reconciliation_report"]["path"])
        migration_report.write_text(migration_report.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "Parser reconciliation evidence changed"):
            self.pipeline.weekly_release_status()

    def test_parser_migration_retains_historical_targets_after_retargeting(self):
        other = self.directory / "other.md"
        other.write_text("# Other instruction\n")
        self.pipeline.begin("parser-retarget", "midnight", initial_inbox=[])
        self.pipeline.audit("parser-retarget", self.audit_payload(), [], False)
        original = next(iter(self.pipeline.status()["issues"].values()))
        self.pipeline.update_issue({"id": original["id"], "status": "justified",
                                    "targets": [self.ref(other)], "description": "Historical finding was retargeted.",
                                    "resolution": "Previous review.", "evidence": [str(self.report)]})
        payload = self.audit_payload(False)
        payload["file_snapshots"].append(self.ref(other))
        payload.update(rule_parser_version=2, parser_reparse=True, comparison_scope="full",
                       legacy_parse_changes=[{"path": str(self.target), "line": 3,
                                              "text": "Require a verified current revision before publication.",
                                              "current_text": "Require a verified current revision before publication. Retain proof."}])
        self.pipeline.audit("parser-retarget", payload, [], True)
        migration = self.pipeline.status()["issues"]["audit-parser-v2-reconciliation"]
        self.assertEqual(set(migration["affected_target_paths"][original["id"]]),
                         {str(self.target.resolve()), str(other.resolve())})
        third = self.directory / "third.md"
        third.write_text("# New target\n")
        self.pipeline.update_issue({"id": original["id"], "status": "investigate", "targets": [self.ref(third)],
                                    "evidence": [str(self.report)]})
        payload["file_snapshots"].append(self.ref(third))
        self.pipeline.audit("parser-retarget", payload, [], False)
        migration = self.pipeline.status()["issues"]["audit-parser-v2-reconciliation"]
        self.assertEqual(set(migration["affected_target_paths"][original["id"]]),
                         {str(self.target.resolve()), str(other.resolve()), str(third.resolve())})
        refs = [self.ref(self.target), self.ref(other), self.ref(third)]
        self.pipeline.update_issue({"id": original["id"], "status": "investigate", "targets": refs,
                                    "evidence": [str(self.report)]})
        self.pipeline.update_issue({"id": original["id"], "status": "investigate", "targets": refs,
                                    "parser_reviewed_version": 2,
                                    "evidence": [str(self.report), migration["parser_reconciliation_report"]["path"]]})
        self.pipeline.update_issue({"id": migration["id"], "status": "resolved",
                                    "resolution": "Reviewed historical and current targets.",
                                    "evidence": [str(self.report)]})
        self.assertEqual(self.pipeline.status()["issues"][original["id"]]["status"], "investigate")

    def test_nonweekly_run_reparses_legacy_state_even_when_files_are_unchanged(self):
        self.target.write_text("# Rules\n\n- Retain the verified evidence\n  with the release.\n")
        self.pipeline.begin("parser-upgrade", "noon", [self.ref(self.target)], [])
        legacy = self.audit_payload()
        legacy.pop("rule_parser_version")
        legacy.pop("comparison_scope")
        legacy["exact_duplicates"][0][0]["text"] = "Retain the verified evidence"
        legacy["exact_duplicates"][0][1]["text"] = "Retain the verified evidence"
        self.pipeline.audit("parser-upgrade", legacy, [], False)
        old = next(iter(self.pipeline.status()["issues"].values()))
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit",
                                                  "--run-id", "parser-upgrade"])
        with patch.object(self.app, "pipeline_roots", return_value=[str(self.target)]), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline_audit(args)
        result = json.loads(output.getvalue())
        audit = json.loads(Path(result["snapshot"]["path"]).read_text())["audit"]
        self.assertEqual(audit["changed_files"], [])
        self.assertTrue(audit["parser_reparse"])
        migration = self.pipeline.status()["issues"]["audit-parser-v2-reconciliation"]
        self.assertIn(old["id"], migration["affected_issue_ids"])
        self.assertEqual(migration["last_assessed_run"], "parser-upgrade")

    def test_parser_migration_excludes_only_id_verified_unchanged_findings(self):
        self.pipeline.begin("parser-selective", "noon", [self.ref(self.target)], [])
        legacy = self.audit_payload()
        legacy.pop("rule_parser_version")
        legacy.pop("comparison_scope")
        unchanged = copy.deepcopy(legacy["exact_duplicates"][0])
        for item in unchanged:
            item["text"] = "A separate unchanged rule remains independently actionable."
        legacy["exact_duplicates"].append(unchanged)
        self.pipeline.audit("parser-selective", legacy, [], False)
        issues = self.pipeline.status()["issues"]
        changed_id = next(key for key, value in issues.items() if "Require a verified" in value["description"])
        unchanged_id = next(key for key, value in issues.items() if "separate unchanged" in value["description"])
        payload = self.audit_payload(False)
        payload.update(rule_parser_version=2, parser_reparse=True, comparison_scope="full",
                       legacy_parse_changes=[{"path": str(self.target), "line": 3,
                                              "text": "Require a verified current revision before publication.",
                                              "current_text": "A complete wrapped replacement."}])
        self.pipeline.audit("parser-selective", payload, [], True)
        affected = self.pipeline.status()["issues"]["audit-parser-v2-reconciliation"]["affected_issue_ids"]
        self.assertEqual(affected, [changed_id])
        self.assertNotIn(unchanged_id, affected)

    def test_forced_full_audit_keeps_real_changed_file_accounting(self):
        other = self.directory / "copy.md"
        other.write_text(self.target.read_text())
        self.pipeline.begin("forced-full", "noon", [self.ref(self.target), self.ref(other)], [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit",
                                                  "--run-id", "forced-full", "--full"])
        with patch.object(self.app, "pipeline_roots", return_value=[str(self.target), str(other)]), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline_audit(args)
        result = json.loads(output.getvalue())
        audit = json.loads(Path(result["snapshot"]["path"]).read_text())["audit"]
        self.assertEqual(audit["changed_files"], [])
        self.assertEqual(audit["comparison_scope"], "full")
        self.assertFalse(audit["parser_reparse"])
        self.assertEqual(len(audit["exact_duplicates"]), 1)

    def test_parser_reconciliation_requires_latest_report_to_cover_reviewed_target(self):
        self.pipeline.begin("parser-refresh", "noon", [self.ref(self.target)], [])
        self.pipeline.audit("parser-refresh", self.audit_payload(), [], False)
        original = next(iter(self.pipeline.status()["issues"].values()))
        payload = self.audit_payload(False)
        payload.update(rule_parser_version=2, parser_reparse=True, comparison_scope="full",
                       legacy_parse_changes=[{"path": str(self.target), "line": 3,
                                              "text": "Require a verified current revision before publication.",
                                              "current_text": "Different complete rule."}])
        self.pipeline.audit("parser-refresh", payload, [], True)
        migration = self.pipeline.status()["issues"]["audit-parser-v2-reconciliation"]
        self.target.write_text("# Instructions\n\n- Preserve the complete current contract.\n")
        with self.assertRaisesRegex(ValueError, "current reconciliation report"):
            self.pipeline.update_issue({"id": original["id"], "status": "resolved", "targets": [self.ref(self.target)],
                                        "parser_reviewed_version": 2, "resolution": "Reviewed current text.",
                                        "evidence": [str(self.report), migration["parser_reconciliation_report"]["path"]]})
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit",
                                                  "--run-id", "parser-refresh", "--reconcile-parser"])
        with patch.object(self.app, "pipeline_roots", return_value=[str(self.target)]), redirect_stdout(io.StringIO()):
            self.app.command_pipeline_audit(args)
        refreshed = self.pipeline.status()["issues"][migration["id"]]
        self.assertEqual(refreshed["affected_issue_ids"], [original["id"]])
        self.pipeline.update_issue({"id": original["id"], "status": "resolved", "targets": [self.ref(self.target)],
                                    "parser_reviewed_version": 2, "resolution": "Reviewed current text.",
                                    "evidence": [str(self.report), refreshed["parser_reconciliation_report"]["path"]]})
        self.pipeline.update_issue({"id": migration["id"], "status": "resolved",
                                    "resolution": "Reviewed all findings.", "evidence": [str(self.report)]})

    def test_audit_groups_repeated_rules_by_file_pair_and_revalidates_changes(self):
        import learning_pipeline
        from learning_pipeline import current_cohort

        other = self.directory / "copy.md"
        content = "# Rules\n\n- Verify the current revision before publication.\n- Retain the current review evidence.\n"
        self.target.write_text(content)
        other.write_text(content)

        def payload():
            refs = [self.ref(self.target), self.ref(other)]
            findings = [[{"path": ref["path"], "line": line, "text": text} for ref in refs]
                        for line, text in ((3, "Verify the current revision before publication."),
                                           (4, "Retain the current review evidence."))]
            return {"file_snapshots": refs, "exact_duplicates": findings,
                    "near_duplicates": [], "potential_conflicts": []}

        self.pipeline.begin("cohort-run", "midnight", initial_inbox=[])
        self.pipeline.audit("cohort-run", payload(), [], True)
        issues = self.pipeline.status()["issues"]
        self.assertEqual(len(issues), 1)
        issue = next(iter(issues.values()))
        self.assertEqual(issue["status"], "candidate")
        self.assertIn("2 repeated instructions", issue["description"])
        audit = json.loads(Path(issue["latest_audit"]["path"]).read_text())["audit"]
        self.assertEqual(len(audit["exact_duplicates"]), 2)
        incomplete = {key: value for key, value in issue.items() if key != "member_hash"}
        self.assertFalse(current_cohort(incomplete))
        incomplete.pop("cohort_version")
        self.assertFalse(current_cohort(incomplete))
        for invalid_version in (True, 1.0):
            self.assertFalse(current_cohort({**issue, "cohort_version": invalid_version}))
        original_check = learning_pipeline._current_cohort
        def change_after_check(item, cache):
            result = original_check(item, cache)
            other.write_text(content + "changed after the first read")
            return result
        with patch.object(learning_pipeline, "_current_cohort", side_effect=change_after_check):
            self.assertFalse(learning_pipeline._cohorts_current_in_worker([issue]))
        other.write_text(content)
        with patch("learning_pipeline.subprocess.run", side_effect=subprocess.TimeoutExpired("cohort", 0.01)):
            self.assertFalse(current_cohort(issue))
        forged_report = self.root / "reports" / "forged-cohort.json"
        audit["file_snapshots"][0]["sha256"] = "0" * 64
        forged_report.write_text(json.dumps({"audit": audit}))
        mismatched = {**issue, "latest_audit": self.ref(forged_report)}
        self.assertFalse(current_cohort(mismatched))
        self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                    "resolution": "Both rules were reviewed in both files.",
                                    "evidence": [str(self.report), issue["latest_audit"]["path"]]})
        other.write_text(content + "\nAdditional context.\n")
        self.assertFalse(current_cohort(self.pipeline.status()["issues"][issue["id"]]))
        self.pipeline.audit("cohort-run", payload(), [], False)
        saved = self.pipeline.status()["issues"][issue["id"]]
        self.assertEqual(saved["status"], "justified")
        self.assertTrue(saved["revalidation_required"])
        self.assertNotEqual(saved["latest_audit"], issue["latest_audit"])
        with self.assertRaisesRegex(ValueError, "current audit report"):
            self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                        "resolution": "Reviewed the old report.", "evidence": [str(self.report)]})
        latest = Path(saved["latest_audit"]["path"])
        latest.write_text(latest.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "Cohort audit or membership changed"):
            self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                        "resolution": "Reviewed both rules.", "evidence": [str(latest)]})
        with self.assertRaisesRegex(ValueError, "cohort audit evidence changed"):
            self.pipeline.finish("cohort-run", "completed", "Reviewed", str(self.report),
                                 [str(self.target), str(other)])

    def test_audit_does_not_absorb_multi_occurrence_or_cross_file_findings(self):
        other = self.directory / "copy.md"
        third = self.directory / "third.md"
        other.write_text(self.target.read_text())
        third.write_text(self.target.read_text())
        refs = [self.ref(path) for path in (self.target, other, third)]
        def member(ref, line, text):
            return {"path": ref["path"], "line": line, "text": text}
        pair = [member(ref, 3, "Require a verified current revision before publication.") for ref in refs[:2]]
        triple = [member(ref, 3, "Another shared instruction requires separate assessment.") for ref in refs]
        within = [member(refs[0], line, "A duplicate within one file needs separate review.") for line in (5, 6)]
        payload = {"file_snapshots": refs, "exact_duplicates": [pair, triple, within],
                   "near_duplicates": [], "potential_conflicts": []}
        self.pipeline.begin("distinct-run", "midnight", initial_inbox=[])
        self.pipeline.audit("distinct-run", payload, [], True)
        issues = self.pipeline.status()["issues"].values()
        self.assertEqual(len(issues), 3)
        self.assertTrue(all(issue["title"] == "Repeated instruction" for issue in issues))

    def test_real_audit_output_produces_one_reviewable_file_pair_cohort(self):
        other = self.directory / "copy.md"
        content = ("# Rules\n\n- Verify the exact release revision before publication.\n"
                   "- Retain the independently reviewed evidence for later inspection.\n")
        self.target.write_text(content)
        other.write_text(content)
        args = self.app.build_parser().parse_args(["audit-rules", "--path", str(self.target),
                                                   "--path", str(other)])
        capture = io.StringIO()
        with redirect_stdout(capture):
            self.app.command_audit_rules(args)
        payload = json.loads(capture.getvalue())
        self.assertEqual(len(payload["exact_duplicates"]), 2)
        self.pipeline.begin("real-audit", "midnight", initial_inbox=[])
        self.pipeline.audit("real-audit", payload, [], True)
        issues = self.pipeline.status()["issues"]
        self.assertEqual(len(issues), 1)
        issue = next(iter(issues.values()))
        self.assertEqual(issue["status"], "candidate")
        self.assertIn("2 repeated instructions", issue["description"])

    def test_cohort_preserves_legacy_decision_and_survives_membership_changes(self):
        from learning_pipeline import current_cohort

        other = self.directory / "copy.md"
        other.write_text(self.target.read_text())
        texts = ["First shared rule needs assessment.", "Second shared rule needs assessment.",
                 "Third shared rule needs assessment."]

        def payload(selected, *, scope="changed-targets", changed=()):
            refs = [self.ref(self.target), self.ref(other)]
            return {"file_snapshots": refs, "changed_files": list(changed), "comparison_scope": scope,
                    "exact_duplicates": [[{"path": ref["path"], "line": i + 3, "text": texts[i]}
                                          for ref in refs] for i in selected],
                    "near_duplicates": [], "potential_conflicts": []}

        self.pipeline.begin("migration-run", "midnight", initial_inbox=[])
        self.pipeline.audit("migration-run", payload([0]), [], True)
        original = next(iter(self.pipeline.status()["issues"].values()))
        proposal = self.decision()
        proposal.update(id=original["id"], kind="audit", sources=[])
        self.pipeline.update_issue(proposal)
        other.write_text(other.read_text() + "\nChanged context.\n")
        self.pipeline.audit("migration-run", payload([0, 1, 2], changed=[str(other)]), [], False)
        issues = self.pipeline.status()["issues"]
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[original["id"]]["status"], "needs-user")
        self.assertEqual(issues[original["id"]]["options"], proposal["options"])
        cohort = next(issue for issue in issues.values() if issue.get("cohort_version") == 1)
        self.assertIn("2 repeated instructions", cohort["description"])
        self.assertEqual(cohort["excluded_member_ids"], [original["id"]])
        other.write_text(other.read_text() + "\nMore context.\n")
        self.pipeline.audit("migration-run", payload([0, 1], changed=[str(other)]), [], False)
        issues = self.pipeline.status()["issues"]
        self.assertEqual(len(issues), 2)
        reduced = issues[cohort["id"]]
        self.assertIn("1 repeated instructions", reduced["description"])
        self.assertTrue(reduced["revalidation_required"])
        with self.pipeline.lock():
            state = self.pipeline.load()
            del state["issues"][cohort["id"]]["cohort_version"]
            self.pipeline.save(state)
        self.assertFalse(current_cohort(self.pipeline.status()["issues"][cohort["id"]]))
        self.pipeline.audit("migration-run", payload([0, 1], changed=[str(other)]), [], False)
        reduced = self.pipeline.status()["issues"][cohort["id"]]
        self.assertEqual(reduced["cohort_version"], 1)
        self.assertTrue(current_cohort(reduced))
        for invalid_version in (True, 1.0):
            with self.pipeline.lock():
                state = self.pipeline.load()
                state["issues"][cohort["id"]]["cohort_version"] = invalid_version
                state["issues"][cohort["id"]]["revalidation_required"] = False
                self.pipeline.save(state)
            self.pipeline.audit("migration-run", payload([0, 1], changed=[str(other)]), [], False)
            reduced = self.pipeline.status()["issues"][cohort["id"]]
            self.assertIs(type(reduced["cohort_version"]), int)
            self.assertTrue(reduced["revalidation_required"])
        previous_report = reduced["latest_audit"]
        self.pipeline.audit("migration-run", payload([]), [], False)
        self.assertEqual(self.pipeline.status()["issues"][cohort["id"]]["latest_audit"], previous_report)
        self.pipeline.audit("migration-run", payload([], scope="full"), [], False)
        empty = self.pipeline.status()["issues"][cohort["id"]]
        self.assertIn("0 repeated instructions", empty["description"])
        self.assertTrue(current_cohort(empty))

    def test_third_file_delta_requires_complete_cohort_audit(self):
        other = self.directory / "copy.md"
        third = self.directory / "third.md"
        other.write_text(self.target.read_text())
        third.write_text(self.target.read_text())
        refs = [self.ref(path) for path in (self.target, other, third)]
        texts = ("Verify the current revision before publication.", "Retain the current review evidence.")
        findings = [[{"path": ref["path"], "line": i + 3, "text": text} for ref in refs[:2]]
                    for i, text in enumerate(texts)]
        base = {"file_snapshots": refs, "near_duplicates": [], "potential_conflicts": []}
        self.pipeline.begin("third-run", "midnight", initial_inbox=[])
        self.pipeline.audit("third-run", {**base, "exact_duplicates": findings}, [], True)
        issue = next(iter(self.pipeline.status()["issues"].values()))
        self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                    "resolution": "Both rules were reviewed.",
                                    "evidence": [issue["latest_audit"]["path"]]})
        triple = [*findings[0], {"path": refs[2]["path"], "line": 3, "text": texts[0]}]
        self.pipeline.audit("third-run", {**base, "exact_duplicates": [triple],
                            "comparison_scope": "changed-targets", "changed_files": [str(third)]}, [], False)
        stale = self.pipeline.status()["issues"][issue["id"]]
        self.assertTrue(stale["coverage_incomplete"])
        with self.assertRaisesRegex(ValueError, "rerun the audit"):
            self.pipeline.update_issue({"id": issue["id"], "status": "justified",
                                        "resolution": "Old evidence is insufficient.",
                                        "evidence": [issue["latest_audit"]["path"]]})

    def test_completion_cannot_omit_a_changed_target(self):
        self.pipeline.begin("run-1", "midnight", [self.ref(self.target)], [])
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.target.write_text(self.target.read_text() + "\n- Preserve confirmed user decisions.\n")
        with self.assertRaisesRegex(ValueError, "omitted"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [])
        self.pipeline.audit("run-1", self.audit_payload(False), [], False)
        self.pipeline.finish("run-1", "completed", "Verified", str(self.report), [str(self.target)])

    def test_completion_requires_the_initial_inbox_to_be_accounted_for(self):
        inbox = self.root / "inbox" / "source.md"
        inbox.write_text('---\nid: "source-1"\nstatus: "inbox"\n---\n# Source\n')
        with patch.object(self.app, "pipeline_inventory", return_value=[]):
            args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-begin", "--run-id", "inbox-run", "--runner", "noon", "--goal-mode", "manual"])
            with redirect_stdout(io.StringIO()):
                self.app.command_pipeline(args)
            self.pipeline.audit("inbox-run", self.audit_payload(False), [], True)
            with self.assertRaisesRegex(ValueError, "initial inbox"):
                self.pipeline.finish("inbox-run", "completed", "Done", str(self.report), [])
            args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "inbox-run", "--file", str(inbox), "--status", "processed", "--rationale", "Already covered by the verified instruction."])
            with redirect_stdout(io.StringIO()):
                self.app.command_finalize_note(args)
            self.pipeline.finish("inbox-run", "completed", "Verified", str(self.report), [])

    def test_unrelated_pending_review_does_not_prevent_completed_run(self):
        self.assertTrue(self.note.is_file())
        self.pipeline.begin("independent-run", "noon", [], [], [])
        self.pipeline.audit("independent-run", self.audit_payload(False), [], True)
        result = self.pipeline.finish("independent-run", "completed", "Inbox assessed", str(self.report), [])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(self.note.is_file())

    def test_partial_finish_reports_unassessed_initial_sources_and_allows_retry(self):
        first = self.root / "inbox" / "first.md"
        second = self.root / "inbox" / "second.md"
        first.write_text('---\nid: "first"\nstatus: "inbox"\n---\n# First\n')
        second.write_text('---\nid: "second"\nstatus: "inbox"\n---\n# Second\n')
        initial = [{"id": name, **self.ref(path)} for name, path in (("first", first), ("second", second))]
        self.pipeline.begin("partial-inbox", "noon", [], initial, [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "partial-inbox", "--file", str(first),
                                                  "--status", "needs-review", "--rationale", "Investigate the saved evidence."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        result = self.pipeline.finish("partial-inbox", "partial", "More work remains", "", [])
        self.assertTrue(result["initial_assessment"]["known"])
        self.assertEqual([item["id"] for item in result["initial_assessment"]["unassessed"]], ["second"])
        self.assertEqual(self.pipeline.begin("retry-inbox", "noon", [], [initial[1]], [])["status"], "running")

    def test_note_only_blocker_requires_known_unassessed_sources(self):
        self.pipeline.begin("no-note-blocker", "noon", [], [], [], goal_mode="scheduled")
        with self.assertRaisesRegex(ValueError, "note-only blocker"):
            self.pipeline.finish("no-note-blocker", "partial", "Other failure", "", [], blocker_scope="notes")
        self.assertEqual(self.pipeline.status()["runs"]["no-note-blocker"]["status"], "running")

    def test_partial_finish_preserves_unknown_initial_assessment_when_index_is_corrupt(self):
        note = self.root / "inbox" / "pending.md"
        note.write_text('---\nid: "pending"\nstatus: "inbox"\n---\n# Pending\n')
        initial = [{"id": "pending", **self.ref(note)}]
        self.pipeline.begin("partial-corrupt", "noon", [], initial, [])
        (self.root / "state" / "processed.json").write_text("invalid JSON")
        result = self.pipeline.finish("partial-corrupt", "failed", "Index unavailable", "", [])
        self.assertFalse(result["initial_assessment"]["known"])
        self.assertEqual(result["initial_assessment"]["unassessed"], initial)
        self.assertEqual(self.pipeline.begin("retry-corrupt", "noon")["status"], "running")

    def test_partial_assessment_rejects_wrong_destination_and_wrong_note_identity(self):
        for index, wrong_destination in enumerate((True, False)):
            with self.subTest(wrong_destination=wrong_destination):
                note_id = f"source-{index}"
                source = self.root / "inbox" / f"{note_id}.md"
                source.write_text(f'---\nid: "{note_id}"\nstatus: "inbox"\n---\n# Source\n')
                initial = [{"id": note_id, **self.ref(source)}]
                run_id = f"wrong-target-{index}"
                self.pipeline.begin(run_id, "noon", [], initial, [])
                source.unlink()
                target = (self.root / "reports" / "unrelated.md" if wrong_destination
                          else self.root / "processed" / "other.md")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('---\nid: "different"\nstatus: "processed"\n---\n# Other\n')
                state = self.app.load_state(self.root)
                state.setdefault("processed", {})[note_id] = {"path": str(target), "status": "processed",
                                                              "processed_at": self.app.now_iso(),
                                                              "source_path": str(source.resolve()),
                                                              "source_sha256": initial[0]["sha256"],
                                                              "source_lineage": [{"path": str(source.resolve()), "sha256": initial[0]["sha256"]}],
                                                              "archive_sha256": self.ref(target)["sha256"]}
                self.app.save_state(self.root, state)
                result = self.pipeline.finish(run_id, "partial", "Inspect mismatch", "", [])
                self.assertEqual([item["id"] for item in result["initial_assessment"]["unassessed"]], [note_id])

    def test_partial_assessment_bounds_slow_archive_reads(self):
        source = self.root / "inbox" / "slow.md"
        source.write_text('---\nid: "slow"\nstatus: "inbox"\n---\n# Slow\n')
        initial = [{"id": "slow", **self.ref(source)}]
        self.pipeline.begin("slow-assessment", "noon", [], initial, [])
        source.unlink()
        target = self.root / "processed" / "slow.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('---\nid: "slow"\nstatus: "processed"\n---\n# Slow\n')
        state = self.app.load_state(self.root)
        state.setdefault("processed", {})["slow"] = {"path": str(target), "status": "processed",
                                                      "processed_at": self.app.now_iso(),
                                                      "source_path": str(source.resolve()),
                                                      "source_sha256": initial[0]["sha256"],
                                                      "source_lineage": [{"path": str(source.resolve()), "sha256": initial[0]["sha256"]}],
                                                      "archive_sha256": self.ref(target)["sha256"]}
        self.app.save_state(self.root, state)
        released = threading.Event()
        try:
            with patch("learning_pipeline.INITIAL_ASSESSMENT_TIMEOUT", 0.02), \
                    patch.object(self.pipeline, "read_finalized", side_effect=lambda _: released.wait(1)):
                started = time.monotonic()
                result = self.pipeline.finish("slow-assessment", "partial", "Archive unreadable", "", [])
                self.assertLess(time.monotonic() - started, 0.5)
        finally:
            released.set()
        self.assertEqual([item["id"] for item in result["initial_assessment"]["unassessed"]], ["slow"])

    def test_bounded_review_inventory_reports_slow_sources_without_waiting_for_all(self):
        notes = []
        for index in range(12):
            note = self.root / "needs-review" / f"slow-{index}.md"
            note.write_text(f'---\nid: "slow-{index}"\nstatus: "needs-review"\n---\n# Slow\n')
            notes.append(note)
        released = threading.Event()
        try:
            with patch.object(self.app, "read_note_bounded", side_effect=lambda _: released.wait(1)):
                started = time.monotonic()
                found, unavailable = self.app.bounded_note_inventory(self.root, notes, timeout=0.02)
                self.assertLess(time.monotonic() - started, 0.5)
        finally:
            released.set()
        self.assertEqual(found, [])
        self.assertEqual(len(unavailable), len(notes))

    def test_goal_status_rechecks_original_snapshot_after_a_later_run(self):
        first = self.root / "inbox" / "first.md"
        second = self.root / "inbox" / "second.md"
        first.write_text('---\nid: "first"\nstatus: "inbox"\n---\n# First\n')
        second.write_text('---\nid: "second"\nstatus: "inbox"\n---\n# Second\n')
        initial = [{"id": name, **self.ref(path)} for name, path in (("first", first), ("second", second))]
        baseline = self.pipeline.begin("goal-baseline", "noon", [], initial, [])
        self.bind_goal("goal-baseline", baseline["initial_fingerprint"])
        self.pipeline.finish("goal-baseline", "partial", "Continue", "", [])
        self.pipeline.begin("goal-retry", "noon", [], initial, [])
        for note, status in ((first, "processed"), (second, "needs-review")):
            args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "goal-retry", "--file", str(note),
                                                      "--status", status, "--rationale", "Reviewed source."])
            with redirect_stdout(io.StringIO()):
                self.app.command_finalize_note(args)
        self.pipeline.finish("goal-retry", "partial", "Receipts pending", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "goal-baseline", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        status = json.loads(out.getvalue())
        self.assertTrue(status["accounting_complete"])
        self.assertEqual(status["receipts"][0]["status"], "pending")
        self.assertEqual(status["needs_review"][0]["note_id"], "second")
        args.initial_fingerprint = "incorrect"
        with self.assertRaisesRegex(self.app.ConfigError, "fingerprint"):
            self.app.command_pipeline(args)

    def test_goal_status_does_not_account_for_a_changed_initial_source(self):
        source = self.root / "inbox" / "changed.md"
        source.write_text('---\nid: "changed"\nstatus: "inbox"\n---\n# Original\n')
        initial = [{"id": "changed", **self.ref(source)}]
        baseline = self.pipeline.begin("changed-goal", "noon", [], initial, [])
        self.bind_goal("changed-goal", baseline["initial_fingerprint"])
        source.write_text('---\nid: "changed"\nstatus: "inbox"\n---\n# Changed\n')
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "changed-goal", "--file", str(source),
                                                  "--status", "needs-review", "--rationale", "Reviewed changed source."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "changed-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        status = json.loads(out.getvalue())
        self.assertFalse(status["accounting_complete"])
        self.assertEqual([item["id"] for item in status["runs"][0]["assessment"]["unassessed"]], ["changed"])
        self.pipeline.audit("changed-goal", self.audit_payload(False), [], True)
        with self.assertRaisesRegex(ValueError, "matching verified source"):
            self.pipeline.finish("changed-goal", "completed", "Done", str(self.report), [])

    def test_goal_status_includes_new_notes_captured_by_a_continuation_run(self):
        first = self.root / "inbox" / "first.md"
        first.write_text('---\nid: "first"\nstatus: "inbox"\n---\n# First\n')
        initial = [{"id": "first", **self.ref(first)}]
        baseline = self.pipeline.begin("first-goal", "noon", [], initial, [])
        self.bind_goal("first-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "first-goal", "--file", str(first),
                                                  "--status", "needs-review", "--rationale", "Reviewed."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        self.pipeline.finish("first-goal", "partial", "Continue", "", [])
        second = self.root / "inbox" / "second.md"
        second.write_text('---\nid: "second"\nstatus: "inbox"\n---\n# Second\n')
        self.pipeline.begin("later-goal", "noon", [], [{"id": "second", **self.ref(second)}], [])
        self.pipeline.finish("later-goal", "partial", "Unassessed note", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "first-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        status = json.loads(out.getvalue())
        self.assertFalse(status["accounting_complete"])
        self.assertEqual(status["runs"][0]["assessment"]["unassessed"], [])
        self.assertEqual(status["runs"][1]["assessment"]["unassessed"][0]["id"], "second")

    def test_goal_advance_allows_bounded_review_evidence_revalidation(self):
        baseline = self.pipeline.begin("budget-goal", "noon", [], [], [])
        self.bind_goal("budget-goal", baseline["initial_fingerprint"])
        self.pipeline.finish("budget-goal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-goal-status", "--run-id", "budget-goal",
            "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
            "--goal-receipt", self.goal_receipt("budget-goal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]

        def verify(_pipeline, _state, _cycle, deadline):
            self.assertGreater(deadline - time.monotonic(), 20)
            return []

        with patch.object(self.pipeline, "verify_sealed", side_effect=verify):
            result = self.pipeline.advance_goal("budget-goal", baseline["initial_fingerprint"], sealed,
                                                self.goal_receipt("budget-goal", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(result["status"], "closed")

    def test_sealed_goal_cycle_excludes_a_later_unrelated_run(self):
        first = self.root / "inbox" / "first.md"
        first.write_text('---\nid: "first"\nstatus: "inbox"\n---\n# First\n')
        initial = [{"id": "first", **self.ref(first)}]
        baseline = self.pipeline.begin("sealed-goal", "noon", [], initial, [])
        self.bind_goal("sealed-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "sealed-goal", "--file", str(first),
                                                  "--status", "rejected", "--rationale", "The finding is not reusable."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        self.pipeline.finish("sealed-goal", "partial", "Review recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "sealed-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal"])
        args.goal_receipt = self.goal_receipt("sealed-goal", baseline["initial_fingerprint"])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        sealed = json.loads(out.getvalue())
        self.assertTrue(sealed["sealed"])
        second = self.root / "inbox" / "second.md"
        second.write_text('---\nid: "second"\nstatus: "inbox"\n---\n# Second\n')
        with self.assertRaisesRegex(ValueError, "sealed Goal"):
            self.pipeline.begin("unrelated-run", "noon", [], [{"id": "second", **self.ref(second)}], [])
        with self.assertRaisesRegex(ValueError, "Native Goal evidence"):
            self.pipeline.advance_goal("sealed-goal", baseline["initial_fingerprint"], sealed["sealed_at"],
                                       self.goal_receipt("sealed-goal", baseline["initial_fingerprint"], "active"))
        absent = self.root / "reports" / "goal-after-completion-get.json"
        absent.write_text(json.dumps({"goal": None, "remainingTokens": None, "completionBudgetReport": None}))
        with self.assertRaisesRegex(ValueError, "Native Goal evidence"):
            self.pipeline.advance_goal("sealed-goal", baseline["initial_fingerprint"], sealed["sealed_at"], str(absent))
        receipt = self.goal_receipt("sealed-goal", baseline["initial_fingerprint"], "complete")
        returned = json.loads(Path(receipt).read_text())
        returned.update(remainingTokens=None, completionBudgetReport="Goal achieved.")
        Path(receipt).write_text(json.dumps(returned))
        closed = self.pipeline.advance_goal("sealed-goal", baseline["initial_fingerprint"], sealed["sealed_at"], receipt)
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(self.pipeline.advance_goal("sealed-goal", baseline["initial_fingerprint"], sealed["sealed_at"], receipt)["status"], "closed")
        later = self.pipeline.begin("unrelated-run", "noon", [], [{"id": "second", **self.ref(second)}], [])
        self.assertNotIn("goal_baseline", later)
        args.seal = False
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        result = json.loads(out.getvalue())
        self.assertTrue(result["accounting_complete"])
        self.assertEqual([run["run_id"] for run in result["runs"]], ["sealed-goal"])

    def test_goal_cycle_cannot_seal_while_running_or_receipt_is_pending(self):
        source = self.root / "inbox" / "pending.md"
        source.write_text('---\nid: "pending"\nstatus: "inbox"\n---\n# Pending\n')
        baseline = self.pipeline.begin("pending-goal", "noon", [], [{"id": "pending", **self.ref(source)}], [])
        self.bind_goal("pending-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "pending-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal"])
        with self.assertRaisesRegex(self.app.ConfigError, "Goal cycle still"):
            self.app.command_pipeline(args)
        finalize = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "pending-goal", "--file", str(source),
                                                      "--status", "processed", "--rationale", "Reviewed."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize)
        self.pipeline.finish("pending-goal", "partial", "Receipt pending", "", [])
        with self.assertRaisesRegex(self.app.ConfigError, "Goal cycle still"):
            self.app.command_pipeline(args)
        self.assertEqual(self.pipeline.status()["goal_cycle"]["status"], "active")

    def test_post_seal_drift_creates_issue_and_allows_next_cycle(self):
        source = self.root / "inbox" / "advance-source.md"
        source.write_text('---\nid: "advance-source"\nstatus: "inbox"\n---\n# Source\n')
        baseline = self.pipeline.begin("advance-goal", "noon", [], [{"id": "advance-source", **self.ref(source)}], [])
        self.bind_goal("advance-goal", baseline["initial_fingerprint"])
        finalize = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "advance-goal", "--file", str(source),
                                                      "--status", "rejected", "--rationale", "Not reusable."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize)
        self.pipeline.finish("advance-goal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "advance-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("advance-goal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        archived = Path(self.app.load_state(self.root)["processed"]["advance-source"]["path"])
        archived.write_text(archived.read_text() + "\nChanged after sealing.\n")
        receipt = self.goal_receipt("advance-goal", baseline["initial_fingerprint"], "complete")
        saved = self.pipeline.load()
        with patch.object(self.pipeline, "load", side_effect=[saved, ValueError("readback unavailable")]):
            with self.assertRaisesRegex(ValueError, "readback unavailable"):
                self.pipeline.advance_goal("advance-goal", baseline["initial_fingerprint"], sealed, receipt)
        result = self.pipeline.advance_goal("advance-goal", baseline["initial_fingerprint"], sealed, receipt)
        self.assertEqual(result["status"], "needs-reconciliation")
        issue = self.pipeline.status()["issues"][result["reconciliation_issues"][0]]
        self.assertEqual(issue["drifts"][0]["id"], "advance-source")
        self.assertNotEqual(issue["drifts"][0]["expected_sha256"], issue["drifts"][0]["observed_sha256"])
        digest = self.pipeline.digest(self.pipeline.status(), [])
        self.assertIn(issue["id"], {item["id"] for item in digest["items"]})
        with self.assertRaisesRegex(ValueError, "Resolve Goal reconciliation"):
            self.pipeline.weekly_release_status()
        issue_id = result["reconciliation_issues"][0]
        self.pipeline.update_issue({"id": issue_id, "status": "resolved", "resolution": "Evidence reconciled.", "evidence": [str(self.report)]})
        self.pipeline.begin("release-proof", "noon", initial_inbox=[])
        self.pipeline.audit("release-proof", self.audit_payload(False), [], True)
        self.pipeline.finish("release-proof", "completed", "Verified", str(self.report), [])
        original_report = self.report.read_text()
        self.report.write_text(original_report + "\nChanged.\n")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.pipeline.weekly_release_status()
        self.report.write_text(original_report)
        self.pipeline.update_issue({"id": issue_id, "status": "investigate"})
        with self.pipeline.lock():
            state = self.pipeline.load()
            del state["issues"][result["reconciliation_issues"][0]]
            self.pipeline.save(state)
        with self.assertRaisesRegex(ValueError, "reconciliation issue is missing"):
            self.pipeline.begin("missing-ledger", "noon", [], [], [])
        self.assertEqual(self.pipeline.advance_goal("advance-goal", baseline["initial_fingerprint"], sealed, receipt), result)
        self.assertIn(result["reconciliation_issues"][0], self.pipeline.status()["issues"])
        new_cycle = self.pipeline.begin("new-cycle", "noon", [], [], [])
        self.bind_goal("new-cycle", new_cycle["initial_fingerprint"])
        self.assertEqual(self.pipeline.advance_goal("advance-goal", baseline["initial_fingerprint"], sealed, receipt), result)
        self.assertEqual(self.pipeline.status()["goal_cycle"]["run_id"], "new-cycle")

    def test_unavailable_sealed_evidence_is_retryable_without_a_drift_issue(self):
        source = self.root / "inbox" / "offline-source.md"
        source.write_text('---\nid: "offline-source"\nstatus: "inbox"\n---\n# Source\n')
        baseline = self.pipeline.begin("offline-goal", "noon", [], [{"id": "offline-source", **self.ref(source)}], [])
        self.bind_goal("offline-goal", baseline["initial_fingerprint"])
        finalize = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "offline-goal", "--file", str(source),
                                                      "--status", "rejected", "--rationale", "Not reusable."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize)
        self.pipeline.finish("offline-goal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "offline-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("offline-goal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        archived = Path(self.app.load_state(self.root)["processed"]["offline-source"]["path"])
        original = self.app.subprocess.run
        def unavailable(command, **kwargs):
            if len(command) >= 3 and command[1] == "-c" and "from agent_learning import note_summary" in command[2]:
                if Path(json.loads(kwargs["input"])["path"]).resolve() == archived.resolve():
                    raise self.app.subprocess.TimeoutExpired(command, kwargs["timeout"])
            return original(command, **kwargs)
        with patch.object(self.app.subprocess, "run", side_effect=unavailable):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                self.pipeline.advance_goal("offline-goal", baseline["initial_fingerprint"], sealed,
                                           self.goal_receipt("offline-goal", baseline["initial_fingerprint"], "complete"))
        status = self.pipeline.status()
        self.assertEqual(status["goal_cycle"]["status"], "sealed")
        self.assertEqual(status["issues"], {})

    def test_receipt_verifier_distinguishes_missing_from_invalid(self):
        missing = self.root / "reports" / "missing.json"
        self.assertEqual(self.app.inspect_receipt_bounded({"path": str(missing), "sha256": "x"}, timeout=3)["status"], "unavailable")
        invalid = self.root / "reports" / "invalid.json"
        invalid.write_text("{}")
        self.assertEqual(self.app.inspect_receipt_bounded(self.ref(invalid), timeout=3)["status"], "invalid")
        with patch.object(self.app.subprocess, "run", return_value=SimpleNamespace(stdout="[]")):
            self.assertEqual(self.app.inspect_receipt_bounded({"path": str(invalid)}, timeout=3)["status"], "unavailable")

    def test_advance_checks_many_archives_concurrently(self):
        sources = []
        for i in range(16):
            path = self.root / "inbox" / f"batch-{i}.md"
            path.write_text(f'---\nid: "batch-{i}"\nstatus: "inbox"\n---\n# Source\n')
            sources.append({"id": f"batch-{i}", **self.ref(path)})
        baseline = self.pipeline.begin("batch-goal", "noon", [], sources, [])
        self.bind_goal("batch-goal", baseline["initial_fingerprint"])
        for source in sources:
            args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "batch-goal", "--file", source["path"],
                                                      "--status", "rejected", "--rationale", "Not reusable."])
            with redirect_stdout(io.StringIO()):
                self.app.command_finalize_note(args)
        self.pipeline.finish("batch-goal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id", "batch-goal",
                                                  "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("batch-goal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        original = self.app.subprocess.run
        import learning_pipeline
        release = threading.Event()
        started_reads = []
        def is_archive(command, kwargs):
            return (len(command) >= 3 and command[1] == "-c"
                    and "from agent_learning import note_summary" in command[2]
                    and json.loads(kwargs["input"])["kind"].startswith("archive-"))
        def stalled_archive(command, **kwargs):
            if is_archive(command, kwargs):
                started_reads.append(command[0])
                release.wait(2)
            return original(command, **kwargs)
        started = time.monotonic()
        try:
            with patch.object(learning_pipeline, "GOAL_ADVANCE_TIMEOUT", 0.8), \
                 patch.object(self.app.subprocess, "run", side_effect=stalled_archive):
                with self.assertRaisesRegex(ValueError, "timed out"):
                    self.pipeline.advance_goal("batch-goal", baseline["initial_fingerprint"], sealed,
                                               self.goal_receipt("batch-goal", baseline["initial_fingerprint"], "complete"))
            self.assertLess(time.monotonic() - started, 1.5)
            self.assertGreater(len(started_reads), 0)
            self.assertLessEqual(len(started_reads), 8)
            self.assertEqual(self.pipeline.status()["goal_cycle"]["status"], "sealed")
            self.assertEqual(self.pipeline.status()["issues"], {})
        finally:
            release.set()
        def slow_archive(command, **kwargs):
            if is_archive(command, kwargs):
                time.sleep(0.15)
            return original(command, **kwargs)
        started = time.monotonic()
        with patch.object(self.app.subprocess, "run", side_effect=slow_archive):
            outcome = self.pipeline.advance_goal("batch-goal", baseline["initial_fingerprint"], sealed,
                                                 self.goal_receipt("batch-goal", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(outcome["status"], "closed")
        self.assertLess(time.monotonic() - started, 2)

    def test_new_unrelated_review_after_seal_does_not_block_advance(self):
        baseline = self.pipeline.begin("review-change-goal", "noon", [], [], [])
        self.bind_goal("review-change-goal", baseline["initial_fingerprint"])
        self.pipeline.finish("review-change-goal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "review-change-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("review-change-goal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        (self.root / "needs-review" / "new.md").write_text('---\nid: "new-review"\nstatus: "needs-review"\n---\n# New\n')
        result = self.pipeline.advance_goal("review-change-goal", baseline["initial_fingerprint"], sealed,
                                            self.goal_receipt("review-change-goal", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(result["status"], "closed")

    def test_legacy_seal_without_snapshot_is_preserved_as_reconciliation(self):
        baseline = self.pipeline.begin("legacy-seal", "noon", [], [], [])
        self.bind_goal("legacy-seal", baseline["initial_fingerprint"])
        self.pipeline.finish("legacy-seal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "legacy-seal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("legacy-seal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        with self.pipeline.lock():
            state = self.pipeline.load()
            sealed = state["goal_cycle"]["sealed_at"]
            state["goal_cycle"].pop("sealed_snapshot")
            self.pipeline.save(state)
        outcome = self.pipeline.advance_goal("legacy-seal", baseline["initial_fingerprint"], sealed,
                                             self.goal_receipt("legacy-seal", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(outcome["status"], "needs-reconciliation")
        issue = self.pipeline.status()["issues"][outcome["reconciliation_issues"][0]]
        self.assertEqual(issue["drifts"][0]["kind"], "legacy-seal")
        self.pipeline.begin("after-legacy", "noon", [], [], [])

    def test_changed_sealed_run_record_is_not_silently_accepted(self):
        baseline = self.pipeline.begin("changed-sealed-run", "noon", [], [], [])
        self.bind_goal("changed-sealed-run", baseline["initial_fingerprint"])
        self.pipeline.finish("changed-sealed-run", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "changed-sealed-run", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("changed-sealed-run", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        with self.pipeline.lock():
            state = self.pipeline.load()
            sealed = state["goal_cycle"]["sealed_at"]
            state["runs"]["changed-sealed-run"]["summary"] = "Changed after sealing"
            self.pipeline.save(state)
        outcome = self.pipeline.advance_goal("changed-sealed-run", baseline["initial_fingerprint"], sealed,
                                             self.goal_receipt("changed-sealed-run", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(outcome["status"], "needs-reconciliation")
        issue = self.pipeline.status()["issues"][outcome["reconciliation_issues"][0]]
        self.assertEqual(issue["drifts"][0]["kind"], "run-record")

    def test_sealed_archive_symlink_cannot_trigger_outside_store_read(self):
        source = self.root / "inbox" / "link-source.md"
        source.write_text('---\nid: "link-source"\nstatus: "inbox"\n---\n# Source\n')
        baseline = self.pipeline.begin("link-goal", "noon", [], [{"id": "link-source", **self.ref(source)}], [])
        self.bind_goal("link-goal", baseline["initial_fingerprint"])
        finalize = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "link-goal", "--file", str(source),
                                                      "--status", "rejected", "--rationale", "Not reusable."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize)
        self.pipeline.finish("link-goal", "partial", "Recorded", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "link-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
                                                  "--goal-receipt", self.goal_receipt("link-goal", baseline["initial_fingerprint"])])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        archived = Path(self.app.load_state(self.root)["processed"]["link-source"]["path"])
        external = self.directory / "outside.fifo"
        os.mkfifo(external)
        archived.unlink()
        archived.symlink_to(external)
        result = self.pipeline.advance_goal("link-goal", baseline["initial_fingerprint"], sealed,
                                            self.goal_receipt("link-goal", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(result["status"], "needs-reconciliation")
        issue = self.pipeline.status()["issues"][result["reconciliation_issues"][0]]
        self.assertIn("symbolic link", issue["drifts"][0]["reason"])

    def test_sealed_evidence_rejects_an_escaped_store_directory(self):
        import learning_pipeline
        external = self.directory / "external-store"
        external.mkdir()
        payload = b"identical outside evidence"
        (external / "source.md").write_bytes(payload)
        (self.root / "processed").symlink_to(external, target_is_directory=True)
        ref = {"path": str(self.root / "processed" / "source.md"), "sha256": hashlib.sha256(payload).hexdigest()}
        current, drift, _ = learning_pipeline.check_sealed_file(self.root, "source", "archive-processed", ref, 2)
        self.assertFalse(current)
        self.assertIn("symbolic link", drift["reason"])

    def test_sealed_archive_cannot_be_redirected_to_another_store_directory(self):
        import learning_pipeline
        payload = b"identical internal evidence"
        review = self.root / "needs-review" / "source.md"
        review.write_bytes(payload)
        (self.root / "processed").symlink_to(self.root / "needs-review", target_is_directory=True)
        ref = {"path": str(self.root / "processed" / "source.md"), "sha256": hashlib.sha256(payload).hexdigest()}
        current, drift, _ = learning_pipeline.check_sealed_file(self.root, "source", "archive-processed", ref, 2)
        self.assertFalse(current)
        self.assertIn("symbolic link", drift["reason"])

    def test_sealed_archive_cannot_be_replaced_by_identical_sibling_symlink(self):
        import learning_pipeline
        processed = self.root / "processed"
        processed.mkdir()
        payload = b"identical internal evidence"
        (processed / "other.md").write_bytes(payload)
        (processed / "source.md").symlink_to(processed / "other.md")
        ref = {"path": str(processed / "source.md"), "sha256": hashlib.sha256(payload).hexdigest()}
        current, drift, _ = learning_pipeline.check_sealed_file(self.root, "source", "archive-processed", ref, 2)
        self.assertFalse(current)
        self.assertIn("symbolic link", drift["reason"])

    def test_goal_cycle_cannot_seal_pending_review_without_residual_evidence(self):
        source = self.root / "inbox" / "review.md"
        source.write_text('---\nid: "review"\nstatus: "inbox"\n---\n# Review\n')
        baseline = self.pipeline.begin("review-pending-goal", "noon", [], [{"id": "review", **self.ref(source)}], [])
        self.bind_goal("review-pending-goal", baseline["initial_fingerprint"])
        finalize = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "review-pending-goal", "--file", str(source),
                                                      "--status", "needs-review", "--rationale", "Further review."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize)
        self.pipeline.finish("review-pending-goal", "partial", "Review pending", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "review-pending-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal"])
        args.goal_receipt = self.goal_receipt("review-pending-goal", baseline["initial_fingerprint"])
        with self.assertRaisesRegex(self.app.ConfigError, "review requires"):
            self.app.command_pipeline(args)
        self.assertEqual(self.pipeline.status()["goal_cycle"]["status"], "active")
        note = next(item for item in self.app.pending_review_notes(self.root) if item["id"] == "review")
        receipt = self.root / "reports" / "residual.json"
        receipt.write_text(json.dumps({"version": 1, "scope": "instructions-only",
                                       "sources": [{"id": "review", "path": note["path"], "sha256": note["sha256"]}],
                                       "decisions": [{"note_id": "review", "classification": "needs-user",
                                                      "rationale": "An explicit choice is missing.",
                                                      "open_choices": ["Which alternative should be used?"]}, None]}))
        args.review_evidence = str(receipt)
        with self.assertRaisesRegex(self.app.ConfigError, "source-linked issue"):
            self.app.command_pipeline(args)
        self.pipeline.update_issue({"id": "review-choice", "kind": "review", "status": "investigate",
                                    "title": "Investigate", "description": "Still under investigation.",
                                    "sources": [{"note_id": "review", **self.ref(Path(note["path"]))}]})
        with self.assertRaisesRegex(self.app.ConfigError, "source-linked issue"):
            self.app.command_pipeline(args)
        self.pipeline.update_issue({"id": "review-choice", "kind": "review", "status": "needs-user",
                                    "title": "Choose an option", "description": "A choice is required.",
                                    "question": "Which option?", "options": [
                                        {"id": "a", "label": "A", "description": "First option."},
                                        {"id": "b", "label": "B", "description": "Second option."}],
                                    "sources": [{"note_id": "review", **self.ref(Path(note["path"]))}]})
        original_note = Path(note["path"]).read_text()
        validate = self.app.validate_residual_review
        def mutate_after_validation(*values, **options):
            result = validate(*values, **options)
            Path(note["path"]).write_text(original_note + "\nConcurrent change.\n")
            return result
        with patch.object(self.app, "validate_residual_review", side_effect=mutate_after_validation):
            with self.assertRaisesRegex(self.app.ConfigError, "Review sources changed"):
                self.app.command_pipeline(args)
        Path(note["path"]).write_text(original_note)
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        self.assertTrue(json.loads(out.getvalue())["sealed"])
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        incomplete = copy.deepcopy(self.pipeline.load())
        incomplete["goal_cycle"]["sealed_snapshot"]["review_issues"].clear()
        with self.assertRaisesRegex(ValueError, "bindings are incomplete"):
            self.pipeline.verify_sealed(self.pipeline, incomplete, incomplete["goal_cycle"], time.monotonic() + 8)
        unknown = copy.deepcopy(self.pipeline.load())
        unknown["goal_cycle"]["sealed_snapshot"]["review_binding_version"] = 2
        with self.assertRaisesRegex(ValueError, "Unknown sealed review binding version"):
            self.pipeline.verify_sealed(self.pipeline, unknown, unknown["goal_cycle"], time.monotonic() + 8)
        downgraded = copy.deepcopy(self.pipeline.load())
        downgraded["goal_cycle"]["sealed_snapshot"].pop("review_binding_version")
        downgraded["goal_cycle"]["sealed_snapshot"].pop("review_issues")
        with self.assertRaisesRegex(ValueError, "Unknown sealed review binding version"):
            self.pipeline.verify_sealed(self.pipeline, downgraded, downgraded["goal_cycle"], time.monotonic() + 8)
        partial = copy.deepcopy(self.pipeline.load())
        partial["goal_cycle"].pop("review_binding_version")
        partial["goal_cycle"]["sealed_snapshot"].pop("review_binding_version")
        partial["goal_cycle"]["sealed_snapshot"]["review_issues"].clear()
        with self.assertRaisesRegex(ValueError, "Unknown sealed review binding version"):
            self.pipeline.verify_sealed(self.pipeline, partial, partial["goal_cycle"], time.monotonic() + 8)
        null_version = copy.deepcopy(self.pipeline.load())
        null_version["goal_cycle"]["sealed_snapshot"]["review_binding_version"] = None
        with self.assertRaisesRegex(ValueError, "Unknown sealed review binding version"):
            self.pipeline.verify_sealed(self.pipeline, null_version, null_version["goal_cycle"], time.monotonic() + 8)
        legacy = copy.deepcopy(self.pipeline.load())
        legacy["goal_cycle"].pop("review_binding_version")
        legacy["goal_cycle"]["sealed_snapshot"].pop("review_binding_version")
        legacy["goal_cycle"]["sealed_snapshot"].pop("review_issues")
        self.assertEqual([], self.pipeline.verify_sealed(self.pipeline, legacy, legacy["goal_cycle"], time.monotonic() + 8))
        changed_state = copy.deepcopy(self.pipeline.load())
        changed_state["issues"]["review-choice"]["status"] = "resolved"
        drift = self.pipeline.verify_sealed(self.pipeline, changed_state, changed_state["goal_cycle"],
                                            time.monotonic() + 8)
        self.assertTrue(any(item["id"] == "review" and item["kind"] == "review-issue" for item in drift))
        Path(note["path"]).write_text(original_note.replace("[ ] Approve", "[x] Approve"))
        outcome = self.pipeline.advance_goal("review-pending-goal", baseline["initial_fingerprint"], sealed,
                                             self.goal_receipt("review-pending-goal", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(outcome["status"], "needs-reconciliation")
        issue = self.pipeline.status()["issues"][outcome["reconciliation_issues"][0]]
        self.assertEqual(issue["drifts"][0]["id"], "review")
        self.assertIn("review", {item["kind"] for item in issue["drifts"]})

    def test_sealed_review_drift_is_attributed_only_to_affected_note(self):
        sources = []
        for note_id in ("review-one", "review-two"):
            source = self.root / "inbox" / f"{note_id}.md"
            source.write_text(f'---\nid: "{note_id}"\nstatus: "inbox"\n---\n# Review\n')
            sources.append(source)
        baseline = self.pipeline.begin("review-isolation", "noon", [],
                                       [{"id": p.stem, **self.ref(p)} for p in sources], [])
        self.bind_goal("review-isolation", baseline["initial_fingerprint"])
        for source in sources:
            args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id",
                                                      "review-isolation", "--file", str(source),
                                                      "--status", "needs-review", "--rationale", "Further review."])
            with redirect_stdout(io.StringIO()):
                self.app.command_finalize_note(args)
        self.pipeline.finish("review-isolation", "partial", "Review pending", "", [])
        notes = self.app.pending_review_notes(self.root)
        receipt = self.root / "reports" / "isolation.json"
        receipt.write_text(json.dumps({"version": 1, "scope": "instructions-only",
            "sources": [{"id": n["id"], "path": n["path"], "sha256": n["sha256"]} for n in notes],
            "decisions": [{"note_id": n["id"], "classification": "needs-user", "rationale": "Choice required.",
                           "open_choices": ["Which option?"]} for n in notes]}))
        for note in notes:
            self.pipeline.update_issue({"id": f"choice-{note['id']}", "kind": "review", "status": "needs-user",
                                        "title": "Choose an option", "description": "A choice is required.",
                                        "question": "Which option?", "options": [
                                            {"id": "a", "label": "A", "description": "First option."},
                                            {"id": "b", "label": "B", "description": "Second option."}],
                                        "sources": [{"note_id": note["id"], **self.ref(Path(note["path"]))}]})
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
            "review-isolation", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal",
            "--goal-receipt", self.goal_receipt("review-isolation", baseline["initial_fingerprint"]),
            "--review-evidence", str(receipt)])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        validate = self.app.validate_residual_review
        def one_stale(root, selected, evidence, **kwargs):
            if any(note["id"] == "review-one" for note in selected):
                raise self.app.ConfigError("Stale residual instruction target")
            return validate(root, selected, evidence, **kwargs)
        with patch.object(self.app, "validate_residual_review", side_effect=one_stale):
            result = self.pipeline.advance_goal("review-isolation", baseline["initial_fingerprint"], sealed,
                self.goal_receipt("review-isolation", baseline["initial_fingerprint"], "complete"))
        self.assertEqual(result["status"], "needs-reconciliation")
        self.assertEqual(len(result["reconciliation_issues"]), 1)
        issue = self.pipeline.status()["issues"][result["reconciliation_issues"][0]]
        self.assertEqual(issue["drifts"][0]["id"], "review-one")

    def test_sealed_cycle_can_reopen_after_verified_active_goal_needs_more_work(self):
        baseline = self.pipeline.begin("reopen-goal", "noon", [], [], [])
        self.bind_goal("reopen-goal", baseline["initial_fingerprint"])
        self.pipeline.finish("reopen-goal", "partial", "No notes", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "reopen-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal"])
        args.goal_receipt = self.goal_receipt("reopen-goal", baseline["initial_fingerprint"])
        with redirect_stdout(io.StringIO()):
            self.app.command_pipeline(args)
        sealed = self.pipeline.status()["goal_cycle"]["sealed_at"]
        receipt = self.goal_receipt("reopen-goal", baseline["initial_fingerprint"])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.pipeline.reopen_goal("reopen-goal", baseline["initial_fingerprint"], "stale", "Evidence changed.", receipt)
        cycle = self.pipeline.reopen_goal("reopen-goal", baseline["initial_fingerprint"], sealed, "Evidence changed.", receipt)
        self.assertEqual(cycle["status"], "active")
        source = self.root / "inbox" / "new.md"
        source.write_text('---\nid: "new"\nstatus: "inbox"\n---\n# New\n')
        run = self.pipeline.begin("reopened-run", "noon", [], [{"id": "new", **self.ref(source)}], [])
        self.assertEqual(run["goal_baseline"]["run_id"], "reopen-goal")

    def test_seal_requires_the_same_active_native_goal(self):
        baseline = self.pipeline.begin("identity-goal", "noon", [], [], [])
        self.bind_goal("identity-goal", baseline["initial_fingerprint"])
        self.pipeline.finish("identity-goal", "partial", "No notes", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "identity-goal", "--initial-fingerprint", baseline["initial_fingerprint"], "--seal"])
        with self.assertRaisesRegex(self.app.ConfigError, "active native Goal receipt"):
            self.app.command_pipeline(args)
        args.goal_receipt = self.goal_receipt("identity-goal", baseline["initial_fingerprint"], "complete")
        with self.assertRaisesRegex(ValueError, "Native Goal evidence"):
            self.app.command_pipeline(args)
        receipt = Path(self.goal_receipt("identity-goal", baseline["initial_fingerprint"]))
        goal = json.loads(receipt.read_text())
        goal["goal"]["createdAt"] += 1
        receipt.write_text(json.dumps(goal))
        args.goal_receipt = str(receipt)
        with self.assertRaisesRegex(self.app.ConfigError, "identity changed"):
            self.app.command_pipeline(args)
        self.assertEqual(self.pipeline.status()["goal_cycle"]["status"], "active")

    def test_unbound_running_baseline_can_be_adopted_after_owner_reconciliation(self):
        baseline = self.pipeline.begin("orphan-goal", "noon", [], [], [])
        with self.assertRaisesRegex(ValueError, "still running"):
            self.pipeline.begin("next-run", "noon", [], [], [])
        cycle = self.bind_goal("orphan-goal", baseline["initial_fingerprint"])
        self.assertEqual(cycle["run_id"], "orphan-goal")
        self.assertEqual(self.pipeline.status()["runs"]["orphan-goal"]["goal_baseline"]["initial_fingerprint"],
                         baseline["initial_fingerprint"])

    def test_goal_intent_is_persisted_before_goal_creation_and_cannot_be_rewritten(self):
        run = self.pipeline.begin("scheduled-intent", "noon", [], [], [], goal_mode="scheduled")
        self.assertEqual(run["goal_mode"], "scheduled")
        self.assertEqual(self.pipeline.status()["runs"]["scheduled-intent"]["goal_mode"], "scheduled")
        with self.assertRaisesRegex(ValueError, "different or unknown Goal intent"):
            self.pipeline.begin("scheduled-intent", "noon", [], [], [], goal_mode="manual")
        self.pipeline.finish("scheduled-intent", "partial", "Interrupted before Goal creation", "", [])
        run = self.pipeline.status()["runs"]["scheduled-intent"]
        self.assertEqual(run["goal_mode"], "scheduled")
        self.assertNotIn("goal_baseline", run)

    def test_manual_and_legacy_runs_remain_distinguishable(self):
        manual = self.pipeline.begin("manual-intent", "noon", [], [], [], goal_mode="manual")
        self.assertEqual(manual["goal_mode"], "manual")
        self.pipeline.finish("manual-intent", "partial", "Maintenance", "", [])
        legacy = self.pipeline.begin("legacy-intent", "noon", [], [], [])
        self.assertNotIn("goal_mode", legacy)
        with self.assertRaisesRegex(ValueError, "different or unknown Goal intent"):
            self.pipeline.begin("legacy-intent", "noon", [], [], [], goal_mode="scheduled")

    def test_pipeline_begin_cli_persists_scheduled_goal_intent(self):
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-begin", "--run-id", "cli-intent",
            "--runner", "noon", "--goal-mode", "scheduled",
        ])
        output = io.StringIO()
        skills = self.directory / "skills"
        skills.mkdir()
        with patch.object(self.app, "DEFAULT_AUDIT_TARGETS", [self.target]), \
                patch.object(self.app, "DEFAULT_AUDIT_SKILLS_DIR", skills), redirect_stdout(output):
            self.assertEqual(self.app.command_pipeline(args), 0)
        self.assertEqual(json.loads(output.getvalue())["goal_mode"], "scheduled")
        self.assertEqual(self.pipeline.status()["runs"]["cli-intent"]["goal_mode"], "scheduled")
        attempt = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-goal-attempt", "--run-id", "cli-intent",
            "--goal-thread-id", ORDINARY,
        ])
        with redirect_stdout(output):
            self.assertEqual(self.app.command_pipeline(attempt), 0)
        self.assertEqual(self.pipeline.status()["runs"]["cli-intent"]["goal_attempt"]["thread_id"], ORDINARY)

    def test_pipeline_begin_cli_rejects_omitted_goal_mode(self):
        with self.assertRaises(SystemExit):
            self.app.build_parser().parse_args([
                "--config", str(self.config), "pipeline-begin", "--run-id", "unclassified",
                "--runner", "noon",
            ])
        self.assertNotIn("unclassified", self.pipeline.status()["runs"])

    def test_scheduled_runner_follows_current_heartbeat_and_replay_is_unknown(self):
        now = datetime.now().astimezone()
        event = {"type": "functionCallOutput", "namespace": "codex_app", "name": "automation_update",
                 "id": "fco_dispatch_test", "output": {"truncated": False, "text": (
                     "<heartbeat>\n<automation_id>agent-learning-noon-consolidation</automation_id>\n"
                     f"<current_time_iso>{now.isoformat()}</current_time_iso>\n<instructions>Example</instructions>\n</heartbeat>")}}
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": int(now.timestamp()), "event": event}
        evidence = self.root / "reports" / "dispatch.json"
        evidence.write_text(json.dumps(capture))
        relative_evidence = os.path.relpath(evidence, Path.cwd())
        initial = [{"id": "source", "path": "/source", "sha256": "a"}]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY}):
            first = self.pipeline.begin("dispatch-one", "midnight", [], initial, [],
                                        goal_mode="scheduled", dispatch_evidence=relative_evidence)
            self.assertEqual(first["runner"], "unknown")
            self.assertEqual(first["dispatch"]["runner"], "noon")
            self.assertEqual(first["requested_runner"], "midnight")
            self.assertEqual(first["dispatch_status"], "attested")
            self.assertEqual(first["dispatch"]["receipt"]["path"], str(evidence))
            self.pipeline.finish("dispatch-one", "partial", "Retry", "", [])
            replay = self.pipeline.begin("dispatch-replay", "noon", [], initial, [],
                                         goal_mode="scheduled", dispatch_evidence=str(evidence))
        self.assertEqual(replay["runner"], "unknown")
        self.assertEqual(replay["initial_inbox"], initial)

    def test_missing_or_wrong_thread_heartbeat_never_blocks_initial_notes(self):
        initial = [{"id": "source", "path": "/source", "sha256": "a"}]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": RELEASE}):
            run = self.pipeline.begin("no-dispatch", "noon", [], initial, [],
                                      goal_mode="scheduled", dispatch_evidence=str(self.report))
        self.assertEqual(run["runner"], "unknown")
        self.assertEqual(run["dispatch_status"], "unknown")
        self.assertEqual(run["initial_inbox"], initial)

    def test_deeply_nested_dispatch_capture_does_not_block_notes(self):
        evidence = self.root / "reports" / "deep-dispatch.json"
        evidence.write_bytes(b"[" * 1100 + b"0" + b"]" * 1100)
        initial = [{"id": "source", "path": "/source", "sha256": "a"}]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY}):
            run = self.pipeline.begin("deep-dispatch", "noon", [], initial, [],
                                      goal_mode="scheduled", dispatch_evidence=str(evidence))
        self.assertEqual(run["runner"], "unknown")
        self.assertEqual(run["initial_inbox"], initial)

    def test_extreme_dispatch_timestamp_does_not_block_notes(self):
        now = datetime.now().astimezone()
        event = {"type": "functionCallOutput", "namespace": "codex_app", "name": "automation_update",
                 "id": "fco_extreme", "output": {"truncated": False, "text": (
                     "<heartbeat><automation_id>agent-learning-noon-consolidation</automation_id>"
                     f"<current_time_iso>{now.isoformat()}</current_time_iso>")}}
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": 10 ** 1000, "event": event}
        evidence = self.root / "reports" / "extreme-dispatch.json"
        evidence.write_text(json.dumps(capture))
        initial = [{"id": "source", "path": "/source", "sha256": "a"}]
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY}):
            run = self.pipeline.begin("extreme-dispatch", "noon", [], initial, [],
                                      goal_mode="scheduled", dispatch_evidence=str(evidence))
        self.assertEqual(run["runner"], "unknown")
        self.assertEqual(run["initial_inbox"], initial)

    def test_heartbeat_capture_rejects_stale_truncated_and_wrong_turn(self):
        from learning_pipeline import captured_heartbeat
        now = datetime.now().astimezone()
        event = {"type": "functionCallOutput", "namespace": "codex_app", "name": "automation_update",
                 "id": "fco_dispatch_test", "output": {"truncated": False, "text": (
                     "<heartbeat>\n<automation_id>agent-learning-noon-consolidation</automation_id>\n"
                     f"<current_time_iso>{now.isoformat()}</current_time_iso>\n<instructions>Example</instructions>")}}
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": int(now.timestamp()), "event": event}
        cases = []
        wrong_thread = copy.deepcopy(capture)
        wrong_thread["thread"] = RELEASE
        cases.append(wrong_thread)
        truncated = copy.deepcopy(capture)
        truncated["event"]["output"]["truncated"] = True
        cases.append(truncated)
        inactive = copy.deepcopy(capture)
        inactive["status"] = "completed"
        cases.append(inactive)
        wrong_turn = copy.deepcopy(capture)
        wrong_turn["turn"] = "not-a-turn"
        cases.append(wrong_turn)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    captured_heartbeat(json.dumps(value).encode(), ORDINARY, now)
        with self.assertRaisesRegex(ValueError, "Stale"):
            captured_heartbeat(json.dumps(capture).encode(), ORDINARY, now + timedelta(hours=1))

    def test_unconsumed_prior_turn_attestation_does_not_set_runner(self):
        now = datetime.now().astimezone() - timedelta(minutes=1)
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": int(now.timestamp()),
                   "event": {"type": "functionCallOutput", "namespace": "codex_app",
                             "name": "automation_update", "id": "fco_prior_unused",
                             "output": {"truncated": False, "text": (
                                 "<heartbeat><automation_id>agent-learning-noon-consolidation</automation_id>"
                                 f"<current_time_iso>{now.isoformat()}</current_time_iso>")}}}
        evidence = self.root / "reports" / "prior-unused-heartbeat.json"
        evidence.write_text(json.dumps(capture))
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY}):
            run = self.pipeline.begin("later-unrelated", "midnight", [], [], [],
                                      goal_mode="scheduled", dispatch_evidence=str(evidence))
        self.assertEqual(run["runner"], "unknown")
        self.assertEqual(run["dispatch_status"], "attested")
        self.assertEqual(run["dispatch"]["runner"], "noon")

    def test_dispatch_bind_correlates_heartbeat_with_exact_start_command(self):
        now = datetime.now().astimezone()
        start_id = "exec-11111111-1111-1111-1111-111111111111"
        event = {"type": "functionCallOutput", "namespace": "codex_app", "name": "automation_update",
                 "id": "fco_current", "output": {"truncated": False, "text": (
                     "<heartbeat><automation_id>agent-learning-noon-consolidation</automation_id>"
                     f"<current_time_iso>{now.isoformat()}</current_time_iso>")}}
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": int(now.timestamp()), "event": event,
                   "command": {"type": "commandExecution", "id": start_id, "status": "completed", "exitCode": 0}}
        evidence = self.root / "reports" / "correlated-dispatch.json"
        evidence.write_text(json.dumps(capture))
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY, "CODEX_TOOL_CALL_ID": start_id}):
            run = self.pipeline.begin("correlated-run", "midnight", [], [], [], goal_mode="scheduled")
            self.assertEqual(run["runner"], "unknown")
            bound = self.pipeline.bind_dispatch("correlated-run", str(evidence))
            self.assertEqual(bound["runner"], "noon")
            self.assertEqual(bound["dispatch_status"], "correlated")
            self.assertEqual(self.pipeline.status()["runs"]["correlated-run"]["runner"], "noon")
            self.assertEqual(self.pipeline.bind_dispatch("correlated-run", str(evidence)), bound)
            evidence.write_text(json.dumps(capture, indent=2))
            with self.assertRaisesRegex(ValueError, "different dispatch was already correlated"):
                self.pipeline.bind_dispatch("correlated-run", str(evidence))

    def test_dispatch_bind_rejects_old_or_failed_start_command(self):
        now = datetime.now().astimezone()
        start_id = "exec-11111111-1111-1111-1111-111111111111"
        event = {"type": "functionCallOutput", "namespace": "codex_app", "name": "automation_update",
                 "id": "fco_old", "output": {"truncated": False, "text": (
                     "<heartbeat><automation_id>agent-learning-noon-consolidation</automation_id>"
                     f"<current_time_iso>{now.isoformat()}</current_time_iso>")}}
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": int(now.timestamp()), "event": event,
                   "command": {"type": "commandExecution", "id": "exec-22222222-2222-2222-2222-222222222222",
                               "status": "completed", "exitCode": 0}}
        evidence = self.root / "reports" / "old-dispatch.json"
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY, "CODEX_TOOL_CALL_ID": start_id}):
            self.pipeline.begin("new-run", "midnight", [], [], [], goal_mode="scheduled")
            for command in (capture["command"], {"type": "commandExecution", "id": start_id,
                                                 "status": "completed", "exitCode": 1}):
                capture["command"] = command
                evidence.write_text(json.dumps(capture))
                with self.assertRaisesRegex(ValueError, "does not contain this run's completed start"):
                    self.pipeline.bind_dispatch("new-run", str(evidence))
            self.assertEqual(self.pipeline.status()["runs"]["new-run"]["runner"], "unknown")

    def test_reused_start_tool_id_cannot_identify_another_run(self):
        start_id = "exec-11111111-1111-1111-1111-111111111111"
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY, "CODEX_TOOL_CALL_ID": start_id}):
            first = self.pipeline.begin("first-start", "noon", [], [], [], goal_mode="scheduled")
            self.assertEqual(first["start_tool_call_id"], start_id)
            self.pipeline.finish("first-start", "partial", "Continue", "", [])
            second = self.pipeline.begin("second-start", "noon", [], [], [], goal_mode="scheduled")
            self.assertNotIn("start_tool_call_id", second)
            with self.assertRaisesRegex(ValueError, "no supported tool-call identity"):
                self.pipeline.bind_dispatch("second-start", str(self.report))

    def test_dispatch_bind_rejects_reused_event_or_turn(self):
        now = datetime.now().astimezone()
        first_id = "exec-11111111-1111-1111-1111-111111111111"
        second_id = "exec-22222222-2222-2222-2222-222222222222"
        capture = {"schema": "app-thread-event-v1", "thread": ORDINARY, "turn": RELEASE,
                   "status": "inProgress", "startedAt": int(now.timestamp()),
                   "event": {"type": "functionCallOutput", "namespace": "codex_app",
                             "name": "automation_update", "id": "fco_shared",
                             "output": {"truncated": False, "text": (
                                 "<heartbeat><automation_id>agent-learning-noon-consolidation</automation_id>"
                                 f"<current_time_iso>{now.isoformat()}</current_time_iso>")}},
                   "command": {"type": "commandExecution", "id": first_id, "status": "completed", "exitCode": 0}}
        evidence = self.root / "reports" / "reused-dispatch.json"
        evidence.write_text(json.dumps(capture))
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY, "CODEX_TOOL_CALL_ID": first_id}):
            self.pipeline.begin("first-dispatch", "noon", [], [], [], goal_mode="scheduled")
            self.pipeline.bind_dispatch("first-dispatch", str(evidence))
            self.pipeline.finish("first-dispatch", "partial", "Continue", "", [])
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY, "CODEX_TOOL_CALL_ID": second_id}):
            self.pipeline.begin("second-dispatch", "noon", [], [], [], goal_mode="scheduled")
            capture["command"]["id"] = second_id
            for turn, event_id in ((ORDINARY, "fco_shared"), (RELEASE, "fco_other")):
                capture["turn"] = turn
                capture["event"]["id"] = event_id
                evidence.write_text(json.dumps(capture))
                with self.assertRaisesRegex(ValueError, "already associated"):
                    self.pipeline.bind_dispatch("second-dispatch", str(evidence))
            self.assertEqual(self.pipeline.status()["runs"]["second-dispatch"]["runner"], "unknown")

    def test_dispatch_bind_reports_deeply_nested_evidence_without_changing_run(self):
        start_id = "exec-11111111-1111-1111-1111-111111111111"
        evidence = self.root / "reports" / "deep-bind.json"
        evidence.write_bytes(b"[" * 1100 + b"0" + b"]" * 1100)
        with patch.dict(os.environ, {"CODEX_THREAD_ID": ORDINARY, "CODEX_TOOL_CALL_ID": start_id}):
            self.pipeline.begin("deep-bind", "noon", [], [], [], goal_mode="scheduled")
            with self.assertRaisesRegex(ValueError, "malformed|Invalid current thread capture"):
                self.pipeline.bind_dispatch("deep-bind", str(evidence))
            self.assertEqual(self.pipeline.status()["runs"]["deep-bind"]["runner"], "unknown")

    def test_unknown_runner_digest_retains_older_failures_in_one_group(self):
        state = self.pipeline.status()
        now = datetime.now().astimezone()
        state["runs"] = {
            "older": {"id": "older", "runner": "unknown", "status": "failed",
                      "started_at": (now - timedelta(hours=2)).isoformat()},
            "newer": {"id": "newer", "runner": "unknown", "status": "completed",
                      "started_at": now.isoformat()},
            "also-partial": {"id": "also-partial", "runner": "unknown", "status": "partial",
                             "started_at": (now - timedelta(hours=1)).isoformat()},
        }
        digest = self.pipeline.digest(state, [], now=now)
        items = [item for item in digest["items"] if item["id"] == "runner-unknown"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["run_ids"], ["also-partial", "older"])
        self.assertIn("also-partial", digest["body"])
        self.assertIn("older", digest["body"])

    def test_attested_runner_digest_keeps_prior_failure_and_discloses_evidence_limit(self):
        state = self.pipeline.status()
        now = datetime.now().astimezone()
        state["runs"] = {
            "earlier": {"id": "earlier", "runner": "noon", "dispatch_status": "correlated",
                        "status": "failed", "started_at": (now - timedelta(hours=2)).isoformat()},
            "later": {"id": "later", "runner": "noon", "dispatch_status": "correlated",
                      "status": "completed", "started_at": now.isoformat()},
        }
        digest = self.pipeline.digest(state, [], now=now)
        item = next(item for item in digest["items"] if item["id"] == "runner-correlated")
        self.assertEqual(item["run_ids"], ["earlier"])
        self.assertIn("agent attestation", item["description"])
        self.assertIn("not rechecked", item["description"])
        self.assertNotIn("Consolidation noon: attention required", digest["body"])

    def test_recovery_keeps_original_status_and_reopens_alert_if_evidence_changes(self):
        from learning_pipeline import fingerprint
        self.pipeline.begin("interrupted", "noon", [], [], [], goal_mode="scheduled")
        original_report = self.root / "reports" / "interrupted.md"
        original_report.write_text("Original blocker")
        self.pipeline.finish("interrupted", "partial", "Audit was unavailable", str(original_report), [])
        self.pipeline.begin("followup", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.audit("followup", self.audit_payload(False), [], True)
        self.pipeline.finish("followup", "completed", "Audit verified", str(self.report), [])
        original = self.pipeline.status()["runs"]["interrupted"]
        blockers = self.recovery_issue(original)
        report = self.root / "reports" / "recovery.json"
        report.write_text(json.dumps({"schema_version": 1, "run_id": "interrupted", "followup_ids": ["followup"],
                                      "run_fingerprint": fingerprint(original), "resolution": "Audit completed in the follow-up.",
                                      "evidence": [self.ref(self.report)], "blocker_issue_ids": blockers}))
        bad_schema = json.loads(report.read_text())
        bad_schema["schema_version"] = True
        report.write_text(json.dumps(bad_schema))
        with self.assertRaisesRegex(ValueError, "bind the runs"):
            self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        bad_schema["schema_version"] = 1
        report.write_text(json.dumps(bad_schema))
        self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        state = self.pipeline.status()
        self.assertEqual(state["runs"]["interrupted"]["status"], "partial")
        digest = self.pipeline.digest(state, [])
        ids = {item["id"] for item in digest["items"]}
        self.assertNotIn("runner-unknown", ids)
        self.assertIn("runner-unknown-attribution", ids)
        with patch.object(self.pipeline, "verify_recovery", side_effect=RuntimeError("Unavailable")):
            unavailable = self.pipeline.digest(state, [])
        self.assertIn("runner-unknown", {item["id"] for item in unavailable["items"]})
        changed_state = copy.deepcopy(state)
        changed_state["runs"]["interrupted"]["summary"] = "A different blocker"
        changed_digest = self.pipeline.digest(changed_state, [])
        self.assertIn("runner-unknown", {item["id"] for item in changed_digest["items"]})
        original_report.write_text("Changed original blocker")
        changed_digest = self.pipeline.digest(state, [])
        self.assertIn("runner-unknown", {item["id"] for item in changed_digest["items"]})
        original_report.write_text("Original blocker")
        changed_state = copy.deepcopy(state)
        changed_state["issues"]["run-blocker"]["status"] = "investigate"
        changed_digest = self.pipeline.digest(changed_state, [])
        self.assertIn("runner-unknown", {item["id"] for item in changed_digest["items"]})
        changed_state = copy.deepcopy(state)
        changed_state["issues"]["another-blocker"] = {"id": "another-blocker", "kind": "operational",
                                                       "status": "resolved", "recovery_run_id": "interrupted"}
        changed_digest = self.pipeline.digest(changed_state, [])
        self.assertIn("runner-unknown", {item["id"] for item in changed_digest["items"]})
        self.report.write_text("Evidence changed")
        digest = self.pipeline.digest(self.pipeline.status(), [])
        self.assertIn("runner-unknown", {item["id"] for item in digest["items"]})

    def test_recovery_rejects_unrelated_or_unfinished_followup(self):
        self.pipeline.begin("interrupted", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.finish("interrupted", "failed", "Interrupted", "", [])
        self.pipeline.begin("unfinished", "noon", [], [], [], goal_mode="scheduled")
        with self.assertRaisesRegex(ValueError, "completed follow-up"):
            self.pipeline.reconcile_run("interrupted", ["unfinished"], str(self.report))
        self.assertNotIn("interrupted", self.pipeline.status().get("run_recoveries", {}))

    def test_recovery_rejects_weekly_audit_from_an_unnamed_run(self):
        self.pipeline.begin("interrupted", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.finish("interrupted", "failed", "Weekly audit failed", "", [])
        self.pipeline.begin("other", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.audit("other", self.audit_payload(False), [], True)
        self.pipeline.finish("other", "completed", "Audit done", str(self.report), [])
        self.pipeline.begin("followup", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.finish("followup", "completed", "Other follow-up", str(self.report), [])
        with self.assertRaisesRegex(ValueError, "weekly audit is not verified"):
            self.pipeline.reconcile_run("interrupted", ["followup"], str(self.report))

    def test_recovery_rejects_a_followup_that_did_not_process_original_sources(self):
        from learning_pipeline import fingerprint
        source = self.root / "inbox" / "unprocessed.md"
        source.write_text('---\nid: "unprocessed"\nstatus: "inbox"\n---\n# Pending\n')
        self.pipeline.begin("interrupted", "noon", [], [{"id": "unprocessed", **self.ref(source)}], [],
                            goal_mode="scheduled")
        self.pipeline.finish("interrupted", "partial", "Source not processed", "", [])
        self.pipeline.begin("unrelated", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.audit("unrelated", self.audit_payload(False), [], True)
        self.pipeline.finish("unrelated", "completed", "Other work", str(self.report), [])
        original = self.pipeline.status()["runs"]["interrupted"]
        blockers = self.recovery_issue(original)
        report = self.root / "reports" / "wrong-recovery.json"
        report.write_text(json.dumps({"schema_version": 1, "run_id": "interrupted", "followup_ids": ["unrelated"],
                                      "run_fingerprint": fingerprint(original), "resolution": "Claimed recovered.",
                                      "evidence": [self.ref(self.report)], "blocker_issue_ids": blockers}))
        with self.assertRaisesRegex(ValueError, "remain unassessed"):
            self.pipeline.reconcile_run("interrupted", ["unrelated"], str(report))
        self.assertNotIn("interrupted", self.pipeline.status().get("run_recoveries", {}))

    def test_recovery_accepts_a_verified_original_source_in_a_later_run(self):
        from learning_pipeline import fingerprint
        source = self.root / "inbox" / "recovered.md"
        source.write_text('---\nid: "recovered"\nstatus: "inbox"\n---\n# Source\n')
        initial = [{"id": "recovered", **self.ref(source)}]
        self.pipeline.begin("interrupted", "noon", [], initial, [], goal_mode="scheduled")
        self.pipeline.finish("interrupted", "partial", "Source pending", "", [], blocker_scope="notes")
        self.pipeline.begin("followup", "noon", [], initial, [], goal_mode="scheduled")
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note",
                                                  "--run-id", "followup", "--file", str(source),
                                                  "--status", "rejected", "--rationale", "Not reusable."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        self.pipeline.audit("followup", self.audit_payload(False), [], True)
        self.pipeline.finish("followup", "completed", "Source assessed", str(self.report), [])
        original = self.pipeline.status()["runs"]["interrupted"]
        report = self.root / "reports" / "source-recovery.json"
        report.write_text(json.dumps({"schema_version": 1, "run_id": "interrupted", "followup_ids": ["followup"],
                                      "run_fingerprint": fingerprint(original), "resolution": "Source was assessed.",
                                      "evidence": [self.ref(self.report)], "blocker_issue_ids": []}))
        mixed = self.pipeline.load()
        mixed["runs"]["interrupted"]["blocker_scope"] = "mixed"
        self.pipeline.save(mixed)
        mixed_report = json.loads(report.read_text())
        mixed_report["run_fingerprint"] = fingerprint(mixed["runs"]["interrupted"])
        report.write_text(json.dumps(mixed_report))
        with self.assertRaisesRegex(ValueError, "bind the runs"):
            self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        mixed["runs"]["interrupted"] = original
        self.pipeline.save(mixed)
        mixed_report["run_fingerprint"] = fingerprint(original)
        report.write_text(json.dumps(mixed_report))
        self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        state = self.pipeline.status()
        self.assertEqual(state["runs"]["interrupted"]["status"], "partial")
        self.assertNotIn("runner-unknown", {item["id"] for item in self.pipeline.digest(state, [])["items"]})

    def test_recovery_covers_sources_processed_in_multiple_followups(self):
        from learning_pipeline import fingerprint
        sources = []
        for name in ("first", "second"):
            source = self.root / "inbox" / f"{name}.md"
            source.write_text(f'---\nid: "{name}"\nstatus: "inbox"\n---\n# Source\n')
            sources.append((name, source, {"id": name, **self.ref(source)}))
        self.pipeline.begin("interrupted", "noon", [], [item[2] for item in sources], [], goal_mode="scheduled")
        self.pipeline.finish("interrupted", "partial", "Sources pending", "", [])
        for index, (name, source, ref) in enumerate(sources):
            followup = f"followup-{index}"
            self.pipeline.begin(followup, "noon", [], [ref], [], goal_mode="scheduled")
            args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note",
                                                      "--run-id", followup, "--file", str(source),
                                                      "--status", "rejected", "--rationale", "Not reusable."])
            with redirect_stdout(io.StringIO()):
                self.app.command_finalize_note(args)
            if index == 0:
                self.pipeline.audit(followup, self.audit_payload(False), [], True)
            self.pipeline.finish(followup, "completed", f"Assessed {name}", str(self.report), [])
        original = self.pipeline.status()["runs"]["interrupted"]
        blockers = self.recovery_issue(original)
        report = self.root / "reports" / "multi-recovery.json"
        report.write_text(json.dumps({"schema_version": 1, "run_id": "interrupted",
                                      "followup_ids": ["followup-0", "followup-1"],
                                      "run_fingerprint": fingerprint(original), "resolution": "Both sources were assessed.",
                                      "evidence": [self.ref(self.report)], "blocker_issue_ids": blockers}))
        self.pipeline.reconcile_run("interrupted", ["followup-0", "followup-1"], str(report))
        state = self.pipeline.status()
        self.assertNotIn("runner-unknown", {item["id"] for item in self.pipeline.digest(state, [])["items"]})

    def test_recovery_checks_many_integrations_with_bounded_parallelism(self):
        from learning_pipeline import fingerprint
        initial = [{"id": f"note-{number}", "path": f"/original/note-{number}", "sha256": "source"}
                   for number in range(90)]
        now = datetime.now().astimezone()
        original = {"id": "interrupted", "status": "partial", "runner": "unknown", "blocker_scope": "notes",
                    "started_at": (now - timedelta(minutes=2)).isoformat(),
                    "finished_at": (now - timedelta(minutes=1)).isoformat(), "weekly_due": False,
                    "initial_inbox": initial, "initial_unavailable": [],
                    "initial_assessment": {"known": True, "unassessed": initial, "unavailable": []},
                    "initial_fingerprint": fingerprint({"inbox": initial, "unavailable": []}),
                    "operational_blocker_ids": []}
        followup = {"id": "followup", "status": "completed", "runner": "noon",
                    "started_at": now.isoformat(), "report": self.ref(self.report)}
        state = self.pipeline.load()
        state["runs"] = {"interrupted": original, "followup": followup}
        self.pipeline.save(state)
        index = self.app.load_state(self.root)
        index["processed"] = {item["id"]: {"status": "processed", "consolidation_run": "followup",
                                           "integration_status": "verified",
                                           "integration_receipt": {"path": "/shared", "sha256": "receipt"}}
                              for item in initial}
        self.app.save_state(self.root, index)
        report = self.root / "reports" / "many-recovery.json"
        report.write_text(json.dumps({"schema_version": 1, "run_id": "interrupted", "followup_ids": ["followup"],
                                      "run_fingerprint": fingerprint(original), "resolution": "Sources assessed.",
                                      "evidence": [self.ref(self.report)], "blocker_issue_ids": []}))
        receipt_validations = []
        omit_last = [False]
        receipt_delay = [0.12]
        actual_run = subprocess.run
        def representative_worker(command, *args, **kwargs):
            if len(command) > 2 and "from learning_integration import validate_receipt" in command[2]:
                time.sleep(receipt_delay[0])
                receipt_validations.append(True)
                selected = initial[:-1] if omit_last[0] else initial
                payload = {"manifest": {"note_ids": [item["id"] for item in selected], "relocations": []},
                           "coverage": [], "relocations": {}}
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
            return actual_run(command, *args, **kwargs)
        with patch.object(self.pipeline, "initial_assessment", return_value={"known": True, "unassessed": [], "unavailable": []}), \
             patch.object(self.app.subprocess, "run", side_effect=representative_worker), \
             patch.object(self.app, "integration_problem", wraps=self.app.integration_problem) as check:
            self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        self.assertEqual(check.call_count, 180)
        self.assertEqual(len(receipt_validations), 2)
        omit_last[0] = True
        with patch.object(self.pipeline, "initial_assessment", return_value={"known": True, "unassessed": [], "unavailable": []}), \
             patch.object(self.app.subprocess, "run", side_effect=representative_worker):
            with self.assertRaisesRegex(ValueError, "does not cover this note"):
                self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        omit_last[0] = False
        receipt_delay[0] = 0.025
        receipt_validations.clear()
        for note_id, entry in index["processed"].items():
            entry["integration_receipt"] = {"path": f"/receipt/{note_id}", "sha256": "receipt"}
        self.app.save_state(self.root, index)
        with patch.object(self.pipeline, "initial_assessment", return_value={"known": True, "unassessed": [], "unavailable": []}), \
             patch.object(self.app.subprocess, "run", side_effect=representative_worker):
            self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        self.assertEqual(len(receipt_validations), 180)
        calls = 0
        guard = threading.Lock()
        def change_index_on_second_pass(*_args, **_kwargs):
            nonlocal calls
            with guard:
                calls += 1
                if calls == 91:
                    changed = copy.deepcopy(index)
                    changed["extra_observation"] = "changed"
                    self.app.save_state(self.root, changed)
            return None
        with patch.object(self.pipeline, "initial_assessment", return_value={"known": True, "unassessed": [], "unavailable": []}), \
             patch.object(self.app, "integration_problem", side_effect=change_index_on_second_pass):
            with self.assertRaisesRegex(ValueError, "Processed outcomes changed"):
                self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        self.app.save_state(self.root, index)
        calls = 0
        def change_report_on_second_pass(*_args, **_kwargs):
            nonlocal calls
            with guard:
                calls += 1
                if calls == 91:
                    self.report.write_text("Changed supporting evidence")
            return None
        with patch.object(self.pipeline, "initial_assessment", return_value={"known": True, "unassessed": [], "unavailable": []}), \
             patch.object(self.app, "integration_problem", side_effect=change_report_on_second_pass):
            with self.assertRaisesRegex(ValueError, "supporting evidence"):
                self.pipeline.reconcile_run("interrupted", ["followup"], str(report))
        self.report.write_text("# Verification\n\nThe complete instruction contract is preserved.\n")
        archive = self.root / "processed" / "archive.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("Original archived source")
        calls = 0
        def assess_archive(*_args, **_kwargs):
            unassessed = [] if archive.read_text() == "Original archived source" else [initial[0]]
            return {"known": True, "unassessed": unassessed, "unavailable": []}
        def change_archive_on_second_pass(*_args, **_kwargs):
            nonlocal calls
            with guard:
                calls += 1
                if calls == 91:
                    archive.write_text("Changed archived source")
            return None
        with patch.object(self.pipeline, "initial_assessment", side_effect=assess_archive), \
             patch.object(self.app, "integration_problem", side_effect=change_archive_on_second_pass):
            with self.assertRaisesRegex(ValueError, "Original source evidence changed"):
                self.pipeline.reconcile_run("interrupted", ["followup"], str(report))

    def test_goal_attempt_survives_partial_run_and_rejects_other_owners(self):
        self.pipeline.begin("attempt-intent", "noon", [], [], [], goal_mode="scheduled")
        first = self.pipeline.mark_goal_attempt("attempt-intent", ORDINARY)
        self.assertEqual(first["thread_id"], ORDINARY)
        self.assertEqual(self.pipeline.mark_goal_attempt("attempt-intent", ORDINARY), first)
        with self.assertRaisesRegex(ValueError, "ownership differs"):
            self.pipeline.mark_goal_attempt("attempt-intent", RELEASE)
        self.pipeline.finish("attempt-intent", "partial", "Interrupted during native Goal call", "", [])
        self.assertEqual(self.pipeline.status()["runs"]["attempt-intent"]["goal_attempt"], first)
        with self.assertRaisesRegex(ValueError, "different or unknown thread"):
            self.pipeline.bind_goal("attempt-intent", self.pipeline.status()["runs"]["attempt-intent"]["initial_fingerprint"],
                                    RELEASE, str(self.report))

    def test_goal_attempt_rejects_manual_and_unavailable_runs(self):
        self.pipeline.begin("manual-no-attempt", "noon", [], [], [], goal_mode="manual")
        with self.assertRaisesRegex(ValueError, "active scheduled"):
            self.pipeline.mark_goal_attempt("manual-no-attempt", ORDINARY)
        self.pipeline.finish("manual-no-attempt", "partial", "Done", "", [])
        self.pipeline.begin("unavailable-attempt", "noon", [], [],
                            [{"path": "/offline", "reason": "offline"}], goal_mode="scheduled")
        with self.assertRaisesRegex(ValueError, "complete initial inbox"):
            self.pipeline.mark_goal_attempt("unavailable-attempt", ORDINARY)

    def test_unbound_attempt_blocks_new_scheduled_run_but_not_manual_maintenance(self):
        baseline = self.pipeline.begin("uncertain-attempt", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("uncertain-attempt", ORDINARY)
        self.pipeline.finish("uncertain-attempt", "partial", "Native result unknown", "", [])
        with self.assertRaisesRegex(ValueError, "earlier Goal attempt is unbound"):
            self.pipeline.begin("duplicate-schedule", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.begin("diagnostic-manual", "noon", [], [], [], goal_mode="manual")
        self.pipeline.finish("diagnostic-manual", "partial", "Diagnosis", "", [])
        self.bind_goal("uncertain-attempt", baseline["initial_fingerprint"])
        continuation = self.pipeline.begin("goal-continuation", "noon", [], [], [], goal_mode="scheduled")
        self.assertEqual(continuation["goal_baseline"]["run_id"], "uncertain-attempt")

    def test_legacy_begin_cannot_bind_around_earlier_unbound_attempt(self):
        self.pipeline.begin("earlier-attempt", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("earlier-attempt", ORDINARY)
        self.pipeline.finish("earlier-attempt", "partial", "Native result unknown", "", [])
        later = self.pipeline.begin("later-legacy", "noon", [], [], [])
        self.pipeline.finish("later-legacy", "partial", "Legacy run", "", [])
        with self.assertRaisesRegex(ValueError, "Another Goal attempt is unbound"):
            self.bind_goal("later-legacy", later["initial_fingerprint"])

    def test_manual_continuation_preserves_active_goal_ownership(self):
        baseline = self.pipeline.begin("scheduled-owner", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("scheduled-owner", ORDINARY)
        self.bind_goal("scheduled-owner", baseline["initial_fingerprint"])
        self.pipeline.finish("scheduled-owner", "partial", "Continue", "", [])
        manual = self.pipeline.begin("manual-continuation", "noon", [], [], [], goal_mode="manual")
        self.assertEqual(manual["goal_mode"], "manual")
        self.assertEqual(manual["goal_baseline"], {
            "run_id": "scheduled-owner", "initial_fingerprint": baseline["initial_fingerprint"],
        })

    def test_recovery_does_not_absorb_later_unbound_manual_run(self):
        baseline = self.pipeline.begin("recover-scheduled", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("recover-scheduled", ORDINARY)
        self.pipeline.finish("recover-scheduled", "partial", "Interrupted", "", [])
        self.pipeline.begin("independent-manual", "noon", [], [],
                            [{"path": "/offline", "reason": "offline"}], goal_mode="manual")
        self.pipeline.finish("independent-manual", "partial", "Maintenance", "", [])
        self.bind_goal("recover-scheduled", baseline["initial_fingerprint"])
        self.assertNotIn("goal_baseline", self.pipeline.status()["runs"]["independent-manual"])
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-goal-status", "--run-id", "recover-scheduled",
            "--initial-fingerprint", baseline["initial_fingerprint"],
        ])
        output = io.StringIO()
        with redirect_stdout(output):
            self.app.command_pipeline(args)
        status = json.loads(output.getvalue())
        self.assertTrue(status["accounting_complete"])
        self.assertEqual(status["unknown_runs"], [])

    def test_unbound_manual_run_cannot_become_scheduled_baseline(self):
        run = self.pipeline.begin("manual-baseline", "noon", [], [], [], goal_mode="manual")
        with self.assertRaisesRegex(ValueError, "manual run cannot"):
            self.bind_goal("manual-baseline", run["initial_fingerprint"])

    def test_independent_manual_outcome_can_account_for_same_initial_source(self):
        note = self.root / "inbox" / "shared-source.md"
        note.write_text('---\nid: "shared-source"\nstatus: "inbox"\n---\n# Shared source\n')
        initial = [{"id": "shared-source", **self.ref(note)}]
        baseline = self.pipeline.begin("scheduled-shared", "noon", [], initial, [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("scheduled-shared", ORDINARY)
        self.pipeline.finish("scheduled-shared", "partial", "Interrupted", "", [])
        self.pipeline.begin("independent-shared", "noon", [], initial, [], goal_mode="manual")
        finalize = self.app.build_parser().parse_args([
            "--config", str(self.config), "finalize-note", "--run-id", "independent-shared",
            "--file", str(note), "--status", "needs-review", "--rationale", "Reviewed source.",
        ])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize)
        self.pipeline.finish("independent-shared", "partial", "Review pending", "", [])
        self.bind_goal("scheduled-shared", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-goal-status", "--run-id", "scheduled-shared",
            "--initial-fingerprint", baseline["initial_fingerprint"],
        ])
        output = io.StringIO()
        with redirect_stdout(output):
            self.app.command_pipeline(args)
        status = json.loads(output.getvalue())
        self.assertTrue(status["accounting_complete"])
        self.assertEqual([x["note_id"] for x in status["needs_review"]], ["shared-source"])
        self.assertNotIn("goal_baseline", self.pipeline.status()["runs"]["independent-shared"])

    def test_goal_receipt_rejects_run_id_prefix_collision(self):
        run = self.pipeline.begin("run-1", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("run-1", ORDINARY)
        receipt = self.goal_receipt("run-10", run["initial_fingerprint"])
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.pipeline.bind_goal("run-1", run["initial_fingerprint"], ORDINARY, receipt)

    def test_goal_receipt_rejects_boolean_creation_time(self):
        run = self.pipeline.begin("bad-created-at", "noon", [], [], [])
        receipt = Path(self.goal_receipt("bad-created-at", run["initial_fingerprint"]))
        data = json.loads(receipt.read_text())
        data["goal"]["createdAt"] = True
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.pipeline.bind_goal("bad-created-at", run["initial_fingerprint"], ORDINARY, str(receipt))

    def test_scheduled_binding_requires_precreation_attempt(self):
        run = self.pipeline.begin("no-marker", "noon", [], [], [], goal_mode="scheduled")
        with self.assertRaisesRegex(ValueError, "requires the pre-creation attempt"):
            self.bind_goal("no-marker", run["initial_fingerprint"])

    def test_scheduled_binding_rejects_marker_later_than_native_goal(self):
        run = self.pipeline.begin("late-marker", "noon", [], [], [], goal_mode="scheduled")
        self.pipeline.mark_goal_attempt("late-marker", ORDINARY)
        receipt = Path(self.goal_receipt("late-marker", run["initial_fingerprint"]))
        data = json.loads(receipt.read_text())
        data["goal"]["createdAt"] -= 10
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "timestamp is not earlier than native creation"):
            self.pipeline.bind_goal("late-marker", run["initial_fingerprint"], ORDINARY, str(receipt))

    def test_scheduled_binding_rejects_same_second_marker_and_goal(self):
        run = self.pipeline.begin("same-second", "noon", [], [], [], goal_mode="scheduled")
        marker = self.pipeline.mark_goal_attempt("same-second", ORDINARY)
        receipt = Path(self.goal_receipt("same-second", run["initial_fingerprint"]))
        data = json.loads(receipt.read_text())
        data["goal"]["createdAt"] = int(datetime.fromisoformat(marker["recorded_at"]).timestamp())
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "timestamp is not earlier than native creation"):
            self.pipeline.bind_goal("same-second", run["initial_fingerprint"], ORDINARY, str(receipt))

    def test_goal_receipt_rejects_unrelated_matching_token(self):
        run = self.pipeline.begin("1", "noon", [], [], [])
        receipt = Path(self.goal_receipt("run-2", run["initial_fingerprint"]))
        data = json.loads(receipt.read_text())
        data["goal"]["objective"] += " phase 1"
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.pipeline.bind_goal("1", run["initial_fingerprint"], ORDINARY, str(receipt))

    def test_goal_receipt_accepts_current_labelled_objective(self):
        run = self.pipeline.begin("labelled", "noon", [], [], [])
        receipt = Path(self.goal_receipt("labelled", run["initial_fingerprint"]))
        data = json.loads(receipt.read_text())
        data["goal"]["objective"] = f"Agent Learning baseline labelled initial_fingerprint {run['initial_fingerprint']}: verify."
        receipt.write_text(json.dumps(data))
        result = self.pipeline.bind_goal("labelled", run["initial_fingerprint"], ORDINARY, str(receipt))
        self.assertEqual(result["run_id"], "labelled")

    def test_goal_receipt_rejects_identity_embedded_in_another_objective(self):
        run = self.pipeline.begin("embedded", "noon", [], [], [])
        receipt = Path(self.goal_receipt("embedded", run["initial_fingerprint"]))
        data = json.loads(receipt.read_text())
        data["goal"]["objective"] = "Unrelated goal: " + data["goal"]["objective"]
        receipt.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.pipeline.bind_goal("embedded", run["initial_fingerprint"], ORDINARY, str(receipt))

    def test_residual_evidence_read_respects_aggregate_deadline(self):
        with self.assertRaisesRegex(self.app.ConfigError, "timed out"):
            self.app.read_before_deadline(self.report, 0)

    def test_unavailable_state_reads_fail_closed(self):
        error = self.app.subprocess.TimeoutExpired("state read", 8)
        with patch.object(self.app.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(self.app.ConfigError, "unavailable or timed out"):
                self.app.load_state(self.root)
            with self.assertRaisesRegex(ValueError, "Pipeline state is unavailable"):
                self.pipeline.load()

    def test_state_metadata_error_is_not_treated_as_missing(self):
        import learning_pipeline
        with patch.object(self.app, "read_store_state", side_effect=ValueError("temporarily inaccessible")):
            with self.assertRaisesRegex(self.app.ConfigError, "unavailable"):
                self.app.load_state(self.root)
            with self.assertRaisesRegex(self.app.ConfigError, "unavailable"):
                self.app.ensure_store(self.root)
        with patch.object(learning_pipeline, "read_store_state", side_effect=ValueError("state unavailable")):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                self.pipeline.load()

    def test_existing_processed_state_is_not_reinitialized_when_open_reports_missing(self):
        state = self.root / "state" / "processed.json"
        before = state.read_bytes()
        real_run = self.app.subprocess.run
        def open_temporarily_missing(command, **kwargs):
            if len(command) >= 3 and command[1] == "-c" and '"pipeline.json","processed.json"' in command[2]:
                injection = '''real_open=os.open\ndef test_open(path,*args,**kwargs):\n    if path=="processed.json": raise FileNotFoundError()\n    return real_open(path,*args,**kwargs)\nos.open=test_open\n'''
                command = [*command[:2], command[2].replace('v=json.load(sys.stdin)', injection + 'v=json.load(sys.stdin)', 1)]
            return real_run(command, **kwargs)
        with patch.object(self.app.subprocess, "run", side_effect=open_temporarily_missing):
            with self.assertRaisesRegex(self.app.ConfigError, "unavailable"):
                self.app.ensure_store(self.root)
        self.assertEqual(state.read_bytes(), before)

    def test_slow_goal_receipt_releases_seal_lock_before_other_writer_times_out(self):
        baseline = self.pipeline.begin("slow-goal", "noon", [], [], [])
        self.bind_goal("slow-goal", baseline["initial_fingerprint"])
        self.pipeline.finish("slow-goal", "partial", "No notes", "", [])
        receipt = self.goal_receipt("slow-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "slow-goal", "--initial-fingerprint", baseline["initial_fingerprint"],
                                                  "--seal", "--goal-receipt", receipt])
        entered = threading.Event()
        run = self.app.subprocess.run
        def slow_cat(command, **options):
            if (len(command) >= 3 and command[1] == "-c" and "from agent_learning import note_summary" in command[2]
                    and json.loads(options["input"])["kind"] == "goal-receipt"):
                entered.set()
                return run(["/bin/sleep", "2"], **options)
            return run(command, **options)
        def seal():
            with self.assertRaisesRegex(ValueError, "unavailable"):
                self.app.command_pipeline(args)
        with patch.object(self.app, "GOAL_SEAL_TIMEOUT_SECONDS", 0.8), \
             patch.object(self.app, "STATE_LOCK_TIMEOUT_SECONDS", 1.5), \
             patch.object(self.app.subprocess, "run", side_effect=slow_cat), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(seal)
            self.assertTrue(entered.wait(2))
            with self.app.state_update_lock(self.root):
                pass
            future.result()

    def test_goal_status_flags_a_later_unbound_run_while_cycle_is_active(self):
        baseline = self.pipeline.begin("bound-goal", "noon", [], [], [])
        self.bind_goal("bound-goal", baseline["initial_fingerprint"])
        self.pipeline.finish("bound-goal", "partial", "Continue", "", [])
        self.pipeline.begin("unbound-later", "noon", [], [], [])
        state = self.pipeline.load()
        state["runs"]["unbound-later"].pop("goal_baseline")
        self.pipeline.save(state)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "bound-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        result = json.loads(out.getvalue())
        self.assertFalse(result["accounting_complete"])
        self.assertIn("unbound-later", result["unknown_runs"])

    def test_goal_status_rejects_a_malformed_terminal_run(self):
        baseline = self.pipeline.begin("malformed-run", "noon", [], [], [])
        self.bind_goal("malformed-run", baseline["initial_fingerprint"])
        self.pipeline.finish("malformed-run", "partial", "Done", "", [])
        state = self.pipeline.load()
        state["runs"]["malformed-run"]["status"] = "mystery"
        self.pipeline.save(state)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "malformed-run", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        result = json.loads(out.getvalue())
        self.assertFalse(result["accounting_complete"])
        self.assertIn("malformed-run", result["unknown_runs"])

    def test_goal_binding_rejects_legacy_or_changed_initial_snapshot(self):
        baseline = self.pipeline.begin("legacy-goal", "noon", [], [], [])
        state = self.pipeline.load()
        state["runs"]["legacy-goal"].pop("initial_fingerprint")
        self.pipeline.save(state)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            self.bind_goal("legacy-goal", baseline["initial_fingerprint"])

    def test_goal_binding_leaves_later_unbound_legacy_run_unknown(self):
        baseline = self.pipeline.begin("before-bind", "noon", [], [], [])
        self.pipeline.finish("before-bind", "partial", "Interrupted before binding", "", [])
        later = self.pipeline.begin("before-bind-retry", "noon", [], [], [])
        with self.assertRaisesRegex(ValueError, "Another run is active"):
            self.bind_goal("before-bind", baseline["initial_fingerprint"])
        self.pipeline.finish("before-bind-retry", "partial", "Retry ended", "", [])
        self.bind_goal("before-bind", baseline["initial_fingerprint"])
        state = self.pipeline.status()
        self.assertNotIn("goal_baseline", state["runs"]["before-bind-retry"])
        self.assertEqual(later["initial_fingerprint"], state["runs"]["before-bind-retry"]["initial_fingerprint"])
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-goal-status", "--run-id", "before-bind",
            "--initial-fingerprint", baseline["initial_fingerprint"],
        ])
        output = io.StringIO()
        with redirect_stdout(output):
            self.app.command_pipeline(args)
        status = json.loads(output.getvalue())
        self.assertFalse(status["accounting_complete"])
        self.assertIn("before-bind-retry", status["unknown_runs"])

    def test_goal_status_does_not_assume_later_same_path_is_the_unavailable_source(self):
        source = self.root / "inbox" / "later.md"
        source.write_text('---\nid: "later"\nstatus: "inbox"\n---\n# Later\n')
        known = self.root / "inbox" / "known.md"
        known.write_text('---\nid: "known"\nstatus: "inbox"\n---\n# Known\n')
        unavailable = [{"path": str(source), "reason": "unavailable or unreadable"}]
        baseline = self.pipeline.begin("unavailable-goal", "noon", [], [{"id": "known", **self.ref(known)}], unavailable)
        with self.assertRaisesRegex(ValueError, "contains unavailable"):
            self.bind_goal("unavailable-goal", baseline["initial_fingerprint"])
        # Preserve fail-closed behavior when inspecting a cycle created by an older version.
        with self.pipeline.lock():
            state = self.pipeline.load()
            reference = {"run_id": "unavailable-goal", "initial_fingerprint": baseline["initial_fingerprint"]}
            state["runs"]["unavailable-goal"]["goal_baseline"] = reference
            state["goal_cycle"] = {**reference, "status": "active"}
            self.pipeline.save(state)
        finalize_known = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "unavailable-goal", "--file", str(known),
                                                            "--status", "rejected", "--rationale", "Not reusable."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(finalize_known)
        self.pipeline.finish("unavailable-goal", "partial", "Wait for source", "", [])
        current = [{"id": "later", **self.ref(source)}]
        self.pipeline.begin("recovered-goal", "noon", [], current, [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "recovered-goal", "--file", str(source),
                                                  "--status", "processed", "--rationale", "Reviewed source."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        self.pipeline.finish("recovered-goal", "partial", "Receipt pending", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "unavailable-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        status = json.loads(out.getvalue())
        self.assertFalse(status["accounting_complete"])
        self.assertEqual(status["runs"][0]["assessment"]["unavailable"], unavailable)
        self.assertEqual(status["receipts"][0]["status"], "pending")

    def test_goal_cannot_bind_a_baseline_with_only_unavailable_sources(self):
        baseline = self.pipeline.begin("all-unavailable", "noon", [], [], [{"path": "/unavailable", "reason": "offline"}])
        with self.assertRaisesRegex(ValueError, "contains unavailable"):
            self.bind_goal("all-unavailable", baseline["initial_fingerprint"])

    def test_goal_continuation_rejects_an_incomplete_inventory(self):
        baseline = self.pipeline.begin("complete-baseline", "noon", [], [], [])
        self.bind_goal("complete-baseline", baseline["initial_fingerprint"])
        self.pipeline.finish("complete-baseline", "partial", "Continue", "", [])
        with self.assertRaisesRegex(ValueError, "complete inbox inventory"):
            self.pipeline.begin("incomplete-continuation", "noon", [], [], [{"path": "/offline", "reason": "offline"}])
        with self.assertRaisesRegex(ValueError, "complete inbox inventory"):
            self.pipeline.begin("missing-continuation", "noon")
        self.assertNotIn("incomplete-continuation", self.pipeline.status()["runs"])

    def test_begin_retry_cannot_replace_the_original_snapshot(self):
        first = [{"id": "first", "path": "/first", "sha256": "a"}]
        self.pipeline.begin("retry-snapshot", "noon", [], first, [])
        self.pipeline.begin("retry-snapshot", "noon", [], first, [])
        with self.assertRaisesRegex(ValueError, "different initial inbox"):
            self.pipeline.begin("retry-snapshot", "noon", [], [], [])
        with self.assertRaisesRegex(ValueError, "different initial inbox"):
            self.pipeline.begin("retry-snapshot", "noon", [], first, [{"path": "/offline", "reason": "offline"}])

    def test_goal_status_rejects_an_edited_final_archive(self):
        source = self.root / "inbox" / "edited.md"
        source.write_text('---\nid: "edited"\nstatus: "inbox"\n---\n# Original\n')
        initial = [{"id": "edited", **self.ref(source)}]
        baseline = self.pipeline.begin("edited-goal", "noon", [], initial, [])
        self.bind_goal("edited-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "edited-goal", "--file", str(source),
                                                  "--status", "processed", "--rationale", "Reviewed source."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        archived = Path(self.app.load_state(self.root)["processed"]["edited"]["path"])
        archived.write_text(archived.read_text() + "\nChanged after finalization.\n")
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "edited-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        status = json.loads(out.getvalue())
        self.assertFalse(status["accounting_complete"])
        self.assertEqual(status["runs"][0]["assessment"]["unassessed"][0]["id"], "edited")

    def test_goal_status_preserves_unchanged_source_lineage_through_review(self):
        source = self.root / "inbox" / "reviewed.md"
        source.write_text('---\nid: "reviewed"\nstatus: "inbox"\n---\n# Reviewed\n')
        initial = [{"id": "reviewed", **self.ref(source)}]
        baseline = self.pipeline.begin("review-goal", "noon", [], initial, [])
        self.bind_goal("review-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "review-goal", "--file", str(source),
                                                  "--status", "needs-review", "--rationale", "Further review."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        review = self.app.load_state(self.root)["processed"]["reviewed"]["path"]
        review_file = Path(review)
        review_file.write_text(review_file.read_text().replace("[ ] Approve:", "[x] Approve:"))
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "review-goal", "--file", review,
                                                  "--status", "processed", "--rationale", "Reviewed."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        self.pipeline.finish("review-goal", "partial", "Receipt pending", "", [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "review-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        status = json.loads(out.getvalue())
        self.assertTrue(status["accounting_complete"])
        self.assertEqual(status["receipts"][0]["status"], "pending")

    def test_goal_status_does_not_inherit_lineage_after_review_body_edit(self):
        source = self.root / "inbox" / "changed-review.md"
        source.write_text('---\nid: "changed-review"\nstatus: "inbox"\n---\n# Original\n')
        initial = [{"id": "changed-review", **self.ref(source)}]
        baseline = self.pipeline.begin("body-edit-goal", "noon", [], initial, [])
        self.bind_goal("body-edit-goal", baseline["initial_fingerprint"])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "body-edit-goal", "--file", str(source),
                                                  "--status", "needs-review", "--rationale", "Further review."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        review = Path(self.app.load_state(self.root)["processed"]["changed-review"]["path"])
        review.write_text(review.read_text().replace("# Original", "# Different"))
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", "body-edit-goal", "--file", str(review),
                                                  "--status", "processed", "--rationale", "Reviewed changed content."])
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-goal-status", "--run-id",
                                                  "body-edit-goal", "--initial-fingerprint", baseline["initial_fingerprint"]])
        out = io.StringIO()
        with redirect_stdout(out):
            self.app.command_pipeline(args)
        self.assertFalse(json.loads(out.getvalue())["accounting_complete"])

    def test_inventory_preserves_note_identity_across_supported_newlines(self):
        for index, newline in enumerate(["\n", "\r\n", "\r"]):
            with self.subTest(newline=repr(newline)):
                note_id = f"actual-id-{index}"
                run_id = f"newline-run-{index}"
                inbox = self.root / "inbox" / f"different-filename-{index}.md"
                text = f'---\nid: "{note_id}"\nstatus: "inbox"\ncreated_at: "2026-01-01T00:00:00+00:00"\nproject_path: "/example/project"\n---\n# Source\n'
                raw = text.replace("\n", newline).encode()
                inbox.write_bytes(raw)
                summary = self.app.note_summary(inbox)
                self.assertEqual(summary["id"], note_id)
                self.assertEqual(summary["project_path"], "/example/project")
                self.assertEqual(summary["created_at"], "2026-01-01T00:00:00+00:00")
                self.assertEqual(summary["sha256"], hashlib.sha256(raw).hexdigest())
                with patch.object(self.app, "pipeline_inventory", return_value=[]):
                    args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-begin", "--run-id", run_id, "--runner", "noon", "--goal-mode", "manual"])
                    with redirect_stdout(io.StringIO()):
                        self.app.command_pipeline(args)
                self.assertEqual(self.pipeline.status()["runs"][run_id]["initial_inbox"][0]["id"], note_id)
                self.pipeline.audit(run_id, self.audit_payload(False), [], True)
                args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--run-id", run_id, "--file", str(inbox), "--status", "processed", "--rationale", "Covered by current evidence."])
                with redirect_stdout(io.StringIO()):
                    self.app.command_finalize_note(args)
                self.pipeline.finish(run_id, "completed", "Verified", str(self.report), [])

    def test_inbox_cohorts_are_hints_and_do_not_combine_empty_identity(self):
        notes = [
            {"id": "a", "project_path": "/project", "source_run_id": "run", "lesson_family": "one", "created_at": "2026-01-01"},
            {"id": "b", "project_path": "/project", "source_run_id": "run", "lesson_family": "two", "created_at": "2026-01-02"},
            {"id": "c", "project_path": "", "source_run_id": "", "lesson_family": "", "created_at": "2026-01-03"},
            {"id": "d", "project_path": "", "source_run_id": "", "lesson_family": "", "created_at": "2026-01-04"},
        ]
        analysis = self.app.inbox_analysis(notes)
        self.assertEqual(analysis["candidate_cohorts"][0]["note_ids"], ["a", "b"])
        self.assertIn("hints", analysis["notice"])
        review = [{"id": "e", "project_path": "/project", "source_run_id": "run", "created_at": "2026-01-03", "review_decision": "pending"}]
        joined = self.app.inbox_analysis(notes, review)["candidate_cohorts"][0]
        self.assertEqual(joined["note_ids"], ["a", "b", "e"])
        self.assertEqual(joined["review_note_ids"], ["e"])
        review.append({"id": "f", "project_path": "/project", "source_run_id": "other", "created_at": "2026-01-04", "review_decision": "pending"})
        review.append({"id": "g", "project_path": "/project", "source_run_id": "run", "created_at": "2026-01-05", "review_decision": "approved"})
        context = self.app.inbox_analysis(notes, review)["candidate_contexts"][0]
        self.assertEqual(context["review_note_ids"], ["e", "f"])
        self.assertNotIn("g", context["note_ids"])

    def test_preparation_keeps_readable_notes_and_reports_inaccessible_sources(self):
        inbox = self.root / "inbox"
        good = inbox / "good.md"
        bad = inbox / "bad.md"
        good.write_text('---\nid: "good"\nstatus: "inbox"\n---\n# Good\n')
        bad.write_text('---\nid: "bad"\nstatus: "inbox"\n---\n# Bad\n')
        real = self.app.read_note_bounded
        def read(path):
            if path.resolve() == bad.resolve():
                raise TimeoutError("stalled")
            return real(path)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "prepare-run"])
        out = io.StringIO()
        with patch.object(self.app, "read_note_bounded", side_effect=read), redirect_stdout(out):
            self.app.command_prepare_run(args)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["inventory_complete"])
        self.assertEqual([n["id"] for n in payload["inbox"]], ["good"])
        self.assertEqual(payload["unavailable_sources"][0]["path"], str(bad))
        self.pipeline.begin("unavailable-run", "noon", [], [], payload["unavailable_sources"])
        self.pipeline.audit("unavailable-run", self.audit_payload(False), [], True)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.pipeline.finish("unavailable-run", "completed", "Done", str(self.report), [])

    def test_begin_does_not_create_run_when_every_inbox_source_is_unreadable(self):
        source = self.root / "inbox" / "offline.md"
        source.write_text('---\nid: "offline"\nstatus: "inbox"\n---\n# Offline\n')
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-begin",
                                                  "--run-id", "offline-run", "--runner", "noon", "--goal-mode", "manual"])
        with patch.object(self.app, "read_note_bounded", side_effect=TimeoutError("offline")):
            with self.assertRaisesRegex(self.app.ConfigError, "none are readable"):
                self.app.command_pipeline(args)
        self.assertNotIn("offline-run", self.pipeline.status()["runs"])

    def test_audit_refresh_keeps_a_pending_decision_and_its_options(self):
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        self.pipeline.audit("run-1", self.audit_payload(), [], True)
        issue = next(iter(self.pipeline.status()["issues"].values()))
        proposal = self.decision()
        proposal.update(id=issue["id"], kind="audit", sources=[])
        self.pipeline.update_issue(proposal)
        self.target.write_text(self.target.read_text() + "\n## Unrelated edit\n")
        self.pipeline.audit("run-1", self.audit_payload(), [], False)
        saved = self.pipeline.status()["issues"][issue["id"]]
        self.assertEqual(saved["status"], "needs-user")
        self.assertEqual(saved["options"], proposal["options"])
        self.assertTrue(saved["revalidation_required"])

    def test_disappearing_finding_still_requires_semantic_revalidation(self):
        self.pipeline.begin("run-1", "midnight", [self.ref(self.target)], [])
        self.pipeline.audit("run-1", self.audit_payload(), [], True)
        issue = next(iter(self.pipeline.status()["issues"].values()))
        proposal = self.decision()
        proposal.update(id=issue["id"], kind="audit", sources=[])
        self.pipeline.update_issue(proposal)
        self.target.write_text("# Instructions\n\n- Preserve the complete source contract.\n")
        self.pipeline.audit("run-1", self.audit_payload(False), [], False)
        self.assertTrue(self.pipeline.status()["issues"][issue["id"]]["revalidation_required"])
        with self.assertRaisesRegex(ValueError, "Revalidate"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [str(self.target)])
        self.pipeline.update_issue({"id": issue["id"], "status": "resolved", "targets": [self.ref(self.target)],
                                    "resolution": "The revised rule preserves coverage without repetition.", "evidence": [str(self.report)]})
        self.pipeline.finish("run-1", "completed", "Verified", str(self.report), [str(self.target)])

    def test_instruction_file_removal_is_explicitly_outside_consolidation(self):
        self.pipeline.begin("run-1", "midnight", [self.ref(self.target)], [])
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.target.unlink()
        with self.assertRaisesRegex(ValueError, "removal/uninstall"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [])

    def test_completion_rejects_legacy_run_without_initial_inbox(self):
        self.pipeline.begin("legacy-no-snapshot", "noon")
        self.pipeline.audit("legacy-no-snapshot", self.audit_payload(False), [], True)
        with self.assertRaisesRegex(ValueError, "initial inbox snapshot"):
            self.pipeline.finish("legacy-no-snapshot", "completed", "Done", str(self.report), [])

    def test_stale_review_sources_cannot_bypass_completion_without_a_post_scan(self):
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.pipeline.update_issue(self.decision(), "run-1")
        self.note.write_text(self.note.read_text() + "\nAdditional evidence changes the decision context.\n")
        with self.assertRaisesRegex(ValueError, "Revalidate"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [])
        revised = self.decision()
        self.pipeline.update_issue(revised, "run-1")
        self.pipeline.finish("run-1", "completed", "Revalidated", str(self.report), [])

    def test_open_candidate_survives_an_unchanged_delta(self):
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        self.pipeline.audit("run-1", self.audit_payload(), [], True)
        self.pipeline.audit("run-1", self.audit_payload(False), [], False)
        self.assertEqual(len(self.pipeline.status()["issues"]), 1)
        self.assertEqual(next(iter(self.pipeline.status()["issues"].values()))["status"], "candidate")
        done = self.pipeline.finish("run-1", "completed", "Notes accounted for", str(self.report), [])
        self.assertEqual(len(done["deferred_issue_ids"]), 1)
        with self.assertRaisesRegex(ValueError, "outstanding issues"):
            self.pipeline.weekly_release_status()

    def test_candidate_on_a_changed_target_still_blocks_completion(self):
        self.pipeline.begin("run-scoped", "midnight", [self.ref(self.target)], [])
        self.pipeline.audit("run-scoped", self.audit_payload(False), [], True)
        self.target.write_text("# Instructions\n\n- Require a verified current revision before publication.\n- New clause.\n")
        self.pipeline.audit("run-scoped", self.audit_payload(), [], False)
        with self.assertRaisesRegex(ValueError, "current-scope audit candidate"):
            self.pipeline.finish("run-scoped", "completed", "Done", str(self.report), [str(self.target)])

    def test_current_open_decision_is_not_reported_as_historical_debt(self):
        self.pipeline.begin("run-1", "noon", initial_inbox=[])
        self.pipeline.audit("run-1", self.audit_payload(), [], True)
        self.pipeline.update_issue(self.decision(), "run-1")
        done = self.pipeline.finish("run-1", "completed", "Classified", str(self.report), [])
        self.assertIn("decision-1", done["outstanding_issue_ids"])
        self.assertNotIn("decision-1", done["deferred_issue_ids"])
        self.assertEqual(len(done["deferred_issue_ids"]), 1)

    def test_post_audit_uses_initial_hashes_when_root_list_expands(self):
        other = self.directory / "other.md"
        other.write_text(self.target.read_text())
        new = self.directory / "new.md"
        new.write_text("# Instructions\n\n- Verify the newly added independent rule.\n")
        self.pipeline.begin("scoped-audit", "noon", [self.ref(self.target), self.ref(other)], [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit", "--run-id", "scoped-audit", "--target", str(new)])
        with patch.object(self.app, "pipeline_roots", return_value=[str(self.target), str(other), str(new)]), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline_audit(args)
        result = json.loads(output.getvalue())
        payload = json.loads(Path(result["snapshot"]["path"]).read_text())["audit"]
        self.assertEqual(payload["comparison_scope"], "changed-targets")
        self.assertEqual(payload["changed_files"], [str(new)])
        self.assertEqual(payload["exact_duplicates"], [])

    def test_expanded_post_audit_preserves_unbaselined_context_without_historical_fanout(self):
        old_a = self.directory / "old-a.md"
        old_b = self.directory / "old-b.md"
        explicit = self.directory / "explicit.md"
        duplicate = "# Rules\n\n- Always verify the complete retained contract before publishing.\n"
        old_a.write_text(duplicate)
        old_b.write_text(duplicate)
        explicit.write_text("# Rules\n\n- Retain an independent receipt for the current operation.\n")
        self.pipeline.begin("expanded", "noon", [self.ref(self.target)], [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit", "--run-id", "expanded", "--target", str(explicit)])
        roots = [str(self.target), str(old_a), str(old_b), str(explicit)]
        with patch.object(self.app, "pipeline_roots", return_value=roots), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline_audit(args)
        result = json.loads(output.getvalue())
        audit = json.loads(Path(result["snapshot"]["path"]).read_text())["audit"]
        self.assertEqual(set(audit["unbaselined_files"]), {str(old_a), str(old_b), str(explicit)})
        self.assertEqual(audit["changed_files"], [str(explicit)])
        self.assertEqual(audit["exact_duplicates"], [])
        explicit.write_text(duplicate)
        with patch.object(self.app, "pipeline_roots", return_value=roots), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline_audit(args)
        audit = json.loads(Path(json.loads(output.getvalue())["snapshot"]["path"]).read_text())["audit"]
        self.assertEqual(len(audit["exact_duplicates"]), 1)
        self.assertIn(str(explicit), {rule["path"] for rule in audit["exact_duplicates"][0]})

    def test_completion_cannot_omit_prior_explicit_reference_audit_target(self):
        reference = self.directory / "references" / "contract.md"
        reference.parent.mkdir()
        reference.write_text("# Contract\n\n- Keep a durable receipt for the requested operation.\n")
        self.pipeline.begin("reference-run", "noon", [self.ref(self.target)], [])
        self.pipeline.audit("reference-run", self.audit_payload(False), [], True)
        roots = [str(self.target), str(reference)]
        for added in (["--target", str(reference)], []):
            args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit", "--run-id", "reference-run", *added])
            with patch.object(self.app, "pipeline_roots", return_value=roots), redirect_stdout(io.StringIO()):
                self.app.command_pipeline_audit(args)
        with self.assertRaisesRegex(ValueError, "omitted"):
            self.pipeline.finish("reference-run", "completed", "Done", str(self.report), [])
        self.pipeline.finish("reference-run", "completed", "Verified", str(self.report), [str(reference)])

    def test_weekly_expanded_roots_reuse_previous_snapshot_and_discover_new_conflicts(self):
        old_a = self.directory / "old-a.md"
        old_b = self.directory / "old-b.md"
        old_c = self.directory / "old-c.md"
        old_d = self.directory / "old-d.md"
        new = self.directory / "new.md"
        duplicate = "# Rules\n\n- Verify the complete retained contract before publication.\n"
        old_a.write_text(duplicate)
        old_b.write_text(duplicate)
        old_c.write_text("# Rules\n\n- Preserve a separate receipt for each execution.\n")
        old_d.write_text(old_c.read_text())
        new.write_text(duplicate)
        previous = self.root / "reports" / "previous-week.json"
        previous.write_text(json.dumps({"audit": {"rule_parser_version": self.app.RULE_PARSER_VERSION, "file_snapshots": [
            {**self.ref(path), "words": len(path.read_text().split())} for path in [old_a, old_b, old_c, old_d]]}}))
        with self.pipeline.lock():
            state = self.pipeline.load()
            state["weekly"] = {"cycle": "older-cycle", "roots": [str(old_a), str(old_b), str(old_c), str(old_d)], "snapshot": self.ref(previous)}
            state["audit_rule_parser_version"] = self.app.RULE_PARSER_VERSION
            self.pipeline.save(state)
        self.pipeline.begin("weekly-expanded", "noon", initial_inbox=[])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit", "--run-id", "weekly-expanded", "--weekly"])
        with patch.object(self.app, "pipeline_roots", return_value=[str(old_a), str(old_b), str(old_c), str(old_d), str(new)]), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline_audit(args)
        record = json.loads(Path(json.loads(output.getvalue())["snapshot"]["path"]).read_text())["audit"]
        self.assertEqual(record["changed_files"], [str(new)])
        self.assertEqual(len(record["exact_duplicates"]), 1)
        self.assertEqual({rule["path"] for rule in record["exact_duplicates"][0]}, {str(old_a), str(old_b), str(new)})
        self.assertEqual(len(self.pipeline.status()["issues"]), 1)

    def test_weekly_explicit_target_must_be_declared_and_post_audited(self):
        self.pipeline.begin("weekly-target", "noon", initial_inbox=[])
        self.pipeline.audit("weekly-target", self.audit_payload(False), [], True, [str(self.target)])
        with self.assertRaisesRegex(ValueError, "omitted"):
            self.pipeline.finish("weekly-target", "completed", "Done", str(self.report), [])
        with self.assertRaisesRegex(ValueError, "post-consolidation audit"):
            self.pipeline.finish("weekly-target", "completed", "Done", str(self.report), [str(self.target)])
        self.pipeline.audit("weekly-target", self.audit_payload(False), [], False)
        self.pipeline.finish("weekly-target", "completed", "Verified", str(self.report), [str(self.target)])

    def test_removed_prior_weekly_target_persists_as_blocking_reconciliation(self):
        vanished = self.directory / "removed.md"
        vanished.write_text("# Contract\n\n- Preserve this previously audited contract.\n")
        previous = self.root / "reports" / "previous-with-removed.json"
        previous.write_text(json.dumps({"audit": {"file_snapshots": [
            {**self.ref(path), "words": len(path.read_text().split())} for path in [self.target, vanished]]}}))
        with self.pipeline.lock():
            state = self.pipeline.load()
            state["weekly"] = {"cycle": "older-cycle", "roots": [str(self.target), str(vanished)], "snapshot": self.ref(previous)}
            self.pipeline.save(state)
        vanished.unlink()
        self.pipeline.begin("removal-run", "noon", [self.ref(self.target)], [])
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-audit", "--run-id", "removal-run", "--weekly"])
        with patch.object(self.app, "pipeline_roots", return_value=[str(self.target)]), redirect_stdout(io.StringIO()):
            self.app.command_pipeline_audit(args)
        weekly_report = Path(self.pipeline.status()["weekly"]["snapshot"]["path"])
        audited = json.loads(weekly_report.read_text())["audit"]["file_snapshots"]
        self.assertEqual([item["path"] for item in audited], [str(self.target)])
        issues = self.pipeline.status()["issues"]
        removal = next(value for key, value in issues.items() if key.startswith("audit-removal-"))
        self.assertEqual(removal["removed_path"], str(vanished.resolve()))
        with self.assertRaisesRegex(ValueError, "disappeared instruction"):
            self.pipeline.finish("removal-run", "completed", "Done", str(self.report), [])
        with self.assertRaisesRegex(ValueError, "outstanding issues"):
            self.pipeline.weekly_release_status()
        self.pipeline.finish("removal-run", "partial", "Reconciliation required", str(self.report), [])
        self.pipeline.begin("removal-next", "noon", [self.ref(self.target)], [])
        with self.assertRaisesRegex(ValueError, "disappeared instruction"):
            self.pipeline.finish("removal-next", "completed", "Done", str(self.report), [])
        self.pipeline.update_issue({"id": removal["id"], "status": "resolved",
                                    "resolution": "Removal separately reconciled with saved evidence.",
                                    "evidence": [str(self.report)]}, "removal-next")
        self.pipeline.finish("removal-next", "completed", "Reconciled", str(self.report), [])
        self.report.write_text("# Changed evidence\n")
        with self.assertRaisesRegex(ValueError, "removal evidence changed"):
            self.pipeline.weekly_release_status()
        self.pipeline.begin("removal-proof-drift", "noon", [self.ref(self.target)], [])
        with self.assertRaisesRegex(ValueError, "reconciliation evidence changed"):
            self.pipeline.finish("removal-proof-drift", "completed", "Done", str(self.report), [])

    def test_parser_snapshot_rejects_parent_symlink_swap_after_audit(self):
        parent = self.directory / "audited-parent"
        parent.mkdir()
        target = parent / "AGENTS.md"
        target.write_text("# Audited\n\n- Keep verified rules.\n")
        self.pipeline.begin("parser-parent-swap", "noon", initial_inbox=[])
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-audit", "--run-id", "parser-parent-swap", "--weekly",
        ])
        with patch.object(self.app, "pipeline_roots", return_value=[str(parent)]), redirect_stdout(io.StringIO()):
            self.app.command_pipeline_audit(args)
        self.pipeline.finish("parser-parent-swap", "completed", "Verified", str(self.report), [])

        outside = self.directory / "outside-audited-parent"
        outside.mkdir()
        (outside / "AGENTS.md").write_text(target.read_text())
        os.rename(parent, self.directory / "audited-parent-original")
        os.symlink(outside, parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Current rule parser audit evidence changed"):
            self.pipeline.weekly_release_status()

    def test_parser_snapshot_rejects_unbound_legacy_alias(self):
        import learning_pipeline

        actual = self.directory / "legacy-audit-target"
        actual.mkdir()
        target = actual / "AGENTS.md"
        target.write_text("# Legacy target\n")
        alias = self.directory / "legacy-audit-alias"
        os.symlink(actual, alias, target_is_directory=True)
        legacy_ref = {"path": str(alias / "AGENTS.md"),
                      "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        self.assertFalse(learning_pipeline.current_parser_snapshot_refs([legacy_ref]))

    def test_scoped_cohort_audit_accepts_var_alias_and_matches_lexical_members(self):
        import learning_pipeline

        rules = self.directory / "scoped-cohort-rules"
        rules.mkdir()
        repeated = ("- Require the verified release evidence before closing the review.\n"
                    "- Retain the complete audit evidence with the release.\n")
        paths = []
        for name in ("a", "b"):
            target_dir = rules / name
            target_dir.mkdir()
            target = target_dir / "AGENTS.md"
            target.write_text("# Rules\n\n" + repeated)
            paths.append(target)
        lexical_root = str(rules).replace("/private/var/", "/var/")
        self.pipeline.begin("scoped-cohort-alias", "noon", initial_inbox=[])
        args = self.app.build_parser().parse_args([
            "--config", str(self.config), "pipeline-audit", "--run-id", "scoped-cohort-alias", "--weekly",
        ])
        with patch.object(self.app, "pipeline_roots", return_value=[lexical_root]), redirect_stdout(io.StringIO()):
            self.app.command_pipeline_audit(args)
        cohorts = [issue for issue in self.pipeline.status()["issues"].values()
                   if "cohort_version" in issue]
        self.assertEqual(len(cohorts), 1)
        self.assertTrue(learning_pipeline.current_scoped_cohorts(cohorts))
        report = json.loads(Path(cohorts[0]["latest_audit"]["path"]).read_text())["audit"]
        self.assertEqual({item["path"] for item in report["file_snapshots"]},
                         {str(path).replace("/private/var/", "/var/") for path in paths})
        outside = self.directory / "scoped-cohort-outside"
        for path in paths:
            target_dir = outside / path.parent.name
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / path.name).write_bytes(path.read_bytes())
        os.rename(rules, self.directory / "scoped-cohort-original")
        os.symlink(outside, rules, target_is_directory=True)
        self.assertFalse(learning_pipeline.current_scoped_cohorts(cohorts))

    def test_issue_assessment_requires_explicit_active_run_ownership(self):
        self.pipeline.begin("run-1", "noon", initial_inbox=[])
        decision = self.decision()
        decision["last_assessed_run"] = "run-1"
        result = self.pipeline.update_issue(decision)
        self.assertNotIn("last_assessed_run", result)
        result = self.pipeline.update_issue(decision, "run-1")
        self.assertEqual(result["last_assessed_run"], "run-1")
        with self.assertRaises(ValueError):
            self.pipeline.update_issue(decision, "missing-run")

    def test_release_blocks_each_unresolved_issue_state(self):
        for state in ("candidate", "investigate", "needs-user"):
            with self.subTest(state=state):
                with self.pipeline.lock():
                    stored = self.pipeline.load()
                    stored["issues"] = {"open": {"id": "open", "status": state}}
                    self.pipeline.save(stored)
                with self.assertRaisesRegex(ValueError, "outstanding issues"):
                    self.pipeline.weekly_release_status()

    def test_scoped_release_assessment_cannot_defer_open_operational_issue(self):
        self.pipeline.begin("release-assessment-run", "noon", initial_inbox=[])
        self.pipeline.audit("release-assessment-run", self.audit_payload(False), [], True)
        self.pipeline.finish("release-assessment-run", "completed", "Verified", str(self.report), [])
        candidate_root, _, _ = self.release_candidate()
        self.pipeline.update_issue({"id": "unrelated-runtime", "kind": "operational", "status": "investigate",
                                    "title": "Unrelated runtime check", "description": "Inspect an external runtime.",
                                    "sources": [], "targets": [self.ref(self.target)]})
        _, assessment = self.release_assessment(candidate_root)
        with self.assertRaisesRegex(ValueError, "Only open review findings may be deferred"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(self.pipeline.status()["issues"]["unrelated-runtime"]["status"], "investigate")

    def test_scoped_release_assessment_cannot_defer_open_review_issue(self):
        self.pipeline.begin("release-review-run", "noon", initial_inbox=[])
        self.pipeline.audit("release-review-run", self.audit_payload(False), [], True)
        self.pipeline.finish("release-review-run", "completed", "Verified", str(self.report), [])
        candidate_root, _, _ = self.release_candidate()
        self.pipeline.update_issue({"id": "unrelated-review", "kind": "review", "status": "investigate",
                                    "title": "Review finding", "description": "Review the active operational choice.",
                                    "question": "Which supported behavior should remain?", "options": [],
                                    "sources": [{**self.ref(self.note), "note_id": "note-1"}],
                                    "targets": [self.ref(self.target)]})
        _, assessment = self.release_assessment(candidate_root)

        with self.assertRaisesRegex(ValueError, "Review independent issue review needs a saved report path"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def add_foreign_investigation(self, issue_id="foreign-runtime-check", status="investigate", target=None):
        foreign = self.directory / "foreign-runtime-project"
        foreign.mkdir(exist_ok=True)
        source = foreign / "source.md"
        source.write_text("Source evidence for the separate runtime.\n", encoding="utf-8")
        target = target or (foreign / "target.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("Target owned by the separate runtime.\n", encoding="utf-8")
        evidence = self.root / "reports" / f"{issue_id}-foreign-report.json"
        evidence.write_text('{"result":"separate project"}\n', encoding="utf-8")
        issue = {"id": issue_id, "kind": "operational", "status": status,
                 "title": "Separate runtime check", "description": "Inspect another project's runtime.",
                 "sources": [self.ref(source)], "targets": [self.ref(target)], "evidence": [str(evidence)]}
        if status == "needs-user":
            issue.update(question="Choose an owner.", options=[{"id": "ops", "label": "Operations",
                                                                 "description": "Assign to operations."},
                                                        {"id": "owner", "label": "Owner",
                                                         "description": "Choose another owner."}])
        self.pipeline.update_issue(issue)
        return issue, source, target, evidence

    def complete_release_cycle(self, run_id):
        self.pipeline.begin(run_id, "noon", initial_inbox=[])
        self.pipeline.audit(run_id, self.audit_payload(False), [], True)
        self.pipeline.finish(run_id, "completed", "Verified", str(self.report), [])

    def unrelated_assessment(self, issue_id, candidate_root):
        payload, assessment = self.release_assessment(candidate_root, classification="block")
        self.mark_unrelated(payload, issue_id)
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        return payload, assessment

    def test_scoped_release_marks_independently_reviewed_external_investigation_unrelated_without_reading_targets(self):
        self.complete_release_cycle("release-unrelated-check")
        issue, source, target, evidence = self.add_foreign_investigation()
        candidate_root, _, _ = self.release_candidate()
        _payload, assessment = self.unrelated_assessment(issue["id"], candidate_root)
        before = self.pipeline.status()

        source.write_text("Changed external source.\n", encoding="utf-8")
        target.unlink()
        evidence.unlink()
        result = self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        release = result["release_assessment"]
        self.assertEqual(release["unrelated_issue_ids"], [issue["id"]])
        self.assertNotIn(issue["id"], release["deferred_issue_ids"])
        self.assertEqual(self.pipeline.status()["issues"], before["issues"])

    def test_scoped_release_ignores_only_reviewed_external_audit_snapshot_file(self):
        import learning_pipeline
        issue, _, target, evidence = self.add_foreign_investigation("audit-foreign-snapshot")
        self.pipeline.update_issue({**issue, "kind": "audit"})
        candidate_root, _, _ = self.release_candidate()
        candidate_file = candidate_root / "CHANGELOG.md"
        full = self.audit_payload(False)
        full["file_snapshots"] = [self.ref(target), self.ref(candidate_file)]
        self.pipeline.begin("external-parser-snapshot", "noon", full["file_snapshots"], [])
        self.pipeline.audit("external-parser-snapshot", full, [], True)
        self.pipeline.audit("external-parser-snapshot", {**full, "comparison_scope": "changed-targets"}, [], False)
        self.pipeline.finish("external-parser-snapshot", "completed", "Verified", str(self.report), [])
        _, assessment = self.unrelated_assessment(issue["id"], candidate_root)
        evidence.unlink()
        target.unlink()
        result = self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(result["release_assessment"]["unrelated_issue_ids"], [issue["id"]])
        snapshot = self.pipeline.status()["audit_rule_parser_snapshot"]
        self.assertFalse(learning_pipeline.current_parser_snapshot(snapshot, learning_pipeline.RULE_PARSER_VERSION))
        self.assertFalse(learning_pipeline.current_parser_snapshot(
            snapshot, learning_pipeline.RULE_PARSER_VERSION, excluded={str(target), str(candidate_file)}))
        report = Path(snapshot["path"])
        invalid_snapshot = json.loads(report.read_text(encoding="utf-8"))
        del invalid_snapshot["audit"]["file_snapshots"][0]["sha256"]
        malformed = report.with_name("malformed-parser-snapshot.json")
        malformed.write_text(json.dumps(invalid_snapshot), encoding="utf-8")
        self.assertFalse(learning_pipeline.current_parser_snapshot(
            self.ref(malformed), learning_pipeline.RULE_PARSER_VERSION, excluded={str(target)}))
        candidate_file.write_text("Changed candidate input.", encoding="utf-8")
        self.assertFalse(learning_pipeline.current_parser_snapshot(
            snapshot, learning_pipeline.RULE_PARSER_VERSION, excluded={str(target)}))

    def test_scoped_release_unrelated_requires_distinct_reviewer_and_current_bindings(self):
        self.complete_release_cycle("release-unrelated-binding")
        candidate_root, _, _ = self.release_candidate()
        for mutation, message in ((lambda review: review.update(reviewer="independent reviewer"),
                                   "distinct reviewer attestation bound"),
                                  (lambda review: review.update(issue_sha256="0" * 64),
                                   "distinct reviewer attestation bound"),
                                  (lambda review: review.update(candidate_sha256="0" * 64),
                                   "distinct reviewer attestation bound")):
            issue_id = "foreign-binding-" + str(time.time_ns())
            with self.subTest(issue_id=issue_id):
                issue, _source, _target, _evidence = self.add_foreign_investigation(issue_id)
                payload, assessment = self.unrelated_assessment(issue["id"], candidate_root)
                record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
                mutation(record["scope_review"])
                assessment.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
                self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")

    def test_scoped_release_unrelated_rejects_candidate_paths_missing_targets_and_protected_issues(self):
        self.complete_release_cycle("release-unrelated-protection")
        candidate_root, _, candidate_file = self.release_candidate()
        for issue_id, status, target, expected in (
                ("foreign-target-inside", "investigate", candidate_file,
                 "references the candidate"),
                ("foreign-needs-user", "needs-user", None, "unprotected, current")):
            with self.subTest(issue_id=issue_id):
                issue, _source, _target, _evidence = self.add_foreign_investigation(issue_id, status, target)
                payload, assessment = self.unrelated_assessment(issue["id"], candidate_root)
                with self.assertRaisesRegex(ValueError, expected):
                    self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
                self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], status)

        issue_id = "foreign-missing-target"
        issue, _source, _target, _evidence = self.add_foreign_investigation(issue_id)
        with self.pipeline.lock():
            state = self.pipeline.load()
            state["issues"][issue_id]["targets"] = []
            self.pipeline.save(state)
        payload, assessment = self.unrelated_assessment(issue_id, candidate_root)
        with self.assertRaisesRegex(ValueError, "explicit, well-formed source and target"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_unrelated_resolves_candidate_file_and_directory_symlinks(self):
        self.complete_release_cycle("release-unrelated-symlink")
        candidate_root, _, candidate_file = self.release_candidate()
        foreign = self.directory / "foreign-runtime-project"
        foreign.mkdir()
        file_alias = foreign / "candidate-file-alias.md"
        file_alias.symlink_to(candidate_file)
        directory_alias = foreign / "candidate-directory-alias"
        directory_alias.symlink_to(candidate_root, target_is_directory=True)
        self.add_foreign_investigation("foreign-file-symlink", target=file_alias)
        self.add_foreign_investigation("foreign-directory-symlink", target=directory_alias / "AGENTS.md")
        payload, assessment = self.release_assessment(candidate_root, classification="block")
        self.mark_unrelated(payload, "foreign-file-symlink")
        self.mark_unrelated(payload, "foreign-directory-symlink")
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "references the candidate"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_unrelated_rechecks_reference_identity_after_its_review(self):
        self.complete_release_cycle("release-unrelated-state-race")
        issue, _source, _target, _evidence = self.add_foreign_investigation("foreign-state-race")
        candidate_root, _, _ = self.release_candidate()
        _payload, assessment = self.unrelated_assessment(issue["id"], candidate_root)
        import learning_pipeline

        original = learning_pipeline.current_scoped_cohorts
        calls = 0

        def mutate_after_final_cohort_check(issues):
            nonlocal calls
            calls += 1
            current = original(issues)
            if calls == 2:
                with self.pipeline.lock():
                    state = self.pipeline.load()
                    state["issues"][issue["id"]]["status"] = "needs-user"
                    state["issues"][issue["id"]]["question"] = "Choose an owner."
                    state["issues"][issue["id"]]["options"] = [
                        {"id": "ops", "label": "Operations", "description": "Assign to operations."},
                        {"id": "owner", "label": "Owner", "description": "Choose another owner."},
                    ]
                    self.pipeline.save(state)
            return current

        with patch.object(learning_pipeline, "current_scoped_cohorts", side_effect=mutate_after_final_cohort_check):
            with self.assertRaisesRegex(ValueError, "Pipeline state changed during release verification"):
                self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_does_not_allow_unrelated_classification_to_bypass_audit_or_parser_ledger(self):
        issue, _ledger = self.parser_ledger_open_finding("release-unrelated-parser-audit")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root, classification="block")
        self.mark_unrelated(payload, issue["id"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Only unprotected, current investigations"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")

    def test_unrelated_assessment_does_not_change_strict_default_release_gate(self):
        self.complete_release_cycle("strict-unrelated-block")
        issue, _source, _target, _evidence = self.add_foreign_investigation("strict-foreign-runtime")
        with self.assertRaisesRegex(ValueError, "outstanding issues"):
            self.pipeline.weekly_release_status()
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")

    def test_release_assessment_requires_origin_main_even_for_feature_commit(self):
        from learning_pipeline import candidate_change_snapshot

        candidate_root, changelog, _ = self.release_candidate()
        subprocess.run(["git", "-C", str(candidate_root), "add", "CHANGELOG.md"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(candidate_root), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "feature change"], check=True,
                       capture_output=True)
        first = candidate_change_snapshot(candidate_root)
        self.assertEqual(first["base_ref"], "origin/main")
        target_tree = subprocess.run(["git", "-C", str(candidate_root), "rev-parse",
                                      f"{first['target_commit']}^{{tree}}"], check=True,
                                     capture_output=True, text=True).stdout.strip()
        advanced_target = subprocess.run(["git", "-C", str(candidate_root), "-c", "user.name=Test", "-c",
                                          "user.email=test@example.invalid", "commit-tree", target_tree,
                                          "-p", first["target_commit"], "-m", "advance target"], check=True,
                                         capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "-C", str(candidate_root), "update-ref", "refs/remotes/origin/main",
                        advanced_target], check=True, capture_output=True)
        second = candidate_change_snapshot(candidate_root)
        self.assertEqual(first["base_commit"], second["base_commit"])
        self.assertNotEqual(first["target_commit"], second["target_commit"])
        self.assertNotEqual(first, second)
        for wrong_target in ("HEAD", "refs/heads/main", "origin/main~1"):
            with self.subTest(wrong_target=wrong_target), self.assertRaisesRegex(ValueError, "origin/main"):
                candidate_change_snapshot(candidate_root, wrong_target)

    def test_release_assessment_rejects_unmerged_and_mixed_index_states(self):
        from learning_pipeline import candidate_change_snapshot

        candidate_root, _, _ = self.release_candidate()
        stages = []
        for stage, content in ((1, b"base\n"), (2, b"ours\n"), (3, b"theirs\n")):
            blob = subprocess.run(["git", "-C", str(candidate_root), "hash-object", "-w", "--stdin"],
                                  input=content, check=True, capture_output=True).stdout.decode().strip()
            stages.append(f"100644 {blob} {stage}\tAGENTS.md\n")
        subprocess.run(["git", "-C", str(candidate_root), "update-index", "--force-remove", "AGENTS.md"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(candidate_root), "update-index", "--index-info"],
                       input="".join(stages).encode(), check=True, capture_output=True)
        status = subprocess.run(["git", "-C", str(candidate_root), "status", "--porcelain=v1"],
                                check=True, capture_output=True, text=True).stdout
        self.assertIn("UU AGENTS.md", status)
        with self.assertRaisesRegex(ValueError, "unmerged index entries"):
            candidate_change_snapshot(candidate_root)
        (candidate_root / "MIXED.md").write_text("Untracked alongside conflict.\n", encoding="utf-8")
        status = subprocess.run(["git", "-C", str(candidate_root), "status", "--porcelain=v1"],
                                check=True, capture_output=True, text=True).stdout
        self.assertIn("UU AGENTS.md", status)
        self.assertIn("?? MIXED.md", status)
        with self.assertRaisesRegex(ValueError, "unmerged index entries"):
            candidate_change_snapshot(candidate_root)

    def test_candidate_snapshot_uses_literal_path_for_base_and_staged_blob(self):
        from learning_pipeline import candidate_change_snapshot

        candidate_root, _, _ = self.release_candidate()
        base_target = self.directory / "a[A]"
        base_sibling = self.directory / "aA"
        base_target.write_text("base target content\n", encoding="utf-8")
        base_sibling.write_text("base sibling content\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.directory), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.directory), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "literal path base"], check=True,
                       capture_output=True)
        base_commit = subprocess.check_output(["git", "-C", str(self.directory), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(self.directory), "update-ref", "refs/remotes/origin/main", base_commit],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(candidate_root), "reset", "--hard", base_commit], check=True,
                       capture_output=True)
        (candidate_root / "FEATURE.md").write_text("Feature change.\n", encoding="utf-8")
        (candidate_root / "a[A]").write_text("candidate target content\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(candidate_root), "add", "FEATURE.md", "a[A]"], check=True,
                       capture_output=True, env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"})
        subprocess.run(["git", "-C", str(candidate_root), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "feature"], check=True,
                       capture_output=True)

        before = candidate_change_snapshot(candidate_root)
        before_row = next(row for row in before["files"] if row["path"] == "a[A]")
        expected_base = subprocess.run(["git", "-C", str(candidate_root), "hash-object", "--stdin"],
                                       input=b"base target content\n", check=True, capture_output=True).stdout.decode().strip()
        self.assertEqual(before_row["base_blob"], expected_base)

        target_path = candidate_root / "a[A]"
        target_path.write_text("staged target content\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(candidate_root), "--literal-pathspecs", "add", "--", "a[A]"],
                       check=True, capture_output=True)
        target_path.write_text("candidate target content\n", encoding="utf-8")
        after = candidate_change_snapshot(candidate_root)
        after_row = next(row for row in after["files"] if row["path"] == "a[A]")
        self.assertNotEqual(before_row["index_blob"], after_row["index_blob"])
        self.assertEqual(before_row["worktree_sha256"], after_row["worktree_sha256"])
        self.assertNotEqual(before["change_sha256"], after["change_sha256"])

    def test_candidate_snapshot_ignores_ambient_git_index_and_repository_overrides(self):
        from learning_pipeline import candidate_change_snapshot

        candidate_root, _, _ = self.release_candidate()
        target = candidate_root / "AGENTS.md"
        target.write_text("Candidate baseline change.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(candidate_root), "add", "AGENTS.md"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(candidate_root), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "candidate baseline"], check=True,
                       capture_output=True)
        original_bytes = target.read_bytes()
        clean = candidate_change_snapshot(candidate_root)
        target.write_text("Staged-only index version.\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(candidate_root), "add", "AGENTS.md"], check=True, capture_output=True)
        target.write_bytes(original_bytes)
        staged = candidate_change_snapshot(candidate_root)
        staged_row = next(row for row in staged["files"] if row["path"] == "AGENTS.md")
        clean_row = next(row for row in clean["files"] if row["path"] == "AGENTS.md")
        self.assertEqual(staged_row["worktree_sha256"], clean_row["worktree_sha256"])
        self.assertNotEqual(staged_row["index_blob"], clean_row["index_blob"])

        alternate_index = self.directory / "alternate.index"
        subprocess.run(["git", "-C", str(candidate_root), "read-tree", "HEAD"], check=True,
                       capture_output=True, env={**os.environ, "GIT_INDEX_FILE": str(alternate_index)})
        overridden = candidate_change_snapshot(candidate_root)
        self.assertEqual(overridden, staged)
        fake_git_dir = self.directory / "unrelated.git"
        with patch.dict(os.environ, {"GIT_INDEX_FILE": str(alternate_index),
                                     "GIT_DIR": str(fake_git_dir),
                                     "GIT_WORK_TREE": str(self.directory),
                                     "GIT_COMMON_DIR": str(fake_git_dir),
                                     "GIT_OBJECT_DIRECTORY": str(fake_git_dir / "objects"),
                                     "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.worktree",
                                     "GIT_CONFIG_VALUE_0": str(self.directory)}):
            injected = candidate_change_snapshot(candidate_root)
        self.assertEqual(injected, staged)

    def test_candidate_snapshot_accepts_deleted_file_with_missing_parent(self):
        from learning_pipeline import candidate_file_snapshot

        root = self.directory.resolve()
        for status in ("committed:D", "D ", " D"):
            with self.subTest(status=status):
                self.assertEqual(candidate_file_snapshot(root, "missing/deeper/file.md", status), (None, None))
        with self.assertRaisesRegex(ValueError, "disappeared"):
            candidate_file_snapshot(root, "missing/deeper/file.md", " M")
        (root / "alias").symlink_to(root / "missing", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "without following symlinks"):
            candidate_file_snapshot(root, "alias/file.md", "committed:D")

    def test_candidate_snapshot_rejects_parent_symlink_swap(self):
        import learning_pipeline

        candidate_root, _, _ = self.release_candidate()
        nested = candidate_root / "nested"
        nested.mkdir()
        (nested / "target.txt").write_text("candidate data\n", encoding="utf-8")
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "target.txt").write_text("outside data\n", encoding="utf-8")
        original_open = learning_pipeline.os.open
        swapped = False

        def swap_parent(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "nested" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                os.rename(nested, candidate_root / "nested-original")
                os.symlink(outside, nested, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(learning_pipeline.os, "open", side_effect=swap_parent):
            with self.assertRaisesRegex(ValueError, "without following symlinks"):
                learning_pipeline.candidate_change_snapshot(candidate_root)
        self.assertTrue(swapped)

    def test_cohort_evidence_rejects_parent_symlink_swap_and_keeps_resolved_aliases(self):
        import learning_pipeline

        actual = self.directory / "cohort-target"
        actual.mkdir()
        target = actual / "rules.md"
        target.write_text("same audit evidence bytes\n", encoding="utf-8")
        alias = self.directory / "cohort-alias"
        os.symlink(actual, alias, target_is_directory=True)
        ref = self.ref(alias / "rules.md")
        self.assertEqual(ref["path"], str(target.resolve()))
        self.assertTrue(learning_pipeline._bounded_file_matches(ref["path"], ref["sha256"]))

        outside = self.directory / "cohort-outside"
        outside.mkdir()
        (outside / "rules.md").write_text("same audit evidence bytes\n", encoding="utf-8")
        original_open = learning_pipeline.os.open
        swapped = False

        def swap_cohort_parent(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "cohort-target" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                os.rename(actual, self.directory / "cohort-original")
                os.symlink(outside, actual, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with patch.object(learning_pipeline.os, "open", side_effect=swap_cohort_parent):
            self.assertFalse(learning_pipeline._bounded_file_matches(ref["path"], ref["sha256"]))
        self.assertTrue(swapped)

    def test_release_status_rejects_fifo_assessment_without_blocking(self):
        candidate_root, _, _ = self.release_candidate()
        fifo = self.root / "reports" / "assessment.fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "bounded regular file"):
            self.pipeline.weekly_release_status(assessment_path=str(fifo), candidate_root=candidate_root)

    def test_release_status_rejects_assessment_replaced_by_fifo_at_final_read(self):
        self.pipeline.begin("release-assessment-fifo-race", "noon", initial_inbox=[])
        self.pipeline.audit("release-assessment-fifo-race", self.audit_payload(False), [], True)
        self.pipeline.finish("release-assessment-fifo-race", "completed", "Verified", str(self.report), [])
        candidate_root, _, _ = self.release_candidate()
        _, assessment = self.release_assessment(candidate_root)
        import learning_pipeline
        original = learning_pipeline.candidate_change_snapshot
        calls = 0

        def replace_with_fifo_on_final_snapshot(root, base_ref="origin/main"):
            nonlocal calls
            calls += 1
            if calls == 2:
                assessment.unlink()
                os.mkfifo(assessment)
            return original(root, base_ref)

        with (patch.object(learning_pipeline, "candidate_change_snapshot",
                           side_effect=replace_with_fifo_on_final_snapshot),
              self.assertRaisesRegex(ValueError, "bounded regular file")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_requires_every_issue_and_current_fingerprints(self):
        self.pipeline.begin("release-assessment-gap", "noon", initial_inbox=[])
        self.pipeline.audit("release-assessment-gap", self.audit_payload(False), [], True)
        self.pipeline.finish("release-assessment-gap", "completed", "Verified", str(self.report), [])
        candidate_root, _, _ = self.release_candidate()
        self.pipeline.update_issue({"id": "unrelated-runtime", "kind": "operational", "status": "investigate",
                                    "title": "Unrelated runtime check", "description": "Inspect an external runtime.",
                                    "sources": [], "targets": [self.ref(self.target)]})
        payload, assessment = self.release_assessment(candidate_root)
        payload["assessments"].clear()
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "every open issue"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        payload, assessment = self.release_assessment(candidate_root)
        payload["assessments"][0]["issue_sha256"] = "0" * 64
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "issue changed"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_cannot_defer_user_decisions_or_changed_candidate(self):
        self.pipeline.begin("release-assessment-protected", "noon", initial_inbox=[])
        self.pipeline.audit("release-assessment-protected", self.audit_payload(False), [], True)
        self.pipeline.finish("release-assessment-protected", "completed", "Verified", str(self.report), [])
        candidate_root, changelog, _ = self.release_candidate()
        self.pipeline.update_issue(self.decision())
        _payload, assessment = self.release_assessment(candidate_root)
        with self.assertRaisesRegex(ValueError, "must remain blocking"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.pipeline.update_issue({"id": "decision-1", "status": "resolved",
                                    "resolution": "The documented choice was reconciled.",
                                    "evidence": [str(self.report)]})
        self.pipeline.begin("release-assessment-followup", "noon", initial_inbox=[])
        self.pipeline.audit("release-assessment-followup", self.audit_payload(False), [], True)
        self.pipeline.finish("release-assessment-followup", "completed", "Verified", str(self.report), [])
        _payload, assessment = self.release_assessment(candidate_root)
        changelog.write_text(changelog.read_text() + "\nChanged after review.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "candidate, target, or complete diff changed"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_keeps_audit_findings_blocking(self):
        candidate_root, _, _ = self.release_candidate()
        self.pipeline.update_issue({"id": "manual-audit", "kind": "audit", "status": "investigate",
                                    "title": "Audit finding", "description": "Review an audit finding.",
                                    "sources": [], "targets": [self.ref(self.target)]})
        _, assessment = self.release_assessment(candidate_root)
        with self.assertRaisesRegex(ValueError, "current saved report"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_candidate_integration_check_binds_current_archived_note_and_candidate_target(self):
        from learning_pipeline import candidate_change_snapshot, verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-check", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue)
        state_before = self.app.load_state(self.root)

        refs = verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                            time.monotonic() + 30, self.root,
                                            self.candidate_issue_evidence(issue))

        self.assertEqual(len(refs), 2)
        self.assertEqual(self.app.load_state(self.root), state_before)
        self.assertNotIn("integration_status", self.app.load_state(self.root)["processed"]["candidate-note"])
        self.assertEqual(candidate, candidate_change_snapshot(candidate_root))

    def test_candidate_integration_also_checks_existing_archive_hash(self):
        from learning_pipeline import verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-hashed-note", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue, include_archive_sha=True)

        refs = verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                            time.monotonic() + 30, self.root,
                                            self.candidate_issue_evidence(issue))

        self.assertEqual(len(refs), 2)
        self.assertTrue(self.app.load_state(self.root)["processed"]["candidate-note"].get("archive_sha256"))

    def test_candidate_integration_rejects_target_that_is_covered_but_not_note_mapped(self):
        from learning_pipeline import verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-unmapped-target", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue, issue_targets_source=True)

        with self.assertRaisesRegex(ValueError, "current operational target"):
            verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                         time.monotonic() + 30, self.root,
                                         self.candidate_issue_evidence(issue))

    def test_candidate_integration_rejects_protected_history_and_empty_targets(self):
        from learning_pipeline import candidate_change_snapshot, verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        candidate = candidate_change_snapshot(candidate_root)
        base = {"id": "candidate-integration-protected", "kind": "operational", "status": "investigate",
                "title": "Verify candidate integration", "description": "Check archived target mapping.",
                "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                "revalidation_required": False}
        record = {"reviewer": "release assessor", "candidate_integration": None}
        for mutate in (lambda issue: issue.update(post_release={"version": "1.4.0"}),
                       lambda issue: issue.update(post_release_binding="bound"),
                       lambda issue: issue.update(history=[{"status": "needs-user"}]),
                       lambda issue: issue.update(history=[{"status": "candidate"}]),
                       lambda issue: issue.update(targets=[])):
            issue = copy.deepcopy(base)
            mutate(issue)
            with self.assertRaisesRegex(ValueError, "current operational investigations|nonempty issue targets"):
                verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                             time.monotonic() + 5, self.root,
                                             [{**self.ref(self.report), "resolved_path": str(self.report.resolve())}])

    def test_candidate_integration_rejects_manifest_note_without_current_archive(self):
        from learning_pipeline import verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-missing-note", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue, note_id="missing-note")

        with self.assertRaisesRegex(ValueError, "archived note in the configured store"):
            verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                         time.monotonic() + 30, self.root,
                                         self.candidate_issue_evidence(issue))

    def test_candidate_integration_rejects_archived_frontmatter_routing_mismatch(self):
        from learning_pipeline import verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-bad-archive", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue)
        archived = self.root / "processed" / "candidate-note.md"
        archived.write_text(self.app.render_frontmatter(
            {"id": "candidate-note", "prevention_targets": json.dumps([str(self.directory / "other.md")]),
             "detection_targets": "[]"}, "# Candidate integration note\n"), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "routing metadata differs"):
            verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                         time.monotonic() + 30, self.root,
                                         self.candidate_issue_evidence(issue))

    def test_candidate_integration_requires_note_ids_in_current_issue_evidence(self):
        from learning_pipeline import verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-unlinked-note", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue)
        evidence_path = Path(issue["evidence"][0])
        evidence_path.write_text(json.dumps({"note_id": "unrelated-note"}), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Candidate note IDs must appear"):
            verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                         time.monotonic() + 10, self.root,
                                         self.candidate_issue_evidence(issue))

    def test_candidate_integration_rejects_changed_candidate_and_same_reviewer(self):
        from learning_pipeline import verify_candidate_integration

        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-integration-stale", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "targets": [self.ref(candidate_file)], "sources": [], "evidence": [str(self.report)],
                 "revalidation_required": False}
        record, candidate = self.candidate_integration_proof(candidate_root, issue)
        review_path = Path(record["candidate_integration"]["independent_review"]["path"])
        review = json.loads(review_path.read_text())
        review["reviewer"] = record["reviewer"]
        review_path.write_text(json.dumps(review), encoding="utf-8")
        record["candidate_integration"]["independent_review"] = self.ref(review_path)

        with self.assertRaisesRegex(ValueError, "Independent review must bind"):
            verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                         time.monotonic() + 30, self.root,
                                         self.candidate_issue_evidence(issue))

        record, candidate = self.candidate_integration_proof(candidate_root, issue)
        candidate_file.write_text(candidate_file.read_text() + "\nChanged after review.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current nonempty issue targets"):
            verify_candidate_integration(issue, record, candidate, self.root / "reports", {},
                                         time.monotonic() + 30, self.root,
                                         [{**self.ref(self.report), "resolved_path": str(self.report.resolve())}])

    def test_candidate_verified_cannot_bypass_missing_proof_or_user_decision(self):
        self.pipeline.begin("candidate-verified-negative", "noon", initial_inbox=[])
        self.pipeline.audit("candidate-verified-negative", self.audit_payload(False), [], True)
        self.pipeline.finish("candidate-verified-negative", "completed", "Verified", str(self.report), [])
        candidate_root, _changelog, candidate_file = self.release_candidate()
        issue = {"id": "candidate-verified-missing-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify candidate integration", "description": "Check archived target mapping.",
                 "sources": [], "targets": [self.ref(candidate_file)], "evidence": [str(self.report)]}
        self.pipeline.update_issue(issue)
        _payload, assessment = self.release_assessment(candidate_root, classification="candidate_verified")

        with self.assertRaisesRegex(ValueError, "Candidate verification needs"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        user_issue = {**issue, "status": "needs-user", "question": "Choose a target.",
                      "options": [{"id": "keep", "label": "Keep", "description": "Keep the current target."}]}
        from learning_pipeline import candidate_change_snapshot, verify_candidate_integration
        record = {"reviewer": "release assessor", "candidate_integration": None}
        with self.assertRaisesRegex(ValueError, "limited to current operational investigations"):
            verify_candidate_integration(user_issue, record, candidate_change_snapshot(candidate_root),
                                         self.root / "reports", {}, time.monotonic() + 5, self.root,
                                         self.candidate_issue_evidence(user_issue))

    def test_scoped_release_rejects_issue_state_key_alias_before_deferral(self):
        issue, _ledger = self.parser_ledger_open_finding("release-issue-key-alias")
        alias = "state-alias-" + issue["id"]
        with self.pipeline.lock():
            state = self.pipeline.load()
            state["issues"][alias] = state["issues"].pop(issue["id"])
            self.pipeline.save(state)
        candidate_root, _changelog, _candidate_file = self.release_candidate()
        _payload, assessment = self.release_assessment(candidate_root)
        before = self.pipeline.status()

        with self.assertRaisesRegex(ValueError, "issue key and identity differ"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        self.assertEqual(self.pipeline.status()["issues"], before["issues"])

    def test_scoped_release_assessment_defers_audit_only_with_current_independent_review_and_user_authorization(self):
        issue = self.open_audit_finding("release-audit-deferral")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_audit_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        result = self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        self.assertEqual(result["release_assessment"]["deferred_issue_ids"], [issue["id"]])
        self.assertEqual(result["release_assessment"]["deferred_audit_issue_ids"], [issue["id"]])
        self.assertEqual(result["release_assessment"]["deferred_review_issue_ids"], [])
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "candidate")

    def test_scoped_release_audit_deferral_satisfies_current_parser_ledger_without_closing_issue(self):
        issue, ledger = self.parser_ledger_open_finding("release-parser-ledger-deferred-audit")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_audit_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        before = self.pipeline.status()
        before_issue = copy.deepcopy(before["issues"][issue["id"]])
        before_ledger = copy.deepcopy(before["issues"][ledger["id"]])

        result = self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        self.assertEqual(result["release_assessment"]["deferred_audit_issue_ids"], [issue["id"]])
        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before_issue)
        self.assertEqual(after["issues"][ledger["id"]], before_ledger)

    def test_scoped_release_parser_ledger_does_not_accept_missing_audit_receipt(self):
        issue, ledger, candidate_root, _payload, assessment, _review, _authorization = \
            self.parser_ledger_scoped_assessment("release-parser-ledger-no-receipt", attach_receipt=False)
        before = self.pipeline.status()
        with self.assertRaisesRegex(ValueError, "Audit independent review needs a saved report path"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def test_scoped_release_parser_ledger_rejects_stale_audit_authorization(self):
        issue, ledger, candidate_root, _payload, assessment, _review, authorization = \
            self.parser_ledger_scoped_assessment("release-parser-ledger-stale-receipt")
        before = self.pipeline.status()
        authorization.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Audit authorization report is stale"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def test_scoped_release_parser_ledger_rejects_receipt_for_another_finding(self):
        issue, ledger, candidate_root, payload, assessment, review_path, authorization_path = \
            self.parser_ledger_scoped_assessment("release-parser-ledger-wrong-issue")
        from learning_pipeline import fingerprint

        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["issue_id"] = "audit-another-finding"
        review_path.write_text(json.dumps(review), encoding="utf-8")
        review_ref = {"path": str(review_path), "sha256": hashlib.sha256(review_path.read_bytes()).hexdigest()}
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["independent_review"] = review_ref
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        record["audit_deferral"]["independent_review"] = review_ref
        record["audit_deferral"]["authorization"]["sha256"] = hashlib.sha256(
            authorization_path.read_bytes()).hexdigest()
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        before = self.pipeline.status()

        with self.assertRaisesRegex(ValueError, "Independent audit review must approve"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def test_scoped_release_parser_ledger_rejects_wrong_candidate_authorization(self):
        issue, ledger, candidate_root, payload, assessment, _review, authorization_path = \
            self.parser_ledger_scoped_assessment("release-parser-ledger-wrong-candidate")
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["candidate_sha256"] = "0" * 64
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        record["audit_deferral"]["authorization"]["sha256"] = hashlib.sha256(
            authorization_path.read_bytes()).hexdigest()
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        before = self.pipeline.status()

        with self.assertRaisesRegex(ValueError, "exact issue, candidate"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def assert_parser_ledger_issue_mutation_blocked(self, run_id, mutation, error):
        issue, ledger, candidate_root, _payload, assessment, _review, _authorization = \
            self.parser_ledger_scoped_assessment(run_id, mutate_issue=mutation)
        before = self.pipeline.status()
        with self.assertRaisesRegex(ValueError, error):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def test_scoped_release_parser_ledger_rejects_parser_version_mismatch(self):
        self.assert_parser_ledger_issue_mutation_blocked(
            "release-parser-version-mismatch", lambda issue: issue.update(parser_reviewed_version=1),
            "Parser reconciliation assessment changed")

    def test_scoped_release_parser_ledger_rejects_parser_target_mismatch(self):
        self.assert_parser_ledger_issue_mutation_blocked(
            "release-parser-target-mismatch", lambda issue: issue.update(parser_reviewed_targets=[]),
            "Parser reconciliation assessment changed")

    def test_scoped_release_parser_ledger_rejects_stale_ledger_report(self):
        issue, ledger, candidate_root, _payload, assessment, _review, _authorization = \
            self.parser_ledger_scoped_assessment("release-parser-ledger-stale-report")
        import learning_pipeline
        original = learning_pipeline.current_parser_reconciliation

        def stale_report(ref, affected, targets, version, refs, timeout=12):
            Path(ref["path"]).write_text("{}", encoding="utf-8")
            return original(ref, affected, targets, version, refs, timeout)

        before = self.pipeline.status()
        with (patch.object(learning_pipeline, "current_parser_reconciliation", side_effect=stale_report),
              self.assertRaisesRegex(ValueError, "Parser reconciliation evidence changed")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def test_scoped_release_parser_ledger_rejects_revalidation(self):
        self.assert_parser_ledger_issue_mutation_blocked(
            "release-parser-revalidation", lambda issue: issue.update(revalidation_required=True),
            "must remain blocking")

    def test_scoped_release_parser_ledger_rejects_needs_user(self):
        self.assert_parser_ledger_issue_mutation_blocked(
            "release-parser-needs-user", lambda issue: issue.update(status="needs-user"),
            "must remain blocking")

    def test_scoped_release_parser_ledger_block_classification_and_global_gate_stay_strict(self):
        issue, ledger, candidate_root, _payload, assessment, _review, _authorization = \
            self.parser_ledger_scoped_assessment("release-parser-ledger-block", classification="block",
                                                 attach_receipt=False)
        before = self.pipeline.status()
        with self.assertRaisesRegex(ValueError, "still has blocking issues"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        with self.assertRaisesRegex(ValueError, "Classify or revalidate outstanding issues"):
            self.pipeline.weekly_release_status()
        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])

    def test_scoped_release_parser_ledger_does_not_defer_operational_issue(self):
        issue, ledger = self.parser_ledger_open_finding("release-parser-ledger-operational")
        candidate_root, _, _ = self.release_candidate()
        operational = {"id": "unrelated-runtime", "kind": "operational", "status": "investigate",
                       "title": "Unrelated runtime check", "description": "Inspect an external runtime.",
                       "sources": [self.ref(self.note)], "targets": [self.ref(self.target)],
                       "evidence": [str(self.report)]}
        self.pipeline.update_issue(operational)
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_audit_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        before = self.pipeline.status()

        with self.assertRaisesRegex(ValueError, "Only open review findings may be deferred"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        after = self.pipeline.status()
        self.assertEqual(after["issues"][issue["id"]], before["issues"][issue["id"]])
        self.assertEqual(after["issues"][ledger["id"]], before["issues"][ledger["id"]])
        self.assertEqual(after["issues"][operational["id"]], before["issues"][operational["id"]])

    def test_scoped_release_assessment_defers_review_only_with_exact_independent_and_user_receipts(self):
        issue = self.open_release_review_issue("release-review-deferral", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_review_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        result = self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        release_result = result["release_assessment"]
        self.assertEqual(release_result["deferred_issue_ids"], [issue["id"]])
        self.assertEqual(release_result["deferred_audit_issue_ids"], [])
        self.assertEqual(release_result["deferred_review_issue_ids"], [issue["id"]])
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")

    def test_scoped_release_assessment_rejects_non_audit_deferral_without_user_receipt(self):
        issue = self.open_release_review_issue("release-review-no-receipt", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        _payload, assessment = self.release_assessment(candidate_root)
        with self.assertRaisesRegex(ValueError, "Review independent issue review needs a saved report path"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")

    def test_scoped_release_assessment_skeleton_allows_review_to_remain_blocking(self):
        issue = self.open_release_review_issue("release-review-remains-blocking", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root, classification="block")
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.assertEqual(record["review_deferral"], {
            "independent_review": {"path": "", "sha256": ""},
            "authorization": {"path": "", "sha256": ""}})
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        before = self.pipeline.status()["issues"][issue["id"]]

        with self.assertRaisesRegex(ValueError, "still has blocking issues"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

        after = self.pipeline.status()["issues"][issue["id"]]
        self.assertEqual(after, before)
        self.assertEqual(after["status"], "investigate")

    def test_scoped_release_assessment_rejects_wrong_candidate_review_deferral(self):
        issue = self.open_release_review_issue("release-review-wrong-candidate", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        _review_path, authorization_path = self.attach_review_deferral(record, issue, payload["candidate"])
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["candidate_sha256"] = "0" * 64
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        record["review_deferral"]["authorization"]["sha256"] = hashlib.sha256(
            authorization_path.read_bytes()).hexdigest()
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "exact issue, candidate"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_stale_review_deferral_evidence(self):
        issue = self.open_release_review_issue("release-review-stale-evidence", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_review_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        self.report.write_text("Changed after independent review.\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Issue evidence is stale"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_changed_review_source_or_target(self):
        issue = self.open_release_review_issue("release-review-stale-reference", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_review_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        self.target.write_text("Target changed after independent review.\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Review deferral requires current targets evidence"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_assessor_as_independent_review_author(self):
        issue = self.open_release_review_issue("release-review-same-reviewer", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        review_path, _authorization_path = self.attach_review_deferral(record, issue, payload["candidate"])
        review = json.loads(review_path.read_text(encoding="utf-8"))
        review["reviewer"] = record["reviewer"]
        review_path.write_text(json.dumps(review), encoding="utf-8")
        record["review_deferral"]["independent_review"]["sha256"] = hashlib.sha256(
            review_path.read_bytes()).hexdigest()
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Independent review must approve"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rechecks_review_receipts_after_verification(self):
        issue = self.open_release_review_issue("release-review-final-check", "unrelated-review")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        review_path, _authorization_path = self.attach_review_deferral(record, issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        import learning_pipeline
        original = learning_pipeline.candidate_change_snapshot
        calls = 0

        def mutate_review_after_async_checks(root, base_ref="origin/main"):
            nonlocal calls
            calls += 1
            if calls == 2:
                review_path.write_text("{}", encoding="utf-8")
            return original(root, base_ref)

        with (patch.object(learning_pipeline, "candidate_change_snapshot", side_effect=mutate_review_after_async_checks),
              self.assertRaisesRegex(ValueError, "Candidate or release assessment changed during verification")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_operational_deferral_even_with_review_receipt(self):
        self.pipeline.begin("release-operational-deferral", "noon", initial_inbox=[])
        self.pipeline.audit("release-operational-deferral", self.audit_payload(False), [], True)
        self.pipeline.finish("release-operational-deferral", "completed", "Verified", str(self.report), [])
        candidate_root, _, _ = self.release_candidate()
        issue = {"id": "unrelated-runtime", "kind": "operational", "status": "investigate",
                 "title": "Unrelated runtime check", "description": "Inspect an external runtime.",
                 "sources": [self.ref(self.note)], "targets": [self.ref(self.target)],
                 "evidence": [str(self.report)]}
        self.pipeline.update_issue(issue)
        current_issue = self.pipeline.status()["issues"][issue["id"]]
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_review_deferral(record, current_issue, payload["candidate"])
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "Only open review findings may be deferred"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_audit_deferral_without_authorization(self):
        issue = self.open_audit_finding("release-audit-no-authorization")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        self.attach_audit_deferral(record, issue, payload["candidate"])
        record["audit_deferral"]["authorization"] = None
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "authorization needs a saved report path"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_wrong_candidate_audit_authorization(self):
        issue = self.open_audit_finding("release-audit-wrong-candidate")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        _review_path, authorization_path = self.attach_audit_deferral(record, issue, payload["candidate"])
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        authorization["candidate_sha256"] = "0" * 64
        authorization_path.write_text(json.dumps(authorization), encoding="utf-8")
        record["audit_deferral"]["authorization"]["sha256"] = hashlib.sha256(
            authorization_path.read_bytes()).hexdigest()
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "exact issue, candidate"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_audit_authorization_changed_after_review(self):
        issue = self.open_audit_finding("release-audit-stale-authorization")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        record = next(item for item in payload["assessments"] if item["issue_id"] == issue["id"])
        _review_path, authorization_path = self.attach_audit_deferral(record, issue, payload["candidate"])
        authorization_path.write_text("{}", encoding="utf-8")
        assessment.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "stale, unavailable"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_allows_resolved_current_audit_cohort(self):
        issue, _ = self.resolved_audit_cohort("release-resolved-cohort")
        candidate_root, _, _ = self.release_candidate()
        payload, assessment = self.release_assessment(candidate_root)
        # Older saved assessments omit the optional review_deferral field.
        for record in payload["assessments"]:
            record.pop("review_deferral", None)
        assessment.write_text(json.dumps(payload), encoding="utf-8")
        result = self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(result["release_assessment"]["deferred_issue_ids"], [])
        self.assertEqual(result["release_assessment"]["reviewed_closed_cohort_ids"], [issue["id"]])

    def test_scoped_release_assessment_blocks_stale_or_missing_resolved_cohort_evidence(self):
        issue, other = self.resolved_audit_cohort("release-stale-cohort")
        candidate_root, _, _ = self.release_candidate()
        _, assessment = self.release_assessment(candidate_root)
        other.write_text(other.read_text() + "Changed after cohort closure.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current source and target evidence"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_blocks_missing_resolved_cohort_report(self):
        issue, _ = self.resolved_audit_cohort("release-missing-cohort-report")
        candidate_root, _, _ = self.release_candidate()
        _, assessment = self.release_assessment(candidate_root)
        Path(issue["latest_audit"]["path"]).unlink()
        with self.assertRaisesRegex(ValueError, "Issue evidence path is unavailable"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rejects_changed_issue_evidence_at_initial_check(self):
        self.pipeline.begin("release-issue-evidence-initial", "noon", initial_inbox=[])
        self.pipeline.audit("release-issue-evidence-initial", self.audit_payload(False), [], True)
        self.pipeline.finish("release-issue-evidence-initial", "completed", "Verified", str(self.report), [])
        candidate_root, _, _ = self.release_candidate()
        self.pipeline.update_issue({"id": "evidence-review", "kind": "operational", "status": "investigate",
                                    "title": "Evidence review", "description": "Inspect this saved evidence.",
                                    "sources": [], "targets": [self.ref(self.target)],
                                    "evidence": [str(self.report)]})
        _, assessment = self.release_assessment(candidate_root)
        self.report.write_text("Changed after template generation.\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Issue evidence is stale"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rechecks_issue_evidence_after_verification(self):
        candidate_root, _, _ = self.release_candidate()
        issue, _payload, assessment = self.audit_assessment(candidate_root, "release-issue-evidence-final")
        evidence = Path(issue["evidence"][0])
        import learning_pipeline
        original = learning_pipeline.candidate_change_snapshot
        calls = 0

        def delete_evidence_after_async_checks(root, base_ref="origin/main"):
            nonlocal calls
            calls += 1
            if calls == 2:
                evidence.unlink()
            return original(root, base_ref)

        with (patch.object(learning_pipeline, "candidate_change_snapshot", side_effect=delete_evidence_after_async_checks),
              self.assertRaisesRegex(ValueError, "Candidate or release assessment changed during verification")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_cannot_defer_bound_post_release_obligation(self):
        self.pipeline.begin("release-assessment-post-release", "noon", initial_inbox=[])
        self.pipeline.audit("release-assessment-post-release", self.audit_payload(False), [], True)
        self.pipeline.finish("release-assessment-post-release", "completed", "Verified", str(self.report), [])
        candidate_root, changelog, candidate = self.release_candidate()
        issue = {"id": "publication-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify publication", "description": "Confirm the actual published release.",
                 "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(issue)
        followup = {"version": "1.4.0", "reason": "Published evidence is unavailable before publication.",
                    "evidence": str(self.report), "candidate_changelog": self.ref(changelog),
                    "candidate_targets": [self.ref(candidate)],
                    "authorization": self.authorize_post_release(issue["id"], candidate_root)}
        self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        _, assessment = self.release_assessment(candidate_root, classification="defer")
        with self.assertRaisesRegex(ValueError, "post-release obligation"):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_default_release_status_rechecks_post_release_targets_after_async_verification(self):
        self.pipeline.begin("release-post-target-race", "noon", initial_inbox=[])
        self.pipeline.audit("release-post-target-race", self.audit_payload(False), [], True)
        self.pipeline.finish("release-post-target-race", "completed", "Verified", str(self.report), [])
        candidate_root, changelog, candidate = self.release_candidate()
        issue = {"id": "publication-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify publication", "description": "Confirm the actual published release.",
                 "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(issue)
        followup = {"version": "1.4.0", "reason": "Published evidence is unavailable before publication.",
                    "evidence": str(self.report), "candidate_changelog": self.ref(changelog),
                    "candidate_targets": [self.ref(candidate)],
                    "authorization": self.authorize_post_release(issue["id"], candidate_root)}
        self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        import learning_pipeline
        original = learning_pipeline.current_release_ref
        checked_paths = []
        candidate_ref_path = str(candidate.resolve())

        def mutate_candidate_target_before_final_check(ref):
            checked_paths.append(ref["path"])
            if ref["path"] == candidate_ref_path:
                candidate.write_text("Changed after async evidence checks.\\n", encoding="utf-8")
            return original(ref)

        with patch.object(learning_pipeline, "current_release_ref", side_effect=mutate_candidate_target_before_final_check):
            try:
                self.pipeline.weekly_release_status("1.4.0", candidate_root)
            except ValueError as exc:
                self.assertIn("Post-release target or changelog evidence changed", str(exc))
            else:
                self.fail(f"Post-release refs were not rechecked; observed refs: {checked_paths}")

    def test_scoped_release_assessment_rechecks_candidate_after_evidence_verification(self):
        candidate_root, changelog, _ = self.release_candidate()
        _issue, _payload, assessment = self.audit_assessment(candidate_root, "release-assessment-race")
        import learning_pipeline
        original = learning_pipeline.candidate_change_snapshot
        calls = 0

        def mutate_after_first_check(root, base_ref="origin/main"):
            nonlocal calls
            calls += 1
            if calls == 2:
                changelog.write_text(changelog.read_text() + "\nConcurrent candidate edit.\n", encoding="utf-8")
            return original(root, base_ref)

        with (patch.object(learning_pipeline, "candidate_change_snapshot", side_effect=mutate_after_first_check),
              self.assertRaisesRegex(ValueError, "changed during verification")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)

    def test_scoped_release_assessment_rechecks_pipeline_state_after_candidate_checks(self):
        candidate_root, _, _ = self.release_candidate()
        issue, _payload, assessment = self.audit_assessment(candidate_root, "release-assessment-state-race")
        import learning_pipeline
        original = learning_pipeline.candidate_change_snapshot
        calls = 0

        def update_issue_during_final_candidate_check(root, base_ref="origin/main"):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.pipeline.update_issue({"id": issue["id"],
                                            "description": "Changed after the earlier state check."})
            return original(root, base_ref)

        with (patch.object(learning_pipeline, "candidate_change_snapshot",
                           side_effect=update_issue_during_final_candidate_check),
              self.assertRaisesRegex(ValueError, "Pipeline state changed during release verification")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(calls, 2)

    def test_scoped_release_assessment_rechecks_deferred_evidence_after_verification(self):
        candidate_root, _, _ = self.release_candidate()
        _issue, _payload, assessment = self.audit_assessment(candidate_root, "release-assessment-evidence-race")
        import learning_pipeline
        original = learning_pipeline._secure_scoped_ref
        calls = 0

        def mutate_after_first_check(ref, timeout=8):
            nonlocal calls
            if ref["path"] == str(self.target.resolve()):
                calls += 1
                if calls == 2:
                    self.target.write_text("# Instructions\\n\\nChanged during release verification.\\n")
            return original(ref, timeout)

        with (patch.object(learning_pipeline, "_secure_scoped_ref", side_effect=mutate_after_first_check),
              self.assertRaisesRegex(ValueError, "changed during verification")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(calls, 2)

    def test_scoped_release_assessment_rejects_deferred_evidence_parent_symlink_swap(self):
        candidate_root, _, _ = self.release_candidate()
        evidence_parent = self.directory / "evidence-parent"
        evidence_parent.mkdir()
        evidence = evidence_parent / "target.md"
        evidence.write_text("Current target bytes.\\n", encoding="utf-8")
        outside = self.directory / "outside-evidence"
        outside.mkdir()
        (outside / "target.md").write_text("Current target bytes.\\n", encoding="utf-8")
        self.target = evidence
        _issue, _payload, assessment = self.audit_assessment(candidate_root, "release-assessment-parent-swap")
        import learning_pipeline
        original = learning_pipeline._secure_scoped_ref
        calls = 0
        evidence_ref_path = str(evidence.resolve())

        def swap_after_initial_verification(ref, timeout=8):
            nonlocal calls
            if ref["path"] == evidence_ref_path:
                calls += 1
                if calls == 2:
                    os.rename(evidence_parent, self.directory / "evidence-original")
                    os.symlink(outside, evidence_parent, target_is_directory=True)
            return original(ref, timeout)

        with (patch.object(learning_pipeline, "_secure_scoped_ref", side_effect=swap_after_initial_verification),
              self.assertRaisesRegex(ValueError, "changed during verification")):
            self.pipeline.weekly_release_status(assessment_path=str(assessment), candidate_root=candidate_root)
        self.assertEqual(calls, 2)

    def test_post_release_verification_is_open_and_bound_to_the_candidate(self):
        self.pipeline.begin("release-cycle", "midnight", initial_inbox=[])
        self.pipeline.audit("release-cycle", self.audit_payload(False), [], True)
        self.pipeline.finish("release-cycle", "completed", "Verified", str(self.report), [])
        candidate_root, changelog, candidate = self.release_candidate()
        issue = {"id": "publication-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify publication", "description": "Confirm the actual published release.",
                 "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(issue)
        followup = {"version": "1.4.0", "reason": "Published evidence cannot exist before publication.",
                    "evidence": str(self.report), "candidate_changelog": self.ref(changelog),
                    "candidate_targets": [self.ref(candidate)],
                    "authorization": self.authorize_post_release(issue["id"], candidate_root)}
        self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        with self.assertRaisesRegex(ValueError, "release version"):
            self.pipeline.weekly_release_status()
        with self.assertRaisesRegex(ValueError, "release version"):
            self.pipeline.weekly_release_status("1.5.0")
        with self.assertRaisesRegex(ValueError, "candidate checkout"):
            self.pipeline.weekly_release_status("1.4.0", self.root)
        result = self.pipeline.weekly_release_status("1.4.0", candidate_root)
        self.assertEqual([x["id"] for x in result["post_release_checks"]], [issue["id"]])
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")
        subprocess.run(["git", "-C", str(candidate_root), "remote", "set-url", "origin",
                        "https://github.com/example/other.git"], check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "checkout identity changed"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)
        subprocess.run(["git", "-C", str(candidate_root), "remote", "set-url", "origin",
                        "https://github.com/example/project.git"], check=True, capture_output=True)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-release-status",
                                                  "--release-version", "1.4.0"])
        with patch("pathlib.Path.cwd", return_value=candidate_root), redirect_stdout(io.StringIO()) as output:
            self.app.command_pipeline(args)
        self.assertEqual(json.loads(output.getvalue())["post_release_checks"][0]["id"], issue["id"])
        saved_state = self.pipeline.load()
        altered = copy.deepcopy(saved_state)
        altered["issues"][issue["id"]]["post_release"]["reason"] = "Changed without reassessment."
        with self.pipeline.lock():
            self.pipeline.save(altered)
        with self.assertRaisesRegex(ValueError, "outstanding issues"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)
        with self.pipeline.lock():
            self.pipeline.save(saved_state)
        unrelated = copy.deepcopy(saved_state)
        unrelated["issues"]["pre-release-defect"] = {"id": "pre-release-defect", "kind": "operational",
                                                       "status": "investigate"}
        with self.pipeline.lock():
            self.pipeline.save(unrelated)
        with self.assertRaisesRegex(ValueError, "outstanding issues"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)
        with self.pipeline.lock():
            self.pipeline.save(saved_state)
        with patch("learning_pipeline.verified_published_release", side_effect=ValueError("published release unavailable")):
            with self.assertRaisesRegex(ValueError, "published release"):
                self.pipeline.update_issue({"id": issue["id"], "status": "resolved",
                                            "resolution": "Claimed success.", "evidence": [str(self.report)]})
        with self.assertRaisesRegex(ValueError, "cannot be removed"):
            self.pipeline.update_issue({"id": issue["id"], "post_release": None})
        head = subprocess.check_output(["git", "-C", str(candidate_root), "rev-parse", "HEAD"], text=True).strip()
        subprocess.run(["git", "-C", str(candidate_root), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "changed candidate"],
                       check=True, capture_output=True)
        with self.assertRaisesRegex(ValueError, "checkout identity changed"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)
        subprocess.run(["git", "-C", str(candidate_root), "switch", "--detach", head], check=True, capture_output=True)
        self.pipeline.update_issue({"id": issue["id"], "description": "The verification scope changed."})
        with self.assertRaisesRegex(ValueError, "outstanding issues"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)
        self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        self.report.write_text("# Changed evidence\n")
        with self.assertRaisesRegex(ValueError, "Post-release evidence"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)
        self.report.write_text("# Verification\n\nThe complete instruction contract is preserved.\n")
        candidate.write_text("# Candidate changed\n")
        with self.assertRaisesRegex(ValueError, "Post-release evidence"):
            self.pipeline.weekly_release_status("1.4.0", candidate_root)

    def test_post_release_cannot_hide_user_or_audit_decisions(self):
        candidate = self.directory / "candidate.py"
        candidate.write_text("# Candidate\n")
        changelog = self.directory / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [1.4.0] - 2026-11-15\n")
        followup = {"version": "1.4.0", "reason": "Later.", "evidence": str(self.report),
                    "candidate_changelog": self.ref(changelog), "candidate_targets": [self.ref(candidate)],
                    "authorization": str(self.report)}
        decision = self.decision()
        decision["post_release"] = followup
        with self.assertRaisesRegex(ValueError, "operational investigation"):
            self.pipeline.update_issue(decision)
        decision.update(id="audit-finding", kind="audit", status="investigate")
        with self.assertRaisesRegex(ValueError, "operational investigation"):
            self.pipeline.update_issue(decision)
        migration = {"id": "audit-parser-v2-reconciliation", "kind": "operational", "status": "investigate",
                     "title": "Parser migration", "description": "Reconcile parser findings.",
                     "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(migration)
        with self.assertRaisesRegex(ValueError, "operational investigation"):
            self.pipeline.update_issue({"id": migration["id"], "post_release": followup})
        user_choice = self.decision()
        user_choice.update(id="operational-choice", kind="operational")
        self.pipeline.update_issue(user_choice)
        with self.assertRaisesRegex(ValueError, "operational investigation"):
            self.pipeline.update_issue({"id": user_choice["id"], "status": "investigate", "post_release": followup})
        self.pipeline.update_issue({"id": user_choice["id"], "status": "investigate"})
        with self.assertRaisesRegex(ValueError, "operational investigation"):
            self.pipeline.update_issue({"id": user_choice["id"], "post_release": followup})
        unclassified = {**migration, "id": "unclassified", "status": "candidate"}
        self.pipeline.update_issue(unclassified)
        self.pipeline.update_issue({"id": unclassified["id"], "status": "investigate"})
        with self.assertRaisesRegex(ValueError, "operational investigation"):
            self.pipeline.update_issue({"id": unclassified["id"], "post_release": followup})

    def test_post_release_requires_current_candidate_coverage_and_version(self):
        candidate_root, changelog, candidate = self.release_candidate()
        issue = {"id": "release-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify publication", "description": "Verify after publication.",
                 "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(issue)
        followup = {"version": "1.4.0", "reason": "Published readback is required.",
                    "evidence": str(self.report), "candidate_changelog": self.ref(changelog),
                    "candidate_targets": [self.ref(candidate)],
                    "authorization": self.authorize_post_release(issue["id"], candidate_root)}
        with patch("learning_pipeline.check_external_ref", return_value=False):
            with self.assertRaisesRegex(ValueError, "current candidate evidence"):
                self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        self.assertNotIn("post_release", self.pipeline.status()["issues"][issue["id"]])
        for key, value in (("version", "1.3.0"), ("reason", ""), ("evidence", str(candidate)),
                           ("candidate_targets", []), ("authorization", str(self.report))):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.pipeline.update_issue({"id": issue["id"], "post_release": {**followup, key: value}})
        wrong_authorization = self.authorize_post_release("another-issue", candidate_root)
        with self.assertRaisesRegex(ValueError, "authorization does not cover"):
            self.pipeline.update_issue({"id": issue["id"], "post_release":
                                        {**followup, "authorization": wrong_authorization}})
        wrong_path = candidate_root / "unrelated.md"
        wrong_path.write_text(self.target.read_text())
        with self.assertRaisesRegex(ValueError, "candidate path"):
            self.pipeline.update_issue({"id": issue["id"], "post_release":
                                        {**followup, "candidate_targets": [self.ref(wrong_path)]}})
        candidate.write_text("# Different candidate\n")
        with self.assertRaisesRegex(ValueError, "candidate must cover"):
            self.pipeline.update_issue({"id": issue["id"], "post_release":
                                        {**followup, "candidate_targets": [self.ref(candidate)]}})
        with self.assertRaisesRegex(ValueError, "binding is generated"):
            self.pipeline.update_issue({"id": issue["id"], "post_release_binding": "forged"})

    def test_post_release_changelog_ignores_fences_and_comments(self):
        candidate_root, changelog, candidate = self.release_candidate()
        issue = {"id": "release-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify publication", "description": "Verify after publication.",
                 "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(issue)
        changelog.write_text("# Changelog\n\n```md\n## [1.4.0] - example\n```\n"
                             "<!--\n## [1.4.0] - hidden\n-->\n## [1.3.0] - actual\n")
        followup = {"version": "1.4.0", "reason": "Published readback is required.",
                    "evidence": str(self.report), "candidate_changelog": self.ref(changelog),
                    "candidate_targets": [self.ref(candidate)],
                    "authorization": self.authorize_post_release(issue["id"], candidate_root)}
        with self.assertRaisesRegex(ValueError, "version must match"):
            self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        changelog.write_text("# Changelog\n\n<pre>\n## [1.4.0] - example\n</pre>\n## [1.3.0] - actual\n")
        with self.assertRaisesRegex(ValueError, "changelog version is unavailable or ambiguous"):
            self.pipeline.update_issue({"id": issue["id"], "post_release":
                                        {**followup, "candidate_changelog": self.ref(changelog)}})
        changelog.write_text("`<!--`\n## [1.4.0] - actual\n-->\n## [1.3.0] - older\n")
        with self.assertRaisesRegex(ValueError, "changelog version is unavailable or ambiguous"):
            self.pipeline.update_issue({"id": issue["id"], "post_release":
                                        {**followup, "candidate_changelog": self.ref(changelog)}})
        changelog.write_text("\\<!--\n## [1.4.0] - actual\n-->\n## [1.3.0] - older\n")
        with self.assertRaisesRegex(ValueError, "changelog version is unavailable or ambiguous"):
            self.pipeline.update_issue({"id": issue["id"], "post_release":
                                        {**followup, "candidate_changelog": self.ref(changelog)}})

    def test_post_release_closure_requires_and_preserves_publication_proof(self):
        candidate_root, changelog, candidate = self.release_candidate()
        issue = {"id": "published-proof", "kind": "operational", "status": "investigate",
                 "title": "Verify publication", "description": "Verify the published release.",
                 "targets": [self.ref(self.target)], "sources": []}
        self.pipeline.update_issue(issue)
        followup = {"version": "1.4.0", "reason": "Readback is available only after publication.",
                    "evidence": str(self.report), "candidate_changelog": self.ref(changelog),
                    "candidate_targets": [self.ref(candidate)],
                    "authorization": self.authorize_post_release(issue["id"], candidate_root)}
        self.pipeline.update_issue({"id": issue["id"], "post_release": followup})
        with patch("learning_pipeline.verified_published_release", side_effect=ValueError("unverified published release")):
            with self.assertRaisesRegex(ValueError, "unverified published release"):
                self.pipeline.update_issue({"id": issue["id"], "status": "resolved",
                                            "resolution": "Verified.", "evidence": [str(self.report)]})
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")
        proof = {"tag": "v1.4.0", "commit": "1" * 40, "release_id": 1}
        with patch("learning_pipeline.verified_published_release",
                   side_effect=[proof, {**proof, "release_id": 2}]):
            with self.assertRaisesRegex(ValueError, "readback changed"):
                self.pipeline.update_issue({"id": issue["id"], "status": "resolved",
                                            "resolution": "Verified.", "evidence": [str(self.report)]})
        self.assertEqual(self.pipeline.status()["issues"][issue["id"]]["status"], "investigate")
        with patch("learning_pipeline.verified_published_release", return_value=proof):
            saved = self.pipeline.update_issue({"id": issue["id"], "status": "resolved",
                                               "resolution": "Published readback verified.",
                                               "evidence": [str(self.report)]})
        path = Path(saved["publication_proof"]["path"])
        self.assertEqual(json.loads(path.read_text())["release"], proof)
        reopened = self.pipeline.update_issue({"id": issue["id"], "status": "investigate"})
        self.assertIsNone(reopened["post_release_binding"])

    def test_published_release_readback_requires_matching_tag_and_nondraft_release(self):
        from learning_pipeline import verified_published_release
        candidate = {"root": str(self.directory), "common_dir": str(self.directory / ".git"), "head": "1" * 40,
                     "remote": {"host": "github.com", "repository": "example/project"}}
        changelog = self.directory / "CHANGELOG.md"
        changelog.write_text("# Changelog\n\n## [1.4.0] - 2026-11-15\n")
        post = {"version": "1.4.0", "candidate_git": candidate,
                "candidate_targets": [self.ref(self.target)], "candidate_changelog": self.ref(changelog)}
        release = {"id": 42, "tag_name": "v1.4.0", "draft": False, "prerelease": False,
                   "published_at": "2026-11-15T17:00:00Z",
                   "html_url": "https://github.com/example/project/releases/tag/v1.4.0"}
        remote_tag = "1" * 40
        peeled = None
        bad_blob = False

        def run(command, **kwargs):
            self.assertTrue(kwargs.get("timeout"))
            if "cat-file" in command:
                file_path = self.directory / command[-1].split(":", 1)[1]
                content = file_path.read_bytes()
                if "-s" in command:
                    return subprocess.CompletedProcess(command, 0, stdout=str(len(content)), stderr="")
                return subprocess.CompletedProcess(command, 0, stdout=b"x" * len(content) if bad_blob else content, stderr=b"")
            if command[0] == "gh":
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps(release), stderr="")
            if "get-url" in command:
                return subprocess.CompletedProcess(command, 0, stdout="git@github.com:example/project.git\n", stderr="")
            self.assertIn("git@github.com:example/project.git", command)
            self.assertNotIn("origin", command)
            output = remote_tag + "\trefs/tags/v1.4.0\n"
            if peeled:
                output += peeled + "\trefs/tags/v1.4.0^{}\n"
            return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

        with patch("learning_pipeline.candidate_release_identity", return_value=candidate), \
             patch("learning_pipeline.subprocess.run", side_effect=run):
            result = verified_published_release(post)
            self.assertEqual(result["commit"], candidate["head"])
            remote_tag, peeled = "3" * 40, "1" * 40
            self.assertEqual(verified_published_release(post)["tag_object"], remote_tag)
            peeled = None
            bad_blob = True
            with self.assertRaisesRegex(ValueError, "published release"):
                verified_published_release(post)
            bad_blob = False
            remote_tag = "2" * 40
            with self.assertRaisesRegex(ValueError, "published release"):
                verified_published_release(post)
            remote_tag = "1" * 40
            release["draft"] = True
            with self.assertRaisesRegex(ValueError, "published release"):
                verified_published_release(post)
    def test_weekly_failure_is_retryable_and_target_verification_is_required(self):
        self.pipeline.begin("failed-run", "midnight")
        self.pipeline.finish("failed-run", "failed", "Audit storage was unavailable", "", [])
        self.assertTrue(self.pipeline.status()["weekly_due"])
        self.pipeline.begin("retry-run", "noon", initial_inbox=[])
        self.pipeline.audit("retry-run", self.audit_payload(False), [], True)
        with self.assertRaisesRegex(ValueError, "post-consolidation audit"):
            self.pipeline.finish("retry-run", "completed", "Done", str(self.report), [str(self.target)])
        self.pipeline.audit("retry-run", self.audit_payload(False), [], False)
        self.pipeline.finish("retry-run", "completed", "Verified", str(self.report), [str(self.target)])
        self.assertEqual(self.pipeline.weekly_release_status()["consolidation"]["id"], "retry-run")
        parser_report = Path(self.pipeline.status()["audit_rule_parser_snapshot"]["path"])
        report_bytes = parser_report.read_bytes()
        parser_report.write_bytes(report_bytes + b" ")
        with self.assertRaisesRegex(ValueError, "rule parser audit evidence changed"):
            self.pipeline.weekly_release_status()
        parser_report.write_bytes(report_bytes)
        for invalid in (None, True, 2.0, 1):
            with self.subTest(parser_version=invalid):
                with self.pipeline.lock():
                    state = self.pipeline.load()
                    state["audit_rule_parser_version"] = invalid
                    self.pipeline.save(state)
                with self.assertRaisesRegex(ValueError, "current rule parser"):
                    self.pipeline.weekly_release_status()

    def test_release_rechecks_all_full_parser_inputs_not_only_run_targets(self):
        other = self.directory / "additional.md"
        other.write_text("# Instructions\n\n- Preserve independent source evidence.\n")
        refs = [self.ref(self.target), self.ref(other)]
        self.pipeline.begin("parser-coverage", "noon", refs, [])
        full = self.audit_payload(False)
        full["file_snapshots"] = refs
        self.pipeline.audit("parser-coverage", full, [], True)
        delta = {**full, "comparison_scope": "changed-targets"}
        self.pipeline.audit("parser-coverage", delta, [], False)
        self.pipeline.finish("parser-coverage", "completed", "Verified", str(self.report), [])
        self.assertEqual(self.pipeline.weekly_release_status()["consolidation"]["id"], "parser-coverage")
        other.write_text(other.read_text() + "\nChanged after full audit.\n")
        with self.assertRaisesRegex(ValueError, "rule parser audit evidence changed"):
            self.pipeline.weekly_release_status()

    def test_changed_inputs_cannot_be_accepted_as_a_post_audit(self):
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        payload = self.audit_payload(False)
        self.target.write_text("# Changed\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.pipeline.audit("run-1", payload, [], True)

    def test_concurrent_begins_admit_only_one_owner(self):
        def begin(i):
            try:
                self.pipeline.begin(f"run-{i}", "midnight")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(begin, range(4))), 1)

    def test_private_state_and_store_escape(self):
        self.assertEqual((self.root / "state" / "pipeline.json").stat().st_mode & 0o777, 0o600)
        outside = self.directory / "outside"
        outside.mkdir()
        other_root = self.directory / "other"
        other_root.mkdir()
        (other_root / "state").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(self.app.ConfigError, "outside"):
            self.app.pipeline_store(other_root).status()
        self.assertEqual(list(outside.iterdir()), [])

    def test_explicit_human_note_decision_suppresses_the_old_question(self):
        self.pipeline.update_issue(self.decision())
        self.note.write_text(self.note.read_text().replace("[ ] Approve", "[x] Approve"))
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes()))

    def test_a_grouped_decision_cannot_hide_remaining_pending_sources(self):
        original = self.note.read_text()
        second = self.note.with_name("second.md")
        second.write_text(original.replace('id: "note-1"', 'id: "note-2"'))
        for closed in ("approved", "rejected", "archived"):
            with self.subTest(closed=closed):
                self.note.write_text(original)
                issue = self.decision()
                issue["sources"].append({**self.ref(second), "note_id": "note-2"})
                self.pipeline.update_issue(issue)
                if closed == "archived":
                    self.note.unlink()
                else:
                    label = "Approve" if closed == "approved" else "Reject"
                    self.note.write_text(original.replace("[ ] " + label, "[x] " + label))
                digest = self.pipeline.digest(self.pipeline.status(), self.notes())
                self.assertIsNotNone(digest)
                self.assertEqual(digest["items"][0]["note_ids"], ["note-2"])
                self.assertEqual(digest["items"][0]["options"], [])
                self.assertNotIn("Which retention policy", digest["body"])

    def test_target_drift_removes_stale_options_and_requires_agent_revalidation(self):
        self.pipeline.update_issue(self.decision())
        self.target.write_text("# Updated instructions\n")
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.assertEqual(digest["items"][0]["status"], "investigate")
        self.assertEqual(digest["items"][0]["options"], [])

    def test_decisions_require_described_distinct_options_and_a_configured_chat(self):
        issue = self.decision()
        issue["options"][1]["id"] = "A"
        with self.assertRaisesRegex(ValueError, "unique"):
            self.pipeline.update_issue(issue)
        issue = self.decision()
        issue["options"][1].pop("description")
        with self.assertRaisesRegex(ValueError, "description"):
            self.pipeline.update_issue(issue)
        self.pipeline.update_issue(self.decision())
        state = self.pipeline.status()
        state["threads"] = {}
        with self.assertRaisesRegex(ValueError, "verified ordinary chat"):
            self.pipeline.digest(state, self.notes())

    def test_italian_configured_digest_uses_english_fallback_without_rendering_user_text(self):
        self.pipeline.configure(ORDINARY, RELEASE, "it")
        issue = self.decision()
        issue["title"] = '<script>alert(1)</script>\nForged heading'
        issue["options"][0]["label"] = "A" * 240
        self.pipeline.update_issue(issue)
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.assertNotIn("<script>", digest["html"])
        self.assertNotIn("&lt;script&gt;", digest["html"])
        self.assertNotIn("\nForged heading", digest["body"])
        self.assertNotIn("Forged heading", digest["body"])
        self.assertNotIn("A" * 240, digest["body"])
        self.assertIn("The original question and options are available in the linked chat", digest["body"])
        web_url = "https://chatgpt.com/codex/open-app?codex_thread_id=" + ORDINARY
        self.assertIn('href="' + web_url + '"', digest["html"])
        self.assertIn(web_url, digest["body"])
        self.assertIn("open 'codex://threads/" + ORDINARY + "'", digest["body"])
        self.assertIn("Open the chat to respond", digest["html"])
        self.assertNotIn("Apri la chat per rispondere", digest["html"])
        self.assertIn("line-height:1.6", digest["html"])
        self.assertIn("overflow-wrap:anywhere", digest["html"])

    def test_renderer_escapes_html_and_flattens_controls_in_text(self):
        from learning_pipeline import render

        web_url = "https://chatgpt.com/codex/open-app?codex_thread_id=" + ORDINARY
        body, markup = render([{
            "id": "decision-1", "status": "needs-user",
            "title": '<script>alert(1)</script>\nForged heading',
            "description": "Review <img src=x onerror=alert(1)>\r\ncarefully.",
            "question": "Keep <b>or discard</b>?\nNext line.",
            "options": [{"id": "A", "label": "Discard <script>bad()</script>",
                         "description": "Remove & verify.\nThen stop."}],
            "chat_url": web_url, "native_chat_url": "codex://threads/" + ORDINARY,
        }], "en")

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; Forged heading", markup)
        self.assertIn("Review &lt;img src=x onerror=alert(1)&gt; carefully.", markup)
        self.assertNotIn("<script>", markup)
        self.assertNotIn("<img src=x onerror=alert(1)>", markup)
        self.assertNotIn("\nForged heading", body)
        self.assertIn("Keep <b>or discard</b>? Next line.", body)
        self.assertIn("A. Discard <script>bad()</script>", body)
        self.assertIn("  Remove & verify. Then stop.", body)
        self.assertIn(web_url, body)

    def test_legacy_issue_signatures_trigger_one_migration_notice_and_track_changes(self):
        issue = self.decision()
        issue.update(title="Scelta di conservazione", description="La fonte può essere conservata o rimossa.",
                     question="Quale politica devo seguire?",
                     options=[{"id": "A", "label": "Conserva", "description": "Mantieni la fonte.", "recommended": True},
                              {"id": "B", "label": "Elimina", "description": "Rimuovi la fonte."}])
        self.pipeline.update_issue(issue)
        now = datetime.now().astimezone()
        state = self.pipeline.status()
        digest = self.pipeline.digest(state, self.notes(), now)
        self.assertEqual(len(digest["items"]), 1)
        native_url = "codex://threads/" + ORDINARY
        https_url = "https://chatgpt.com/codex/open-app?codex_thread_id=" + ORDINARY
        previous_dynamic_item = {
            "id": issue["id"], "status": "needs-user", "title": "Choose the retention policy",
            "description": "The source can be kept or removed.",
            "options": [{"id": "A", "label": "Keep", "description": "Retain the source.", "recommended": True},
                        {"id": "B", "label": "Remove", "description": "Delete the source.", "recommended": False}],
            "question": "Which retention policy should apply?",
        }

        # The prior native-link signature included the old dynamic decision text.
        old_native_item = {**previous_dynamic_item, "chat_url": native_url}
        state["sent"][issue["id"]] = {"signature": self.app.fingerprint(old_native_item),
                                     "at": now.isoformat(), "receipt": "previous-native-send"}
        migration = self.pipeline.digest(state, self.notes(), now)
        self.assertIsNotNone(migration)
        self.pipeline.reserve(migration, lambda value: self.pipeline.digest(value, self.notes(), now))
        self.pipeline.acknowledge(migration["digest_id"], "fixture migration receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes(), now))

        # The prior HTTPS-link signature also carried the native URL helper; it still gets one migration send.
        old_https_item = {**previous_dynamic_item, "chat_url": https_url, "native_chat_url": native_url}
        changed = {**issue, "question": "Quale politica aggiornata devo seguire?",
                   "options": [{"id": "A", "label": "Mantieni", "description": "Conserva la fonte.", "recommended": True},
                               {"id": "B", "label": "Rimuovi", "description": "Elimina la fonte."}]}
        self.pipeline.update_issue(changed)
        state = self.pipeline.status()
        state["sent"][issue["id"]] = {"signature": self.app.fingerprint(old_https_item),
                                     "at": now.isoformat(), "receipt": "previous-https-send"}
        updated_digest = self.pipeline.digest(state, self.notes(), now)
        self.assertIsNotNone(updated_digest)
        self.assertNotIn("Quale politica aggiornata", updated_digest["body"])
        self.assertNotIn("Mantieni", updated_digest["html"])
        self.pipeline.reserve(updated_digest, lambda value: self.pipeline.digest(value, self.notes(), now))
        self.pipeline.acknowledge(updated_digest["digest_id"], "fixture updated migration receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes(), now))

    def test_legacy_non_issue_signature_remains_deduplicated(self):
        now = datetime.now().astimezone()
        state = self.pipeline.status()
        inbox = [{"id": "legacy-backlog-note", "created_at": (now - timedelta(days=2)).isoformat()}]
        digest = self.pipeline.digest(state, [], now, inbox=inbox)
        item = dict(digest["items"][0])
        item.pop("signature")
        item.pop("email_language")
        state["sent"][item["id"]] = {"signature": self.app.fingerprint(item),
                                     "at": now.isoformat(), "receipt": "previous-send"}
        self.assertIsNone(self.pipeline.digest(state, [], now, inbox=inbox))

    def test_delivery_acknowledgment_and_weekly_reminder(self):
        self.pipeline.update_issue(self.decision())
        now = datetime.now().astimezone()
        state = self.pipeline.status()
        digest = self.pipeline.digest(state, self.notes(), now)
        self.pipeline.reserve(digest)
        with self.assertRaisesRegex(ValueError, "no delivery receipt"):
            self.pipeline.reserve(digest)
        self.pipeline.acknowledge(digest["digest_id"], "gmail:verified-message-id")
        state = self.pipeline.status()
        self.assertIsNone(self.pipeline.digest(state, self.notes(), now + timedelta(minutes=1)))
        self.assertIsNotNone(self.pipeline.digest(state, self.notes(), now + timedelta(days=8)))
        with self.assertRaisesRegex(ValueError, "already acknowledged"):
            self.pipeline.reserve(digest)

    def test_reservation_revalidates_current_sources(self):
        self.pipeline.update_issue(self.decision())
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.note.write_text(self.note.read_text().replace("[ ] Reject", "[x] Reject"))
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.pipeline.reserve(digest, lambda state: self.pipeline.digest(state, self.notes()))

    def test_error_is_reported_without_pending_review_and_success_clears_it(self):
        self.note.unlink()
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        self.pipeline.finish("run-1", "failed", "Complete queue could not be read", "", [])
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.assertIn("The consolidation needs attention. Open the linked chat for details.", digest["body"])
        self.assertNotIn("queue could not be read", digest["body"])
        self.pipeline.begin("run-2", "midnight", initial_inbox=[])
        self.pipeline.audit("run-2", self.audit_payload(False), [], True)
        self.pipeline.finish("run-2", "completed", "Verified", str(self.report), [])
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes()))

    def test_digest_reports_old_inbox_and_revalidates_it_before_reservation(self):
        self.note.unlink()
        now = datetime.now().astimezone()
        inbox = [{"id": "old", "created_at": (now - timedelta(days=2)).isoformat()}]
        digest = self.pipeline.digest(self.pipeline.status(), [], now, inbox=inbox)
        self.assertIn("1", digest["body"])
        self.assertIn("inbox", digest["body"].lower())
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.pipeline.reserve(digest, lambda state: self.pipeline.digest(state, [], now, inbox=[]))
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now, inbox=[
            {"id": "new", "created_at": now.isoformat()}
        ]))
        unknown = self.pipeline.digest(self.pipeline.status(), [], now, inbox=[
            {"id": "invalid", "created_at": "not-a-date"}
        ])
        self.assertIn("unknown creation time", unknown["body"])

    def test_notification_body_reads_inbox_without_review_notes(self):
        self.note.unlink()
        (self.root / "inbox" / "old.md").write_text(
            '---\nid: "inbox-1"\ncreated_at: "2020-01-01T00:00:00+00:00"\n'
            'status: "inbox"\n---\n# Pending\n'
        )
        payload = self.app.notification_body({}, self.root)
        self.assertIsNotNone(payload)
        self.assertIn("1 inbox note", payload[1])

    def test_notification_does_not_reserve_an_incomplete_inventory(self):
        self.pipeline.update_issue(self.decision())
        args = self.app.build_parser().parse_args(["--config", str(self.config), "notify", "--prepare"])
        with patch.object(self.app, "read_note_bounded", side_effect=TimeoutError("stalled")):
            with self.assertRaisesRegex(self.app.ConfigError, "inventory is incomplete"):
                self.app.command_notify(args)
        self.assertIsNone(self.pipeline.status()["outbox"])

    def test_msmtp_failure_preserves_the_pending_submission(self):
        self.pipeline.update_issue(self.decision())
        self.config.write_text(self.config.read_text().replace("user@example.test", "review@example.invalid"))
        args = self.app.build_parser().parse_args(["--config", str(self.config), "notify", "--send-msmtp"])
        with patch.object(self.app.shutil, "which", return_value="msmtp"), patch.object(self.app, "send_msmtp", side_effect=RuntimeError("unknown delivery")):
            with self.assertRaises(RuntimeError):
                self.app.command_notify(args)
        self.assertIsNotNone(self.pipeline.status()["outbox"])
        with self.assertRaisesRegex(self.app.ConfigError, "reconciliation"):
            self.app.command_notify(args)

    def test_mime_alternatives_and_header_injection_rejection(self):
        with patch.object(self.app.shutil, "which", return_value="msmtp"), patch.object(self.app.subprocess, "run") as run:
            self.app.send_msmtp("reviewer@example.invalid", "A decision", "Plain options", "<p>HTML options</p>")
            message = Parser(policy=policy.default).parsestr(run.call_args.kwargs["input"])
            self.assertEqual(message.get_content_type(), "multipart/alternative")
            self.assertEqual(message.get_body(preferencelist=("plain",)).get_content().strip(), "Plain options")
            self.assertIn("HTML options", message.get_body(preferencelist=("html",)).get_content())
            with self.assertRaises(ValueError):
                self.app.send_msmtp("reviewer@example.invalid", "Subject\nBcc: other@example.invalid", "body")
            self.assertEqual(run.call_count, 1)

    def test_ordinary_summary_does_not_read_archive_history(self):
        state = {"processed": {"n": {"path": str(self.report), "status": "processed", "processed_at": "2026-10-27",
                                     "lesson_family": "family", "scope": "global", "prevention_targets": ["global:AGENTS.md"]}}}
        self.app.save_state(self.root, state)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "summarize-runs"])
        with patch.object(self.app, "read_note", side_effect=AssertionError("archive read")):
            self.assertEqual(self.app.command_summarize_runs(args), 0)

    def test_cli_cycle_captures_indexed_project_targets_and_rejects_omitted_changes(self):
        skills = self.directory / "skills"
        skills.mkdir()
        project = self.directory / "project"
        project.mkdir()
        project_rules = project / "AGENTS.md"
        project_rules.write_text("# Project\n\n- Check the current deployment revision before declaring completion.\n")
        self.app.save_state(self.root, {"processed": {"project-note": {
            "prevention_targets": ["project-local:" + str(project_rules)]}}})
        def call(*args):
            with redirect_stdout(io.StringIO()) as output:
                status = self.app.main(["--config", str(self.config), *args])
            return status, output.getvalue()
        with patch.object(self.app, "DEFAULT_AUDIT_TARGETS", (self.target,)), patch.object(self.app, "DEFAULT_AUDIT_SKILLS_DIR", skills):
            self.assertEqual(call("pipeline-begin", "--run-id", "cli-run", "--runner", "midnight", "--goal-mode", "manual")[0], 0)
            self.assertEqual(call("pipeline-audit", "--run-id", "cli-run", "--weekly")[0], 0)
            weekly = self.pipeline.status()["weekly"]
            captured = json.loads(Path(weekly["snapshot"]["path"]).read_text())["audit"]
            self.assertIn(str(project_rules.resolve()), captured["scanned_files"])
            project_rules.write_text(project_rules.read_text() + "\n- Retain the controller reconciliation result.\n")
            self.assertEqual(call("pipeline-finish", "--run-id", "cli-run", "--status", "completed", "--summary", "Done", "--report", str(self.report))[0], 2)
            self.assertEqual(call("pipeline-audit", "--run-id", "cli-run", "--target", str(project_rules))[0], 0)
            self.assertEqual(call("pipeline-finish", "--run-id", "cli-run", "--status", "completed", "--summary", "Verified", "--report", str(self.report), "--target", str(project_rules))[0], 0)

    def test_persisted_italian_language_keeps_email_english_and_deduplicates(self):
        self.pipeline.configure(ORDINARY, RELEASE, "it")
        issue = self.decision()
        issue.update(title="Scegli la conservazione", description="La fonte può essere conservata o rimossa dopo la verifica.",
                     question="Quale politica di conservazione devo applicare?",
                     options=[{"id": "A", "label": "Conserva", "description": "Mantieni la fonte per la riconciliazione.", "recommended": True},
                              {"id": "B", "label": "Elimina", "description": "Rimuovi la fonte dopo la verifica."}])
        self.pipeline.update_issue(issue)
        now = datetime.now().astimezone()
        digest = self.pipeline.digest(self.pipeline.status(), self.notes(), now)
        self.assertEqual(digest["subject"], "Agent Learning: 1 decision(s), 0 issue(s)")
        self.assertNotIn("decisioni", digest["subject"])
        self.assertNotIn("segnalazioni", digest["subject"])
        self.assertIn("Decision requires your review", digest["body"])
        self.assertIn("The original question and options are available in the linked chat", digest["body"])
        self.assertNotIn("Quale politica di conservazione", digest["body"])
        self.assertNotIn("Conserva", digest["html"])
        self.assertEqual(digest["items"][0]["email_language"], "en")
        self.assertIn("Open the chat to respond", digest["html"])
        self.assertNotIn("Apri la chat per rispondere", digest["html"])
        self.assertIn('lang="en"', digest["html"])
        self.assertIn("https://chatgpt.com/codex/open-app?codex_thread_id=" + ORDINARY, digest["body"])
        self.assertIn("open 'codex://threads/" + ORDINARY + "'", digest["body"])

        self.pipeline.reserve(digest, lambda state: self.pipeline.digest(state, self.notes(), now))
        self.pipeline.acknowledge(digest["digest_id"], "fixture English delivery receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes(), now))

        changed = {**issue, "question": "Quale politica aggiornata devo applicare?",
                   "options": [{"id": "A", "label": "Mantieni", "description": "Conserva la fonte aggiornata.", "recommended": True},
                               {"id": "B", "label": "Rimuovi", "description": "Rimuovi la fonte aggiornata."}]}
        self.pipeline.update_issue(changed)
        changed_digest = self.pipeline.digest(self.pipeline.status(), self.notes(), now)
        self.assertIsNotNone(changed_digest)
        self.assertNotIn("Quale politica aggiornata", changed_digest["body"])
        self.assertNotIn("Mantieni", changed_digest["body"])
        self.assertIn("Open the chat to respond", changed_digest["html"])

    def test_english_digest_uses_fixed_chat_fallback_and_tracks_issue_revision(self):
        issue = self.decision()
        issue.update(title="Scegli la conservazione", description="La fonte può essere conservata o rimossa dopo la verifica.",
                     question="Quale politica di conservazione devo applicare?",
                     options=[{"id": "A", "label": "Conserva", "description": "Mantieni la fonte per la riconciliazione.", "recommended": True},
                              {"id": "B", "label": "Elimina", "description": "Rimuovi la fonte dopo la verifica."}])
        self.pipeline.update_issue(issue)
        now = datetime.now().astimezone()
        digest = self.pipeline.digest(self.pipeline.status(), self.notes(), now)

        self.assertIn("Decision decision-1: the original question and options are in the linked chat", digest["body"])
        self.assertIn("The original question and options are available in the linked chat", digest["body"])
        self.assertEqual(digest["items"][0]["options"], [])
        self.assertTrue(digest["items"][0]["content_revision"])
        self.assertNotIn("content_revision", digest["body"])
        self.assertNotIn("content_revision", digest["html"])
        self.assertIn("Open the chat to respond", digest["html"])
        self.assertIn('lang="en"', digest["html"])
        self.assertNotIn("Quale politica di conservazione", digest["body"])
        self.assertNotIn("Conserva", digest["html"])
        self.assertIn("open 'codex://threads/" + ORDINARY + "'", digest["body"])
        self.pipeline.reserve(digest, lambda state: self.pipeline.digest(state, self.notes(), now))
        self.pipeline.acknowledge(digest["digest_id"], "fixture provider receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes(), now))

        updated = {**issue, "question": "Quale criterio aggiornato devo seguire?",
                   "options": [{"id": "A", "label": "Mantieni", "description": "Conserva la copia.", "recommended": True},
                               {"id": "B", "label": "Rimuovi", "description": "Elimina la copia."}]}
        self.pipeline.update_issue(updated)
        refreshed = self.pipeline.digest(self.pipeline.status(), self.notes(), now)
        self.assertIsNotNone(refreshed)
        self.assertNotEqual(digest["items"][0]["content_revision"], refreshed["items"][0]["content_revision"])
        self.assertNotIn("Quale criterio aggiornato", refreshed["body"])
        self.assertNotIn("Mantieni", refreshed["html"])

    def test_english_issue_revision_binds_sources_targets_evidence_and_reservation_refresh(self):
        from learning_pipeline import issue_notification_revision

        issue = self.decision()
        base = issue_notification_revision(issue)
        for field, value in (
            ("status", "investigate"),
            ("title", "Updated title"),
            ("description", "Updated description"),
            ("question", "Updated question"),
            ("options", [{"id": "A", "label": "Keep", "description": "Retain.", "recommended": True}]),
            ("sources", [self.ref(self.report)]),
            ("targets", [self.ref(self.report)]),
            ("evidence", [self.ref(self.report)]),
            ("revalidation_required", True),
        ):
            changed = {**issue, field: value}
            self.assertNotEqual(base, issue_notification_revision(changed), field)

        issue.update(title="Scelta iniziale", description="Descrizione iniziale.",
                     question="Quale scelta devo applicare?")
        self.pipeline.update_issue(issue)
        now = datetime.now().astimezone()
        original_digest = self.pipeline.digest(self.pipeline.status(), self.notes(), now)
        new_target = self.directory / "notification-target.md"
        new_target.write_text("# Current target\n", encoding="utf-8")
        revised = {**issue, "title": "Scelta aggiornata", "description": "Descrizione aggiornata.",
                   "question": "Quale scelta aggiornata devo applicare?",
                   "sources": [{**self.ref(self.report), "note_id": "note-1"}],
                   "targets": [self.ref(new_target)], "evidence": [self.ref(self.report)]}
        self.pipeline.update_issue(revised)
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.pipeline.reserve(original_digest,
                                  lambda state: self.pipeline.digest(state, self.notes(), now))
        refreshed = self.pipeline.digest(self.pipeline.status(), self.notes(), now)
        self.assertNotEqual(original_digest["items"][0]["content_revision"],
                            refreshed["items"][0]["content_revision"])
        self.assertNotIn("Scelta aggiornata", refreshed["body"])
        self.assertNotIn("Descrizione aggiornata", refreshed["html"])
        self.pipeline.reserve(refreshed, lambda state: self.pipeline.digest(state, self.notes(), now))
        self.pipeline.acknowledge(refreshed["digest_id"], "fixture current revision receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), self.notes(), now))

    def test_pipeline_issue_rejects_removed_email_en_field(self):
        with self.assertRaisesRegex(ValueError, "email_en is unsupported"):
            self.pipeline.update_issue({**self.decision(), "email_en": {"language": "en"}})

    def test_english_digest_does_not_expose_free_form_run_summary(self):
        self.pipeline.begin("italian-run-summary", "noon", initial_inbox=[])
        self.pipeline.finish("italian-run-summary", "failed", "La coda è bloccata in attesa della revisione.", "", [])
        digest = self.pipeline.digest(self.pipeline.status(), [])
        self.assertIn("The consolidation needs attention. Open the linked chat for details.", digest["body"])
        self.assertNotIn("La coda", digest["body"])
        self.assertTrue(digest["items"][0]["content_revision"])

    def test_english_run_alert_revision_tracks_runs_summaries_and_legacy_migration(self):
        from learning_pipeline import run_notification_revision

        run = {"id": "run-base", "runner": "midnight", "requested_runner": "midnight",
               "dispatch_status": "unknown", "dispatch": None, "dispatch_history": [],
               "start_tool_call_id": "", "goal_mode": "manual", "status": "failed",
               "started_at": "2026-11-16T00:00:00+01:00", "finished_at": "2026-11-16T00:01:00+01:00",
               "summary": "First summary", "report": {"path": "/reports/first.md", "sha256": "a" * 64}}
        base_revision = run_notification_revision(run)
        for field, value in (
            ("id", "run-next"), ("runner", "noon"), ("requested_runner", "noon"),
            ("dispatch_status", "correlated"), ("dispatch", {"turn_id": "turn-new"}),
            ("dispatch_history", [{"status": "unknown"}]), ("start_tool_call_id", "exec-turn-new"),
            ("goal_mode", "scheduled"), ("status", "partial"),
            ("started_at", "2026-11-16T12:00:00+01:00"), ("finished_at", "2026-11-16T12:01:00+01:00"),
            ("summary", "Changed summary"),
            ("report", {"path": "/reports/second.md", "sha256": "b" * 64}),
        ):
            self.assertNotEqual(base_revision, run_notification_revision({**run, field: value}), field)

        self.pipeline.begin("run-one", "midnight", initial_inbox=[])
        self.pipeline.finish("run-one", "failed", "Old free-form run summary", "", [])
        now = datetime.now().astimezone()
        state = self.pipeline.status()
        legacy_item = {"id": "runner-midnight", "status": "error",
                       "title": "Consolidation midnight: attention required",
                       "description": "Old free-form run summary", "question": "", "options": [],
                       "chat_url": "https://chatgpt.com/codex/open-app?codex_thread_id=" + ORDINARY,
                       "native_chat_url": "codex://threads/" + ORDINARY}
        state["sent"]["runner-midnight"] = {"signature": self.app.fingerprint(legacy_item),
                                             "at": now.isoformat(), "receipt": "legacy-run-receipt"}
        migration = self.pipeline.digest(state, [], now)
        self.assertIsNotNone(migration)
        self.assertNotIn("Old free-form run summary", migration["body"])
        self.assertTrue(migration["items"][0]["content_revision"])
        self.pipeline.reserve(migration, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.acknowledge(migration["digest_id"], "fixture run migration receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now))

        self.pipeline.begin("run-two", "midnight", initial_inbox=[])
        self.pipeline.finish("run-two", "failed", "Second authored summary", "", [])
        second = self.pipeline.digest(self.pipeline.status(), [], now)
        self.assertIsNotNone(second)
        self.assertNotEqual(migration["items"][0]["content_revision"], second["items"][0]["content_revision"])
        self.assertNotIn("Second authored summary", second["body"])
        self.pipeline.reserve(second, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.acknowledge(second["digest_id"], "fixture second run receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now))

        with self.pipeline.lock():
            state = self.pipeline.load()
            state["runs"]["run-two"]["summary"] = "Corrected summary without exposing it"
            self.pipeline.save(state)
        corrected = self.pipeline.digest(self.pipeline.status(), [], now)
        self.assertIsNotNone(corrected)
        self.assertNotEqual(second["items"][0]["content_revision"], corrected["items"][0]["content_revision"])
        self.assertNotIn("Corrected summary", corrected["body"])
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.pipeline.reserve(second, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.reserve(corrected, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.acknowledge(corrected["digest_id"], "fixture corrected run receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now))

    def test_english_unknown_runner_group_tracks_hidden_run_summary(self):
        self.pipeline.begin("unknown-run", "unknown", initial_inbox=[])
        self.pipeline.finish("unknown-run", "failed", "Sensitive authored summary", "", [])
        now = datetime.now().astimezone()
        digest = self.pipeline.digest(self.pipeline.status(), [], now)
        item = next(item for item in digest["items"] if item["id"] == "runner-unknown")
        self.assertTrue(item["content_revision"])
        self.assertIn("unknown-run", digest["body"])
        self.assertNotIn("Sensitive authored summary", digest["body"])
        self.pipeline.reserve(digest, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.acknowledge(digest["digest_id"], "fixture unknown run receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now))

        with self.pipeline.lock():
            state = self.pipeline.load()
            state["runs"]["unknown-run"]["summary"] = "Changed hidden summary"
            self.pipeline.save(state)
        changed = self.pipeline.digest(self.pipeline.status(), [], now)
        self.assertIsNotNone(changed)
        changed_item = next(item for item in changed["items"] if item["id"] == "runner-unknown")
        self.assertNotEqual(item["content_revision"], changed_item["content_revision"])
        self.assertNotIn("Changed hidden summary", changed["body"])

    def test_english_correlated_and_recovered_run_groups_track_hidden_run_details(self):
        now = datetime.now().astimezone()
        self.pipeline.begin("correlated-run", "unknown", initial_inbox=[])
        self.pipeline.finish("correlated-run", "partial", "Correlated hidden summary", "", [])
        with self.pipeline.lock():
            state = self.pipeline.load()
            run = state["runs"]["correlated-run"]
            run.update(dispatch_status="correlated", requested_runner="midnight",
                       dispatch={"event_id": "event-one", "runner": "midnight"})
            self.pipeline.save(state)
        correlated = self.pipeline.digest(self.pipeline.status(), [], now)
        correlated_item = next(item for item in correlated["items"] if item["id"] == "runner-correlated")
        self.assertTrue(correlated_item["content_revision"])
        self.assertNotIn("Correlated hidden summary", correlated["body"])
        self.pipeline.reserve(correlated, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.acknowledge(correlated["digest_id"], "fixture correlated receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now))

        with self.pipeline.lock():
            state = self.pipeline.load()
            state["runs"]["correlated-run"]["dispatch"]["event_id"] = "event-two"
            self.pipeline.save(state)
        changed_correlated = self.pipeline.digest(self.pipeline.status(), [], now)
        changed_item = next(item for item in changed_correlated["items"] if item["id"] == "runner-correlated")
        self.assertNotEqual(correlated_item["content_revision"], changed_item["content_revision"])
        self.assertNotIn("event-two", changed_correlated["body"])

        self.pipeline.begin("recovered-unknown-run", "unknown", initial_inbox=[])
        self.pipeline.finish("recovered-unknown-run", "failed", "Recovered hidden summary", "", [])
        self.pipeline.verify_recovery = lambda _pipeline, _state, _recovery, _deadline: None
        with self.pipeline.lock():
            state = self.pipeline.load()
            state.setdefault("run_recoveries", {})["recovered-unknown-run"] = {
                "run_id": "recovered-unknown-run", "verification": "fixture"}
            self.pipeline.save(state)
        recovered = self.pipeline.digest(self.pipeline.status(), [], now)
        recovered_item = next(item for item in recovered["items"] if item["id"] == "runner-unknown-attribution")
        self.assertTrue(recovered_item["content_revision"])
        self.assertNotIn("Recovered hidden summary", recovered["body"])
        self.pipeline.reserve(recovered, lambda value: self.pipeline.digest(value, [], now))
        self.pipeline.acknowledge(recovered["digest_id"], "fixture recovered attribution receipt")
        self.assertIsNone(self.pipeline.digest(self.pipeline.status(), [], now))

        with self.pipeline.lock():
            state = self.pipeline.load()
            state["runs"]["recovered-unknown-run"]["requested_runner"] = "noon"
            self.pipeline.save(state)
        changed_recovered = self.pipeline.digest(self.pipeline.status(), [], now)
        changed_recovered_item = next(item for item in changed_recovered["items"]
                                      if item["id"] == "runner-unknown-attribution")
        self.assertNotEqual(recovered_item["content_revision"], changed_recovered_item["content_revision"])

    def test_english_digest_falls_back_for_untranslated_reconciliation_issue(self):
        self.pipeline.update_issue({"id": "goal-reconcile-email", "kind": "operational", "status": "investigate",
                                    "title": "Riconciliazione della sessione", "description": "La sessione precedente richiede un controllo.",
                                    "sources": [], "targets": [self.ref(self.target)]})
        digest = self.pipeline.digest(self.pipeline.status(), [])
        self.assertIn("Run reconciliation required", digest["body"])
        self.assertIn("evidence-based reconciliation", digest["body"])
        self.assertIn("Issue goal-reconcile-email", digest["body"])
        self.assertNotIn("Riconciliazione", digest["body"])
        self.assertNotIn("La sessione", digest["html"])

    def test_english_fallback_without_chat_uses_status_command(self):
        self.pipeline.update_issue({"id": "stale-runtime", "kind": "operational", "status": "investigate",
                                    "title": "Controllo runtime", "description": "Verifica il runtime corrente.",
                                    "sources": [], "targets": [self.ref(self.target)]})
        self.target.write_text("# Changed target\n", encoding="utf-8")
        state = self.pipeline.status()
        state["threads"] = {}

        digest = self.pipeline.digest(state, [])
        stale = next(item for item in digest["items"] if item["id"] == "stale-runtime")

        self.assertIn("pipeline-status", stale["description"])
        self.assertIn("Issue stale-runtime", stale["description"])
        self.assertNotIn("linked chat", stale["description"])
        self.assertNotIn("Controllo runtime", digest["body"])
        self.assertNotIn("Verifica il runtime", digest["body"])

    def test_release_gate_rejects_a_newer_failed_cycle(self):
        self.pipeline.begin("run-1", "midnight", initial_inbox=[])
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.pipeline.finish("run-1", "completed", "Verified", str(self.report), [])
        self.pipeline.begin("run-2", "noon")
        self.pipeline.finish("run-2", "failed", "Queue unreadable", "", [])
        with self.assertRaisesRegex(ValueError, "verified completed cycle"):
            self.pipeline.weekly_release_status()

    def test_release_gate_detects_concurrent_state_change(self):
        import learning_pipeline
        self.pipeline.begin("release-concurrent", "midnight", initial_inbox=[])
        self.pipeline.audit("release-concurrent", self.audit_payload(False), [], True)
        self.pipeline.finish("release-concurrent", "completed", "Verified", str(self.report), [])
        check = learning_pipeline.check_sealed_file
        changed = False
        def concurrent(*args):
            nonlocal changed
            if not changed:
                changed = True
                with self.pipeline.lock():
                    state = self.pipeline.load()
                    state["issues"]["goal-reconcile-concurrent"] = {"id": "goal-reconcile-concurrent", "kind": "operational",
                                                                      "status": "investigate", "title": "Changed", "description": "Reconcile."}
                    self.pipeline.save(state)
            return check(*args)
        with patch.object(learning_pipeline, "check_sealed_file", side_effect=concurrent):
            with self.assertRaisesRegex(ValueError, "state changed during release"):
                self.pipeline.weekly_release_status()

    def test_release_gate_deduplicates_shared_reconciliation_evidence(self):
        import learning_pipeline
        self.pipeline.begin("release-dedup", "midnight", initial_inbox=[])
        self.pipeline.audit("release-dedup", self.audit_payload(False), [], True)
        self.pipeline.finish("release-dedup", "completed", "Verified", str(self.report), [])
        ref = self.ref(self.report)
        with self.pipeline.lock():
            state = self.pipeline.load()
            records = {}
            for index in range(12):
                issue_id = f"goal-reconcile-{index}"
                original = {"id": issue_id, "kind": "operational", "status": "investigate", "drifts": [{"id": str(index)}]}
                records[issue_id] = original
                state["issues"][issue_id] = {**original, "status": "resolved", "resolution": "Checked.",
                                             "evidence": [str(self.report)], "resolution_evidence": [ref]}
            state.setdefault("goal_cycle_history", []).append({"status": "needs-reconciliation", "reconciliation_issues": list(records),
                                                               "reconciliation_records": records})
            self.pipeline.save(state)
        original_check = learning_pipeline.check_sealed_file
        with patch.object(learning_pipeline, "check_sealed_file", wraps=original_check) as checked:
            self.pipeline.weekly_release_status()
            self.assertEqual(checked.call_count, 2)  # One weekly audit and one shared report.


if __name__ == "__main__":
    unittest.main()
