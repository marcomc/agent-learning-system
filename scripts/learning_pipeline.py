"""Persistent handoffs and presentation for the scheduled learning pipeline."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


OPEN = {"candidate", "investigate", "needs-user"}
STATUSES = OPEN | {"resolved", "justified"}
RUNNERS = {"midnight", "noon"}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
THREAD = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\Z")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def clean(value: Any) -> str:
    """Keep untrusted text inside a single presentation field."""
    return " ".join(str(value).replace("\x00", "").split())


def chat_url(thread_id: str) -> str:
    if not THREAD.fullmatch(thread_id):
        raise ValueError("A verified local Codex thread UUID is required.")
    return f"codex://threads/{thread_id}"


def snapshot(raw: str) -> dict[str, str]:
    path = Path(raw).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Expected a file: {path}")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def current(ref: dict[str, str]) -> bool:
    try:
        return snapshot(ref["path"])["sha256"] == ref["sha256"]
    except (OSError, KeyError, ValueError):
        return False


class Pipeline:
    def __init__(self, root: Path, lock):
        self.root = root.resolve()
        self.lock = lock

    def inside(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Pipeline state/report resolves outside the learning store.")
        return path

    def write(self, path: Path, value: Any) -> None:
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("Pipeline output is outside the learning store.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def load(self) -> dict[str, Any]:
        path = self.inside("state/pipeline.json")
        if not path.exists():
            return {"version": 1, "runs": {}, "issues": {}, "weekly": {},
                    "threads": {}, "sent": {}, "outbox": None}
        state = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(state, dict) or state.get("version") != 1
                or any(not isinstance(state.get(key), dict)
                       for key in ("runs", "issues", "weekly", "threads", "sent"))
                or (state.get("outbox") is not None and not isinstance(state["outbox"], dict))):
            raise ValueError("Malformed pipeline state; preserve it for reconciliation.")
        return state

    def save(self, state: dict[str, Any]) -> None:
        self.write(self.inside("state/pipeline.json"), state)

    @staticmethod
    def cycle(now: datetime) -> str:
        return (now.date() - timedelta(days=now.weekday())).isoformat()

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        with self.lock():
            state = self.load()
            return {**state, "cycle": self.cycle(now),
                    "weekly_due": state["weekly"].get("cycle") != self.cycle(now)}

    def configure(self, ordinary: str, release: str, language: str = "en") -> None:
        chat_url(ordinary)
        chat_url(release)
        if language not in {"en", "it"}:
            raise ValueError("Digest language must be en or it.")
        with self.lock():
            state = self.load()
            state["threads"] = {"ordinary": ordinary, "release": release, "language": language}
            self.save(state)

    def begin(self, run_id: str, runner: str, targets: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if not ID.fullmatch(run_id) or runner not in RUNNERS:
            raise ValueError("Invalid pipeline run ID or runner.")
        with self.lock():
            state = self.load()
            existing = state["runs"].get(run_id)
            if existing:
                if existing["runner"] != runner or existing["status"] != "running":
                    raise ValueError("Run ID already belongs to another or finished run.")
                return existing
            if any(run["status"] == "running" for run in state["runs"].values()):
                raise ValueError("A consolidation is still running; reconcile it before starting another.")
            now = datetime.now().astimezone()
            run = {"id": run_id, "runner": runner, "status": "running",
                   "started_at": now.isoformat(), "cycle": self.cycle(now),
                   "weekly_due": state["weekly"].get("cycle") != self.cycle(now),
                   "initial_targets": targets or [],
                   "verified_targets": []}
            state["runs"][run_id] = run
            self.save(state)
            return run

    def active(self, state: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = state["runs"].get(run_id)
        if not run or run["status"] != "running":
            raise ValueError("A running consolidation is required.")
        return run

    def audit(self, run_id: str, payload: dict[str, Any], roots: list[str], weekly: bool) -> dict[str, Any]:
        if not payload.get("file_snapshots") or any(not current(ref) for ref in payload["file_snapshots"]):
            raise ValueError("Audit targets changed or could not be captured completely.")
        with self.lock():
            state = self.load()
            run = self.active(state, run_id)
            if weekly and state["weekly"].get("cycle") == run["cycle"]:
                return state["weekly"]
            name = f"pipeline-{run_id}-{'weekly' if weekly else 'post'}-{uuid.uuid4().hex[:8]}.json"
            report = self.inside("reports/" + name)
            record = {"run_id": run_id, "cycle": run["cycle"], "roots": roots,
                      "created_at": datetime.now().astimezone().isoformat(), "audit": payload}
            self.write(report, record)
            refs = {ref["path"]: {"path": ref["path"], "sha256": ref["sha256"]}
                    for ref in payload["file_snapshots"]}
            groups = [("exact", group) for group in payload.get("exact_duplicates", [])]
            groups += [(kind, [pair["a"], pair["b"]])
                       for kind, key in (("similar", "near_duplicates"), ("conflict", "potential_conflicts"))
                       for pair in payload.get(key, [])]
            for kind, members in groups:
                identity = sorted({(item["path"], item["text"]) for item in members})
                issue_id = "audit-" + fingerprint([kind, identity])[:24]
                targets = [refs[path] for path in sorted({item["path"] for item in members})]
                old = state["issues"].get(issue_id)
                if old:
                    if old["targets"] != targets:
                        old.setdefault("previous_targets", old["targets"])
                        old["revalidation_required"] = True
                    old.update(targets=targets, last_seen=record["created_at"])
                    continue
                state["issues"][issue_id] = {
                    "id": issue_id, "kind": "audit", "status": "candidate",
                    "title": {"exact": "Repeated instruction", "similar": "Similar instructions",
                              "conflict": "Potential instruction conflict"}[kind],
                    "description": "Lexical discovery requiring semantic assessment: "
                                   + " | ".join(clean(item["text"]) for item in members),
                    "targets": targets, "sources": [], "options": [], "question": "",
                    "evidence": [str(report)], "last_seen": record["created_at"], "resolution": "",
                }
            for issue in state["issues"].values():
                refs = issue.get("targets", []) + (issue.get("sources", []) if issue["status"] in OPEN else [])
                if (issue["kind"] == "audit" or issue["status"] in OPEN) and any(not current(ref) for ref in refs):
                    issue["revalidation_required"] = True
            # Absence from a delta is not evidence of resolution.
            reference = {"cycle": run["cycle"], "run_id": run_id,
                         "snapshot": snapshot(str(report)), "roots": roots}
            if weekly:
                state["weekly"] = reference
            run["post_audit" if not weekly else "weekly_audit"] = reference
            self.save(state)
            return reference

    def update_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(issue, dict) or not ID.fullmatch(str(issue.get("id", ""))):
            raise ValueError("Issue needs a stable ID.")
        with self.lock():
            previous = self.load()["issues"].get(issue["id"], {})
        issue = {**previous, **issue}
        if issue.get("status") not in STATUSES or issue.get("kind") not in {"audit", "review", "operational"}:
            raise ValueError("Invalid issue kind/status.")
        for key in ("title", "description"):
            if not isinstance(issue.get(key), str) or not clean(issue[key]):
                raise ValueError(f"Issue needs {key}.")
        for key in ("targets", "sources"):
            refs = issue.get(key, [])
            if not isinstance(refs, list) or any(not isinstance(ref, dict) or not current(ref) for ref in refs):
                raise ValueError("Issue references must match current file hashes.")
        options = issue.get("options", [])
        if not isinstance(options, list):
            raise ValueError("Options must be a list.")
        if issue["status"] == "needs-user":
            if not clean(issue.get("question", "")) or len(options) < 2:
                raise ValueError("A user decision needs a question and at least two described options.")
            if any(not isinstance(item, dict) or any(not clean(item.get(key, ""))
                   for key in ("id", "label", "description")) for item in options):
                raise ValueError("Each option needs an ID, label and description.")
            if len({item["id"] for item in options}) != len(options):
                raise ValueError("Option IDs must be unique.")
        if issue["status"] in {"resolved", "justified"}:
            if not clean(issue.get("resolution", "")) or not issue.get("evidence"):
                raise ValueError("Closing an issue requires a rationale and verification evidence.")
            for raw in issue["evidence"]:
                path = Path(raw).expanduser().resolve()
                if not path.is_relative_to(self.inside("reports")) or not path.is_file():
                    raise ValueError("Verification evidence must be a saved report in this store.")
        with self.lock():
            state = self.load()
            if state["issues"].get(issue["id"], {}) != previous:
                raise ValueError("Issue changed during assessment; refresh it before updating.")
            merged = {**previous, "sources": [], "targets": [], "options": [], "question": "",
                      "resolution": "", "evidence": [], **issue,
                      "revalidation_required": False,
                      "updated_at": datetime.now().astimezone().isoformat()}
            if previous:
                merged["history"] = previous.get("history", []) + [
                    {key: previous.get(key) for key in ("status", "resolution", "question", "options", "sources", "targets", "evidence", "updated_at")}
                ]
            state["issues"][issue["id"]] = merged
            self.save(state)
            return merged

    def finish(self, run_id: str, status: str, summary: str, report: str, targets: list[str],
               inventory: list[dict[str, str]] | None = None) -> dict[str, Any]:
        if status not in {"completed", "partial", "failed"} or not clean(summary):
            raise ValueError("Run finish needs a status and summary.")
        proof = None
        if report:
            path = Path(report).expanduser().resolve()
            if not path.is_relative_to(self.inside("reports")):
                raise ValueError("Run report must be in this store's reports directory.")
            proof = snapshot(str(path))
        target_refs = [snapshot(raw) for raw in targets]
        with self.lock():
            state = self.load()
            run = self.active(state, run_id)
            if status == "completed":
                if not proof or list(self.inside("state/finalizations").glob("*.json")):
                    raise ValueError("Completion requires a report and no pending finalizations.")
                if run["weekly_due"] and state["weekly"].get("cycle") != run["cycle"]:
                    raise ValueError("The weekly discovery is still due; retry it before completion.")
                if any(issue["status"] == "candidate" for issue in state["issues"].values()):
                    raise ValueError("Classify every open audit candidate before completing consolidation.")
                if any(issue.get("revalidation_required") or (
                    issue["status"] in OPEN and any(not current(ref) for ref in issue.get("sources", []) + issue.get("targets", []))
                ) for issue in state["issues"].values()):
                    raise ValueError("Revalidate changed issue contexts while preserving saved decisions.")
                initial = {ref["path"]: ref for ref in run.get("initial_targets", [])}
                if any(not Path(raw).is_file() for raw in initial):
                    raise ValueError("A managed instruction file disappeared; removal/uninstall needs a separate workflow.")
                changed = {raw for raw, ref in initial.items() if not current(ref)}
                if inventory is not None:
                    changed.update(ref["path"] for ref in inventory if ref["path"] not in initial)
                if not changed.issubset({ref["path"] for ref in target_refs}):
                    raise ValueError("Changed instruction targets were omitted from completion.")
                audit = run.get("post_audit")
                if targets:
                    if not audit or not current(audit["snapshot"]):
                        raise ValueError("Changed targets require a saved post-consolidation audit.")
                    captured = json.loads(Path(audit["snapshot"]["path"]).read_text())["audit"]["file_snapshots"]
                    if any(not any(x["path"] == ref["path"] and x["sha256"] == ref["sha256"]
                                   for x in captured) for ref in target_refs):
                        raise ValueError("Post-audit does not cover the current changed targets.")
            run.update(status=status, summary=clean(summary), report=proof,
                       verified_targets=target_refs, finished_at=datetime.now().astimezone().isoformat())
            self.save(state)
            return run

    def weekly_release_status(self) -> dict[str, Any]:
        state = self.status()
        runs = [run for run in state["runs"].values() if run["cycle"] == state["cycle"]]
        latest = max(runs, key=lambda x: x["started_at"]) if runs else None
        if (state["weekly_due"] or not current(state["weekly"].get("snapshot", {}))
                or any(run["status"] == "running" for run in state["runs"].values())
                or not latest or latest["status"] != "completed" or not current(latest.get("report") or {})
                or any(not current(ref) for ref in latest.get("verified_targets", []))):
            raise ValueError("A verified completed cycle is required before preparing the release.")
        return {"weekly": state["weekly"], "consolidation": latest}

    def digest(self, state: dict[str, Any], notes: list[dict[str, str]], now: datetime | None = None) -> dict[str, Any] | None:
        now = now or datetime.now().astimezone()
        ordinary = state["threads"].get("ordinary", "")
        url = chat_url(ordinary) if ordinary else ""
        live = {note["id"]: note for note in notes}
        items = []
        covered = set()
        for issue in state["issues"].values():
            if issue["status"] not in OPEN and not issue.get("revalidation_required"):
                continue
            sources = issue.get("sources", [])
            covered.update(ref.get("note_id") for ref in sources)
            if any(ref.get("note_id") and ref["note_id"] not in live for ref in sources):
                continue  # A source was archived or the user made an explicit checkbox decision.
            stale = issue.get("revalidation_required") or any(not current(ref) for ref in issue.get("targets", []) + sources)
            if stale:
                item = {"id": issue["id"], "status": "investigate", "title": "Revalidation required",
                        "description": clean(issue["title"]) + ": source or target changed; the agent must reassess it.",
                        "options": [], "question": ""}
            elif issue["status"] == "needs-user":
                if not url:
                    raise ValueError("Configure a verified ordinary chat before sending decision requests.")
                item = {key: issue.get(key, "") for key in
                        ("id", "status", "title", "description", "options", "question")}
            else:
                continue  # Technical candidates are input for consolidation, not human decisions.
            items.append({**item, "chat_url": url})
        unclassified = set(live) - covered
        if unclassified:
            items.append({"id": "unclassified-notes", "status": "investigate", "title": "Assessment pending",
                          "description": f"{len(unclassified)} note(s) still need agent classification. This is not an approval request.",
                          "question": "", "options": [], "chat_url": url,
                          "note_ids": sorted(unclassified)})
        latest = {}
        for run in state["runs"].values():
            if run["runner"] not in latest or run["started_at"] > latest[run["runner"]]["started_at"]:
                latest[run["runner"]] = run
        for runner, run in latest.items():
            overdue = run["status"] == "running" and now - datetime.fromisoformat(run["started_at"]) > timedelta(hours=2)
            if run["status"] in {"failed", "partial"} or overdue:
                items.append({"id": f"runner-{runner}", "status": "error", "title": f"Consolidation {runner}: attention required",
                              "description": run.get("summary", "Run still has no verified completion; the agent must reconcile it."),
                              "question": "", "options": [], "chat_url": url})
        for item in items:
            item["signature"] = fingerprint(item)
        due = []
        for item in items:
            sent = state["sent"].get(item["id"], {})
            last = datetime.fromisoformat(sent["at"]) if sent.get("at") else None
            if sent.get("signature") != item["signature"] or last is None or now - last >= timedelta(days=7):
                due.append(item)
        if not due:
            return None
        digest_id = fingerprint([(item["id"], item["signature"]) for item in due])
        subject = f"Agent Learning: {sum(i['status'] == 'needs-user' for i in due)} decision(s), {sum(i['status'] != 'needs-user' for i in due)} issue(s)"
        language = state["threads"].get("language", "en")
        if language == "it":
            subject = f"Agent Learning: {sum(i['status'] == 'needs-user' for i in due)} decisioni, {sum(i['status'] != 'needs-user' for i in due)} segnalazioni"
        body, markup = render(due, language)
        return {"digest_id": digest_id, "subject": subject, "body": body, "html": markup, "items": due}

    def reserve(self, digest: dict[str, Any], refresh=None) -> None:
        with self.lock():
            state = self.load()
            if state["outbox"]:
                raise ValueError("An earlier notification has no delivery receipt; reconcile it before sending again.")
            if refresh is not None:
                latest = refresh(state)
                if latest is None or latest["digest_id"] != digest["digest_id"]:
                    raise ValueError("Notification inputs changed before reservation; prepare again.")
            elif all(state["sent"].get(item["id"], {}).get("signature") == item["signature"]
                     and datetime.now().astimezone() - datetime.fromisoformat(state["sent"][item["id"]]["at"]) < timedelta(days=7)
                     for item in digest["items"]):
                raise ValueError("This notification was already acknowledged.")
            state["outbox"] = {"digest_id": digest["digest_id"], "items": digest["items"],
                               "created_at": datetime.now().astimezone().isoformat()}
            self.save(state)

    def acknowledge(self, digest_id: str, receipt: str, delivered: bool = True) -> None:
        if not clean(receipt):
            raise ValueError("Provider delivery evidence or a verified non-delivery reason is required.")
        with self.lock():
            state = self.load()
            pending = state["outbox"]
            if not pending or pending["digest_id"] != digest_id:
                raise ValueError("Notification does not match the pending submission.")
            if delivered:
                for item in pending["items"]:
                    state["sent"][item["id"]] = {"signature": item["signature"],
                                                 "at": datetime.now().astimezone().isoformat(), "receipt": clean(receipt)}
            state["outbox"] = None
            self.save(state)


def render(items: list[dict[str, Any]], language: str = "en") -> tuple[str, str]:
    italian = language == "it"
    language = "it" if italian else "en"
    heading = "Decisioni e stato delle esecuzioni" if italian else "Decisions and operational status"
    intro = ("Apri la chat collegata per consultare lo stato aggiornato e rispondere."
             if italian else "Open the linked chat for the current state and to respond.")
    decision_label = "Decisione" if italian else "Decision"
    link_label = "Apri la chat per rispondere" if italian else "Open the chat to respond"
    text = ["Agent Learning — " + heading, "", intro, ""]
    blocks = []
    esc = lambda value: html.escape(clean(value), quote=True)
    for item in items:
        text += [clean(item["title"]), clean(item["description"]), ""]
        content = f'<h2 style="font-size:20px;line-height:1.4;margin:0 0 12px">{esc(item["title"])}</h2>'
        content += f'<p style="margin:0 0 16px">{esc(item["description"])}</p>'
        if item["status"] == "needs-user":
            text += [decision_label + ": " + clean(item["question"]), ""]
            content += f'<p style="margin:0 0 16px"><strong>{decision_label}:</strong> {esc(item["question"])}</p>'
            content += '<ul style="padding-left:24px;margin:0 0 20px">'
            for option in item["options"]:
                label = clean(option["id"]) + ". " + clean(option["label"])
                if option.get("recommended"):
                    label += " (consigliata)" if italian else " (recommended)"
                text += [label, "  " + clean(option["description"]), ""]
                content += f'<li style="margin:0 0 14px"><strong>{esc(label)}</strong><br>{esc(option["description"])}</li>'
            content += '</ul>'
            instruction = (f'Rispondi nella chat con "{clean(item["id"])}: ID opzione" ed eventuali vincoli.' if italian
                           else f'Reply in the chat with "{clean(item["id"])}: option ID" and any constraints.')
            text += [instruction, ""]
            content += f'<p style="margin:0 0 16px">{esc(instruction)}</p>'
        if item.get("chat_url"):
            text += [link_label + ": " + item["chat_url"], ""]
            content += f'<p style="margin:0"><a style="color:#175cd3;text-decoration:underline" href="{esc(item["chat_url"])}">{link_label}</a></p>'
        blocks.append('<section style="padding:24px 0;border-bottom:1px solid #dfe3e8;overflow-wrap:anywhere;word-break:break-word">' + content + '</section>')
    markup = ('<!doctype html><html lang="' + language + '"><head><meta charset="utf-8">'
              '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
              '<body style="margin:0;background:#ffffff;color:#202124">'
              '<main style="max-width:640px;margin:0 auto;padding:24px 20px;font-family:Arial,sans-serif;'
              'font-size:16px;line-height:1.6;overflow-wrap:anywhere;word-break:break-word">'
              '<h1 style="font-size:24px;line-height:1.3;margin:0 0 16px">Agent Learning</h1>'
              '<p style="margin:0 0 8px">' + heading + '. ' + intro + '</p>'
              + ''.join(blocks) + '</main></body></html>')
    return "\n".join(text).strip() + "\n", markup
