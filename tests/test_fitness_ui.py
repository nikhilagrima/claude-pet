"""FitnessTab + fitness bubbles — construction, close, add, no-Enter-close.

Runs under QT_QPA_PLATFORM=offscreen. Never spins a real event loop.
Every button must:
- exist
- have setDefault(False) so Enter mid-typing in the spinbox doesn't
  submit-and-close prematurely
- actually dismiss the dialog when clicked
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _isolate():
    root = Path(tempfile.mkdtemp(prefix="claude-pet-fit-ui-"))
    (root / "claude-pet").mkdir(parents=True, exist_ok=True)
    return (root / "claude-pet" / "fitness.json",
            root / "claude-pet" / "fitness.db",
            root / "claude-pet" / "fitness_note.txt")


class OverlayButtonDefaultsTests(unittest.TestCase):
    """Regression: pressing Enter in a spinbox must NOT close the dialog.

    Root cause of the "cannot change weight past 80" bug: the primary button
    was Qt's default → Enter mid-typing triggered submit → dialog closed
    before the user finished typing the value.
    """

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cfg, self.db, _ = _isolate()
        p1 = mock.patch("claude_pet.fitness.config._config_path",
                        return_value=self.cfg)
        p2 = mock.patch("claude_pet.fitness.tracker.db_path",
                        return_value=self.db)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def _all_buttons_are_not_default(self, dialog):
        from PySide6.QtWidgets import QPushButton
        for btn in dialog.findChildren(QPushButton):
            self.assertFalse(
                btn.autoDefault(),
                f"button {btn.text()!r} has autoDefault=True — "
                f"Enter mid-typing will close the dialog"
            )
            self.assertFalse(
                btn.isDefault(),
                f"button {btn.text()!r} isDefault=True — same problem"
            )

    def test_weighin_bubble_buttons_not_default(self):
        from claude_pet.fitness.overlay import WeighInBubble
        d = WeighInBubble(current_kg=80.0,
                          on_submit=lambda kg: None,
                          on_dismiss=lambda: None)
        self._all_buttons_are_not_default(d)
        d.deleteLater()

    def test_meal_bubble_buttons_not_default(self):
        from claude_pet.fitness.overlay import MealCheckBubble
        d = MealCheckBubble(on_submit=lambda ok, note: None,
                            on_dismiss=lambda: None)
        self._all_buttons_are_not_default(d)
        d.deleteLater()

    def test_workout_bubble_buttons_not_default(self):
        from claude_pet.fitness.overlay import WorkoutBubble
        d = WorkoutBubble("today's plan", on_close=lambda: None)
        self._all_buttons_are_not_default(d)
        d.deleteLater()

    def test_coach_note_bubble_buttons_not_default(self):
        from claude_pet.fitness.overlay import CoachNoteBubble
        d = CoachNoteBubble("keep the LISS", on_close=lambda: None)
        self._all_buttons_are_not_default(d)
        d.deleteLater()


class OverlayCallbacksTests(unittest.TestCase):
    """Every bubble must actually run its callback and dismiss on click."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cfg, self.db, _ = _isolate()
        p1 = mock.patch("claude_pet.fitness.config._config_path",
                        return_value=self.cfg)
        p2 = mock.patch("claude_pet.fitness.tracker.db_path",
                        return_value=self.db)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_weighin_log_button_calls_on_submit_with_current_value(self):
        from claude_pet.fitness.overlay import WeighInBubble
        captured = {}
        d = WeighInBubble(
            current_kg=79.5,
            on_submit=lambda kg: captured.setdefault("kg", kg),
            on_dismiss=lambda: captured.setdefault("dismissed", True),
        )
        # Simulate the user editing the spinbox
        d.spin.setValue(82.3)
        d._log()
        self.assertAlmostEqual(captured.get("kg", 0), 82.3, places=1)
        self.assertNotIn("dismissed", captured, "log() must not fire on_dismiss")

    def test_weighin_skip_button_calls_on_dismiss_not_submit(self):
        from claude_pet.fitness.overlay import WeighInBubble
        captured = {}
        d = WeighInBubble(
            current_kg=80.0,
            on_submit=lambda kg: captured.setdefault("kg", kg),
            on_dismiss=lambda: captured.setdefault("dismissed", True),
        )
        d._skip()
        self.assertTrue(captured.get("dismissed"))
        self.assertNotIn("kg", captured)

    def test_meal_on_plan_records_true(self):
        from claude_pet.fitness.overlay import MealCheckBubble
        captured = {}
        d = MealCheckBubble(
            on_submit=lambda ok, note: captured.setdefault("payload", (ok, note)),
            on_dismiss=lambda: captured.setdefault("dismissed", True),
        )
        d.note.setText("grilled fish + brown rice")
        d._submit(True)
        self.assertEqual(captured.get("payload"),
                          (True, "grilled fish + brown rice"))


class FitnessTabTests(unittest.TestCase):
    """Dashboard tab: construction, refresh, log-now buttons, goal save."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cfg, self.db, _ = _isolate()
        p1 = mock.patch("claude_pet.fitness.config._config_path",
                        return_value=self.cfg)
        p2 = mock.patch("claude_pet.fitness.tracker.db_path",
                        return_value=self.db)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_tab_constructs_without_data(self):
        """Zero rows in tracker DB — must not crash on refresh."""
        from claude_pet.panel import FitnessTab
        tab = FitnessTab()
        tab.refresh()
        self.assertEqual(tab.weight_table.rowCount(), 0)
        self.assertEqual(tab.workout_table.rowCount(), 0)
        self.assertEqual(tab.meal_table.rowCount(), 0)

    def test_log_weight_now_button_writes_to_tracker(self):
        from claude_pet.panel import FitnessTab
        from claude_pet.fitness import tracker
        tab = FitnessTab()
        tab.current_kg.setValue(78.4)
        tab._log_weight_now()
        self.assertAlmostEqual(tracker.latest_weight(), 78.4, places=1)
        # Refresh populated the weight table with the new row
        self.assertGreaterEqual(tab.weight_table.rowCount(), 1)

    def test_log_workout_now_button_writes(self):
        from claude_pet.panel import FitnessTab
        from claude_pet.fitness import tracker
        tab = FitnessTab()
        tab._log_workout_now()
        r = tracker.recent(days=1)
        self.assertEqual(len(r["workouts"]), 1)
        self.assertTrue(r["workouts"][0]["completed"])

    def test_log_meal_on_and_off(self):
        from claude_pet.panel import FitnessTab
        from claude_pet.fitness import tracker
        tab = FitnessTab()
        tab._log_meal_now(True)
        r = tracker.recent(days=1)
        self.assertTrue(r["meals"][0]["on_plan"])
        # Second log same day overwrites
        tab._log_meal_now(False)
        r = tracker.recent(days=1)
        self.assertFalse(r["meals"][0]["on_plan"])

    def test_goal_save_persists_to_config(self):
        from claude_pet.panel import FitnessTab
        from claude_pet.fitness import config as fcfg
        tab = FitnessTab()
        tab.refresh()   # ensure _ready is True
        tab.current_kg.setValue(82.0)
        tab.target_kg.setValue(75.0)
        tab._save_profile()
        prof = fcfg.profile()
        self.assertAlmostEqual(float(prof["weight_kg"]), 82.0, places=1)
        self.assertAlmostEqual(float(prof["target_weight_kg"]), 75.0, places=1)

    def test_all_action_buttons_not_default(self):
        from claude_pet.panel import FitnessTab
        tab = FitnessTab()
        for btn in (tab.log_weight_btn, tab.log_workout_btn,
                    tab.meal_on_btn, tab.meal_off_btn):
            self.assertFalse(btn.autoDefault(),
                             f"{btn.text()!r} autoDefault is True")


class BodyMapWidgetTests(unittest.TestCase):
    """Clickable body-map widget — construction, state colors, click flow."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cfg, self.db, _ = _isolate()
        p1 = mock.patch("claude_pet.fitness.tracker.db_path",
                        return_value=self.db)
        p2 = mock.patch("claude_pet.fitness.config._config_path",
                        return_value=self.cfg)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_all_expected_body_parts_are_clickable(self):
        from claude_pet.fitness.body_map import BodyMapWidget
        from claude_pet.fitness.plan import ALL_BODY_PARTS
        w = BodyMapWidget()
        for part in ALL_BODY_PARTS:
            self.assertIn(part, w._items,
                          f"body-map is missing clickable region for {part!r}")

    def test_widget_reflects_direct_log_after_refresh(self):
        from claude_pet.fitness.body_map import BodyMapWidget
        from claude_pet.fitness import tracker
        from datetime import date, timedelta
        # Seed last week: cover chest so it wouldn't be carry-forward red
        last_monday = date.today() - timedelta(days=date.today().weekday() + 7)
        tracker.log_body_part("chest", day=last_monday.isoformat())
        w = BodyMapWidget()
        # Before: chest is grey (nothing logged this week; last week covered)
        self.assertEqual(w._items["chest"]._state, "grey")
        tracker.log_body_part("chest")
        w.refresh()
        self.assertEqual(w._items["chest"]._state, "green")

    def test_carry_forward_marks_last_week_missing_as_red(self):
        """Simulate: last week had a workout for PUSH but nothing else.
        This week has no logs. Every non-push body part should be red."""
        from claude_pet.fitness.body_map import BodyMapWidget
        from claude_pet.fitness import tracker
        from datetime import date, timedelta
        last_monday = date.today() - timedelta(days=date.today().weekday() + 7)
        tracker.log_workout("PUSH", completed=True, day=last_monday.isoformat())
        w = BodyMapWidget()
        # Chest was covered last week (via PUSH) — should NOT be red this week
        self.assertEqual(w._items["chest"]._state, "grey")
        # Back was NOT covered last week and not this week → red
        self.assertEqual(w._items["back"]._state, "red")


class BubbleStackingGuardTests(unittest.TestCase):
    """Regression: 'Got it' seemed to not close the coach-note bubble.

    Root cause: _drain_fitness_scheduler ran every tick and note_needs_showing
    stayed True until the click, so an identical bubble was created each tick
    and stacked at the same position. Clicking closed only the top one.
    The fix is a hard guard: no new fitness bubble while one is visible.
    """

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.cfg, self.db, self.note = _isolate()
        p1 = mock.patch("claude_pet.fitness.config._config_path",
                        return_value=self.cfg)
        p2 = mock.patch("claude_pet.fitness.tracker.db_path",
                        return_value=self.db)
        p3 = mock.patch("claude_pet.fitness.config._fitness_note_path",
                        return_value=self.note)
        p1.start(); p2.start(); p3.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)
        self.addCleanup(p3.stop)

    def _drain(self, holder):
        """Invoke the real drain method on a bare holder object."""
        from claude_pet.app import PetWindow
        PetWindow._drain_fitness_scheduler(holder)

    def test_note_bubble_not_stacked_across_ticks(self):
        """Core regression: while a note bubble is visible, subsequent
        ticks must NOT create identical stacked copies. The 'Got it'
        button appears to do nothing when copies are stacked (each click
        only closes the top one). Guard = single-bubble invariant."""
        import types
        self.note.write_text("keep the LISS, add one PULL day")
        holder = types.SimpleNamespace()
        self._drain(holder)     # tick 1 → creates the bubble
        self.assertEqual(len(holder._active_fitness_bubbles), 1)
        first = holder._active_fitness_bubbles[0]
        self.assertTrue(first.isVisible())
        self._drain(holder)     # tick 2 → guard must block a second bubble
        self._drain(holder)     # tick 3 → still blocked
        self._drain(holder)     # tick 4 → still blocked
        self.assertEqual(
            len(holder._active_fitness_bubbles), 1,
            "note bubble was re-created on subsequent ticks — 'Got it' "
            "click would only close the topmost stacked copy, making it "
            "look like the button is broken",
        )
        self.assertIs(holder._active_fitness_bubbles[0], first)
        # Click 'Got it' → the visible bubble is hidden + scheduled for
        # deletion. The visibility filter in the guard drops it.
        first._finish()
        self.assertFalse(first.isVisible())


class SoundCooldownTests(unittest.TestCase):
    """Same key can't fire twice within COOLDOWN_S."""

    def setUp(self):
        self.cfg = Path(tempfile.mkdtemp()) / "config.json"
        p = mock.patch("claude_pet.pet_config._config_path",
                       return_value=self.cfg)
        p.start(); self.addCleanup(p.stop)

    def test_cooldown_suppresses_rapid_repeats(self):
        from claude_pet import pet_config
        from claude_pet.app import SoundPlayer
        pet_config.set_muted(False)
        sp = SoundPlayer()
        if not sp.sounds.get("success"):
            self.skipTest("no bundled/system 'success' sound on this host")
        with mock.patch("claude_pet.app._play_audio",
                        return_value=None) as spawned:
            sp.play("success")     # fires
            sp.play("success")     # cooldown — suppressed
            sp.play("success")     # cooldown — suppressed
        self.assertEqual(spawned.call_count, 1,
                         "second rapid play() should be suppressed by cooldown")


if __name__ == "__main__":
    unittest.main()
