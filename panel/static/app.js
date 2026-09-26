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

function showTab(name) {
  const panels = document.querySelectorAll("[data-tab-panel]");
  if (!panels.length) return;
  if (![...panels].some((panel) => panel.dataset.tabPanel === name)) name = panels[0].dataset.tabPanel;
  for (const panel of panels) panel.hidden = panel.dataset.tabPanel !== name;
  for (const tab of document.querySelectorAll("[data-tab]")) tab.classList.toggle("on", tab.dataset.tab === name);
}

showTab(location.hash.slice(1));
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));

for (const input of document.querySelectorAll("[data-player-search]")) {
  const box = input.closest(".picker");
  const hidden = box.querySelector("input[name=identity]");
  const chosen = box.querySelector(".chosen");
  const list = box.querySelector(".suggest");
  let timer, active = -1, asked = "";

  const pick = (item) => {
    input.value = item.dataset.name;
    hidden.value = item.dataset.identity;
    chosen.textContent = item.dataset.identity;
    list.hidden = true;
  };
  const highlight = (index) => {
    const items = list.querySelectorAll("li");
    active = Math.max(-1, Math.min(index, items.length - 1));
    items.forEach((li, i) => li.classList.toggle("active", i === active));
  };

  input.addEventListener("input", () => {
    hidden.value = "";
    chosen.textContent = "";
    clearTimeout(timer);
    const q = input.value.trim();
    if (q.length < 2) { list.hidden = true; return; }
    timer = setTimeout(async () => {
      asked = q;
      const response = await fetch("/players/search.json?q=" + encodeURIComponent(q), { credentials: "same-origin" });
      if (!response.ok || asked !== input.value.trim()) return;
      const matches = await response.json();
      list.replaceChildren(...matches.map((m) => {
        const li = document.createElement("li");
        li.dataset.name = m.name;
        li.dataset.identity = m.identity;
        const name = document.createElement("b");
        name.textContent = m.name;
        const detail = document.createElement("span");
        detail.className = "muted";
        detail.textContent = [m.online ? "on " + m.online : m.seen && "seen " + m.seen,
          m.aka.length && "also " + m.aka.join(", "), m.identity.slice(0, 8) + "…"].filter(Boolean).join(" · ");
        li.append(name, " ", detail);
        if (m.online) li.classList.add("online");
        if (m.banned) { const tag = document.createElement("span"); tag.className = "pill down"; tag.textContent = "Banned"; li.append(" ", tag); }
        li.addEventListener("mousedown", (event) => { event.preventDefault(); pick(li); });
        return li;
      }));
      if (!matches.length) {
        const li = document.createElement("li");
        li.className = "muted empty";
        li.textContent = "Nobody by that name has been seen on the servers.";
        list.append(li);
      }
      active = -1;
      list.hidden = false;
    }, 150);
  });

  input.addEventListener("keydown", (event) => {
    if (list.hidden) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      highlight(active + (event.key === "ArrowDown" ? 1 : -1));
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      pick(list.querySelectorAll("li")[active]);
    } else if (event.key === "Escape") {
      list.hidden = true;
    }
  });
  input.addEventListener("blur", () => { list.hidden = true; });
}
