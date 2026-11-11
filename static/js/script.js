// ---------- CSRF (Phase 6) ----------
// The server puts a per-session secret in <meta name="csrf-token">.
// 1) add it as a hidden field to every POST form
// 2) send it as a header on every same-site fetch() that changes data
const csrfMeta = document.querySelector('meta[name="csrf-token"]');
const csrfToken = csrfMeta ? csrfMeta.content : "";

Array.from(document.forms).forEach(function (f) {
  if ((f.method || "").toLowerCase() === "post") {
    const i = document.createElement("input");
    i.type = "hidden";
    i.name = "csrf_token";
    i.value = csrfToken;
    f.appendChild(i);
  }
});

const rawFetch = window.fetch;
window.fetch = function (url, opts) {
  opts = opts || {};
  const method = (opts.method || "GET").toUpperCase();
  if (method !== "GET" && typeof url === "string" && url.charAt(0) === "/") {
    opts.headers = Object.assign({}, opts.headers, { "X-CSRF-Token": csrfToken });
  }
  return rawFetch(url, opts);
};

// ---------- Flash messages ----------
// Auto-hide flash messages after 4 seconds
document.querySelectorAll(".flash").forEach(function (el) {
  setTimeout(function () {
    el.style.opacity = "0";
    setTimeout(function () { el.remove(); }, 400);
  }, 4000);
});

// ---------- Forms ----------
// Client-side check: password and confirm must match
const form = document.getElementById("reg-form");
if (form) {
  form.addEventListener("submit", function (e) {
    const pw = form.elements["password"].value;
    const pw2 = form.elements["confirm"].value;
    if (pw !== pw2) {
      e.preventDefault();
      alert("Passwords do not match.");
    }
  });
}

// Ask for confirmation before any form that has a data-msg attribute
document.querySelectorAll("form[data-msg]").forEach(function (f) {
  f.addEventListener("submit", function (e) {
    if (!confirm(f.dataset.msg)) e.preventDefault();
  });
});

// Project form: deadline can't be earlier than the start date
const pf = document.getElementById("proj-form");
if (pf) {
  const s = pf.elements["start_date"], d = pf.elements["end_date"];
  const sync = function () { d.min = s.value; };
  s.addEventListener("change", sync);
  sync();
  pf.addEventListener("submit", function (e) {
    if (s.value && d.value && d.value < s.value) {
      e.preventDefault();
      alert("Deadline cannot be before the start date.");
    }
  });
}

// Set progress bar widths from data-w (keeps Jinja out of CSS)
document.querySelectorAll(".bar[data-w]").forEach(function (b) {
  b.style.width = b.dataset.w + "%";
});

// Task table: changing a status dropdown submits its form immediately
document.querySelectorAll(".status-sel").forEach(function (sel) {
  sel.addEventListener("change", function () { sel.form.submit(); });
});