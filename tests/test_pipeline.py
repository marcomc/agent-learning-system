from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import tempfile
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

    def ref(self, path):
        return self.app.snapshot(str(path))

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

    def audit_payload(self, duplicate=True):
        ref = self.ref(self.target)
        members = [{"path": ref["path"], "line": 3, "text": "Require a verified current revision before publication."},
                   {"path": ref["path"], "line": 5, "text": "Require a verified current revision before publication."}]
        return {"file_snapshots": [{**ref, "words": 10, "lines": 3}],
                "exact_duplicates": [members] if duplicate else [], "near_duplicates": [], "potential_conflicts": []}

    def test_incremental_absence_does_not_close_a_finding_and_context_drift_reopens_it(self):
        self.pipeline.begin("run-1", "midnight")
        self.pipeline.audit("run-1", self.audit_payload(), [str(self.target)], True)
        issue = next(iter(self.pipeline.status()["issues"].values()))
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

    def test_completion_cannot_omit_a_changed_target(self):
        self.pipeline.begin("run-1", "midnight", [self.ref(self.target)])
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
            args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-begin", "--run-id", "inbox-run", "--runner", "noon"])
            with redirect_stdout(io.StringIO()):
                self.app.command_pipeline(args)
            self.pipeline.audit("inbox-run", self.audit_payload(False), [], True)
            with self.assertRaisesRegex(ValueError, "initial inbox"):
                self.pipeline.finish("inbox-run", "completed", "Done", str(self.report), [])
            args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--file", str(inbox), "--status", "processed", "--rationale", "Already covered by the verified instruction."])
            with redirect_stdout(io.StringIO()):
                self.app.command_finalize_note(args)
            self.pipeline.finish("inbox-run", "completed", "Verified", str(self.report), [])

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
                    args = self.app.build_parser().parse_args(["--config", str(self.config), "pipeline-begin", "--run-id", run_id, "--runner", "noon"])
                    with redirect_stdout(io.StringIO()):
                        self.app.command_pipeline(args)
                self.assertEqual(self.pipeline.status()["runs"][run_id]["initial_inbox"][0]["id"], note_id)
                self.pipeline.audit(run_id, self.audit_payload(False), [], True)
                args = self.app.build_parser().parse_args(["--config", str(self.config), "finalize-note", "--file", str(inbox), "--status", "processed", "--rationale", "Covered by current evidence."])
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

    def test_audit_refresh_keeps_a_pending_decision_and_its_options(self):
        self.pipeline.begin("run-1", "midnight")
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
        self.pipeline.begin("run-1", "midnight", [self.ref(self.target)])
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
        self.pipeline.begin("run-1", "midnight", [self.ref(self.target)])
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.target.unlink()
        with self.assertRaisesRegex(ValueError, "removal/uninstall"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [])

    def test_stale_review_sources_cannot_bypass_completion_without_a_post_scan(self):
        self.pipeline.begin("run-1", "midnight")
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.pipeline.update_issue(self.decision())
        self.note.write_text(self.note.read_text() + "\nAdditional evidence changes the decision context.\n")
        with self.assertRaisesRegex(ValueError, "Revalidate"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [])
        revised = self.decision()
        self.pipeline.update_issue(revised)
        self.pipeline.finish("run-1", "completed", "Revalidated", str(self.report), [])

    def test_open_candidate_survives_an_unchanged_delta(self):
        self.pipeline.begin("run-1", "midnight")
        self.pipeline.audit("run-1", self.audit_payload(), [], True)
        self.pipeline.audit("run-1", self.audit_payload(False), [], False)
        self.assertEqual(len(self.pipeline.status()["issues"]), 1)
        self.assertEqual(next(iter(self.pipeline.status()["issues"].values()))["status"], "candidate")
        with self.assertRaisesRegex(ValueError, "Classify every"):
            self.pipeline.finish("run-1", "completed", "Done", str(self.report), [])

    def test_weekly_failure_is_retryable_and_target_verification_is_required(self):
        self.pipeline.begin("failed-run", "midnight")
        self.pipeline.finish("failed-run", "failed", "Audit storage was unavailable", "", [])
        self.assertTrue(self.pipeline.status()["weekly_due"])
        self.pipeline.begin("retry-run", "noon")
        self.pipeline.audit("retry-run", self.audit_payload(False), [], True)
        with self.assertRaisesRegex(ValueError, "post-consolidation audit"):
            self.pipeline.finish("retry-run", "completed", "Done", str(self.report), [str(self.target)])
        self.pipeline.audit("retry-run", self.audit_payload(False), [], False)
        self.pipeline.finish("retry-run", "completed", "Verified", str(self.report), [str(self.target)])
        self.assertEqual(self.pipeline.weekly_release_status()["consolidation"]["id"], "retry-run")

    def test_changed_inputs_cannot_be_accepted_as_a_post_audit(self):
        self.pipeline.begin("run-1", "midnight")
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

    def test_digest_escapes_html_flattens_controls_and_contains_the_same_choices_in_text(self):
        issue = self.decision()
        issue["title"] = '<script>alert(1)</script>\nForged heading'
        issue["options"][0]["label"] = "A" * 240
        self.pipeline.update_issue(issue)
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.assertNotIn("<script>", digest["html"])
        self.assertIn("&lt;script&gt;", digest["html"])
        self.assertNotIn("\nForged heading", digest["body"])
        self.assertIn("codex://threads/" + ORDINARY, digest["html"])
        self.assertIn("codex://threads/" + ORDINARY, digest["body"])
        self.assertIn("open 'codex://threads/" + ORDINARY + "'", digest["body"])
        self.assertIn("Open the chat to respond", digest["html"])
        self.assertIn("Discard", digest["html"])
        self.assertIn("Discard", digest["body"])
        self.assertIn("line-height:1.6", digest["html"])
        self.assertIn("overflow-wrap:anywhere", digest["html"])

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
        self.pipeline.begin("run-1", "midnight")
        self.pipeline.finish("run-1", "failed", "Complete queue could not be read", "", [])
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.assertIn("queue could not be read", digest["body"])
        self.pipeline.begin("run-2", "midnight")
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
            self.assertEqual(call("pipeline-begin", "--run-id", "cli-run", "--runner", "midnight")[0], 0)
            self.assertEqual(call("pipeline-audit", "--run-id", "cli-run", "--weekly")[0], 0)
            weekly = self.pipeline.status()["weekly"]
            captured = json.loads(Path(weekly["snapshot"]["path"]).read_text())["audit"]
            self.assertIn(str(project_rules.resolve()), captured["scanned_files"])
            project_rules.write_text(project_rules.read_text() + "\n- Retain the controller reconciliation result.\n")
            self.assertEqual(call("pipeline-finish", "--run-id", "cli-run", "--status", "completed", "--summary", "Done", "--report", str(self.report))[0], 2)
            self.assertEqual(call("pipeline-audit", "--run-id", "cli-run", "--target", str(project_rules))[0], 0)
            self.assertEqual(call("pipeline-finish", "--run-id", "cli-run", "--status", "completed", "--summary", "Verified", "--report", str(self.report), "--target", str(project_rules))[0], 0)

    def test_italian_digest_keeps_the_verified_local_chat_link(self):
        self.pipeline.configure(ORDINARY, RELEASE, "it")
        self.pipeline.update_issue(self.decision())
        digest = self.pipeline.digest(self.pipeline.status(), self.notes())
        self.assertIn("Decisione:", digest["body"])
        self.assertIn("Apri la chat per rispondere", digest["html"])
        self.assertIn('lang="it"', digest["html"])

    def test_release_gate_rejects_a_newer_failed_cycle(self):
        self.pipeline.begin("run-1", "midnight")
        self.pipeline.audit("run-1", self.audit_payload(False), [], True)
        self.pipeline.finish("run-1", "completed", "Verified", str(self.report), [])
        self.pipeline.begin("run-2", "noon")
        self.pipeline.finish("run-2", "failed", "Queue unreadable", "", [])
        with self.assertRaisesRegex(ValueError, "verified completed cycle"):
            self.pipeline.weekly_release_status()


if __name__ == "__main__":
    unittest.main()
