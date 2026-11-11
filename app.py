"""AI-SPMS main Flask app.
Phase 1: auth | 2: projects | 3: team + tasks | 4: AI tasks
Phase 5: dashboard | 6: CSRF, security headers, error pages."""
import os
import re
import secrets
import sqlite3
from datetime import date, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, abort, jsonify)
from werkzeug.security import generate_password_hash, check_password_hash

import ai_service
from database import get_db, close_db, init_db

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True    # JS can't read the session cookie
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"   # not sent on cross-site POSTs
app.teardown_appcontext(close_db)

if app.secret_key == "dev-secret-change-me":
    print("WARNING: set a long random SECRET_KEY in your .env file.")

ROLES = ["Team Leader", "Team Member", "Mentor"]
PROJECT_ROLES = ["Member", "Mentor"]          # 'Owner' is set automatically
PRIORITIES = ["Low", "Medium", "High"]
STATUSES = ["To-Do", "In-Progress", "Completed"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

init_db()


# ---------------------------------------------------------------
# Security (Phase 6): CSRF protection + headers
# ---------------------------------------------------------------
def csrf_token():
    """Per-session random token. Templates read it via {{ csrf_token() }}."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def csrf_protect():
    """Every state-changing request must carry the session's secret token,
    either as a form field or as an X-CSRF-Token header (fetch calls)."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
        good = session.get("csrf_token", "")
        if not good or not secrets.compare_digest(sent.encode(), good.encode()):
            if request.path.startswith("/api/"):
                return jsonify(error="Security token missing or expired. "
                                     "Refresh the page and try again."), 400
            flash("Your session expired. Please try again.", "error")
            return redirect(back(url_for("index")))


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")          # no clickjacking
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def login_required(f):
    """Block a page route unless the user is logged in."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def api_login(f):
    """Same check for JSON routes: answer with JSON + 401, not a redirect."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify(error="Your session expired. Please log in again."), 401
        return f(*args, **kwargs)
    return wrapper


def get_project_or_404(pid):
    """Return the project only if the logged-in user is a member of it.
    Strangers get a 404, so they can't tell if the project exists."""
    row = get_db().execute(
        "SELECT p.* FROM projects p "
        "JOIN project_members m ON m.project_id = p.project_id "
        "WHERE p.project_id = ? AND m.user_id = ?",
        (pid, session["user_id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def owned_project(pid):
    """Return the project only if the logged-in user OWNS it, else None."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    return get_db().execute(
        "SELECT * FROM projects WHERE project_id = ? AND created_by = ?",
        (pid, session["user_id"]),
    ).fetchone()


def is_owner(project):
    return project["created_by"] == session["user_id"]


def get_task_or_404(tid):
    """Return the task (plus its project's owner id) only for project members."""
    row = get_db().execute(
        "SELECT t.*, p.created_by AS owner_id FROM tasks t "
        "JOIN projects p ON p.project_id = t.project_id "
        "JOIN project_members m ON m.project_id = t.project_id "
        "WHERE t.task_id = ? AND m.user_id = ?",
        (tid, session["user_id"]),
    ).fetchone()
    if row is None:
        abort(404)
    return row


def get_members(pid):
    return get_db().execute(
        "SELECT u.user_id, u.full_name, u.email, m.role_in_project, m.joined_at "
        "FROM project_members m JOIN users u ON u.user_id = m.user_id "
        "WHERE m.project_id = ? ORDER BY m.mapping_id", (pid,)
    ).fetchall()


def my_projects():
    """Short list (id + title) of projects the user belongs to."""
    return get_db().execute(
        "SELECT p.project_id, p.title FROM projects p "
        "JOIN project_members m ON m.project_id = p.project_id "
        "WHERE m.user_id = ? ORDER BY p.title", (session["user_id"],)
    ).fetchall()


def back(default):
    """Redirect target after a POST: the page the user came from,
    but only if it is on our own site."""
    ref = request.referrer or ""
    return ref if ref.startswith(request.host_url) else default


def check_project_form(form):
    """Validate the project form. Returns (clean_data, error_or_None)."""
    data = {
        "title": form.get("title", "").strip(),
        "description": form.get("description", "").strip(),
        "tech_stack": form.get("tech_stack", "").strip(),
        "start_date": form.get("start_date", "").strip(),
        "end_date": form.get("end_date", "").strip(),
    }
    err = None
    if len(data["title"]) < 3:
        err = "Project title must be at least 3 characters."
    elif len(data["title"]) > 100:
        err = "Project title must be under 100 characters."
    elif not data["tech_stack"]:
        err = "Please enter the technology stack (comma separated)."
    else:
        try:
            s = date.fromisoformat(data["start_date"])
            e = date.fromisoformat(data["end_date"])
        except ValueError:
            err = "Please choose a valid start date and deadline."
        else:
            if e < s:
                err = "Deadline cannot be before the start date."
    return data, err


def check_task_form(form, pid):
    """Validate the task form. Returns (clean_data, error_or_None)."""
    d = {
        "task_title": form.get("task_title", "").strip(),
        "task_desc": form.get("task_desc", "").strip(),
        "assigned_to": form.get("assigned_to", "").strip(),
        "priority": form.get("priority", ""),
        "status": form.get("status", "To-Do"),
        "est_days": form.get("est_days", "").strip(),
        "deadline": form.get("deadline", "").strip(),
    }
    err = None
    if len(d["task_title"]) < 3:
        err = "Task title must be at least 3 characters."
    elif len(d["task_title"]) > 120:
        err = "Task title must be under 120 characters."
    elif d["priority"] not in PRIORITIES:
        err = "Please choose a valid priority."
    elif d["status"] not in STATUSES:
        err = "Please choose a valid status."
    elif d["est_days"] and not (d["est_days"].isdigit()
                                and 1 <= int(d["est_days"]) <= 365):
        err = "Estimated days must be a whole number from 1 to 365."
    elif d["deadline"]:
        try:
            date.fromisoformat(d["deadline"])
        except ValueError:
            err = "Please enter a valid deadline."

    # the assignee must be a real member of THIS project (form can be tampered)
    if not err and d["assigned_to"]:
        ok = d["assigned_to"].isdigit() and get_db().execute(
            "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
            (pid, int(d["assigned_to"])),
        ).fetchone()
        if not ok:
            err = "You can only assign tasks to members of this project."
    return d, err


def task_values(d):
    """Convert validated form strings to DB values (empty -> NULL)."""
    return (
        d["task_title"], d["task_desc"],
        int(d["assigned_to"]) if d["assigned_to"] else None,
        d["priority"], d["status"],
        int(d["est_days"]) if d["est_days"] else None,
        d["deadline"] or None,
    )


def clean_ai_task(it):
    """Validate one AI-suggested task sent back by the browser.
    Returns ((title, desc, priority, est_days, deadline), error_or_None)."""
    if not isinstance(it, dict):
        return None, "Invalid task data."
    title = str(it.get("title", "")).strip()
    desc = str(it.get("description", "")).strip()[:1000]
    pri = it.get("priority")
    if not 3 <= len(title) <= 120:
        return None, "Each task needs a title of 3 to 120 characters."
    if pri not in PRIORITIES:
        return None, "Invalid priority."

    est = it.get("est_days")
    if est in (None, ""):
        est = None
    else:
        try:
            est = int(est)
        except (TypeError, ValueError):
            return None, "Estimated days must be a number."
        if not 1 <= est <= 365:
            return None, "Estimated days must be from 1 to 365."

    dl = it.get("deadline") or None
    if dl:
        try:
            date.fromisoformat(str(dl))
        except ValueError:
            return None, "Invalid deadline."
        dl = str(dl)
    return (title, desc, pri, est, dl), None


# ---------------------------------------------------------------
# Auth routes (Phase 1)
# ---------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        pw2 = request.form.get("confirm", "")
        role = request.form.get("role", "")

        err = None
        if len(name) < 2:
            err = "Please enter your full name."
        elif not EMAIL_RE.match(email):
            err = "Please enter a valid email."
        elif len(pw) < 6:
            err = "Password must be at least 6 characters."
        elif pw != pw2:
            err = "Passwords do not match."
        elif role not in ROLES:
            err = "Please choose a valid role."

        if err:
            flash(err, "error")
            return render_template("register.html", roles=ROLES,
                                   name=name, email=email, role=role)

        try:
            db = get_db()
            db.execute(
                "INSERT INTO users (full_name, email, password_hash, role) "
                "VALUES (?, ?, ?, ?)",
                (name, email, generate_password_hash(pw), role),
            )
            db.commit()
        except sqlite3.IntegrityError:
            flash("That email is already registered.", "error")
            return render_template("register.html", roles=ROLES,
                                   name=name, email=email, role=role)

        flash("Account created! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", roles=ROLES)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")

        user = get_db().execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], pw):
            flash("Invalid email or password.", "error")
            return render_template("login.html", email=email)

        session.clear()
        session["user_id"] = user["user_id"]
        session["name"] = user["full_name"]
        session["role"] = user["role"]
        flash(f"Welcome back, {user['full_name']}!", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


# ---------------------------------------------------------------
# Dashboard (Phase 5)
# ---------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    """Analytics for all the user's projects, or one project (?pid=)."""
    db = get_db()
    uid = session["user_id"]
    pid = request.args.get("pid", type=int)
    project = get_project_or_404(pid) if pid else None   # 404 for strangers

    today = date.today().isoformat()
    soon = (date.today() + timedelta(days=14)).isoformat()

    # Every query starts from this scope. The JOIN on project_members means a
    # task is only visible if the user belongs to its project.
    scope = (" FROM tasks t JOIN project_members m "
             "ON m.project_id = t.project_id AND m.user_id = ?")
    args = [uid]
    if pid:
        scope += " AND t.project_id = ?"
        args.append(pid)

    # --- headline numbers: ONE query (SUM of a comparison counts matches) ---
    st = db.execute(
        "SELECT COUNT(*) AS total, "
        " COALESCE(SUM(t.status = 'Completed'), 0)   AS done, "
        " COALESCE(SUM(t.status = 'In-Progress'), 0) AS doing, "
        " COALESCE(SUM(t.status = 'To-Do'), 0)       AS todo, "
        " COALESCE(SUM(t.status != 'Completed' AND t.deadline < ?), 0) AS late"
        + scope, [today] + args
    ).fetchone()

    # Overall completion (%) = completed / total * 100
    pct = round(st["done"] * 100 / st["total"]) if st["total"] else 0

    if pid:
        n_projects = 1
    else:
        n_projects = db.execute(
            "SELECT COUNT(*) FROM project_members WHERE user_id = ?", (uid,)
        ).fetchone()[0]

    # --- member contributions ---
    rows = db.execute(
        "SELECT t.assigned_to, u.full_name AS name, COUNT(*) AS assigned, "
        " SUM(t.status = 'Completed') AS done"
        + scope +
        " LEFT JOIN users u ON u.user_id = t.assigned_to "
        "GROUP BY t.assigned_to ORDER BY done DESC, assigned DESC", args
    ).fetchall()

    contrib = []
    for r in rows:
        if r["assigned_to"] is None and r["done"] == 0:
            continue                      # nothing to credit to "Unassigned"
        contrib.append({
            "name": r["name"] or "Unassigned",
            "assigned": r["assigned"] if r["assigned_to"] is not None else None,
            "done": r["done"],
            # Member Contribution (%) = completed by member / all completed * 100
            "pct": round(r["done"] * 100 / st["done"]) if st["done"] else 0,
        })

    # --- per-project progress (only in the "all projects" view) ---
    prog = []
    if not pid:
        prows = db.execute(
            "SELECT p.project_id, p.title, p.end_date, "
            " COUNT(t.task_id) AS total, "
            " COALESCE(SUM(t.status = 'Completed'), 0) AS done "
            "FROM projects p "
            "JOIN project_members m ON m.project_id = p.project_id AND m.user_id = ? "
            "LEFT JOIN tasks t ON t.project_id = p.project_id "
            "GROUP BY p.project_id ORDER BY p.title", (uid,)
        ).fetchall()
        prog = [dict(r, pct=round(r["done"] * 100 / r["total"]) if r["total"] else 0)
                for r in prows]

    # --- upcoming (next 14 days) and overdue task lists ---
    cols = ("SELECT t.task_id, t.project_id, t.task_title, t.deadline, "
            " p.title AS ptitle, u.full_name AS who, "
            " CAST(julianday(t.deadline) - julianday(?) AS INTEGER) AS days")
    joins = (" JOIN projects p ON p.project_id = t.project_id "
             "LEFT JOIN users u ON u.user_id = t.assigned_to ")

    upcoming = db.execute(
        cols + scope + joins +
        "WHERE t.status != 'Completed' AND t.deadline >= ? AND t.deadline <= ? "
        "ORDER BY t.deadline LIMIT 8", [today] + args + [today, soon]
    ).fetchall()

    overdue = db.execute(
        cols + scope + joins +
        "WHERE t.status != 'Completed' AND t.deadline < ? "
        "ORDER BY t.deadline LIMIT 8", [today] + args + [today]
    ).fetchall()

    return render_template(
        "dashboard.html", st=st, pct=pct, n_projects=n_projects,
        contrib=contrib, prog=prog, upcoming=upcoming, overdue=overdue,
        plist=my_projects(), p=project, pid=pid,
    )


# ---------------------------------------------------------------
# Project routes (Phase 2)
# ---------------------------------------------------------------
@app.route("/projects")
@login_required
def projects():
    """List every project the user owns or has joined, with progress."""
    rows = get_db().execute(
        "SELECT p.*, "
        " (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.project_id) AS total, "
        " (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.project_id "
        "    AND t.status = 'Completed') AS done, "
        " (SELECT COUNT(*) FROM project_members x WHERE x.project_id = p.project_id) AS members "
        "FROM projects p "
        "JOIN project_members m ON m.project_id = p.project_id "
        "WHERE m.user_id = ? ORDER BY p.project_id DESC",
        (session["user_id"],),
    ).fetchall()
    return render_template("projects.html", projects=rows)


@app.route("/projects/new", methods=["GET", "POST"])
@login_required
def create_project():
    if request.method == "POST":
        data, err = check_project_form(request.form)
        if err:
            flash(err, "error")
            return render_template("create_project.html", f=data, mode="create")

        db = get_db()
        cur = db.execute(
            "INSERT INTO projects (title, description, tech_stack, "
            "start_date, end_date, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (data["title"], data["description"], data["tech_stack"],
             data["start_date"], data["end_date"], session["user_id"]),
        )
        pid = cur.lastrowid
        db.execute(
            "INSERT INTO project_members (project_id, user_id, role_in_project) "
            "VALUES (?, ?, 'Owner')", (pid, session["user_id"]),
        )
        db.commit()   # one commit = both inserts succeed or neither does

        flash("Project created successfully!", "success")
        return redirect(url_for("project_details", pid=pid))

    return render_template("create_project.html", f={}, mode="create")


@app.route("/projects/<int:pid>")
@login_required
def project_details(pid):
    project = get_project_or_404(pid)
    db = get_db()
    members = get_members(pid)

    st = db.execute(
        "SELECT COUNT(*) AS total, "
        " COALESCE(SUM(status = 'Completed'), 0)   AS done, "
        " COALESCE(SUM(status = 'In-Progress'), 0) AS doing "
        "FROM tasks WHERE project_id = ?", (pid,)
    ).fetchone()

    # Project Progress (%) = completed / total * 100
    pct = round(st["done"] * 100 / st["total"]) if st["total"] else 0
    days_left = (date.fromisoformat(project["end_date"]) - date.today()).days

    return render_template(
        "project_details.html", p=project, members=members, st=st,
        pct=pct, days_left=days_left, is_owner=is_owner(project),
    )


@app.route("/projects/<int:pid>/edit", methods=["GET", "POST"])
@login_required
def edit_project(pid):
    project = get_project_or_404(pid)
    if not is_owner(project):
        abort(403)   # members can view, only the owner can edit

    if request.method == "POST":
        data, err = check_project_form(request.form)
        if err:
            flash(err, "error")
            return render_template("create_project.html", f=data,
                                   mode="edit", pid=pid)
        db = get_db()
        db.execute(
            "UPDATE projects SET title=?, description=?, tech_stack=?, "
            "start_date=?, end_date=? WHERE project_id=?",
            (data["title"], data["description"], data["tech_stack"],
             data["start_date"], data["end_date"], pid),
        )
        db.commit()
        flash("Project updated.", "success")
        return redirect(url_for("project_details", pid=pid))

    return render_template("create_project.html", f=project, mode="edit", pid=pid)


@app.route("/projects/<int:pid>/delete", methods=["POST"])
@login_required
def delete_project(pid):
    project = get_project_or_404(pid)
    if not is_owner(project):
        abort(403)
    db = get_db()
    db.execute("DELETE FROM projects WHERE project_id = ?", (pid,))
    db.commit()   # CASCADE removes members, tasks and AI suggestions
    flash("Project deleted.", "success")
    return redirect(url_for("projects"))


# ---------------------------------------------------------------
# Team routes (Phase 3)
# ---------------------------------------------------------------
@app.route("/team")
@login_required
def team():
    plist = my_projects()
    pid = request.args.get("pid", type=int)
    if pid is None and plist:
        pid = plist[0]["project_id"]          # default: first project

    project, members, counts = None, [], {}
    if pid is not None:
        project = get_project_or_404(pid)
        members = get_members(pid)
        rows = get_db().execute(
            "SELECT assigned_to, COUNT(*) AS total, "
            " SUM(status = 'Completed') AS done "
            "FROM tasks WHERE project_id = ? AND assigned_to IS NOT NULL "
            "GROUP BY assigned_to", (pid,)
        ).fetchall()
        counts = {r["assigned_to"]: r for r in rows}

    return render_template(
        "team.html", plist=plist, pid=pid, p=project, members=members,
        counts=counts, roles=PROJECT_ROLES,
        is_owner=bool(project and is_owner(project)),
    )


@app.route("/projects/<int:pid>/members/add", methods=["POST"])
@login_required
def add_member(pid):
    project = get_project_or_404(pid)
    if not is_owner(project):
        abort(403)

    email = request.form.get("email", "").strip().lower()
    role = request.form.get("role_in_project", "Member")
    if role not in PROJECT_ROLES:
        role = "Member"

    db = get_db()
    user = db.execute(
        "SELECT user_id, full_name FROM users WHERE email = ?", (email,)
    ).fetchone()

    if user is None:
        flash("No registered user with that email. Ask them to sign up first.", "error")
    else:
        try:
            db.execute(
                "INSERT INTO project_members (project_id, user_id, role_in_project) "
                "VALUES (?, ?, ?)", (pid, user["user_id"], role),
            )
            db.commit()
            flash(f"{user['full_name']} added to the project.", "success")
        except sqlite3.IntegrityError:    # UNIQUE (project_id, user_id)
            flash("That user is already in this project.", "error")
    return redirect(url_for("team", pid=pid))


@app.route("/projects/<int:pid>/members/<int:uid>/remove", methods=["POST"])
@login_required
def remove_member(pid, uid):
    project = get_project_or_404(pid)
    if not is_owner(project):
        abort(403)
    if uid == project["created_by"]:
        flash("The project owner cannot be removed.", "error")
        return redirect(url_for("team", pid=pid))

    db = get_db()
    # un-assign their tasks first, then remove them (one transaction)
    db.execute("UPDATE tasks SET assigned_to = NULL "
               "WHERE project_id = ? AND assigned_to = ?", (pid, uid))
    db.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
               (pid, uid))
    db.commit()
    flash("Member removed. Their tasks are now unassigned.", "success")
    return redirect(url_for("team", pid=pid))


# ---------------------------------------------------------------
# Task routes (Phase 3)
# ---------------------------------------------------------------
@app.route("/tasks")
@login_required
def tasks():
    """Task table with optional filters: project, status, my tasks only."""
    uid = session["user_id"]
    pid = request.args.get("pid", type=int)
    status = request.args.get("status", "")
    mine = request.args.get("mine") == "1"

    project = get_project_or_404(pid) if pid else None

    # fixed SQL text + optional clauses; values always go through "?"
    sql = ("SELECT t.*, p.title AS ptitle, p.created_by AS owner_id, "
           " u.full_name AS who "
           "FROM tasks t "
           "JOIN projects p ON p.project_id = t.project_id "
           "JOIN project_members m ON m.project_id = t.project_id AND m.user_id = ? "
           "LEFT JOIN users u ON u.user_id = t.assigned_to "
           "WHERE 1 = 1")
    args = [uid]
    if pid:
        sql += " AND t.project_id = ?"
        args.append(pid)
    if status in STATUSES:
        sql += " AND t.status = ?"
        args.append(status)
    if mine:
        sql += " AND t.assigned_to = ?"
        args.append(uid)
    sql += (" ORDER BY t.deadline IS NULL, t.deadline, "
            "CASE t.priority WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END")

    rows = get_db().execute(sql, args).fetchall()
    return render_template(
        "tasks.html", tasks=rows, plist=my_projects(), p=project, pid=pid,
        status=status, mine=mine, statuses=STATUSES,
        today=date.today().isoformat(),
    )


@app.route("/projects/<int:pid>/tasks/new", methods=["GET", "POST"])
@login_required
def create_task(pid):
    project = get_project_or_404(pid)
    if not is_owner(project):
        abort(403)
    members = get_members(pid)

    if request.method == "POST":
        d, err = check_task_form(request.form, pid)
        if err:
            flash(err, "error")
            return render_template("task_form.html", f=d, p=project,
                                   members=members, mode="create",
                                   priorities=PRIORITIES, statuses=STATUSES)
        db = get_db()
        db.execute(
            "INSERT INTO tasks (project_id, task_title, task_desc, assigned_to, "
            "priority, status, est_days, deadline) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pid,) + task_values(d),
        )
        db.commit()
        flash("Task created.", "success")
        return redirect(url_for("tasks", pid=pid))

    return render_template("task_form.html",
                           f={"priority": "Medium", "status": "To-Do"},
                           p=project, members=members, mode="create",
                           priorities=PRIORITIES, statuses=STATUSES)


@app.route("/tasks/<int:tid>/edit", methods=["GET", "POST"])
@login_required
def edit_task(tid):
    t = get_task_or_404(tid)
    if t["owner_id"] != session["user_id"]:
        abort(403)
    pid = t["project_id"]
    project = get_project_or_404(pid)
    members = get_members(pid)

    if request.method == "POST":
        d, err = check_task_form(request.form, pid)
        if err:
            flash(err, "error")
            return render_template("task_form.html", f=d, p=project,
                                   members=members, mode="edit", tid=tid,
                                   priorities=PRIORITIES, statuses=STATUSES)
        db = get_db()
        db.execute(
            "UPDATE tasks SET task_title=?, task_desc=?, assigned_to=?, "
            "priority=?, status=?, est_days=?, deadline=? WHERE task_id=?",
            task_values(d) + (tid,),
        )
        db.commit()
        flash("Task updated.", "success")
        return redirect(url_for("tasks", pid=pid))

    return render_template("task_form.html", f=t, p=project, members=members,
                           mode="edit", tid=tid,
                           priorities=PRIORITIES, statuses=STATUSES)


@app.route("/tasks/<int:tid>/delete", methods=["POST"])
@login_required
def delete_task(tid):
    t = get_task_or_404(tid)
    if t["owner_id"] != session["user_id"]:
        abort(403)
    db = get_db()
    db.execute("DELETE FROM tasks WHERE task_id = ?", (tid,))
    db.commit()
    flash("Task deleted.", "success")
    return redirect(back(url_for("tasks", pid=t["project_id"])))


@app.route("/tasks/<int:tid>/status", methods=["POST"])
@login_required
def task_status(tid):
    """Owner or the assigned member can change a task's status."""
    t = get_task_or_404(tid)
    uid = session["user_id"]
    if uid != t["owner_id"] and uid != t["assigned_to"]:
        abort(403)
    new = request.form.get("status", "")
    if new not in STATUSES:
        abort(400)
    db = get_db()
    db.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (new, tid))
    db.commit()
    flash("Status updated.", "success")
    return redirect(back(url_for("tasks", pid=t["project_id"])))


# ---------------------------------------------------------------
# AI routes (Phase 4)
# ---------------------------------------------------------------
@app.route("/ai-generator")
@login_required
def ai_generator():
    """Page with the generator form. Only projects the user OWNS are listed,
    because only owners can create tasks."""
    plist = get_db().execute(
        "SELECT * FROM projects WHERE created_by = ? ORDER BY title",
        (session["user_id"],),
    ).fetchall()
    return render_template(
        "ai_generator.html", plist=plist,
        pid=request.args.get("pid", type=int),
        key_ok=bool(os.getenv("GROQ_API_KEY", "").strip()),
    )


@app.route("/api/ai/generate", methods=["POST"])
@api_login
def ai_generate():
    """Ask Groq for task suggestions. Nothing is saved to the tasks table here."""
    data = request.get_json(silent=True) or {}
    project = owned_project(data.get("project_id"))
    if project is None:
        return jsonify(error="Project not found, or you are not its owner."), 404

    title = str(data.get("title", "")).strip()
    desc = str(data.get("description", "")).strip()[:1500]
    stack = str(data.get("tech_stack", "")).strip()[:300]
    if len(title) < 3:
        return jsonify(error="Please enter a project title (at least 3 characters)."), 400
    if not stack:
        return jsonify(error="Please enter the technology stack."), 400
    try:
        end = date.fromisoformat(str(data.get("deadline", "")))
    except ValueError:
        return jsonify(error="Please choose a valid deadline."), 400

    # plan from the later of: project start, today
    start = max(date.fromisoformat(project["start_date"]), date.today())
    if end <= start:
        return jsonify(error="The deadline must be after today and after the project start date."), 400

    try:
        suggestions, prompt, raw = ai_service.generate_tasks(title, desc, stack, start, end)
    except ai_service.AIError as e:
        return jsonify(error=str(e),
                       manual_url=url_for("create_task", pid=project["project_id"])), 502

    db = get_db()
    cur = db.execute(
        "INSERT INTO ai_suggestions (project_id, prompt_text, ai_response_raw) "
        "VALUES (?, ?, ?)", (project["project_id"], prompt, raw),
    )
    db.commit()
    return jsonify(tasks=suggestions, suggestion_id=cur.lastrowid)


@app.route("/api/ai/save", methods=["POST"])
@api_login
def ai_save():
    """Save ONLY the suggestions the user accepted (unassigned, status To-Do)."""
    data = request.get_json(silent=True) or {}
    project = owned_project(data.get("project_id"))
    if project is None:
        return jsonify(error="Project not found, or you are not its owner."), 404

    items = data.get("tasks")
    if not isinstance(items, list) or not 1 <= len(items) <= 20:
        return jsonify(error="Choose between 1 and 20 tasks to save."), 400

    rows = []
    for it in items:                       # re-validate everything server-side
        vals, err = clean_ai_task(it)
        if err:
            return jsonify(error=err), 400
        rows.append((project["project_id"],) + vals)

    db = get_db()
    db.executemany(
        "INSERT INTO tasks (project_id, task_title, task_desc, priority, "
        "est_days, deadline, is_ai_gen) VALUES (?, ?, ?, ?, ?, ?, 1)", rows,
    )
    db.commit()
    flash(f"{len(rows)} AI-suggested task(s) saved. Assign them to your team below.",
          "success")
    return jsonify(saved=len(rows),
                   redirect=url_for("tasks", pid=project["project_id"]))


# ---------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------
@app.errorhandler(400)
def bad_request(e):
    return render_template("error.html", code=400,
                           msg="That request was not valid."), 400


@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403,
                           msg="You don't have permission to do that."), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404,
                           msg="That page or project was not found."), 404


@app.errorhandler(405)
def not_allowed(e):
    return render_template("error.html", code=405,
                           msg="That action is not allowed here."), 405


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500,
                           msg="Something went wrong on our side. Please try again."), 500


if __name__ == "__main__":
    app.run(debug=True)   # debug=True is for development only