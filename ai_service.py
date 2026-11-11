"""Groq AI service: builds the prompt, calls the API, validates the reply
and schedules deadlines. The API key never leaves the server."""
import json
import os
from datetime import timedelta

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
PRIORITIES = ("Low", "Medium", "High")


class AIError(Exception):
    """An error whose message is safe to show to the user."""


def build_prompt(title, desc, stack, start, end):
    return (
        "You are a project planning assistant for college students.\n"
        "Break the project below into 5 to 15 small, concrete tasks, in the "
        "order they should be done (setup first, testing and documentation last).\n\n"
        f"Project title: {title}\n"
        f"Description: {desc or 'Not provided'}\n"
        f"Technology stack: {stack}\n"
        f"Start date: {start}\n"
        f"Deadline: {end}\n\n"
        "Reply with JSON only, in exactly this shape:\n"
        '{"tasks": [{"title": "...", "description": "one or two sentences", '
        '"priority": "Low or Medium or High", "est_days": 2}]}\n'
        "Rules: est_days is a whole number of days from 1 to 30. "
        "Keep each title under 80 characters."
    )


def ask_groq(prompt):
    """Send the prompt to Groq and return the raw text of the reply."""
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key:
        raise AIError("AI is not configured: GROQ_API_KEY is missing in your .env file.")

    body = {
        "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "messages": [
            {"role": "system", "content": "You output only valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},   # JSON mode
    }
    try:
        r = requests.post(GROQ_URL, json=body, timeout=30,
                          headers={"Authorization": f"Bearer {key}"})
    except requests.exceptions.Timeout:
        raise AIError("The AI took too long to respond. Please try again.")
    except requests.exceptions.RequestException:
        raise AIError("Could not reach the AI service. Check your internet connection.")

    if r.status_code == 401:
        raise AIError("Groq rejected the API key. Check GROQ_API_KEY in your .env file.")
    if r.status_code == 429:
        raise AIError("Groq rate limit reached. Wait a minute and try again.")
    if r.status_code != 200:
        raise AIError(f"The AI service returned an error (HTTP {r.status_code}). "
                      "If it mentions the model, check GROQ_MODEL in your .env file.")
    try:
        return r.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        raise AIError("The AI sent an unexpected response. Please try again.")


def parse_tasks(raw):
    """Turn the AI's JSON text into a clean list of task dicts."""
    try:
        items = json.loads(raw)["tasks"]
    except (ValueError, KeyError, TypeError):
        raise AIError("The AI reply was not in the expected format. Please try again.")
    if not isinstance(items, list):
        raise AIError("The AI reply was not in the expected format. Please try again.")

    out = []
    for it in items[:15]:                       # at most 15 tasks
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "")).strip()[:120]
        if len(title) < 3:
            continue
        pri = str(it.get("priority", "Medium")).strip().capitalize()
        if pri not in PRIORITIES:
            pri = "Medium"
        try:
            est = int(it.get("est_days", 2))
        except (TypeError, ValueError):
            est = 2
        est = min(max(est, 1), 30)
        out.append({
            "title": title,
            "description": str(it.get("description", "")).strip()[:500],
            "priority": pri,
            "est_days": est,
        })
    if len(out) < 3:
        raise AIError("The AI returned too few usable tasks. Please try again.")
    return out


def schedule(tasks, start, end):
    """Give each task a deadline. Tasks run in order, and each one gets a share
    of the window (start -> end) proportional to its est_days.
    start and end are date objects; the caller guarantees end > start."""
    total = sum(t["est_days"] for t in tasks)
    span = (end - start).days
    done = 0
    for t in tasks:
        done += t["est_days"]
        offset = min(max(1, round(done * span / total)), span)
        t["deadline"] = (start + timedelta(days=offset)).isoformat()


def generate_tasks(title, desc, stack, start, end):
    """Full pipeline. Returns (tasks, prompt, raw_reply)."""
    prompt = build_prompt(title, desc, stack, start.isoformat(), end.isoformat())
    raw = ask_groq(prompt)
    tasks = parse_tasks(raw)
    schedule(tasks, start, end)
    return tasks, prompt, raw