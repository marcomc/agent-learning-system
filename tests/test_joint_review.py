from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts/agent_learning.py"
spec = importlib.util.spec_from_file_location("joint_review_test", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class JointReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.base = self.directory / "learnings"
        self.base.mkdir()
        self.config = self.directory / "config.env"
        self.set_policy()
        self.root = Path(self.run_helper("init-store").stdout.strip())
        self.target = self.directory / "project" / "AGENTS.md"
        self.target.parent.mkdir()
        self.target.write_text("# Project\n\n- Validate the bounded failure path.\n")

    def set_policy(self, enabled: str = "1", threshold: str = "8") -> None:
        self.config.write_text(
            f'AGENT_LEARNING_DIR="{self.base}"\n'
            f'AGENT_LEARNING_AUTONOMOUS_REVIEW="{enabled}"\n'
            f'AGENT_LEARNING_REVIEW_GROUP_THRESHOLD="{threshold}"\n'
        )

    def run_helper(self, *args: str, check: bool = True):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_LEARNING_")}
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--config", str(self.config), *args],
            capture_output=True, text=True, check=check, env=env,
        )

    def note(self, name: str, checked: str = "") -> Path:
        path = self.root / "needs-review" / f"{name}.md"
        body = f"# {name}\n\nObserved bounded failure and corroborated fix.\n\n{module.REVIEW_BLOCK}"
        if checked:
            for label in checked.split(","):
                body = body.replace(f"- [ ] {label}:", f"- [x] {label}:")
        module.write_note(path, {
            "id": name, "status": "needs-review", "project_path": str(self.target.parent),
            "lesson_family": "bounded-failure-path",
        }, body)
        return path

    def receipt(self, notes: list[Path]) -> tuple[Path, dict]:
        snapshots = [{"id": p.stem, "path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in notes]
        data = {
            "version": 1, "scope": "instructions-only", "sources": snapshots,
            "decisions": [{
                "note_id": p.stem, "classification": "technical", "rationale": "Corroborated bounded fix; instruction validated.",
                "evidence_refs": [n.stem for n in notes], "open_choices": [],
                "targets": [{"path": str(self.target), "sha256": hashlib.sha256(self.target.read_bytes()).hexdigest()}],
            } for p in notes],
        }
        path = self.root / "reports" / "joint.json"
        path.write_text(json.dumps(data))
        return path, data

    def finalize(
        self,
        note: Path,
        receipt: Path,
        check: bool = True,
        routing_target: str | None = None,
        include_routing_target: bool = True,
    ):
        args = [
            "finalize-note", "--file", str(note), "--status", "processed",
            "--rationale", "Corroborated and covered by current instruction.",
            "--review-evidence", str(receipt), "--scope", "project-local",
            "--lesson-family", "bounded-failure-path",
            "--routing-rationale", "Owning project loads verified rule.", "--enforce-routing",
        ]
        if include_routing_target:
            args.extend(["--prevention-target", routing_target or str(self.target)])
        return self.run_helper(
            *args,
            check=check,
        )

    def assert_refused(
        self,
        note: Path,
        receipt: Path,
        message: str = "",
        routing_target: str | None = None,
        include_routing_target: bool = True,
    ) -> None:
        before = note.read_bytes()
        result = self.finalize(
            note, receipt, check=False, routing_target=routing_target,
            include_routing_target=include_routing_target,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        if message:
            self.assertIn(message, result.stderr)
        self.assertEqual(note.read_bytes(), before)
        self.assertNotIn(note.stem, module.load_state(self.root)["processed"])

    def test_threshold_boundary_and_human_precedence(self) -> None:
        self.config.write_text(f'AGENT_LEARNING_DIR="{self.base}"\nAGENT_LEARNING_AUTONOMOUS_REVIEW="1"\n')
        for number in range(7):
            self.note(f"note-{number}")
        self.note("approved", "Approve")
        first = json.loads(self.run_helper("prepare-run").stdout)["review_analysis"]
        self.assertFalse(first["group_review_due"])
        self.assertEqual(first["pending_count"], 7)
        self.note("ambiguous", "Approve,Reject")
        second = json.loads(self.run_helper("prepare-run").stdout)["review_analysis"]
        self.assertTrue(second["group_review_due"])
        self.assertEqual(second["group_threshold"], 8)
        self.assertEqual(len(second["autonomous_candidates"]), 7)
        self.assertEqual(set(second["human_precedence"]), {"approved", "ambiguous"})
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(len(second["candidate_contexts"]), 1)
        repeated = json.loads(self.run_helper("prepare-run").stdout)["review_analysis"]
        self.assertEqual(second["fingerprint"], repeated["fingerprint"])

    def test_existing_project_aliases_share_one_context(self) -> None:
        first, second = self.note("a"), self.note("b")
        alias = self.directory / "old-project-name"
        alias.symlink_to(self.target.parent, target_is_directory=True)
        text = second.read_text().replace(str(self.target.parent), str(alias))
        second.write_text(text)
        analysis = json.loads(self.run_helper("prepare-run").stdout)["review_analysis"]
        self.assertEqual(len(analysis["candidate_contexts"]), 1)
        self.assertEqual(set(analysis["candidate_contexts"][0]["note_ids"]), {first.stem, second.stem})

    def test_due_queue_cannot_be_reported_complete_without_review(self) -> None:
        self.set_policy(threshold="2")
        self.note("a")
        self.note("b")
        result = self.run_helper("write-report", "--run-id", "unchecked", "--summary", "Completed.", check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--review-evidence", result.stderr)
        self.assertFalse((self.root / "reports" / "unchecked.md").exists())

    def test_pending_review_report_requires_complete_scoped_residuals(self) -> None:
        self.set_policy(threshold="2")
        notes = [self.note("a"), self.note("b")]
        receipt, original = self.receipt(notes)
        for data in (original, {**original, "decisions": original["decisions"][:1]}):
            receipt.write_text(json.dumps(data))
            result = self.run_helper("write-report", "--summary", "Deferred.", "--review-evidence", str(receipt), check=False)
            self.assertEqual(result.returncode, 2, result.stderr)
        data = copy.deepcopy(original)
        for decision in data["decisions"]:
            decision.update(classification="investigate", rationale="Project is dirty.")
        receipt.write_text(json.dumps(data))
        result = self.run_helper("write-report", "--summary", "Deferred.", "--review-evidence", str(receipt), check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        for decision in data["decisions"]:
            decision["blocker"] = {"overlap": "Existing retry rule conflicts with the proposed source boundary.",
                                   "next_action": "Reconcile the two exact instruction clauses."}
        receipt.write_text(json.dumps(data))
        report = Path(self.run_helper("write-report", "--summary", "Scoped residuals.", "--review-evidence", str(receipt)).stdout.strip())
        self.assertIn("investigate: 2", report.read_text())
        self.target.write_text(self.target.read_text() + "\nNew target evidence.\n")
        result = self.run_helper("write-report", "--summary", "Reuse.", "--review-evidence", str(receipt), check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Stale", result.stderr)

    def test_pending_review_report_preserves_human_choices_and_snapshot_identity(self) -> None:
        self.set_policy(threshold="2")
        notes = [self.note("a"), self.note("b", "Approve,Reject")]
        receipt, data = self.receipt(notes)
        for decision in data["decisions"]:
            decision.update(classification="needs-user", open_choices=["Choose the operational boundary."], targets=[])
        receipt.write_text(json.dumps(data))
        report = Path(self.run_helper("write-report", "--summary", "Human decisions remain.", "--review-evidence", str(receipt)).stdout.strip())
        self.assertIn("needs-user: 2", report.read_text())
        self.assertTrue(all(n.exists() for n in notes))
        notes[0].write_text(notes[0].read_text() + "\nNew evidence.\n")
        result = self.run_helper("write-report", "--summary", "Stale.", "--review-evidence", str(receipt), check=False)
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_pending_review_report_rejects_foreign_evidence(self) -> None:
        self.set_policy(threshold="2")
        notes = [self.note("a"), self.note("b")]
        receipt, data = self.receipt(notes)
        foreign = self.directory / "foreign.json"
        foreign.write_bytes(receipt.read_bytes())
        result = self.run_helper("write-report", "--summary", "Foreign.", "--review-evidence", str(foreign), check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("configured reports", result.stderr)

    def test_threshold_override_and_invalid_configuration(self) -> None:
        self.set_policy(threshold="2")
        self.note("a")
        self.note("b")
        self.assertTrue(json.loads(self.run_helper("prepare-run").stdout)["review_analysis"]["group_review_due"])
        for enabled, threshold in (("yes", "8"), ("1", "0"), ("1", "-1"), ("1", "many")):
            with self.subTest(enabled=enabled, threshold=threshold):
                self.set_policy(enabled, threshold)
                self.assertEqual(self.run_helper("prepare-run", check=False).returncode, 2)

    def test_cohort_can_resolve_before_threshold_without_fabricating_human_approval(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, data = self.receipt(notes)
        data["decisions"][1]["classification"] = "already-covered"
        receipt.write_text(json.dumps(data))
        first = Path(self.finalize(notes[0], receipt).stdout.strip())
        second = Path(self.finalize(notes[1], receipt).stdout.strip())
        for path in (first, second):
            fm, body = module.read_note(path)
            self.assertEqual(fm["review_authority"], "autonomous-instructions")
            self.assertEqual(module.checkbox_decision(body)[0], "pending")
            self.assertTrue(path.is_relative_to(self.root / "processed"))
            self.assertEqual(fm["status"], "processed")
        self.assertEqual(module.load_state(self.root)["processed"]["b"]["review_classification"], "already-covered")

    def test_default_manual_mode_refuses_receipt(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, _ = self.receipt(notes)
        self.config.write_text(f'AGENT_LEARNING_DIR="{self.base}"\n')
        self.assert_refused(notes[0], receipt, "explicit")

    def test_human_decisions_cannot_be_overridden(self) -> None:
        for selected in ("Approve", "Retry", "Reject", "Approve,Reject"):
            with self.subTest(selected=selected):
                notes = [self.note("a", selected), self.note("b")]
                receipt, _ = self.receipt(notes)
                self.assert_refused(notes[0], receipt, "precedence")

    def test_unresolved_or_non_technical_decisions_are_not_archived(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, original = self.receipt(notes)
        for classification, choices in (("needs-user", []), ("investigate", []), ("technical", ["Choose policy"]), ("technical", None)):
            with self.subTest(classification=classification, choices=choices):
                data = copy.deepcopy(original)
                data["decisions"][0].update(classification=classification, open_choices=choices)
                receipt.write_text(json.dumps(data))
                self.assert_refused(notes[0], receipt)

    def test_stale_source_and_target_are_rejected(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, _ = self.receipt(notes)
        notes[1].write_text(notes[1].read_text() + "\nNew contradictory evidence.\n")
        self.assert_refused(notes[0], receipt, "Stale")
        receipt, _ = self.receipt(notes)
        self.target.write_text(self.target.read_text() + "\nChanged target.\n")
        self.assert_refused(notes[0], receipt, "Stale instruction")

    def test_foreign_source_snapshots_and_current_source_are_rejected(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, data = self.receipt(notes)
        foreign = self.directory / "foreign.md"
        foreign.write_bytes(notes[1].read_bytes())
        data["sources"][1].update(path=str(foreign), sha256=hashlib.sha256(foreign.read_bytes()).hexdigest())
        receipt.write_text(json.dumps(data))
        self.assert_refused(notes[0], receipt, "outside")
        self.assert_refused(foreign, receipt, "outside learning store")

    def test_non_instruction_target_and_foreign_receipt_are_rejected(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, data = self.receipt(notes)
        code = self.directory / "app.py"
        code.write_text("print('not an instruction')\n")
        data["decisions"][0]["targets"] = [{"path": str(code), "sha256": hashlib.sha256(code.read_bytes()).hexdigest()}]
        receipt.write_text(json.dumps(data))
        self.assert_refused(notes[0], receipt, "instruction Markdown")
        outside = self.directory / "outside.json"
        outside.write_bytes(receipt.read_bytes())
        self.assert_refused(notes[0], outside, "inside configured reports")

    def test_concrete_routing_target_must_match_receipt(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, data = self.receipt(notes)
        other = self.directory / "other" / "AGENTS.md"
        other.parent.mkdir()
        other.write_text("# Other instruction\n")
        data["decisions"][0]["targets"] = [{"path": str(other), "sha256": hashlib.sha256(other.read_bytes()).hexdigest()}]
        receipt.write_text(json.dumps(data))
        relative_target = os.path.relpath(self.target, Path.cwd())
        for target in (
            str(self.target),
            relative_target,
            f"project-local:{relative_target}",
            "project-local:AGENTS.md",
        ):
            with self.subTest(target=target):
                self.assert_refused(notes[0], receipt, "Concrete routing targets", target)

    def test_relative_concrete_routing_target_matches_receipt(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, _ = self.receipt(notes)
        relative_target = os.path.relpath(self.target, Path.cwd())

        result = self.finalize(notes[0], receipt, routing_target=relative_target)

        archived = Path(result.stdout.strip())
        frontmatter, _body = module.read_note(archived)
        self.assertEqual(module.parse_json_list(frontmatter["prevention_targets"]), [relative_target])

    def test_extensionless_logical_target_scheme_remains_supported(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, _ = self.receipt(notes)

        result = self.finalize(notes[0], receipt, routing_target="project-local:learning-system")

        archived = Path(result.stdout.strip())
        frontmatter, _body = module.read_note(archived)
        self.assertEqual(
            module.parse_json_list(frontmatter["prevention_targets"]),
            ["project-local:learning-system"],
        )

    def test_concrete_frontmatter_target_must_match_receipt(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, data = self.receipt(notes)
        other = self.directory / "other" / "AGENTS.md"
        other.parent.mkdir()
        other.write_text("# Other instruction\n")
        frontmatter, body = module.read_note(notes[0])
        frontmatter["prevention_targets"] = json.dumps([str(other)])
        module.write_note(notes[0], frontmatter, body)
        data["sources"][0]["sha256"] = hashlib.sha256(notes[0].read_bytes()).hexdigest()
        receipt.write_text(json.dumps(data))

        self.assert_refused(
            notes[0], receipt, "Concrete routing targets", include_routing_target=False
        )

    def test_project_agent_procedures_require_a_loaded_root_pointer(self) -> None:
        root_instruction = self.target
        procedure = self.target.parent / "docs" / "agent-procedures.md"
        procedure.parent.mkdir()
        procedure.write_text("# Agent procedures\n\n- Preserve approved scope during technical recovery.\n")
        self.target = procedure
        notes = [self.note("a"), self.note("b")]
        receipt, _ = self.receipt(notes)
        self.assert_refused(notes[0], receipt, "instruction Markdown")
        root_instruction.write_text("# Project\n\nRead `docs/agent-procedures.md` before dispatch or recovery.\n")
        self.finalize(notes[0], receipt)
        self.finalize(notes[1], receipt)

    def test_missing_archived_evidence_and_malformed_receipts_fail_closed(self) -> None:
        notes = [self.note("a"), self.note("b")]
        receipt, original = self.receipt(notes)
        notes[1].unlink()
        self.assert_refused(notes[0], receipt, "missing")
        notes[1] = self.note("b")
        mutations = [
            {"scope": "application-code"}, {"sources": original["sources"][:1]},
            {"sources": [original["sources"][0], original["sources"][0]]},
            {"decisions": []},
        ]
        for change in mutations:
            with self.subTest(change=change):
                data = copy.deepcopy(original)
                data.update(change)
                receipt.write_text(json.dumps(data))
                self.assert_refused(notes[0], receipt)

    def test_installer_failure_preserves_existing_config_and_skills(self) -> None:
        config_dir = self.directory / "xdg" / "agent-learning-system"
        config_dir.mkdir(parents=True)
        sentinel = config_dir / "config.env"
        sentinel.write_text("existing-config\n")
        bins = self.directory / "bin"
        bins.mkdir()
        validator = bins / "markdownlint"
        validator.write_text("#!/bin/sh\nexit 42\n")
        validator.chmod(0o755)
        env = dict(os.environ, XDG_CONFIG_HOME=str(config_dir.parent), PATH=str(bins) + os.pathsep + os.environ["PATH"])
        for args, expected in ((["--review-group-threshold", "0"], 2), (["--autonomous-review"], 42)):
            result = subprocess.run(["bash", str(REPO / "install.sh"), "--dir", str(self.base), "--skip-automations", *args], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(sentinel.read_text(), "existing-config\n")


if __name__ == "__main__":
    unittest.main()
