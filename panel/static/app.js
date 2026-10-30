document.addEventListener("submit", (event) => {
  const message = (event.submitter && event.submitter.dataset.confirm) || event.target.dataset.confirm;
  if (message && !confirm(message)) event.preventDefault();
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-check-all]")) {
    for (const box of document.querySelectorAll(`input[type=checkbox][name="${event.target.dataset.checkAll}"]`)) box.checked = event.target.checked;
  }
  if (event.target.matches("[data-autosubmit]")) {
    if (location.hash) event.target.form.action = location.pathname + location.hash;
    event.target.form.submit();
  }
});

async function refresh(box) {
  const pane = box.closest("[data-joiners]") ? null : box.closest("[data-filter]");
  const url = pane ? `${box.dataset.refresh}?show=${encodeURIComponent(pane.dataset.filter)}` : box.dataset.refresh;
  try {
    const response = await fetch(url, { credentials: "same-origin" });
    if (response.status === 401) return location.reload();
    if (response.ok) {
      box.innerHTML = await response.text();
      box.dispatchEvent(new Event("refreshed"));
      for (const input of document.querySelectorAll("[data-filter-rows]")) filterRows(input);
    }
  } catch (err) {}
}

// A part that's out of sight (a closed tab, a hidden side panel) isn't asked for;
// it's brought up to date as soon as it can be seen again.
const shown = (box) => box.getClientRects().length > 0;
const missed = new Set();

for (const box of document.querySelectorAll("[data-refresh]")) {
  const every = Number(box.dataset.every) || 10000;
  setInterval(() => {
    if (document.hidden || box.contains(document.activeElement) || box.querySelector(".kick-pick[open]")) return;
    if (!shown(box)) return missed.add(box);
    refresh(box);
  }, every);
}

// The joins tool lists nobody until Run is pressed, and only asks again when Run is pressed again, so it
// has no refresh address until then. Its time, PC-only and Steam choices are remembered for next time.
// Run also has the server check more Steam accounts than it would otherwise.
for (const card of document.querySelectorAll("[data-joiners]")) {
  const body = card.querySelector(".joiner-results");
  const form = card.querySelector("form");
  const saved = () => {
    try { return JSON.parse(localStorage.getItem("joiners") || "{}"); } catch (err) { return {}; }
  };
  const was = saved();
  if (was.amount) form.elements.amount.value = was.amount;
  if (was.unit) form.elements.unit.value = was.unit;
  if ("pc" in was) form.elements.pc.checked = was.pc;
  form.elements.steam.checked = Boolean(was.steam);
  const query = () => {
    const amount = Math.max(1, Number(form.elements.amount.value) || 30);
    const mins = Math.min(amount * Number(form.elements.unit.value), 10080);
    return `mins=${mins}&pc=${form.elements.pc.checked ? 1 : 0}&steam=${form.elements.steam.checked ? 1 : 0}`;
  };
  const remember = () => {
    const { amount, unit, pc, steam } = form.elements;
    try { localStorage.setItem("joiners", JSON.stringify({ amount: amount.value, unit: unit.value, pc: pc.checked, steam: steam.checked })); } catch (err) {}
  };
  form.addEventListener("change", remember);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    remember();
    body.dataset.refresh = `${body.dataset.base}?${query()}&run=1`;
    await refresh(body);
  });
}

function catchUp() {
  for (const box of missed) {
    if (!shown(box)) continue;
    missed.delete(box);
    refresh(box);
  }
}
document.addEventListener("click", () => setTimeout(catchUp, 0));
window.addEventListener("hashchange", catchUp);
document.addEventListener("visibilitychange", () => { if (!document.hidden) catchUp(); });

function closeFull() {
  for (const open of document.querySelectorAll(".pane.full, .card.full, .ctl-panel.full")) open.classList.remove("full");
  document.body.classList.remove("has-full");
}

for (const pane of document.querySelectorAll("[data-expand]")) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "expand";
  button.title = "Full screen";
  button.setAttribute("aria-label", "Full screen");
  button.textContent = "⤢";
  button.addEventListener("click", () => {
    const opening = !pane.classList.contains("full");
    closeFull();
    if (opening) {
      pane.classList.add("full");
      document.body.classList.add("has-full");
      button.textContent = "✕";
      button.title = "Close";
      button.setAttribute("aria-label", "Close full screen");
    }
  });
  pane.prepend(button);
}

// Phones have no Escape key: the ✕ button, a tap outside the panel, or the
// back button all leave full screen, so the page never stays locked.
document.addEventListener("click", (event) => {
  if (!document.body.classList.contains("has-full")) return;
  if (event.target.closest(".pane.full, .card.full, .ctl-panel.full")) return;
  closeFull();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeFull();
});
window.addEventListener("popstate", closeFull);
function resetExpandButtons() {
  for (const pane of document.querySelectorAll(".expand")) {
    const open = pane.parentElement.classList.contains("full");
    pane.textContent = open ? "✕" : "⤢";
  }
}
document.addEventListener("click", () => setTimeout(resetExpandButtons, 0));

document.addEventListener("click", (event) => {
  const chip = event.target.closest("[data-show]");
  if (!chip) return;
  const pane = chip.closest("[data-filter]");
  pane.dataset.filter = chip.dataset.show;
  const box = pane.querySelector("[data-refresh]");
  if (box) refresh(box);
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

function filterRows(input) {
  const q = input.value.trim().toLowerCase();
  for (const row of document.querySelectorAll(input.dataset.filterRows + " tbody tr")) {
    row.hidden = Boolean(q) && !row.textContent.toLowerCase().includes(q);
  }
}

for (const input of document.querySelectorAll("[data-filter-rows]")) {
  input.addEventListener("input", () => filterRows(input));
}

for (const button of document.querySelectorAll("[data-copy]")) {
  button.addEventListener("click", async () => {
    const box = document.querySelector(button.dataset.copy);
    box.select();
    try { await navigator.clipboard.writeText(box.value); } catch { document.execCommand("copy"); }
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy"; }, 1500);
  });
}

for (const select of document.querySelectorAll("[data-jump]")) {
  select.addEventListener("change", () => { location.href = select.value; });
}

for (const select of document.querySelectorAll("[data-reason-pick]")) {
  select.addEventListener("change", () => { if (select.value) select.form.elements.reason.value = select.value; });
}

for (const select of document.querySelectorAll("[data-ban-length]")) {
  const custom = select.form.querySelector("[data-ban-custom]");
  const show = () => {
    custom.hidden = select.value !== "custom";
    custom.querySelector("input").required = select.value === "custom";
  };
  select.addEventListener("change", show);
  show();
}

// Links and buttons that start something slow say so straight away.
for (const el of document.querySelectorAll("[data-loading]")) {
  el.dataset.label = el.textContent;
  const start = () => {
    el.classList.add("busy");
    el.textContent = el.dataset.loading;
  };
  if (el.tagName === "A") el.addEventListener("click", start);
  else if (el.form) el.form.addEventListener("submit", start);
}
window.addEventListener("pageshow", (event) => {
  if (!event.persisted) return;
  for (const el of document.querySelectorAll(".busy")) {
    el.classList.remove("busy");
    el.textContent = el.dataset.label;
  }
});

// The bell and name menus close when you click anywhere else.
document.addEventListener("click", (event) => {
  for (const open of document.querySelectorAll(".top-pop[open]")) {
    if (!open.contains(event.target)) open.removeAttribute("open");
  }
});
