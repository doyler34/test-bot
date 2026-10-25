"""Finds network spikes in what the panel has been recording about the box.

Run from the panel's folder, where panel.local.json is:

    .venv/bin/python dev/network_spikes.py        # last 24 hours
    .venv/bin/python dev/network_spikes.py 72     # last 72 hours
    .venv/bin/python dev/network_spikes.py 5 02:30   # minute by minute, 40 minutes either side of 02:30 today

It only reads the panel's database. Nothing is changed or sent anywhere.
"""

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel.config import load_config  # noqa: E402
from panel.db import PanelDB  # noqa: E402

TRAFFIC = ("rx_mbps", "tx_mbps", "rx_pps", "tx_pps")
LOSS = ("rx_drop", "udp_errors", "udp_rcvbuf", "udp_sndbuf", "softnet_drop", "nic_errors", "tcp_retrans")
FACTOR = 3.0
GAP = 120


def stamp(at):
    return time.strftime("%d %b %H:%M:%S", time.localtime(at))


def spikes(rows, key):
    values = [r[key] for r in rows if r[key] is not None]
    if len(values) < 30:
        return []
    middle = statistics.median(values)
    spread = statistics.median(abs(v - middle) for v in values) or max(middle * 0.1, 1e-9)
    floor = middle + FACTOR * 3 * spread
    return [(r["at"], r[key]) for r in rows if r[key] is not None and r[key] > max(floor, middle * 2)]


def groups(points):
    out = []
    for at, value in points:
        if out and at - out[-1][-1][0] <= GAP:
            out[-1].append((at, value))
        else:
            out.append([(at, value)])
    return out


def around(rows, clock):
    """Minute by minute for 40 minutes either side of a time of day (today, the box's clock)."""
    hour, minute = (int(x) for x in clock.split(":"))
    today = time.localtime()
    centre = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, hour, minute, 0, 0, 0, -1))
    if centre > time.time():
        centre -= 86400
    cols = ("rx_mbps", "tx_mbps", "tx_pps", "udp_errors", "rx_drop", "softnet_drop", "tcp_retrans", "cpu", "load1")
    print("time    " + "".join(f"{c:>13}" for c in cols) + "   (highest in that minute)")
    minutes = {}
    for r in rows:
        if abs(r["at"] - centre) <= 2400:
            minutes.setdefault(int(r["at"] // 60), []).append(r)
    for key in sorted(minutes):
        cells = []
        for c in cols:
            values = [r[c] for r in minutes[key] if r[c] is not None]
            cells.append(f"{max(values):13.1f}" if values else f"{'-':>13}")
        print(time.strftime("%H:%M   ", time.localtime(key * 60)) + "".join(cells))


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 24
    config = load_config()
    db = PanelDB(config.database)
    since = int(time.time() - hours * 3600)
    rows = db.box_samples(since)
    if not rows:
        sys.exit(f"No box samples in the last {hours:g} hours.")
    print(f"{len(rows)} box samples, {stamp(rows[0]['at'])} to {stamp(rows[-1]['at'])}\n")
    if len(sys.argv) > 2:
        return around(rows, sys.argv[2])
    found = False
    for key in TRAFFIC:
        values = [r[key] for r in rows if r[key] is not None]
        if not values:
            continue
        middle = statistics.median(values)
        print(f"{key}: typical {middle:.1f}, highest {max(values):.1f}")
        for run in groups(spikes(rows, key))[:8]:
            peak = max(run, key=lambda p: p[1])
            found = True
            print(f"   spike {stamp(run[0][0])} to {stamp(run[-1][0])}, peak {peak[1]:.1f} at {stamp(peak[0])}")
    print()
    for key in LOSS:
        bad = [(r["at"], r[key]) for r in rows if r[key]]
        if bad:
            found = True
            print(f"{key}: {sum(v for _, v in bad):.0f} in total, worst {max(v for _, v in bad):.0f} at "
                  f"{stamp(max(bad, key=lambda p: p[1])[0])}")
    for server in config.servers:
        samples = db.server_samples(server.id, since)
        dips = [s for s in samples if s["fps"] is not None and s["fps"] < 20 and (s["players"] or 0) > 10]
        drops = [s for s in samples if s["sock_drops"]]
        if dips or drops:
            found = True
            print(f"\n{server.name}: {len(dips)} samples under 20 FPS with players on, {len(drops)} with dropped packets")
            for s in (dips + drops)[:6]:
                print(f"   {stamp(s['at'])}  fps {s['fps']}  players {s['players']}  dropped {s['sock_drops']}")
    if not found:
        print("No spikes or packet loss found in that window.")


main()
