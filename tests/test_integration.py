from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from datetime import datetime
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_agent_learning as support


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.app = support.load_agent_learning_module()
        self.config = support.AgentLearningTests().write_config(self.directory)
        self.root = self.app.learning_root(self.app.load_config(self.config))
        self.app.ensure_store(self.root)
        self.source = self.directory / "repository" / "example"
        self.copy = self.directory / "runtime" / "example"
        for folder in [self.source, self.copy]:
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text("# Example\n\nPreserve lock ownership.\n")
            (folder / "helper.py").write_text("safe = True\n")
        self.note = self.root / "processed" / "note.md"
        self.note.parent.mkdir(exist_ok=True)
        self.note.write_text("# Captured learning\n")
        self.app.save_state(self.root, {"processed": {"note-1": {
            "path": str(self.note), "status": "processed",
            "template_upstream_status": "not-applicable"}}, "captures": {}})
        self.manifest = {"version": 1, "note_ids": ["note-1"],
                         "rationale": "The owning source and complete deployed payload preserve the reviewed rule.",
                         "files": [{**self.app.snapshot(str(self.source / "SKILL.md")),
                                    "contains": ["Preserve lock ownership."]}],
                         "skills": [{"source": str(self.source), "copies": [str(self.copy)]}]}
        self.manifest_path = self.directory / "manifest.json"
        self.before = self.directory / "before.md"
        self.before.write_bytes((self.source / "SKILL.md").read_bytes())
        self.policy = self.directory / "policy.md"
        self.policy.write_text("# Policy\n\nPreserve confirmed requirements.\n")
        self.auto_review = True

    def seal_review(self):
        import learning_integration
        import learning_coherence
        try:
            artifacts = learning_integration.verify_distribution(self.manifest)
        except (ValueError, OSError):
            return
        target = str((self.source / "SKILL.md").resolve())
        changed = self.before.read_bytes() != (self.source / "SKILL.md").read_bytes()
        assessment = {
            "version": 1, "editor": "fixture-editor", "level": "peer" if changed else "local",
            "unresolved": [], "risks": {"authority_change": False, "resolved_conflict": False, "material_growth": False},
            "checks": {key: "Synthetic fixture assessment of the complete current context."
                       for key in learning_coherence.CHECKS},
            "context": [{**self.app.snapshot(str(self.policy)), "role": "policy"}]
                       + [{"path": path, "sha256": sha, "role": "reference"}
                          for path, sha in artifacts["files"].items() if path != target],
            "targets": [{"path": target, "kind": "skill", "operation": "refine" if changed else "already-covered",
                         "rationale": "Preserve the fixture's reviewed lock-ownership behavior.",
                         "growth_rationale": "The fixture retains explicit required coverage.",
                         "before": self.app.snapshot(str(self.before))}], "reviews": []}
        self.manifest["coherence"] = assessment
        proposal = learning_coherence.proposal_hash(self.manifest, artifacts)
        report = self.directory / ("review-" + proposal + ".md")
        report.write_text("Synthetic review of proposal " + proposal + "\n")
        assessment["reviews"] = [{"role": "reviewer", "reviewer": "fixture-peer" if changed else "fixture-editor",
                                  "proposal_sha256": proposal, "verdict": "pass", "evidence": self.app.snapshot(str(report))}]

    def command(self, *args):
        if args[0] in {"integration-record", "integration-check"} and self.auto_review:
            self.seal_review()
        self.manifest_path.write_text(json.dumps(self.manifest))
        with redirect_stdout(io.StringIO()) as captured:
            result = self.app.main(["--config", str(self.config), *args])
        return result, captured.getvalue()

    def record(self):
        return self.command("integration-record", "--manifest", str(self.manifest_path))[0]

    def test_real_payload_drift_blocks_recording_and_reopens_verified_result(self):
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 0)
        (self.copy / "helper.py").write_text("safe = False\n")
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 1)
        self.assertEqual(self.record(), 2)
        (self.copy / "helper.py").write_text("safe = True\n")
        (self.copy / "extra.py").write_text("unexpected = True\n")
        self.assertEqual(self.record(), 2)

    def test_internal_directory_symlinks_cannot_certify_complete_payloads(self):
        self.assertEqual(self.record(), 0)
        for root in [self.source, self.copy]:
            with self.subTest(root=root):
                external = self.directory / (root.parent.name + "-references")
                external.mkdir()
                (external / "rule.md").write_text("Unverified instructions.\n")
                link = root / "references"
                link.symlink_to(external, target_is_directory=True)
                self.assertEqual(self.record(), 2)
                self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)
                link.unlink()
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 0)

    def test_root_and_file_symlinks_remain_supported_and_track_drift(self):
        linked_root = self.directory / "runtime-link"
        linked_root.symlink_to(self.copy, target_is_directory=True)
        self.manifest["skills"][0]["copies"] = [str(linked_root)]
        linked_file = self.directory / "helper-source.py"
        linked_file.write_bytes((self.source / "helper.py").read_bytes())
        (self.source / "helper.py").unlink()
        (self.source / "helper.py").symlink_to(linked_file)
        self.assertEqual(self.record(), 0)
        linked_file.write_text("safe = False\n")
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)

    def test_draft_and_missing_rule_cannot_be_called_integrated(self):
        self.manifest["template_status"] = "draft-created"
        self.assertEqual(self.record(), 2)
        self.manifest.pop("template_status")
        self.manifest["files"][0]["contains"] = ["A rule that was never installed."]
        self.assertEqual(self.record(), 2)

    def test_source_reinstall_and_receipt_tampering_are_detected(self):
        self.assertEqual(self.record(), 0)
        (self.source / "helper.py").write_text("safe = False\n")
        (self.copy / "helper.py").write_text("safe = False\n")
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)
        (self.source / "helper.py").write_text("safe = True\n")
        (self.copy / "helper.py").write_text("safe = True\n")
        receipt = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        Path(receipt["path"]).write_text("{}")
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)

    def test_outside_store_note_and_installation_as_owner_are_rejected(self):
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["path"] = str(self.source / "SKILL.md")
        self.app.save_state(self.root, state)
        before = copy.deepcopy(self.app.load_state(self.root))
        self.assertEqual(self.record(), 2)
        self.assertEqual(self.app.load_state(self.root), before)
        self.assertEqual(list((self.root / "reports").rglob("*.json")), [])
        self.manifest["skills"][0]["source"] = str(self.directory / ".agents" / "skills" / "example")
        self.assertEqual(self.record(), 2)

    def test_unknown_note_and_unverified_legacy_entries_fail_strict_audit(self):
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)
        self.assertEqual(self.command("integration-audit", "--note-id", "missing", "--require-verified")[0], 1)

    def test_discovered_configured_copy_cannot_be_omitted(self):
        extra_root = self.directory / "another-runtime"
        extra = extra_root / "example"
        extra.mkdir(parents=True)
        (extra / "SKILL.md").write_text("# Stale installation\n")
        self.manifest["skill_roots"] = [str(extra_root)]
        self.assertEqual(self.record(), 2)

    def test_grouped_receipt_promotes_only_notes_with_a_template_handoff(self):
        global_file = self.directory / "AGENTS.md"
        global_file.write_text("# Policy\n\nPreserve evidence.\n")
        self.manifest["files"].append({**self.app.snapshot(str(global_file)), "contains": ["Preserve evidence."]})
        self.manifest["template_status"] = "promoted"
        self.manifest["global_projections"] = [str(global_file)]
        second = self.note.with_name("second.md")
        second.write_text("# Second captured lesson\n")
        state = self.app.load_state(self.root)
        state["processed"]["note-2"] = {"path": str(second), "status": "processed", "template_upstream_status": "draft-created"}
        self.app.save_state(self.root, state)
        self.manifest["note_ids"].append("note-2")
        self.assertEqual(self.record(), 0)
        rows = self.app.load_state(self.root)["processed"]
        self.assertEqual(rows["note-1"]["template_upstream_status"], "not-applicable")
        self.assertEqual(rows["note-2"]["template_upstream_status"], "promoted")

    def test_configured_completion_gate_requires_current_receipt_even_without_cli_flag(self):
        self.config.write_text(self.config.read_text() + '\nAGENT_LEARNING_REQUIRE_INTEGRATION="1"\n')
        pipeline = self.app.pipeline_store(self.root)
        pipeline.begin("run-1", "noon")
        target = str(self.source / "SKILL.md")
        pipeline.audit("run-1", {"file_snapshots": [self.app.snapshot(target)], "exact_duplicates": [],
                                "near_duplicates": [], "potential_conflicts": []}, [target], True)
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["processed_at"] = datetime.now().astimezone().isoformat()
        self.app.save_state(self.root, state)
        report = self.root / "reports" / "run.md"
        report.write_text("# Verified run\n")
        args = ("pipeline-finish", "--run-id", "run-1", "--status", "completed",
                "--summary", "Verified integration", "--report", str(report))
        with patch.object(self.app, "pipeline_inventory", return_value=[]):
            self.assertEqual(self.command(*args)[0], 2)
            self.assertEqual(self.record(), 0)
            (self.copy / "helper.py").write_text("safe = False\n")
            self.assertEqual(self.command(*args)[0], 2)
            (self.copy / "helper.py").write_text("safe = True\n")
            self.assertEqual(self.command(*args)[0], 0)

    def test_record_cannot_omit_a_concrete_prevention_target(self):
        target = self.directory / "AGENTS.md"
        target.write_text("# Instructions\n")
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["prevention_targets"] = [str(target)]
        self.app.save_state(self.root, state)
        self.assertEqual(self.record(), 2)

    def test_home_relative_targets_require_coverage_and_revalidate_drift(self):
        target = self.directory / "AGENTS.md"
        target.write_text("# Instructions\n\nPreserve evidence.\n")
        state = self.app.load_state(self.root)
        state["processed"]["note-1"].update(
            prevention_targets=["~/AGENTS.md"],
            detection_targets=["skill:~/AGENTS.md"],
        )
        self.app.save_state(self.root, state)
        with patch.dict("os.environ", {"HOME": str(self.directory)}):
            self.assertEqual(self.record(), 2)
            self.manifest["files"].append({**self.app.snapshot(str(target)),
                                           "contains": ["Preserve evidence."]})
            self.assertEqual(self.record(), 0)
            target.write_text("# Instructions\n\nChanged guidance.\n")
            self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)

    def test_template_integration_rejects_stale_consumers_and_unavailable_roots(self):
        import learning_integration
        repo = self.directory / "templates"
        repo.mkdir()
        template = repo / "python.md"
        template.write_text("# Python\n\nPreserve evidence.\n")
        project = self.directory / "project"
        project.mkdir()
        output = project / "AGENTS.md"
        output.write_text("GENERATED\nPreserve evidence.\n")
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["prevention_targets"] = [str(output)]
        self.app.save_state(self.root, state)
        self.manifest["files"].append({**self.app.snapshot(str(template)), "contains": ["Preserve evidence."]})
        self.manifest["template_status"] = "promoted"
        self.manifest["template_consumers"] = [{"repository": str(repo), "roots": [str(project)], "template_ids": ["python"]}]
        init = SimpleNamespace(GENERATED_MARKER="GENERATED", generated_types=lambda text: ["python"],
                               AGENT_GLOBAL_PATHS={}, check_output=lambda *args: {"status": "out-of-sync"})
        module = SimpleNamespace(load_init_helper=lambda: init,
                                 load_manifest=lambda root: {"project_types": [{"id": "python", "path": "python.md"}]},
                                 find_agents=lambda *args: [output], project_root_for_instruction=lambda path: project,
                                 instruction_agent=lambda *args: "codex")
        spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda value: None))
        with patch.object(learning_integration.importlib.util, "spec_from_file_location", return_value=spec), \
                patch.object(learning_integration.importlib.util, "module_from_spec", return_value=module):
            self.assertEqual(self.record(), 2)
            init.check_output = lambda *args: {"status": "current"}
            self.assertEqual(self.record(), 0)
            output.write_text("GENERATED\nChanged outside this integration.\n")
            self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)
            self.manifest["template_consumers"][0]["roots"] = [str(project / "missing")]
            self.assertEqual(self.record(), 2)


if __name__ == "__main__":
    unittest.main()
