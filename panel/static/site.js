// Keeps the server list and the "playing now" line live without a reload.
async function refresh() {
  let data;
  try {
    const response = await fetch("/status.json", { cache: "no-store" });
    if (!response.ok) return;
    data = await response.json();
  } catch {
    return;
  }
  for (const s of data.servers) {
    const row = document.querySelector(`[data-server="${s.id}"]`);
    if (!row) continue;
    row.querySelector("[data-dot]").classList.toggle("on", s.online);
    row.querySelector("[data-players]").textContent = s.players;
    row.querySelector("[data-state]").textContent = s.online ? "playing" : "offline";
  }
  const summary = document.querySelector("[data-summary]");
  if (summary) {
    const total = data.servers.length;
    summary.textContent = `${data.playing} in game right now · ${data.online} of ${total} server${total === 1 ? "" : "s"} up`;
    document.querySelector("[data-live] .dot").classList.toggle("on", data.online > 0);
  }
}

if (!document.querySelector(".preview")) setInterval(refresh, 30000);
