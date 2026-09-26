import asyncio
import logging
import re
import secrets
import shutil
import time
from pathlib import Path

import jinja2
from aiohttp import web

from . import archive, auth, memory
from .alerts import GOLD, GREEN, RED
from .config import PanelConfig
from .connections import LogReader, folder_start
from .db import PanelDB, now
from .oyb_stats import OybStats
from .rcon import RconError
from .servers import POWER_RCON, POWER_SERVICE, ServerManager, clean, valid_identity

log = logging.getLogger("panel.web")

HERE = Path(__file__).parent
ASSET_VERSION = str(int(max((HERE / "static" / n).stat().st_mtime for n in ("style.css", "app.js"))))
BRAND = HERE.parent / "assets" / "rank-card"
COOKIE = "oyb_panel"
PUBLIC = ("/login", "/static/", "/brand/")
DURATIONS = [("3600", "1 hour"), ("86400", "1 day"), ("604800", "7 days"),
             ("2592000", "30 days"), ("0", "Permanent")]
POWER_LABELS = {
    "restart_mission": "Restart mission",
    "shutdown": "Shut down (RCON)",
    "start": "Start server",
    "stop": "Stop server",
    "restart": "Restart server",
}

CONFIG = web.AppKey("config", PanelConfig)
DB = web.AppKey("db", PanelDB)
MANAGER = web.AppKey("manager", ServerManager)
THROTTLE = web.AppKey("throttle", auth.LoginThrottle)
FLASH = web.AppKey("flash", dict)
JINJA = web.AppKey("jinja", jinja2.Environment)
STATS = web.AppKey("stats", OybStats)
USER = web.RequestKey("user", object) if hasattr(web, "RequestKey") else "user"
CSRF = web.RequestKey("csrf", str) if hasattr(web, "RequestKey") else "csrf"


def _ts(value):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(value)) if value else "—"


def _ago(value):
    if not value:
        return "never"
    seconds = max(now() - int(value), 0)
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return "just now"


def _span(seconds):
    seconds = int(seconds or 0)
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}m"
    return f"{seconds // 60}m"


def _clock(value):
    if not value:
        return ""
    if time.strftime("%Y%m%d", time.localtime(value)) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M:%S", time.localtime(value))
    return time.strftime("%d %b %H:%M", time.localtime(value))


def _hms(value):
    return time.strftime("%H:%M:%S", time.localtime(value)) if value else ""


def _until(value):
    if not value:
        return "Permanent"
    seconds = int(value) - now()
    if seconds <= 0:
        return "Expired"
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{unit} left"
    return "under a minute"


def render(request, template, **context):
    user = request.get(USER)
    context.update(
        box_total=request.app[MANAGER].box_total,
        asset_version=ASSET_VERSION,
        oyb=request.app[STATS].player,
        settle_minutes=request.app[MANAGER].settle // 60,
        user=user,
        csrf=request.get(CSRF, ""),
        can=(lambda perm: bool(user) and auth.can(user["role"], perm)),
        servers=request.app[CONFIG].servers,
        path=request.path,
        flashes=request.app[FLASH].pop(user["id"], []) if user and not template.startswith("_") else [],
    )
    html = request.app[JINJA].get_template(template).render(**context)
    return web.Response(text=html, content_type="text/html")


def flash(request, message, kind="ok"):
    request.app[FLASH].setdefault(request[USER]["id"], []).append((kind, message))


def require(request, permission):
    if not auth.can(request[USER]["role"], permission):
        raise web.HTTPForbidden(text="You do not have permission for that.")


def audit(request, action, server="", target="", detail="", ok=True):
    request.app[DB].log(request[USER]["username"], action, server, target, detail, ok)


def client_ip(request):
    return request.headers.get("X-Forwarded-For", request.remote or "").split(",")[-1].strip()


def alert(request, title, description="", colour=GOLD, fields=()):
    by = request[USER]["username"]
    request.app[MANAGER].alerts.send(title, description, colour, [*fields, ("By", by)])


def memory_chart(samples, since, until, fresh=0, width=600, height=120):
    if len(samples) < 2:
        return None
    top = max(max(r["rss"] for r in samples), fresh) * 1.1 or 1
    span = max(until - since, 1)
    xy = [((r["at"] - since) / span * width, height - r["rss"] / top * height) for r in samples]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    return {
        "line": line,
        "area": f"{xy[0][0]:.1f},{height} {line} {xy[-1][0]:.1f},{height}",
        "fresh": height - fresh / top * height if fresh else None,
        "top": top, "peak": max(r["rss"] for r in samples), "width": width, "height": height,
    }


def server_or_404(request, server_id):
    state = request.app[MANAGER].state(server_id)
    if state is None:
        raise web.HTTPNotFound(text="No such server.")
    return state


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; form-action 'self'",
}


@web.middleware
async def security(request, handler):
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(SECURITY_HEADERS)
        raise
    response.headers.update(SECURITY_HEADERS)
    return response


@web.middleware
async def session(request, handler):
    db = request.app[DB]
    user, csrf = auth.session_user(db, request.cookies.get(COOKIE))
    request[USER], request[CSRF] = user, csrf
    if request.path.startswith(PUBLIC):
        return await handler(request)
    if user is None:
        if request.path.startswith("/api/") or request.path.endswith((".part", ".json")):
            raise web.HTTPUnauthorized(text="Log in again.")
        raise web.HTTPFound("/login")
    if request.method == "POST" and request.path.endswith("/history/upload"):
        # Streamed straight to disk, so the token rides in the URL instead of the form.
        if not csrf or request.query.get("csrf") != csrf:
            raise web.HTTPForbidden(text="This form expired. Go back, reload the page and try again.")
    elif request.method == "POST":
        form = await request.post()
        sent = request.headers.get("X-CSRF") or form.get("csrf", "")
        if not csrf or sent != csrf:
            raise web.HTTPForbidden(text="This form expired. Go back, reload the page and try again.")
    if user["must_change"] and request.path not in ("/account", "/logout"):
        raise web.HTTPFound("/account")
    return await handler(request)


# login

async def login_page(request):
    if request[USER]:
        raise web.HTTPFound("/")
    return render(request, "login.html", error="")


async def login(request):
    form = await request.post()
    username = clean(form.get("username", ""), 64)
    password = form.get("password", "")
    db, throttle = request.app[DB], request.app[THROTTLE]
    if throttle.blocked(username):
        db.log(username or "?", "login blocked", detail=client_ip(request), ok=False)
        return render(request, "login.html", error="Too many wrong passwords. Try again in 15 minutes.")
    row = db.user_by_name(username)
    good = auth.check_password(password, row["password_hash"] if row else auth.DUMMY_HASH)
    if not row or not good or row["disabled"]:
        throttle.failed(username)
        db.log(username or "?", "login failed", detail=client_ip(request), ok=False)
        return render(request, "login.html", error="Wrong username or password.")
    throttle.cleared(username)
    token = auth.start_session(db, row["id"])
    db.log(row["username"], "login", detail=client_ip(request))
    response = web.HTTPFound("/account" if row["must_change"] else "/")
    response.set_cookie(COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True,
                        secure=request.app[CONFIG].cookie_secure, samesite="Lax", path="/")
    raise response


async def logout(request):
    auth.end_session(request.app[DB], request.cookies.get(COOKIE))
    response = web.HTTPFound("/login")
    response.del_cookie(COOKIE, path="/")
    raise response


async def account_page(request):
    return render(request, "account.html", error="")


async def account(request):
    form = await request.post()
    db, user = request.app[DB], request[USER]
    if not auth.check_password(form.get("current", ""), user["password_hash"]):
        return render(request, "account.html", error="Your current password is wrong.")
    new = form.get("new", "")
    problem = auth.password_problem(new)
    if not problem and new != form.get("confirm", ""):
        problem = "The two new passwords do not match."
    if problem:
        return render(request, "account.html", error=problem)
    db.write("UPDATE users SET password_hash = ?, must_change = 0 WHERE id = ?", auth.hash_password(new), user["id"])
    audit(request, "changed own password")
    flash(request, "Password changed.")
    raise web.HTTPFound("/")


# servers

def server_view(state):
    return {
        "id": state.config.id,
        "name": state.config.name,
        "configured": state.config.configured,
        "online": state.online,
        "error": state.error,
        "players": state.players,
        "raw_players": state.raw_players,
        "updated": state.updated,
        "online_since": state.online_since,
        "service": state.config.service,
        "log_dir": state.config.log_dir,
        "pid": state.pid,
        "memory": state.memory,
        "process_age": state.process_age,
        "fresh": state.fresh,
        "check": state.check,
        "ping_ms": state.ping_ms,
        "fps": state.fps if now() - state.fps_at < 180 else None,
    }


async def dashboard(request):
    states = [server_view(s) for s in request.app[MANAGER].states.values()]
    return render(request, "dashboard.html", cards=states)


async def dashboard_part(request):
    states = [server_view(s) for s in request.app[MANAGER].states.values()]
    return render(request, "_cards.html", cards=states)


async def server_page(request):
    state = server_or_404(request, request.match_info["id"])
    actions = list(POWER_RCON) + (list(POWER_SERVICE) if state.config.service else [])
    db = request.app[DB]
    recent = db.audit(server=state.config.id, limit=15)
    return render(request, "server.html", s=server_view(state), actions=actions, labels=POWER_LABELS,
                  recent=recent, health=health_data(db, state), alts=alt_flags(request, state),
                  feed=db.feed(state.config.id), **history(request, state))


def history(request, state):
    """Games that started on the chosen day, or in the last week when none is chosen."""
    day = request.query.get("day", "")
    try:
        start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        since, until = int(start), int(start) + 86400
    except ValueError:
        day, since, until = "", now() - 7 * 86400, now() + 86400
    games = [dict(g) for g in request.app[DB].games(state.config.id, since, until)]
    live = None
    if state.config.log_dir:
        folders = LogReader(state.config.log_dir).folders()
        if folders and folders[-1].name not in {g["folder"] for g in games}:
            started = folder_start(folders[-1])
            if since <= started < until:
                live = {"folder": folders[-1].name, "started": started}
    return {"games": games, "live": live, "day": day, "today": time.strftime("%Y-%m-%d")}


def game_file(request, state, folder):
    if not archive.valid_folder(folder):
        raise web.HTTPNotFound(text="No such game.")
    row = request.app[DB].archived_game(state.config.id, folder)
    if row and Path(row["path"]).is_file():
        return Path(row["path"]), True
    if state.config.log_dir:
        path = Path(state.config.log_dir, folder)
        if (path / "console.log").is_file():
            return path, False
    raise web.HTTPNotFound(text="That game's log isn't there any more.")


async def game_page(request):
    state = server_or_404(request, request.match_info["id"])
    folder = request.match_info["folder"]
    path, archived = game_file(request, state, folder)
    info = path.stat() if archived else (path / "console.log").stat()
    settings = tuple(sorted(request.app[MANAGER].config.suspicion.items()))
    game = await asyncio.to_thread(archive.read_game, str(path), folder, info.st_size, info.st_mtime, settings)
    admin = [{"at": r["at"], "kind": "admin", "ip": "",
              "text": f"{r['username']}: {r['action']}" + (f" {r['target']}" if r["target"] else "")
                      + (f" ({r['detail']})" if r["detail"] else "")}
             for r in request.app[DB].audit_between(state.config.id, game["started"], game["ended"])]
    feed = sorted(game["feed"] + admin, key=lambda e: e["at"])
    return render(request, "game.html", s=server_view(state), folder=folder, game=game, feed=feed,
                  archived=archived, size=info.st_size)


UPLOAD_LIMIT = 200 * 1024 * 1024
FOLDER_NAME = re.compile(r"logs_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")


async def upload_log(request):
    """Adds a console.log from anywhere to a server's History, e.g. one saved before the panel ran."""
    require(request, "ips")
    state = server_or_404(request, request.match_info["id"])
    back = f"/server/{state.config.id}#history"
    reader = await request.multipart()
    part = await reader.next()
    while part is not None and part.name != "log":
        part = await reader.next()
    if part is None or not part.filename:
        flash(request, "Choose a console.log to upload.", "error")
        raise web.HTTPFound(back)
    manager = request.app[MANAGER]
    staging = manager.archive_root / state.config.id / ".upload"
    staging.mkdir(parents=True, exist_ok=True)
    temp = staging / f"{secrets.token_hex(8)}.log"
    size = 0
    try:
        with open(temp, "wb") as out:
            while chunk := await part.read_chunk(1 << 20):
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    flash(request, "That file is over 200 MB.", "error")
                    raise web.HTTPFound(back)
                out.write(chunk)
        with open(temp, "rb") as fh:
            head = fh.read(4096).decode("utf-8", "replace")
        match = FOLDER_NAME.search(head) or FOLDER_NAME.search(part.filename)
        if not match:
            flash(request, "Couldn't tell when that game started. Upload the console.log from a logs_<date> folder.",
                  "error")
            raise web.HTTPFound(back)
        folder = match[0]
        if request.app[DB].archived_game(state.config.id, folder):
            flash(request, f"{folder} is already in History.", "error")
            raise web.HTTPFound(back)
        game_dir = staging / folder
        game_dir.mkdir(exist_ok=True)
        temp.replace(game_dir / "console.log")
        dest = manager.archive_root / state.config.id / f"{folder}.tar.gz"
        try:
            game = await asyncio.to_thread(archive.archive_game, game_dir, dest)
        finally:
            shutil.rmtree(game_dir, ignore_errors=True)
        request.app[DB].add_archive(state.config.id, folder, str(dest), dest.stat().st_size, game)
    finally:
        temp.unlink(missing_ok=True)
    audit(request, "upload log", server=state.config.id, target=folder, detail=f"{size // 1024} KB")
    raise web.HTTPFound(f"/server/{state.config.id}/game/{folder}")


async def game_log(request):
    require(request, "ips")
    state = server_or_404(request, request.match_info["id"])
    folder = request.match_info["folder"]
    path, archived = game_file(request, state, folder)
    body = await asyncio.to_thread(path.read_bytes if archived else lambda: archive.pack_bytes(path))
    name = f"{state.config.id}-{folder}.tar.gz"
    audit(request, "download log", server=state.config.id, target=folder)
    return web.Response(body=body, content_type="application/gzip",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})


def health_data(db, state):
    t = now()
    return {
        "day": db.uptime(state.config.id, t - 86400),
        "week": db.uptime(state.config.id, t - 7 * 86400),
        "events": db.events(state.config.id, 12, ("started", "crashed", "stopped", "online", "offline")),
        "crashes": len([e for e in db.events(state.config.id, 200, ("crashed",)) if e["at"] > t - 7 * 86400]),
        "chart": memory_chart(db.memory(state.config.id, t - 86400), t - 86400, t, state.fresh),
    }


async def summary_part(request):
    state = server_or_404(request, request.match_info["id"])
    return render(request, "_summary.html", s=server_view(state), health=health_data(request.app[DB], state))


async def feed_part(request):
    state = server_or_404(request, request.match_info["id"])
    return render(request, "_feed.html", s=server_view(state), feed=request.app[DB].feed(state.config.id))


def alt_flags(request, state):
    if not auth.can(request[USER]["role"], "ips"):
        return {}
    return request.app[DB].alt_summary([p["identity"] for p in state.players if p["identity"]])


async def players_part(request):
    state = server_or_404(request, request.match_info["id"])
    return render(request, "_players.html", s=server_view(state), alts=alt_flags(request, state))


async def memory_part(request):
    state = server_or_404(request, request.match_info["id"])
    return render(request, "_memory.html", s=server_view(state))


async def status_api(request):
    return web.json_response([
        {"id": v["id"], "name": v["name"], "online": v["online"], "players": len(v["players"]), "error": v["error"]}
        for v in map(server_view, request.app[MANAGER].states.values())
    ])


async def kick(request):
    require(request, "kick")
    state = server_or_404(request, request.match_info["id"])
    form = await request.post()
    player, name = form.get("player", ""), clean(form.get("name", ""), 64)
    try:
        await request.app[MANAGER].kick(state.config.id, player)
    except RconError as exc:
        audit(request, "kick", state.config.id, name or player, str(exc), ok=False)
        flash(request, f"Kick failed: {exc}", "error")
    else:
        audit(request, "kick", state.config.id, name or player)
        alert(request, f"Kick on {state.config.name}", f"**{name or player}** was kicked.")
        flash(request, f"Kicked {name or player}.")
    raise web.HTTPFound(f"/server/{state.config.id}")


async def power(request):
    require(request, "power")
    state = server_or_404(request, request.match_info["id"])
    form = await request.post()
    action = form.get("action", "")
    if action not in POWER_LABELS:
        raise web.HTTPBadRequest(text="Unknown action.")
    manager = request.app[MANAGER]
    if action == "restart_mission":
        await manager.start_mission_check(state.config.id, request[USER]["username"])
    try:
        result = await manager.power(state.config.id, action)
    except RconError as exc:
        state.check = None
        audit(request, POWER_LABELS[action].lower(), state.config.id, detail=str(exc), ok=False)
        flash(request, f"{POWER_LABELS[action]} failed: {exc}", "error")
    else:
        audit(request, POWER_LABELS[action].lower(), state.config.id, detail=clean(result, 300))
        alert(request, f"{POWER_LABELS[action]}: {state.config.name}",
              colour=RED if action in ("stop", "shutdown") else GOLD)
        note = ""
        if action == "restart_mission" and state.check:
            note = f" Memory check in {manager.settle // 60} minutes."
        flash(request, f"{POWER_LABELS[action]}: sent.{note}")
    raise web.HTTPFound(f"/server/{state.config.id}")


# bans

async def bans_page(request):
    db = request.app[DB]
    show_old = request.query.get("all") == "1"
    rows = [dict(b, synced=db.synced(b["id"]), ips=db.ban_ips(b["id"])) for b in db.bans(include_old=show_old)]
    prefill = {k: clean(request.query.get(k, ""), 64) for k in ("identity", "name")}
    return render(request, "bans.html", bans=rows, show_old=show_old, durations=DURATIONS,
                  prefill=prefill, error="")


async def add_ban(request):
    require(request, "ban")
    form = await request.post()
    db, manager = request.app[DB], request.app[MANAGER]
    typed = clean(form.get("player", ""), 64)
    identity = form.get("identity", "").strip().lower()
    name = ""
    reason = clean(form.get("reason", ""))
    duration = form.get("duration", "")
    if valid_identity(typed.lower()):
        identity = typed.lower()
    elif valid_identity(identity):
        name = typed
    else:
        found = db.by_name(typed) if typed else []
        if len(found) != 1:
            flash(request, "Pick the player from the list as you type, or paste their identity ID." if not found else
                  f"{len(found)} players have gone by {typed}. Pick the right one from the list.", "error")
            raise web.HTTPFound("/bans")
        identity, name = found[0]["identity"], found[0]["name"]
    if duration not in dict(DURATIONS) or not reason:
        flash(request, "Pick a length and give a reason.", "error")
        raise web.HTTPFound("/bans")
    if db.active_ban(identity):
        flash(request, "That player is already banned. Unban them first to change the ban.", "error")
        raise web.HTTPFound("/bans")
    seconds = int(duration)
    expires = now() + seconds if seconds else None
    name = name or (db.player(identity) or {"name": ""})["name"]
    by = request[USER]["username"]
    ip_ban = form.get("ip") == "1" and auth.can(request[USER]["role"], "ips")
    targets = [(identity, name, reason)]
    if ip_ban:
        ips = [c["ip"] for c in db.ips(identity)]
        targets += [(a["identity"], a["name"], f"{reason} (same IP as {name or identity[:8]})")
                    for a in db.accounts_on_ips(ips, identity) if not db.active_ban(a["identity"])]
    lines = []
    for target, target_name, target_reason in targets:
        if ip_ban:
            ban = manager.ip_ban_account(target, target_name, target_reason, by, expires)
        else:
            ban = db.ban(db.add_ban(target, target_name, target_reason, by, expires))
        results = await manager.push_ban(ban)
        kicked = await manager.kick_everywhere(target)
        summary = ", ".join(f"{k}: {v}" for k, v in results.items()) or "no servers set up"
        if kicked:
            summary += f"; kicked from {', '.join(kicked)}"
        audit(request, "ban", target=target_name or target,
              detail=f"{dict(DURATIONS)[duration]} — {target_reason} ({summary})")
        alert(request, f"Banned {target_name or target}", colour=RED, fields=(
            ("Length", dict(DURATIONS)[duration]), ("Reason", target_reason), ("Identity", target)))
        lines.append(f"{target_name or target} ({summary})")
    extra = ""
    if ip_ban:
        known = len(db.ips(identity))
        extra = (f" IP banned {known} address{'es' if known != 1 else ''}." if known
                 else " No IPs known for them yet, so only this account.")
    flash(request, f"Banned {len(targets)} account{'s' if len(targets) != 1 else ''}: {'; '.join(lines)}.{extra}")
    raise web.HTTPFound("/bans")


async def remove_ban(request):
    require(request, "ban")
    db = request.app[DB]
    ban = db.ban(int(request.match_info["ban_id"]))
    if ban is None or ban["removed_at"]:
        raise web.HTTPFound("/bans")
    db.remove_ban(ban["id"], request[USER]["username"])
    results = await request.app[MANAGER].push_unban(db.ban(ban["id"]))
    summary = ", ".join(f"{k}: {v}" for k, v in results.items()) or "nothing to undo on the servers"
    audit(request, "unban", target=ban["name"] or ban["identity"], detail=summary)
    alert(request, f"Unbanned {ban['name'] or ban['identity']}", colour=GREEN,
          fields=(("Was banned for", ban["reason"]),))
    flash(request, f"Unbanned {ban['name'] or ban['identity']}. {summary}")
    raise web.HTTPFound("/bans")


# players

async def player_search(request):
    """Suggestions for the ban form, as you type a name or ID."""
    q = clean(request.query.get("q", ""), 64)
    if len(q) < 2:
        return web.json_response([])
    online = {p["identity"]: s.config.id for s in request.app[MANAGER].states.values() for p in s.players}
    found = {}
    for r in request.app[DB].search_players(q, limit=10):
        names = [n["name"] for n in request.app[DB].player_names(r["identity"]) if n["name"] != r["name"]]
        found[r["identity"]] = {"identity": r["identity"], "name": r["name"], "seen": _ago(r["last_seen"]),
                                "aka": names[:3], "online": online.get(r["identity"], ""),
                                "banned": bool(request.app[DB].active_ban(r["identity"]))}
    for r in request.app[STATS].search(q)[:10]:
        if r["identity"] not in found and len(found) < 10:
            found[r["identity"]] = {"identity": r["identity"], "name": r["name"], "seen": "", "aka": [],
                                    "online": online.get(r["identity"], ""),
                                    "banned": bool(request.app[DB].active_ban(r["identity"]))}
    matches = sorted(found.values(), key=lambda m: (not m["online"], not m["name"].lower().startswith(q.lower())))
    return web.json_response(matches)


async def players_page(request):
    q = clean(request.query.get("q", ""), 64)
    rows = [dict(r) for r in request.app[DB].search_players(q, limit=50)]
    if q and re.fullmatch(r"[0-9a-fA-F.:]{3,}", q) and auth.can(request[USER]["role"], "ips"):
        seen = {r["identity"] for r in rows}
        rows += [dict(r) for r in request.app[DB].search_ip(q) if r["identity"] not in seen]
    if q:
        seen = {r["identity"] for r in rows}
        rows += [dict(r, last_seen=0, last_server="") for r in request.app[STATS].search(q) if r["identity"] not in seen]
    return render(request, "players.html", q=q, rows=rows)


async def player_page(request):
    identity = request.match_info["identity"].lower()
    if not valid_identity(identity):
        raise web.HTTPNotFound(text="No such player.")
    db, oyb = request.app[DB], request.app[STATS]
    bans = [b for b in db.all("SELECT * FROM bans WHERE identity = ? ORDER BY id DESC", identity)]
    stats = oyb.player(identity)
    linked_via = None
    if stats and not stats["discord"]:
        for alt in db.alts(identity):
            other = oyb.player(alt["identity"])
            if other and other["discord"]:
                linked_via = {"identity": alt["identity"], "name": alt["name"], "discord": other["discord"]}
                break
    return render(request, "player.html", identity=identity, player=db.player(identity),
                  stats=stats, stats_here=oyb.available, linked_via=linked_via,
                  ips=db.ips(identity) if auth.can(request[USER]["role"], "ips") else [],
                  banned_ips=db.banned_ips() if auth.can(request[USER]["role"], "ips") else set(),
                  alts=db.alts(identity) if auth.can(request[USER]["role"], "ips") else [],
                  names=db.player_names(identity), notes=db.notes(identity), bans=bans,
                  incidents=db.incidents_for(identity),
                  active=db.active_ban(identity), durations=DURATIONS)


async def add_note(request):
    require(request, "notes")
    identity = request.match_info["identity"].lower()
    if not valid_identity(identity):
        raise web.HTTPNotFound(text="No such player.")
    form = await request.post()
    body = form.get("body", "").strip()[:2000]
    if body:
        request.app[DB].add_note(identity, request[USER]["username"], body)
        audit(request, "note", target=identity, detail=clean(body, 200))
    raise web.HTTPFound(f"/player/{identity}")


# console

async def console_page(request):
    require(request, "console")
    return render(request, "console.html", output=None, chosen="", command="")


async def console(request):
    require(request, "console")
    form = await request.post()
    server_id, command = form.get("server", ""), form.get("command", "").strip()
    server_or_404(request, server_id)
    if not command or "\n" in command:
        flash(request, "Type one command.", "error")
        raise web.HTTPFound("/console")
    try:
        output = await request.app[MANAGER].command(server_id, command)
        ok = True
    except RconError as exc:
        output, ok = f"Error: {exc}", False
    audit(request, "console", server_id, command, clean(output, 300), ok=ok)
    return render(request, "console.html", output=output, chosen=server_id, command=command)


# audit

async def audit_page(request):
    require(request, "audit")
    q = clean(request.query.get("q", ""), 64)
    server = request.query.get("server", "")
    return render(request, "audit.html", rows=request.app[DB].audit(q, 300, server), q=q, chosen=server)


# users

async def users_page(request):
    require(request, "users")
    return render(request, "users.html", rows=request.app[DB].users(), roles=auth.ROLES, created=None)


async def add_user(request):
    require(request, "users")
    form = await request.post()
    db = request.app[DB]
    username = form.get("username", "").strip()
    role = form.get("role", "")
    if not username or len(username) > 32 or not username.replace("_", "").replace("-", "").isalnum():
        flash(request, "Usernames use letters, numbers, - and _ (32 max).", "error")
        raise web.HTTPFound("/users")
    if role not in auth.ROLES:
        raise web.HTTPBadRequest(text="Unknown role.")
    if db.user_by_name(username):
        flash(request, "That username is taken.", "error")
        raise web.HTTPFound("/users")
    password = auth.temp_password()
    db.add_user(username, auth.hash_password(password), role, must_change=True)
    audit(request, "created account", target=username, detail=role)
    return render(request, "users.html", rows=db.users(), roles=auth.ROLES,
                  created={"username": username, "password": password})


async def change_user(request):
    require(request, "users")
    db = request.app[DB]
    target = db.user(int(request.match_info["user_id"]))
    if target is None:
        raise web.HTTPNotFound(text="No such account.")
    form = await request.post()
    action = form.get("action", "")
    is_owner = target["role"] == "owner" and not target["disabled"]
    last_owner = is_owner and db.owner_count() <= 1
    if action == "role":
        role = form.get("role", "")
        if role not in auth.ROLES:
            raise web.HTTPBadRequest(text="Unknown role.")
        if last_owner and role != "owner":
            flash(request, "There has to be at least one owner.", "error")
        else:
            db.write("UPDATE users SET role = ? WHERE id = ?", role, target["id"])
            audit(request, "changed role", target=target["username"], detail=f"{target['role']} → {role}")
            flash(request, f"{target['username']} is now {role}.")
    elif action in ("disable", "enable"):
        if action == "disable" and (target["id"] == request[USER]["id"] or last_owner):
            flash(request, "You can't disable yourself or the last owner.", "error")
        else:
            db.write("UPDATE users SET disabled = ? WHERE id = ?", int(action == "disable"), target["id"])
            if action == "disable":
                auth.end_all_sessions(db, target["id"])
            audit(request, f"{action}d account", target=target["username"])
            flash(request, f"{target['username']} {action}d.")
    elif action == "reset":
        password = auth.temp_password()
        db.write("UPDATE users SET password_hash = ?, must_change = 1 WHERE id = ?",
                 auth.hash_password(password), target["id"])
        auth.end_all_sessions(db, target["id"])
        audit(request, "reset password", target=target["username"])
        return render(request, "users.html", rows=db.users(), roles=auth.ROLES,
                      created={"username": target["username"], "password": password})
    else:
        raise web.HTTPBadRequest(text="Unknown action.")
    raise web.HTTPFound("/users")


def create_app(config: PanelConfig, db: PanelDB | None = None, manager: ServerManager | None = None,
               start_manager: bool = True) -> web.Application:
    app = web.Application(middlewares=[security, session], client_max_size=64 * 1024)
    app[CONFIG] = config
    app[DB] = db or PanelDB(config.database)
    app[MANAGER] = manager or ServerManager(config, app[DB])
    app[THROTTLE] = auth.LoginThrottle()
    app[FLASH] = {}
    app[STATS] = OybStats(config.oyb_data)
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"),
                             autoescape=True, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(ts=_ts, ago=_ago, clock=_clock, hms=_hms, until=_until, span=_span, gb=memory.gb)
    app[JINJA] = env

    if start_manager:
        async def lifecycle(app):
            app[MANAGER].start()
            yield
            await app[MANAGER].stop()
            app[DB].close()
        app.cleanup_ctx.append(lifecycle)

    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    app.router.add_get("/account", account_page)
    app.router.add_post("/account", account)
    app.router.add_get("/", dashboard)
    app.router.add_get("/cards.part", dashboard_part)
    app.router.add_get("/api/status", status_api)
    app.router.add_get("/server/{id}", server_page)
    app.router.add_get("/server/{id}/players.part", players_part)
    app.router.add_get("/server/{id}/memory.part", memory_part)
    app.router.add_get("/server/{id}/feed.part", feed_part)
    app.router.add_get("/server/{id}/summary.part", summary_part)
    app.router.add_get("/server/{id}/game/{folder}", game_page)
    app.router.add_get("/server/{id}/game/{folder}/log", game_log)
    app.router.add_post("/server/{id}/history/upload", upload_log)
    app.router.add_post("/server/{id}/kick", kick)
    app.router.add_post("/server/{id}/power", power)
    app.router.add_get("/bans", bans_page)
    app.router.add_post("/bans", add_ban)
    app.router.add_post("/bans/{ban_id:\\d+}/remove", remove_ban)
    app.router.add_get("/players", players_page)
    app.router.add_get("/players/search.json", player_search)
    app.router.add_get("/player/{identity}", player_page)
    app.router.add_post("/player/{identity}/notes", add_note)
    app.router.add_get("/console", console_page)
    app.router.add_post("/console", console)
    app.router.add_get("/audit", audit_page)
    app.router.add_get("/users", users_page)
    app.router.add_post("/users", add_user)
    app.router.add_post("/users/{user_id:\\d+}", change_user)
    app.router.add_static("/static/", HERE / "static")
    app.router.add_static("/brand/", BRAND)
    return app
