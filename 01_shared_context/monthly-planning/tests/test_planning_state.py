import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/planning_state.py"
spec = importlib.util.spec_from_file_location("planning_state", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PlanningStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        module.initialize(self.path, "fictional", {"library_ref": "audience.json", "context_ref": "context.md"})

    def event(self, **kw):
        value = {"id": "e1", "store_id": "fictional", "kind": "planning_note",
                 "occurred_at": "2031-04-27T10:00:00+08:00", "actor": "test",
                 "summary": "虚构规划建议", "references": ["fictional.md"]}
        value.update(kw)
        return value

    def card(self, **kw):
        value = self.event(kind="card_revision", card_id="P1", claim_id="meal",
                           before="原价需求已有证明", after="免费体验不能证明原价需求",
                           reason="来源修订", claim_status="limited", basis="source_correction",
                           impact="调整相应预算建议", unknowns="原价需求规模")
        value.update(kw)
        return value

    def test_init_cannot_overwrite(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            module.initialize(self.path, "another", {"library_ref": "b", "context_ref": "c"})
        self.assertEqual(before, self.path.read_bytes())

    def test_retry_is_idempotent_and_history_is_preserved(self):
        event = self.card()
        module.record(self.path, 0, event)
        before = self.path.read_bytes()
        self.assertTrue(module.record(self.path, 0, event)["duplicate"])
        self.assertEqual(before, self.path.read_bytes())
        state = module.read_json(self.path)
        self.assertEqual(state["events"][0]["event"]["before"], "原价需求已有证明")
        self.assertEqual(state["claims"]["P1"]["meal"]["text"], event["after"])

    def test_conflict_does_not_lose_others_work(self):
        module.record(self.path, 0, self.event())
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            module.record(self.path, 0, self.event(id="e2"))
        self.assertEqual(before, self.path.read_bytes())

    def test_same_id_different_body_rejected(self):
        module.record(self.path, 0, self.event())
        with self.assertRaises(ValueError):
            module.record(self.path, 1, self.event(summary="changed"))

    def test_different_store_rejected(self):
        with self.assertRaises(ValueError):
            module.record(self.path, 0, self.event(store_id="different"))
        self.assertEqual(module.read_json(self.path)["revision"], 0)

    def test_failed_and_partial_refresh_keep_success_cursor(self):
        event = self.event(kind="source_refresh", source_id="pos", status="success",
                           attempted_at="2031-04-27T10:00:00+08:00", coverage="through 2031-04-24",
                           source_version="snapshot1", checks="count and totals checked")
        module.record(self.path, 0, event)
        for i, status in enumerate(["failed", "partial"], start=1):
            fail = self.event(id=f"e{i+1}", kind="source_refresh", source_id="pos", status=status,
                              attempted_at=f"2031-04-2{7+i}T10:00:00+08:00", limitation="login or pagination")
            module.record(self.path, i, fail)
        source = module.read_json(self.path)["source_checks"]["pos"]
        self.assertEqual(source["coverage"], "through 2031-04-24")
        self.assertEqual(source["last_success"], event["attempted_at"])
        self.assertEqual(source["last_status"], "partial")

    def test_failure_cannot_claim_complete_coverage(self):
        with self.assertRaises(ValueError):
            module.record(self.path, 0, self.event(kind="source_refresh", source_id="pos", status="failed",
                          attempted_at="2031-04-27T10:00:00+08:00", limitation="login", coverage="today"))

    def test_claim_revision_requires_current_before(self):
        event = self.card()
        module.record(self.path, 0, event)
        with self.assertRaises(ValueError):
            module.record(self.path, 1, self.card(id="e2"))
        module.record(self.path, 1, self.card(id="e2", before=event["after"], after="仍需补原价消费来源"))
        self.assertEqual(len(module.read_json(self.path)["events"]), 2)

    def test_no_promotion_for_unexecuted_or_immature_result(self):
        checks = {"execution": "not_started", "exposure": "unknown", "fulfillment": "unknown",
                  "window": "immature", "confounders": "unknown", "conclusion_boundary": "not tested"}
        event = self.card(basis="action_result", claim_status="supported_in_scope", result_checks=checks)
        with self.assertRaises(ValueError):
            module.record(self.path, 0, event)
        event["claim_status"] = "unresolved"
        module.record(self.path, 0, event)

    def test_existing_lock_does_not_mutate_state(self):
        lock = self.path.with_name(self.path.name + ".lock")
        lock.mkdir()
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            module.record(self.path, 0, self.event())
        self.assertEqual(before, self.path.read_bytes())
        lock.rmdir()

    def test_cli_record_read_and_restart(self):
        event_path = self.path.parent / "event.json"
        event_path.write_text(json.dumps(self.event()))
        subprocess.run([sys.executable, str(SCRIPT), "record", "--state", str(self.path),
                        "--expected-revision", "0", "--event", str(event_path)], check=True, capture_output=True)
        out = subprocess.run([sys.executable, str(SCRIPT), "read", "--state", str(self.path)], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(out.stdout)["revision"], 1)


if __name__ == "__main__":
    unittest.main()
