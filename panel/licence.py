"""Reactivating AMP on this machine, for when its licence stops matching the box (NoMatchingMachineId).

The key is typed into the panel and handed to ampinstmgr on its standard input. It is never
stored, never written to the audit log, and taken out of anything shown back.
"""
import asyncio
import re

KEY = re.compile(r"^[A-Za-z0-9-]{8,80}$")
AMP_USER = "amp"
TIMEOUT = 90


def scrub(text, key):
    """The last lines of what AMP said, with the key blanked out."""
    lines = text.replace(key, "***").splitlines()[-12:]
    return "\n".join(line.rstrip() for line in lines)[:1200]


def worked(code, text):
    lowered = text.lower()
    return code == 0 and "[error" not in lowered and "not a valid licence" not in lowered


async def _run(command, feed, spawn):
    proc = await spawn(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                       stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(feed.encode() if feed is not None else None), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, "AMP didn't answer in time."
    return proc.returncode, out.decode("utf-8", "replace")


async def reactivate(instance, key, spawn=asyncio.create_subprocess_exec):
    """(worked, what AMP said) for one instance. The key goes in on stdin; if AMP says it
    got no key at all, it is tried once as an argument instead."""
    base = ["sudo", "-n", "-u", AMP_USER, "ampinstmgr", "reactivate", instance]
    code, text = await _run(base, key + "\n", spawn)
    if "]  is not a valid licence" in text.replace(key, "***"):
        code, text = await _run(base + [key], None, spawn)
    text = scrub(text, key)
    return worked(code, text), text
