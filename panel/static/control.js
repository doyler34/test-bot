/* Control page: the kill map and the player search box. Map coordinates
 * follow the mortar map's calibration (GeNeFRAG's transform plus the earth
 * correction), which the server hands over as JSON. */

for (const box of document.querySelectorAll("[data-filter-list]")) {
  const list = document.querySelector(box.dataset.filterList);
  box.addEventListener("input", () => {
    const want = box.value.trim().toLowerCase();
    for (const row of list.querySelectorAll("[data-name]")) row.hidden = want && !row.dataset.name.includes(want);
  });
}

for (const button of document.querySelectorAll("[data-copy-text]")) {
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(button.dataset.copyText);
      button.classList.add("done");
      button.title = "Copied";
    } catch (err) {
      window.prompt("Copy this", button.dataset.copyText);
    }
    setTimeout(() => { button.classList.remove("done"); button.title = "Copy IP"; }, 1500);
  });
}

/* The right-hand panel switches between who's on and the live feed; the choice is remembered. */
const SIDE_TAB = "oyb-side-tab";
for (const panel of document.querySelectorAll(".ctl-switch")) {
  const box = panel.closest(".ctl-panel");
  const show = (name) => {
    for (const b of panel.querySelectorAll("[data-side-tab]")) b.classList.toggle("on", b.dataset.sideTab === name);
    for (const part of box.querySelectorAll("[data-side-panel]")) part.hidden = part.dataset.sidePanel !== name;
    const search = box.querySelector(".ctl-search");
    if (search) search.hidden = name !== "players";
  };
  let saved = "players";
  try { saved = localStorage.getItem(SIDE_TAB) || "players"; } catch (err) { /* private window */ }
  show(saved);
  panel.addEventListener("click", (event) => {
    const button = event.target.closest("[data-side-tab]");
    if (!button) return;
    show(button.dataset.sideTab);
    try { localStorage.setItem(SIDE_TAB, button.dataset.sideTab); } catch (err) { /* private window */ }
  });
}

/* Favourite servers are kept first, per browser. */
const FAVOURITES = "oyb-favourite-servers";
const favourites = () => { try { return JSON.parse(localStorage.getItem(FAVOURITES)) || []; } catch (err) { return []; } };
function showFavourites(grid) {
  const saved = favourites();
  for (const card of [...grid.querySelectorAll(".ctl-card")].reverse()) {
    const on = saved.includes(card.dataset.id);
    const star = card.querySelector("[data-fav]");
    if (star) { star.classList.toggle("on", on); star.setAttribute("aria-pressed", on); }
    if (on) grid.prepend(card);
  }
}
for (const grid of document.querySelectorAll("[data-favourites]")) {
  showFavourites(grid);
  grid.addEventListener("refreshed", () => showFavourites(grid));
  grid.addEventListener("click", (event) => {
    const star = event.target.closest("[data-fav]");
    if (!star) return;
    const saved = favourites().filter((id) => id !== star.dataset.fav);
    if (!star.classList.contains("on")) saved.push(star.dataset.fav);
    try { localStorage.setItem(FAVOURITES, JSON.stringify(saved)); } catch (err) { /* private window */ }
    showFavourites(grid);
  });
}

const mapBox = document.getElementById("ctl-map");
if (mapBox && window.L) startMap(mapBox);

const KILL_COLOURS = { k: "#f47b20", t: "#e0443a", a: "#b18cff" };
// The log's hit zones on the same body as the player page's combat card.
const ZONE_PART = { Head: "head", Neck: "neck", Chest: "chest", Abdomen: "abdomen", Hips: "hips",
  LArm: "larm", LForearm: "larm", LHand: "larm", RArm: "rarm", RForearm: "rarm", RHand: "rarm",
  LThigh: "lleg", LCalf: "lleg", LFoot: "lleg", RThigh: "rleg", RCalf: "rleg", RFoot: "rleg" };
const ZONE_WORDS = { LArm: "left arm", LForearm: "left forearm", LHand: "left hand", RArm: "right arm",
  RForearm: "right forearm", RHand: "right hand", LThigh: "left thigh", LCalf: "left calf", LFoot: "left foot",
  RThigh: "right thigh", RCalf: "right calf", RFoot: "right foot" };
const BODY_SHAPES = {
  head: '<circle cx="60" cy="20" r="15"/>',
  neck: '<path d="M54 34h12v12h-12z"/>',
  chest: '<path d="M33 54Q36 46 48 45H72Q84 46 87 54L84 86Q83 89 79 89H41Q37 89 36 86Z"/>',
  abdomen: '<path d="M40 91H80L78 114Q77 116 74 116H46Q43 116 42 114Z"/>',
  hips: '<path d="M42 118H78Q83 122 83 132L81 138H39L37 132Q37 122 42 118Z"/>',
  rarm: '<path d="M30 50Q22 52 21 62L14 118Q13 128 16 136L22 137Q25 128 25 120L33 70Z"/>',
  larm: '<path d="M90 50Q98 52 99 62L106 118Q107 128 104 136L98 137Q95 128 95 120L87 70Z"/>',
  rleg: '<path d="M39 140H58L56 200L54 248Q54 256 47 256H42Q38 256 39 248L40 200Q37 170 39 140Z"/>',
  lleg: '<path d="M62 140H81Q83 170 80 200L81 248Q82 256 78 256H73Q66 256 66 248L64 200Z"/>',
};
const hitBody = (zone) => `<svg class="kill-body" viewBox="0 0 120 262" aria-hidden="true">${Object.entries(BODY_SHAPES)
  .map(([part, shape]) => shape.replace("/>", ` class="${ZONE_PART[zone] === part ? "hit" : ""}"/>`)).join("")}</svg>`;
const RANGE_WORDS = { "game": "since this game started", "30m": "last 30 min", "1h": "last hour", "6h": "last 6 hours", "24h": "last 24 hours",
  "7d": "last 7 days", "30d": "last 30 days" };

async function startMap(box) {
  const panel = box.closest(".ctl-map-panel");
  const reply = await fetch("/control-map/map.json", { credentials: "same-origin" });
  if (!reply.ok) { box.textContent = "The map isn't available."; return; }
  const MAP = (await reply.json()).map;
  const corrected = (m, size) => (MAP.earthCorrection ? (m - MAP.correction / 2) / (1 - MAP.correction / size) : m);
  const uncorrected = (m, size) => (MAP.earthCorrection ? m * (1 - MAP.correction / size) + MAP.correction / 2 : m);
  const toLatLng = (east, north) => L.latLng(
    corrected(north, MAP.size[1]) / MAP.transform.lat.cof - MAP.transform.lat.offset,
    corrected(east, MAP.size[0]) / MAP.transform.lng.cof - MAP.transform.lng.offset);
  const toMetres = (ll) => [uncorrected((ll.lng + MAP.transform.lng.offset) * MAP.transform.lng.cof, MAP.size[0]),
    uncorrected((ll.lat + MAP.transform.lat.offset) * MAP.transform.lat.cof, MAP.size[1])];
  const digits = MAP.digits || 3;
  const grid = (east, north) => `${String(Math.floor(east / 100)).padStart(digits, "0")} ${String(Math.floor(north / 100)).padStart(digits, "0")}`;
  const touch = window.matchMedia("(pointer: coarse)").matches;

  const bounds = L.latLngBounds(L.latLng(0, 0), L.latLng(MAP.span[1], MAP.span[0]));
  const map = L.map(box, {
    crs: L.extend({}, L.CRS.Simple, { transformation: new L.Transformation(1, 0, 1, 0) }),
    minZoom: MAP.tiles ? MAP.tiles.minZoom : 0, maxZoom: MAP.maxZoom + 2,
    zoomSnap: .25, attributionControl: false, preferCanvas: true, tap: true,
  });
  if (MAP.tiles) {
    L.tileLayer("/control-map/tiles/{z}/{x}/{y}", {
      tileSize: MAP.tiles.tileSize, minZoom: MAP.tiles.minZoom, maxNativeZoom: MAP.maxZoom,
      maxZoom: MAP.maxZoom + 2, bounds, noWrap: true,
    }).addTo(map);
  } else {
    L.imageOverlay("/control-map/picture", bounds).addTo(map);
  }
  map.fitBounds(bounds);
  map.setMaxBounds(bounds.pad(0.2));
  // Drawn inside a tab that may have been hidden: size it again when it shows.
  window.addEventListener("hashchange", () => setTimeout(() => { map.invalidateSize(); map.fitBounds(bounds); }, 0));

  // Lines go in first so the dots sit on top of them.
  const layers = { places: L.layerGroup().addTo(map), lines: L.layerGroup().addTo(map), dots: L.layerGroup().addTo(map),
    bases: L.layerGroup().addTo(map), heat: L.layerGroup(), grid: L.layerGroup(), count: L.layerGroup().addTo(map) };
  const clock = (at) => new Date(at * 1000).toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const text = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const who = (name, id) => (id ? `<a href="/player/${encodeURIComponent(id)}">${text(name || "Unknown")}</a>` : text(name || "Unknown"));
  const readout = panel.querySelector("[data-map-readout]");
  const sub = panel.querySelector("[data-map-sub]");
  const rangePick = panel.querySelector("[data-map-range]");
  const serverPick = panel.querySelector("[data-map-server]");
  const state = { names: true, lines: true, heat: false, grid: false, count: false, kills: [], bases: [], radius: 250, spot: null };
  let server = box.dataset.server;

  function drawGrid() {
    layers.grid.clearLayers();
    const style = { color: "#ffffff", weight: 1, opacity: .25, interactive: false };
    for (let m = 0; m <= MAP.size[0]; m += 1000) L.polyline([toLatLng(m, 0), toLatLng(m, MAP.size[1])], style).addTo(layers.grid);
    for (let m = 0; m <= MAP.size[1]; m += 1000) L.polyline([toLatLng(0, m), toLatLng(MAP.size[0], m)], style).addTo(layers.grid);
  }

  function drawHeat() {
    layers.heat.clearLayers();
    const label = map.getZoom() >= map.getMinZoom() + 1;
    const cell = 500, counts = new Map();
    for (const k of state.kills) {
      const key = `${Math.floor(k.to[0] / cell)},${Math.floor(k.to[1] / cell)}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    const most = Math.max(1, ...counts.values());
    for (const [key, n] of counts) {
      const [x, y] = key.split(",").map(Number);
      const square = L.rectangle([toLatLng(x * cell, y * cell), toLatLng((x + 1) * cell, (y + 1) * cell)], {
        color: "#ff8a3d", weight: .5, opacity: .5, fillColor: "#ff5a1f", fillOpacity: .15 + .6 * (n / most),
      }).bindTooltip(`${n} kill${n === 1 ? "" : "s"} in the 500 m square at ${grid(x * cell, y * cell)}`);
      square.addTo(layers.heat);
      if (label) L.tooltip({ permanent: true, direction: "center", className: "heat-n" })
        .setLatLng(square.getBounds().getCenter()).setContent(String(n)).addTo(layers.heat);
    }
  }

  function drawCount() {
    layers.count.clearLayers();
    if (!state.count || !state.spot) return;
    const [e, n] = state.spot;
    const near = state.kills.filter((k) => Math.hypot(k.to[0] - e, k.to[1] - n) <= state.radius);
    const tks = near.filter((k) => k.teamkill).length, ai = near.filter((k) => k.ai).length;
    const edge = toLatLng(e + state.radius, n);
    const centre = toLatLng(e, n);
    L.circle(centre, { radius: map.distance(centre, edge), color: "#ffd166", weight: 3, dashArray: "6 4",
      fillColor: "#ffd166", fillOpacity: .12, interactive: false }).addTo(layers.count).bringToFront();
    const ring = map.latLngToContainerPoint(edge).x - map.latLngToContainerPoint(centre).x;
    if (state.zoomTo && ring < 40) map.fitBounds(L.latLngBounds(toLatLng(e - state.radius * 2.5, n - state.radius * 2.5),
      toLatLng(e + state.radius * 2.5, n + state.radius * 2.5)));
    state.zoomTo = false;
    readout.innerHTML = `<b>${near.length} kill${near.length === 1 ? "" : "s"}</b> within `
      + `<select data-map-radius aria-label="Radius">${[100, 250, 500, 1000].map((r) =>
        `<option value="${r}" ${r === state.radius ? "selected" : ""}>${r} m</option>`).join("")}</select>`
      + ` of ${grid(e, n)} · ${tks} TK${tks === 1 ? "" : "s"} · ${ai} AI`;
  }

  // The named bases and towers, drawn once.
  async function drawPlaces() {
    const reply = await fetch("/control-map/places.json", { credentials: "same-origin" });
    if (!reply.ok) return;
    for (const place of (await reply.json()).places) {
      L.marker(toLatLng(place.x, place.z), { interactive: false, keyboard: false, icon: L.divIcon({
        className: `place ${place.kind}`, iconSize: [0, 0], html: `<i></i><span>${text(place.name)}</span>` }) }).addTo(layers.places);
    }
  }

  // Where each side started this game.
  function drawBases() {
    layers.bases.clearLayers();
    for (const base of state.bases) {
      L.marker(toLatLng(base.x, base.z), { interactive: false, keyboard: false, zIndexOffset: 1000, icon: L.divIcon({
        className: `mob mob-${text(base.side).toLowerCase()}`, iconSize: [0, 0], html: `<i></i><b>${text(base.label)}</b>` }) }).addTo(layers.bases);
    }
  }

  function draw() {
    layers.dots.clearLayers();
    layers.lines.clearLayers();
    for (const k of state.kills) {
      const kind = k.teamkill ? "t" : (k.ai || !k.killer ? "a" : "k");
      const colour = KILL_COLOURS[kind];
      const to = toLatLng(k.to[0], k.to[1]);
      let line = null;
      if (k.from) {
        const from = toLatLng(k.from[0], k.from[1]);
        line = L.polyline([from, to], { color: colour, weight: 1, opacity: .4, interactive: false }).addTo(layers.lines);
        L.circleMarker(from, { radius: 1.8, weight: 0, fillColor: colour, fillOpacity: .6, interactive: false }).addTo(layers.lines);
      }
      const zone = ZONE_PART[k.zone] ? `<p class="kill-zone">Hit in the <b>${text(ZONE_WORDS[k.zone] || k.zone.toLowerCase())}</b></p>` : "";
      const details = `<div class="kill-pop">${ZONE_PART[k.zone] ? hitBody(k.zone) : ""}<div>`
        + `<b class="${kind}">${k.teamkill ? "Teamkill" : kind === "a" ? "Killed by AI or nobody" : "Kill"}</b>`
        + `<p>${k.ai ? text(k.killer || "AI") : who(k.killer, k.killer_id)} → ${who(k.victim, k.victim_id)}</p>`
        + `<p>${text(k.how || "unknown")}${k.distance ? `, ${k.distance} m` : ""}</p>${zone}`
        + `<p>${clock(k.at)} · grid ${grid(k.to[0], k.to[1])}</p></div></div>`;
      // Teamkills stand out with a white ring; AI kills sit back.
      const size = (kind === "t" ? 5 : kind === "a" ? 3.5 : 4) * (touch ? 1.5 : 1);
      L.circleMarker(to, { radius: size, color: kind === "t" ? "#fff" : "#0d1013", weight: kind === "t" ? 1.5 : 1,
        fillColor: colour, fillOpacity: kind === "a" ? .7 : .92 })
        .on("mouseover", () => line && line.setStyle({ opacity: 1, weight: 2 }).bringToFront())
        .on("mouseout", () => line && line.setStyle({ opacity: .4, weight: 1 }))
        .on("click", () => {
          // While counting, a dot is just a spot to count around.
          if (state.count) { state.spot = [...k.to]; state.zoomTo = true; drawCount(); return; }
          L.popup({ maxWidth: 300 }).setLatLng(to).setContent(details).openOn(map);
        })
        .addTo(layers.dots);
    }
    if (state.heat) drawHeat();
    drawCount();
  }

  async function load() {
    if (map.getContainer().querySelector(".leaflet-popup")) return;    // don't pull a popup out from under someone
    const range = rangePick ? rangePick.value : "game";
    const option = serverPick && serverPick.selectedOptions[0];
    const other = option && option.dataset.mapName && !option.dataset.mapName.toLowerCase().includes(MAP.name.toLowerCase());
    if (other) {
      state.kills = [];
      state.bases = [];
      drawBases();
      draw();
      sub.textContent = `· ${option.textContent} is on ${option.dataset.mapName}; this map is ${MAP.name}`;
      return;
    }
    const res = await fetch(`/server/${encodeURIComponent(server)}/kills.json?range=${range}`, { credentials: "same-origin" });
    if (!res.ok) return;
    const data = await res.json();
    state.kills = data.kills;
    state.bases = data.bases || [];
    drawBases();
    draw();
    sub.textContent = `· ${data.kills.length}${data.capped ? "+" : ""} kill${data.kills.length === 1 ? "" : "s"}`;
    sub.title = RANGE_WORDS[range];
  }

  rangePick?.addEventListener("change", load);
  serverPick?.addEventListener("change", () => { server = serverPick.value; load(); });
  for (const button of panel.querySelectorAll("[data-map-toggle]")) {
    button.addEventListener("click", () => {
      const name = button.dataset.mapToggle;
      state[name] = !state[name];
      button.classList.toggle("on", state[name]);
      button.setAttribute("aria-pressed", state[name]);
      if (name === "names") state.names ? layers.places.addTo(map) : layers.places.remove();
      if (name === "lines" && !state.heat) state.lines ? layers.lines.addTo(map) : layers.lines.remove();
      if (name === "heat") {
        // Squares instead of dots: the dots would hide them.
        if (state.heat) { drawHeat(); layers.heat.addTo(map); layers.dots.remove(); layers.lines.remove(); }
        else { layers.heat.remove(); layers.dots.addTo(map); if (state.lines) layers.lines.addTo(map); }
      }
      if (name === "grid") { if (state.grid) { drawGrid(); layers.grid.addTo(map); } else layers.grid.remove(); }
      if (name === "count") {
        box.classList.toggle("counting", state.count);
        state.spot = null;
        drawCount();
        readout.textContent = state.count ? (touch ? "Tap the map to count the kills around that spot." : "Click the map to count the kills around that spot.") : "";
      }
    });
  }
  readout.addEventListener("change", (event) => {
    if (event.target.matches("[data-map-radius]")) { state.radius = Number(event.target.value); state.zoomTo = true; drawCount(); }
  });
  map.on("click", (event) => {
    if (!state.count) return;
    state.spot = toMetres(event.latlng);
    state.zoomTo = true;
    drawCount();
  });
  map.on("zoomend", () => { if (state.heat) drawHeat(); });
  map.on("popupopen", () => {
    if (!touch) return;
    panel.classList.remove("map-tools-open");
    panel.querySelector("[data-map-layers]")?.setAttribute("aria-expanded", "false");
  });
  const layersButton = panel.querySelector("[data-map-layers]");
  layersButton?.addEventListener("click", () => {
    const open = panel.classList.toggle("map-tools-open");
    layersButton.setAttribute("aria-expanded", open);
  });
  map.on("mousemove", (event) => {
    if (state.count && state.spot) return;
    const [e, n] = toMetres(event.latlng);
    if (e < 0 || n < 0 || e > MAP.size[0] || n > MAP.size[1]) return;
    readout.textContent = `Grid ${grid(e, n)}`;
  });

  const full = panel.querySelector("[data-map-full]");
  full?.addEventListener("click", () => {
    const on = panel.classList.toggle("map-full-on");
    document.body.classList.toggle("map-open", on);
    full.setAttribute("aria-label", on ? "Close full screen map" : "Full screen map");
    setTimeout(() => { map.invalidateSize(); map.fitBounds(bounds); }, 0);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && panel.classList.contains("map-full-on")) full.click(); });

  drawPlaces();
  load();
  setInterval(() => {
    const range = rangePick ? rangePick.value : "game";
    if (!document.hidden && ["game", "30m", "1h"].includes(range)) load();
  }, 30000);
}
