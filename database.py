"""Database helpers: connection handling and table creation."""
import os
import sqlite3
from flask import g

BASE = os.path.dirname(os.path.abspath(__file__))
# AISPMS_DB lets the automated tests use a temporary database file
DB_PATH = os.getenv("AISPMS_DB") or os.path.join(BASE, "instance", "ai_spms.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL
        CHECK (role IN ('Team Leader', 'Team Member', 'Mentor'))
);

CREATE TABLE IF NOT EXISTS projects (
    project_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT,
    tech_stack  TEXT,
    start_date  TEXT,
    end_date    TEXT,
    created_by  INTEGER NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS project_members (
    mapping_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    role_in_project TEXT NOT NULL DEFAULT 'Team Member',
    joined_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, user_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(user_id)       ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    task_title TEXT NOT NULL,
    task_desc  TEXT,
    assigned_to INTEGER,
    priority   TEXT NOT NULL DEFAULT 'Medium'
        CHECK (priority IN ('Low', 'Medium', 'High')),
    status     TEXT NOT NULL DEFAULT 'To-Do'
        CHECK (status IN ('To-Do', 'In-Progress', 'Completed')),
    is_ai_gen  INTEGER NOT NULL DEFAULT 0,
    est_days   INTEGER,
    deadline   TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id)  REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_to) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ai_suggestions (
    suggestion_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER,
    prompt_text     TEXT NOT NULL,
    ai_response_raw TEXT,
    generated_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
"""


def get_db():
    """Return one DB connection per request (stored in Flask's g)."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row          # access columns by name
        g.db.execute("PRAGMA foreign_keys = ON")  # enforce relationships
    return g.db


def close_db(e=None):
    """Close the connection when the request ends."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create the instance folder and all tables (safe to run repeatedly)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.commit()
    db.close()