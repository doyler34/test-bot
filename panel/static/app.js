document.addEventListener("submit", (event) => {
  const message = event.target.dataset.confirm;
  if (message && !confirm(message)) event.preventDefault();
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-autosubmit]")) event.target.form.submit();
});

for (const box of document.querySelectorAll("[data-refresh]")) {
  const every = Number(box.dataset.every) || 10000;
  setInterval(async () => {
    if (document.hidden || box.contains(document.activeElement)) return;
    try {
      const response = await fetch(box.dataset.refresh, { credentials: "same-origin" });
      if (response.status === 401) return location.reload();
      if (response.ok) box.innerHTML = await response.text();
    } catch (err) {}
  }, every);
}

function closeFull() {
  const open = document.querySelector(".pane.full");
  if (open) open.classList.remove("full");
  document.body.classList.remove("has-full");
}

for (const pane of document.querySelectorAll("[data-expand]")) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "expand";
  button.title = "Full screen";
  button.textContent = "⤢";
  button.addEventListener("click", () => {
    const opening = !pane.classList.contains("full");
    closeFull();
    if (opening) {
      pane.classList.add("full");
      document.body.classList.add("has-full");
    }
  });
  pane.prepend(button);
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeFull();
});

document.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-show]");
  if (!chip) return;
  const pane = chip.closest("[data-filter]");
  pane.dataset.filter = chip.dataset.show;
});
