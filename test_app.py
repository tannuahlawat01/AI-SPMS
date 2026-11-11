"""Automated tests for AI-SPMS.   Run:  python -m unittest -v
Uses a throwaway database, so your real data is never touched."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

# --- must happen BEFORE importing the app: point it at a temp database ---
_fd, TMP_DB = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["AISPMS_DB"] = TMP_DB
os.environ["GROQ_API_KEY"] = ""          # tests never call the real AI

import ai_service                         # noqa: E402
import app as spms                        # noqa: E402


def tearDownModule():
    try:
        os.remove(TMP_DB)
    except OSError:
        pass


# ---------------------------------------------------------------
# helpers
# ---------------------------------------------------------------
def D(n):
    """ISO date n days from today."""
    return (date.today() + timedelta(days=n)).isoformat()


def db(sql, args=()):
    c = sqlite3.connect(TMP_DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(sql, args).fetchall()
    c.commit()
    c.close()
    return rows


def token(c):
    with c.session_transaction() as s:
        return s.setdefault("csrf_token", "test-token")


def post(c, url, data=None, **kw):
    d = dict(data or {})
    d["csrf_token"] = token(c)
    return c.post(url, data=d, **kw)


def post_json(c, url, payload):
    return c.post(url, json=payload, headers={"X-CSRF-Token": token(c)})


def uid(email):
    return db("SELECT user_id FROM users WHERE email = ?", (email,))[0]["user_id"]


def signup(c, name, email, role="Team Leader"):
    """Register + log in. Returns the new user's id."""
    post(c, "/register", {"full_name": name, "email": email, "password": "secret1",
                          "confirm": "secret1", "role": role})
    r = post(c, "/login", {"email": email, "password": "secret1"})
    assert r.status_code == 302, "login failed in test helper"
    return uid(email)


def new_project(c, title="Demo Project", start=0, end=20):
    r = post(c, "/projects/new", {"title": title, "description": "demo",
                                  "tech_stack": "Python", "start_date": D(start),
                                  "end_date": D(end)})
    assert r.status_code == 302, r.data
    return db("SELECT MAX(project_id) AS m FROM projects")[0]["m"]


def add_member(c, pid, email):
    return post(c, f"/projects/{pid}/members/add",
                {"email": email, "role_in_project": "Member"})


def add_task(pid, title, status="To-Do", assigned=None, deadline=None):
    db("INSERT INTO tasks (project_id, task_title, status, assigned_to, deadline) "
       "VALUES (?, ?, ?, ?, ?)", (pid, title, status, assigned, deadline))
    return db("SELECT MAX(task_id) AS m FROM tasks")[0]["m"]


class Base(unittest.TestCase):
    def setUp(self):
        for t in ("tasks", "ai_suggestions", "project_members", "projects", "users"):
            db(f"DELETE FROM {t}")
        self.c = spms.app.test_client()          # the "owner" browser
        self.c2 = spms.app.test_client()         # a second browser


# ---------------------------------------------------------------
# Module 1: authentication
# ---------------------------------------------------------------
class AuthTests(Base):
    def test_register_hashes_password(self):
        signup(self.c, "Ann", "ann@test.com")
        h = db("SELECT password_hash FROM users")[0]["password_hash"]
        self.assertNotEqual(h, "secret1")
        self.assertTrue(h.startswith(("scrypt:", "pbkdf2:")))

    def test_duplicate_email_rejected(self):
        signup(self.c, "Ann", "ann@test.com")
        r = post(self.c2, "/register", {"full_name": "Ann2", "email": "ann@test.com",
                 "password": "secret1", "confirm": "secret1", "role": "Mentor"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"already registered", r.data)

    def test_short_password_rejected(self):
        r = post(self.c, "/register", {"full_name": "Ann", "email": "a@test.com",
                 "password": "123", "confirm": "123", "role": "Mentor"})
        self.assertIn(b"at least 6", r.data)
        self.assertEqual(len(db("SELECT * FROM users")), 0)

    def test_wrong_password(self):
        signup(self.c, "Ann", "ann@test.com")
        r = post(self.c2, "/login", {"email": "ann@test.com", "password": "nope"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Invalid email or password", r.data)

    def test_sql_injection_login_fails(self):
        signup(self.c, "Ann", "ann@test.com")
        r = post(self.c2, "/login", {"email": "' OR '1'='1", "password": "x"})
        self.assertIn(b"Invalid email or password", r.data)

    def test_protected_page_redirects_to_login(self):
        r = self.c.get("/dashboard")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_logout_is_post_and_ends_session(self):
        signup(self.c, "Ann", "ann@test.com")
        self.assertEqual(self.c.get("/logout").status_code, 405)    # GET not allowed
        self.assertEqual(post(self.c, "/logout").status_code, 302)
        self.assertEqual(self.c.get("/dashboard").status_code, 302)


# ---------------------------------------------------------------
# Module 2: projects + authorization
# ---------------------------------------------------------------
class ProjectTests(Base):
    def test_create_project_adds_owner_member(self):
        a = signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        m = db("SELECT user_id, role_in_project FROM project_members WHERE project_id=?", (pid,))
        self.assertEqual((m[0]["user_id"], m[0]["role_in_project"]), (a, "Owner"))

    def test_deadline_before_start_rejected(self):
        signup(self.c, "Ann", "ann@test.com")
        r = post(self.c, "/projects/new", {"title": "Bad", "tech_stack": "Py",
                 "start_date": D(5), "end_date": D(1)})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"before the start", r.data)
        self.assertEqual(len(db("SELECT * FROM projects")), 0)

    def test_outsider_gets_404(self):
        signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        signup(self.c2, "Bob", "bob@test.com")
        self.assertEqual(self.c2.get(f"/projects/{pid}").status_code, 404)

    def test_member_can_view_but_not_edit_or_delete(self):
        signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        signup(self.c2, "Bob", "bob@test.com", "Team Member")
        add_member(self.c, pid, "bob@test.com")
        self.assertEqual(self.c2.get(f"/projects/{pid}").status_code, 200)
        self.assertEqual(self.c2.get(f"/projects/{pid}/edit").status_code, 403)
        self.assertEqual(post(self.c2, f"/projects/{pid}/delete").status_code, 403)
        self.assertEqual(len(db("SELECT * FROM projects")), 1)

    def test_delete_cascades(self):
        signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        add_task(pid, "T1")
        post(self.c, f"/projects/{pid}/delete")
        self.assertEqual(len(db("SELECT * FROM projects")), 0)
        self.assertEqual(len(db("SELECT * FROM tasks")), 0)
        self.assertEqual(len(db("SELECT * FROM project_members")), 0)

    def test_html_is_escaped(self):
        signup(self.c, "Ann", "ann@test.com")
        new_project(self.c, title="<script>alert(1)</script>")
        r = self.c.get("/projects")
        self.assertIn(b"&lt;script&gt;", r.data)
        self.assertNotIn(b"<script>alert(1)", r.data)


# ---------------------------------------------------------------
# Module 4 + team: assignment and permissions
# ---------------------------------------------------------------
class TaskTests(Base):
    def setUp(self):
        super().setUp()
        self.a = signup(self.c, "Ann", "ann@test.com")
        self.pid = new_project(self.c)
        self.b = signup(self.c2, "Bob", "bob@test.com", "Team Member")
        add_member(self.c, self.pid, "bob@test.com")

    def test_unknown_email_cannot_be_added(self):
        r = add_member(self.c, self.pid, "ghost@test.com")
        r = self.c.get(r.headers["Location"])
        self.assertIn(b"No registered user", r.data)

    def test_duplicate_member_rejected(self):
        r = add_member(self.c, self.pid, "bob@test.com")
        r = self.c.get(r.headers["Location"])
        self.assertIn(b"already in this project", r.data)

    def test_owner_creates_task(self):
        r = post(self.c, f"/projects/{self.pid}/tasks/new", {
            "task_title": "Design DB", "priority": "High", "status": "To-Do",
            "assigned_to": str(self.b), "est_days": "3", "deadline": D(5)})
        self.assertEqual(r.status_code, 302)
        t = db("SELECT * FROM tasks")[0]
        self.assertEqual((t["task_title"], t["assigned_to"], t["est_days"]),
                         ("Design DB", self.b, 3))

    def test_member_cannot_create_task(self):
        self.assertEqual(self.c2.get(f"/projects/{self.pid}/tasks/new").status_code, 403)

    def test_cannot_assign_to_outsider(self):
        signup(spms.app.test_client(), "Cat", "cat@test.com")
        outsider = uid("cat@test.com")
        r = post(self.c, f"/projects/{self.pid}/tasks/new", {
            "task_title": "Sneaky", "priority": "Low", "status": "To-Do",
            "assigned_to": str(outsider)})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"only assign tasks to members", r.data)
        self.assertEqual(len(db("SELECT * FROM tasks")), 0)

    def test_task_validation(self):
        r = post(self.c, f"/projects/{self.pid}/tasks/new", {
            "task_title": "ab", "priority": "Low", "status": "To-Do"})
        self.assertIn(b"at least 3", r.data)
        r = post(self.c, f"/projects/{self.pid}/tasks/new", {
            "task_title": "Good title", "priority": "Low", "status": "To-Do",
            "est_days": "0"})
        self.assertIn(b"Estimated days", r.data)

    def test_status_permissions(self):
        tid = add_task(self.pid, "Mine", assigned=self.b)
        other = add_task(self.pid, "Not mine", assigned=self.a)
        r = post(self.c2, f"/tasks/{tid}/status", {"status": "Completed"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(db("SELECT status FROM tasks WHERE task_id=?", (tid,))[0]["status"],
                         "Completed")
        # Bob may NOT change a task assigned to Ann
        self.assertEqual(post(self.c2, f"/tasks/{other}/status",
                              {"status": "Completed"}).status_code, 403)
        # invalid status value
        self.assertEqual(post(self.c, f"/tasks/{tid}/status",
                              {"status": "Hacked"}).status_code, 400)

    def test_member_cannot_delete_task(self):
        tid = add_task(self.pid, "T", assigned=self.b)
        self.assertEqual(post(self.c2, f"/tasks/{tid}/delete").status_code, 403)
        self.assertEqual(len(db("SELECT * FROM tasks")), 1)

    def test_remove_member_unassigns_tasks(self):
        tid = add_task(self.pid, "T", assigned=self.b)
        post(self.c, f"/projects/{self.pid}/members/{self.b}/remove")
        self.assertIsNone(db("SELECT assigned_to FROM tasks WHERE task_id=?", (tid,))[0]["assigned_to"])
        self.assertEqual(len(db("SELECT * FROM project_members WHERE user_id=?", (self.b,))), 0)

    def test_outsider_cannot_see_tasks(self):
        tid = add_task(self.pid, "T")
        c3 = spms.app.test_client()
        signup(c3, "Cat", "cat@test.com")
        self.assertEqual(c3.get(f"/tasks?pid={self.pid}").status_code, 404)
        self.assertEqual(c3.get(f"/tasks/{tid}/edit").status_code, 404)


# ---------------------------------------------------------------
# Module 5: dashboard math
# ---------------------------------------------------------------
class DashboardTests(Base):
    def test_numbers_and_formulas(self):
        a = signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        b = signup(self.c2, "Bob", "bob@test.com", "Team Member")
        add_member(self.c, pid, "bob@test.com")
        for i in range(3):
            add_task(pid, f"A done {i}", "Completed", a)
        add_task(pid, "B done", "Completed", b)
        add_task(pid, "Fix bug", "To-Do", None, D(-2))            # overdue
        add_task(pid, "Write report", "In-Progress", b, D(3))     # upcoming
        # total 6, completed 4 -> 67% ; Ann 3/4 = 75% ; Bob 1/4 = 25%
        r = self.c.get("/dashboard")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'class="num">6</div>', r.data)       # total
        self.assertIn(b'class="num ok">4</div>', r.data)    # completed
        self.assertIn(b'class="num warn">1</div>', r.data)  # in progress
        self.assertIn(b'class="num bad">1</div>', r.data)   # overdue
        self.assertIn(b"67%", r.data)
        self.assertIn(b"75%", r.data)
        self.assertIn(b"25%", r.data)
        self.assertIn(b"2 day(s) overdue", r.data)
        self.assertIn(b"in 3 days", r.data)

    def test_project_filter(self):
        signup(self.c, "Ann", "ann@test.com")
        p1 = new_project(self.c, "One")
        p2 = new_project(self.c, "Two")
        add_task(p1, "x", "Completed")
        add_task(p2, "y")
        r = self.c.get(f"/dashboard?pid={p1}")
        self.assertIn(b'class="num ok">1</div>', r.data)
        self.assertIn(b'class="num">1</div>', r.data)       # total for project One only

    def test_new_user_sees_empty_state(self):
        signup(self.c, "Ann", "ann@test.com")
        self.assertIn(b"set up your first project", self.c.get("/dashboard").data)

    def test_dashboard_isolation(self):
        signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        add_task(pid, "secret task")
        signup(self.c2, "Bob", "bob@test.com")
        r = self.c2.get("/dashboard")
        self.assertNotIn(b"secret task", r.data)
        self.assertEqual(self.c2.get(f"/dashboard?pid={pid}").status_code, 404)


# ---------------------------------------------------------------
# Module 3: AI (Groq is mocked, no internet needed)
# ---------------------------------------------------------------
FAKE = json.dumps({"tasks": [
    {"title": f"Task {i}", "description": "d", "priority": "high", "est_days": i + 1}
    for i in range(5)
]})


class AIUnitTests(unittest.TestCase):
    def test_schedule_last_task_on_deadline_and_ordered(self):
        t = [{"est_days": d} for d in (2, 3, 5)]
        ai_service.schedule(t, date(2026, 1, 1), date(2026, 1, 31))
        dl = [x["deadline"] for x in t]
        self.assertEqual(dl, sorted(dl))
        self.assertEqual(dl[-1], "2026-01-31")

    def test_schedule_tight_window_never_passes_deadline(self):
        t = [{"est_days": 5} for _ in range(6)]
        ai_service.schedule(t, date(2026, 1, 1), date(2026, 1, 2))
        self.assertTrue(all(x["deadline"] <= "2026-01-02" for x in t))

    def test_parse_cleans_bad_data(self):
        raw = json.dumps({"tasks": [
            {"title": "Setup", "priority": "urgent", "est_days": 999},
            {"title": "Build", "priority": "LOW", "est_days": "x"},
            {"title": "Test", "priority": "Medium", "est_days": 3},
            {"description": "no title at all"},
        ]})
        out = ai_service.parse_tasks(raw)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0]["priority"], "Medium")   # unknown -> Medium
        self.assertEqual(out[0]["est_days"], 30)         # clamped
        self.assertEqual(out[1]["priority"], "Low")
        self.assertEqual(out[1]["est_days"], 2)          # bad number -> default

    def test_parse_rejects_garbage(self):
        with self.assertRaises(ai_service.AIError):
            ai_service.parse_tasks("this is not json")
        with self.assertRaises(ai_service.AIError):
            ai_service.parse_tasks(json.dumps({"tasks": [{"title": "Only one"}]}))


class AIApiTests(Base):
    def setUp(self):
        super().setUp()
        signup(self.c, "Ann", "ann@test.com")
        self.pid = new_project(self.c, start=0, end=20)
        self.payload = {"project_id": self.pid, "title": "Demo", "description": "x",
                        "tech_stack": "Python", "deadline": D(20)}

    def test_generate_does_not_save_tasks(self):
        with mock.patch("ai_service.ask_groq", return_value=FAKE):
            r = post_json(self.c, "/api/ai/generate", self.payload)
        self.assertEqual(r.status_code, 200)
        tasks = r.get_json()["tasks"]
        self.assertEqual(len(tasks), 5)
        dl = [t["deadline"] for t in tasks]
        self.assertEqual(dl, sorted(dl))
        self.assertEqual(dl[-1], D(20))
        self.assertEqual(tasks[0]["priority"], "High")           # "high" normalised
        self.assertEqual(len(db("SELECT * FROM tasks")), 0)      # nothing saved yet
        self.assertEqual(len(db("SELECT * FROM ai_suggestions")), 1)   # but logged

    def test_generate_without_key_gives_fallback(self):
        r = post_json(self.c, "/api/ai/generate", self.payload)   # key is empty
        self.assertEqual(r.status_code, 502)
        self.assertIn("manual_url", r.get_json())

    def test_generate_past_deadline_rejected(self):
        self.payload["deadline"] = D(-1)
        r = post_json(self.c, "/api/ai/generate", self.payload)
        self.assertEqual(r.status_code, 400)

    def test_save_creates_unassigned_ai_tasks(self):
        r = post_json(self.c, "/api/ai/save", {"project_id": self.pid, "tasks": [
            {"title": "Setup repo", "description": "x", "priority": "High",
             "est_days": 2, "deadline": D(3)}]})
        self.assertEqual(r.status_code, 200)
        t = db("SELECT * FROM tasks")[0]
        self.assertEqual((t["is_ai_gen"], t["status"]), (1, "To-Do"))
        self.assertIsNone(t["assigned_to"])

    def test_save_rejects_invalid_priority(self):
        r = post_json(self.c, "/api/ai/save", {"project_id": self.pid, "tasks": [
            {"title": "Setup repo", "priority": "Urgent"}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(db("SELECT * FROM tasks")), 0)

    def test_non_owner_cannot_use_ai(self):
        signup(self.c2, "Bob", "bob@test.com", "Team Member")
        add_member(self.c, self.pid, "bob@test.com")
        r = post_json(self.c2, "/api/ai/save", {"project_id": self.pid, "tasks": [
            {"title": "Setup repo", "priority": "High"}]})
        self.assertEqual(r.status_code, 404)

    def test_api_requires_login(self):
        r = self.c2.post("/api/ai/save", json={}, headers={"X-CSRF-Token": "x"})
        self.assertIn(r.status_code, (400, 401))


# ---------------------------------------------------------------
# Phase 6: CSRF + headers
# ---------------------------------------------------------------
class SecurityTests(Base):
    def test_form_post_without_token_is_rejected(self):
        signup(self.c, "Ann", "ann@test.com")
        r = self.c.post("/projects/new", data={"title": "Hack", "tech_stack": "Py",
                        "start_date": D(0), "end_date": D(5)})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db("SELECT * FROM projects")), 0)

    def test_form_post_with_wrong_token_is_rejected(self):
        signup(self.c, "Ann", "ann@test.com")
        r = self.c.post("/projects/new", data={"title": "Hack", "tech_stack": "Py",
                        "start_date": D(0), "end_date": D(5), "csrf_token": "forged"})
        self.assertEqual(len(db("SELECT * FROM projects")), 0)

    def test_json_post_without_token_is_rejected(self):
        signup(self.c, "Ann", "ann@test.com")
        pid = new_project(self.c)
        r = self.c.post("/api/ai/save", json={"project_id": pid, "tasks": [
            {"title": "Setup repo", "priority": "High"}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(db("SELECT * FROM tasks")), 0)

    def test_security_headers_present(self):
        r = self.c.get("/")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")

    def test_unknown_url_gives_friendly_404(self):
        r = self.c.get("/does-not-exist")
        self.assertEqual(r.status_code, 404)
        self.assertIn(b"not found", r.data)


if __name__ == "__main__":
    unittest.main()