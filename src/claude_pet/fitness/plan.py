"""Weekly plan + food guide + supplements + BMR/target math.

Data-only, no side effects. All functions are pure and cheap enough to call
inside the tick loop. Keeps the coach.py and cli.py surfaces small.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


DayName = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass(frozen=True)
class DayPlan:
    day: DayName
    focus: str                    # "PUSH", "PULL", "CARDIO", etc.
    body_parts: tuple[str, ...]
    lifts: tuple[str, ...]        # empty for cardio/rest days
    cardio: str                   # human-readable cardio block
    # display name — used in bubbles
    label: str


# --- WEEKLY PLAN (verbatim from spec) ---------------------------------------
WEEKLY_PLAN: tuple[DayPlan, ...] = (
    DayPlan(
        day="mon", label="Monday — PUSH",
        focus="PUSH",
        body_parts=("chest", "shoulders", "triceps"),
        lifts=(
            "push-ups / bench press",
            "overhead press",
            "incline dumbbell press",
            "triceps dips",
            "— 3×8-12",
        ),
        cardio="15 min LISS (low-intensity steady state) after lifts",
    ),
    DayPlan(
        day="tue", label="Tuesday — CARDIO + CORE",
        focus="CARDIO+CORE",
        body_parts=("core",),
        lifts=(
            "plank 3×45s",
            "dead bug 3×12",
            "Russian twists 3×20",
        ),
        cardio="30 min Zone-2 (walk / cycle / swim)",
    ),
    DayPlan(
        day="wed", label="Wednesday — PULL",
        focus="PULL",
        body_parts=("back", "biceps", "rear delts"),
        lifts=(
            "rows",
            "lat pulldown or pull-ups",
            "face pulls",
            "biceps curls",
            "— 3×8-12",
        ),
        cardio="15 min LISS after lifts",
    ),
    DayPlan(
        day="thu", label="Thursday — HIIT",
        focus="HIIT",
        body_parts=("full-body",),
        lifts=(),
        cardio="30s hard / 90s easy × 10 rounds; 10 min mobility after",
    ),
    DayPlan(
        day="fri", label="Friday — LEGS",
        focus="LEGS",
        body_parts=("quads", "hamstrings", "glutes", "calves"),
        lifts=(
            "squats",
            "Romanian deadlift",
            "lunges",
            "calf raises",
            "— 3×8-12",
        ),
        cardio="10 min walk cooldown",
    ),
    DayPlan(
        day="sat", label="Saturday — ACTIVE RECOVERY",
        focus="RECOVERY",
        body_parts=("full-body",),
        lifts=(),
        cardio="45-60 min long walk; 20 min light yoga / stretching",
    ),
    DayPlan(
        day="sun", label="Sunday — REST",
        focus="REST",
        body_parts=(),
        lifts=(),
        cardio="Full rest. Weekly reflection.",
    ),
)


# Convenience lookup: python weekday index (Mon=0..Sun=6) → DayPlan.
_INDEX = {i: WEEKLY_PLAN[i] for i in range(7)}


def day_plan_for(weekday_index: int) -> DayPlan:
    """weekday_index: Mon=0, Sun=6 (Python's datetime.weekday())."""
    return _INDEX[weekday_index % 7]


DAILY_STEP_TARGET = 9000


# --- BODY-PART COVERAGE -----------------------------------------------------
# Maps a workout `focus` string (stored in workout_log) to the body parts
# that focus trains. Used to answer "which body parts have I hit this week?"
# and "what's missing?" so Claude Code can propose a make-up plan.
BODY_PART_MAP: dict[str, tuple[str, ...]] = {
    "PUSH":         ("chest", "shoulders", "triceps"),
    "PULL":         ("back", "biceps", "rear delts"),
    "LEGS":         ("quads", "hamstrings", "glutes", "calves"),
    "CARDIO+CORE":  ("core", "cardio"),
    "HIIT":         ("cardio", "full-body"),
    "RECOVERY":     ("recovery",),
    "REST":         (),
}


# The full set of body parts we care about tracking through a week. Ordered
# by upper→lower→conditioning so gap displays read naturally.
ALL_BODY_PARTS: tuple[str, ...] = (
    "chest", "shoulders", "triceps",
    "back", "biceps", "rear delts",
    "quads", "hamstrings", "glutes", "calves",
    "core", "cardio",
)


def body_parts_for_focus(focus: str) -> tuple[str, ...]:
    """Return body parts trained by a given workout focus (case-insensitive)."""
    return BODY_PART_MAP.get(focus.upper(), ())


def coverage_from_workouts(workouts: list[dict]) -> dict:
    """Given a list of completed workout dicts (with 'focus' + 'completed'),
    return {'trained': set[str], 'missing': set[str], 'per_focus': dict}.

    Only counts workouts where `completed` is True — a skipped workout
    doesn't cover its body parts.
    """
    trained: set[str] = set()
    per_focus: dict[str, bool] = {f: False for f in BODY_PART_MAP}
    for w in workouts:
        if not w.get("completed"):
            continue
        focus = (w.get("focus") or "").upper()
        per_focus[focus] = True
        for bp in body_parts_for_focus(focus):
            trained.add(bp)
    missing = set(ALL_BODY_PARTS) - trained
    # Recovery, rest, cardio-only don't count as "must-cover" — allow the
    # weekly checker to focus on strength groups.
    return {
        "trained": trained,
        "missing": missing,
        "per_focus": per_focus,
    }


# Focus categories the weekly plan wants hit every week — used to answer
# "did you skip any major muscle group this week?"
MUST_HIT_FOCUSES: tuple[str, ...] = ("PUSH", "PULL", "LEGS")


# --- FOOD GUIDE (Kerala/Indian-friendly) ------------------------------------
FOOD_PRINCIPLES: tuple[str, ...] = (
    "~500 kcal daily deficit",
    "protein 1.6-2.0 g per kg bodyweight",
    "half plate vegetables",
    "2.5-3 L water per day",
    "no sugary drinks",
)

EAT_MORE: tuple[str, ...] = (
    "eggs",
    "fish — sardine, mackerel, karimeen — grilled or curry with less oil",
    "chicken breast",
    "dal, chana",
    "curd, buttermilk",
    "brown rice or 2 chapatis",
    "oats",
)

EAT_LESS: tuple[str, ...] = (
    "fried snacks (pazham pori, samosa)",
    "bakery items",
    "sugary chai",
    "parotta",
    "large biryani portions",
    "late-night eating",
    "alcohol",
)

# One sample day, editable by user later.
SAMPLE_DAY: tuple[tuple[str, str], ...] = (
    ("breakfast", "omelette + dosa, or oats"),
    ("lunch",     "rice + fish curry + thoran + curd"),
    ("snack",     "buttermilk or a small handful of nuts"),
    ("dinner",    "2 chapatis + chicken or dal"),
)


# --- SUPPLEMENTS (food first, all optional, confirm with doctor) ------------
@dataclass(frozen=True)
class Supplement:
    name: str
    dose: str
    when: str
    note: str


SUPPLEMENTS_NOTICE = (
    "OPTIONAL — food first, always. Confirm doses / interactions with your "
    "doctor. None of this is required for the plan to work."
)

SUPPLEMENTS: tuple[Supplement, ...] = (
    Supplement(
        name="whey protein",
        dose="~25 g per serving",
        when="only if daily protein target isn't met from food",
        note="convenience, not necessity",
    ),
    Supplement(
        name="creatine monohydrate",
        dose="3-5 g",
        when="daily, any time",
        note="strongest evidence of any legal supplement for strength",
    ),
    Supplement(
        name="omega-3 (EPA + DHA)",
        dose="1-2 g",
        when="with a meal",
        note="skip if you already eat fatty fish 3+ times/week",
    ),
    Supplement(
        name="vitamin D3",
        dose="1000-2000 IU",
        when="daily",
        note="get a blood level first — over-supplementing is a real risk",
    ),
    Supplement(
        name="caffeine (optional pre-workout)",
        dose="see coffee/tea",
        when="pre-workout only; NONE after 2 pm",
        note="sleep debt costs more than any workout gain",
    ),
)


# --- BMR + TARGETS (Mifflin-St Jeor) ---------------------------------------

def mifflin_st_jeor_bmr(weight_kg: float, height_cm: float,
                        age: int, male: bool) -> float:
    """Basal metabolic rate in kcal/day.

    Formula from Mifflin & St Jeor 1990. Most-recommended clinical BMR
    formula per the American Dietetic Association's 2005 review.
    """
    base = (10.0 * weight_kg) + (6.25 * height_cm) - (5.0 * age)
    return base + (5.0 if male else -161.0)


# Activity multiplier — assume light activity (desk job + 3-4 workouts/week).
# Users who are more/less active can adjust `activity_factor` in config.
_DEFAULT_ACTIVITY = 1.375


@dataclass(frozen=True)
class DailyTargets:
    maintenance_kcal: int
    target_kcal: int           # deficit for weight loss
    protein_g: int             # 1.8 × bodyweight kg (mid-range 1.6-2.0)
    steps: int                 # DAILY_STEP_TARGET


def daily_targets(weight_kg: float, height_cm: float,
                  age: int, male: bool,
                  activity_factor: float = _DEFAULT_ACTIVITY,
                  deficit_kcal: int = 500,
                  protein_g_per_kg: float = 1.8) -> DailyTargets:
    """Return kcal + protein + step targets for one day.

    - `maintenance_kcal` = BMR × activity_factor (Harris-Benedict style TDEE)
    - `target_kcal` = maintenance − deficit_kcal
    - `protein_g` = protein_g_per_kg × weight_kg
    - `steps` = the module-wide DAILY_STEP_TARGET
    """
    bmr = mifflin_st_jeor_bmr(weight_kg, height_cm, age, male)
    maintenance = bmr * activity_factor
    return DailyTargets(
        maintenance_kcal=int(round(maintenance)),
        target_kcal=int(round(maintenance - deficit_kcal)),
        protein_g=int(round(protein_g_per_kg * weight_kg)),
        steps=DAILY_STEP_TARGET,
    )
