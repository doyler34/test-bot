"""How much memory each game server holds, read from its systemd service.

Restart mission reloads the scenario inside the same process, and Reforger
doesn't hand back everything the old mission used. Comparing against what the
same server used shortly after a clean start shows how much is left behind.
"""

import asyncio
import os
import shutil

SETTLE_SECONDS = 180
BASELINE_WINDOW = 900
LEAK_FLOOR = 512 * 1024 * 1024


def gb(value: int) -> str:
    return f"{value / 1024 ** 3:.1f} GB"


async def main_pid(unit: str) -> int:
    proc = await asyncio.create_subprocess_exec(
        shutil.which("systemctl") or "/usr/bin/systemctl", "show", "-p", "MainPID", "--value", unit,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    output, _ = await proc.communicate()
    try:
        return int(output.decode().strip() or 0)
    except ValueError:
        return 0


def process_memory(pid: int, proc_root="/proc") -> dict | None:
    try:
        with open(f"{proc_root}/{pid}/status") as fh:
            rss = next(int(line.split()[1]) * 1024 for line in fh if line.startswith("VmRSS:"))
        with open(f"{proc_root}/{pid}/stat") as fh:
            started = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open(f"{proc_root}/uptime") as fh:
            uptime = float(fh.read().split()[0])
    except (OSError, StopIteration, IndexError, ValueError):
        return None
    age = uptime - started / os.sysconf("SC_CLK_TCK")
    return {"pid": pid, "rss": rss, "age": max(int(age), 0)}


async def sample_service(unit: str) -> dict | None:
    pid = await main_pid(unit)
    return process_memory(pid) if pid else None


def box_total(proc_root="/proc") -> int:
    try:
        with open(f"{proc_root}/meminfo") as fh:
            return next(int(line.split()[1]) * 1024 for line in fh if line.startswith("MemTotal:"))
    except (OSError, StopIteration, ValueError):
        return 0


def verdict(before: int, after: int, fresh: int) -> tuple[str, str]:
    if fresh:
        extra = after - fresh
        if extra > max(LEAK_FLOOR, fresh // 5):
            return "leak", (f"Still holding {gb(extra)} more than a fresh start ({gb(after)} now, "
                            f"{gb(fresh)} fresh). Use Restart server to clear it.")
        return "ok", f"Fine: {gb(after)} now, within {gb(max(LEAK_FLOOR, fresh // 5))} of a fresh start ({gb(fresh)})."
    if before - after < before // 10:
        return "leak", (f"Only went from {gb(before)} to {gb(after)}, so the old mission's memory looks stuck. "
                        "Use Restart server to clear it.")
    return "unknown", (f"Went from {gb(before)} to {gb(after)}. There's no fresh-start reading yet to compare "
                       "with; one is taken a few minutes after the next full server restart.")
