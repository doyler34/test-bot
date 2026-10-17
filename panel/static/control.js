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

const mapBox = document.getElementById("ctl-map");
if (mapBox && window.L) startMap(mapBox);

async function startMap(box) {
  const reply = await fetch("/control-map/map.json", { credentials: "same-origin" });
  if (!reply.ok) { box.textContent = "The map isn't available."; return; }
  const MAP = (await reply.json()).map;
  const corrected = (m, size) => (MAP.earthCorrection ? (m - MAP.correction / 2) / (1 - MAP.correction / size) : m);
  const toLatLng = (east, north) => L.latLng(
    corrected(north, MAP.size[1]) / MAP.transform.lat.cof - MAP.transform.lat.offset,
    corrected(east, MAP.size[0]) / MAP.transform.lng.cof - MAP.transform.lng.offset);

  const bounds = L.latLngBounds(L.latLng(0, 0), L.latLng(MAP.span[1], MAP.span[0]));
  const map = L.map(box, {
    crs: L.extend({}, L.CRS.Simple, { transformation: new L.Transformation(1, 0, 1, 0) }),
    minZoom: MAP.tiles ? MAP.tiles.minZoom : 0, maxZoom: MAP.maxZoom + 2,
    zoomSnap: .25, attributionControl: false,
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

  const layer = L.layerGroup().addTo(map);
  const clock = (at) => new Date(at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const text = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  async function draw() {
    const res = await fetch(`/server/${encodeURIComponent(box.dataset.server)}/kills.json`, { credentials: "same-origin" });
    if (!res.ok) return;
    const { kills } = await res.json();
    layer.clearLayers();
    for (const k of kills) {
      const kind = k.teamkill ? "t" : (k.ai || !k.killer ? "a" : "k");
      const to = toLatLng(k.to[0], k.to[1]);
      if (k.from) {
        L.polyline([toLatLng(k.from[0], k.from[1]), to], { className: `ctl-shot ${kind}`, interactive: false }).addTo(layer);
      }
      L.circleMarker(to, { radius: 6, className: `ctl-kill ${kind}` })
        .bindTooltip(`<b>${text(k.killer || "Unknown")}</b> → ${text(k.victim)}<br>${text(k.how)}`
          + `${k.distance ? `, ${k.distance} m` : ""} · ${clock(k.at)}${k.teamkill ? " · <b>teamkill</b>" : ""}`)
        .addTo(layer);
    }
  }
  draw();
  setInterval(() => { if (!document.hidden) draw(); }, 30000);
}
