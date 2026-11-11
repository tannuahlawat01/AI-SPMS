# AI-Based Student Project Management System (AI-SPMS)

A web application that helps college students manage team projects: create projects,
build a team, assign tasks with deadlines and priorities, track progress on a dashboard,
and generate an ordered task plan with AI (Groq).

Built as a Software Engineering academic project using the Agile approach (6 phases).

## Features

| Module | What it does |
|---|---|
| Authentication | Register / login / logout, hashed passwords, session-based auth, roles (Team Leader, Team Member, Mentor) |
| Project management | Create, view, edit, delete projects; tech stack; start date and deadline |
| Team and tasks | Add members by email; assign tasks; priority; status (To-Do / In-Progress / Completed); filters; "my tasks" |
| AI task suggestions | Groq generates 5-15 ordered tasks with priority, estimate and deadlines; accept / edit / reject; nothing saved without approval |
| Dashboard | Totals, status chart, overall completion, member contributions, upcoming and overdue deadlines |

## Tech stack

- Frontend: HTML5, CSS3, vanilla JavaScript
- Backend: Python 3, Flask
- Database: SQLite
- AI: Groq API (called only from the Flask backend)

## Project structure

```
AI-SPMS/
├── app.py              # routes + business logic
├── database.py         # schema + connection helpers
├── ai_service.py       # Groq prompt, API call, validation, deadline scheduling
├── test_app.py         # automated tests (unittest)
├── requirements.txt
├── .env.example
├── templates/          # Jinja2 pages (base.html is the shared layout)
├── static/
│   ├── css/            # style.css, ai.css, dashboard.css
│   └── js/             # script.js, ai.js, dashboard.js
└── instance/ai_spms.db # created automatically
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows   (Mac/Linux: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env           # Mac/Linux: cp .env.example .env
```

Edit `.env`:

```
SECRET_KEY=a-long-random-string
GROQ_API_KEY=gsk_your_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

Get a free Groq key at https://console.groq.com. Without a key the app still works;
the AI page shows an error and you can add tasks manually.

## Run

```bash
python app.py
```
Open http://127.0.0.1:5000

## Run the tests

```bash
python -m unittest -v
```
Tests use a temporary database and mock the AI, so they need no internet or API key.

## Roles and permissions

| Action | Owner | Assigned member | Other member |
|---|---|---|---|
| View project, team, tasks | yes | yes | yes |
| Edit / delete project | yes | no | no |
| Add / remove members | yes | no | no |
| Create / edit / delete tasks, use AI generator | yes | no | no |
| Change task status | yes | own tasks | no |

Non-members get a 404 (they cannot tell whether a project exists).

## Database (5 tables)

- `users` (user_id PK, full_name, email UNIQUE, password_hash, role)
- `projects` (project_id PK, title, description, tech_stack, start_date, end_date, created_by FK -> users)
- `project_members` (mapping_id PK, project_id FK, user_id FK, role_in_project, joined_at; UNIQUE project+user)
- `tasks` (task_id PK, project_id FK, task_title, task_desc, assigned_to FK, priority, status, is_ai_gen, est_days, deadline, created_at)
- `ai_suggestions` (suggestion_id PK, project_id FK, prompt_text, ai_response_raw, generated_at)

Relationships: a user creates many projects; users and projects are many-to-many through
`project_members`; a project has many tasks; a task is assigned to at most one user.
Deleting a project cascades to its members, tasks and AI suggestions.

## Formulas

- Project Progress (%) = Completed Tasks / Total Tasks x 100
- Member Contribution (%) = Tasks Completed by Member / Total Completed Tasks x 100

## Security measures

- Passwords hashed with Werkzeug (salted)
- Parameterized SQL everywhere (no SQL injection)
- Server-side authorization on every route (not only hidden buttons)
- CSRF token on every POST and fetch call
- Jinja auto-escaping and `textContent` in JavaScript (no XSS)
- API key only on the server (`.env`, never in frontend code, never committed)
- HttpOnly + SameSite session cookies; X-Frame-Options, nosniff, Referrer-Policy headers
- All AI output is validated before use; AI suggestions are saved only after user approval

## Development phases (Agile)

1. Setup, database, authentication
2. Project CRUD and authorization
3. Team management and task assignment
4. AI task generation and timeline suggestions
5. Progress dashboard and analytics
6. Testing, security hardening, documentation

## Known limitations and future work

- SQLite and the Flask dev server suit a college project, not production (use PostgreSQL + gunicorn)
- No email invitations or password reset
- Dates use the server's timezone
- Task deadlines are not forced to fall inside the project window
- Possible extensions: comments on tasks, file uploads, notifications, Gantt chart