from flask import Flask, render_template, request, redirect, session

from db import get_connection

app = Flask(__name__)
app.secret_key = "simple_secret_key"   # required for sessions


# ---------------- LOGIN ----------------
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
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
            return "Invalid username or password"

    return render_template("login.html")


# ---------------- REGISTER ----------------
@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        full_name = request.form["full_name"]
        email = request.form["email"]
        username = request.form["username"]
        password = request.form["password"]

        db = get_connection()
        cur = db.cursor()

        cur.execute(
            "INSERT INTO users (full_name, email, username, password) VALUES (%s,%s,%s,%s)",
            (full_name, email, username, password)
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

    return render_template("dashboard.html")


# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


if __name__ == "__main__":
    app.run(debug=True)
