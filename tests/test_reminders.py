"""Reminders — store roundtrips, 3-stage scheduler, snooze, delete, tab.

The scheduler tests use explicit `now` datetimes so they never depend on
wall clock.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock


def _isolate_db() -> Path:
    return Path(tempfile.mkdtemp(prefix="claude-pet-rem-")) / "reminders.db"


class StoreRoundtripTests(unittest.TestCase):
    def setUp(self):
        self.db = _isolate_db()
        p = mock.patch("claude_pet.reminders.store.db_path",
                       return_value=self.db)
        p.start(); self.addCleanup(p.stop)

    def test_add_and_list(self):
        from claude_pet.reminders import store
        due = datetime.now() + timedelta(hours=2)
        rid = store.add("Pay rent", due, note="autopay didn't fire")
        rows = store.list_active()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], rid)
        self.assertEqual(rows[0]["title"], "Pay rent")
        self.assertEqual(rows[0]["fired_stages"], [])
        self.assertIsNone(rows[0]["completed_at"])

    def test_add_empty_title_raises(self):
        from claude_pet.reminders import store
        with self.assertRaises(ValueError):
            store.add("", datetime.now() + timedelta(hours=1))

    def test_mark_stage_fired_idempotent(self):
        from claude_pet.reminders import store
        rid = store.add("x", datetime.now() + timedelta(hours=2))
        store.mark_stage_fired(rid, "day_before")
        store.mark_stage_fired(rid, "day_before")  # duplicate — no-op
        r = store.get(rid)
        self.assertEqual(r["fired_stages"], ["day_before"])

    def test_all_three_stages_auto_completes(self):
        from claude_pet.reminders import store
        rid = store.add("x", datetime.now() + timedelta(hours=2))
        store.mark_stage_fired(rid, "day_before")
        store.mark_stage_fired(rid, "five_min")
        store.mark_stage_fired(rid, "on_time")
        r = store.get(rid)
        self.assertIsNotNone(r["completed_at"],
                             "third stage should auto-complete the reminder")
        # It's no longer in the active list
        self.assertEqual(store.list_active(), [])

    def test_mark_completed(self):
        from claude_pet.reminders import store
        rid = store.add("x", datetime.now() + timedelta(hours=2))
        ok = store.mark_completed(rid)
        self.assertTrue(ok)
        self.assertEqual(store.list_active(), [])
        # Second call returns False (already completed)
        self.assertFalse(store.mark_completed(rid))

    def test_snooze_pushes_due_and_resets_stages(self):
        from claude_pet.reminders import store
        rid = store.add("x", datetime.now() - timedelta(minutes=5))
        store.mark_stage_fired(rid, "on_time")
        r_before = store.get(rid)
        self.assertEqual(r_before["fired_stages"], ["on_time"])
        ok = store.snooze(rid, minutes=30)
        self.assertTrue(ok)
        r_after = store.get(rid)
        self.assertEqual(r_after["fired_stages"], [])
        due = datetime.fromisoformat(r_after["due_at"])
        self.assertGreater(due, datetime.now())

    def test_delete(self):
        from claude_pet.reminders import store
        rid = store.add("x", datetime.now() + timedelta(hours=1))
        self.assertTrue(store.delete(rid))
        self.assertFalse(store.delete(rid))    # already gone


class SchedulerThreeStageTests(unittest.TestCase):
    def setUp(self):
        self.db = _isolate_db()
        p = mock.patch("claude_pet.reminders.store.db_path",
                       return_value=self.db)
        p.start(); self.addCleanup(p.stop)

    def _add_at(self, due: datetime) -> int:
        from claude_pet.reminders import store
        return store.add("test", due)

    def test_no_fire_before_day_before_window(self):
        from claude_pet.reminders import scheduler
        due = datetime(2026, 8, 1, 12, 0)
        self._add_at(due)
        # 26 hours before → outside the day_before window
        result = scheduler.check_due(now=due - timedelta(hours=26))
        self.assertEqual(result, [])

    def test_day_before_fires_in_window(self):
        from claude_pet.reminders import scheduler
        due = datetime(2026, 8, 1, 12, 0)
        self._add_at(due)
        # 20 hours before — inside [due-24h, due-12h]
        result = scheduler.check_due(now=due - timedelta(hours=20))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stage, "day_before")

    def test_five_min_fires_close_to_due(self):
        from claude_pet.reminders import scheduler
        due = datetime(2026, 8, 1, 12, 0)
        self._add_at(due)
        result = scheduler.check_due(now=due - timedelta(minutes=3))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stage, "five_min")

    def test_on_time_fires_at_due(self):
        from claude_pet.reminders import scheduler
        due = datetime(2026, 8, 1, 12, 0)
        self._add_at(due)
        result = scheduler.check_due(now=due)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stage, "on_time")

    def test_on_time_fires_even_when_late(self):
        """You want to know you missed it — on_time always fires eventually."""
        from claude_pet.reminders import scheduler
        due = datetime(2026, 8, 1, 12, 0)
        self._add_at(due)
        result = scheduler.check_due(now=due + timedelta(hours=3))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].stage, "on_time")

    def test_scheduler_respects_fired_stages(self):
        from claude_pet.reminders import store, scheduler
        due = datetime(2026, 8, 1, 12, 0)
        rid = self._add_at(due)
        store.mark_stage_fired(rid, "day_before")
        # Still in day_before window but stage already fired → no repeat
        result = scheduler.check_due(now=due - timedelta(hours=20))
        self.assertEqual(result, [])

    def test_completed_reminder_never_fires(self):
        from claude_pet.reminders import store, scheduler
        due = datetime(2026, 8, 1, 12, 0)
        rid = self._add_at(due)
        store.mark_completed(rid)
        result = scheduler.check_due(now=due)
        self.assertEqual(result, [])


class RemindTabTests(unittest.TestCase):
    """RemindersTab: add via UI, list, delete."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.db = _isolate_db()
        p = mock.patch("claude_pet.reminders.store.db_path",
                       return_value=self.db)
        p.start(); self.addCleanup(p.stop)

    def test_tab_constructs_empty(self):
        from claude_pet.panel import RemindersTab
        tab = RemindersTab()
        tab.refresh()
        self.assertEqual(tab.table.rowCount(), 0)

    def test_add_via_inputs(self):
        from claude_pet.panel import RemindersTab
        from claude_pet.reminders import store
        tab = RemindersTab()
        tab.title_input.setText("Call dentist")
        tab.when_input.setText("tomorrow 10:00")
        tab._add()
        self.assertEqual(len(store.list_active()), 1)
        self.assertEqual(store.list_active()[0]["title"], "Call dentist")
        # Inputs cleared
        self.assertEqual(tab.title_input.text(), "")

    def test_done_button_marks_completed(self):
        from claude_pet.panel import RemindersTab
        from claude_pet.reminders import store
        tab = RemindersTab()
        rid = store.add("do thing", datetime.now() + timedelta(hours=1))
        tab.refresh()
        tab._done(rid)
        self.assertEqual(store.list_active(), [])

    def test_all_buttons_no_auto_default(self):
        """Same Enter-safety as fitness bubbles."""
        from claude_pet.panel import RemindersTab
        tab = RemindersTab()
        self.assertFalse(tab.add_btn.autoDefault())


if __name__ == "__main__":
    unittest.main()
