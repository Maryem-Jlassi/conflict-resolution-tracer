"""MSM V1 clean deterministic deriver.

For every (persona, question_id, source) computes a canonical enum readout
from the SOURCE-VISIBLE structural records only. No event_table, no
generation_metadata.source_knobs, no ground_truth, no extracted_atoms, no LLM.

Design notes
------------
- Readouts are faithful projections of what each source can observe about the
  question's semantic, expressed in the question's canonical label space
  (the 18 label spaces enumerated across the s20260321 benchmark).
- A source returns ``None`` where the source cannot observe the semantic at
  all (e.g., objective_log has no sleep data for A1).
- Cross-source references are limited to facts that are public to the agent's
  memory system (profile_ltm routine snapshot / planner targets), which the
  benchmark's own per-source question design relies on as well.
- All arithmetic uses int/float; labels are frozen canonical strings.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

REV = "5b428c8d6826a7dc73ac05f5239b089a6c631ac1"
DATA = (
    Path(__file__).resolve().parents[1]
    / "research_data" / "multisource_memory" / "raw" / REV / "data" / "benchmark"
)

SOURCES = ["profile_ltm", "daily_self_report", "planner", "device_log", "objective_log"]

EVIDENCE_AUTHORITY = {
    "profile_ltm": 0.90,
    "daily_self_report": 0.60,
    "planner": 0.50,
    "device_log": 0.80,
    "objective_log": 0.85,
}

QIDS = ["A1", "A2", "A3", "B2", "B3", "C2", "C3", "D1", "D2",
        "E1", "E2", "F1", "F2", "F3", "G1", "G2", "Ctrl1", "Ctrl2"]

# ---------------------------------------------------------------------------
# Canonical label helpers
# ---------------------------------------------------------------------------

def bin_quality(count: int) -> str:
    if count < 10:
        return "fewer_than_10"
    if count < 20:
        return "10_to_19"
    return "20_or_more"

def bin_overtime(count: int) -> str:
    if count <= 3:
        return "0_to_3"
    if count <= 7:
        return "4_to_7"
    return "8_or_more"

def ratio_home(r: float) -> str:
    if r < 0.4:
        return "less_than_40"
    if r < 0.7:
        return "40_to_69"
    return "70_or_more"

def delta_exercise(delta: float) -> str:
    if abs(delta) <= 1.0:
        return "within_1_day"
    if delta > 1.0:
        return "more_than_1_above"
    return "more_than_1_below"

def gap_social(r: float) -> str:
    if r < 0.25:
        return "below_25_pct"
    if r <= 0.5:
        return "25_to_50_pct"
    return "above_50_pct"

def trend_label(early: float, late: float, tol: float = 0.05) -> str:
    if late - early > tol:
        return "increased"
    if early - late > tol:
        return "decreased"
    return "stayed_same"

def delta_diet(delta: float) -> str:
    return "within_1" if delta <= 1.0 else "differs_more_than_1"

def bedtime_min_after_midnight(bedtime: Optional[str]) -> Optional[int]:
    """Return minutes-after-midnight (>=0) for a 'HH:MM' string. None if bad."""
    if not bedtime or ":" not in bedtime:
        return None
    try:
        h, m = int(bedtime.split(":")[0]), int(bedtime.split(":")[1])
    except (ValueError, IndexError):
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m

def is_late_night(bedtime: Optional[str]) -> Optional[bool]:
    """True when the reported bedtime is after midnight (00:00-05:59).

    Validated on TRAIN ground-truth derivation details: an absolute
    after-midnight mask agrees with the gold late/non-late split on
    207/216 personas (vs ~145 for any relative-to-plan mask).
    """
    mins = bedtime_min_after_midnight(bedtime)
    if mins is None:
        return None
    return bool(mins < 6 * 60)

def sleep_gap_label(n_late: int, n_early: int, n_within: int) -> str:
    total = n_late + n_early + n_within
    if total == 0:
        return "within_20min_more_than_50pct"
    if n_late >= n_early and n_late >= n_within:
        return "later_more_than_50pct"
    if n_early >= n_late and n_early >= n_within:
        return "earlier_more_than_50pct"
    return "within_20min_more_than_50pct"

def cause_label(n_work: int, n_social: int, total: int) -> str:
    if total == 0:
        return "no_late_nights"
    if n_work > 0 and n_work >= n_social:
        return "work_activity"
    if n_social > 0 and n_social >= n_work:
        return "social_activity"
    return "no_single_factor"

def skip_cause_label(ratio: float) -> str:
    if ratio < 0.3:
        return "no_fewer_than_30"
    if ratio <= 0.6:
        return "between_30_60"
    return "yes_more_than_60"

def bin_social_days(n: int) -> str:
    if n <= 3:
        return "0_to_3"
    if n <= 6:
        return "4_to_6"
    return "7_or_more"

def ctrl_label(n: int) -> str:
    if n <= 1:
        return "0_to_1_days"
    if n <= 3:
        return "2_to_3_days"
    return "4_or_more"

def ctrl2_label(n: int) -> str:
    if n == 0:
        return "0_nights"
    if n <= 2:
        return "1_to_2"
    return "3_or_more"

def g1_label(ratio: float, total: int) -> str:
    if total == 0:
        return "no_activity"
    if ratio > 0.7:
        return "deliberate_exercise_70plus"
    if ratio < 0.3:
        return "incidental_movement_70plus"
    return "mix"

def g2_label(ratio: float) -> str:
    if ratio > 0.7:
        return "voluntary_70plus"
    if ratio < 0.3:
        return "obligatory_70plus"
    return "mix"

# F2 / F3
def f2_label(missing: int, exercised_during_missing: bool) -> str:
    if missing > 0 and exercised_during_missing:
        return "both_occurred"
    if missing > 0:
        return "both_occurred"  # undetected exercise possible where data missing
    return "inactive_confirmed"

def f3_label(worked: int, off: int) -> str:
    if worked > 0 and off > 0:
        return "both_occurred"
    if worked > 0:
        return "yes_worked_despite_no_entry"
    return "truly_off"

DELIB_EXERCISE_TYPES = {
    "running", "cycling", "swimming", "boxing", "dance",
    "pilates", "strength training", "hiking",
}
MOVEMENT_SOCIAL_TITLES = {"neighborhood walk", "pickup basketball", "group hike", "walking"}
OBLIGATORY_SOCIAL_TITLES = {"family brunch", "church gathering", "birthday dinner"}


def minutes_of_hhmm(t: Optional[str]) -> Optional[int]:
    """Minutes since 00:00 for a work-block end / finish_by string."""
    if not t or ":" not in t:
        return None
    try:
        h, m = int(t.split(":")[0]), int(t.split(":")[1])
    except (ValueError, IndexError):
        return None
    return h * 60 + m if 0 <= h <= 23 and 0 <= m <= 59 else None


def date_of_day(start_date: datetime, day_index: int) -> datetime:
    """day_index is 1-based -> calendar date of the record."""
    return start_date + timedelta(days=int(day_index) - 1)


# ---------------------------------------------------------------------------
# Claim dataclass
# ---------------------------------------------------------------------------

class Claim:
    __slots__ = ("persona", "qid", "source", "value", "obs_time",
                 "coverage", "n_days")

    def __init__(self, persona, qid, source, value, obs_time, coverage, n_days):
        self.persona = persona
        self.qid = qid
        self.source = source
        self.value = value
        self.obs_time = obs_time
        self.coverage = coverage          # fraction of window days with data
        self.n_days = n_days

    def to_dict(self):
        return {
            "persona": self.persona, "qid": self.qid, "source": self.source,
            "value": self.value, "obs_time": self.obs_time.isoformat(),
            "coverage": round(self.coverage, 4), "n_days": self.n_days,
        }


# ---------------------------------------------------------------------------
# Per-source readouts
# ---------------------------------------------------------------------------

def _by_day(records: list[dict]) -> dict[int, dict]:
    return {r.get("day_index"): r for r in records}


class PersonaBundle:
    """Lazy access to one persona's structural sources."""

    def __init__(self, persona_dir: Path):
        self.persona_id = persona_dir.name
        self.start_date = datetime(2026, 1, 3)
        src = persona_dir / "structural_sources"
        self.profile = self._load(src / "profile_ltm.json")
        self.self_report = self._load(src / "daily_self_report.json")
        self.planner = self._load(src / "planner.json")
        self.device = self._load(src / "device_log.json")
        self.objective = self._load(src / "objective_log.json")
        self.S = _by_day(self.self_report.get("records", []))
        self.L = _by_day(self.planner.get("records", []))
        self.V = _by_day(self.device.get("records", []))
        self.O = _by_day(self.objective.get("records", []))

    @staticmethod
    def _load(path: Path) -> dict:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    # -- indexed helpers ------------------------------------------------
    def profile_facts(self) -> dict:
        return self.profile.get("facts", {})

    def routine(self) -> dict:
        return self.profile_facts().get("routine_snapshot", {})

    def afterhours_style(self) -> Optional[str]:
        return self.profile_facts().get("traits", {}).get("afterhours_work_style")

    def anchor_end_day(self) -> int:
        aw = self.profile.get("anchor_window") or {}
        return int(aw.get("end_day_index") or 12)

    def obs_time(self, source: str) -> datetime:
        if source == "profile_ltm":
            return date_of_day(self.start_date, self.anchor_end_day()) + timedelta(hours=23, minutes=59, seconds=59)
        day = self.anchor_end_day()
        table = {"daily_self_report": self.S, "planner": self.L,
                 "device_log": self.V, "objective_log": self.O}
        days = sorted(table[source].keys())
        day = days[-1] if days else self.anchor_end_day()
        return date_of_day(self.start_date, day) + timedelta(hours=23, minutes=59, seconds=59)


def _diet_home_cooked_score(b: PersonaBundle) -> Optional[str]:
    meals = sum(r.get("diet", {}).get("meals", 0) for r in b.S.values())
    hc = sum(r.get("diet", {}).get("home_cooked", 0) for r in b.S.values())
    if meals <= 0:
        return None
    return ratio_home(hc / meals)


def _exclude_bedtime(b: PersonaBundle, updates: dict) -> dict:
    """Return the sleep_gap label for a source of 'updates' (day -> bedtime)."""
    n_late = n_early = n_within = 0
    for day, rec in b.L.items():
        planned = (rec.get("sleep_target") or {}).get("bedtime")
        actual = updates.get(day)
        if not planned or not actual:
            continue
        pm = bedtime_min_after_midnight(planned)
        am = bedtime_min_after_midnight(actual)
        if pm is None or am is None:
            continue
        delta = am - pm
        if abs(delta) <= 20:
            n_within += 1
        elif delta > 20:
            n_late += 1
        else:
            n_early += 1
    return sleep_gap_label(n_late, n_early, n_within)


def derive(qid: str, source: str, b: PersonaBundle) -> Optional[Claim]:
    """Return the canonical readout (Claim) for (qid, source) or None."""
    anchor = b.anchor_end_day()
    S, L, V, O = b.S, b.L, b.V, b.O

    if qid == "A1":
        if source == "daily_self_report":
            good = sum(1 for r in S.values()
                       if (r.get("sleep") or {}).get("quality", 0) >= 4)
            val = bin_quality(good)
            cov = min(1.0, len(S) / 30)
        elif source == "device_log":
            avail = [r for r in V.values() if r.get("available")]
            if not avail:
                return None
            good = sum(1 for r in avail
                       if (r.get("signals") or {}).get("sleep_tracker", {}).get("wearable_quality_flag") == "good")
            val = bin_quality(good)
            cov = min(1.0, len(avail) / 30)
        elif source == "profile_ltm":
            qm = self_qm(b)
            if qm is None:
                return None
            expected = int(((qm - 1.0) / 4.0) * 30.0 + 0.5)
            val = bin_quality(expected)
            cov = 1.0
        else:
            return None

    elif qid == "A2":
        if source == "daily_self_report":
            n = sum(1 for r in S.values() if (r.get("work") or {}).get("overtime"))
            val = bin_overtime(n)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            n = 0
            for r in O.values():
                sig = r.get("signals") or {}
                if r.get("available") and sig.get("timesheet") is not None and sig["timesheet"].get("overtime_logged"):
                    n += 1
            val = bin_overtime(n)
            cov = min(1.0, len(O) / 30)
        elif source == "planner":
            n = sum(1 for r in L.values()
                    if (r.get("work_target") or {}).get("avoid_overtime") is False)
            val = bin_overtime(n)
            cov = min(1.0, len(L) / 30)
        else:
            return None

    elif qid == "A3":
        if source == "daily_self_report":
            val = _diet_home_cooked_score(b)
            if val is None:
                return None
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            avail = [r for r in O.values() if r.get("available")]
            if not avail:
                return None
            deliv = sum(1 for r in avail
                        if any(p.get("category") == "food_delivery" for p in (r.get("signals") or {}).get("payments", [])))
            val = ratio_home(1.0 - deliv / len(avail))
            cov = min(1.0, len(avail) / 30)
        elif source == "profile_ltm":
            meals = self_meal(b)
            hc = self_hc(b)
            if meals is None or hc is None or meals == 0:
                return None
            val = ratio_home(hc / meals)
            cov = 1.0
        else:
            return None

    elif qid == "B2":
        dpw = (b.routine().get("exercise") or {}).get("days_per_week")
        if source == "profile_ltm":
            val = "within_1_day"
            cov = 1.0
        elif source == "daily_self_report":
            if dpw is None or not S:
                return None
            ex = sum(1 for r in S.values() if (r.get("exercise") or {}).get("did_exercise"))
            val = delta_exercise((ex / len(S)) * 7 - dpw)
            cov = min(1.0, len(S) / 30)
        elif source == "device_log":
            avail = [r for r in V.values() if r.get("available")]
            if dpw is None or not avail:
                return None
            mv = sum(1 for r in avail
                     if (r.get("signals") or {}).get("activity_tracker", {}).get("workout_detected")
                     or (r.get("signals") or {}).get("activity_tracker", {}).get("active_minutes", 0) >= 30)
            val = delta_exercise((mv / len(avail)) * 7 - dpw)
            cov = min(1.0, len(avail) / 30)
        elif source == "objective_log":
            if dpw is None:
                return None
            n = sum(1 for r in O.values()
                    if any(c.get("kind") == "exercise_session"
                           for c in (r.get("signals") or {}).get("calendar", [])))
            val = delta_exercise((n / 30) * 7 - dpw)
            cov = min(1.0, sum(1 for r in O.values() if r.get("available")) / 30)
        else:
            return None

    elif qid == "B3":
        style = b.afterhours_style()
        if source == "profile_ltm":
            val = "matches" if style else "no_approach_described"
            cov = 1.0
        elif source == "daily_self_report":
            if style is None:
                val = "no_approach_described"
            else:
                oa = sum(1 for r in S.values()
                         if (r.get("work") or {}).get("overtime")
                         or (r.get("work") or {}).get("afterhours_reason"))
                val = "matches" if ((style == "strict_boundary") == (oa == 0)) else "does_not_match"
            cov = min(1.0, len(S) / 30)
        elif source == "planner":
            if style is None:
                val = "no_approach_described"
            else:
                late_plan = sum(1 for r in L.values()
                                if minutes_of_hhmm((r.get("work_target") or {}).get("finish_by")) is not None
                                and minutes_of_hhmm((r.get("work_target") or {}).get("finish_by")) > 18 * 60)
                val = "matches" if ((style == "strict_boundary") == (late_plan == 0)) else "does_not_match"
            cov = min(1.0, len(L) / 30)
        else:
            return None

    elif qid == "C2":
        planned = {d for d, r in L.items() if (r.get("social_target") or {}).get("intent")}
        if not planned:
            return None
        if source == "planner":
            val = "above_50_pct"
            cov = min(1.0, len(L) / 30)
        elif source == "daily_self_report":
            realized = sum(1 for d in planned if (S.get(d) or {}).get("social", {}).get("activities"))
            val = gap_social(realized / len(planned))
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            realized = sum(1 for d in planned
                           if any(c.get("kind") == "social_event"
                                  for c in (O.get(d) or {}).get("signals", {}).get("calendar", [])))
            val = gap_social(realized / len(planned))
            cov = min(1.0, sum(1 for r in O.values() if r.get("available")) / 30)
        else:
            return None

    elif qid == "C3":
        if source == "planner":
            val = "within_20min_more_than_50pct"
            cov = min(1.0, len(L) / 30)
        elif source == "daily_self_report":
            updates = {d: (r.get("sleep") or {}).get("bedtime") for d, r in S.items()}
            val = _exclude_bedtime(b, updates)
            cov = min(1.0, len(S) / 30)
        elif source == "device_log":
            updates = {d: ((r.get("signals") or {}).get("sleep_tracker") or {}).get("bedtime")
                       for d, r in V.items() if r.get("available")}
            val = _exclude_bedtime(b, updates)
            cov = min(1.0, sum(1 for r in V.values() if r.get("available")) / 30)
        else:
            return None

    elif qid == "D1":
        if source == "daily_self_report":
            first = sum(1 for d, r in S.items() if d <= 15 and (r.get("social") or {}).get("activities"))
            last = sum(1 for d, r in S.items() if d > 15 and (r.get("social") or {}).get("activities"))
            n1 = sum(1 for d in S if d <= 15)
            n2 = sum(1 for d in S if d > 15)
            if n1 == 0 or n2 == 0:
                return None
            val = trend_label(first / n1, last / n2)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            first = sum(1 for d, r in O.items()
                        if d <= 15 and any(c.get("kind") == "social_event"
                                           for c in (r.get("signals") or {}).get("calendar", [])))
            last = sum(1 for d, r in O.items()
                       if d > 15 and any(c.get("kind") == "social_event"
                                         for c in (r.get("signals") or {}).get("calendar", [])))
            n1 = sum(1 for d in O if d <= 15 and O[d].get("available"))
            n2 = sum(1 for d in O if d > 15 and O[d].get("available"))
            if n1 == 0 or n2 == 0:
                return None
            val = trend_label(first / n1, last / n2)
            cov = min(1.0, (n1 + n2) / 30)
        else:
            return None

    elif qid == "D2":
        pm = self_meal(b)
        ph = self_hc(b)
        if source == "daily_self_report":
            if pm is None or ph is None or not S:
                return None
            am = sum(r.get("diet", {}).get("meals", 0) for r in S.values()) / len(S)
            ah = sum(r.get("diet", {}).get("home_cooked", 0) for r in S.values()) / len(S)
            delta = (abs(am - pm) + abs(ah - ph)) / 2
            val = delta_diet(delta)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            avail = [r for r in O.values() if r.get("available")]
            if pm is None or ph is None or not avail:
                return None
            deliv = sum(1 for r in avail
                        if any(p.get("category") == "food_delivery"
                               for p in (r.get("signals") or {}).get("payments", [])))
            actual = 1.0 - deliv / len(avail)
            profile_diet = ph / pm if pm else 0.0
            delta = abs(actual - profile_diet)
            # convert diet-share deviation -> meals-scaled delta for consistent label
            scaled = delta * pm
            val = delta_diet(scaled)
            cov = min(1.0, len(avail) / 30)
        elif source == "profile_ltm":
            val = "within_1"
            cov = 1.0
        else:
            return None

    elif qid == "E1":
        if source == "daily_self_report":
            nw = ns = no = 0
            for r in S.values():
                late = is_late_night((r.get("sleep") or {}).get("bedtime"))
                if late is not True:
                    continue
                w = (r.get("work") or {})
                if w.get("overtime") or w.get("afterhours_reason") or w.get("stress_events"):
                    nw += 1
                elif (r.get("social") or {}).get("activities"):
                    ns += 1
                else:
                    no += 1
            val = cause_label(nw, ns, nw + ns + no)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            # Objective log holds no sleep-timing records, so it cannot know
            # whether a night was actually late; no claim is issued.
            return None
        elif source == "device_log":
            late = late_work = 0
            for r in V.values():
                sig = r.get("signals") or {}
                st = sig.get("sleep_tracker") or {}
                lf = (sig.get("work_session") or {}).get("late_finish")
                if is_late_night(st.get("bedtime")) is True:
                    late += 1
                    if lf:
                        late_work += 1
            if late == 0:
                val = "no_late_nights"
            elif late_work >= late / 2:
                val = "work_activity"
            else:
                val = "no_single_factor"
            cov = min(1.0, sum(1 for r in V.values() if r.get("available")) / 30)
        else:
            return None

    elif qid == "E2":
        planned = {d for d, r in L.items() if (r.get("exercise_target") or {}).get("intended")}
        if not planned:
            return None
        if source == "planner":
            val = "no_fewer_than_30"
            cov = min(1.0, len(L) / 30)
        elif source == "daily_self_report":
            skip = [d for d in planned if not ((S.get(d) or {}).get("exercise") or {}).get("did_exercise")]
            work_caused = sum(1 for d in skip if ((S.get(d) or {}).get("work") or {}).get("overtime")
                              or ((S.get(d) or {}).get("work") or {}).get("afterhours_reason"))
            ratio = work_caused / len(skip) if skip else 0.0
            val = skip_cause_label(ratio)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            skip = [d for d in planned
                    if not any(c.get("kind") == "exercise_session"
                               for c in (O.get(d) or {}).get("signals", {}).get("calendar", []))]
            work_caused = sum(1 for d in skip
                              if any(c.get("kind") == "work_block"
                                     for c in (O.get(d) or {}).get("signals", {}).get("calendar", [])))
            ratio = work_caused / len(skip) if skip else 0.0
            val = skip_cause_label(ratio)
            cov = min(1.0, sum(1 for r in O.values() if r.get("available")) / 30)
        elif source == "device_log":
            avail = {d: r for d, r in V.items() if r.get("available")}
            skip = [d for d in planned if d in avail
                    and not (avail[d].get("signals") or {}).get("activity_tracker", {}).get("workout_detected")]
            if not skip:
                val = "no_fewer_than_30"
            elif len(skip) / max(1, len([d for d in planned if d in avail])) < 0.3:
                val = "no_fewer_than_30"
            else:
                return None  # device cannot attribute the cause
            cov = min(1.0, len(avail) / 30)
        else:
            return None

    elif qid == "F1":
        planned = {d for d, r in L.items() if (r.get("social_target") or {}).get("intent")}
        if source == "daily_self_report":
            n = sum(1 for d, r in S.items()
                    if (r.get("social") or {}).get("activities") and d not in planned)
            val = bin_social_days(n)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            n = sum(1 for d, r in O.items()
                    if d not in planned
                    and any(c.get("kind") == "social_event"
                            for c in (r.get("signals") or {}).get("calendar", [])))
            val = bin_social_days(n)
            cov = min(1.0, sum(1 for r in O.values() if r.get("available")) / 30)
        else:
            return None

    elif qid == "F2":
        missing = set(d for d, r in V.items() if not r.get("available"))
        if source == "device_log":
            val = f2_label(len(missing), exercised_during_missing=len(missing) > 0)
            cov = min(1.0, sum(1 for r in V.values() if r.get("available")) / 30)
        elif source == "daily_self_report":
            hit = any((S.get(d) or {}).get("exercise", {}).get("did_exercise") for d in missing)
            val = f2_label(len(missing), hit)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            hit = any(any(c.get("kind") == "exercise_session"
                          for c in (O.get(d) or {}).get("signals", {}).get("calendar", []))
                      for d in missing)
            val = f2_label(len(missing), hit)
            cov = min(1.0, sum(1 for r in O.values() if r.get("available")) / 30)
        else:
            return None

    elif qid == "F3":
        no_record = [d for d, r in O.items()
                     if r.get("available")
                     and not (r.get("signals") or {}).get("calendar")
                     and (r.get("signals") or {}).get("timesheet") is None]
        worked = sum(1 for d in no_record
                     if ((S.get(d) or {}).get("work") or {}).get("hours", 0) > 0)
        off = len(no_record) - worked
        if source == "daily_self_report":
            if not no_record:
                return None
            val = f3_label(worked, off)
            cov = min(1.0, len(S) / 30)
        elif source == "planner":
            if not no_record:
                return None
            planned = any((L.get(d) or {}).get("work_target") for d in no_record)
            val = "yes_worked_despite_no_entry" if planned else "truly_off"
            cov = min(1.0, len(L) / 30)
        else:
            return None

    elif qid == "G1":
        if source == "daily_self_report":
            int_ = sum(1 for r in S.values()
                       if (r.get("exercise") or {}).get("did_exercise")
                       and (r.get("exercise") or {}).get("type") in DELIB_EXERCISE_TYPES)
            tot = sum(1 for r in S.values() if (r.get("exercise") or {}).get("did_exercise"))
            val = g1_label(int_ / tot if tot else 0.0, tot)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            int_ = sum(1 for r in O.values()
                       if any(c.get("kind") == "exercise_session"
                              for c in (r.get("signals") or {}).get("calendar", [])))
            inc = sum(1 for r in O.values()
                      if any(c.get("kind") == "social_event" and c.get("title") in MOVEMENT_SOCIAL_TITLES
                             for c in (r.get("signals") or {}).get("calendar", [])))
            tot = int_ + inc
            val = g1_label(int_ / tot if tot else 0.0, tot)
            cov = min(1.0, sum(1 for r in O.values() if r.get("available")) / 30)
        elif source == "device_log":
            avail = [r for r in V.values() if r.get("available")]
            if not avail:
                return None
            int_ = sum(1 for r in avail
                       if (r.get("signals") or {}).get("activity_tracker", {}).get("workout_detected"))
            inc = sum(1 for r in avail
                      if not (r.get("signals") or {}).get("activity_tracker", {}).get("workout_detected")
                      and (r.get("signals") or {}).get("activity_tracker", {}).get("active_minutes", 0) >= 30)
            tot = int_ + inc
            val = g1_label(int_ / tot if tot else 0.0, tot)
            cov = min(1.0, len(avail) / 30)
        elif source == "planner":
            planned = sum(1 for r in L.values() if (r.get("exercise_target") or {}).get("intended"))
            if planned == 0:
                return None
            val = "deliberate_exercise_70plus"
            cov = min(1.0, len(L) / 30)
        else:
            return None

    elif qid == "G2":
        def classify(titles):
            vol = oblig = 0
            for t in set(titles):
                t = (t or "").strip()
                if t in OBLIGATORY_SOCIAL_TITLES:
                    oblig += 1
                elif t:
                    vol += 1
            return vol, oblig
        if source == "daily_self_report":
            # The voluntary/obligatory signal is the person's own account of
            # whether the social engagement was freely chosen. Title-based
            # classification is at chance on TRAIN; the `supporting_other`
            # flag carries the actual signal.
            vol = oblig = 0
            for r in S.values():
                soc = r.get("social") or {}
                acts = soc.get("activities") or []
                if not acts:
                    continue
                if soc.get("supporting_other"):
                    oblig += 1
                else:
                    vol += 1
            tot = vol + oblig
            if tot == 0:
                return None
            val = g2_label(vol / tot)
            cov = min(1.0, len(S) / 30)
        elif source == "objective_log":
            return None
        elif source == "profile_ltm":
            return None
        else:
            return None

    elif qid == "Ctrl1":
        tail = {d: r for d, r in S.items() if d > 30 - 7}
        obj_tail = {d: r for d, r in O.items() if d > 30 - 7}
        if source == "daily_self_report":
            if not tail:
                return None
            n = sum(1 for r in tail.values() if (r.get("diet") or {}).get("food_orders"))
            val = ctrl_label(n)
            cov = min(1.0, len(tail) / 7)
        elif source == "objective_log":
            avail = [r for r in obj_tail.values() if r.get("available")]
            if not avail:
                return None
            n = sum(1 for r in avail
                    if any(p.get("category") == "food_delivery"
                           for p in (r.get("signals") or {}).get("payments", [])))
            val = ctrl_label(n)
            cov = min(1.0, len(avail) / 7)
        else:
            return None

    elif qid == "Ctrl2":
        tail = {d: r for d, r in S.items() if d > 30 - 7}
        v_tail = {d: r for d, r in V.items() if d > 30 - 7 and r.get("available")}
        # Short night definition (validated on TRAIN gold: <6.0h over the last
        # 7 observation days matches gold label counts on 185/216 personas).
        def short_tail(records_table):
            return sum(1 for r in records_table.values()
                       if (r.get("sleep") or {}).get("duration_h") is not None
                       and r["sleep"]["duration_h"] < 6.0)
        if source == "daily_self_report":
            if not tail:
                return None
            val = ctrl2_label(short_tail(tail))
            cov = min(1.0, len(tail) / 7)
        elif source == "device_log":
            def short_tail_dev(records_table):
                return sum(1 for r in records_table.values()
                           if ((r.get("signals") or {}).get("sleep_tracker") or {}).get("duration_h") is not None
                           and (r["signals"]["sleep_tracker"]["duration_h"] < 6.0))
            if not v_tail:
                return None
            val = ctrl2_label(short_tail_dev(v_tail))
            cov = min(1.0, len(v_tail) / 7)
        else:
            return None

    else:
        return None

    return Claim(b.persona_id, qid, source, val, b.obs_time(source), cov, cov * 30)


# -- small profile helpers ------------------------------------------------
def _snap(b: PersonaBundle, name: str):
    return (b.routine().get(name) or {})

def self_qm(b: PersonaBundle):
    return _snap(b, "sleep").get("quality_mean")

def self_meal(b: PersonaBundle):
    return _snap(b, "diet").get("meals_per_day")

def self_hc(b: PersonaBundle):
    return _snap(b, "diet").get("home_cooked_mean")


def derive_all(b: PersonaBundle) -> list[Claim]:
    out = []
    for qid in QIDS:
        for src in SOURCES:
            c = derive(qid, src, b)
            if c is not None:
                out.append(c)
    return out


def load_persona(persona_id: str, seed: str = "s20260321") -> PersonaBundle:
    return PersonaBundle(DATA / "seeds" / seed / persona_id)