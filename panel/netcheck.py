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
            "wmem_max": "net/core/wmem_max", "wmem_default": "net/core/wmem_default", "backlog": "net/core/netdev_max_backlog",
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


def find_games(proc_root="/proc"):
    """Every Arma Reforger server program running on the box."""
    found = []
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        try:
            with open(f"{proc_root}/{pid}/cmdline", "rb") as fh:
                cmdline = fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        if "ArmaReforgerServer" not in cmdline:
            continue
        try:
            cwd = os.readlink(f"{proc_root}/{pid}/cwd")
        except OSError:
            cwd = ""
        found.append({"pid": int(pid), "cmdline": cmdline, "cwd": cwd})
    return found


def _folder_keys(log_dir):
    """Bits of a server's log folder path that its program's command line or
    working folder will also contain."""
    parts = [p for p in str(log_dir).split("/") if p]
    if "instances" in parts and parts.index("instances") + 1 < len(parts):
        return ["/instances/" + parts[parts.index("instances") + 1] + "/"]
    while parts and parts[-1].lower() in ("logs", "profile"):
        parts.pop()
    return ["/" + "/".join(parts) + "/"] if parts else []


def match_games(servers, log_dirs, games):
    """Fills in the program for servers the panel doesn't already know one for."""
    pids = {sid: pid for sid, _, pid, _ in servers}
    taken = {pid for pid in pids.values() if pid}
    for sid, _, pid, _ in servers:
        if pid:
            continue
        keys = _folder_keys(log_dirs.get(sid, ""))
        for game in games:
            text = game["cmdline"] + " " + game["cwd"] + "/"
            if game["pid"] not in taken and keys and any(key in text for key in keys):
                pids[sid] = game["pid"]
                taken.add(game["pid"])
                break
    return pids


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


def parse_ss(text):
    """`ss -uanme`: each UDP socket's inode with the receive and send buffer the
    kernel gave it (rb and tb in its skmem)."""
    found, record = {}, ""
    for row in text.splitlines() + [""]:
        if row[:1].isspace():
            record += " " + row.strip()
            continue
        inode = re.search(r"\bino:(\d+)", record)
        mem = re.search(r"skmem:\(([^)]*)\)", record)
        if inode and mem:
            parts = dict(re.findall(r"([a-z]+)(\d+)", mem.group(1)))
            if "rb" in parts and "tb" in parts:
                found[inode.group(1)] = {"rb": int(parts["rb"]), "tb": int(parts["tb"])}
        record = row
    return found


async def socket_buffers():
    tool = shutil.which("ss")
    if not tool:
        return None
    proc = await asyncio.create_subprocess_exec(tool, "-uanme", stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.DEVNULL)
    out, _ = await proc.communicate()
    return parse_ss(out.decode(errors="replace"))


def _size(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n // 1024} KB"


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

def line(section, label, value, verdict=INFO, why="", fix=""):
    return {"section": section, "label": label, "value": value, "verdict": verdict, "why": why, "fix": fix}


# What each network driver usually is, in words.
ONBOARD = {"igb": "Intel 1 Gb", "igc": "Intel 2.5 Gb", "e1000e": "Intel 1 Gb", "r8169": "Realtek 1 Gb",
           "tg3": "Broadcom 1 Gb"}
FAST = {"bnxt_en": "Broadcom 10/25 Gb", "ixgbe": "Intel 10 Gb", "i40e": "Intel 10/40 Gb", "ice": "Intel 25/100 Gb",
        "mlx5_core": "Mellanox 25/100 Gb", "mlx4_en": "Mellanox 10/40 Gb", "qede": "QLogic 25 Gb", "sfc": "Solarflare 10 Gb"}
MANAGEMENT = {"cdc_ether", "rndis_host"}


def port_kind(nic):
    """('fast' | 'onboard' | 'management' | 'virtual' | 'other', words)."""
    driver = nic["driver"]
    if driver in FAST:
        return "fast", f"{FAST[driver]} port"
    if driver in ONBOARD:
        return "onboard", f"motherboard {ONBOARD[driver]} port"
    if driver in MANAGEMENT:
        return "management", "management link to the BMC"
    if nic["virtual"]:
        return "virtual", "virtual port"
    return "other", f"{driver or 'unknown'} port"


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
    nics = after["nics"]
    in_use = [n for n in used if n in nics]
    fast_down = [n for n, nic in nics.items() if port_kind(nic)[0] == "fast" and nic["state"] != "up"]
    fast_up = [n for n, nic in nics.items() if port_kind(nic)[0] == "fast" and nic["state"] == "up"]
    if in_use and port_kind(nics[in_use[0]])[0] == "onboard" and fast_down and not fast_up:
        name = in_use[0]
        nic = nics[name]
        old = before["nics"].get(name, nic)
        mbps = (nic["stats"]["rx_bytes"] - old["stats"]["rx_bytes"] + nic["stats"]["tx_bytes"] - old["stats"]["tx_bytes"]) \
            * 8 / span / 1e6
        share = mbps / nic["speed"] if nic["speed"] and nic["speed"] > 0 else 0
        out.insert(1, line(s, "Running on the motherboard port",
                           f"{name} ({port_kind(nic)[1]}); the {port_kind(nics[fast_down[0]])[1]}s aren't connected",
                           WARN if share > 0.5 else INFO,
                           f"Only one port is connected, so all traffic goes through the motherboard's {nic['speed']} Mb/s "
                           f"port. You're using {share:.0%} of it, so its speed isn't the limit"
                           + ("." if share <= 0.5 else ": it's getting full, and a faster port would help.")
                           + " It does have a smaller packet buffer than the add-in ports, which the receive buffer "
                             "line below covers.",
                           "Ask the host about connecting a 10 Gb port." if share > 0.5 else ""))
    for name, nic in after["nics"].items():
        if nic["virtual"] and name not in used:
            continue
        old = before["nics"].get(name, nic)
        grew = {k: nic["stats"][k] - old["stats"][k] for k in STAT_KEYS}
        kind, words = port_kind(nic)
        label = f"{name}: {words}"
        if nic["state"] != "up" and name not in used:
            out.append(line(s, label, "not connected", INFO, "No cable in it, or not in use: normal."))
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
                out.append(line(s, f"{name} receive buffer", f"{tool['rx_now']} of {tool['rx_max']} packets",
                                WARN if small else OK,
                                f"The card only holds {tool['rx_now']} packets while it waits for the CPU; a burst "
                                "bigger than that is lost." if small else "Already at its biggest.",
                                f"ethtool -G {name} rx {tool['rx_max']}" if small else ""))
            busy = {k: v for k, v in tool["counters"].items() if v}
            damaged = {k: v for k, v in busy.items() if "csum" in k or "crc" in k}
            other = {k: v for k, v in busy.items() if k not in damaged}
            if damaged:
                out.append(line(s, f"{name} packets arriving damaged", f"{sum(damaged.values())} since boot "
                                f"({', '.join(f'{k} {v}' for k, v in sorted(damaged.items()))})", WARN,
                                "Packets reached the card with a bad checksum: corrupted on the way, usually by a faulty "
                                "cable, switch port or transceiver. Damaged game packets are thrown away.",
                                "Send this to the host: they can check the cable and switch port on their side."))
            if other:
                out.append(line(s, f"{name} card counters", ", ".join(f"{k} {v}" for k, v in sorted(other.items())),
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
    for key, text, way in (("InErrors", "Incoming UDP packets thrown away", "in"),
                           ("SndbufErrors", "Outgoing UDP packets thrown away", "out")):
        grew, ever = udp_b.get(key, 0) - udp_a.get(key, 0), udp_b.get(key, 0)
        where = ("players' packets reached the box but were thrown away before the game read them" if way == "in"
                 else "the game's own packets to players were thrown away before they left the box")
        fix = ("sysctl -w net.core.rmem_max=16777216 net.core.rmem_default=4194304" if way == "in"
               else "sysctl -w net.core.wmem_max=16777216 net.core.wmem_default=4194304")
        raised = (extras["sysctl"].get("rmem_default" if way == "in" else "wmem_default") or 0) >= 1024 * 1024
        if raised:
            fix = ("The box already allows bigger buffers: restart the game servers so they use them. Each server's "
                   "\"Buffers it's using\" line shows which still need it.")
        full = udp_b.get("RcvbufErrors", 0) if way == "in" else ever
        reason = f" ({full:,} because the buffer was full)" if way == "in" and full else ""
        out.append(line(s, text, f"{grew:,} in 10 s, {ever:,} since boot{reason}",
                        BAD if grew else WARN if ever > 1000 and not raised else OK,
                        f"Right now, {where}: players feel this as lag, rubber-banding and radio cutting out." if grew else
                        f"None during the test. The {ever:,} are counted since boot, so they include the time before the "
                        "buffers were raised." if ever > 1000 and raised else
                        f"Not during the test, but {ever:,} times since boot {where}. It happens in bursts, and voice "
                        "is the first thing to break up." if ever > 1000 else "", fix if grew or (ever > 1000 and not raised) else ""))
    tcp_a, tcp_b = before["snmp"].get("Tcp", {}), after["snmp"].get("Tcp", {})
    sent = tcp_b.get("OutSegs", 0) - tcp_a.get("OutSegs", 0)
    resent = tcp_b.get("RetransSegs", 0) - tcp_a.get("RetransSegs", 0)
    if sent:
        share = resent / sent
        few = sent < 1000
        verdict = OK if share <= 0.01 else WARN if few or share <= 0.03 else BAD
        out.append(line(s, "TCP resends", f"{resent} of {sent} ({share:.1%})", verdict,
                        ("Packets going missing between here and the other end"
                         + (" (only a small sample in 10 s: run it again to be sure)." if few else ".")) if share > 0.01 else "",
                        "Send this to the host with the damaged-packet count." if verdict != OK else ""))
    k = extras["sysctl"]
    for key, text, fix in (("rmem_max", "Largest incoming UDP buffer allowed", "sysctl -w net.core.rmem_max=16777216"),
                           ("wmem_max", "Largest outgoing UDP buffer allowed", "sysctl -w net.core.wmem_max=16777216")):
        if k[key]:
            small = k[key] < 8 * 1024 * 1024
            out.append(line(s, text, f"{k[key] // 1024} KB", WARN if small else OK,
                            "Small for busy game servers: a burst of traffic overflows it and packets are lost."
                            if small else "", fix if small else ""))
    if k["backlog"]:
        out.append(line(s, "Packet backlog per CPU", f"{k['backlog']} packets", WARN if k["backlog"] < 2000 else OK,
                        "Low for a busy game box: a burst can overflow it." if k["backlog"] < 2000 else "",
                        "sysctl -w net.core.netdev_max_backlog=5000" if k["backlog"] < 2000 else ""))
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
                            "The panel couldn't find this server's program running, so its own ports weren't checked. "
                            "It may be stopped, or running as a different user the panel can't see.",
                            'Set "service" for this server in panel.local.json, or run the panel as root.'))
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
            buffers = extras.get("buffers")
            sized = [buffers[x["inode"]] for x in socks if buffers and x["inode"] in buffers]
            if sized:
                rb, tb = min(b["rb"] for b in sized), min(b["tb"] for b in sized)
                small = min(rb, tb) < 1024 * 1024
                raised = min(k.get("rmem_default") or 0, k.get("wmem_default") or 0) >= 1024 * 1024
                out.append(line(s, "Buffers it's using", f"receive {_size(rb)}, send {_size(tb)}", WARN if small else OK,
                                ("The box allows bigger buffers now, but this server started before they were set: "
                                 "it picks them up the next time it restarts." if raised else
                                 "Small for a busy game server: a burst overflows it and radio and movement break up.")
                                if small else "",
                                "Restart this server when it's quiet." if small and raised else
                                "Raise the buffers (the Kernel lines above), then restart this server." if small else ""))
            elif buffers is None and socks:
                out.append(line(s, "Buffers it's using", "couldn't read", INFO,
                                "Install iproute2 (it has the ss command) to see each server's buffers."))
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


async def run(servers, proc_root="/proc", sys_root="/sys", log_dirs=None):
    """The whole check, about ten seconds. `servers` is [(id, name, pid, (host, port) or None)];
    a server without a pid is matched to a running game program by its log folder."""
    games = await asyncio.to_thread(find_games, proc_root)
    pids = match_games(servers, log_dirs or {}, games)
    servers = [(sid, name, pids.get(sid) or 0, query) for sid, name, _, query in servers]
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
    found_queries, found_internet, tools, buffers = await asyncio.gather(
        queries(), internet(), asyncio.gather(*(ethtool(n) for n in sorted(used))), socket_buffers())
    await asyncio.sleep(max(WINDOW - (time.monotonic() - started), 0))
    after = await asyncio.to_thread(snapshot, pids, proc_root, sys_root)
    extras = {"routes": default_routes(proc_root), "sysctl": sysctls(proc_root), "queries": found_queries,
              "internet": found_internet, "buffers": buffers,
              "ethtool": {n: t for n, t in zip(sorted(used), tools) if t}}
    lines = judge(before, after, servers, extras)
    spare = [g for g in games if g["pid"] not in pids.values()]
    for game in spare:
        lines.append(line("Network ports", f"Game program {game['pid']}", "running, not matched to a server", INFO,
                          "An Arma Reforger server the panel doesn't have in panel.local.json."))
    ports = []
    for name, nic in after["nics"].items():
        kind, words = port_kind(nic)
        if kind == "virtual" and name not in used:
            continue
        ports.append({"name": name, "kind": kind, "words": words, "up": nic["state"] == "up", "in_use": name in used,
                      "speed": nic["speed"] if nic["speed"] and nic["speed"] > 0 else None})
    ports.sort(key=lambda port: (not port["in_use"], port["kind"] != "fast", port["name"]))
    return {"at": time.time(), "lines": lines, "ethtool": bool(shutil.which("ethtool")), "ports": ports,
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
