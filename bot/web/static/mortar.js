/* The mortar map.
 *
 * This file draws and it asks. Every number it shows came back from the bot's
 * own ballistic engine over /api/mortar/calculate - there is no table and no
 * trigonometry for a firing solution in here, on purpose. The only maths it
 * does is turning a click into an image pixel and labelling a grid square.
 */
'use strict';

const TOKEN = document.body.dataset.token;
// Inside the panel the login is the key, and its forms need the panel's token.
const CSRF = document.body.dataset.csrf || '';
// The imagery stops at its own deepest zoom; past that Leaflet enlarges those
// tiles so a marker can still be put down to the metre.
const EXTRA_ZOOM = 3;
const el = (id) => document.getElementById(id);

let MAP = null;          // the calibration the server handed us
let TUBES = [];
let map, layer, gun = null, target = null, line = null, ring = null, label = null;
let arrow = null;
let gridMinor = null, gridLabels = null;
let pending = 0;
/* The wind as the spotter reports it: the bearing it blows FROM. Everything
 * else about wind - components, corrections, signs - is the backend's work. */
const WIND = { speed: 0, from: 0 };
const POINTS = [['N', 0], ['NE', 45], ['E', 90], ['SE', 135],
                ['S', 180], ['SW', 225], ['W', 270], ['NW', 315]];

/* --- coordinates ----------------------------------------------------
 * GeNeFRAG's transform, as his mapEngine.js applies it:
 *
 *   gameX = (lng + lng.offset) * lng.cof
 *   gameZ = (lat + lat.offset) * lat.cof
 *
 * plus, where the map sets it, an earth correction that pulls towards the
 * middle and is zero at the centre. This mirrors Calibration.world() and
 * Calibration.leaflet() in bot/mortar/calibration.py, which is where the rule
 * is defined and tested; the figures on the panel come from the server.
 */
const corrected = (metres, size) => (MAP.earthCorrection
  ? metres + ((size / 2 - metres) / size) * MAP.correction : metres);
const uncorrected = (metres, size) => (MAP.earthCorrection
  ? (metres - MAP.correction / 2) / (1 - MAP.correction / size) : metres);

function toWorld(latlng) {
  const t = MAP.transform;
  return {
    east: corrected((latlng.lng + t.lng.offset) * t.lng.cof, MAP.size[0]),
    north: corrected((latlng.lat + t.lat.offset) * t.lat.cof, MAP.size[1]),
  };
}

function toLatLng(east, north) {
  const t = MAP.transform;
  return L.latLng(uncorrected(north, MAP.size[1]) / t.lat.cof - t.lat.offset,
                  uncorrected(east, MAP.size[0]) / t.lng.cof - t.lng.offset);
}

function gridText(east, north) {
  const step = Math.pow(10, 5 - MAP.digits);
  const part = (v) => String(Math.floor(v / step) % Math.pow(10, MAP.digits))
    .padStart(MAP.digits, '0');
  return `${part(east)} ${part(north)}`;
}

/* The tile pyramid's CRS: Simple, but without Leaflet's usual y flip, because
 * the tiles are written with row 0 at the top like any web map. */
function tileCRS() {
  return L.extend({}, L.CRS.Simple, {
    transformation: new L.Transformation(1, 0, 1, 0),
  });
}

/* --- grid -----------------------------------------------------------
 * The game's own: a line every kilometre, and every 100 m once they are far
 * enough apart to be read. The kilometre numbers ride along the top and left
 * of whatever part of the map is on screen.
 */
function gridLine(a, b, weight, opacity) {
  return L.polyline([a, b], { color: '#fff', weight, opacity, interactive: false });
}

function drawGrid() {
  const [width, height] = MAP.size;
  const major = L.layerGroup().addTo(map);
  gridMinor = L.layerGroup();
  for (let m = 0; m <= Math.max(width, height); m += 100) {
    const layer = m % 1000 === 0 ? major : gridMinor;
    const weight = m % 1000 === 0 ? 1.2 : 0.6, opacity = m % 1000 === 0 ? 0.55 : 0.3;
    if (m <= width) gridLine(toLatLng(m, 0), toLatLng(m, height), weight, opacity).addTo(layer);
    if (m <= height) gridLine(toLatLng(0, m), toLatLng(width, m), weight, opacity).addTo(layer);
  }
  gridLabels = L.layerGroup().addTo(map);
  map.on('zoomend moveend', labelGrid);
  labelGrid();
}

function labelGrid() {
  /* 100 m lines only once they are at least ~18px apart. */
  const apart = map.latLngToContainerPoint(toLatLng(100, 0)).x - map.latLngToContainerPoint(toLatLng(0, 0)).x;
  if (Math.abs(apart) >= 18) gridMinor.addTo(map); else gridMinor.remove();
  gridLabels.clearLayers();
  /* Screen corners, not Leaflet's north/south: this map's y runs downwards. */
  const size = map.getSize(), [width, height] = MAP.size;
  const topLeft = toWorld(map.containerPointToLatLng([0, 0]));
  const bottomRight = toWorld(map.containerPointToLatLng([size.x, size.y]));
  const west = Math.max(topLeft.east, 0), east = Math.min(bottomRight.east, width);
  const top = Math.min(topLeft.north, height), bottom = Math.max(bottomRight.north, 0);
  const along = top - (top - bottom) * 0.03, across = west + (east - west) * 0.03;
  /* The left column of labels skips the row the top labels sit on. */
  const topRow = map.latLngToContainerPoint(toLatLng(west, along)).y;
  const label = (latlng, text, side) => {
    if (side && Math.abs(map.latLngToContainerPoint(latlng).y - topRow) < 20) return;
    place(latlng, text);
  };
  const place = (latlng, text) => L.marker(latlng, { interactive: false, icon: L.divIcon({
    className: '', iconSize: null, html: `<span class="grid-label">${text}</span>` }) }).addTo(gridLabels);
  /* Close in, every 100 m square gets the same 3-digit number the solution
   * panel shows; further out, every kilometre gets two. A square only part on
   * screen is labelled in the middle of the part that is. */
  const fine = map.hasLayer(gridMinor), step = fine ? 100 : 1000;
  const name = (m) => String(Math.round(m / step)).padStart(fine ? 3 : 2, '0');
  const squares = (low, high, limit, put) => {
    for (let m = Math.floor(low / step) * step; m < Math.min(high, limit); m += step) {
      const from = Math.max(m, low), to = Math.min(m + step, high);
      if (to - from >= step * 0.35) put((from + to) / 2, name(m));
    }
  };
  squares(west, east, width, (x, text) => label(toLatLng(x, along), text));
  squares(bottom, top, height, (y, text) => label(toLatLng(across, y), text, true));
}

/* --- markers -------------------------------------------------------- */
function marker(east, north, kind) {
  const icon = L.divIcon({
    className: '', iconSize: [34, 34],
    html: `<div class="marker ${kind}">${kind === 'gun' ? '📍' : '🎯'}</div>`,
  });
  const pin = L.marker(toLatLng(east, north), { icon, draggable: true, autoPan: true })
    .addTo(map);
  pin.on('drag', draw);
  pin.on('dragend', solve);
  return pin;
}

function place(east, north) {
  if (!gun) {
    gun = marker(east, north, 'gun');
    el('hint').textContent = 'Now tap the target';
  } else if (!target) {
    target = marker(east, north, 'target');
    el('hint').hidden = true;
  } else {
    target.setLatLng(toLatLng(east, north));   // the gun stays put between targets
  }
  draw();
  solve();
}

/* --- drawing -------------------------------------------------------- */
function draw() {
  if (line) { line.remove(); line = null; }
  if (label) { label.remove(); label = null; }
  drawRing();
  drawArrow();
  if (!gun || !target) return;
  const a = gun.getLatLng(), b = target.getLatLng();
  line = L.polyline([a, b], { color: '#d9a441', weight: 2 }).addTo(map);
  /* A rough label while dragging; the figure on the panel is the engine's. */
  const from = toWorld(a), to = toWorld(b);
  const metres = Math.hypot(to.east - from.east, to.north - from.north);
  label = L.marker(L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2), {
    interactive: false,
    icon: L.divIcon({ className: '', html: `<span class="range-label">${Math.round(metres)} m</span>` }),
  }).addTo(map);
}

function drawArrow() {
  if (arrow) { arrow.remove(); arrow = null; }
  if (!gun || !WIND.speed) return;
  /* The text says where the wind comes FROM; the arrow shows where the air is
   * going, which is the opposite way. */
  const travelling = (WIND.from + 180) % 360;
  arrow = L.marker(gun.getLatLng(), {
    interactive: false,
    icon: L.divIcon({
      className: '', iconSize: [30, 30],
      html: `<div class="wind-arrow" style="transform:rotate(${travelling}deg)">↑</div>`,
    }),
  }).addTo(map);
}

function drawRing() {
  if (ring) { ring.remove(); ring = null; }
  const shell = currentRound();
  if (!gun || !shell) return;
  ring = L.circle(gun.getLatLng(), {
    radius: shell.maxRange / MAP.metresPerUnit,    // the circle is drawn in map units
    color: '#d9a441', weight: 1, opacity: .5, fill: false, dashArray: '6 6',
    interactive: false,
  }).addTo(map);
}

/* --- loadout pickers ------------------------------------------------ */
const currentTube = () => TUBES.find((t) => t.key === el('tube').value);
const currentRound = () => {
  const tube = currentTube();
  return tube && tube.rounds.find((r) => r.key === el('round').value);
};

function fillTubes() {
  el('tube').innerHTML = TUBES
    .map((t) => `<option value="${t.key}">${t.label}</option>`).join('');
  fillRounds();
}

function fillRounds(keep) {
  const tube = currentTube();
  if (!tube) return;
  const wanted = keep || el('round').value;
  el('round').innerHTML = tube.rounds
    .map((r) => `<option value="${r.key}">${r.name}</option>`).join('');
  // Changing tube keeps the round in hand where that tube carries it.
  const held = tube.rounds.find((r) => r.key === wanted);
  el('round').value = held ? held.key : tube.rounds[0].key;
}

/* --- the solution --------------------------------------------------- */
function show(answer) {
  const panel = el('panel');
  panel.classList.remove('empty');
  el('loadout').textContent = `${answer.tubeName} — ${answer.roundName}`;
  el('gunGrid').textContent = answer.mortar_grid || '—';
  el('targetGrid').textContent = answer.target_grid || '—';
  el('range').textContent = `${answer.range_m.toLocaleString()} m`;
  showWind(answer);
  const problem = el('problem');
  if (answer.valid) {
    problem.hidden = true;
    el('azimuth').textContent = `${answer.azimuth_mils} mils`;
    el('ring').textContent = answer.ring;
    el('elevation').textContent = `${answer.elevation_mils} mils`;
    el('tof').textContent = `${answer.tof_seconds} sec`;
    el('dispersion').textContent = `${answer.dispersion_m} m`;
    return;
  }
  ['azimuth', 'ring', 'elevation', 'tof', 'dispersion'].forEach((id) => {
    el(id).textContent = '—';
  });
  problem.hidden = false;
  if (answer.reason === 'bad_wind') {
    problem.innerHTML = `<b>WIND NOT USABLE</b>${answer.detail || 'Check the wind figures.'}`;
  } else if (answer.reason === 'out_of_range') {
    problem.innerHTML = `<b>OUT OF RANGE</b>No valid ring reaches this target. This round
       covers ${answer.min_range_m}–${answer.max_range_m} m.`;
  } else {
    problem.innerHTML = '<b>NO SOLUTION</b>That could not be worked out. Try again in a moment.';
  }
}

/* The backend does the wind. This only prints what it sent back. */
function showWind(answer) {
  const wind = answer.wind;
  const base = el('base');
  const note = el('windNote');
  if (!wind || !wind.speed_mps) {
    base.hidden = true;
    note.hidden = true;
    return;
  }
  base.hidden = false;
  el('crosswind').textContent = `${wind.crosswind_mps.toFixed(2)} m/s`;
  el('parallel').textContent = `${wind.parallel_mps.toFixed(2)} m/s`;
  const baseSolution = answer.base_solution || {};
  el('baseAzimuth').textContent = baseSolution.azimuth_mils != null
    ? `${baseSolution.azimuth_mils} mils` : '—';
  el('baseRange').textContent = baseSolution.range_m != null
    ? `${baseSolution.range_m.toLocaleString()} m` : '—';
  el('azimuthCorr').textContent = signed(wind.azimuth_correction_weapon_mils, 'mils');
  el('rangeCorr').textContent = signed(-wind.parallel_range_correction_m, 'm');
  note.hidden = wind.applied;
  if (!wind.has_data) {
    note.textContent = `Wind from ${wind.from_degrees}° at ${wind.speed_mps} m/s, split `
      + 'against your line of fire. Reforger\'s wind table for this round has not been '
      + 'supplied yet, so no correction is applied — the settings above are still-air '
      + 'figures.';
  } else if (!wind.measured) {
    note.textContent = 'This range or wind speed is outside the samples measured for this '
      + 'round, so no correction is applied rather than reaching past them.';
  } else {
    note.textContent = '';
  }
}

const signed = (value, unit) => `${value > 0 ? '+' : ''}${value} ${unit}`;

async function solve() {
  if (!gun || !target) return;
  drawRing();
  const mine = ++pending;
  const body = {
    token: TOKEN, tube: el('tube').value, round: el('round').value,
    gun: toWorld(gun.getLatLng()), target: toWorld(target.getLatLng()),
    wind: { speed_mps: WIND.speed, from_degrees: WIND.from },
  };
  try {
    const reply = await fetch('/api/mortar/calculate', {
      method: 'POST',
      headers: CSRF ? { 'Content-Type': 'application/json', 'X-CSRF': CSRF }
                    : { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (mine !== pending) return;            // a later drag already won
    if (reply.status === 401 || reply.status === 403) {
      show({ valid: false, reason: 'expired', tubeName: '', roundName: '',
             range_m: 0, azimuth_mils: 0 });
      el('problem').innerHTML = CSRF ? '<b>LOGGED OUT</b>Log in to the panel again.'
                                     : '<b>LINK EXPIRED</b>Run /mortar in Discord for a new map link.';
      return;
    }
    show(await reply.json());
  } catch (err) {
    /* Offline or a dropped connection: leave the last good figures alone. */
  }
}

/* --- buttons -------------------------------------------------------- */
function wire() {
  el('tube').addEventListener('change', () => { fillRounds(el('round').value); draw(); solve(); });
  el('round').addEventListener('change', () => { draw(); solve(); });

  el('resetTarget').addEventListener('click', () => {
    if (target) { target.remove(); target = null; }
    el('hint').hidden = false;
    el('hint').textContent = 'Tap a new target';
    draw();
  });

  el('resetAll').addEventListener('click', () => {
    [gun, target].forEach((m) => m && m.remove());
    gun = target = null;
    draw();
    el('panel').classList.add('empty');
    el('hint').hidden = false;
    el('hint').textContent = 'Tap the map to place your mortar';
  });

  el('swap').addEventListener('click', () => {
    if (!gun || !target) return;
    const a = gun.getLatLng(), b = target.getLatLng();
    gun.setLatLng(b); target.setLatLng(a);
    draw(); solve();
  });

  const speed = el('windSpeed');
  const from = el('windFrom');

  function windChanged() {
    const typed = Number(String(speed.value).replace(',', '.')) || 0;
    WIND.speed = Math.round(Math.min(60, Math.max(0, typed)) * 10) / 10;
    WIND.from = ((Number(from.value) || 0) % 360 + 360) % 360;
    speed.value = WIND.speed;
    from.value = WIND.from;
    [...el('compass').children].forEach((button) => {
      button.classList.toggle('on', Number(button.dataset.deg) === WIND.from && WIND.speed > 0);
    });
    el('windSummary').textContent = WIND.speed ? `${WIND.speed} m/s from ${WIND.from}°` : 'none';
    drawArrow();
    solve();
  }

  el('compass').innerHTML = POINTS
    .map(([name, deg]) => `<button type="button" data-deg="${deg}">${name}</button>`).join('');
  el('compass').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button) return;
    from.value = button.dataset.deg;      // degrees stay the stored value
    if (!WIND.speed) speed.value = 1;
    windChanged();
  });
  el('windUp').addEventListener('click', () => { speed.value = (Number(speed.value) || 0) + 0.1; windChanged(); });
  el('windDown').addEventListener('click', () => { speed.value = (Number(speed.value) || 0) - 0.1; windChanged(); });
  speed.addEventListener('change', windChanged);
  speed.addEventListener('input', windChanged);
  from.addEventListener('change', windChanged);

  el('copy').addEventListener('click', async () => {
    const text = [el('loadout').textContent,
      `Mortar ${el('gunGrid').textContent} → Target ${el('targetGrid').textContent}`,
      `Range ${el('range').textContent}`,
      WIND.speed ? `Wind from ${WIND.from}° at ${WIND.speed} m/s` : 'No wind',
      `Azimuth ${el('azimuth').textContent} · Elevation ${el('elevation').textContent}`,
      `Ring ${el('ring').textContent} · Flight ${el('tof').textContent}`,
    ].join('\n');
    try {
      await navigator.clipboard.writeText(text);
      el('copy').textContent = 'Copied';
      setTimeout(() => { el('copy').textContent = 'Copy solution'; }, 1500);
    } catch (err) {
      window.prompt('Copy this', text);
    }
  });
}

/* --- start ---------------------------------------------------------- */
async function start() {
  const reply = await fetch(`/mortar/${TOKEN}/loadouts`);
  if (!reply.ok) { window.location.reload(); return; }
  const data = await reply.json();
  MAP = data.map;
  TUBES = data.tubes;
  fillTubes();
  wire();

  const bounds = L.latLngBounds(L.latLng(0, 0), L.latLng(MAP.span[1], MAP.span[0]));
  map = L.map('map', {
    crs: tileCRS(),
    minZoom: MAP.tiles ? MAP.tiles.minZoom : 0,
    maxZoom: MAP.maxZoom + EXTRA_ZOOM,
    zoomSnap: .25, attributionControl: false, tap: true,
  });

  if (MAP.tiles) {
    layer = L.tileLayer(`/mortar/${TOKEN}/tiles/{z}/{x}/{y}`, {
      tileSize: MAP.tiles.tileSize, minZoom: MAP.tiles.minZoom,
      maxNativeZoom: MAP.maxZoom, maxZoom: MAP.maxZoom + EXTRA_ZOOM,
      bounds, noWrap: true,
    }).addTo(map);
  } else {
    layer = L.imageOverlay(`/mortar/${TOKEN}/map`, bounds).addTo(map);
  }

  map.fitBounds(bounds);
  map.setMaxBounds(bounds.pad(0.2));
  drawGrid();
  map.on('click', (event) => {
    if (!bounds.contains(event.latlng)) return;
    const where = toWorld(event.latlng);
    place(where.east, where.north);
  });
  if (window.matchMedia('(hover: hover)').matches) {
    const cursor = el('cursor');
    cursor.hidden = false;
    map.on('mousemove', (event) => {
      const where = toWorld(event.latlng);
      cursor.textContent = gridText(where.east, where.north);
    });
  }
}

start();
