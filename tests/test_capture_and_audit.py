from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_agent_learning as support


class CaptureAndAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.helper = support.AgentLearningTests()
        self.config = self.helper.write_config(self.directory)
        self.root = Path(self.run_helper("init-store").stdout.strip())
        self.module = support.load_agent_learning_module()

    def run_helper(self, *args: str, **kwargs):
        return self.helper.run_helper(self.config, *args, **kwargs)

    def capture_args(self, *args: str):
        return [
            "record", "--project-path", str(self.directory / "project"),
            "--title", "Shared validation", "--problem", "The same check was rediscovered.",
            "--source-run-id", "run-a", "--lesson-family", "shared-validation",
            *args,
        ]

    def record(self, *args: str, check: bool = True):
        return self.run_helper(*self.capture_args(*args), check=check)

    def state(self):
        return json.loads((self.root / "state" / "processed.json").read_text())

    def test_capture_is_idempotent_across_terminal_producers(self) -> None:
        first = Path(self.record("--source-skill", "retro").stdout.strip())
        before = first.read_bytes(), first.stat().st_mtime_ns
        second = Path(self.record("--source-skill", "codex-review-skill-autolearn").stdout.strip())
        self.assertEqual(first, second)
        self.assertEqual(before, (first.read_bytes(), first.stat().st_mtime_ns))
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)

    def test_same_family_in_another_run_or_project_preserves_occurrence(self) -> None:
        paths = {
            self.record().stdout.strip(),
            self.record("--source-run-id", "run-b").stdout.strip(),
            self.record("--project-path", str(self.directory / "other-project")).stdout.strip(),
        }
        self.assertEqual(len(paths), 3)

    def test_changed_capture_content_is_a_conflict_not_a_second_note(self) -> None:
        note = Path(self.record().stdout.strip())
        before = note.read_bytes(), self.state()
        result = self.record("--problem", "An independent observation.", check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("different content", result.stderr)
        self.assertEqual(before, (note.read_bytes(), self.state()))
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)

    def test_retry_finds_the_same_capture_after_finalization(self) -> None:
        note = self.record().stdout.strip()
        archived = self.run_helper(
            "finalize-note", "--file", note, "--status", "processed", "--rationale", "Already covered."
        ).stdout.strip()
        self.assertEqual(self.record().stdout.strip(), archived)
        self.assertFalse(list((self.root / "inbox").glob("*.md")))

    def test_concurrent_captures_share_one_identity(self) -> None:
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.record().stdout.strip(), range(5)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(self.state()["captures"]), 1)

    def test_concurrent_first_captures_do_not_reset_new_state(self) -> None:
        (self.root / "state" / "processed.json").unlink()
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: self.record().stdout.strip(), range(5)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(self.state()["captures"]), 1)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)

    def test_project_alias_uses_the_same_terminal_identity(self) -> None:
        project = self.directory / "project"
        project.mkdir()
        alias = self.directory / "project-alias"
        alias.symlink_to(project, target_is_directory=True)
        first = self.record().stdout.strip()
        self.assertEqual(self.record("--project-path", str(alias)).stdout.strip(), first)

    def test_run_identity_needs_an_explicit_family(self) -> None:
        result = self.run_helper("record", "--source-run-id", "run", check=False)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(list((self.root / "inbox").glob("*.md")))

    def test_capture_rejects_malformed_state_before_writing(self) -> None:
        for state in ({"captures": []}, {"processed": []}, []):
            with self.subTest(state=state):
                (self.root / "state" / "processed.json").write_text(json.dumps(state))
                result = self.record(check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(list((self.root / "inbox").glob("*.md")))

    def test_capture_state_save_failure_leaves_no_unindexed_note(self) -> None:
        args = self.module.build_parser().parse_args([
            "--config", str(self.config), "record", "--source-run-id", "run",
            "--lesson-family", "family",
        ])
        with patch.object(self.module, "save_state", side_effect=self.module.ConfigError("disk failure")):
            with self.assertRaises(self.module.ConfigError):
                self.module.command_record(args)
        self.assertFalse(list((self.root / "inbox").glob("*.md")))
        self.assertNotIn("captures", self.state())

    def worker(self, body: str, *args: str):
        worker = self.directory / "worker.py"
        worker.write_text(
            "import importlib.util, os, sys, time\n"
            "from contextlib import contextmanager\n"
            f"spec = importlib.util.spec_from_file_location('worker_module', {str(support.SCRIPT)!r})\n"
            "module = importlib.util.module_from_spec(spec)\nspec.loader.exec_module(module)\n"
            + body + "\nraise SystemExit(module.main(sys.argv[1:]))\n"
        )
        env = {key: value for key, value in os.environ.items() if not key.startswith("AGENT_LEARNING_")}
        return [sys.executable, str(worker), "--config", str(self.config), *args], env

    def interrupted_capture(self):
        command, env = self.worker("module.save_state = lambda *args: os._exit(91)", *self.capture_args())
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 91, result.stderr)
        notes = list((self.root / "inbox").glob("*.md"))
        self.assertEqual(len(notes), 1)
        self.assertNotIn("captures", self.state())
        return notes[0]

    def test_process_death_before_index_commit_recovers_one_capture(self) -> None:
        note = self.interrupted_capture()
        before = note.read_bytes()
        result = Path(self.record().stdout.strip())
        self.assertEqual(result.resolve(), note.resolve())
        self.assertEqual(result.read_bytes(), before)
        self.assertEqual(len(list((self.root / "inbox").glob("*.md"))), 1)
        self.assertEqual(len(self.state()["captures"]), 1)

    def test_unindexed_capture_is_found_after_archival(self) -> None:
        note = self.interrupted_capture()
        archived = Path(self.run_helper(
            "finalize-note", "--file", str(note), "--status", "processed", "--rationale", "Covered."
        ).stdout.strip())
        self.assertEqual(Path(self.record().stdout.strip()).resolve(), archived.resolve())
        self.assertEqual(len(list((self.root / "processed").glob("*/*/*.md"))), 1)
        self.assertFalse(list((self.root / "inbox").glob("*.md")))

    def interrupted_finalization(self, note: Path, body: str = "module.save_state = lambda *args: os._exit(91)"):
        command, env = self.worker(
            body, "finalize-note", "--file", str(note), "--status", "processed", "--rationale", "Covered."
        )
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertIn(result.returncode, [91, 92], result.stderr)
        self.assertEqual(len(list((self.root / "state" / "finalizations").glob("*.json"))), 1)

    def test_capture_retry_recovers_finalization_state_after_process_death(self) -> None:
        note = Path(self.record().stdout.strip())
        self.interrupted_finalization(note)
        self.assertFalse(note.exists())
        self.assertEqual(self.state()["processed"], {})
        retried = Path(self.record().stdout.strip())
        self.assertTrue(retried.is_file())
        self.assertIn("processed", retried.parts)
        self.assertEqual(len(self.state()["processed"]), 1)
        summary = json.loads(self.run_helper("summarize-runs").stdout)
        self.assertEqual(summary["processed_count"], 1)
        self.assertEqual(summary["lesson_families"], {"shared-validation": 1})
        self.assertEqual(summary["pending_finalizations"], 0)

    def test_pending_transaction_is_visible_and_blocks_false_completion(self) -> None:
        note = Path(self.record().stdout.strip())
        self.interrupted_finalization(note)
        before = self.state()
        pending = json.loads(self.run_helper("prepare-run").stdout)
        self.assertEqual(len(pending["pending_finalizations"]), 1)
        self.assertEqual(self.state(), before)
        report = self.run_helper("write-report", "--summary", "Complete.", check=False)
        self.assertEqual(report.returncode, 2, report.stderr)
        self.assertFalse(list((self.root / "reports").glob("*.md")))
        recovered = json.loads(self.run_helper("recover-finalizations").stdout)
        self.assertEqual(recovered["recovered"][0]["outcome"], "committed")
        self.assertEqual(json.loads(self.run_helper("prepare-run").stdout)["pending_finalizations"], [])

    def test_report_holds_the_store_lock_through_its_pending_check_and_write(self) -> None:
        checked = threading.Event()
        attempted = threading.Event()
        acquired = threading.Event()
        journal = self.root / "state" / "finalizations" / "concurrent.json"

        def finalizer():
            if not checked.wait(5):
                return
            attempted.set()
            with self.module.state_update_lock(self.root):
                acquired.set()
                self.module.atomic_write_text(journal, "{}")

        real_journals = self.module.finalization_journals

        def observe_check(root):
            result = real_journals(root)
            checked.set()
            self.assertTrue(attempted.wait(5))
            self.assertFalse(acquired.wait(0.1), "finalizer interleaved with report snapshot")
            return result

        thread = threading.Thread(target=finalizer, daemon=True)
        thread.start()
        args = self.module.build_parser().parse_args([
            "--config", str(self.config), "write-report", "--run-id", "atomic-report", "--summary", "Complete snapshot."
        ])
        try:
            with patch.object(self.module, "finalization_journals", observe_check):
                self.module.command_write_report(args)
        finally:
            thread.join(6)
        self.assertFalse(thread.is_alive())
        self.assertTrue(acquired.is_set())
        self.assertTrue((self.root / "reports" / "atomic-report.md").exists())
        self.assertTrue(journal.exists())

    def test_recovery_after_state_commit_only_cleans_the_journal(self) -> None:
        note = Path(self.record().stdout.strip())
        body = (
            "original_unlink = module.Path.unlink\n"
            "def interrupted_unlink(path, *args, **kwargs):\n"
            "    if path.parent.name == 'finalizations' and path.suffix == '.json': os._exit(91)\n"
            "    return original_unlink(path, *args, **kwargs)\n"
            "module.Path.unlink = interrupted_unlink"
        )
        self.interrupted_finalization(note, body)
        before = self.state()
        result = json.loads(self.run_helper("recover-finalizations").stdout)
        self.assertEqual(result["recovered"][0]["outcome"], "committed")
        self.assertEqual(self.state(), before)
        self.assertEqual(len(list((self.root / "processed").glob("*/*/*.md"))), 1)

    def test_in_place_review_finalization_recovers_without_losing_human_choices(self) -> None:
        note = Path(self.record().stdout.strip())
        pending = Path(self.run_helper(
            "finalize-note", "--file", str(note), "--status", "needs-review", "--rationale", "Pending."
        ).stdout.strip())
        text = pending.read_text().replace("- [ ] Approve:", "- [x] Approve:")
        pending.write_text(text)
        command, env = self.worker(
            "module.save_state = lambda *args: os._exit(91)",
            "finalize-note", "--file", str(pending), "--status", "needs-review", "--rationale", "Still pending."
        )
        self.assertEqual(subprocess.run(command, env=env, capture_output=True).returncode, 91)
        self.run_helper("recover-finalizations")
        self.assertIn("- [x] Approve:", pending.read_text())
        self.assertEqual(len(list((self.root / "needs-review").glob("*.md"))), 1)
        self.assertFalse(list((self.root / "processed").glob("*/*/*.md")))

    def test_recovery_before_move_restores_the_original_queued_note(self) -> None:
        note = Path(self.record().stdout.strip())
        before = note.read_bytes()
        self.interrupted_finalization(note, "module.shutil.move = lambda *args: os._exit(92)")
        self.assertNotEqual(note.read_bytes(), before)
        result = json.loads(self.run_helper("recover-finalizations").stdout)
        self.assertEqual(result["recovered"][0]["outcome"], "rolled-back")
        self.assertEqual(note.read_bytes(), before)
        self.assertEqual(self.state()["processed"], {})
        self.assertFalse(list((self.root / "processed").glob("*/*/*.md")))

    def test_failed_temporary_note_write_preserves_original_bytes(self) -> None:
        note = Path(self.record().stdout.strip())
        before = note.read_bytes()
        args = self.module.build_parser().parse_args([
            "--config", str(self.config), "finalize-note", "--file", str(note),
            "--status", "processed", "--rationale", "Covered.",
        ])
        real_write = Path.write_text

        def interrupted_write(path, text, *values, **kwargs):
            if path.parent == note.parent and path.name.startswith(f".{note.name}."):
                real_write(path, "partial", *values, **kwargs)
                raise OSError("simulated failed temporary write")
            return real_write(path, text, *values, **kwargs)

        with patch.object(Path, "write_text", interrupted_write):
            with self.assertRaises(OSError):
                self.module.command_finalize_note(args)
        self.assertEqual(note.read_bytes(), before)
        self.assertEqual(self.state()["processed"], {})
        self.assertFalse(list((self.root / "state" / "finalizations").glob("*.json")))

    def test_failed_finalization_state_save_rolls_back_note_and_archive(self) -> None:
        note = Path(self.record().stdout.strip())
        before = note.read_bytes(), self.state()
        args = self.module.build_parser().parse_args([
            "--config", str(self.config), "finalize-note", "--file", str(note),
            "--status", "processed", "--rationale", "Covered.",
        ])
        with patch.object(self.module, "save_state", side_effect=self.module.ConfigError("disk failure")):
            with self.assertRaises(self.module.ConfigError):
                self.module.command_finalize_note(args)
        self.assertEqual((note.read_bytes(), self.state()), before)
        self.assertFalse(list((self.root / "processed").glob("*/*/*.md")))

    def test_recovery_preserves_changed_note_and_refuses_foreign_journal_path(self) -> None:
        note = Path(self.record().stdout.strip())
        self.interrupted_finalization(note, "module.shutil.move = lambda *args: os._exit(92)")
        note.write_text(note.read_text() + "\nHuman edit.\n")
        before = note.read_bytes()
        self.assertEqual(self.run_helper("recover-finalizations", check=False).returncode, 2)
        self.assertEqual(note.read_bytes(), before)
        journal = next((self.root / "state" / "finalizations").glob("*.json"))
        data = json.loads(journal.read_text())
        foreign = self.directory / "foreign.md"
        foreign.write_text("# Foreign\n")
        data["source"] = str(foreign)
        journal.write_text(json.dumps(data))
        self.assertEqual(self.run_helper("recover-finalizations", check=False).returncode, 2)
        self.assertEqual(foreign.read_text(), "# Foreign\n")

    def test_concurrent_finalization_cannot_recreate_the_moved_source(self) -> None:
        note = self.record().stdout.strip()
        ready = self.directory / "barrier"
        ready.mkdir()
        body = (
            "from pathlib import Path\n"
            "original_lock = module.state_update_lock\nlock_calls = 0\n"
            "@contextmanager\ndef synchronized_lock(root):\n"
            "    global lock_calls\n    lock_calls += 1\n"
            "    if lock_calls == 2:\n"
            f"        ready = Path({str(ready)!r})\n"
            "        (ready / str(os.getpid())).touch()\n"
            "        deadline = time.monotonic() + 10\n"
            "        while len(list(ready.iterdir())) < 2:\n"
            "            if time.monotonic() > deadline: raise RuntimeError('barrier timed out')\n"
            "            time.sleep(0.01)\n"
            "    with original_lock(root): yield\n"
            "module.state_update_lock = synchronized_lock"
        )
        command, env = self.worker(body, "finalize-note", "--file", note, "--status", "processed", "--rationale", "Covered.")
        children = [subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for child in children:
            self.addCleanup(lambda process=child: process.kill() if process.poll() is None else None)
        outputs = [child.communicate(timeout=15) for child in children]
        self.assertEqual(sorted(child.returncode for child in children), [0, 2], outputs)
        self.assertEqual(len(list((self.root / "processed").glob("*/*/*.md"))), 1)
        self.assertEqual(len(self.state()["processed"]), 1)
        self.assertFalse(Path(note).exists())

    def test_capture_retry_rejects_an_outside_store_symlink(self) -> None:
        note = Path(self.record().stdout.strip())
        external = self.directory / "foreign.md"
        note.rename(external)
        note.symlink_to(external)
        before = external.read_bytes()
        result = self.record(check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(external.read_bytes(), before)

    def test_queue_directory_symlink_cannot_move_foreign_sources_or_capture(self) -> None:
        external = self.directory / "external"
        external.mkdir()
        note = external / "foreign.md"
        note.write_text('---\nid: "foreign"\n---\n# Foreign\n')
        (self.root / "inbox").rmdir()
        (self.root / "inbox").symlink_to(external, target_is_directory=True)
        before = note.read_bytes()
        result = self.run_helper("finalize-note", "--file", str(note), "--status", "processed", "--rationale", "Invalid.", check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(note.read_bytes(), before)
        self.assertEqual(self.record(check=False).returncode, 2)
        self.assertEqual(list(external.iterdir()), [note])

    def test_archive_directory_symlink_cannot_move_a_valid_source_outside_store(self) -> None:
        note = Path(self.record().stdout.strip())
        external = self.directory / "external"
        external.mkdir()
        (self.root / "processed").symlink_to(external, target_is_directory=True)
        before = note.read_bytes()
        result = self.run_helper("finalize-note", "--file", str(note), "--status", "processed", "--rationale", "Invalid.", check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(note.read_bytes(), before)
        self.assertFalse(list(external.iterdir()))

    def test_applied_capture_keeps_target_and_verification_snapshots(self) -> None:
        target = self.directory / "SKILL.md"
        target.write_text("# Skill\n\nUse the shared check.\n")
        evidence = self.directory / "validation.log"
        evidence.write_text("Shared validation passed on the recorded inputs.\n")
        note = Path(self.record(
            "--application-status", "applied", "--applied-target", str(target),
            "--application-evidence", str(evidence), "--verification", "Shared check passed."
        ).stdout.strip())
        fm, _ = self.module.read_note(note)
        targets = json.loads(fm["applied_targets"].replace('\\"', '"'))
        self.assertEqual(targets, [{"path": str(target.resolve()), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}])
        self.assertEqual(fm["application_status"], "applied")
        pending = json.loads(self.run_helper("prepare-run").stdout)["inbox"]
        self.assertEqual(pending[0]["application_status"], "applied")
        self.assertEqual(note.parent.name, "inbox")

    def test_application_claim_requires_proof_and_proposal_cannot_claim_targets(self) -> None:
        for args in (
            ("--application-status", "applied"),
            ("--application-status", "applied", "--applied-target", "/missing/target", "--application-evidence", "/missing/proof", "--verification", "Claim"),
            ("--applied-target", "/missing/target"),
        ):
            with self.subTest(args=args):
                result = self.record(*args, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(list((self.root / "inbox").glob("*.md")))

    def test_legacy_capture_without_run_identity_remains_supported(self) -> None:
        first = self.run_helper("record", "--title", "Legacy capture").stdout.strip()
        second = self.run_helper("record", "--title", "Legacy capture").stdout.strip()
        self.assertNotEqual(first, second)

    def audit(self, *args: str, check: bool = True):
        return self.run_helper("audit-rules", "--path", str(self.directory / "rules"), "--all-md", *args, check=check)

    def rules_and_baseline(self):
        rules = self.directory / "rules"
        rules.mkdir()
        for name in ("a.md", "b.md"):
            (rules / name).write_text("# Rules\n\n- Run the complete validation before replacing installed files.\n")
        payload = json.loads(self.audit().stdout)
        baseline = self.directory / "baseline.json"
        baseline.write_text(json.dumps(payload))
        return rules, baseline, payload

    def test_audit_reports_size_without_claiming_semantic_or_check_approval(self) -> None:
        _, _, first = self.rules_and_baseline()
        self.assertEqual(len(first["file_snapshots"]), 2)
        self.assertTrue(first["exact_duplicates"])
        self.assertFalse(first["semantic_approval"])
        self.assertEqual(first["check_effectiveness"], "not-evaluated")
        self.assertGreater(first["file_snapshots"][0]["words"], 0)

    def test_unchanged_audit_reuses_baseline_without_repeating_pairs(self) -> None:
        _, baseline, _ = self.rules_and_baseline()
        before = baseline.read_bytes()
        result = json.loads(self.audit("--baseline", str(baseline)).stdout)
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["exact_duplicates"], [])
        self.assertEqual(result["comparison_scope"], "changed-targets")
        self.assertEqual(baseline.read_bytes(), before)

    def test_changed_target_is_compared_with_unchanged_coverage(self) -> None:
        rules, baseline, _ = self.rules_and_baseline()
        with (rules / "a.md").open("a") as handle:
            handle.write("\nA new conditional reference.\n")
        result = json.loads(self.audit("--baseline", str(baseline)).stdout)
        self.assertEqual(result["changed_files"], [str(rules / "a.md")])
        self.assertTrue(result["exact_duplicates"])
        self.assertEqual(result["file_snapshots"][0]["word_delta"], 4)

    def test_audit_records_removed_target_without_mutation(self) -> None:
        rules, baseline, _ = self.rules_and_baseline()
        (rules / "b.md").unlink()
        before = (rules / "a.md").read_bytes()
        result = json.loads(self.audit("--baseline", str(baseline)).stdout)
        self.assertEqual(result["removed_files"], [str(rules / "b.md")])
        self.assertEqual((rules / "a.md").read_bytes(), before)

    def test_audit_rejects_unusable_baseline(self) -> None:
        rules, baseline, first = self.rules_and_baseline()
        for content in ("invalid json", "{}", json.dumps({"file_snapshots": first["file_snapshots"] * 2})):
            with self.subTest(content=content[:20]):
                baseline.write_text(content)
                result = self.audit("--baseline", str(baseline), check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(len(list(rules.glob("*.md"))), 2)


if __name__ == "__main__":
    unittest.main()
