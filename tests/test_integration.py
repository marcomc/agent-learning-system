from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
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

    def test_unavailable_receipt_is_pending_and_does_not_block_audit(self):
        entry = {"integration_status": "verified", "integration_receipt": {"path": "/offline/receipt.json", "sha256": "x"}}
        cache = {}
        with patch.object(self.app.subprocess, "run", side_effect=self.app.subprocess.TimeoutExpired("receipt", 8)) as run:
            reason = self.app.integration_problem(entry, "note-1", cache)
            self.assertIn("timed out", reason)
            self.assertEqual(self.app.integration_problem(entry, "note-1", cache), reason)
            self.assertEqual(run.call_count, 1)
        self.assertIn("budget exhausted", self.app.integration_problem(entry, "note-1", timeout=0))

    def test_manifest_verification_timeout_is_an_error(self):
        with patch.object(self.app.subprocess, "run", side_effect=self.app.subprocess.TimeoutExpired("manifest", 30)):
            with self.assertRaisesRegex(self.app.ConfigError, "timed out"):
                self.app.verify_manifest_bounded(self.manifest)

    def test_receipt_verifies_the_hashed_bytes_and_rejects_midcheck_change(self):
        import learning_integration
        self.assertEqual(self.record(), 0)
        reference = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        path = Path(reference["path"])
        original_text = Path.read_text
        def no_reread(candidate, *args, **kwargs):
            if candidate == path:
                raise AssertionError("receipt must be parsed from the hashed bytes")
            return original_text(candidate, *args, **kwargs)
        with patch.object(Path, "read_text", no_reread):
            learning_integration.validate_receipt(reference)
        verify = learning_integration.verify
        def mutate(manifest):
            result = verify(manifest)
            path.write_text(path.read_text() + " ")
            return result
        with patch.object(learning_integration, "verify", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed during verification"):
                learning_integration.validate_receipt(reference)

    def test_record_timeout_during_locked_revalidation_does_not_publish_receipt(self):
        before = self.app.load_state(self.root)
        verified = {"files": {}, "skills": {}, "generated": {}}
        with patch.object(self.app, "verify_manifest_bounded", side_effect=[verified, self.app.ConfigError("timed out")]):
            self.assertEqual(self.record(), 2)
        self.assertEqual(self.app.load_state(self.root), before)
        self.assertEqual(list((self.root / "reports" / "integrations").glob("*.json")), [])

    def seal_review(self):
        import learning_integration
        import learning_coherence
        if self.manifest.get("relocations"):
            target_items = {item["target"]: {"path": item["target"], "operation": "refine", "before": item["snapshot"]}
                            for item in self.manifest["relocations"]}
            self.manifest.setdefault("coherence", {})["targets"] = list(target_items.values())
            if any(item.get("schema_version") == 2 for item in self.manifest["relocations"]):
                self.manifest["coherence"]["level"] = "council"
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
        for item in self.manifest.get("relocations", []):
            if not any(target["path"] == item["target"] for target in assessment["targets"]):
                semantic = item.get("schema_version") == 2
                assessment["targets"].append({"path": item["target"], "kind": "project",
                    "operation": "refine" if semantic else "already-covered",
                    "rationale": "The candidate preserves the reviewed target.",
                    "growth_rationale": "The reviewed successor adds explicit evidence coverage.",
                    "before": item["snapshot"]})
                if semantic:
                    assessment["level"] = "council"
        self.manifest["coherence"] = assessment
        proposal = learning_coherence.proposal_hash(self.manifest, artifacts)
        report = self.directory / ("review-" + proposal + ".md")
        report.write_text("Synthetic review of proposal " + proposal + "\n")
        assessment["reviews"] = [{"role": "reviewer", "reviewer": "fixture-peer" if changed else "fixture-editor",
                                  "proposal_sha256": proposal, "verdict": "pass", "evidence": self.app.snapshot(str(report))}]
        if assessment["level"] == "council":
            assessment["reviews"] = [
                {"role": role, "reviewer": "fixture-" + role, "proposal_sha256": proposal,
                 "verdict": "pass", "evidence": self.app.snapshot(str(report))}
                for role in ("policy", "ergonomics")
            ]

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

    def test_run_audit_ignores_unverified_history_but_rejects_unverified_current_notes(self):
        from datetime import datetime, timedelta
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 2)
        result, output = self.command("integration-audit")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["scope"], {"kind": "history"})
        now = datetime.now().astimezone()
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["processed_at"] = (now - timedelta(days=2)).isoformat()
        self.app.save_state(self.root, state)
        pipeline = self.app.pipeline_store(self.root)
        started = pipeline.begin("current-run", "noon", initial_inbox=[])
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["processed_at"] = datetime.fromisoformat(started["started_at"]).replace(microsecond=0).isoformat()
        self.app.save_state(self.root, state)
        result, output = self.command("integration-audit", "--run-id", "current-run", "--require-verified")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["notes"], [])
        result, output = self.command("integration-audit", "--require-verified")
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output)["scope"], {"kind": "run", "run_id": "current-run"})
        result, output = self.command("integration-audit", "--all-history", "--require-verified")
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output)["scope"], {"kind": "history"})
        self.assertEqual(self.command("integration-audit", "--all-history", "--run-id", "current-run")[0], 2)
        state = self.app.load_state(self.root)
        state["processed"]["note-2"] = {"path": str(self.note), "status": "processed", "processed_at": datetime.now().astimezone().isoformat(), "consolidation_run": "current-run"}
        self.app.save_state(self.root, state)
        result, output = self.command("integration-audit", "--run-id", "current-run", "--require-verified")
        self.assertEqual(result, 1)
        self.assertEqual(self.command("integration-audit", "--require-verified")[0], 1)
        self.assertEqual([n["note_id"] for n in json.loads(output)["notes"]], ["note-2"])
        self.manifest["note_ids"] = ["note-2"]
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.command("integration-audit", "--run-id", "current-run", "--require-verified")[0], 0)
        (self.copy / "helper.py").write_text("safe = False\n")
        self.assertEqual(self.command("integration-audit", "--run-id", "current-run", "--require-verified")[0], 1)
        self.assertEqual(self.command("integration-audit", "--run-id", "missing", "--require-verified")[0], 2)

    def test_run_scope_uses_baseline_and_explicit_membership_not_time(self):
        run = {"id": "run-a", "integration_scope_version": 1,
               "initial_inbox": [{"id": "baseline"}], "started_at": "2026-11-10T12:00:00.900000+01:00"}
        state = {"processed": {
            "baseline": {"status": "processed", "processed_at": "2026-11-10T12:00:00+01:00", "consolidation_run": "run-a"},
            "extra": {"status": "processed", "processed_at": "2026-11-10T12:00:01+01:00", "consolidation_run": "run-a"},
            "historical": {"status": "processed", "processed_at": "2026-11-10T12:00:00+01:00"},
            "manual": {"status": "processed", "processed_at": "2026-11-10T12:00:02+01:00"},
            "other": {"status": "processed", "consolidation_run": "run-b"},
        }}
        self.assertEqual({key for key, _ in self.app.run_processed_entries(state, run)}, {"baseline", "extra"})
        state["processed"]["reprocessed"] = {"status": "processed", "consolidation_run": "run-b", "consolidation_runs": ["run-a", "run-b"]}
        with self.assertRaisesRegex(self.app.ConfigError, "outcome was superseded"):
            self.app.run_processed_entries(state, run)
        del state["processed"]["reprocessed"]
        state["processed"]["baseline"].pop("consolidation_run")
        for lineage in ([], [{"path": "/other/source", "sha256": "changed"}], "malformed"):
            state["processed"]["baseline"]["source_lineage"] = lineage
            with self.subTest(lineage=lineage), self.assertRaisesRegex(self.app.ConfigError, "lacks exact run attribution"):
                self.app.run_processed_entries(state, run)
        run.pop("integration_scope_version")
        with self.assertRaisesRegex(self.app.ConfigError, "Legacy run lacks exact"):
            self.app.run_processed_entries(state, run)

    def test_baseline_finalization_needs_run_identity_without_mutation(self):
        source = self.root / "inbox" / "baseline.md"
        source.write_text('---\nid: "baseline"\nstatus: "inbox"\n---\n# Baseline\n')
        before = source.read_bytes()
        self.app.pipeline_store(self.root).begin("run-a", "noon", initial_inbox=[{"id": "baseline", **self.app.snapshot(str(source))}])
        state_before = self.app.load_state(self.root)
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--file", str(source),
                                                  "--status", "processed", "--rationale", "Reviewed."])
        with self.assertRaisesRegex(self.app.ConfigError, "requires the active --run-id"):
            self.app.command_finalize_note(args)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(self.app.load_state(self.root), state_before)
        self.assertFalse(list((self.root / "state" / "finalizations").glob("*.json")))
        args.run_id = "run-a"
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        entry = self.app.load_state(self.root)["processed"]["baseline"]
        self.assertEqual(entry["consolidation_run"], "run-a")
        self.assertIn('consolidation_run: "run-a"', Path(entry["path"]).read_text())

    def test_extra_finalization_needs_active_run_id_and_persists_it(self):
        pipeline = self.app.pipeline_store(self.root)
        pipeline.begin("current-run", "noon", initial_inbox=[])
        source = self.root / "inbox" / "extra.md"
        source.write_text('---\nid: "extra"\nstatus: "inbox"\n---\n# Extra\n')
        args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--file", str(source),
                                                  "--status", "processed", "--rationale", "Reviewed."])
        with self.assertRaisesRegex(self.app.ConfigError, "requires the active --run-id"):
            self.app.command_finalize_note(args)
        self.assertTrue(source.exists())
        args.run_id = "current-run"
        with redirect_stdout(io.StringIO()):
            self.app.command_finalize_note(args)
        self.assertEqual(self.app.load_state(self.root)["processed"]["extra"]["consolidation_run"], "current-run")

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
                self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)
                link.unlink()
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 0)

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
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)

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
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)
        (self.source / "helper.py").write_text("safe = True\n")
        (self.copy / "helper.py").write_text("safe = True\n")
        receipt = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        Path(receipt["path"]).write_text("{}")
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)

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
        self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)
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
        pipeline.begin("run-1", "noon", initial_inbox=[])
        target = str(self.source / "SKILL.md")
        pipeline.audit("run-1", {"file_snapshots": [self.app.snapshot(target)], "exact_duplicates": [],
                                "near_duplicates": [], "potential_conflicts": []}, [target], True)
        state = self.app.load_state(self.root)
        state["processed"]["note-1"]["processed_at"] = datetime.now().astimezone().isoformat()
        state["processed"]["note-1"]["consolidation_run"] = "run-1"
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

    def test_worktree_relocation_requires_historical_receipt_and_same_repository(self):
        import learning_integration
        old = self.directory / "old"
        old.mkdir()
        def git(repo, *args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git(old, "init", "-q")
        source = old / "AGENTS.md"
        source.write_text("# Instructions\n\nPreserve evidence.\n")
        git(old, "add", "AGENTS.md")
        git(old, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        candidate = self.directory / "release"
        git(old, "worktree", "add", "--detach", str(candidate), "HEAD")
        target = candidate / "AGENTS.md"
        before = self.directory / "historical.md"
        before.write_bytes(source.read_bytes())
        self.note.write_text(self.app.render_frontmatter({"id": "note-1", "prevention_targets":
                             json.dumps([str(source)]), "detection_targets": "[]"}, "# Captured learning\n"))
        digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
        prior = self.directory / "prior.json"
        def historical_receipt(notes):
            import learning_coherence
            manifest = {"version": 1, "coherence": {"version": 1, "reviews": []}, "note_ids": notes}
            artifacts = {"files": {str(source.resolve()): digest(before)}, "skills": {}, "generated": {}}
            proposal = learning_coherence.proposal_hash(manifest, artifacts)
            manifest["coherence"]["reviews"] = [{"verdict": "pass", "proposal_sha256": proposal}]
            return {"manifest": manifest, "verified": {**artifacts, "coherence": {"proposal_sha256": proposal}},
                    "verified_at": "2026-01-01T00:00:00+00:00"}
        prior.write_text(json.dumps(historical_receipt(["note-1"])))
        mapping = {"note_id": "note-1", "source": str(source), "target": str(target),
                   "source_root": str(old.resolve()), "target_root": str(candidate.resolve()),
                   "source_head": git(old, "rev-parse", "HEAD"),
                   "target_head": git(candidate, "rev-parse", "HEAD"),
                   "common_dir": str((old / ".git").resolve()),
                   "note": {"path": str(self.note), "sha256": digest(self.note)},
                   "snapshot": {"path": str(before), "sha256": digest(before)},
                   "prior_receipt": {"path": str(prior), "sha256": digest(prior)}}
        manifest = {"note_ids": ["note-1"], "relocations": [mapping],
                    "coherence": {"targets": [{"path": str(target)}]}}
        covered = {str(target.resolve()): digest(target)}
        result = learning_integration.verify_relocations(manifest, covered)
        self.assertEqual(result["note-1"][str(source.resolve())], str(target.resolve()))
        target.write_text("# Instructions\n\nPreserve evidence and review successors.\n")
        successor_covered = {str(target.resolve()): digest(target)}
        with self.assertRaises(ValueError):
            learning_integration.verify_relocations(manifest, successor_covered)
        semantic = copy.deepcopy(manifest)
        semantic["coherence"] = {"level": "council", "targets": [{"path": str(target),
            "operation": "refine", "before": mapping["snapshot"]}]}
        semantic["relocations"][0].update(schema_version=2, relation="semantic-successor",
            target_sha256=digest(target), note_impacts=[{"note_id": "note-1", "disposition": "preserved",
                "reason": "The reviewed successor retains this obligation.", "note": mapping["note"],
                "prevention_targets": [str(source)], "detection_targets": []}])
        self.assertEqual(learning_integration.verify_relocations(semantic, successor_covered), result)
        for field, value in (("note_id", "spoofed"), ("prevention_targets", [])):
            altered = copy.deepcopy(semantic)
            altered["relocations"][0]["note_impacts"][0][field] = value
            with self.assertRaisesRegex(ValueError, "identity or routing differs"):
                learning_integration.verify_relocations(altered, successor_covered)
        altered = copy.deepcopy(semantic)
        altered["relocations"][0]["note_impacts"][0]["prevention_targets"] = []
        fake_parser = SimpleNamespace(parse_frontmatter=lambda *args, **kwargs: ({"id": "note-1"}, ""),
                                      coerce_list=lambda value: [])
        with patch.dict(sys.modules, {"agent_learning": fake_parser}), \
                self.assertRaisesRegex(ValueError, "identity or routing differs"):
            learning_integration.verify_relocations(altered, successor_covered)
        bad_semantic = copy.deepcopy(semantic)
        bad_semantic["coherence"]["level"] = "peer"
        with self.assertRaises(ValueError):
            learning_integration.verify_relocations(bad_semantic, successor_covered)
        bad_semantic = copy.deepcopy(semantic)
        bad_semantic["relocations"][0]["note_impacts"][0]["disposition"] = "unresolved"
        with self.assertRaises(ValueError):
            learning_integration.verify_relocations(bad_semantic, successor_covered)
        target.write_bytes(before.read_bytes())
        source.write_text("# Restored main checkout\n")
        self.assertEqual(learning_integration.verify_relocations(manifest, covered), result)
        second = self.root / "processed" / "second.md"
        second.write_text("# Another learning\n")
        prior.write_text(json.dumps(historical_receipt(["note-1", "note-2"])))
        multi = copy.deepcopy(manifest)
        multi["note_ids"].append("note-2")
        multi["relocations"][0]["prior_receipt"]["sha256"] = digest(prior)
        extra = copy.deepcopy(multi["relocations"][0])
        extra["note_id"] = "note-2"
        extra["note"] = {"path": str(second), "sha256": digest(second)}
        multi["relocations"].append(extra)
        with patch.object(learning_integration.subprocess, "run", wraps=subprocess.run) as git_calls:
            self.assertEqual(set(learning_integration.verify_relocations(multi, covered)), {"note-1", "note-2"})
        self.assertEqual(git_calls.call_count, 19)
        prior.write_text(json.dumps(historical_receipt(["note-1"])))
        cases = []
        wrong_head = copy.deepcopy(manifest)
        wrong_head["relocations"][0]["target_head"] = "0" * 40
        cases.append(wrong_head)
        wrong_source_head = copy.deepcopy(manifest)
        wrong_source_head["relocations"][0]["source_head"] = "0" * 40
        cases.append(wrong_source_head)
        wrong_path = copy.deepcopy(manifest)
        wrong_path["relocations"][0]["target"] = str(candidate / "other.md")
        cases.append(wrong_path)
        circular = copy.deepcopy(manifest)
        circular["relocations"][0]["snapshot"]["path"] = str(target)
        cases.append(circular)
        hardlink = self.directory / "hardlinked-snapshot.md"
        os.link(target, hardlink)
        linked = copy.deepcopy(manifest)
        linked["relocations"][0]["snapshot"]["path"] = str(hardlink)
        cases.append(linked)
        duplicate = copy.deepcopy(manifest)
        duplicate["relocations"].append(copy.deepcopy(mapping))
        cases.append(duplicate)
        wrong_note = copy.deepcopy(manifest)
        wrong_note["relocations"][0]["note_id"] = "not-covered"
        cases.append(wrong_note)
        minimal_receipt = self.directory / "minimal-receipt.json"
        minimal_receipt.write_text(json.dumps({"manifest": {"note_ids": ["note-1"]},
                                              "verified": {"files": {str(source.resolve()): digest(before)}}}))
        forged = copy.deepcopy(manifest)
        forged["relocations"][0]["prior_receipt"] = {"path": str(minimal_receipt),
                                                     "sha256": digest(minimal_receipt)}
        cases.append(forged)
        context_only = copy.deepcopy(manifest)
        context_only["coherence"]["targets"] = []
        cases.append(context_only)
        (old / "untracked.md").write_text("untracked\n")
        (candidate / "untracked.md").write_text("untracked\n")
        untracked = copy.deepcopy(manifest)
        untracked["relocations"][0].update(source=str(old / "untracked.md"),
                                            target=str(candidate / "untracked.md"))
        cases.append(untracked)
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "AGENTS.md").write_bytes(target.read_bytes())
        (candidate / "escaped").symlink_to(outside, target_is_directory=True)
        escaped = copy.deepcopy(manifest)
        escaped["relocations"][0]["target"] = str(candidate / "escaped" / "AGENTS.md")
        cases.append(escaped)
        unrelated = self.directory / "unrelated"
        unrelated.mkdir()
        git(unrelated, "init", "-q")
        (unrelated / "AGENTS.md").write_bytes(target.read_bytes())
        git(unrelated, "add", "AGENTS.md")
        git(unrelated, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        wrong_repo = copy.deepcopy(manifest)
        wrong_repo["relocations"][0].update(target=str(unrelated / "AGENTS.md"),
                                             target_root=str(unrelated.resolve()),
                                             target_head=git(unrelated, "rev-parse", "HEAD"))
        cases.append(wrong_repo)
        for bad in cases:
            with self.subTest(mapping=bad["relocations"]):
                with self.assertRaises(ValueError):
                    learning_integration.verify_relocations(bad, covered)
        original_run = subprocess.run
        changed_head = False
        def move_head_after_inventory(args, **kwargs):
            nonlocal changed_head
            output = original_run(args, **kwargs)
            if not changed_head and "worktree" in args and "list" in args:
                changed_head = True
                (candidate / "marker.txt").write_text("new commit\n")
                git(candidate, "add", "marker.txt")
                git(candidate, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-qm", "changed head")
            return output
        with patch.object(learning_integration.subprocess, "run", side_effect=move_head_after_inventory):
            with self.assertRaises(ValueError):
                learning_integration.verify_relocations(manifest, covered)
        self.assertTrue(changed_head)
        git(candidate, "checkout", "--detach", mapping["target_head"])
        swapped = False
        saved_target = self.directory / "candidate-before-swap.md"
        def swap_target_after_inventory(args, **kwargs):
            nonlocal swapped
            output = original_run(args, **kwargs)
            if not swapped and "worktree" in args and "list" in args:
                swapped = True
                target.rename(saved_target)
                target.symlink_to(outside / "AGENTS.md")
            return output
        with patch.object(learning_integration.subprocess, "run", side_effect=swap_target_after_inventory):
            with self.assertRaises(ValueError):
                learning_integration.verify_relocations(manifest, covered)
        self.assertTrue(swapped)
        target.unlink()
        saved_target.rename(target)
        self.note.write_text("# Changed note\n")
        with self.assertRaises(ValueError):
            learning_integration.verify_relocations(manifest, covered)
        self.note.write_text("# Captured learning\n")
        target.write_text("# Changed candidate\n")
        with self.assertRaises(ValueError):
            learning_integration.verify_relocations(manifest, covered)
        target.write_bytes(before.read_bytes())
        before.write_text("tampered\n")
        with self.assertRaises(ValueError):
            learning_integration.verify_relocations(manifest, covered)

    def test_record_relocation_preserves_routing_and_rechecks_receipt(self):
        old = self.directory / "git-main"
        old.mkdir()
        def git(repo, *args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git(old, "init", "-q")
        source = old / "AGENTS.md"
        source.write_text("# Instructions\n\nPreserve original evidence.\n")
        git(old, "add", "AGENTS.md")
        git(old, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        release = self.directory / "git-release"
        git(old, "worktree", "add", "--detach", str(release), "HEAD")
        target = release / "AGENTS.md"
        state = self.app.load_state(self.root)
        state["processed"]["note-1"].update(prevention_targets=[str(source)],
                                             archive_sha256=hashlib.sha256(self.note.read_bytes()).hexdigest())
        second = self.root / "processed" / "second.md"
        second.write_text("# Another learning\n")
        state["processed"]["note-2"] = {"path": str(second), "status": "processed",
            "template_upstream_status": "not-applicable", "prevention_targets": [str(source)],
            "archive_sha256": hashlib.sha256(second.read_bytes()).hexdigest()}
        self.app.save_state(self.root, state)
        self.manifest["note_ids"].append("note-2")
        self.manifest["files"].append({**self.app.snapshot(str(source)),
                                       "contains": ["Preserve original evidence."]})
        self.assertEqual(self.record(), 0)
        prior = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        backup = self.directory / "historical-snapshot.md"
        backup.write_bytes(source.read_bytes())
        source.write_text("# Old worktree restored\n")
        self.manifest["files"][-1] = {**self.app.snapshot(str(target)), "contains": ["Preserve original evidence."]}
        self.manifest["relocations"] = [{"note_id": "note-1", "source": str(source), "target": str(target),
            "source_root": str(old.resolve()), "target_root": str(release.resolve()),
            "source_head": git(old, "rev-parse", "HEAD"), "target_head": git(release, "rev-parse", "HEAD"),
            "common_dir": str((old / ".git").resolve()), "snapshot": self.app.snapshot(str(backup)),
            "prior_receipt": prior, "note": self.app.snapshot(str(self.note))}]
        second_mapping = copy.deepcopy(self.manifest["relocations"][0])
        second_mapping["note_id"] = "note-2"
        second_mapping["note"] = self.app.snapshot(str(second))
        self.manifest["relocations"].append(second_mapping)
        fake_prior = self.root / "reports" / "integrations" / "copied-receipt.json"
        fake_prior.write_bytes(Path(prior["path"]).read_bytes())
        self.manifest["relocations"][0]["prior_receipt"] = self.app.snapshot(str(fake_prior))
        self.assertEqual(self.record(), 2)
        self.manifest["relocations"][0]["prior_receipt"] = prior
        original_verify = self.app.verify_manifest_bounded
        calls = 0
        def change_during_record(manifest, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                target.write_text("# Concurrent modification\n")
            return original_verify(manifest, **kwargs)
        with patch.object(self.app, "verify_manifest_bounded", side_effect=change_during_record):
            self.assertEqual(self.record(), 2)
        self.assertEqual(self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"], prior)
        target.write_bytes(backup.read_bytes())
        self.assertEqual(self.record(), 0)
        entry = self.app.load_state(self.root)["processed"]["note-1"]
        self.assertEqual(entry["prevention_targets"], [str(source)])
        self.assertNotEqual(entry["integration_receipt"], prior)
        self.assertEqual(self.command("integration-check", "--manifest", str(self.manifest_path))[0], 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-2", "--require-verified")[0], 0)
        for change in ({"prevention_targets": [str(self.directory / "unrelated.md")]},
                       {"archive_sha256": "0" * 64}):
            state = self.app.load_state(self.root)
            previous = copy.deepcopy(state["processed"]["note-1"])
            state["processed"]["note-1"].update(change)
            self.app.save_state(self.root, state)
            self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 1)
            state["processed"]["note-1"] = previous
            self.app.save_state(self.root, state)
        target.write_text("# Candidate changed\n")
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 1)

    def test_semantic_successor_after_identical_worktree_relocation(self):
        old = self.directory / "chain-main"
        old.mkdir()
        def git(repo, *args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git(old, "init", "-q")
        source = old / "AGENTS.md"
        source.write_text("# Instructions\n\nPreserve original evidence.\n")
        git(old, "add", "AGENTS.md")
        git(old, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
        release = self.directory / "chain-release"
        git(old, "worktree", "add", "--detach", str(release), "HEAD")
        target = release / "AGENTS.md"
        self.note.write_text(self.app.render_frontmatter({"id": "note-1", "prevention_targets":
                             json.dumps([str(source)]), "detection_targets": "[]"}, "# Learning\n"))
        state = self.app.load_state(self.root)
        state["processed"]["note-1"].update(prevention_targets=[str(source)], detection_targets=[],
                                             archive_sha256=hashlib.sha256(self.note.read_bytes()).hexdigest())
        self.app.save_state(self.root, state)
        self.manifest["files"].append({**self.app.snapshot(str(source)), "contains": ["Preserve original evidence."]})
        self.assertEqual(self.record(), 0)
        prior = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        before = self.directory / "chain-before.md"
        before.write_bytes(source.read_bytes())
        self.manifest["files"][-1] = {**self.app.snapshot(str(target)), "contains": ["Preserve original evidence"]}
        mapping = {"note_id": "note-1", "source": str(source), "target": str(target),
                   "source_root": str(old.resolve()), "target_root": str(release.resolve()),
                   "source_head": git(old, "rev-parse", "HEAD"), "target_head": git(release, "rev-parse", "HEAD"),
                   "common_dir": str((old / ".git").resolve()), "snapshot": self.app.snapshot(str(before)),
                   "prior_receipt": prior, "note": self.app.snapshot(str(self.note))}
        self.manifest["relocations"] = [mapping]
        self.assertEqual(self.record(), 0)
        intermediate = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        self.assertNotEqual(intermediate, prior)
        target.write_text("# Instructions\n\nPreserve original evidence and review successors.\n")
        self.manifest["files"][-1] = {**self.app.snapshot(str(target)), "contains": ["Preserve original evidence"]}
        mapping.update(schema_version=2, relation="semantic-successor", target_sha256=self.app.snapshot(str(target))["sha256"],
                       note_impacts=[{"note_id": "note-1", "disposition": "preserved", "reason": "The rule remains.",
                                      "note": self.app.snapshot(str(self.note)), "prevention_targets": [str(source)],
                                      "detection_targets": []}])
        captured = json.loads(Path(intermediate["path"]).read_text())
        self.assertTrue(self.app.chained_relocation_matches(mapping, captured))
        shared_review_cache = {}
        with patch.object(self.app, "read_before_deadline", wraps=self.app.read_before_deadline) as read_review:
            for _ in range(10):
                self.assertTrue(self.app.chained_relocation_matches(mapping, captured, review_cache=shared_review_cache))
        self.assertEqual(read_review.call_count, len(captured["manifest"]["coherence"]["reviews"]))
        for mutate in ("source", "verified_target", "review", "duplicate", "blank_reviewer", "wrong_role"):
            altered = copy.deepcopy(captured)
            if mutate == "source":
                altered["manifest"]["relocations"][0]["source"] = str(self.directory / "unrelated.md")
            elif mutate == "verified_target":
                altered["verified"]["files"][str(target.resolve())] = "0" * 64
            elif mutate == "review":
                altered["manifest"]["coherence"]["reviews"][0]["verdict"] = "fail"
            elif mutate == "blank_reviewer":
                altered["manifest"]["coherence"]["reviews"][0]["reviewer"] = ""
            elif mutate == "wrong_role":
                altered["manifest"]["coherence"]["reviews"][0]["role"] = "policy"
            else:
                altered["manifest"]["relocations"].append(copy.deepcopy(altered["manifest"]["relocations"][0]))
            with self.subTest(mutation=mutate):
                self.assertFalse(self.app.chained_relocation_matches(mapping, altered))
        review_path = Path(captured["manifest"]["coherence"]["reviews"][0]["evidence"]["path"])
        review_bytes = review_path.read_bytes()
        duplicate_manifest = copy.deepcopy(self.manifest)
        duplicate_manifest["relocations"].append(copy.deepcopy(mapping))
        original_match = self.app.chained_relocation_matches
        calls = 0
        def mutate_after_second_mapping(*args, **kwargs):
            nonlocal calls
            result = original_match(*args, **kwargs)
            calls += 1
            if calls == 2:
                review_path.write_bytes(review_bytes + b"changed\n")
            return result
        with patch.object(self.app, "chained_relocation_matches", side_effect=mutate_after_second_mapping):
            with self.assertRaisesRegex(self.app.ConfigError, "review evidence changed"):
                self.app.validate_relocation_bindings(self.root, duplicate_manifest, self.app.load_state(self.root))
        self.assertEqual(calls, 2)
        review_path.write_bytes(review_bytes)
        review_path.write_bytes(review_bytes + b"changed\n")
        self.assertEqual(self.record(), 2)
        review_path.write_bytes(review_bytes)
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 0)
        intermediate_semantic = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        council_receipt = json.loads(Path(intermediate_semantic["path"]).read_text())
        target.write_text("# Instructions\n\nPreserve original evidence and review successors twice.\n")
        self.manifest["files"][-1] = {**self.app.snapshot(str(target)), "contains": ["Preserve original evidence"]}
        mapping["target_sha256"] = self.app.snapshot(str(target))["sha256"]
        self.assertTrue(self.app.chained_relocation_matches(mapping, council_receipt))
        for mutate in ("duplicate_role", "duplicate_reviewer", "editor_reviewer", "padded_duplicate", "padded_editor"):
            altered = copy.deepcopy(council_receipt)
            coherence = altered["manifest"]["coherence"]
            if mutate == "duplicate_role":
                coherence["reviews"][1]["role"] = coherence["reviews"][0]["role"]
            elif mutate == "duplicate_reviewer":
                coherence["reviews"][1]["reviewer"] = coherence["reviews"][0]["reviewer"]
            elif mutate == "editor_reviewer":
                coherence["reviews"][0]["reviewer"] = coherence["editor"]
            elif mutate == "padded_duplicate":
                coherence["reviews"][1]["reviewer"] = " " + coherence["reviews"][0]["reviewer"]
            else:
                coherence["reviews"][0]["reviewer"] = coherence["editor"] + " "
            with self.subTest(mutation=mutate):
                self.assertFalse(self.app.chained_relocation_matches(mapping, altered))
        missing_report = copy.deepcopy(council_receipt)
        missing_report["manifest"]["coherence"]["reviews"][0]["evidence"]["path"] = str(self.directory / "missing-review.txt")
        with self.assertRaises(OSError):
            self.app.chained_relocation_matches(mapping, missing_report)
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 0)

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
            self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)

    def test_semantic_successor_requires_council_and_tracks_the_entire_note_group(self):
        old = self.directory / "successor-main"
        old.mkdir()
        def git(repo, *args):
            return subprocess.run(["git", "-C", str(repo), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()
        git(old, "init", "-q")
        source = old / "AGENTS.md"
        source.write_text("# Instructions\n\nPreserve the original rule.\n")
        git(old, "add", "AGENTS.md")
        git(old, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "-qm", "fixture")
        release = self.directory / "successor-release"
        git(old, "worktree", "add", "--detach", str(release), "HEAD")
        target = release / "AGENTS.md"
        second = self.root / "processed" / "second.md"
        self.note.write_text(self.app.render_frontmatter({"id": "note-1", "prevention_targets":
                             json.dumps([str(source)]), "detection_targets": "[]"}, "# Captured learning\n"))
        second.write_text(self.app.render_frontmatter({"id": "note-2", "prevention_targets":
                          json.dumps([str(source)]), "detection_targets": "[]"}, "# Second source\n"))
        state = self.app.load_state(self.root)
        state["processed"]["note-1"].update(prevention_targets=[str(source)],
            archive_sha256=hashlib.sha256(self.note.read_bytes()).hexdigest())
        state["processed"]["note-2"] = {"path": str(second), "status": "processed",
            "template_upstream_status": "not-applicable", "prevention_targets": [str(source)],
            "archive_sha256": hashlib.sha256(second.read_bytes()).hexdigest()}
        self.app.save_state(self.root, state)
        self.manifest["note_ids"].append("note-2")
        self.manifest["files"].append({**self.app.snapshot(str(source)), "contains": ["Preserve the original rule."]})
        self.assertEqual(self.record(), 0)
        prior = self.app.load_state(self.root)["processed"]["note-1"]["integration_receipt"]
        backup = self.directory / "successor-before.md"
        backup.write_bytes(source.read_bytes())
        source.write_text("# Restored main\n")
        target.write_text("# Instructions\n\nPreserve the original rule and review the successor.\n")
        self.manifest["files"][-1] = {**self.app.snapshot(str(target)), "contains": ["Preserve the original rule"]}
        self.manifest["files"].append({**self.app.snapshot(str(source)), "contains": ["Restored main"]})
        impacts = [{"note_id": note_id, "disposition": "preserved", "reason": "The old rule remains covered.",
                    "note": self.app.snapshot(str(path)), "prevention_targets": [str(target_path)], "detection_targets": []}
                   for note_id, path, target_path in (("note-1", self.note, source), ("note-2", second, source))]
        common = {"source": str(source), "target": str(target), "source_root": str(old.resolve()),
                  "target_root": str(release.resolve()), "source_head": git(old, "rev-parse", "HEAD"),
                  "target_head": git(release, "rev-parse", "HEAD"), "common_dir": str((old / ".git").resolve()),
                  "snapshot": self.app.snapshot(str(backup)), "prior_receipt": prior, "schema_version": 2,
                  "relation": "semantic-successor", "target_sha256": self.app.snapshot(str(target))["sha256"],
                  "note_impacts": impacts}
        self.manifest["relocations"] = [{**common, "note_id": impact["note_id"], "note": impact["note"]}
                                        for impact in impacts]
        self.auto_review = False
        self.seal_review()
        reviewed = copy.deepcopy(self.manifest)
        self.manifest["relocations"].pop()
        import learning_integration
        with self.assertRaisesRegex(ValueError, "mapping for every note sharing the source"):
            learning_integration.verify_distribution(self.manifest)
        self.assertEqual(self.record(), 2)
        self.manifest = copy.deepcopy(reviewed)
        candidate = next(item for item in self.manifest["coherence"]["targets"]
                         if Path(item["path"]).resolve() == target.resolve())
        candidate["before"] = {**candidate["before"], "sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "exact historical preimage"):
            learning_integration.verify_distribution(self.manifest)
        self.manifest = copy.deepcopy(reviewed)
        other_receipt = self.directory / "second-prior.json"
        other_receipt.write_bytes(Path(prior["path"]).read_bytes())
        other_mapping = copy.deepcopy(self.manifest["relocations"][0])
        other_mapping["prior_receipt"] = self.app.snapshot(str(other_receipt))
        self.manifest["relocations"].append(other_mapping)
        with self.assertRaisesRegex(ValueError, "one historical receipt at a time"):
            learning_integration.verify_distribution(self.manifest)
        self.manifest = copy.deepcopy(reviewed)
        self.manifest["coherence"]["reviews"].pop()
        self.assertEqual(self.record(), 2)
        self.manifest = copy.deepcopy(reviewed)
        self.manifest["coherence"]["reviews"][1]["reviewer"] = self.manifest["coherence"]["reviews"][0]["reviewer"]
        self.assertEqual(self.record(), 2)
        self.manifest = copy.deepcopy(reviewed)
        self.manifest["coherence"]["reviews"][1]["proposal_sha256"] = "0" * 64
        self.assertEqual(self.record(), 2)
        for mutation in ("missing", "duplicate", "unresolved", "bad-note-id"):
            self.manifest = copy.deepcopy(reviewed)
            changed = self.manifest["relocations"][0]["note_impacts"]
            if mutation == "missing":
                changed.pop()
            elif mutation == "duplicate":
                changed[1]["note_id"] = changed[0]["note_id"]
            elif mutation == "unresolved":
                changed[1]["disposition"] = "unresolved"
            else:
                changed[1]["note_id"] = []
            self.assertEqual(self.record(), 2, mutation)
        self.manifest = copy.deepcopy(reviewed)
        state = self.app.load_state(self.root)
        original = state["processed"]["note-2"]["prevention_targets"]
        state["processed"]["note-2"]["prevention_targets"] = [str(self.directory / "changed.md")]
        self.app.save_state(self.root, state)
        self.assertEqual(self.record(), 2)
        state["processed"]["note-2"]["prevention_targets"] = original
        self.app.save_state(self.root, state)
        for evidence in (backup, target, second):
            original_bytes = evidence.read_bytes()
            evidence.write_bytes(original_bytes + b"changed\n")
            self.assertEqual(self.record(), 2)
            evidence.write_bytes(original_bytes)
        self.assertEqual(self.record(), 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 0)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-2", "--require-verified")[0], 0)
        state = self.app.load_state(self.root)
        state["processed"]["note-2"]["prevention_targets"] = [str(self.directory / "changed.md")]
        self.app.save_state(self.root, state)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-1", "--require-verified")[0], 1)
        self.assertEqual(self.command("integration-audit", "--note-id", "note-2", "--require-verified")[0], 1)

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
                patch.object(learning_integration.importlib.util, "module_from_spec", return_value=module), \
                patch.object(self.app, "verify_manifest_bounded", side_effect=lambda manifest, **_: learning_integration.verify(manifest)):
            self.assertEqual(self.record(), 2)
            init.check_output = lambda *args: {"status": "current"}
            self.assertEqual(self.record(), 0)
            output.write_text("GENERATED\nChanged outside this integration.\n")
            self.assertEqual(self.command("integration-audit", "--all-history", "--require-verified")[0], 1)
            self.manifest["template_consumers"][0]["roots"] = [str(project / "missing")]
            self.assertEqual(self.record(), 2)


if __name__ == "__main__":
    unittest.main()
