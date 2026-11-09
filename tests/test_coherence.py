from __future__ import annotations

import copy
import json
import unittest

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
        result, output = self.command("integration-audit", "--require-verified")
        self.assertEqual(result, 1)
        self.assertIn("Coherence review is required", output)

    def test_bound_policy_and_report_drift_invalidate_the_receipt(self):
        self.ready()
        self.assertEqual(self.record(), 0)
        self.policy.write_text("# Policy\n\nChanged superior policy.\n")
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)
        self.policy.write_text("# Policy\n\nPreserve confirmed requirements.\n")
        report = self.manifest["coherence"]["reviews"][0]["evidence"]
        from pathlib import Path
        Path(report["path"]).write_text("A stale generic approval.\n")
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)

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


if __name__ == "__main__":
    unittest.main()
