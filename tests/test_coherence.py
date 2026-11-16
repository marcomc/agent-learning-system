from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import test_integration as integration_support


class CoherenceTests(unittest.TestCase):
    setUp = integration_support.IntegrationTests.setUp
    seal_review = integration_support.IntegrationTests.seal_review
    command = integration_support.IntegrationTests.command
    record = integration_support.IntegrationTests.record

    def ready(self):
        self.seal_review()
        self.auto_review = False

    def resign(self, roles):
        import learning_coherence
        import learning_integration
        proposal = learning_coherence.proposal_hash(self.manifest, learning_integration.verify_distribution(self.manifest))
        rows = []
        for role, identity in roles:
            report = self.directory / (role + "-" + proposal + ".md")
            report.write_text("Synthetic independent review of " + proposal + "\n")
            rows.append({"role": role, "reviewer": identity, "proposal_sha256": proposal,
                         "verdict": "pass", "evidence": self.app.snapshot(str(report))})
        self.manifest["coherence"]["reviews"] = rows

    def test_distribution_alone_cannot_record_or_certify_legacy_receipts(self):
        self.auto_review = False
        self.assertEqual(self.record(), 2)
        import learning_integration
        receipt = self.root / "reports" / "legacy.json"
        receipt.write_text(json.dumps({"manifest": self.manifest, "verified": learning_integration.verify_distribution(self.manifest)}))
        state = self.app.load_state(self.root)
        state["processed"]["note-1"].update(integration_status="verified", integration_receipt=self.app.snapshot(str(receipt)))
        self.app.save_state(self.root, state)
        result, output = self.command("integration-audit", "--all-history", "--require-verified")
        self.assertEqual(result, 1)
        self.assertIn("Coherence review is required", output)

    def test_bound_policy_and_report_drift_invalidate_the_receipt(self):
        self.ready()
        self.assertEqual(self.record(), 0)
        self.policy.write_text("# Policy\n\nChanged superior policy.\n")
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)
        self.policy.write_text("# Policy\n\nPreserve confirmed requirements.\n")
        report = self.manifest["coherence"]["reviews"][0]["evidence"]
        from pathlib import Path
        Path(report["path"]).write_text("A stale generic approval.\n")
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)

    def test_voluntary_escalation_above_the_minimum_retains_independence(self):
        self.ready()
        assessment = self.manifest["coherence"]
        assessment["level"] = "peer"
        self.resign([("reviewer", "independent-peer")])
        self.assertEqual(self.record(), 0)
        target = self.source / "SKILL.md"
        target.write_text(target.read_text() + "\nA bounded operational refinement.\n")
        (self.copy / "SKILL.md").write_bytes(target.read_bytes())
        self.manifest["files"][0].update(self.app.snapshot(str(target)))
        assessment["targets"][0]["operation"] = "refine"
        assessment["level"] = "council"
        self.resign([("policy", "policy-reviewer"), ("ergonomics", "cost-reviewer")])
        self.assertEqual(self.record(), 0)

    def test_unchanged_global_and_multi_target_outcomes_do_not_force_a_council(self):
        self.ready()
        other = self.directory / "AGENTS.md"
        other.write_text("# Policy\n\nRetain the existing rule.\n")
        before = self.directory / "other-before.md"
        before.write_bytes(other.read_bytes())
        self.manifest["files"].append({**self.app.snapshot(str(other)), "contains": ["Retain the existing rule."]})
        self.manifest["coherence"]["targets"].append({
            "path": str(other), "kind": "global", "operation": "already-covered",
            "rationale": "Both independent targets remain unchanged.", "before": self.app.snapshot(str(before))})
        self.resign([("reviewer", "fixture-editor")])
        self.assertEqual(self.record(), 0)

    def test_council_requires_two_distinct_independent_lenses(self):
        self.ready()
        target = self.source / "SKILL.md"
        target.write_text(target.read_text() + "\nRefine the fixture policy.\n")
        (self.copy / "SKILL.md").write_bytes(target.read_bytes())
        self.manifest["files"][0].update(self.app.snapshot(str(target)))
        assessment = self.manifest["coherence"]
        assessment["targets"][0].update(kind="global", operation="refine")
        self.resign([("reviewer", "fixture-editor")])
        self.assertEqual(self.record(), 2)
        assessment["level"] = "council"
        self.resign([("policy", "one-reviewer"), ("ergonomics", "one-reviewer")])
        self.assertEqual(self.record(), 2)
        self.resign([("policy", "policy-reviewer"), ("ergonomics", "cost-reviewer")])
        self.assertEqual(self.record(), 0)

    def test_new_rule_needs_peer_not_a_local_self_approval(self):
        self.ready()
        target = self.source / "SKILL.md"
        target.write_text(target.read_text() + "\nAdd the fixture boundary.\n")
        (self.copy / "SKILL.md").write_bytes(target.read_bytes())
        self.manifest["files"][0].update(self.app.snapshot(str(target)))
        assessment = self.manifest["coherence"]
        assessment["targets"][0]["operation"] = "add"
        self.resign([("reviewer", "fixture-editor")])
        self.assertEqual(self.record(), 2)
        assessment["level"] = "peer"
        self.resign([("reviewer", "fixture-editor")])
        self.assertEqual(self.record(), 2)
        self.resign([("reviewer", "independent-peer")])
        self.assertEqual(self.record(), 0)

    def test_unresolved_conflicts_missing_checks_and_stale_proposal_fail(self):
        self.ready()
        original = copy.deepcopy(self.manifest["coherence"])
        for field, value in [("unresolved", ["Conflicting authorization rule"]),
                             ("checks", {}), ("context", [])]:
            with self.subTest(field=field):
                self.manifest["coherence"] = copy.deepcopy(original)
                self.manifest["coherence"][field] = value
                self.assertEqual(self.record(), 2)
        self.manifest["coherence"] = original
        self.manifest["rationale"] = "Changed scope after review."
        self.assertEqual(self.record(), 2)

    def test_frozen_report_cannot_be_reused_by_merely_relabelling_its_digest(self):
        self.ready()
        review = self.manifest["coherence"]
        review["checks"]["cost"] = "Different cost assessment."
        import learning_coherence
        import learning_integration
        review["reviews"][0]["proposal_sha256"] = learning_coherence.proposal_hash(
            self.manifest, learning_integration.verify_distribution(self.manifest))
        self.assertEqual(self.record(), 2)

    def test_growth_requires_rationale_and_before_metrics_are_measured(self):
        self.ready()
        target = self.source / "SKILL.md"
        target.write_text(target.read_text() + "\nPreserve every required negative path.\n")
        (self.copy / "SKILL.md").write_bytes(target.read_bytes())
        self.manifest["files"][0].update(self.app.snapshot(str(target)))
        assessment = self.manifest["coherence"]
        assessment["targets"][0].update(operation="refine", growth_rationale="")
        assessment["level"] = "peer"
        self.resign([("reviewer", "independent-peer")])
        self.assertEqual(self.record(), 2)
        assessment["targets"][0]["growth_rationale"] = "Retain explicit failure-path coverage at the acting boundary."
        self.resign([("reviewer", "independent-peer")])
        result, output = self.command("coherence-proposal", "--manifest", str(self.manifest_path))
        self.assertEqual(result, 0)
        sizes = json.loads(output)["sizes"][0]
        self.assertGreater(sizes["after_words"], sizes["before_words"])
        self.assertEqual(self.record(), 0)

    def test_audit_origin_uses_reviewed_preimages_without_certifying_notes(self):
        from learning_pipeline import exact_issue_id
        self.ready()
        pipeline = self.app.pipeline_store(self.root)
        ref = self.app.snapshot(str(self.source / "SKILL.md"))
        group = [{"path": ref["path"], "line": line, "text": "Preserve lock ownership."} for line in (3, 5)]
        payload = {"rule_parser_version": 2, "comparison_scope": "full", "file_snapshots": [ref],
                   "exact_duplicates": [group], "near_duplicates": [], "potential_conflicts": []}
        pipeline.begin("fixture-audit", "midnight", [], [], [])
        audit = pipeline.audit("fixture-audit", payload, [], False)["snapshot"]
        issue = pipeline.status()["issues"][exact_issue_id(group)]
        result, output = self.command("audit-coherence-source", "--issue-id", issue["id"],
                                      "--audit-report", audit["path"])
        self.assertEqual(result, 0)
        self.manifest["note_ids"] = []
        self.manifest["audit_sources"] = [json.loads(output)]
        self.manifest["coherence"]["reviews"] = []
        result, output = self.command("audit-coherence-proposal", "--manifest", str(self.manifest_path))
        self.assertEqual(result, 0)
        proposal = json.loads(output)["result"]["proposal_sha256"]
        review = self.directory / "audit-review.md"
        review.write_text("Reviewed frozen audit proposal " + proposal + "\n")
        self.manifest["coherence"]["reviews"] = [{"role": "reviewer", "reviewer": "fixture-editor",
            "proposal_sha256": proposal, "verdict": "pass", "evidence": self.app.snapshot(str(review))}]
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 0)
        self.assertEqual(self.command("integration-check", "--manifest", str(self.manifest_path))[0], 2)
        self.assertEqual(self.command("integration-record", "--manifest", str(self.manifest_path))[0], 2)
        self.assertEqual(list((self.root / "reports" / "integrations").glob("*.json")), [])

        original = copy.deepcopy(self.manifest)
        self.manifest["coherence"]["targets"][0]["before"]["sha256"] = "0" * 64
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)
        self.manifest = copy.deepcopy(original)
        self.manifest["note_ids"] = ["note-1"]
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)
        self.manifest = copy.deepcopy(original)
        (self.copy / "helper.py").write_text("changed = True\n")
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)
        (self.copy / "helper.py").write_text("safe = True\n")
        with pipeline.lock():
            state = pipeline.load()
            state["issues"][issue["id"]]["status"] = "needs-user"
            pipeline.save(state)
        self.assertEqual(self.command("audit-coherence-source", "--issue-id", issue["id"],
                                      "--audit-report", audit["path"])[0], 2)
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)

        forged = {**issue, "id": "audit-forged", "status": "candidate"}
        with pipeline.lock():
            state = pipeline.load()
            state["issues"][forged["id"]] = forged
            pipeline.save(state)
        with patch.object(self.app, "current_refs", side_effect=AssertionError("Unbound target was read")):
            self.assertEqual(self.command("audit-coherence-source", "--issue-id", forged["id"],
                                          "--audit-report", audit["path"])[0], 2)
        with pipeline.lock():
            state = pipeline.load()
            state["issues"][issue["id"]]["status"] = "candidate"
            state["issues"][issue["id"]]["description"] = "The discovery changed."
            pipeline.save(state)
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)

    def test_audit_cohort_proposal_checks_current_membership(self):
        from learning_pipeline import fingerprint
        for folder in (self.source, self.copy):
            with (folder / "SKILL.md").open("a") as stream:
                stream.write("Confirm release boundary.\n")
        self.before.write_bytes((self.source / "SKILL.md").read_bytes())
        self.manifest["files"][0].update(self.app.snapshot(str(self.source / "SKILL.md")))
        self.ready()
        other = self.directory / "other.md"
        other.write_text("# Other\n\nPreserve lock ownership.\nConfirm release boundary.\n")
        before = self.directory / "other-before.md"
        before.write_bytes(other.read_bytes())
        refs = [self.app.snapshot(str(self.source / "SKILL.md")), self.app.snapshot(str(other))]
        groups = [[{"path": ref["path"], "line": line, "text": text} for ref in refs]
                  for line, text in ((3, "Preserve lock ownership."), (4, "Confirm release boundary."))]
        pipeline = self.app.pipeline_store(self.root)
        payload = {"rule_parser_version": 2, "comparison_scope": "full", "file_snapshots": refs,
                   "exact_duplicates": groups, "near_duplicates": [], "potential_conflicts": []}
        pipeline.begin("cohort-audit", "midnight", [], [], [])
        audit = pipeline.audit("cohort-audit", payload, [], False)["snapshot"]
        report = Path(audit["path"])
        issue_id = "audit-" + fingerprint(["exact-file-pair-v1", sorted(ref["path"] for ref in refs)])[:24]
        issue = pipeline.status()["issues"][issue_id]
        result, output = self.command("audit-coherence-source", "--issue-id", issue["id"], "--audit-report", str(report))
        self.assertEqual(result, 0)
        self.manifest["note_ids"] = []
        self.manifest["audit_sources"] = [json.loads(output)]
        self.manifest["files"].append({**refs[1], "contains": ["Preserve lock ownership."]})
        self.manifest["coherence"]["targets"].append({"path": refs[1]["path"], "kind": "project",
            "operation": "already-covered", "rationale": "Retain the independent file's rule.",
            "before": self.app.snapshot(str(before))})
        self.manifest["coherence"]["reviews"] = []
        result, output = self.command("audit-coherence-proposal", "--manifest", str(self.manifest_path))
        self.assertEqual(result, 0)
        proposal = json.loads(output)["result"]["proposal_sha256"]
        review = self.directory / "cohort-review.md"
        review.write_text("Reviewed frozen proposal " + proposal + "\n")
        self.manifest["coherence"]["reviews"] = [{"role": "reviewer", "reviewer": "fixture-editor",
            "proposal_sha256": proposal, "verdict": "pass", "evidence": self.app.snapshot(str(review))}]
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 0)
        original_report = report.read_bytes()
        outside = self.directory / "outside-report.json"
        outside.write_bytes(original_report)
        report.unlink()
        report.symlink_to(outside)
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)
        report.unlink()
        report.write_bytes(original_report)
        report.write_text(report.read_text() + " ")
        self.assertEqual(self.command("audit-coherence-source", "--issue-id", issue["id"], "--audit-report", str(report))[0], 2)
        self.assertEqual(self.command("audit-coherence-proposal", "--manifest", str(self.manifest_path))[0], 2)
        self.assertEqual(self.command("audit-coherence-check", "--manifest", str(self.manifest_path))[0], 2)

    def test_audit_report_batch_enforces_the_report_count_bound(self):
        from learning_pipeline import current_audit_sources, exact_issue_id
        pipeline = self.app.pipeline_store(self.root)
        ref = self.app.snapshot(str(self.source / "SKILL.md"))
        group = [{"path": ref["path"], "line": line, "text": "Preserve lock ownership."} for line in (3, 5)]
        payload = {"rule_parser_version": 2, "comparison_scope": "full", "file_snapshots": [ref],
                   "exact_duplicates": [group], "near_duplicates": [], "potential_conflicts": []}
        pipeline.begin("audit-batch", "midnight", [], [], [])
        report = pipeline.audit("audit-batch", payload, [], False)["snapshot"]
        state = pipeline.status()
        issue_id = exact_issue_id(group)
        sources = []
        state["runs"] = {}
        for index in range(9):
            path = self.root / "reports" / f"copy-{index}.json"
            path.write_bytes(Path(report["path"]).read_bytes())
            reference = self.app.snapshot(str(path))
            state["runs"][str(index)] = {"post_audit": {"snapshot": reference}}
            sources.append({"id": issue_id, "report": reference})
        self.assertTrue(current_audit_sources(self.root, sources[:8], state, {}))
        self.assertFalse(current_audit_sources(self.root, sources, state, {}))
        large_sources = []
        state["runs"] = {}
        for index in range(3):
            path = self.root / "reports" / f"large-{index}.json"
            path.write_bytes(Path(report["path"]).read_bytes() + b" " * (11 * 1024 * 1024))
            reference = self.app.snapshot(str(path))
            state["runs"][str(index)] = {"post_audit": {"snapshot": reference}}
            large_sources.append({"id": issue_id, "report": reference})
        self.assertTrue(current_audit_sources(self.root, large_sources[:2], state, {}))
        self.assertFalse(current_audit_sources(self.root, large_sources, state, {}))


if __name__ == "__main__":
    unittest.main()
