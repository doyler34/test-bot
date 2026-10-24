// Keeps server numbers live without a reload, and runs the menus.
const HEALTH = { healthy: "Healthy", partial: "Partly up", down: "Offline", unknown: "Unknown" };
const stale = document.querySelectorAll("[data-stale]");

function chip(online, players) {
  return online ? [players ? "Online" : "Online · empty", players ? "on" : "on empty"] : ["Offline", "off"];
}

function paint(box, s) {
  box.classList.toggle("is-on", s.online);
  box.classList.toggle("is-off", !s.online);
  for (const el of box.querySelectorAll("[data-players]")) el.textContent = s.players;
  for (const el of box.querySelectorAll("[data-slots]")) if (s.slots) el.textContent = s.slots;
  for (const bar of box.querySelectorAll("[data-bar]")) bar.style.width = s.slots ? Math.min(100, Math.floor(s.players * 100 / s.slots)) + "%" : "0%";
  for (const el of box.querySelectorAll("[data-state]")) {
    const [text, cls] = chip(s.online, s.players);
    el.textContent = text;
    el.className = "chip " + cls;
  }
}

function summarise(data) {
  const slots = data.servers.map(s => s.slots).filter(Boolean);
  const total = data.servers.length;
  const health = !total ? "unknown" : data.online === total ? "healthy" : data.online ? "partial" : "down";
  const set = (name, html) => document.querySelectorAll(`[data-metric="${name}"]`).forEach(el => el.innerHTML = html);
  set("online", `${data.online}<span class="of"> / ${total}</span>`);
  set("players", data.playing);
  set("slots", slots.length ? slots.reduce((a, b) => a + b, 0) : "—");
  document.querySelectorAll('[data-metric="health"]').forEach(el => { el.textContent = HEALTH[health]; el.className = "health " + health; });
  const line = !total ? "No servers listed right now" : !data.online ? "No servers online right now"
    : `${data.playing} playing across ${data.online} online server${data.online === 1 ? "" : "s"}`;
  document.querySelectorAll("[data-live-text]").forEach(el => el.textContent = line);
  document.querySelectorAll("[data-live-line] .dot").forEach(el => el.classList.toggle("on", data.online > 0));
}

function reorder() {
  for (const grid of document.querySelectorAll("[data-grid]")) {
    const key = card => [card.classList.contains("is-on") ? 0 : 1, -Number(card.querySelector("[data-players]").textContent),
                         card.querySelector("h3").textContent.toLowerCase()];
    const sorted = [...grid.children].sort((a, b) => {
      const [x, y] = [key(a), key(b)];
      return x[0] - y[0] || x[1] - y[1] || (x[2] < y[2] ? -1 : x[2] > y[2] ? 1 : 0);
    });
    grid.append(...sorted);
  }
}

async function refresh() {
  let data;
  try {
    const response = await fetch("/status.json", { cache: "no-store" });
    if (!response.ok) throw new Error(response.status);
    data = await response.json();
  } catch {
    stale.forEach(el => el.hidden = false);
    document.querySelectorAll("[data-state]").forEach(el => el.classList.add("stale-chip"));
    return;
  }
  stale.forEach(el => el.hidden = true);
  document.querySelectorAll("[data-state]").forEach(el => el.classList.remove("stale-chip"));
  for (const s of data.servers) document.querySelectorAll(`[data-server="${s.id}"]`).forEach(box => paint(box, s));
  reorder();
  summarise(data);
}

if (document.querySelector("[data-server], [data-metric]") && !document.querySelector(".preview")) setInterval(refresh, 30000);

const root = document.documentElement;
const menu = document.querySelector(".menu");
if (menu) {
  const summary = menu.querySelector("summary");
  const close = () => { menu.open = false; };
  menu.addEventListener("toggle", () => {
    root.classList.toggle("menu-open", menu.open);
    if (menu.open) menu.querySelector(".menu-sheet a")?.focus();
  });
  menu.querySelectorAll(".menu-sheet a").forEach(a => a.addEventListener("click", close));
  window.matchMedia("(min-width: 960px)").addEventListener("change", close);
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && menu.open) { close(); summary.focus(); }
  });
}

const drops = document.querySelectorAll(".drop");
for (const drop of drops) {
  drop.addEventListener("toggle", () => { if (drop.open) drops.forEach(other => { if (other !== drop) other.open = false; }); });
  drop.querySelectorAll("a").forEach(a => a.addEventListener("click", () => { drop.open = false; }));
}
document.addEventListener("click", e => drops.forEach(drop => { if (drop.open && !drop.contains(e.target)) drop.open = false; }));
document.addEventListener("keydown", e => {
  if (e.key !== "Escape") return;
  drops.forEach(drop => { if (drop.open) { drop.open = false; drop.querySelector("summary").focus(); } });
});
