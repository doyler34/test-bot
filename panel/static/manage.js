// Keeps a server's console page live: re-reads the log every few seconds and
// stays at the bottom unless you've scrolled up to read something.
const live = document.querySelector("[data-live]");
if (live) {
  const atBottom = () => live.scrollHeight - live.scrollTop - live.clientHeight < 40;
  live.scrollTop = live.scrollHeight;
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const reply = await fetch(live.dataset.live, { credentials: "same-origin" });
      if (!reply.ok) return;
      const text = await reply.text();
      if (text === live.textContent) return;
      const stick = atBottom();
      live.textContent = text;
      if (stick) live.scrollTop = live.scrollHeight;
    } catch (e) { /* try again next time */ }
  }, 3000);
}

// Settings page: filter as you type, and count what's been changed.
const form = document.querySelector("[data-set-form]");
if (form) {
  const rows = [...form.querySelectorAll("[data-set-row]")];
  const search = document.querySelector("[data-set-search]");
  const empty = form.querySelector("[data-set-empty]");
  search?.addEventListener("input", () => {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let shown = 0;
    for (const row of rows) {
      const hit = words.every((w) => row.dataset.words.includes(w));
      row.hidden = !hit;
      shown += hit;
    }
    for (const section of form.querySelectorAll(".set-section")) {
      section.hidden = ![...section.querySelectorAll("[data-set-row]")].some((r) => !r.hidden);
    }
    empty.hidden = shown > 0;
  });

  const count = form.querySelector("[data-set-count]");
  const fields = [...form.querySelectorAll("[data-was]")];
  // Compared with what the page showed when it loaded, so the browser's own tidying
  // of values (a textarea drops its first newline, say) never looks like a change.
  const shown = new Map(fields.map((f) => [f, f.type === "checkbox" ? f.checked : f.value]));
  const changed = (f) => (f.type === "checkbox" ? f.checked : f.value) !== shown.get(f);
  const update = () => {
    let n = 0;
    for (const f of fields) {
      const on = changed(f);
      f.closest("[data-set-row]").classList.toggle("changed", on);
      n += on;
    }
    count.textContent = n ? `${n} change${n === 1 ? "" : "s"} not saved` : "No changes yet";
    count.classList.toggle("warn", n > 0);
  };
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  window.addEventListener("beforeunload", (e) => {
    if (fields.some(changed) && !form.dataset.sending) e.preventDefault();
  });
  form.addEventListener("submit", () => { form.dataset.sending = "1"; });
}

// New server: picking another server to copy from reloads its scenario and player limit.
const from = document.querySelector("[data-reload-from]");
from?.addEventListener("change", () => { location.href = "/manage/new?from=" + encodeURIComponent(from.value); });

// Server manager list: while a new server is being set up, check on it every few seconds.
if (document.querySelector(".setup-progress .spinner")) setTimeout(() => location.reload(), 8000);

// Mods page: tick several mods, then add them together.
for (const bar of document.querySelectorAll("[data-pick]")) {
  const boxes = () => [...document.querySelectorAll(`input[name=pick][form="${bar.id}"]`)];
  const count = bar.querySelector("[data-pick-count]");
  const go = bar.querySelector("[data-pick-go]");
  const refresh = () => {
    const n = boxes().filter((b) => b.checked).length;
    count.textContent = n ? `${n} selected` : "None selected";
    go.disabled = !n;
    go.textContent = n ? `Add ${n} selected` : "Add selected";
  };
  document.addEventListener("change", (event) => {
    if (event.target.matches && event.target.matches(`input[name=pick][form="${bar.id}"]`)) refresh();
  });
  bar.querySelector("[data-pick-all]").addEventListener("click", () => { for (const b of boxes()) b.checked = true; refresh(); });
  bar.querySelector("[data-pick-none]").addEventListener("click", () => { for (const b of boxes()) b.checked = false; refresh(); });
  refresh();
}
