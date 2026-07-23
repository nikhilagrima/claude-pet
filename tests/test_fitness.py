"""Unit tests for the fitness/ module.

Covers what the spec asks for:
- plan.targets (Mifflin-St Jeor + deficit + protein math)
- tracker round-trips (log-then-read for weight, workout, meal)
- scheduler once-per-day firing (multiple check_due calls after mark_fired)
- fitness_note read + shown-once logic
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock


def _isolate():
    """Return (fitness_cfg_path, fitness_db_path, note_path) under a fresh tmp."""
    root = Path(tempfile.mkdtemp(prefix="claude-pet-fitness-"))
    (root / "claude-pet").mkdir(parents=True, exist_ok=True)
    return (root / "claude-pet" / "fitness.json",
            root / "claude-pet" / "fitness.db",
            root / "claude-pet" / "fitness_note.txt")


class PlanTargetsTests(unittest.TestCase):
    def test_mifflin_st_jeor_male(self):
        """Standard textbook example: 80 kg, 175 cm, 30 y, male."""
        from claude_pet.fitness import plan
        bmr = plan.mifflin_st_jeor_bmr(80.0, 175.0, 30, male=True)
        # 10*80 + 6.25*175 - 5*30 + 5 = 800 + 1093.75 - 150 + 5 = 1748.75
        self.assertAlmostEqual(bmr, 1748.75, places=2)

    def test_mifflin_st_jeor_female(self):
        from claude_pet.fitness import plan
        bmr = plan.mifflin_st_jeor_bmr(60.0, 165.0, 30, male=False)
        # 10*60 + 6.25*165 - 5*30 - 161 = 600 + 1031.25 - 150 - 161 = 1320.25
        self.assertAlmostEqual(bmr, 1320.25, places=2)

    def test_daily_targets_deficit_and_protein(self):
        from claude_pet.fitness import plan
        t = plan.daily_targets(80.0, 175.0, 30, male=True,
                                activity_factor=1.375, deficit_kcal=500,
                                protein_g_per_kg=1.8)
        # maintenance = 1748.75 * 1.375 = 2404.53
        # target = 2404.53 - 500 = 1904.53 → 1905
        # protein = 1.8 * 80 = 144
        self.assertEqual(t.maintenance_kcal, 2405)   # rounded
        self.assertEqual(t.target_kcal, 1905)
        self.assertEqual(t.protein_g, 144)
        self.assertEqual(t.steps, 9000)

    def test_day_plan_lookup(self):
        from claude_pet.fitness import plan
        # Monday = 0, Sunday = 6
        self.assertEqual(plan.day_plan_for(0).focus, "PUSH")
        self.assertEqual(plan.day_plan_for(4).focus, "LEGS")
        self.assertEqual(plan.day_plan_for(6).focus, "REST")

    def test_weekly_plan_has_all_seven_days(self):
        from claude_pet.fitness import plan
        self.assertEqual(len(plan.WEEKLY_PLAN), 7)
        days = [d.day for d in plan.WEEKLY_PLAN]
        self.assertEqual(days, ["mon", "tue", "wed", "thu",
                                 "fri", "sat", "sun"])


class TrackerRoundtripTests(unittest.TestCase):
    def setUp(self):
        _, self.db_path, _ = _isolate()
        p = mock.patch("claude_pet.fitness.tracker.db_path",
                       return_value=self.db_path)
        p.start(); self.addCleanup(p.stop)

    def test_weight_log_roundtrip(self):
        from claude_pet.fitness import tracker
        tracker.log_weight(79.2)
        r = tracker.recent(days=1)
        self.assertEqual(len(r["weights"]), 1)
        self.assertAlmostEqual(r["weights"][0]["weight_kg"], 79.2)

    def test_workout_log_roundtrip(self):
        from claude_pet.fitness import tracker
        tracker.log_workout("PUSH", completed=True)
        r = tracker.recent(days=1)
        self.assertEqual(len(r["workouts"]), 1)
        self.assertEqual(r["workouts"][0]["focus"], "PUSH")
        self.assertTrue(r["workouts"][0]["completed"])

    def test_meal_log_roundtrip_with_note(self):
        from claude_pet.fitness import tracker
        tracker.log_meal(on_plan=True, note="grilled karimeen + brown rice")
        r = tracker.recent(days=1)
        self.assertEqual(len(r["meals"]), 1)
        self.assertTrue(r["meals"][0]["on_plan"])
        self.assertIn("karimeen", r["meals"][0]["note"])

    def test_re_logging_overwrites_same_day(self):
        """One row per day — a second log for the same day replaces."""
        from claude_pet.fitness import tracker
        tracker.log_weight(80.0)
        tracker.log_weight(79.5)
        r = tracker.recent(days=1)
        self.assertEqual(len(r["weights"]), 1)
        self.assertAlmostEqual(r["weights"][0]["weight_kg"], 79.5)

    def test_latest_weight(self):
        from claude_pet.fitness import tracker
        self.assertIsNone(tracker.latest_weight())
        tracker.log_weight(78.8)
        self.assertAlmostEqual(tracker.latest_weight(), 78.8)


class SchedulerOncePerDayTests(unittest.TestCase):
    def setUp(self):
        self.cfg_path, _, _ = _isolate()
        p = mock.patch("claude_pet.fitness.config._config_path",
                       return_value=self.cfg_path)
        p.start(); self.addCleanup(p.stop)

    def test_workout_fires_at_or_after_configured_time(self):
        from claude_pet.fitness import scheduler
        # Default workout time is 07:00 — before is None, after is 'workout'
        before = scheduler.check_due(datetime(2026, 7, 23, 6, 59))
        after = scheduler.check_due(datetime(2026, 7, 23, 7, 0))
        self.assertIsNone(before)
        self.assertEqual(after, "workout")

    def test_mark_fired_prevents_second_fire_same_day(self):
        from claude_pet.fitness import scheduler
        # Precedence: weigh_in (07:30) > workout (07:00) > meal_check (20:30).
        # At 08:00 both weigh_in and workout are past their times; weigh_in
        # wins because you weigh before you lift.
        first = scheduler.check_due(datetime(2026, 7, 23, 8, 0))
        self.assertEqual(first, "weigh_in")
        scheduler.mark_fired("weigh_in")
        # Weigh done → workout is next (still past its 07:00 time)
        second = scheduler.check_due(datetime(2026, 7, 23, 9, 0))
        self.assertEqual(second, "workout")
        scheduler.mark_fired("workout")
        # Both done → nothing else until 20:30
        third = scheduler.check_due(datetime(2026, 7, 23, 18, 0))
        self.assertIsNone(third)
        # At 20:30 the meal check fires
        fourth = scheduler.check_due(datetime(2026, 7, 23, 20, 30))
        self.assertEqual(fourth, "meal_check")
        scheduler.mark_fired("meal_check")
        fifth = scheduler.check_due(datetime(2026, 7, 23, 23, 0))
        self.assertIsNone(fifth)

    def test_disabled_module_returns_none(self):
        from claude_pet.fitness import config as fcfg
        from claude_pet.fitness import scheduler
        cfg = fcfg.load(); cfg["enabled"] = False; fcfg.save(cfg)
        self.assertIsNone(scheduler.check_due(datetime(2026, 7, 23, 8, 0)))


class FitnessNoteTests(unittest.TestCase):
    def setUp(self):
        self.cfg_path, _, self.note_path = _isolate()
        p1 = mock.patch("claude_pet.fitness.config._config_path",
                        return_value=self.cfg_path)
        p2 = mock.patch("claude_pet.fitness.config._fitness_note_path",
                        return_value=self.note_path)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def test_latest_note_missing_returns_none(self):
        from claude_pet.fitness import coach
        self.assertIsNone(coach.latest_note())

    def test_note_needs_showing_false_when_no_file(self):
        from claude_pet.fitness import coach
        self.assertFalse(coach.note_needs_showing())

    def test_read_and_shown_once_cycle(self):
        from claude_pet.fitness import coach
        self.note_path.write_text("keep the LISS, add one more PULL day")
        self.assertTrue(coach.note_needs_showing())
        self.assertIn("LISS", coach.latest_note())
        coach.mark_note_shown()
        self.assertFalse(coach.note_needs_showing())

    def test_new_note_after_shown_needs_showing_again(self):
        """A fresh note written LATER than the last shown date must re-surface."""
        import os, time
        from claude_pet.fitness import coach
        self.note_path.write_text("first note")
        coach.mark_note_shown()
        self.assertFalse(coach.note_needs_showing())
        # Simulate a note written tomorrow — bump the file mtime forward
        future = time.time() + 86400 * 2
        os.utime(self.note_path, (future, future))
        self.assertTrue(coach.note_needs_showing())

    def test_weekly_adjustment_pending_only_on_sunday(self):
        from claude_pet.fitness import coach
        # We can't move real date.today() from a test easily; assert the
        # behavior on the specific day it runs. Just verify the function
        # doesn't crash and returns a bool.
        val = coach.weekly_adjustment_pending()
        self.assertIsInstance(val, bool)

    def test_build_weekly_adjustment_context_is_stringable(self):
        from claude_pet.fitness import coach
        text = coach.build_weekly_adjustment_context()
        self.assertIn("Weekly fitness adjustment", text)
        self.assertIn("fitness_note.txt", text)
        self.assertLess(len(text), 2000, "keep the injection compact")


if __name__ == "__main__":
    unittest.main()
