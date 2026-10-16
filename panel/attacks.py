"""Failed logins on the box - SSH, AMP and this panel - grouped by where they
came from, so someone hammering a login shows up and gets reported.

SSH failures come from the system's auth log, AMP's from its own log files. Both
are read from where the last read stopped, so each line counts once. What's
exposed is read from /proc: every TCP port listening on all addresses.
"""
import glob
import os
import re
import shutil
import subprocess
import time
from datetime import datetime

AUTH_LOGS = ("/var/log/auth.log", "/var/log/secure")
AMP_LOGS = "/home/amp/.ampdata/instances/*/AMP_Logs/*.log"
# On the first read of a big file, only its tail matters.
FIRST_READ = 2 * 1024 * 1024

IP = r"(\d{1,3}(?:\.\d{1,3}){3}|[0-9a-fA-F:]{3,39})"
SSH = (
    re.compile(rf"Failed (?:password|publickey) for (?:invalid user )?(\S+) from {IP}"),
    re.compile(rf"Invalid user (\S*) from {IP}"),
    re.compile(rf"authentication failure;.*rhost={IP}(?:\s+user=(\S+))?"),
    re.compile(rf"Connection closed by authenticating user (\S+) {IP}"),
    re.compile(rf"Disconnected from invalid user (\S*) {IP}"),
)
AMP_FAILED = re.compile(r"(fail|invalid|incorrect|denied|bad)", re.I)
AMP_LOGIN = re.compile(r"(login|log in|logon|auth|password|credential)", re.I)
AMP_IP = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
AMP_USER = re.compile(r"user(?:name)?\s*[:=]?\s*'?\"?([A-Za-z0-9_.@-]{1,64})", re.I)
SYSLOG_TIME = re.compile(r"^([A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2})")
ISO_TIME = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def _when(line, fallback):
    if match := ISO_TIME.match(line):
        try:
            return datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S").timestamp()
        except ValueError:
            return fallback
    if match := SYSLOG_TIME.match(line):
        try:
            stamp = datetime.strptime(f"{datetime.now().year} {match[1]}", "%Y %b %d %H:%M:%S").timestamp()
            return stamp - 365 * 86400 if stamp > fallback + 86400 else stamp
        except ValueError:
            return fallback
    return fallback


def ssh_attempt(line, fallback):
    if "sshd" not in line:
        return None
    for pattern in SSH:
        if match := pattern.search(line):
            groups = match.groups()
            if pattern is SSH[2]:
                ip, user = groups[0], groups[1] or ""
            else:
                user, ip = groups
            return {"at": _when(line, fallback), "ip": ip, "source": "SSH", "user": user[:64]}
    return None


def amp_attempt(line, fallback):
    if not (AMP_LOGIN.search(line) and AMP_FAILED.search(line)):
        return None
    ip = AMP_IP.search(line)
    if not ip:
        return None
    user = AMP_USER.search(line)
    return {"at": _when(line, fallback), "ip": ip[1], "source": "AMP", "user": user[1] if user else ""}


class Reader:
    """Remembers where it got to in each log file."""

    def __init__(self, auth_logs=AUTH_LOGS, amp_logs=AMP_LOGS):
        self.auth_logs, self.amp_logs = auth_logs, amp_logs
        self.positions: dict[str, tuple[int, int]] = {}
        self.unreadable: set[str] = set()

    def _new_lines(self, path):
        try:
            stat = os.stat(path)
            with open(path, "rb") as fh:
                inode, at = self.positions.get(path, (stat.st_ino, max(stat.st_size - FIRST_READ, 0)))
                if inode != stat.st_ino or at > stat.st_size:
                    at = 0    # rotated or truncated
                fh.seek(at)
                data = fh.read()
                self.positions[path] = (stat.st_ino, at + len(data))
            self.unreadable.discard(path)
        except PermissionError:
            self.unreadable.add(path)
            return []
        except OSError:
            return []
        return data.decode(errors="replace").splitlines()

    def read(self):
        found, now = [], time.time()
        for path in self.auth_logs:
            for text in self._new_lines(path):
                if attempt := ssh_attempt(text, now):
                    found.append(attempt)
        for path in sorted(glob.glob(self.amp_logs))[-20:]:
            for text in self._new_lines(path):
                if attempt := amp_attempt(text, now):
                    found.append(attempt)
        return found


def open_ports(proc_root="/proc"):
    """TCP ports listening on every address, so reachable from outside unless a
    firewall says otherwise."""
    ports = set()
    for table, anywhere in (("tcp", "00000000"), ("tcp6", "00000000000000000000000000000000")):
        try:
            with open(f"{proc_root}/net/{table}") as fh:
                rows = fh.read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) > 3 and f[3] == "0A":     # LISTEN
                address, port = f[1].rsplit(":", 1)
                if address == anywhere:
                    ports.add(int(port, 16))
    return sorted(ports)


KNOWN_PORTS = {22: "SSH", 8080: "AMP panel (default)", 8081: "AMP", 80: "web", 443: "web (HTTPS)", 3306: "MySQL",
               5432: "PostgreSQL", 21: "FTP", 2223: "AMP SFTP", 2224: "AMP SFTP", 3389: "Remote Desktop",
               6379: "Redis", 27017: "MongoDB", 19999: "Reforger RCON (often)", 8085: "OYB mortar map"}


def protections():
    """Is fail2ban running, and does SSH still take passwords?"""
    out = {"fail2ban": None, "ssh_passwords": None}
    tool = shutil.which("systemctl")
    if tool:
        try:
            state = subprocess.run([tool, "is-active", "fail2ban"], capture_output=True, text=True, timeout=5)
            out["fail2ban"] = state.stdout.strip() == "active"
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        text = open("/etc/ssh/sshd_config").read()
        for extra in glob.glob("/etc/ssh/sshd_config.d/*.conf"):
            text = open(extra).read() + "\n" + text    # included files are read first and win
        setting = re.search(r"^\s*PasswordAuthentication\s+(yes|no)", text, re.M | re.I)
        out["ssh_passwords"] = setting[1].lower() == "yes" if setting else True
    except OSError:
        pass
    return out
