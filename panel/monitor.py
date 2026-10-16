"""What the box and each game server are doing, every few seconds: the numbers
behind the Monitor page.

The box's own counters come from /proc - CPU, memory, every network interface
but loopback, and UDP, which is what the game itself runs on: a UDP receive
error or a full receive buffer is a packet a player sent that the server never
got, which players feel as lag and rubber-banding. The game program's CPU comes
from its own /proc entry.

The outside check asks the server's public query port the same question the
server browser and BattleMetrics ask (Steam's A2S_INFO). If the program is up
but that goes unanswered, the problem is the port or the network, not the game.
"""
import asyncio
import os
import socket
import struct
import time

A2S_INFO = b"\xff\xff\xff\xffTSource Engine Query\x00"


def _read(path):
    with open(path) as fh:
        return fh.read()


def read_box(proc_root="/proc"):
    """Raw counters for the whole machine. Rates come from two of these."""
    sample = {"at": time.time()}
    try:
        cpu = [int(v) for v in _read(f"{proc_root}/stat").splitlines()[0].split()[1:]]
        sample["cpu_total"], sample["cpu_idle"] = sum(cpu[:8]), cpu[3] + (cpu[4] if len(cpu) > 4 else 0)
    except (OSError, ValueError, IndexError):
        pass
    try:
        info = {}
        for line in _read(f"{proc_root}/meminfo").splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) * 1024
        sample["mem_total"] = info.get("MemTotal", 0)
        sample["mem_used"] = info.get("MemTotal", 0) - info.get("MemAvailable", info.get("MemFree", 0))
        sample["swap_used"] = info.get("SwapTotal", 0) - info.get("SwapFree", 0)
    except (OSError, ValueError):
        pass
    try:
        sample["load1"] = float(_read(f"{proc_root}/loadavg").split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        rx = rx_packets = rx_drop = tx = tx_packets = 0
        for line in _read(f"{proc_root}/net/dev").splitlines()[2:]:
            name, values = line.split(":", 1)
            if name.strip() == "lo":
                continue
            v = [int(x) for x in values.split()]
            rx, rx_packets, rx_drop = rx + v[0], rx_packets + v[1], rx_drop + v[3]
            tx, tx_packets = tx + v[8], tx_packets + v[9]
        sample.update(rx=rx, rx_packets=rx_packets, rx_drop=rx_drop, tx=tx, tx_packets=tx_packets)
    except (OSError, ValueError, IndexError):
        pass
    try:
        lines = [line.split() for line in _read(f"{proc_root}/net/snmp").splitlines() if line.startswith("Udp:")]
        udp = dict(zip(lines[0][1:], (int(v) for v in lines[1][1:])))
        sample["udp_errors"] = udp.get("InErrors", 0)
        sample["udp_rcvbuf"] = udp.get("RcvbufErrors", 0)
        sample["udp_sndbuf"] = udp.get("SndbufErrors", 0)
    except (OSError, ValueError, IndexError):
        pass
    return sample


def read_process(pid, proc_root="/proc"):
    """CPU time used so far by one process, in clock ticks, and its thread count."""
    try:
        fields = _read(f"{proc_root}/{pid}/stat").rsplit(")", 1)[1].split()
        return {"at": time.time(), "ticks": int(fields[11]) + int(fields[12]), "threads": int(fields[17])}
    except (OSError, ValueError, IndexError):
        return None


def _per_second(before, after, key, span):
    if key not in before or key not in after or span <= 0:
        return None
    return max(after[key] - before[key], 0) / span


def box_rates(before, after):
    """The readings worth charting, from two raw samples."""
    span = after["at"] - before["at"]
    row = {"mem_used": after.get("mem_used"), "mem_total": after.get("mem_total"),
           "swap_used": after.get("swap_used"), "load1": after.get("load1")}
    if "cpu_total" in before and "cpu_total" in after:
        total = after["cpu_total"] - before["cpu_total"]
        idle = after["cpu_idle"] - before["cpu_idle"]
        row["cpu"] = round(100 * (total - idle) / total, 1) if total > 0 else None
    for key, out, scale in (("rx", "rx_mbps", 8 / 1e6), ("tx", "tx_mbps", 8 / 1e6),
                            ("rx_packets", "rx_pps", 1), ("tx_packets", "tx_pps", 1)):
        rate = _per_second(before, after, key, span)
        row[out] = None if rate is None else round(rate * scale, 3)
    for key in ("rx_drop", "udp_errors", "udp_rcvbuf", "udp_sndbuf"):
        rate = _per_second(before, after, key, span)
        row[key] = None if rate is None else round(rate * 60, 2)  # per minute
    return row


def process_cpu(before, after):
    """The program's CPU use, where 100% is one whole core."""
    if not before or not after or after["at"] <= before["at"]:
        return None
    ticks = (after["ticks"] - before["ticks"]) / os.sysconf("SC_CLK_TCK")
    return round(100 * max(ticks, 0) / (after["at"] - before["at"]), 1)


def _string(data, at):
    end = data.index(b"\x00", at)
    return data[at:end].decode("utf-8", "replace"), end + 1


def parse_info(data):
    """The useful parts of an A2S_INFO reply."""
    if data[:5] != b"\xff\xff\xff\xffI":
        raise ValueError("not an info reply")
    at = 6
    name, at = _string(data, at)
    game_map, at = _string(data, at)
    _, at = _string(data, at)
    _, at = _string(data, at)
    players, slots = data[at + 2], data[at + 3]
    return {"name": name, "map": game_map, "players": players, "slots": slots}


class _Query(asyncio.DatagramProtocol):
    def __init__(self):
        self.replies = asyncio.Queue()

    def datagram_received(self, data, addr):
        self.replies.put_nowait(data)

    def error_received(self, exc):
        self.replies.put_nowait(exc)


async def query(host, port, timeout=3.0):
    """Ask the server what the server browser asks. Returns how long it took
    and what it said, or an error."""
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    try:
        transport, protocol = await loop.create_datagram_endpoint(_Query, remote_addr=(host, port),
                                                                  family=socket.AF_INET)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        transport.sendto(A2S_INFO)
        for _ in range(2):
            reply = await asyncio.wait_for(protocol.replies.get(), timeout)
            if isinstance(reply, Exception):
                return {"ok": False, "error": str(reply)}
            if reply[:5] == b"\xff\xff\xff\xffA":
                # Newer servers want their challenge number sent back first.
                transport.sendto(A2S_INFO + reply[5:9])
                continue
            info = parse_info(reply)
            return {"ok": True, "ms": round((time.monotonic() - started) * 1000, 1), **info}
        return {"ok": False, "error": "kept asking for a challenge"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"no answer in {timeout:.0f} s"}
    except (ValueError, IndexError) as exc:
        return {"ok": False, "error": f"odd reply: {exc}"}
    finally:
        transport.close()


def downsample(rows, since, until, keys, buckets=240, peak=()):
    """Many samples squeezed into at most `buckets` points: averages, except
    the keys in `peak`, which keep the worst value so a short spike still shows."""
    if not rows:
        return []
    width = max((until - since) / buckets, 1)
    out, group, edge = [], [], None
    for row in rows:
        slot = int((row["at"] - since) // width)
        if edge is not None and slot != edge:
            out.append(_merge(group, keys, peak))
            group = []
        edge = slot
        group.append(row)
    out.append(_merge(group, keys, peak))
    return out


def _merge(group, keys, peak):
    merged = {"at": group[-1]["at"]}
    for key in keys:
        values = [r[key] for r in group if r[key] is not None]
        if not values:
            merged[key] = None
        elif key in peak:
            merged[key] = max(values)
        else:
            merged[key] = sum(values) / len(values)
    return merged


def chart(rows, since, until, series, width=600, height=110, floor=None):
    """SVG points for one chart: each series is (key, label, class). Gaps in
    the readings break the line rather than joining across them."""
    values = [r[key] for r in rows for key, _, _ in series if r.get(key) is not None]
    if not values:
        return None
    top = max(values) * 1.15 or 1
    if floor is not None:
        top = max(top, floor)
    span = max(until - since, 1)
    lines = []
    for key, label, kind in series:
        runs, run = [], []
        for r in rows:
            if r.get(key) is None:
                if run:
                    runs.append(run)
                run = []
                continue
            run.append(f"{(r['at'] - since) / span * width:.1f},{height - r[key] / top * height:.1f}")
        if run:
            runs.append(run)
        latest = next((r[key] for r in reversed(rows) if r.get(key) is not None), None)
        lines.append({"label": label, "kind": kind, "runs": [" ".join(r) for r in runs if len(r) > 1],
                      "now": latest, "peak": max((r[key] for r in rows if r.get(key) is not None), default=None)})
    return {"lines": lines, "top": top, "width": width, "height": height}
