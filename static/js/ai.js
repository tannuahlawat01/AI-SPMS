// AI Task Generator page.
// All text is inserted with textContent (never innerHTML), so AI output
// or user input can never inject HTML/scripts into the page (XSS-safe).
(function () {
  const $ = function (id) { return document.getElementById(id); };
  const form = $("ai-form");
  if (!form) return;

  const sel = $("ai-project"), title = $("ai-title"), desc = $("ai-desc"),
        stack = $("ai-stack"), end = $("ai-end"), go = $("ai-go"),
        err = $("ai-error"), box = $("ai-results"), list = $("ai-list"),
        saveBtn = $("ai-save"), allBtn = $("ai-accept-all"), count = $("ai-count");

  let items = [];      // the suggestions: {title, description, priority, est_days, deadline, state, editing}
  let pid = null;      // project the suggestions belong to (locked at generation)

  // ---------- small helpers ----------
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function btn(label, cls, fn) {
    const b = el("button", "btn btn-sm " + cls, label);
    b.type = "button";
    b.addEventListener("click", fn);
    return b;
  }
  function field(label, input) {
    const l = el("label", null, label);
    l.appendChild(input);
    return l;
  }
  function showError(msg, link) {
    err.textContent = msg;
    if (link) {
      const a = el("a", null, "Add tasks manually →");
      a.href = link;
      err.appendChild(a);
    }
    err.hidden = false;
  }
  function hideError() { err.hidden = true; err.textContent = ""; }

  async function post(url, body) {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    let data = {};
    try { data = await r.json(); } catch (e) { data = { error: "Unexpected server response." }; }
    return { ok: r.ok, data: data };
  }

  // ---------- prefill the form from the chosen project ----------
  function fill() {
    const o = sel.options[sel.selectedIndex];
    if (!o || !o.value) return;
    title.value = o.dataset.title;
    desc.value = o.dataset.desc;
    stack.value = o.dataset.stack;
    end.value = o.dataset.end;
  }
  sel.addEventListener("change", fill);
  fill();   // handles ?pid= preselection

  // ---------- rendering ----------
  function viewBox(t) {
    const d = el("div");
    d.appendChild(el("strong", null, t.title));
    if (t.description) d.appendChild(el("p", null, t.description));
    const meta = el("div", "ai-meta");
    meta.appendChild(el("span", "badge pri-" + t.priority.toLowerCase(), t.priority));
    if (t.est_days) meta.appendChild(el("span", null, "~" + t.est_days + " day(s)"));
    if (t.deadline) meta.appendChild(el("span", null, "📅 " + t.deadline));
    d.appendChild(meta);
    return d;
  }

  function editBox(t) {
    const d = el("div", "ai-edit");
    const ti = el("input"); ti.type = "text"; ti.maxLength = 120; ti.value = t.title;
    const de = el("textarea"); de.rows = 2; de.value = t.description || "";
    const pr = el("select");
    ["Low", "Medium", "High"].forEach(function (p) {
      const o = el("option", null, p); o.value = p;
      if (p === t.priority) o.selected = true;
      pr.appendChild(o);
    });
    const es = el("input"); es.type = "number"; es.min = 1; es.max = 365; es.value = t.est_days || "";
    const dl = el("input"); dl.type = "date"; dl.value = t.deadline || "";

    d.appendChild(field("Title", ti));
    d.appendChild(field("Description", de));
    const row = el("div", "row3");
    row.appendChild(field("Priority", pr));
    row.appendChild(field("Est. days", es));
    row.appendChild(field("Deadline", dl));
    d.appendChild(row);

    d.appendChild(btn("Done", "btn-primary", function () {
      if (ti.value.trim().length < 3) { alert("Title must be at least 3 characters."); return; }
      t.title = ti.value.trim();
      t.description = de.value.trim();
      t.priority = pr.value;
      t.est_days = es.value ? parseInt(es.value, 10) : null;
      t.deadline = dl.value || null;
      t.editing = false;
      render();
    }));
    return d;
  }

  function actions(t) {
    const a = el("div", "ai-actions");
    if (t.state === "rejected") {
      a.appendChild(btn("Restore", "btn-light", function () { t.state = "pending"; render(); }));
      return a;
    }
    if (t.state === "accepted") {
      a.appendChild(btn("✓ Accepted", "btn-primary", function () { t.state = "pending"; render(); }));
    } else {
      a.appendChild(btn("Accept", "btn-light", function () { t.state = "accepted"; render(); }));
    }
    if (!t.editing) a.appendChild(btn("Edit", "btn-light", function () { t.editing = true; render(); }));
    a.appendChild(btn("Reject", "btn-danger", function () { t.state = "rejected"; t.editing = false; render(); }));
    return a;
  }

  function render() {
    list.textContent = "";
    items.forEach(function (t, i) {
      const card = el("div", "ai-card " + t.state);
      card.appendChild(el("div", "ai-num", String(i + 1)));
      const body = el("div", "ai-body");
      body.appendChild(t.editing ? editBox(t) : viewBox(t));
      card.appendChild(body);
      card.appendChild(actions(t));
      list.appendChild(card);
    });
    const n = items.filter(function (x) { return x.state === "accepted"; }).length;
    saveBtn.textContent = "Save accepted (" + n + ")";
    saveBtn.disabled = n === 0;
    count.textContent = "(" + items.length + ")";
  }

  // ---------- generate ----------
  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    hideError();
    if (!sel.value) { showError("Please select a project first."); return; }

    go.disabled = true;
    go.textContent = "Generating…";
    try {
      const res = await post("/api/ai/generate", {
        project_id: sel.value,
        title: title.value,
        description: desc.value,
        tech_stack: stack.value,
        deadline: end.value
      });
      if (!res.ok) {
        showError(res.data.error || "Something went wrong.", res.data.manual_url);
        return;
      }
      pid = sel.value;
      items = res.data.tasks.map(function (t) {
        return Object.assign({}, t, { state: "pending", editing: false });
      });
      box.hidden = false;
      render();
      box.scrollIntoView({ behavior: "smooth" });
    } catch (x) {
      showError("Could not reach the server. Is Flask still running?");
    } finally {
      go.disabled = false;
      go.textContent = "✨ Generate Tasks";
    }
  });

  // ---------- accept all / save ----------
  allBtn.addEventListener("click", function () {
    items.forEach(function (t) { if (t.state === "pending") t.state = "accepted"; });
    render();
  });

  saveBtn.addEventListener("click", async function () {
    hideError();
    const chosen = items.filter(function (t) { return t.state === "accepted"; })
      .map(function (t) {
        return { title: t.title, description: t.description, priority: t.priority,
                 est_days: t.est_days, deadline: t.deadline };
      });
    if (!chosen.length) return;

    saveBtn.disabled = true;
    try {
      const res = await post("/api/ai/save", { project_id: pid, tasks: chosen });
      if (res.ok) {
        window.location.href = res.data.redirect;
      } else {
        showError(res.data.error || "Could not save the tasks.");
        saveBtn.disabled = false;
      }
    } catch (x) {
      showError("Could not reach the server. Is Flask still running?");
      saveBtn.disabled = false;
    }
  });
})();