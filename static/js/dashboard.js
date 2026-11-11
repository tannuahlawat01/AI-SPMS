// Draw the task-status donut from the data-* attributes (no chart library).
(function () {
  const d = document.getElementById("donut");
  if (!d) return;

  const done = Number(d.dataset.done);
  const doing = Number(d.dataset.doing);
  const todo = Number(d.dataset.todo);
  const total = done + doing + todo;

  if (total === 0) {                 // no tasks yet: plain grey ring
    d.style.background = "#e5e7eb";
    return;
  }
  const a = (done * 100) / total;    // end of the green slice
  const b = a + (doing * 100) / total;   // end of the amber slice
  d.style.background =
    "conic-gradient(#16a34a 0 " + a + "%, #f59e0b " + a + "% " + b + "%, #9ca3af " + b + "% 100%)";
})();