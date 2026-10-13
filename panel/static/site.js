// Keeps the server list and the "playing now" line live without a reload.
const stale = document.querySelector("[data-stale]");

async function refresh() {
  let data;
  try {
    const response = await fetch("/status.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    data = await response.json();
  } catch {
    if (stale) stale.hidden = false;
    return;
  }
  if (stale) stale.hidden = true;
  for (const s of data.servers) {
    const row = document.querySelector(`[data-server="${s.id}"]`);
    if (!row) continue;
    row.classList.toggle("is-on", s.online);
    row.classList.toggle("is-off", !s.online);
    row.querySelector("[data-players]").textContent = s.players;
    row.querySelector("[data-state]").textContent = s.online ? "Online" : "Offline";
  }
  const list = document.querySelector(".servers");
  if (list) {
    const rows = [...list.children];
    const busy = row => [Number(row.dataset.game), row.classList.contains("is-on") ? 0 : 1,
                         -Number(row.querySelector("[data-players]").textContent)];
    rows.sort((a, b) => {
      const [x, y] = [busy(a), busy(b)];
      return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
    });
    list.append(...rows);
  }
  const summary = document.querySelector("[data-summary]");
  if (summary) {
    summary.textContent = `${data.playing} playing · ${data.online} of ${data.servers.length} up`;
    document.querySelector("[data-live] .dot").classList.toggle("on", data.online > 0);
  }
}

if (!document.querySelector(".preview")) setInterval(refresh, 30000);

const menu = document.querySelector(".menu");
if (menu) {
  menu.querySelectorAll("nav a").forEach(a => a.addEventListener("click", () => menu.open = false));
  document.addEventListener("click", e => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
}
