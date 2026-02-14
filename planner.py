# planner.py
from db import get_connection
from datetime import date, datetime, timedelta, time
import math

# We import get_connection from your db module when used by app
# This module exposes the main function generate_daily_plan(user_id, persist=False, plan_date=None)
# persist=True will insert rows into study_sessions table for the generated plan.

# Configurable constants
PRIORITY_WEIGHTS = {
    "difficulty": 0.3,
    "importance": 0.3,
    "confidence_inv": 0.4  # using (6 - confidence)
}
URGENCY_LOOKBACK_DAYS = 30
MAX_URGENCY_MULTIPLIER = 2.0  # when exam is today or passed
SESSION_PREFERRED_MINUTES = 50
SESSION_MINIMUM_MINUTES = 25
BREAK_MINUTES = 10   # not counted against user's daily_study_hours (assumption)
DEFAULT_DAILY_HOURS = 2.0
DEFAULT_START_TIME = time(hour=8, minute=0)  # sequentially schedule from 08:00 if persisting


def compute_priority_score(difficulty, importance, confidence):
    """Compute intrinsic priority score for a topic."""
    return (
        difficulty * PRIORITY_WEIGHTS["difficulty"]
        + importance * PRIORITY_WEIGHTS["importance"]
        + (6 - confidence) * PRIORITY_WEIGHTS["confidence_inv"]
    )


def compute_urgency_multiplier(days_until_exam):
    """Compute urgency multiplier from days until exam (int)."""
    if days_until_exam <= 0:
        return MAX_URGENCY_MULTIPLIER
    if days_until_exam >= URGENCY_LOOKBACK_DAYS:
        return 1.0
    # linear ramp from 1.0 -> MAX_URGENCY_MULTIPLIER as days -> 0
    frac = (URGENCY_LOOKBACK_DAYS - days_until_exam) / URGENCY_LOOKBACK_DAYS
    return 1.0 + frac * (MAX_URGENCY_MULTIPLIER - 1.0)


def minutes_from_hours(h):
    return int(round(h * 60))


def generate_daily_plan(get_connection, user_id, plan_date=None, persist=False):
    """
    Generate (and optionally save) a daily plan for user_id for plan_date (date obj).
    - get_connection: function to return a DB connection (use your db.get_connection)
    - plan_date: datetime.date object. If None, uses today().
    - persist: if True, inserts rows into study_sessions (status 'pending').
    Returns: dict with metadata and list of session dicts in order.
    """
    if plan_date is None:
        plan_date = date.today()

    # Connect DB
    db = get_connection()
    cur = db.cursor(dictionary=True)

    # 1) Get user preference (daily hours). Fallback default.
    cur.execute("SELECT daily_study_hours FROM user_preferences WHERE user_id=%s", (user_id,))
    pref = cur.fetchone()
    daily_hours = float(pref["daily_study_hours"]) if pref and pref.get("daily_study_hours") is not None else DEFAULT_DAILY_HOURS
    available_minutes = minutes_from_hours(daily_hours)
    # Guard minimum
    if available_minutes < SESSION_MINIMUM_MINUTES:
        # Minimum one session worth (25 minutes)
        available_minutes = SESSION_MINIMUM_MINUTES

    # 2) Get next upcoming exam per subject (subject -> next_exam_date)
    cur.execute("SELECT subject_id, MIN(exam_date) AS next_exam FROM exams WHERE exam_date >= %s GROUP BY subject_id", (plan_date,))
    exam_rows = cur.fetchall()
    next_exam_by_subject = {r["subject_id"]: r["next_exam"] for r in exam_rows}

    # 3) Get topics for user (join subjects to ensure user-owned)
    cur.execute("""
        SELECT t.topic_id, t.subject_id, t.topic_name, t.difficulty_level, t.importance, t.confidence_level, t.hours_required, s.subject_name
        FROM topics t
        JOIN subjects s ON t.subject_id = s.subject_id
        WHERE s.user_id = %s
    """, (user_id,))
    topic_rows = cur.fetchall()

    # If no topics: return empty plan
    if not topic_rows:
        cur.close()
        db.close()
        return {"date": plan_date.isoformat(), "daily_hours": daily_hours, "sessions": [], "note": "No topics available."}

    # 4) Build topic objects with computed effective priority
    topics = []
    for r in topic_rows:
        subj_id = r["subject_id"]
        next_exam = next_exam_by_subject.get(subj_id)
        if next_exam:
            days_until = (next_exam - plan_date).days
        else:
            days_until = 9999  # no exam -> very low urgency

        priority_score = compute_priority_score(r["difficulty_level"], r["importance"], r["confidence_level"])
        urgency_multiplier = compute_urgency_multiplier(days_until)
        spaced_multiplier = 1.2 if r["confidence_level"] < 3 else 1.0  # weakened topics get boost
        effective_priority = priority_score * urgency_multiplier * spaced_multiplier

        remaining_minutes = minutes_from_hours(float(r["hours_required"]))

        topics.append({
            "topic_id": r["topic_id"],
            "subject_id": subj_id,
            "subject_name": r["subject_name"],
            "topic_name": r["topic_name"],
            "difficulty": r["difficulty_level"],
            "importance": r["importance"],
            "confidence": r["confidence_level"],
            "hours_required": float(r["hours_required"]),
            "remaining_minutes": remaining_minutes,
            "priority_score": priority_score,
            "urgency_multiplier": urgency_multiplier,
            "spaced_multiplier": spaced_multiplier,
            "effective_priority": effective_priority,
            "days_until_exam": days_until
        })

    # 5) Sort topics by effective priority descending
    topics.sort(key=lambda x: x["effective_priority"], reverse=True)

    sessions = []
    # We'll first try to allocate as many full preferred sessions (50 min) as possible greedily.
    # Loop over topics and assign blocks of SESSION_PREFERRED_MINUTES while possible.
    minutes_left = available_minutes

    # Primary pass: assign 50-min blocks
    for t in topics:
        while minutes_left >= SESSION_PREFERRED_MINUTES and t["remaining_minutes"] >= SESSION_PREFERRED_MINUTES:
            # allocate one block
            sessions.append({
                "topic_id": chosen["topic_id"],
                "subject_id": chosen["subject_id"],
                "subject_name": chosen["subject_name"],
                "topic_name": chosen["topic_name"],
                "duration_minutes": int(alloc),   # make absolutely sure it's int
                "days_until_exam": chosen["days_until_exam"]
            })

            t["remaining_minutes"] -= SESSION_PREFERRED_MINUTES
            minutes_left -= SESSION_PREFERRED_MINUTES
        # continue to next topic

    # Secondary pass: try to allocate remaining time in chunks >= SESSION_MINIMUM_MINUTES
    # Re-sort topics again by remaining effective priority (some topics now still most urgent)
    topics.sort(key=lambda x: x["effective_priority"], reverse=True)
    # While we have enough minutes for at least minimum and any topic has remaining minutes:
    while minutes_left >= SESSION_MINIMUM_MINUTES:
        # find next topic with remaining_minutes >= SESSION_MINIMUM_MINUTES
        chosen = None
        for t in topics:
            if t["remaining_minutes"] >= SESSION_MINIMUM_MINUTES:
                chosen = t
                break
        if not chosen:
            break

        # allocate either remaining_minutes (capped by SESSION_PREFERRED or minutes_left)
        alloc = min(chosen["remaining_minutes"], SESSION_PREFERRED_MINUTES, minutes_left)
        # If alloc would be < SESSION_MINIMUM_MINUTES, break
        if alloc < SESSION_MINIMUM_MINUTES:
            break
        sessions.append({
            "topic_id": chosen["topic_id"],
            "subject_id": chosen["subject_id"],
            "subject_name": chosen["subject_name"],
            "topic_name": chosen["topic_name"],
            "duration_minutes": alloc,
            "notes": None,
            "days_until_exam": chosen["days_until_exam"]
        })
        chosen["remaining_minutes"] -= alloc
        minutes_left -= alloc

    # If no sessions allocated (rare), return empty note
    if not sessions:
        cur.close()
        db.close()
        return {"date": plan_date.isoformat(), "daily_hours": daily_hours, "sessions": [], "note": "Not enough time to schedule even a minimum session."}

    # Assign start times sequentially if persisting; otherwise default times
    # We will use DEFAULT_START_TIME (08:00) and increment by session + BREAK_MINUTES when saving.
    if persist:
        # Persist to study_sessions (status pending) and assign times starting from DEFAULT_START_TIME
        schedule_time = datetime.combine(plan_date, DEFAULT_START_TIME)
        cur_insert = db.cursor()
        for s in sessions:
            scheduled_time_str = schedule_time.time().strftime("%H:%M:%S")
            cur_insert.execute(
                "INSERT INTO study_sessions (user_id, topic_id, scheduled_date, scheduled_time, duration_minutes, status) VALUES (%s,%s,%s,%s,%s,%s)",
                (user_id, s["topic_id"], plan_date, scheduled_time_str, s["duration_minutes"], 'pending')
            )
            # increment schedule_time by duration + break
            schedule_time += timedelta(minutes=(s["duration_minutes"] + BREAK_MINUTES))
        db.commit()
        cur_insert.close()

    cur.close()
    db.close()

    # Return plan (sessions in order). Include leftover minutes and some metadata.
    return {
        "date": plan_date.isoformat(),
        "daily_hours": daily_hours,
        "available_minutes_initial": minutes_from_hours(daily_hours),
        "available_minutes_left": minutes_left,
        "sessions": sessions
    }

def generate_weekly_plan(user_id, start_date=None, persist=False):
    """
    Generate a week's plan for the user (7 days starting start_date or today).
    Respects completed minutes in study_sessions and tracks remaining minutes across the week.
    Returns a list of 7 day dicts. Each day dict contains a date (datetime.date object),
    daily_hours, available_minutes_left, and sessions list.
    """
    if start_date is None:
        start_date = date.today()

    db = get_connection()
    cur = db.cursor(dictionary=True)

    # 1) Get user preference
    cur.execute("SELECT daily_study_hours FROM user_preferences WHERE user_id=%s", (user_id,))
    pref = cur.fetchone()
    daily_hours = float(pref["daily_study_hours"]) if pref and pref.get("daily_study_hours") else DEFAULT_DAILY_HOURS
    daily_minutes = minutes_from_hours(daily_hours)
    if daily_minutes < SESSION_MINIMUM_MINUTES:
        daily_minutes = SESSION_MINIMUM_MINUTES

    # 2) Get next exams (for urgency calculations)
    cur.execute(
        "SELECT subject_id, MIN(exam_date) AS next_exam FROM exams WHERE exam_date >= %s GROUP BY subject_id",
        (start_date,)
    )
    exam_rows = cur.fetchall()
    next_exam_by_subject = {r["subject_id"]: r["next_exam"] for r in exam_rows}

    # 3) Load topics for this user
    cur.execute("""
        SELECT t.topic_id, t.subject_id, t.topic_name, t.difficulty_level, t.importance, t.confidence_level, t.hours_required, s.subject_name
        FROM topics t
        JOIN subjects s ON t.subject_id = s.subject_id
        WHERE s.user_id=%s
    """, (user_id,))
    topic_rows = cur.fetchall()

    if not topic_rows:
        cur.close()
        db.close()
        # Return 7 empty day dicts (dates are date objects)
        return [{
            "date": (start_date + timedelta(days=i)),
            "daily_hours": daily_hours,
            "available_minutes_left": daily_minutes,
            "sessions": [],
            "note": "No topics"
        } for i in range(7)]

    # 4) Get completed minutes per topic (so we subtract already-done work)
    topic_ids = [t["topic_id"] for t in topic_rows]
    completed_minutes_by_topic = {}
    if topic_ids:
        placeholders = ",".join(["%s"] * len(topic_ids))
        sql = f"""
            SELECT topic_id, COALESCE(SUM(duration_minutes),0) AS completed_minutes
            FROM study_sessions
            WHERE topic_id IN ({placeholders}) AND user_id=%s AND status='completed'
            GROUP BY topic_id
        """
        params = tuple(topic_ids) + (user_id,)
        cur.execute(sql, params)
        for r in cur.fetchall():
            completed_minutes_by_topic[r["topic_id"]] = int(r["completed_minutes"] or 0)

    # 5) Build topics_by_id mapping with normalized keys and remaining_minutes
    topics_by_id = {}
    for t in topic_rows:
        tid = t["topic_id"]
        total_minutes = minutes_from_hours(float(t["hours_required"]))
        completed = completed_minutes_by_topic.get(tid, 0)
        remaining = total_minutes - completed
        if remaining <= 0:
            # already completed — skip adding it
            continue

        topics_by_id[tid] = {
            "topic_id": tid,
            "subject_id": t["subject_id"],
            "subject_name": t["subject_name"],
            "topic_name": t["topic_name"],
            "difficulty": t.get("difficulty_level", 1),
            "importance": t.get("importance", 1),
            "confidence": t.get("confidence_level", 3),
            "hours_required": float(t["hours_required"]),
            "remaining_minutes": remaining
        }

    # If all topics are completed
    if not topics_by_id:
        cur.close()
        db.close()
        return [{
            "date": (start_date + timedelta(days=i)),
            "daily_hours": daily_hours,
            "available_minutes_left": daily_minutes,
            "sessions": [],
            "note": "All topics already completed."
        } for i in range(7)]

    # Prepare cursor for inserts if persisting
    cur_insert = None
    if persist:
        cur_insert = db.cursor()

    weekly_plan = []

    # 6) Build plan day-by-day, updating topics_by_id remaining_minutes as we allocate
    for day_offset in range(7):
        plan_date = start_date + timedelta(days=day_offset)
        minutes_left = daily_minutes

        # Build today's candidate list from topics_by_id (only items with remaining_minutes > 0)
        todays = []
        for t in topics_by_id.values():
            if t["remaining_minutes"] <= 0:
                continue
            next_exam = next_exam_by_subject.get(t["subject_id"])
            days_until = (next_exam - plan_date).days if next_exam else None
            priority_score = compute_priority_score(t["difficulty"], t["importance"], t["confidence"])
            urgency_multiplier = compute_urgency_multiplier(days_until if days_until is not None else 9999)
            spaced = 1.2 if t["confidence"] < 3 else 1.0
            effective_priority = priority_score * urgency_multiplier * spaced

            todays.append({
                "topic_id": t["topic_id"],
                "subject_id": t["subject_id"],
                "subject_name": t["subject_name"],
                "topic_name": t["topic_name"],
                "remaining_minutes": t["remaining_minutes"],
                "effective_priority": effective_priority,
                "days_until_exam": days_until
            })

        # Sort today's candidates by effective priority
        todays.sort(key=lambda x: x["effective_priority"], reverse=True)

        sessions = []

        # Primary allocation: 50-min blocks
        for cand in todays:
            while minutes_left >= SESSION_PREFERRED_MINUTES and cand["remaining_minutes"] >= SESSION_PREFERRED_MINUTES:
                alloc = SESSION_PREFERRED_MINUTES
                sessions.append({
                    "topic_id": cand["topic_id"],
                    "subject_id": cand["subject_id"],
                    "subject_name": cand["subject_name"],
                    "topic_name": cand["topic_name"],
                    "duration_minutes": alloc,
                    "days_until_exam": cand["days_until_exam"]
                })
                # decrement both candidate and master record
                cand["remaining_minutes"] -= alloc
                topics_by_id[cand["topic_id"]]["remaining_minutes"] = cand["remaining_minutes"]
                minutes_left -= alloc

        # Secondary allocation: fill remaining minutes >= minimum
        # Rebuild todays list (some remaining_minutes may have changed)
        todays = [t for t in todays if t["remaining_minutes"] >= SESSION_MINIMUM_MINUTES]
        todays.sort(key=lambda x: x["effective_priority"], reverse=True)

        while minutes_left >= SESSION_MINIMUM_MINUTES and todays:
            # pick first candidate with remaining_minutes >= minimum
            chosen = None
            for c in todays:
                if c["remaining_minutes"] >= SESSION_MINIMUM_MINUTES:
                    chosen = c
                    break
            if not chosen:
                break
            alloc = min(chosen["remaining_minutes"], SESSION_PREFERRED_MINUTES, minutes_left)
            if alloc < SESSION_MINIMUM_MINUTES:
                break
            sessions.append({
                "topic_id": chosen["topic_id"],
                "subject_id": chosen["subject_id"],
                "subject_name": chosen["subject_name"],
                "topic_name": chosen["topic_name"],
                "duration_minutes": int(alloc),
                "days_until_exam": chosen["days_until_exam"]
            })
            chosen["remaining_minutes"] -= alloc
            topics_by_id[chosen["topic_id"]]["remaining_minutes"] = chosen["remaining_minutes"]
            minutes_left -= alloc

            # refresh todays list for the loop
            todays = [t for t in todays if topics_by_id[t["topic_id"]]["remaining_minutes"] >= SESSION_MINIMUM_MINUTES]
            todays.sort(key=lambda x: x["effective_priority"], reverse=True)

        # Persist today's sessions if requested
        if persist and sessions:
            schedule_time = datetime.combine(plan_date, DEFAULT_START_TIME)
            for s in sessions:
                cur_insert.execute(
                    "INSERT INTO study_sessions (user_id, topic_id, scheduled_date, scheduled_time, duration_minutes, status) VALUES (%s,%s,%s,%s,%s,%s)",
                    (user_id, s["topic_id"], plan_date, schedule_time.time().strftime("%H:%M:%S"), s["duration_minutes"], "pending")
                )
                schedule_time += timedelta(minutes=(s["duration_minutes"] + BREAK_MINUTES))

        day_entry = {
            "date": plan_date,                  # keep as date object for jinja strftime
            "daily_hours": daily_hours,
            "available_minutes_initial": daily_minutes,
            "available_minutes_left": minutes_left,
            "sessions": sessions
        }
        if not sessions:
            day_entry["note"] = "No sessions scheduled today (all topics exhausted or not enough time)."

        weekly_plan.append(day_entry)

    # commit inserts if we persisted
    if persist and cur_insert:
        db.commit()
        cur_insert.close()

    cur.close()
    db.close()
    return weekly_plan
