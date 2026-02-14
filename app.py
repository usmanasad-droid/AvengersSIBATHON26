from flask import Flask, render_template, request, redirect, session
from planner import generate_weekly_plan
from db import get_connection

app = Flask(__name__)
app.secret_key = "simple_secret_key"   # required for sessions


# ---------------- LOGIN ----------------
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        db = get_connection()
        cur = db.cursor(dictionary=True)

        cur.execute(
            "SELECT * FROM users WHERE username=%s AND password=%s",
            (username, password)
        )
        user = cur.fetchone()

        cur.close()
        db.close()

        if user:
            session["user_id"] = user["user_id"]
            session["username"] = user["username"]
            return redirect("/dashboard")
        else:
            error = "Invalid username or password"

    return render_template("login.html", error=error)


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        email = request.form["email"]
        password = request.form["password"]

        db = get_connection()
        cur = db.cursor()

        cur.execute(
            "INSERT INTO users (username, email, password) VALUES (%s,%s,%s)",
            (username, email, password)
        )

        db.commit()
        cur.close()
        db.close()

        return redirect("/login")

    return render_template("register.html")


# ---------------- DASHBOARD ----------------
@app.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    db = get_connection()
    cur = db.cursor(dictionary=True)

    cur.execute("""
        SELECT 
            s.subject_id,
            s.subject_name,

            t.topic_id,
            t.topic_name,
            t.difficulty_level,
            t.importance,
            t.confidence_level,
            t.hours_required,

            e.exam_id,
            e.exam_name,
            e.exam_date

        FROM subjects s
        LEFT JOIN topics t ON s.subject_id = t.subject_id
        LEFT JOIN exams e ON s.subject_id = e.subject_id
        WHERE s.user_id = %s
        ORDER BY s.created_at DESC, t.topic_id ASC
    """, (user_id,))

    rows = cur.fetchall()
    cur.close()
    db.close()

    subjects = {}

    for row in rows:
        sid = row["subject_id"]

        if sid not in subjects:
            subjects[sid] = {
                "subject_id": sid,
                "subject_name": row["subject_name"],
                "topics": [],
                "exams": []
            }

        # topics
        if row["topic_id"]:
            subjects[sid]["topics"].append({
                "topic_name": row["topic_name"],
                "difficulty_level": row["difficulty_level"],
                "importance": row["importance"],
                "confidence_level": row["confidence_level"],
                "hours_required": row["hours_required"]
            })

        # exams (avoid duplicates)
        if row["exam_id"]:
            exam = {
                "exam_name": row["exam_name"],
                "exam_date": row["exam_date"]
            }
            if exam not in subjects[sid]["exams"]:
                subjects[sid]["exams"].append(exam)

    return render_template("dashboard.html", subjects=list(subjects.values()))


# ---------------- DELETE SUBJECT ----------------
@app.route("/deletesubject/<int:subject_id>")
def delete_subject(subject_id):
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    db = get_connection()
    cur = db.cursor()

    try:
        # 1) Ensure the subject belongs to the logged-in user
        cur.execute("SELECT subject_id FROM subjects WHERE subject_id=%s AND user_id=%s", (subject_id, user_id))
        if not cur.fetchone():
            cur.close()
            db.close()
            return "Subject not found or not authorized", 403

        # Begin transaction (most connectors auto-begin)
        # 2) Delete study_sessions for topics under this subject
        #    (if your DB uses ON DELETE CASCADE this may be redundant, but explicit deletion is safe)
        cur.execute("DELETE ss FROM study_sessions ss JOIN topics t ON ss.topic_id = t.topic_id WHERE t.subject_id = %s", (subject_id,))

        # 3) Delete exams for this subject
        cur.execute("DELETE FROM exams WHERE subject_id=%s", (subject_id,))

        # 4) Delete topics under this subject
        cur.execute("DELETE FROM topics WHERE subject_id=%s", (subject_id,))

        # 5) Finally delete the subject
        cur.execute("DELETE FROM subjects WHERE subject_id=%s", (subject_id,))

        db.commit()
    except Exception as e:
        db.rollback()
        # optionally log e somewhere
        cur.close()
        db.close()
        return "Error deleting subject: {}".format(str(e)), 500
    finally:
        cur.close()
        db.close()

    return redirect("/dashboard")


# ---------------- ADD TOPICS ----------------
@app.route("/addtopics", methods=["GET", "POST"])
def add_topics():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    if request.method == "POST":
        subject_name = request.form.get("subject_name", "").strip()
        topic_names = request.form.getlist("topic_name[]")
        difficulties = request.form.getlist("difficulty_level[]")
        importances = request.form.getlist("importance[]")
        confidences = request.form.getlist("confidence_level[]")
        hours = request.form.getlist("hours_required[]")

        if not subject_name or not topic_names or all(not t.strip() for t in topic_names):
            # if validation fails, stay on form
            error = "Please provide subject name and at least one topic."
            return render_template("addtopics.html", error=error)

        db = get_connection()
        cur = db.cursor()

        try:
            # Insert subject
            cur.execute(
                "INSERT INTO subjects (user_id, subject_name) VALUES (%s,%s)",
                (user_id, subject_name)
            )
            subject_id = cur.lastrowid

            # Insert topics
            for i, name in enumerate(topic_names):
                name = name.strip()
                if not name:
                    continue
                cur.execute(
                    """INSERT INTO topics 
                       (subject_id, topic_name, difficulty_level, importance, confidence_level, hours_required)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (subject_id, name,
                     int(difficulties[i]),
                     int(importances[i]),
                     int(confidences[i]),
                     float(hours[i]))
                )

            db.commit()
        except Exception as e:
            db.rollback()
            error = "Error saving subject or topics. Make sure names are unique."
            cur.close()
            db.close()
            return render_template("addtopics.html", error=error)
        finally:
            cur.close()
            db.close()

        # ✅ Redirect to dashboard after successful save
        return redirect("/dashboard")

    # GET request
    return render_template("addtopics.html")


# ---------------- EDIT TOPICS ----------------
@app.route("/edittopics/<int:subject_id>", methods=["GET", "POST"])
def edit_topics(subject_id):
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    db = get_connection()
    cur = db.cursor(dictionary=True)

    # Ensure subject belongs to user
    cur.execute("SELECT * FROM subjects WHERE subject_id=%s AND user_id=%s", (subject_id, user_id))
    subject = cur.fetchone()
    if not subject:
        cur.close()
        db.close()
        return "Subject not found or not authorized", 403

    if request.method == "POST":
        topic_ids = request.form.getlist("topic_id[]")
        topic_names = request.form.getlist("topic_name[]")
        difficulties = request.form.getlist("difficulty_level[]")
        importances = request.form.getlist("importance[]")
        confidences = request.form.getlist("confidence_level[]")
        hours = request.form.getlist("hours_required[]")

        try:
            # Update each topic
            for i, tid in enumerate(topic_ids):
                cur.execute("""
                    UPDATE topics 
                    SET topic_name=%s, difficulty_level=%s, importance=%s, confidence_level=%s, hours_required=%s
                    WHERE topic_id=%s AND subject_id=%s
                """, (
                    topic_names[i].strip(),
                    int(difficulties[i]),
                    int(importances[i]),
                    int(confidences[i]),
                    float(hours[i]),
                    int(tid),
                    subject_id
                ))
            db.commit()
        except Exception as e:
            db.rollback()
            cur.close()
            db.close()
            return f"Error updating topics: {str(e)}", 500
        finally:
            cur.close()
            db.close()
        return redirect("/dashboard")

    # GET: load topics
    cur.execute("SELECT * FROM topics WHERE subject_id=%s", (subject_id,))
    topics = cur.fetchall()
    cur.close()
    db.close()

    return render_template("edittopics.html", subject=subject, topics=topics)


# ---------------- EXAMS ----------------
@app.route("/exams", methods=["GET", "POST"])
@app.route("/exams", methods=["GET", "POST"])
def exams():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    db = get_connection()
    cur = db.cursor(dictionary=True)

    if request.method == "POST":
        subject_id = request.form["subject_id"]
        exam_name = request.form["exam_name"].strip()
        exam_date = request.form["exam_date"]

        # Check if exam already exists for this subject
        cur.execute(
            "SELECT * FROM exams WHERE subject_id=%s",
            (subject_id,)
        )
        existing_exam = cur.fetchone()

        if existing_exam:
            # Update the existing exam
            cur.execute(
                "UPDATE exams SET exam_name=%s, exam_date=%s WHERE exam_id=%s",
                (exam_name, exam_date, existing_exam["exam_id"])
            )
        else:
            # Insert a new exam
            cur.execute(
                "INSERT INTO exams (subject_id, exam_name, exam_date) VALUES (%s, %s, %s)",
                (subject_id, exam_name, exam_date)
            )

        db.commit()
        cur.close()
        db.close()
        return redirect("/dashboard")

    # GET: load subjects for dropdown
    cur.execute("SELECT subject_id, subject_name FROM subjects WHERE user_id=%s", (user_id,))
    subjects = cur.fetchall()

    cur.close()
    db.close()
    return render_template("exams.html", subjects=subjects)


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------- USER PREFERENCES ----------------
@app.route("/preferences", methods=["GET", "POST"])
@app.route("/preferences", methods=["GET", "POST"])
def preferences():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]

    db = get_connection()
    cur = db.cursor()

    if request.method == "POST":
        hours = float(request.form["daily_study_hours"])

        # Insert new or update existing
        cur.execute("""
            INSERT INTO user_preferences (user_id, daily_study_hours)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE daily_study_hours = %s
        """, (user_id, hours, hours))

        db.commit()
        cur.close()
        db.close()
        return redirect("/dashboard")

    cur.close()
    db.close()
    return render_template("preferences.html")


# ---------------- WEEKLY PLAN ----------------
@app.route("/plan/weekly")
def weekly_plan():
    if "user_id" not in session:
        return redirect("/login")

    user_id = session["user_id"]
    weekly_plan_data = generate_weekly_plan(user_id)

    return render_template("weekly_plan.html", weekly_plan=weekly_plan_data)

if __name__ == "__main__":
    app.run(debug=True)
