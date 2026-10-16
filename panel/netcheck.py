"""The Network check: everything about the box's network and the game servers'
traffic, read twice ten seconds apart so what's going wrong right now stands
apart from totals that built up months ago.

Each line gets a verdict - ok, warn or bad - and a sentence saying why, and the
whole thing turns into plain text to paste to the host.
"""
import asyncio
import os
import re
import shutil
import socket
import time

from . import monitor

WINDOW = 10
OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"
STAT_KEYS = ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped",
             "tx_dropped", "rx_crc_errors", "rx_missed_errors", "rx_fifo_errors", "rx_over_errors", "collisions")
ETHTOOL_WORTH = re.compile(r"miss|drop|err|crc|over|fifo|no_buffer|discard|timeout|restart", re.I)
TARGETS = (("Cloudflare", "1.1.1.1", 443), ("Google", "8.8.8.8", 443))


def _read(path, default=""):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return default


def _int(text, default=None):
    try:
        return int(text)
    except (TypeError, ValueError):
        return default


# --- what gets read ------------------------------------------------------

def interfaces(sys_root="/sys"):
    found = {}
    base = f"{sys_root}/class/net"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return found
    for name in names:
        if name == "lo":
            continue
        here = f"{base}/{name}"
        driver = ""
        try:
            driver = os.path.basename(os.readlink(f"{here}/device/driver"))
        except OSError:
            pass
        found[name] = {
            "state": _read(f"{here}/operstate", "unknown"), "carrier": _int(_read(f"{here}/carrier")),
            "speed": _int(_read(f"{here}/speed")), "duplex": _read(f"{here}/duplex"),
            "mtu": _int(_read(f"{here}/mtu")), "carrier_changes": _int(_read(f"{here}/carrier_changes")),
            "driver": driver, "virtual": not os.path.exists(f"{here}/device"),
            "stats": {k: _int(_read(f"{here}/statistics/{k}"), 0) for k in STAT_KEYS},
        }
    return found


def default_routes(proc_root="/proc"):
    routes = []
    for line in _read(f"{proc_root}/net/route").splitlines()[1:]:
        f = line.split()
        if len(f) >= 3 and f[1] == "00000000":
            gateway = socket.inet_ntoa(bytes.fromhex(f[2])[::-1])
            routes.append({"interface": f[0], "gateway": gateway})
    return routes


def softnet(proc_root="/proc"):
    """Per CPU: packets dropped because the backlog was full, and times the
    kernel ran out of time to process packets (time_squeeze)."""
    rows = []
    for line in _read(f"{proc_root}/net/softnet_stat").splitlines():
        f = [int(x, 16) for x in line.split()]
        if len(f) >= 3:
            rows.append({"processed": f[0], "dropped": f[1], "squeezed": f[2]})
    return rows


def snmp(proc_root="/proc"):
    out = {}
    lines = _read(f"{proc_root}/net/snmp").splitlines()
    for head, values in zip(lines[::2], lines[1::2]):
        name = head.split(":")[0]
        out[name] = dict(zip(head.split()[1:], (_int(v, 0) for v in values.split()[1:])))
    return out


def sysctls(proc_root="/proc"):
    keys = {"rmem_max": "net/core/rmem_max", "rmem_default": "net/core/rmem_default",
            "wmem_max": "net/core/wmem_max", "backlog": "net/core/netdev_max_backlog",
            "conntrack": "net/netfilter/nf_conntrack_count", "conntrack_max": "net/netfilter/nf_conntrack_max"}
    return {k: _int(_read(f"{proc_root}/sys/{path}")) for k, path in keys.items()}


def interrupts(names, proc_root="/proc"):
    """Interrupt counts per CPU for each network interface's queues."""
    lines = _read(f"{proc_root}/interrupts").splitlines()
    if not lines:
        return {}
    cpus = len(lines[0].split())
    found = {}
    for line in lines[1:]:
        f = line.split()
        label = " ".join(f[cpus + 1:])
        for name in names:
            if name in label:
                counts = [_int(x, 0) for x in f[1:cpus + 1]]
                found.setdefault(name, [0] * cpus)
                found[name] = [a + b for a, b in zip(found[name], counts)]
    return found


def cpu_cores(proc_root="/proc"):
    cores = {}
    for line in _read(f"{proc_root}/stat").splitlines():
        if re.match(r"cpu\d+ ", line):
            f = line.split()
            values = [int(x) for x in f[1:9]]
            cores[f[0]] = (sum(values), values[3] + values[4])
    return cores


def game_sockets(pid, proc_root="/proc"):
    """The UDP ports one server program has open, with what's queued and what
    the kernel has dropped on each."""
    inodes = set()
    try:
        fds = os.listdir(f"{proc_root}/{pid}/fd")
    except OSError:
        return None
    for fd in fds:
        try:
            target = os.readlink(f"{proc_root}/{pid}/fd/{fd}")
        except OSError:
            continue    # closed since the listing, or the listing's own handle
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    found = []
    for table in ("udp", "udp6"):
        for line in _read(f"{proc_root}/{pid}/net/{table}").splitlines()[1:]:
            f = line.split()
            if len(f) >= 13 and f[9] in inodes:
                address, port = f[1].rsplit(":", 1)
                queued = int(f[4].split(":")[1], 16)
                found.append({"port": int(port, 16), "queued": queued, "drops": int(f[12]), "inode": f[9],
                              "any": set(address) == {"0"}})
    return sorted(found, key=lambda s: s["port"])


async def ethtool(name):
    """Ring sizes and the driver's own error counters, where ethtool is installed."""
    tool = shutil.which("ethtool")
    if not tool:
        return None

    async def run(*args):
        proc = await asyncio.create_subprocess_exec(tool, *args, name, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return out.decode(errors="replace")
    rings = await run("-g")
    numbers = re.findall(r"^RX:\s+(\d+)", rings, re.M)
    counters = {}
    for line in (await run("-S")).splitlines():
        if ":" in line:
            key, value = (p.strip() for p in line.split(":", 1))
            if ETHTOOL_WORTH.search(key) and value.isdigit():
                counters[key] = int(value)
    return {"rx_max": _int(numbers[0]) if numbers else None, "rx_now": _int(numbers[1]) if len(numbers) > 1 else None,
            "counters": counters}


def snapshot(pids, proc_root="/proc", sys_root="/sys"):
    nics = interfaces(sys_root)
    return {"at": time.time(), "nics": nics, "softnet": softnet(proc_root), "snmp": snmp(proc_root),
            "irq": interrupts(nics, proc_root), "cores": cpu_cores(proc_root),
            "sockets": {sid: game_sockets(pid, proc_root) if pid else None for sid, pid in pids.items()}}


async def connect_times(host, port, tries=10):
    """TCP connection times to somewhere well known: latency, jitter, failures."""
    times, failed = [], 0
    for _ in range(tries):
        started = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
            times.append((time.monotonic() - started) * 1000)
            writer.close()
        except (OSError, asyncio.TimeoutError):
            failed += 1
        await asyncio.sleep(0.5)
    return _spread(times, failed, tries)


async def query_times(host, port, tries=10):
    times, failed = [], 0
    for _ in range(tries):
        answer = await monitor.query(host, port, timeout=2)
        if answer["ok"]:
            times.append(answer["ms"])
        else:
            failed += 1
        await asyncio.sleep(0.5)
    return _spread(times, failed, tries)


def _spread(times, failed, tries):
    if not times:
        return {"tries": tries, "failed": failed}
    jitter = sum(abs(a - b) for a, b in zip(times, times[1:])) / max(len(times) - 1, 1)
    return {"tries": tries, "failed": failed, "avg": sum(times) / len(times), "worst": max(times),
            "best": min(times), "jitter": jitter}


# --- turning it into verdicts --------------------------------------------

def line(section, label, value, verdict=INFO, why=""):
    return {"section": section, "label": label, "value": value, "verdict": verdict, "why": why}


def judge(before, after, servers, extras):
    """Every line of the report. `servers` is [(id, name, pid, query)]; `extras`
    holds the slower checks run during the window."""
    span = max(after["at"] - before["at"], 1)
    out = []
    routes = extras["routes"]
    used = {r["interface"] for r in routes}

    s = "Network ports"
    if not routes:
        out.append(line(s, "Default route", "none", BAD, "The box has no default route: nothing can reach the internet."))
    elif len(routes) > 1:
        out.append(line(s, "Default route", ", ".join(f"{r['interface']} via {r['gateway']}" for r in routes), WARN,
                        "More than one default route: traffic can leave by a different port than it came in on."))
    else:
        out.append(line(s, "Default route", f"{routes[0]['interface']} via {routes[0]['gateway']}", OK,
                        "All traffic leaves through this port."))
    for name, nic in after["nics"].items():
        if nic["virtual"] and name not in used:
            continue
        old = before["nics"].get(name, nic)
        grew = {k: nic["stats"][k] - old["stats"][k] for k in STAT_KEYS}
        label = f"{name} ({nic['driver'] or 'virtual'})"
        if nic["state"] != "up" and name not in used:
            out.append(line(s, label, "link down", INFO,
                            "Not carrying traffic. Fine if this port was taken out of use on purpose."))
            continue
        speed = nic["speed"] if nic["speed"] and nic["speed"] > 0 else None
        mbps = (grew["rx_bytes"] + grew["tx_bytes"]) * 8 / span / 1e6
        verdict, why = OK, "Link up."
        if nic["state"] != "up":
            verdict, why = BAD, "This port carries the default route but its link is down."
        elif nic["duplex"] == "half":
            verdict, why = BAD, "Half duplex: the link and the switch have mismatched settings. Packets collide and drop."
        elif speed and speed < 1000:
            verdict, why = BAD, f"Linked at only {speed} Mb/s: a bad cable, port or negotiation."
        out.append(line(s, label, f"{nic['state']}, {speed or '?'} Mb/s {nic['duplex'] or ''}, MTU {nic['mtu']}",
                        verdict, why))
        if speed:
            share = mbps / speed
            out.append(line(s, f"{name} traffic", f"{mbps:.1f} Mb/s ({share:.0%} of the link)",
                            BAD if share > 0.8 else WARN if share > 0.5 else OK,
                            "Close to full: bursts will queue and drop." if share > 0.5 else "Plenty of room."))
        if nic["carrier_changes"] is not None:
            flaps = nic["carrier_changes"]
            out.append(line(s, f"{name} link drops since boot", str(flaps), WARN if flaps > 4 else OK,
                            "The link has gone down and up this many times: a flaky cable, port or switch."
                            if flaps > 4 else "Each boot counts a couple; more means the link is flapping."))
        live = {k: v for k, v in grew.items() if v and k not in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets")}
        total = {k: v for k, v in nic["stats"].items() if v and k not in ("rx_bytes", "tx_bytes", "rx_packets", "tx_packets")}
        if live:
            out.append(line(s, f"{name} errors right now", ", ".join(f"{k} +{v}" for k, v in live.items()), BAD,
                            "Packets were lost on this port during the test. rx_missed/fifo means the card's buffer "
                            "overflowed; crc means a bad cable or switch port."))
        else:
            out.append(line(s, f"{name} errors right now", "none in 10 s", OK, ""))
        if total:
            out.append(line(s, f"{name} errors since boot", ", ".join(f"{k} {v}" for k, v in total.items()),
                            WARN if not live else INFO, "Built up over time; only matters if it's still growing."))
        tool = extras["ethtool"].get(name)
        if tool:
            if tool["rx_now"] and tool["rx_max"]:
                small = tool["rx_now"] < tool["rx_max"]
                out.append(line(s, f"{name} receive ring", f"{tool['rx_now']} of {tool['rx_max']}",
                                WARN if small else OK,
                                f"The card holds only {tool['rx_now']} packets while waiting for the CPU. Raising it "
                                f"to {tool['rx_max']} (ethtool -G {name} rx {tool['rx_max']}) helps with bursts."
                                if small else "Already at its biggest."))
            busy = {k: v for k, v in tool["counters"].items() if v}
            if busy:
                out.append(line(s, f"{name} card counters", ", ".join(f"{k} {v}" for k, v in sorted(busy.items())),
                                WARN, "The driver's own count of trouble since boot; rx_missed / rx_no_buffer mean "
                                      "packets arrived faster than they were taken off the card."))

    s = "Kernel"
    pairs = list(zip(before["softnet"], after["softnet"]))
    dropped = sum(b["dropped"] - a["dropped"] for a, b in pairs)
    squeezed = sum(b["squeezed"] - a["squeezed"] for a, b in pairs)
    out.append(line(s, "Packets dropped by the kernel (backlog full)", f"{dropped} in 10 s", BAD if dropped else OK,
                    "Packets arrived faster than the CPU took them in." if dropped else ""))
    out.append(line(s, "Times the kernel ran out of time for packets", f"{squeezed} in 10 s",
                    WARN if squeezed > 5 else OK,
                    "The CPU handling network traffic is too busy; packets wait and arrive late." if squeezed > 5 else ""))
    udp_a, udp_b = before["snmp"].get("Udp", {}), after["snmp"].get("Udp", {})
    for key, text in (("InErrors", "UDP packets thrown away"), ("RcvbufErrors", "UDP receive buffer full"),
                      ("SndbufErrors", "UDP send buffer full")):
        grew = udp_b.get(key, 0) - udp_a.get(key, 0)
        out.append(line(s, text, f"{grew} in 10 s ({udp_b.get(key, 0)} since boot)", BAD if grew else OK,
                        "Game traffic lost on the box itself: players feel this as lag and radio cutting out." if grew else ""))
    tcp_a, tcp_b = before["snmp"].get("Tcp", {}), after["snmp"].get("Tcp", {})
    sent = tcp_b.get("OutSegs", 0) - tcp_a.get("OutSegs", 0)
    resent = tcp_b.get("RetransSegs", 0) - tcp_a.get("RetransSegs", 0)
    if sent:
        share = resent / sent
        out.append(line(s, "TCP resends", f"{resent} of {sent} ({share:.1%})", BAD if share > 0.03 else WARN if share > 0.01 else OK,
                        "Packets going missing somewhere between here and the other end." if share > 0.01 else ""))
    k = extras["sysctl"]
    if k["rmem_max"]:
        small = k["rmem_max"] < 8 * 1024 * 1024
        out.append(line(s, "Largest UDP receive buffer allowed", f"{k['rmem_max'] // 1024} KB", WARN if small else OK,
                        "Small for busy game servers: a burst can overflow it. sysctl -w net.core.rmem_max=16777216 "
                        "(and rmem_default) gives it room." if small else ""))
    if k["backlog"]:
        out.append(line(s, "Packet backlog per CPU", str(k["backlog"]), WARN if k["backlog"] < 2000 else OK,
                        "Low for a busy game box: sysctl -w net.core.netdev_max_backlog=5000." if k["backlog"] < 2000 else ""))
    if k["conntrack_max"]:
        share = (k["conntrack"] or 0) / k["conntrack_max"]
        out.append(line(s, "Connection tracking table", f"{k['conntrack']} of {k['conntrack_max']} ({share:.0%})",
                        BAD if share > 0.9 else WARN if share > 0.7 else OK,
                        "When it fills, new traffic is dropped outright: joins fail and voice cuts out." if share > 0.7 else ""))
    for name, counts in after["irq"].items():
        old = before["irq"].get(name, [0] * len(counts))
        grew = [b - a for a, b in zip(old, counts)]
        busy = [i for i, g in enumerate(grew) if g]
        total = sum(grew)
        if total:
            one = max(grew) / total
            out.append(line(s, f"{name} interrupts", f"spread over {len(busy)} of {len(grew)} cores",
                            WARN if one > 0.9 and len(grew) > 1 else OK,
                            "Nearly every packet lands on one core. If the game shares that core, packets wait: "
                            "spread them with irqbalance." if one > 0.9 and len(grew) > 1 else ""))
    busiest = []
    for core, (total, idle) in after["cores"].items():
        t0, i0 = before["cores"].get(core, (total, idle))
        if total - t0:
            busiest.append((100 * (1 - (idle - i0) / (total - t0)), core))
    if busiest:
        top, core = max(busiest)
        out.append(line(s, "Busiest CPU core", f"{core} at {top:.0f}%", BAD if top > 95 else WARN if top > 85 else OK,
                        "A core this busy delays whatever else runs on it, network traffic included." if top > 85 else ""))

    for sid, name, pid, query in servers:
        s = f"Server: {name}"
        if not pid:
            out.append(line(s, "Server program", "not found", WARN,
                            "The panel can't see this server's program, so its own ports can't be checked."))
        else:
            socks_a = {x["inode"]: x for x in before["sockets"].get(sid) or []}
            socks = after["sockets"].get(sid)
            if socks is None:
                out.append(line(s, "UDP ports", "couldn't read", WARN,
                                "The panel isn't allowed to look at this program's ports: run the panel as the same "
                                "user as the server to see them."))
            elif not socks:
                out.append(line(s, "UDP ports", "none open", BAD, "The server program has no UDP ports open: players "
                                                                  "can't reach it at all."))
            socks = socks or []
            for sock in socks:
                grew = sock["drops"] - socks_a.get(sock["inode"], sock)["drops"]
                out.append(line(s, f"UDP port {sock['port']}",
                                f"{grew} dropped in 10 s ({sock['drops']} since it opened), {sock['queued']} bytes waiting",
                                BAD if grew else WARN if sock["drops"] else OK,
                                "Packets for this port were thrown away because the game didn't read them fast enough: "
                                "exactly what cuts radio and movement." if grew else
                                "Some dropped earlier; none during the test." if sock["drops"] else
                                "Listening on every address." if sock["any"] else ""))
        result = extras["queries"].get(sid)
        if result is None:
            out.append(line(s, "Server browser check", "not set up", INFO,
                            'Add "query_host" and "query_port" for this server in panel.local.json.'))
        elif "avg" not in result:
            out.append(line(s, "Server browser check", f"no answer to {result['tries']} tries", BAD,
                            "The query port isn't answering from outside: check the port is open and forwarded."))
        else:
            lost = result["failed"] / result["tries"]
            out.append(line(s, "Server browser check",
                            f"{result['avg']:.0f} ms average, worst {result['worst']:.0f} ms, jitter {result['jitter']:.1f} ms, "
                            f"{result['failed']} of {result['tries']} unanswered",
                            BAD if lost > 0.1 else WARN if lost or result["jitter"] > 20 else OK,
                            "Answers went missing: packets are being lost on the way." if lost else
                            "The answer time jumps around: voice will stutter." if result["jitter"] > 20 else ""))

    s = "Out to the internet"
    for label, result in extras["internet"].items():
        if "avg" not in result:
            out.append(line(s, label, "unreachable", BAD, "The box couldn't connect out at all."))
            continue
        lost = result["failed"] / result["tries"]
        out.append(line(s, label, f"{result['avg']:.0f} ms average, worst {result['worst']:.0f} ms, "
                                  f"jitter {result['jitter']:.1f} ms, {result['failed']} of {result['tries']} failed",
                        BAD if lost > 0.1 else WARN if lost or result["jitter"] > 15 else OK,
                        "Connections failed: packet loss on the host's network." if lost else
                        "Latency jumps around: the host's network is congested." if result["jitter"] > 15 else ""))
    return out


async def run(servers, proc_root="/proc", sys_root="/sys"):
    """The whole check, about ten seconds. `servers` is [(id, name, pid, (host, port) or None)]."""
    pids = {sid: pid for sid, _, pid, _ in servers}
    before = await asyncio.to_thread(snapshot, pids, proc_root, sys_root)
    used = {r["interface"] for r in default_routes(proc_root)}

    async def queries():
        found = {}
        jobs = {sid: query_times(*q) for sid, _, _, q in servers if q}
        for sid, result in zip(jobs, await asyncio.gather(*jobs.values())):
            found[sid] = result
        return found

    async def internet():
        results = await asyncio.gather(*(connect_times(host, port) for _, host, port in TARGETS))
        return {f"{label} ({host})": r for (label, host, _), r in zip(TARGETS, results)}

    started = time.monotonic()
    found_queries, found_internet, tools = await asyncio.gather(
        queries(), internet(), asyncio.gather(*(ethtool(n) for n in sorted(used))))
    await asyncio.sleep(max(WINDOW - (time.monotonic() - started), 0))
    after = await asyncio.to_thread(snapshot, pids, proc_root, sys_root)
    extras = {"routes": default_routes(proc_root), "sysctl": sysctls(proc_root), "queries": found_queries,
              "internet": found_internet,
              "ethtool": {n: t for n, t in zip(sorted(used), tools) if t}}
    lines = judge(before, after, servers, extras)
    return {"at": time.time(), "lines": lines, "ethtool": bool(shutil.which("ethtool")),
            "host": socket.gethostname(), "kernel": os.uname().release}


def report_text(result, title="OYB network check"):
    marks = {OK: "OK  ", WARN: "WARN", BAD: "BAD ", INFO: "    "}
    out = [f"{title} - {time.strftime('%Y-%m-%d %H:%M %Z', time.localtime(result['at']))}",
           f"Host {result.get('host', '?')}, kernel {result.get('kernel', '?')}. Counters read twice, {WINDOW} s apart.", ""]
    section = None
    for row in result["lines"]:
        if row["section"] != section:
            section = row["section"]
            out += ["", f"== {section} =="]
        out.append(f"[{marks[row['verdict']]}] {row['label']}: {row['value']}" + (f"  - {row['why']}" if row["why"] else ""))
    return "\n".join(out).replace("\n\n\n", "\n\n")
