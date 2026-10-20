"""Running one shell command on the box for the console page.

Each command runs on its own, in a fresh shell, as the user the panel runs as.
Nothing waits for typing, so anything that asks a question (a password, y/n)
gets end-of-input and stops. Long ones are cut off after TIMEOUT seconds.
"""

import asyncio
import os
import signal
import time

TIMEOUT = 60
MAX_OUTPUT = 200_000
KEEP = 20

QUICK = [
    # top cuts the name off when it isn't on a screen, so ps it is.
    ("Game servers' CPU", "ps -o pid,%cpu,%mem,etime,args -p \"$(pgrep -d, ArmaReforgerSer)\""
                          " | sed -E 's#/home/amp/.ampdata/instances/([^/]+)/.*#\\1#'"),
    ("Memory", "free -h"),
    ("Disk", "df -h /"),
    ("Uptime and load", "uptime"),
    ("Busiest programs", "ps -eo pid,user,%cpu,%mem,etime,comm --sort=-%cpu | head -n 15"),
    ("UDP drops", "netstat -su | grep -iE 'errors|buffer'"),
]


async def run(command, timeout=TIMEOUT):
    """{command, output, code, seconds, cut} once it has finished or been stopped."""
    started = time.monotonic()
    process = await asyncio.create_subprocess_shell(
        command, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True, cwd=os.path.expanduser("~"))
    cut = False
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError:
        cut = True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        output, _ = await process.communicate()
    text = output.decode("utf-8", "replace")
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n… cut off after {MAX_OUTPUT:,} characters"
    return {"command": command, "output": text, "code": process.returncode, "cut": cut,
            "seconds": round(time.monotonic() - started, 1), "at": int(time.time())}


def remember(history, user_id, result):
    runs = history.setdefault(user_id, [])
    runs.insert(0, result)
    del runs[KEEP:]
