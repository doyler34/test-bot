/* The mortar map.
 *
 * This file draws and it asks. Every number it shows came back from the bot's
 * own ballistic engine over /api/mortar/calculate - there is no table and no
 * trigonometry for a firing solution in here, on purpose. The only maths it
 * does is turning a click into an image pixel and labelling a grid square.
 */
'use strict';

const TOKEN = document.body.dataset.token;
const el = (id) => document.getElementById(id);

let MAP = null;          // the calibration the server handed us
let TUBES = [];
let map, layer, gun = null, target = null, line = null, ring = null, label = null;
let pending = 0;

/* --- coordinates ---------------------------------------------------- */
/* Leaflet's CRS.Simple counts y upwards; the image counts it downwards. */
const toImage = (latlng) => ({ x: latlng.lng, y: MAP.height - latlng.lat });
const toLatLng = (x, y) => L.latLng(MAP.height - y, x);

function gridText(x, y) {
  const east = MAP.originWorld[0] + (x - MAP.originPixel[0]) * MAP.eastPerPixel;
  const north = MAP.originWorld[1] + (y - MAP.originPixel[1]) * MAP.northPerPixel;
  const step = Math.pow(10, 5 - MAP.digits);
  const part = (v) => String(Math.floor(v / step) % Math.pow(10, MAP.digits))
    .padStart(MAP.digits, '0');
  return `${part(east)} ${part(north)}`;
}

/* --- markers -------------------------------------------------------- */
function marker(x, y, kind) {
  const icon = L.divIcon({
    className: '', iconSize: [34, 34],
    html: `<div class="marker ${kind}">${kind === 'gun' ? '📍' : '🎯'}</div>`,
  });
  const pin = L.marker(toLatLng(x, y), { icon, draggable: true, autoPan: true })
    .addTo(map);
  pin.on('drag', draw);
  pin.on('dragend', solve);
  return pin;
}

function place(x, y) {
  if (!gun) {
    gun = marker(x, y, 'gun');
    el('hint').textContent = 'Now tap the target';
  } else if (!target) {
    target = marker(x, y, 'target');
    el('hint').hidden = true;
  } else {
    target.setLatLng(toLatLng(x, y));   // the gun stays put between targets
  }
  draw();
  solve();
}

/* --- drawing -------------------------------------------------------- */
function draw() {
  if (line) { line.remove(); line = null; }
  if (label) { label.remove(); label = null; }
  drawRing();
  if (!gun || !target) return;
  const a = gun.getLatLng(), b = target.getLatLng();
  line = L.polyline([a, b], { color: '#d9a441', weight: 2 }).addTo(map);
  const pa = toImage(a), pb = toImage(b);
  const metres = Math.hypot(pb.x - pa.x, pb.y - pa.y) * MAP.metresPerPixel;
  label = L.marker(L.latLng((a.lat + b.lat) / 2, (a.lng + b.lng) / 2), {
    interactive: false,
    icon: L.divIcon({ className: '', html: `<span class="range-label">${Math.round(metres)} m</span>` }),
  }).addTo(map);
}

function drawRing() {
  if (ring) { ring.remove(); ring = null; }
  const shell = currentRound();
  if (!gun || !shell) return;
  ring = L.circle(gun.getLatLng(), {
    radius: shell.maxRange / MAP.metresPerPixel,   // CRS.Simple: radius is in pixels
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
  el('azimuth').textContent = `${answer.azimuth_mils} mils`;
  const problem = el('problem');
  if (answer.valid) {
    problem.hidden = true;
    el('ring').textContent = answer.ring;
    el('elevation').textContent = `${answer.elevation_mils} mils`;
    el('tof').textContent = `${answer.tof_seconds} sec`;
    el('dispersion').textContent = `${answer.dispersion_m} m`;
    return;
  }
  ['ring', 'elevation', 'tof', 'dispersion'].forEach((id) => { el(id).textContent = '—'; });
  problem.hidden = false;
  problem.innerHTML = answer.reason === 'out_of_range'
    ? `<b>OUT OF RANGE</b>No valid ring reaches this target. This round covers
       ${answer.min_range_m}–${answer.max_range_m} m.`
    : '<b>NO SOLUTION</b>That could not be worked out. Try again in a moment.';
}

async function solve() {
  if (!gun || !target) return;
  drawRing();
  const mine = ++pending;
  const body = {
    token: TOKEN, tube: el('tube').value, round: el('round').value,
    gun: toImage(gun.getLatLng()), target: toImage(target.getLatLng()),
  };
  try {
    const reply = await fetch('/api/mortar/calculate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const answer = await reply.json();
    if (mine !== pending) return;            // a later drag already won
    if (reply.status === 403) {
      show({ valid: false, reason: 'expired', tubeName: '', roundName: '',
             range_m: 0, azimuth_mils: 0 });
      el('problem').innerHTML = '<b>LINK EXPIRED</b>Run /mortar in Discord for a new map link.';
      return;
    }
    show(answer);
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

  el('copy').addEventListener('click', async () => {
    const text = [el('loadout').textContent,
      `Mortar ${el('gunGrid').textContent} → Target ${el('targetGrid').textContent}`,
      `Range ${el('range').textContent} · Azimuth ${el('azimuth').textContent}`,
      `Ring ${el('ring').textContent} · Elevation ${el('elevation').textContent} · Flight ${el('tof').textContent}`,
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

  const bounds = [[0, 0], [MAP.height, MAP.width]];
  map = L.map('map', {
    crs: L.CRS.Simple, minZoom: -5, maxZoom: 3, zoomSnap: .25,
    attributionControl: false, tap: true,
  });
  layer = L.imageOverlay(`/mortar/${TOKEN}/map`, bounds).addTo(map);
  map.setMaxBounds(bounds);
  map.fitBounds(bounds);
  map.on('click', (event) => {
    const point = toImage(event.latlng);
    if (point.x < 0 || point.y < 0 || point.x > MAP.width || point.y > MAP.height) return;
    place(point.x, point.y);
  });
  if (window.matchMedia('(hover: hover)').matches) {
    const cursor = el('cursor');
    cursor.hidden = false;
    map.on('mousemove', (event) => {
      const point = toImage(event.latlng);
      cursor.textContent = gridText(point.x, point.y);
    });
  }
}

start();
