import asyncio
import hashlib
import json
import logging
import re
import secrets
import shutil
import time
from pathlib import Path

import jinja2
from aiohttp import web

from bot.discord import welcome_doc
from bot.discord.ban_roles import length_text

from . import archive, auth, memory, outliers, site
from .alerts import GOLD, GREEN, RED
from .config import PanelConfig
from .connections import LogReader, folder_start
from .db import PanelDB, now
from .oyb_stats import OybStats
from .rcon import RconError
from .servers import POWER_RCON, POWER_SERVICE, ServerManager, clean, valid_identity

log = logging.getLogger("panel.web")

HERE = Path(__file__).parent
ASSET_VERSION = str(int(max((HERE / "static" / n).stat().st_mtime
                           for n in ("style.css", "app.js", "discord.js", "site.css", "site.js"))))
BRAND = HERE.parent / "assets" / "rank-card"
COOKIE = "oyb_panel"
PUBLIC = ("/login", "/static/", "/brand/", "/drop/", "/survey.sh")
SURVEY = HERE.parent / "dev" / "live_survey.sh"
DURATIONS = [("300", "5 minutes"), ("1800", "30 minutes"), ("3600", "1 hour"), ("21600", "6 hours"),
             ("86400", "1 day"), ("259200", "3 days"), ("604800", "7 days"), ("1209600", "14 days"),
             ("2592000", "30 days"), ("0", "Permanent"), ("custom", "Custom…")]
BAN_UNITS = (("60", "minutes"), ("3600", "hours"), ("86400", "days"))
LONGEST_BAN = 3650 * 86400


def ban_seconds(form):
    """The length picked on the ban form in seconds (0 = permanent), or None."""
    duration = form.get("duration", "")
    if duration == "custom":
        amount, unit = form.get("amount", "").strip(), form.get("unit", "")
        if not amount.isdigit() or unit not in dict(BAN_UNITS) or not 0 < int(amount) * int(unit) <= LONGEST_BAN:
            return None
        return int(amount) * int(unit)
    return int(duration) if duration in dict(DURATIONS) else None
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


SITE_FILES = ("/static/site.css", "/static/site.js", "/static/site/oyb-logo.jpg", "/static/site/oyb-icon.png",
              "/static/site/banner.jpg")


@web.middleware
async def public_site(request, handler):
    """On the public domain only the website exists; everywhere else the
    panel asks search engines to stay away."""
    if site.is_site_host(request.host, request.app[CONFIG].site_hosts):
        if request.method == "GET" and request.path == "/":
            return site_page(request)
        if request.method == "GET" and request.path == "/status.json":
            return web.json_response(site.status(site_view(request)), headers={"Cache-Control": "no-store"})
        if request.method == "GET" and request.path == "/robots.txt":
            return web.Response(text="User-agent: *\nAllow: /\n")
        if request.method == "GET" and request.path in SITE_FILES:
            return await handler(request)
        raise web.HTTPNotFound(text="Not found.")
    if request.path == "/robots.txt":
        return web.Response(text="User-agent: *\nDisallow: /\n")
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers["X-Robots-Tag"] = "noindex, nofollow"
        raise
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
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
    db = request.app[DB]
    hour = time.localtime().tm_hour
    welcome = {
        "greeting": "Good morning" if 5 <= hour < 12 else "Good afternoon" if hour < 18 else "Good evening",
        "online": sum(1 for s in states if s["online"]),
        "players": sum(len(s["players"]) for s in states if s["online"]),
        "bans": len(db.bans()),
        "sus": db.one("SELECT COUNT(*) FROM feed WHERE kind = 'sus' AND at > ?", now() - 86400)[0],
    }
    return render(request, "dashboard.html", cards=states, welcome=welcome)


GUIDE = [
    ("getting-started", "Getting started", "Logging in, the home page, roles and your password.", None),
    ("servers", "Running a server", "The server page, its controls, the summary strip and tabs.", None),
    ("live-feed", "The live feed", "Everything happening in game as it happens, and how to filter it.", None),
    ("cheaters", "Spotting cheaters", "What the Sus flags mean and what to do about one.", None),
    ("players", "Looking players up", "Searching, player pages, alt accounts and notes.", None),
    ("kicking", "Kicking", "Getting someone off a server for now.", None),
    ("banning", "Banning", "Bans on every server, IP bans and unbanning.", None),
    ("history", "Past games", "Pulling up any day's games, and downloading or uploading logs.", None),
    ("health", "Health and memory", "Uptime, crashes, and when a server needs a full restart.", None),
    ("discord", "Discord", "What gets posted to the staff channel, and linked accounts.", None),
    ("console-audit", "Console and audit log", "Raw RCON commands, and the record of who did what.", "audit"),
    ("discord-messages", "Discord messages", "Start here, server info and rules, names, ban messages, posts and the greeting.", "discord"),
    ("admins", "Admin accounts", "Making accounts, roles and password resets.", "users"),
]


def guide_pages(request):
    return [{"slug": slug, "title": title, "summary": summary} for slug, title, summary, need in GUIDE
            if need is None or auth.can(request[USER]["role"], need)]


async def guide(request):
    return render(request, "guide.html", pages=guide_pages(request))


async def guide_page(request):
    pages = guide_pages(request)
    slugs = [p["slug"] for p in pages]
    slug = request.match_info["page"]
    if slug not in slugs:
        raise web.HTTPNotFound(text="No such guide page.")
    at = slugs.index(slug)
    return render(request, f"guide/{slug}.html", pages=pages, page=pages[at], number=at + 1,
                  prev=pages[at - 1] if at else None, next=pages[at + 1] if at + 1 < len(pages) else None)


async def dashboard_part(request):
    states = [server_view(s) for s in request.app[MANAGER].states.values()]
    return render(request, "_cards.html", cards=states)


async def server_page(request):
    state = server_or_404(request, request.match_info["id"])
    # A blank command (e.g. shutdown on a box where AMP runs the servers) hides its button.
    actions = [a for a in POWER_RCON if state.config.commands.get(a)] + \
        (list(POWER_SERVICE) if state.config.service else [])
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
    links = request.app[DB].upload_links(state.config.id) if auth.can(request[USER]["role"], "ips") else []
    return {"games": games, "live": live, "day": day, "today": time.strftime("%Y-%m-%d"), "upload_links": links}


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


class UploadError(Exception):
    pass


async def store_upload(app, state, chunks, filename=""):
    """Saves an uploaded console.log into a server's History. Returns (folder, size)."""
    manager = app[MANAGER]
    staging = manager.archive_root / state.config.id / ".upload"
    staging.mkdir(parents=True, exist_ok=True)
    temp = staging / f"{secrets.token_hex(8)}.log"
    size = 0
    try:
        with open(temp, "wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    raise UploadError("That file is over 200 MB.")
                out.write(chunk)
        if not size:
            raise UploadError("That file was empty.")
        with open(temp, "rb") as fh:
            head = fh.read(4096).decode("utf-8", "replace")
        match = FOLDER_NAME.search(head) or FOLDER_NAME.search(filename or "")
        if not match:
            raise UploadError("Couldn't tell when that game started. Upload the console.log from a logs_<date> folder.")
        folder = match[0]
        if app[DB].archived_game(state.config.id, folder):
            raise UploadError(f"{folder} is already in History.")
        game_dir = staging / folder
        game_dir.mkdir(exist_ok=True)
        temp.replace(game_dir / "console.log")
        dest = manager.archive_root / state.config.id / f"{folder}.tar.gz"
        try:
            game = await asyncio.to_thread(archive.archive_game, game_dir, dest)
        finally:
            shutil.rmtree(game_dir, ignore_errors=True)
        app[DB].add_archive(state.config.id, folder, str(dest), dest.stat().st_size, game)
        return folder, size
    finally:
        temp.unlink(missing_ok=True)


async def part_chunks(part):
    while chunk := await part.read_chunk(1 << 20):
        yield chunk


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
    try:
        folder, size = await store_upload(request.app, state, part_chunks(part), part.filename)
    except UploadError as exc:
        flash(request, str(exc), "error")
        raise web.HTTPFound(back)
    audit(request, "upload log", server=state.config.id, target=folder, detail=f"{size // 1024} KB")
    raise web.HTTPFound(f"/server/{state.config.id}/game/{folder}")


def link_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def site_url(request):
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme).split(",")[0].strip()
    return f"{scheme}://{request.host}"


async def make_upload_link(request):
    require(request, "ips")
    state = server_or_404(request, request.match_info["id"])
    token = secrets.token_urlsafe(24)
    request.app[DB].add_upload_link(link_hash(token), state.config.id, request[USER]["username"])
    audit(request, "make upload link", server=state.config.id, detail="24 hours")
    flash(request, f"Upload link for {state.config.name}, working for 24 hours. Copy it now, it won't be shown "
                   f"again: {site_url(request)}/drop/{token}")
    raise web.HTTPFound(f"/server/{state.config.id}#history")


async def revoke_upload_link(request):
    require(request, "ips")
    state = server_or_404(request, request.match_info["id"])
    request.app[DB].revoke_upload_link(int(request.match_info["link_id"]), state.config.id)
    audit(request, "revoke upload link", server=state.config.id)
    raise web.HTTPFound(f"/server/{state.config.id}#history")


async def drop(request):
    """An upload link: anyone with it can add logs to one server's History
    for 24 hours, no login. Takes a browser upload or a raw file body."""
    db = request.app[DB]
    link = db.upload_link(link_hash(request.match_info["token"]))
    if link is None:
        raise web.HTTPNotFound(text="This upload link has expired or was turned off. Ask for a new one.")
    state = request.app[MANAGER].state(link["server"])
    if state is None:
        raise web.HTTPNotFound(text="That server isn't set up any more.")
    if request.method == "GET":
        return render(request, "drop.html", server=state.config.name, expires=link["expires_at"],
                      result=None, error="")
    form = request.content_type.startswith("multipart/")
    filename = ""
    if form:
        reader = await request.multipart()
        part = await reader.next()
        while part is not None and not part.filename:
            part = await reader.next()
        if part is None:
            return render(request, "drop.html", server=state.config.name, expires=link["expires_at"],
                          result=None, error="Choose a console.log to upload.")
        chunks, filename = part_chunks(part), part.filename
    else:
        if request.query.get("kind") == "survey":
            return await store_survey(request, link, state)
        chunks = request.content.iter_chunked(1 << 20)
        filename = request.query.get("name", "")
    try:
        folder, size = await store_upload(request.app, state, chunks, filename)
    except UploadError as exc:
        if not form:
            raise web.HTTPBadRequest(text=str(exc))
        return render(request, "drop.html", server=state.config.name, expires=link["expires_at"],
                      result=None, error=str(exc))
    db.used_upload_link(link["id"])
    db.log(f"upload link ({link['created_by']})", "upload log", state.config.id, folder,
           f"{size // 1024} KB from {client_ip(request)}")
    if not form:
        return web.Response(text=f"Added {folder} to {state.config.name}'s History.\n")
    return render(request, "drop.html", server=state.config.name, expires=link["expires_at"],
                  result=folder, error="")


SURVEY_LIMIT = 2 * 1024 * 1024


def survey_dir(app):
    return Path(app[CONFIG].database).resolve().parent / "surveys"


async def store_survey(request, link, state):
    """A report from dev/live_survey.sh about the box a server runs on."""
    body = await request.content.read(SURVEY_LIMIT + 1)
    if not body or len(body) > SURVEY_LIMIT:
        raise web.HTTPBadRequest(text="That report is empty or too big.\n")
    folder = survey_dir(request.app)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y-%m-%d_%H-%M-%S', time.gmtime())}_{state.config.id}.txt"
    (folder / name).write_bytes(body)
    request.app[DB].used_upload_link(link["id"])
    request.app[DB].log(f"upload link ({link['created_by']})", "box report", state.config.id, name,
                        f"{len(body) // 1024} KB from {client_ip(request)}")
    return web.Response(text="Report sent. All done, nothing else to do.\n")


async def survey_script(request):
    """The box report script, so a server box can fetch it with one short line."""
    return web.Response(text=SURVEY.read_text(), content_type="text/plain")


async def surveys_page(request):
    require(request, "ips")
    folder = survey_dir(request.app)
    reports = sorted(folder.glob("*.txt"), reverse=True) if folder.is_dir() else []
    name = request.query.get("report", "")
    chosen = next((p for p in reports if p.name == name), reports[0] if reports else None)
    return render(request, "surveys.html", reports=[p.name for p in reports],
                  chosen=chosen.name if chosen else "",
                  text=chosen.read_text(errors="replace") if chosen else "")


def site_doc(request):
    row = request.app[DB].discord_doc("site")
    raw = json.loads(row["published"]) if row and row["published"] else site.default_site()
    return site.check_site(raw, [s.id for s in request.app[CONFIG].servers])[0]


def site_view(request, doc=None):
    return site.view(doc or site_doc(request), request.app[CONFIG].servers, request.app[MANAGER].states,
                     bridge(request))


def site_page(request, preview=False):
    doc = site_doc(request)
    html = request.app[JINJA].get_template("site.html").render(
        doc=doc, site=site_view(request, doc), preview=preview, asset_version=ASSET_VERSION)
    return web.Response(text=html, content_type="text/html")


async def website_page(request):
    require(request, "discord")
    return website_form(request, site_doc(request), [])


def website_form(request, doc, problems):
    labels = {s.get("id"): s.get("label") for s in bridge(request).get("servers", [])}
    servers = [{"id": s.id, "name": labels.get(s.id) or s.name} for s in request.app[CONFIG].servers]
    return render(request, "website.html", doc=doc, problems=problems, site_servers=servers,
                  hosts=request.app[CONFIG].site_hosts)


async def website_save(request):
    require(request, "discord")
    form = await request.post()
    raw = {key: form.get(key, "") for key in ("name", "tagline", "about", "discord")}
    raw["servers"] = {s.id: {"show": f"show_{s.id}" in form, "name": form.get(f"name_{s.id}", ""),
                             "game": form.get(f"game_{s.id}", ""),
                             "join": form.get(f"join_{s.id}", "")} for s in request.app[CONFIG].servers}
    doc, problems = site.check_site(raw, [s.id for s in request.app[CONFIG].servers])
    if problems:
        return website_form(request, {**doc, "discord": raw["discord"]}, problems)
    request.app[DB].publish_discord_doc("site", json.dumps(doc), request[USER]["username"])
    audit(request, "edit website")
    flash(request, "Saved. The public site shows it now.")
    raise web.HTTPFound("/website")


async def website_preview(request):
    require(request, "discord")
    return site_page(request, preview=True)


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
    show = request.query.get("show", "all")
    return render(request, "_feed.html", s=server_view(state), feed=request.app[DB].feed(state.config.id, show=show), show=show)


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
    return render(request, "bans.html", bans=rows, show_old=show_old, durations=DURATIONS, ban_units=BAN_UNITS,
                  prefill=prefill, error="")


async def add_ban(request):
    require(request, "ban")
    form = await request.post()
    db, manager = request.app[DB], request.app[MANAGER]
    typed = clean(form.get("player", ""), 64)
    identity = form.get("identity", "").strip().lower()
    name = ""
    reason = clean(form.get("reason", ""))
    seconds = ban_seconds(form)
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
    if seconds is None or not reason:
        flash(request, "Pick a length (a custom one needs a number, up to 10 years) and give a reason.", "error")
        raise web.HTTPFound("/bans")
    if db.active_ban(identity):
        flash(request, "That player is already banned. Unban them first to change the ban.", "error")
        raise web.HTTPFound("/bans")
    expires = now() + seconds if seconds else None
    length = length_text(seconds) if seconds else "Permanent"
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
              detail=f"{length} — {target_reason} ({summary})")
        alert(request, f"Banned {target_name or target}", colour=RED, fields=(
            ("Length", length), ("Reason", target_reason), ("Identity", target)))
        lines.append(f"{target_name or target} ({summary})")
    extra = ""
    if ip_ban:
        known = len(db.ips(identity))
        extra = (f" IP banned {known} address{'es' if known != 1 else ''}." if known
                 else " No IPs known for them yet, so only this account.")
    flash(request, f"Banned {len(targets)} account{'s' if len(targets) != 1 else ''}: {'; '.join(lines)}.{extra}")
    raise web.HTTPFound("/bans")


async def edit_ban(request):
    """Only whoever made a ban can reword its reason, while it's still in force."""
    require(request, "ban")
    db = request.app[DB]
    ban = db.ban(int(request.match_info["ban_id"]))
    by = request[USER]["username"]
    if ban is None or ban["removed_at"] or (ban["expires_at"] and ban["expires_at"] <= now()):
        raise web.HTTPFound("/bans")
    if ban["created_by"] != by:
        raise web.HTTPForbidden(text="Only the admin who made this ban can change its reason.")
    reason = clean((await request.post()).get("reason", ""))
    if not reason:
        flash(request, "The reason can't be empty.", "error")
        raise web.HTTPFound("/bans")
    if reason != ban["reason"]:
        db.set_ban_reason(ban["id"], reason)
        audit(request, "edit ban", target=ban["name"] or ban["identity"], detail=f"{ban['reason']} → {reason}")
    flash(request, f"Reason for {ban['name'] or ban['identity']} updated.")
    raise web.HTTPFound("/bans")


async def remove_ban(request):
    require(request, "unban")
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


UNUSUAL_PERIODS = (("7", "7 days"), ("30", "30 days"), ("90", "90 days"), ("all", "All time"))


async def unusual_page(request):
    period = request.query.get("days", "30")
    if period not in dict(UNUSUAL_PERIODS):
        period = "30"
    since = 0 if period == "all" else now() - int(period) * 86400
    db = request.app[DB]
    players, typical = outliers.unusual(db.player_totals(since), db.incident_counts(since))
    return render(request, "unusual.html", players=players, typical=typical, metrics=outliers.METRICS,
                  periods=UNUSUAL_PERIODS, period=period,
                  scored=db.one("SELECT COUNT(*) AS n FROM scored_games")["n"],
                  archived=db.one("SELECT COUNT(*) AS n FROM log_archive")["n"])


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


KILL_LOGS = (("tk_by", "Teamkills they did"), ("tk_on", "Who teamkilled them"), ("all", "All their kills and deaths"))
KILL_PERIODS = (("last", "Their last game"), ("1", "Last 24 hours"), ("7", "Last 7 days"), ("30", "Last 30 days"))
DAMAGE = {"KINETIC": "bullet", "EXPLOSIVE": "explosion", "FRAGMENTATION": "grenade or shrapnel", "INCENDIARY": "fire",
          "FIRE": "fire", "BLEEDING": "bled out", "COLLISION": "vehicle", "MELEE": "melee"}


def body_part(zone):
    """The log's hit zones (RArm, LThigh, Head) as plain words."""
    side = {"L": "left ", "R": "right "}.get(zone[:1], "") if zone[1:2].isupper() else ""
    rest = zone[len(side) and 1:]
    return side + re.sub(r"(?<!^)(?=[A-Z])", " ", rest).lower()


def kill_log(request, identity):
    """The kill log asked for on a player's page, or None if none was."""
    mode = request.query.get("log", "")
    if mode not in dict(KILL_LOGS):
        return None
    period, day = request.query.get("period", "last"), request.query.get("day", "")
    db, names = request.app[DB], {s.id: s.name for s in request.app[CONFIG].servers}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        start = int(time.mktime(time.strptime(day, "%Y-%m-%d")))
        rows, label = db.kill_log(identity, mode, start, start + 86400), day
    else:
        period = period if period in dict(KILL_PERIODS) else "last"
        since = "last" if period == "last" else now() - int(period) * 86400
        rows, label, day = db.kill_log(identity, mode, since), dict(KILL_PERIODS)[period], ""
    lines = []
    for r in rows:
        stamp = time.strftime("%d %b %Y · %H:%M:%S", time.localtime(r["at"]))
        what = "teamkilled" if r["kind"] == "teamkill" else "killed"
        killer = r["killer_name"] or "Unknown"
        if r["killer"] == r["victim"]:
            what, killer = "died by their own hand", r["victim_name"]
        how = DAMAGE.get(r["damage"], r["damage"].lower())
        extra = ", ".join(x for x in (how, f"{r['distance']:.0f} m" if r["distance"] else "",
                                      f"hit in the {body_part(r['zone'])}" if r["zone"] else "") if x)
        revenge = db.revenge_for(r)
        note = ""
        if revenge:
            gap = r["at"] - revenge["at"]
            note = (f"{gap // 60} min {gap % 60} s after {revenge['killer_name']} teamkilled "
                    f"{revenge['victim_name']} at {time.strftime('%H:%M:%S', time.localtime(revenge['at']))}")
        lines.append({"row": r, "stamp": stamp, "server": names.get(r["server"], r["server"]), "what": what,
                      "killer": killer, "extra": extra, "revenge": note,
                      "game": time.strftime("logs_%Y-%m-%d_%H-%M-%S", time.localtime(r["game"])) if r["game"] else ""})
    text = "\n".join(f"{l['stamp']} [{l['server']}] {l['killer']} {l['what']}"
                     + ("" if l["row"]["killer"] == l["row"]["victim"] else f" {l['row']['victim_name']}")
                     + (f" ({l['extra']})" if l["extra"] else "") + (f" - {l['revenge']}" if l["revenge"] else "")
                     for l in lines)
    return {"mode": mode, "title": dict(KILL_LOGS)[mode], "period": period, "day": day, "label": label,
            "lines": lines, "text": text, "full": len(rows) >= 500}


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
                  incidents=db.incidents_for(identity), kills=kill_log(request, identity),
                  kill_logs=KILL_LOGS, kill_periods=KILL_PERIODS,
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


# Discord: what the bot posts, edited here and published to it

DISCORD_PAGES = (("welcome", "Start here message"), ("serverinfo", "Server info & rules"), ("names", "Server names"),
                 ("factions", "Factions"), ("matchping", "Match alerts"), ("weekly", "Weekly top 3"), ("greeting", "Join greeting"), ("bans", "Ban messages"),
                 ("posts", "Posts"), ("links", "Link requests"), ("channels", "Channels & roles"))


def bridge(request):
    """What the bot last reported: roles, channels, and how publishing went."""
    path = Path(request.app[CONFIG].oyb_data, "panel_bridge.json")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    data["age"] = now() - int(data.get("updated", 0))
    return data


def discord_state(request, key, defaults, check):
    row = request.app[DB].discord_doc(key)
    published = json.loads(row["published"]) if row and row["published"] else None
    draft = json.loads(row["draft"]) if row and row["draft"] else None
    doc = draft or published or defaults()
    return {"row": row, "doc": check(doc)[0], "draft": draft is not None, "live": published is not None}


async def discord_home(request):
    require(request, "discord")
    raise web.HTTPFound("/discord/welcome")


def text_ids(value):
    """Discord ids are longer than a browser's numbers can hold exactly, so
    the editors get them as text; the checks turn them back into numbers."""
    if isinstance(value, dict):
        return {k: text_ids(v) for k, v in value.items()}
    if isinstance(value, list):
        return [text_ids(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool) and value > 2 ** 53:
        return str(value)
    return value


def discord_context(key, **extra):
    base = {"types": welcome_doc.TYPES, "placeholders": welcome_doc.PLACEHOLDERS,
            "ban_placeholders": welcome_doc.BAN_PLACEHOLDERS, "dm_title": welcome_doc.DM_TITLE,
            "dm_text": welcome_doc.DM_TEXT, "ticket_title": welcome_doc.TICKET_TITLE,
            "pages": DISCORD_PAGES, "page": key, "kind": key, "action_url": f"/discord/{key}", "heading": None,
            "text_ids": text_ids, "channel_settings": welcome_doc.CHANNEL_SETTINGS,
            "role_settings": welcome_doc.ROLE_SETTINGS}
    return {**base, **extra}


async def discord_page(request):
    require(request, "discord")
    key = request.match_info["page"]
    if key == "posts":
        return posts_page(request)
    if key == "links":
        return links_page(request)
    if key not in DISCORD_DOCS or key == "post":
        raise web.HTTPNotFound(text="No such page.")
    defaults, check = DISCORD_DOCS[key]
    state = discord_state(request, key, defaults, check)
    report = bridge(request)
    if key == "bans" and not state["live"] and not state["draft"] and report.get("ban_settings"):
        # Start from what the bot is using now (its .env), not blank.
        state["doc"] = check(report["ban_settings"])[0]
    return render(request, f"discord_{key}.html", bridge=report, default_doc=defaults(), problems=[], **state,
                  **discord_context(key))


async def discord_save(request):
    require(request, "discord")
    key = request.match_info["page"]
    if key == "links":
        return await link_action(request)
    if key not in DISCORD_DOCS or key == "post":
        raise web.HTTPNotFound(text="No such page.")
    return await save_doc(request, key, key, f"discord_{key}.html", discord_context(key))


async def save_doc(request, key, label, template, context):
    """Save a draft, publish, or throw the draft away, for any of the Discord pages."""
    defaults, check = DISCORD_DOCS[key.split(":")[0]]
    form = await request.post()
    action = form.get("action", "")
    db, who = request.app[DB], request[USER]["username"]
    back = context["action_url"]
    if action == "discard":
        db.discard_discord_draft(key)
        audit(request, f"discard {label} draft")
        flash(request, "Draft thrown away. The editor shows what's live again.")
        raise web.HTTPFound(back)
    try:
        raw = json.loads(form.get("doc", ""))
    except ValueError:
        raw = None
    doc, problems = check(raw)
    if key == "names":
        problems += name_clashes(doc, bridge(request).get("servers", []))
    text = json.dumps(doc)
    if action == "publish" and not problems:
        db.publish_discord_doc(key, text, who)
        audit(request, f"publish {label}")
        flash(request, "Published. The bot updates Discord within a minute.")
        raise web.HTTPFound(back)
    db.save_discord_draft(key, text, who)
    if action == "publish" or problems:
        state = discord_state(request, key, defaults, check)
        return render(request, template, bridge=bridge(request), default_doc=defaults(), problems=problems,
                      **{**state, "doc": doc}, **context)
    audit(request, f"save {label} draft")
    flash(request, "Draft saved. Nothing changes in Discord until you publish.")
    raise web.HTTPFound(back)


def posts_page(request):
    db, report = request.app[DB], bridge(request)
    channels = {c["id"]: c["name"] for c in report.get("channels", [])}
    posts = []
    for row in db.all("SELECT * FROM discord_docs WHERE key LIKE 'post:%' ORDER BY COALESCE(published_at, draft_at) DESC"):
        live = json.loads(row["published"]) if row["published"] else None
        if live and live.get("deleted"):
            continue
        doc = json.loads(row["draft"]) if row["draft"] else live
        if doc is None:
            continue
        post_id = row["key"][5:]
        state = report.get("posts", {}).get(post_id, {})
        posts.append({"id": post_id, "title": doc.get("title") or next(
                          (s["heading"] or s["text"][:60] for s in doc.get("sections", []) if s["heading"] or s["text"]),
                          "Untitled"),
                      "channel": channels.get(str(doc.get("channel_id")), ""), "live": bool(live), "draft": bool(row["draft"]),
                      "row": row, "problems": state.get("problems", []) if state.get("version") == row["version"] else [],
                      "confirmed": state.get("version") == row["version"] and live is not None})
    return render(request, "discord_posts.html", posts=posts, bridge=report, **discord_context("posts"))


async def new_post(request):
    require(request, "discord")
    post_id = secrets.token_hex(4)
    request.app[DB].save_discord_draft(f"post:{post_id}", json.dumps(welcome_doc.default_post()), request[USER]["username"])
    audit(request, "new post")
    raise web.HTTPFound(f"/discord/posts/{post_id}")


def post_key(request):
    post_id = request.match_info["post"]
    if not re.fullmatch(r"[0-9a-f]{8}", post_id) or request.app[DB].discord_doc(f"post:{post_id}") is None:
        raise web.HTTPNotFound(text="No such post.")
    return post_id, f"post:{post_id}"


def post_context(post_id):
    return discord_context("posts", kind="post", action_url=f"/discord/posts/{post_id}", types=welcome_doc.POST_TYPES,
                           heading="Post")


async def post_page(request):
    require(request, "discord")
    post_id, key = post_key(request)
    defaults, check = DISCORD_DOCS["post"]
    state = discord_state(request, key, defaults, check)
    report = bridge(request)
    reported = report.get("posts", {}).get(post_id)
    return render(request, "discord_welcome.html", bridge={**report, "posts": None, "post": reported},
                  default_doc=defaults(), problems=[], **state, **post_context(post_id))


async def post_save(request):
    require(request, "discord")
    post_id, key = post_key(request)
    form = await request.post()
    if form.get("action") == "delete":
        row = request.app[DB].discord_doc(key)
        if row["published"]:
            # The bot needs to see it gone to take the message down.
            request.app[DB].publish_discord_doc(key, json.dumps({"deleted": True}), request[USER]["username"])
            flash(request, "Deleted. The bot takes it down from Discord within a minute.")
        else:
            request.app[DB].write("DELETE FROM discord_docs WHERE key = ?", key)
            flash(request, "Deleted. It was never posted, so there was nothing to take down.")
        audit(request, "delete post", detail=post_id)
        raise web.HTTPFound("/discord/posts")
    return await save_doc(request, key, "post", "discord_welcome.html", post_context(post_id))


LINK_ACTIONS = {"approve": "Approve", "reject": "Reject", "link": "Link", "unlink": "Unlink"}


def links_page(request):
    report = bridge(request)
    links = report.get("link_requests") or {}
    results = report.get("links", {}).get("results", {})
    actions = []
    for row in request.app[DB].link_actions():
        done = results.get(str(row["id"]))
        if done is None and row["at"] < now() - 86400:
            done = {"ok": False, "text": "The bot never picked this up."}
        actions.append({**dict(row), "done": done})
    busy = {a["target"] for a in actions if a["done"] is None}
    names = {r["discord_id"]: r["member"] for r in links.get("linked", [])}
    names.update({r["token"]: r["member"] or r["name"] for r in links.get("pending", [])})
    return render(request, "discord_links.html", bridge=report, links=links, actions=actions, busy=busy,
                  names=names, action_labels=LINK_ACTIONS, **discord_context("links"))


async def link_action(request):
    """Queue a decision for the bot, which carries it out with the same code
    as the Approve and Reject buttons in the staff channel."""
    form = await request.post()
    kind, target = form.get("kind", ""), form.get("target", "").strip()
    identity = form.get("identity", "").strip() or form.get("player", "").strip()
    if kind == "unlink":
        ok = bool(re.fullmatch(r"\d{5,25}", target)) and (not identity or valid_identity(identity))
    elif kind in ("approve", "reject", "link"):
        ok = bool(re.fullmatch(r"[0-9a-f]{32}", target)) and (kind != "link" or valid_identity(identity))
    else:
        ok = False
    if not ok:
        flash(request, "Pick their game account from the list first." if kind == "link" else "That didn't make sense; nothing was done.")
        raise web.HTTPFound("/discord/links")
    request.app[DB].add_link_action(kind, target, identity if kind in ("link", "unlink") else None,
                                    request[USER]["username"])
    audit(request, f"link request {kind}", target=identity, detail=target)
    flash(request, f"{LINK_ACTIONS[kind]} sent to the bot. It happens within a minute; the result shows below.")
    raise web.HTTPFound("/discord/links")


DISCORD_DOCS = {"welcome": (welcome_doc.default_welcome, welcome_doc.check_welcome),
                "greeting": (welcome_doc.default_greeting, welcome_doc.check_greeting),
                "names": (welcome_doc.default_names, welcome_doc.check_names),
                "serverinfo": (welcome_doc.default_serverinfo, welcome_doc.check_serverinfo),
                "bans": (welcome_doc.default_bans, welcome_doc.check_bans),
                "post": (welcome_doc.default_post, welcome_doc.check_post),
                "matchping": (welcome_doc.default_matchping, welcome_doc.check_matchping),
                "weekly": (welcome_doc.default_weekly, welcome_doc.check_weekly),
                "channels": (welcome_doc.default_channels, welcome_doc.check_channels),
                "factions": (welcome_doc.default_factions, welcome_doc.check_factions)}


def name_clashes(doc, servers):
    """A new name that matches another server's name, counting the ones left at their default."""
    final = {s["id"]: doc["names"].get(s["id"]) or s["default"] for s in servers}
    problems, seen = [], {}
    for server_id, name in final.items():
        other = seen.get(name.lower())
        if other and (server_id in doc["names"] or other in doc["names"]):
            problems.append(f"{other} and {server_id} would both be called {name}; give each its own name.")
        seen[name.lower()] = server_id
    return problems


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
    app = web.Application(middlewares=[security, public_site, session], client_max_size=64 * 1024)
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
    app.router.add_get("/guide", guide)
    app.router.add_get("/guide/{page}", guide_page)
    app.router.add_get("/api/status", status_api)
    app.router.add_get("/server/{id}", server_page)
    app.router.add_get("/server/{id}/players.part", players_part)
    app.router.add_get("/server/{id}/memory.part", memory_part)
    app.router.add_get("/server/{id}/feed.part", feed_part)
    app.router.add_get("/server/{id}/summary.part", summary_part)
    app.router.add_get("/server/{id}/game/{folder}", game_page)
    app.router.add_get("/server/{id}/game/{folder}/log", game_log)
    app.router.add_post("/server/{id}/history/upload", upload_log)
    app.router.add_post("/server/{id}/upload-links", make_upload_link)
    app.router.add_post("/server/{id}/upload-links/{link_id:\\d+}/revoke", revoke_upload_link)
    app.router.add_route("*", "/drop/{token}", drop)
    app.router.add_get("/surveys", surveys_page)
    app.router.add_get("/survey.sh", survey_script)
    app.router.add_get("/website", website_page)
    app.router.add_post("/website", website_save)
    app.router.add_get("/website/preview", website_preview)
    app.router.add_post("/server/{id}/kick", kick)
    app.router.add_post("/server/{id}/power", power)
    app.router.add_get("/bans", bans_page)
    app.router.add_post("/bans", add_ban)
    app.router.add_post("/bans/{ban_id:\\d+}/remove", remove_ban)
    app.router.add_post("/bans/{ban_id:\\d+}/edit", edit_ban)
    app.router.add_get("/players", players_page)
    app.router.add_get("/players/unusual", unusual_page)
    app.router.add_get("/players/search.json", player_search)
    app.router.add_get("/player/{identity}", player_page)
    app.router.add_post("/player/{identity}/notes", add_note)
    app.router.add_get("/discord", discord_home)
    app.router.add_post("/discord/posts/new", new_post)
    app.router.add_get("/discord/posts/{post}", post_page)
    app.router.add_post("/discord/posts/{post}", post_save)
    app.router.add_get("/discord/{page}", discord_page)
    app.router.add_post("/discord/{page}", discord_save)
    app.router.add_get("/console", console_page)
    app.router.add_post("/console", console)
    app.router.add_get("/audit", audit_page)
    app.router.add_get("/users", users_page)
    app.router.add_post("/users", add_user)
    app.router.add_post("/users/{user_id:\\d+}", change_user)
    app.router.add_static("/static/", HERE / "static")
    app.router.add_static("/brand/", BRAND)
    return app
