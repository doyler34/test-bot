import asyncio
from collections import Counter, defaultdict
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
import warnings
from pathlib import Path
from urllib.parse import quote, urlencode

import jinja2
from aiohttp import web

from bot.discord import welcome_doc
from bot.discord.ban_roles import length_text
from bot.mortar.calibration import calibration, image_path, tile_path
from bot.mortar import heights
from bot.web import server as mortar_server
from bot.mortar.solution import OUT_OF_RANGE, apply_wind, bearing, parse_grid, profiles
from bot.mortar.wind import Wind

from . import archive, attacks, auth, ballistics, console, files, investigate, maptiles, memory, monitor, netcheck, outliers, site, whois, workshop
from .amp import GAME_NODE, MAX_ADMINS, Amp, AmpError, instance_name as amp_instance_name, parse_ids
from .alerts import GOLD, GREEN, RED
from .config import PanelConfig
from .connections import LogReader, folder_start
from .db import PanelDB, now
from .oyb_stats import OybStats
from .reasons import BAN_REASONS, KICK_REASONS, options as reason_options, with_appeal
from .rcon import RconError
from .battlemetrics import BanSync
from .suspicion import DAMAGE, DEFAULTS as SUS_DEFAULTS, LIMITS as SUS_LIMITS, body_part, check_settings as check_sus
from .servers import (ATTACK_LOGINS, ATTACK_WINDOW, UNREACHABLE_AFTER, RconError, ServerManager, clean, systemctl,
                      valid_identity)

log = logging.getLogger("panel.web")

HERE = Path(__file__).parent
ASSET_VERSION = str(int(max((HERE / "static" / n).stat().st_mtime
                           for n in ("style.css", "app.js", "discord.js", "site.css", "site.js", "tour.js", "manage.js"))))
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

CONFIG = web.AppKey("config", PanelConfig)
DB = web.AppKey("db", PanelDB)
MANAGER = web.AppKey("manager", ServerManager)
THROTTLE = web.AppKey("throttle", auth.LoginThrottle)
FLASH = web.AppKey("flash", dict)
JINJA = web.AppKey("jinja", jinja2.Environment)
WORKSHOP = web.AppKey("workshop", workshop.Workshop)
STATS = web.AppKey("stats", OybStats)
BM = web.AppKey("battlemetrics", object)
AMP = web.AppKey("amp", object)
USER = web.RequestKey("user", object) if hasattr(web, "RequestKey") else "user"
CSRF = web.RequestKey("csrf", str) if hasattr(web, "RequestKey") else "csrf"
GRANTS = web.RequestKey("grants", set) if hasattr(web, "RequestKey") else "grants"


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


def _axis(value, kind):
    """A chart's axis label in that chart's unit."""
    if kind == "cpu":
        return f"{value:.0f}%"
    if kind == "mem":
        return f"{value:.0f} GB"
    if kind == "net":
        return f"{value:.0f} MB/s" if value >= 10 or value == 0 else f"{value:.1f} MB/s"
    return f"{value:.0f}"


def _clock(value):
    if not value:
        return ""
    if time.strftime("%Y%m%d", time.localtime(value)) == time.strftime("%Y%m%d"):
        return time.strftime("%H:%M:%S", time.localtime(value))
    return time.strftime("%d %b %H:%M", time.localtime(value))


def _filesize(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


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


# The first-login tour starts on the dashboard and walks on through these pages.
TOUR_PAGES = ("dashboard.html", "server.html", "players.html", "bans.html", "data.html", "settings.html")
# Raise this when the tour changes enough that everyone should see it once more.
TOUR_VERSION = 2
# The same for the Server manager walkthrough; people who saw an older one get it once more.
MANAGE_TOUR_VERSION = 2


def render(request, template, **context):
    user = request.get(USER)
    context.update(
        box_total=request.app[MANAGER].box_total,
        asset_version=ASSET_VERSION,
        oyb=request.app[STATS].player,
        settle_minutes=request.app[MANAGER].settle // 60,
        user=user,
        csrf=request.get(CSRF, ""),
        can=(lambda perm: bool(user) and allowed(request, perm)),
        servers=request.app[CONFIG].servers,
        nav_servers=[{"id": st.config.id, "name": st.config.name, "online": st.online, "map": st.query.get("map") or ""}
                     for st in request.app[MANAGER].states.values()],
        path=request.path,
        flashes=request.app[FLASH].pop(user["id"], []) if user and not template.startswith("_") else [],
    )
    if user and not template.startswith("_"):
        context.setdefault("notices", attention(request, list(request.app[MANAGER].states.values())))
        # Anyone who runs servers from the Server manager gets the walkthrough once, whatever their role.
        if template in MANAGE_TOUR_PAGES and allowed(request, "files") and (
                request.query.get("tour") == "1" or user["manage_toured"] < MANAGE_TOUR_VERSION):
            context.setdefault("tour", "manage")
            context.setdefault("tour_inst", request.match_info.get("inst") or first_instance(request))
        else:
            context.setdefault("tour", template in TOUR_PAGES and (request.query.get("tour") == "1" or (
                template == "dashboard.html" and user["toured"] < TOUR_VERSION)))
    html = request.app[JINJA].get_template(template).render(**context)
    headers = {"Content-Security-Policy": context["csp"]} if context.get("csp") else None
    return web.Response(text=html, content_type="text/html", headers=headers)


def flash(request, message, kind="ok"):
    request.app[FLASH].setdefault(request[USER]["id"], []).append((kind, message))


def named(request, user=None):
    """The panel's own owners (console_users in the config): they always have everything,
    and they're the only ones who can manage manager accounts."""
    return (user or request[USER])["username"].lower() in request.app[CONFIG].console_users


def allowed(request, permission):
    if named(request):
        return True
    return auth.allows(request[USER]["role"], request.get(GRANTS, set()), permission)


def require(request, permission):
    if not allowed(request, permission):
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
    started = time.monotonic()
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        exc.headers.update(SECURITY_HEADERS)
        raise
    finally:
        took = time.monotonic() - started
        if took > 1:
            log.warning("slow page: %s took %.1fs", request.path, took)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


SITE_FILES = ("/static/site.css", "/static/site.js", "/static/site/oyb-logo.jpg", "/static/site/oyb-icon.png",
              "/static/site/banner.jpg", "/brand/fonts/Display.ttf", "/brand/fonts/Body.ttf")


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
        if request.path == "/mortar" or request.path.startswith("/mortar/"):
            return await site_mortar(request)
        if request.method == "GET" and (request.path == "/workshop" or request.path.startswith("/workshop/")):
            return await site_workshop(request)
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
    request[GRANTS] = db.grants(user["id"]) if user else set()
    if request.path.startswith(PUBLIC):
        return await handler(request)
    if user is None:
        if request.path.startswith("/api/") or request.path.endswith((".part", ".json")):
            raise web.HTTPUnauthorized(text="Log in again.")
        raise web.HTTPFound("/login")
    if request.method == "POST" and (request.path.endswith(("/history/upload", "/picture/upload"))
                                     or (request.path.startswith("/files/") and "do" in request.query)):
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
        db.add_attempts([{"at": now(), "ip": client_ip(request), "source": "Panel", "user": (username or "")[:64]}])
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
        "admins": state.admins if state.online else [],
        "up_for": state.process_age if state.pid else
                  (now() - state.online_since if state.online and state.online_since else None),
    }


async def dashboard(request):
    live = list(request.app[MANAGER].states.values())
    details = card_details(request.app[DB])
    states = [control_card(s, request.app[PICTURES], details) for s in live]
    chosen = request.app[MANAGER].states.get(request.query.get("server", "")) \
        or next((s for s in live if s.online), live[0] if live else None)
    db = request.app[DB]
    hour = time.localtime().tm_hour
    welcome = {
        "greeting": "Good morning" if 5 <= hour < 12 else "Good afternoon" if hour < 18 else "Good evening",
        "online": sum(1 for s in states if s["online"]),
        "players": sum(len(s["players"]) for s in states if s["online"]),
        "bans": len(db.bans()),
        "sus": db.one("SELECT COUNT(*) FROM feed WHERE kind = 'sus' AND at > ?", now() - 86400)[0],
    }
    extra = {}
    if chosen:
        extra = {"chosen": next(c for c in states if c["id"] == chosen.config.id), **server_overview(request, chosen),
                 "csp": MAP_CSP}
    return render(request, "dashboard.html", cards=states, welcome=welcome, amp=bool(request.app[AMP]), gb=GB, **extra)


async def tour_done(request):
    request.app[DB].write("UPDATE users SET toured = ? WHERE id = ?", TOUR_VERSION, request[USER]["id"])
    return web.Response(status=204)


async def manage_tour_done(request):
    request.app[DB].write("UPDATE users SET manage_toured = ? WHERE id = ?", MANAGE_TOUR_VERSION, request[USER]["id"])
    return web.Response(status=204)


MANAGE_TOUR_PAGES = ("manage_index.html", "manage_overview.html", "manage_console.html", "manage_settings.html",
                     "mods.html", "files.html", "file_edit.html", "manage_new.html")


def first_instance(request):
    names = files.instances(request.app[FILES_ROOT])
    return names[0] if names else ""


GUIDE = [
    ("getting-started", "Getting started", "Logging in, finding your way round, roles and your password.", None),
    ("server-manager", "The Server manager", "Running servers from the panel: power, console, settings, mods, files and new servers.", None),
    ("dashboard", "The dashboard", "Every server's card, the server panel, its live map, players and charts.", None),
    ("servers", "A server's own page", "Power buttons, the summary bars and the tabs.", None),
    ("live-feed", "The live feed and kill map", "Everything happening in game as it happens, and how to filter it.", None),
    ("cheaters", "Spotting cheaters", "What the Sus flags mean, unusual players, and what to do about one.", None),
    ("players", "Looking players up", "Searching, player pages, alt accounts and notes.", None),
    ("kicking", "Kicking", "Getting someone off a server for now.", None),
    ("banning", "Banning", "Bans on every server, IP bans and unbanning.", None),
    ("history", "Past games", "Pulling up any day's games, and downloading or uploading logs.", None),
    ("health", "Health and memory", "Uptime, crashes, the Monitor, and when a server needs a restart.", None),
    ("discord", "Discord", "What gets posted for staff, teamkill alerts, and linked accounts.", None),
    ("data", "Data", "The audit log, the Box dashboard, login attacks and IP lookups.", ("audit", "box", "ips")),
    ("settings", "Settings", "Server cards and their pictures, and the Sus check settings.", ("cards", "sus")),
    ("discord-messages", "Discord messages", "Every Discord page: what the bot posts, where, and staff alerts.", "discord"),
    ("discord-tutorial", "Discord tutorial", "Every Discord page step by step, with pictures. Also as a PDF.", "discord"),
    ("website", "The website", "The public site: its words, servers and rules.", "website"),
    ("game-admins", "In-game admins", "Making players admin in game on each server, through AMP.", "game_admins"),
    ("admins", "Admin accounts", "Making accounts, roles, and choosing what each person can do.", "users"),
]
GUIDE_SHOTS = HERE / "static" / "guide"


def guide_pages(request):
    def may(need):
        return need is None or any(allowed(request, n) for n in ((need,) if isinstance(need, str) else need))
    return [{"slug": slug, "title": title, "summary": summary} for slug, title, summary, need in GUIDE if may(need)]


def phone_shots():
    return {p.stem for p in (GUIDE_SHOTS / "phone").glob("*.jpg")}


async def guide(request):
    return render(request, "guide.html", pages=guide_pages(request))


async def guide_page(request):
    pages = guide_pages(request)
    slugs = [p["slug"] for p in pages]
    slug = request.match_info["page"]
    if slug not in slugs:
        raise web.HTTPNotFound(text="No such guide page.")
    at = slugs.index(slug)
    return render(request, f"guide/{slug}.html", pages=pages, page=pages[at], number=at + 1, phone_shots=phone_shots(),
                  prev=pages[at - 1] if at else None, next=pages[at + 1] if at + 1 < len(pages) else None)


async def dashboard_part(request):
    details = card_details(request.app[DB])
    states = [control_card(s, request.app[PICTURES], details) for s in request.app[MANAGER].states.values()]
    return render(request, "_cards.html", cards=states, amp=bool(request.app[AMP]), chosen_id=request.query.get("server", ""))


async def server_page(request):
    state = server_or_404(request, request.match_info["id"])
    db = request.app[DB]
    recent = db.audit(server=state.config.id, limit=15)
    return render(request, "server.html", s=server_view(state), card=control_card(state, request.app[PICTURES], card_details(db)), recent=recent,
                  health=health_data(db, state), alts=alt_flags(request, state),
                  played=played(request, [p["identity"] for p in state.players]), amp=bool(request.app[AMP]),
                  gb=GB, csp=MAP_CSP, feed=db.feed(state.config.id), **server_overview(request, state),
                  **history(request, state))


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
    links = request.app[DB].upload_links(state.config.id) if allowed(request, "ips") else []
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


def site_rules(request):
    """The rules the bot posts in Discord: as edited on Server info & rules, else the bot's own."""
    row = request.app[DB].discord_doc("serverinfo")
    published = json.loads(row["published"]) if row and row["published"] else {}
    return site.rules(published.get("rules") or (bridge(request).get("card") or {}).get("rules", ""))


def site_page(request, preview=False):
    doc = site_doc(request)
    html = request.app[JINJA].get_template("site.html").render(
        doc=doc, site=site_view(request, doc), rules=site_rules(request), board=request.app[STATS].weekly_board(),
        preview=preview,
        asset_version=ASSET_VERSION, year=time.strftime("%Y"))
    return web.Response(text=html, content_type="text/html")


async def site_workshop(request):
    """The Workshop mod browser on the public site: search, and one page per mod."""
    client, doc = request.app[WORKSHOP], site_doc(request)
    parts = request.path.rstrip("/").split("/")
    if len(parts) == 3 and parts[2]:
        try:
            mod = await client.mod(parts[2])
        except workshop.NotFound:
            raise web.HTTPNotFound(text="That mod isn't on the Workshop.")
        except workshop.WorkshopError as exc:
            return render_workshop(request, doc, error=str(exc))
        return render_workshop(request, doc, mod=mod)
    search = request.query.get("search", "")
    sort = request.query.get("sort", "popularity")
    try:
        page = max(1, int(request.query.get("page", "1")))
    except ValueError:
        page = 1
    try:
        found = await client.search(search, sort, page)
    except workshop.WorkshopError as exc:
        return render_workshop(request, doc, error=str(exc), search=search, sort=sort)
    return render_workshop(request, doc, found=found, links=workshop.paging(found, "/workshop"), search=search,
                           sort=sort)


def render_workshop(request, doc, **context):
    context.setdefault("found", None)
    context.setdefault("error", "")
    context.setdefault("search", "")
    context.setdefault("sort", "popularity")
    context.setdefault("sorts", workshop.SORTS)
    context.setdefault("links", {})
    html = request.app[JINJA].get_template("workshop_site.html").render(
        doc=doc, asset_version=ASSET_VERSION, year=time.strftime("%Y"), **context)
    return web.Response(text=html, content_type="text/html")


async def website_page(request):
    require(request, "website")
    return website_form(request, site_doc(request), [])


def website_form(request, doc, problems):
    labels = {s.get("id"): s.get("label") for s in bridge(request).get("servers", [])}
    servers = [{"id": s.id, "name": labels.get(s.id) or s.name} for s in request.app[CONFIG].servers]
    return render(request, "website.html", doc=doc, problems=problems, site_servers=servers,
                  hosts=request.app[CONFIG].site_hosts)


async def website_save(request):
    require(request, "website")
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
    require(request, "website")
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


MONITOR_RANGES = (("1h", "Last hour", 3600, "1 hour ago"), ("6h", "6 hours", 6 * 3600, "6 hours ago"),
                  ("24h", "24 hours", 86400, "24 hours ago"), ("7d", "7 days", 7 * 86400, "7 days ago"))
GB = 1024 ** 3


def monitor_problems(box, server, state):
    """Stretches worth a look, plainest first."""
    found = []

    def stretch(rows, test, title, why):
        hits = [r["at"] for r in rows if test(r)]
        if hits:
            found.append({"title": title, "why": why, "count": len(hits), "first": hits[0], "last": hits[-1]})

    stretch(server, lambda r: r["query_ok"] == 0, "The server browser couldn't reach it",
            "The outside check went unanswered. If the program was running at the time, the port or the network "
            "was down: players couldn't see or join the server.")
    stretch(server, lambda r: (r["sock_drops"] or 0) > 0, "Packets dropped on the game's own ports",
            "Players' packets reached the server but the game didn't read them in time, so the kernel threw them "
            "away. This is what cuts out radio and voice, and makes movement stutter.")
    stretch(server, lambda r: r["fps"] is not None and r["fps"] < 30, "Server FPS under 30",
            "The server was struggling to keep up: players feel this as lag, rubber-banding and late hits.")
    stretch(server, lambda r: r["proc_cpu"] is not None and r["proc_cpu"] >= 95, "The game program maxed out a core",
            "Reforger's server does most of its work on one core. Pinned at 100% it can't go any faster, and the "
            "frame rate drops: usually too many AI, vehicles or players for the machine.")
    stretch(box, lambda r: r["cpu"] is not None and r["cpu"] >= 90, "The whole box was nearly out of CPU",
            "Something on the machine is using everything: the game, another server, or a backup or update.")
    stretch(box, lambda r: (r["udp_errors"] or 0) > 0 or (r["udp_rcvbuf"] or 0) > 0,
            "Game packets were thrown away on arrival",
            "UDP packets reached the box but were dropped before the game read them, usually because the game was "
            "too busy to keep up or the receive buffer is too small. Players feel this as lag and desync.")
    stretch(box, lambda r: (r["rx_drop"] or 0) > 0, "The network card dropped packets",
            "Traffic arrived faster than the machine took it in. Heavy load, or a flood of traffic at the box.")
    stretch(box, lambda r: r["mem_total"] and r["mem_used"] / r["mem_total"] >= 0.92, "Memory nearly full",
            "With memory this full the machine starts swapping and everything slows down, the game included.")
    stretch(server, lambda r: (r["disconnects"] or 0) >= 5, "Several players dropped at once",
            "Five or more disconnects in 15 seconds: a network hiccup, a crash or a restart. The reasons are "
            "listed below.")
    return found


async def monitor_page(request):
    state = server_or_404(request, request.match_info["id"])
    key = request.query.get("range", "6h")
    key, label, seconds, ago = next((r for r in MONITOR_RANGES if r[0] == key), MONITOR_RANGES[1])
    until = now()
    since = until - seconds
    db = request.app[DB]
    box_rows = [dict(r) for r in db.box_samples(since)]
    server_rows = [dict(r) for r in db.server_samples(state.config.id, since)]
    reasons = Counter()
    for r in server_rows:
        if r["reasons"]:
            reasons.update({int(k): v for k, v in json.loads(r["reasons"]).items()})
    for r in box_rows:
        r["mem_gb"] = r["mem_used"] / GB if r["mem_used"] else None
    for r in server_rows:
        r["rss_gb"] = r["rss"] / GB if r["rss"] else None
        r["query_down"] = None if r["query_ok"] is None else (0 if r["query_ok"] else 1)
    box = monitor.downsample(box_rows, since, until,
                             ("cpu", "mem_gb", "rx_mbps", "tx_mbps", "rx_pps", "tx_pps",
                              "rx_drop", "udp_errors", "udp_rcvbuf", "udp_sndbuf"),
                             peak=("rx_drop", "udp_errors", "udp_rcvbuf", "udp_sndbuf"))
    srv = monitor.downsample(server_rows, since, until,
                             ("players", "fps", "proc_cpu", "rss_gb", "query_ms", "query_down", "disconnects", "errors",
                              "sock_drops"),
                             peak=("query_down", "disconnects", "errors", "query_ms", "sock_drops"))
    chart = lambda rows, series, floor=None: monitor.chart(rows, since, until, series, floor=floor)
    charts = [
        ("Players", chart(srv, [("players", "Players", "a")]), ""),
        ("Server FPS", chart(srv, [("fps", "FPS", "a")], floor=60),
         "" if any(r["fps"] for r in server_rows) else "The server isn't logging its frame rate. Add -logStats 10000 "
         "to its start parameters and restart it; then this fills in every 10 seconds."),
        ("Game program CPU (100% = one core)", chart(srv, [("proc_cpu", "Game", "a")], floor=100), ""),
        ("Whole box CPU", chart(box, [("cpu", "All cores", "b")], floor=100), ""),
        ("Memory (GB)", chart(srv, [("rss_gb", "Game", "a")]) if not box else
         monitor.chart(box + srv, since, until, [("mem_gb", "Whole box", "b"), ("rss_gb", "Game", "a")]), ""),
        ("Network (Mbps)", chart(box, [("rx_mbps", "In", "a"), ("tx_mbps", "Out", "b")]), ""),
        ("Packets per second", chart(box, [("rx_pps", "In", "a"), ("tx_pps", "Out", "b")]), ""),
        ("Lost packets per minute", chart(box, [("udp_errors", "UDP errors", "bad"), ("udp_rcvbuf", "UDP buffer full", "c"),
                                                ("rx_drop", "Card dropped", "b")]),
         "Should be flat at zero. Anything above it is game traffic the server never got."),
        ("Dropped on the game's ports (per minute)", chart(srv, [("sock_drops", "Dropped", "bad")]),
         "Should be flat at zero. This is the closest thing to a radio-cutting-out meter."),
        ("Outside check (ms)", chart(srv, [("query_ms", "Answer time", "a")]),
         "" if state.config.query_host else 'Not set up: add "query_host" (the server\'s public IP) and "query_port" '
         "(its A2S query port, often 17777) to this server in panel.local.json."),
        ("Disconnects and script errors (most in 15 s)", chart(srv, [("disconnects", "Disconnects", "c"),
                                                                     ("errors", "Script errors", "bad")]), ""),
    ]
    checked = [r for r in server_rows if r["query_ok"] is not None]
    latest_box = box_rows[-1] if box_rows else {}
    latest = server_rows[-1] if server_rows else {}
    return render(request, "monitor.html", s=server_view(state), ranges=MONITOR_RANGES, range_key=key,
                  range_label=label, range_ago=ago, charts=charts, problems=monitor_problems(box_rows, server_rows, state),
                  reasons=sorted(reasons.items(), key=lambda kv: -kv[1]), latest=latest, latest_box=latest_box,
                  reachable=(sum(r["query_ok"] for r in checked) / len(checked)) if checked else None,
                  gb=GB)


async def summary_part(request):
    state = server_or_404(request, request.match_info["id"])
    return render(request, "_summary.html", s=server_view(state), health=health_data(request.app[DB], state))


async def feed_part(request):
    state = server_or_404(request, request.match_info["id"])
    show = request.query.get("show", "all")
    return render(request, "_feed.html", s=server_view(state), feed=request.app[DB].feed(state.config.id, show=show), show=show)


def played(request, identities):
    return request.app[STATS].playtimes([i for i in identities if i])


def alt_flags(request, state):
    if not allowed(request, "ips"):
        return {}
    return request.app[DB].alt_summary([p["identity"] for p in state.players if p["identity"]])


async def players_part(request):
    state = server_or_404(request, request.match_info["id"])
    return render(request, "_players.html", s=server_view(state), alts=alt_flags(request, state),
                  played=played(request, [p["identity"] for p in state.players]))


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
    reason = clean(form.get("reason", ""))
    reason = with_appeal(reason) if reason else ""
    try:
        await request.app[MANAGER].kick(state.config.id, player)
    except RconError as exc:
        audit(request, "kick", state.config.id, name or player, str(exc), ok=False)
        flash(request, f"Kick failed: {exc}", "error")
    else:
        audit(request, "kick", state.config.id, name or player, reason)
        alert(request, f"Kick on {state.config.name}",
              f"**{name or player}** was kicked." + (f"\nReason: {reason}" if reason else ""))
        flash(request, f"Kicked {name or player}.")
    raise web.HTTPFound(f"/server/{state.config.id}")


# bans

BANS_PER_PAGE = 25


async def bans_page(request):
    db = request.app[DB]
    show_old = request.query.get("all") == "1"
    q = clean(request.query.get("q", ""), 64).lower()
    source = request.query.get("from", "")
    on_bm = db.bm_sources()
    found = []
    for b in db.bans(include_old=show_old):
        made_on_bm = on_bm.get(b["id"]) == "battlemetrics"
        if (source == "bm" and not made_on_bm) or (source == "panel" and made_on_bm):
            continue
        if q and not any(q in str(b[k] or "").lower() for k in ("name", "identity", "reason", "created_by")):
            continue
        found.append(b)
    pages = max(1, -(-len(found) // BANS_PER_PAGE))
    try:
        page = min(max(int(request.query.get("page", "1")), 1), pages)
    except ValueError:
        page = 1
    rows = [dict(b, synced=db.synced(b["id"]), ips=db.ban_ips(b["id"]), bm=on_bm.get(b["id"]))
            for b in found[(page - 1) * BANS_PER_PAGE:page * BANS_PER_PAGE]]
    keep = {k: v for k, v in (("all", "1" if show_old else ""), ("q", q), ("from", source)) if v}
    prefill = {k: clean(request.query.get(k, ""), 64) for k in ("identity", "name")}
    sync = request.app[BM]
    return render(request, "bans.html", bans=rows, show_old=show_old, durations=DURATIONS, ban_units=BAN_UNITS,
                  prefill=prefill, error="", bm=sync.status if sync else None, q=q, source=source,
                  page=page, pages=pages, total=len(found), keep=keep, synced_on=bool(on_bm))


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
    reason = with_appeal(reason)
    if db.active_ban(identity):
        flash(request, "That player is already banned. Unban them first to change the ban.", "error")
        raise web.HTTPFound("/bans")
    expires = now() + seconds if seconds else None
    length = length_text(seconds) if seconds else "Permanent"
    name = name or (db.player(identity) or {"name": ""})["name"]
    by = request[USER]["username"]
    ip_ban = form.get("ip") == "1" and allowed(request, "ips")
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
    """Whoever made a ban can reword it; anyone who may edit bans (owners) can change
    any ban's reason and length, without unbanning."""
    db = request.app[DB]
    ban = db.ban(int(request.match_info["ban_id"]))
    by = request[USER]["username"]
    if ban is None or ban["removed_at"] or (ban["expires_at"] and ban["expires_at"] <= now()):
        raise web.HTTPFound("/bans")
    any_ban = allowed(request, "edit_bans")
    if not any_ban and not (allowed(request, "ban") and ban["created_by"] == by):
        raise web.HTTPForbidden(text="Only the admin who made this ban, or a senior admin, can change it.")
    form = await request.post()
    reason = clean(form.get("reason", ""))
    if not reason:
        flash(request, "The reason can't be empty.", "error")
        raise web.HTTPFound("/bans")
    reason = with_appeal(reason)
    who = ban["name"] or ban["identity"]
    changes = []
    if reason != ban["reason"]:
        db.set_ban_reason(ban["id"], reason)
        changes.append(f"reason: {ban['reason']} → {reason}")
    if any_ban and form.get("duration", "keep") != "keep":
        seconds = ban_seconds(form)
        if seconds is None:
            flash(request, "Pick a length (a custom one needs a number, up to 10 years).", "error")
            raise web.HTTPFound("/bans")
        expires = now() + seconds if seconds else None
        db.set_ban_expiry(ban["id"], expires)
        results = await request.app[MANAGER].reban(db.ban(ban["id"]))
        summary = ", ".join(f"{k}: {v}" for k, v in results.items()) or "no servers set up"
        length = length_text(seconds) if seconds else "Permanent"
        changes.append(f"length: now {length} ({summary})")
    if changes:
        audit(request, "edit ban", target=who, detail="; ".join(changes))
        flash(request, f"Ban on {who} updated.")
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
    everyone = request.query.get("show") == "all"
    staff = request.app[MANAGER].staff_ids()
    hidden = sum(1 for p in players if p["identity"] in staff or not p["stands_out"])
    if not everyone:
        players = [p for p in players if p["stands_out"] and p["identity"] not in staff]
    return render(request, "unusual.html", players=players, typical=typical, metrics=outliers.METRICS,
                  periods=UNUSUAL_PERIODS, period=period, everyone=everyone, hidden=hidden, staff=staff,
                  scored=db.one("SELECT COUNT(*) AS n FROM scored_games")["n"],
                  archived=db.one("SELECT COUNT(*) AS n FROM log_archive")["n"])


async def players_page(request):
    q = clean(request.query.get("q", ""), 64)
    # The top bar's search also finds servers by name.
    server = next((st for st in request.app[MANAGER].states.values()
                   if q and q.lower() in (st.config.name.lower(), st.config.id.lower())), None)
    if server:
        raise web.HTTPFound(f"/?server={server.config.id}")
    rows = [dict(r) for r in request.app[DB].search_players(q, limit=50)]
    if q and re.fullmatch(r"[0-9a-fA-F.:]{3,}", q) and allowed(request, "ips"):
        seen = {r["identity"] for r in rows}
        rows += [dict(r) for r in request.app[DB].search_ip(q) if r["identity"] not in seen]
    if re.fullmatch(r"7656119\d{10}", q) and allowed(request, "ips"):
        seen = {r["identity"] for r in rows}
        rows += [dict(r) for r in request.app[DB].all(
            "SELECT p.* FROM players p JOIN platforms f ON f.identity = p.identity WHERE f.steam_id = ?", q)
            if r["identity"] not in seen]
    if q:
        seen = {r["identity"] for r in rows}
        rows += [dict(r, last_seen=0, last_server="") for r in request.app[STATS].search(q) if r["identity"] not in seen]
    return render(request, "players.html", q=q, rows=rows, played=played(request, [r["identity"] for r in rows]))


KILL_LOGS = (("tk_by", "Teamkills they did"), ("tk_on", "Who teamkilled them"), ("all", "All their kills and deaths"))
KILL_PERIODS = (("last", "Their last game"), ("1", "Last 24 hours"), ("7", "Last 7 days"), ("30", "Last 30 days"))
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
    groups = kill_log_groups(identity, lines)
    text = "\n".join(
        f"--- As {g['name']} · {g['first']} to {g['last']} · {g['kills']} kills, {g['deaths']} deaths ---\n"
        + "\n".join(f"{l['stamp']} [{l['server']}] {l['killer']} {l['what']}"
                     + ("" if l["row"]["killer"] == l["row"]["victim"] else f" {l['row']['victim_name']}")
                     + (f" ({l['extra']})" if l["extra"] else "") + (f" - {l['revenge']}" if l["revenge"] else "")
                     for l in g["lines"])
        for g in groups)
    return {"mode": mode, "title": dict(KILL_LOGS)[mode], "period": period, "day": day, "label": label,
            "lines": lines, "groups": groups, "names": len({g["name"] for g in groups}), "text": text,
            "full": len(rows) >= 500}


def kill_log_groups(identity, lines):
    """The log split wherever the player was going by a different name, newest
    first, so a rename stands out instead of hiding in the rows."""
    groups = []
    for l in lines:
        r = l["row"]
        own = r["killer_name"] if r["killer"] == identity else r["victim_name"] if r["victim"] == identity else ""
        name = own if own and own != "?" else "a name the log didn't record"
        if not groups or groups[-1]["name"] != name:
            groups.append({"name": name, "lines": [], "kills": 0, "deaths": 0})
        group = groups[-1]
        group["lines"].append(l)
        if r["killer"] != r["victim"]:
            group["kills"] += r["killer"] == identity
            group["deaths"] += r["victim"] == identity
    for group in groups:
        group["first"], group["last"] = group["lines"][0]["stamp"][:6], group["lines"][-1]["stamp"][:6]
        group["lines"].reverse()
    groups.reverse()
    return groups


INVESTIGATE_PERIODS = (("30", "Last 30 days"), ("90", "Last 90 days"), ("all", "Everything kept"))


async def investigate_page(request):
    """Investigate: the player and every account that has shared an IP with them."""
    require(request, "ips")
    identity = request.match_info["identity"].lower()
    if not valid_identity(identity):
        raise web.HTTPNotFound(text="No such player.")
    period = request.query.get("days", "30")
    period = period if period in dict(INVESTIGATE_PERIODS) else "30"
    since = 0 if period == "all" else now() - int(period) * 86400
    db = request.app[DB]
    subject = db.player(identity)
    linked = db.alts(identity)
    platforms = {}
    mine = db.platform(identity)
    same = db.same_steam(mine["steam_id"], identity) if mine else []
    ids = list(dict.fromkeys([identity] + [a["identity"] for a in linked] + [o["identity"] for o in same]))
    for i in ids:
        platforms[i] = db.platform(i)
    rows = {i: [dict(r) for r in db.kill_log(i, "all", since, limit=20000)] for i in ids}
    banned = {i: db.active_ban(i) is not None for i in ids}
    shared = {a["identity"]: (a["shared"] or "").split(",") for a in linked}
    accounts = []
    for i in ids:
        player = db.player(i)
        stats = investigate.kill_stats(i, rows[i])
        label = (player["name"] if player else "") or (stats["names"][0] if stats["names"] else i[:8])
        accounts.append({"identity": i, "label": label, "stats": stats, "banned": banned[i],
                         "names": list(dict.fromkeys(stats["names"] + [n["name"] for n in db.player_names(i)])),
                         "ips": db.ips(i), "bans": db.all("SELECT * FROM bans WHERE identity = ? ORDER BY id DESC", i),
                         "platform": platforms[i],
                         "relation": None if i == identity else investigate.relationship(
                             identity, i, rows, shared.get(i, []), platforms[identity], platforms[i])})
    everyone, typical = outliers.unusual(db.player_totals(since), db.incident_counts(since))
    labels = dict(outliers.METRICS)
    unusual = {p["identity"]: {**p, "labels": [labels[k] for k in p["high"]]} for p in everyone if p["identity"] in ids}
    names = {n for a in accounts for n in a["names"] + a["stats"]["names"] if n and n != "?"}
    sus = [r for r in db.all("SELECT * FROM feed WHERE kind = 'sus' AND at >= ? ORDER BY at DESC LIMIT 2000", since)
           if any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", r["text"]) for n in names)]
    incidents = [dict(r, who=a["label"]) for a in accounts for r in db.incidents_for(a["identity"]) if r["at"] >= since]
    found = investigate.findings(identity, accounts, unusual, sus, incidents)
    level, title, about = investigate.verdict(found)
    servers = {s.id: s.name for s in request.app[CONFIG].servers}
    audit(request, "investigate", target=accounts[0]["label"], detail=f"{len(ids)} account{'s' if len(ids) != 1 else ''}, {title}")
    return render(request, "investigate.html", identity=identity, subject=subject, accounts=accounts,
                  found=found, level=level, title=title, about=about, unusual=unusual, typical=typical,
                  metrics=outliers.METRICS, sus=sus, incidents=incidents, server_names=servers,
                  periods=INVESTIGATE_PERIODS, period=period,
                  text=investigate.report_text(accounts[0]["label"], title, found, accounts))


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
    fight = db.combat(identity)
    fight.update(hit=body_map(fight["hit"]), hit_by=body_map(fight["hit_by"]))
    return render(request, "player.html", identity=identity, player=db.player(identity), combat=fight,
                  average=combat_average(db),
                  stats=stats, stats_here=oyb.available, linked_via=linked_via,
                  ips=db.ips(identity) if allowed(request, "ips") else [],
                  banned_ips=db.banned_ips() if allowed(request, "ips") else set(),
                  alts=db.alts(identity) if allowed(request, "ips") else [],
                  names=db.player_names(identity), notes=db.notes(identity), bans=bans,
                  platform=db.platform(identity),
                  same_steam=db.same_steam((db.platform(identity) or {"steam_id": ""})["steam_id"], identity)
                  if allowed(request, "ips") else [],
                  incidents=db.incidents_for(identity), kills=kill_log(request, identity),
                  kill_logs=KILL_LOGS, kill_periods=KILL_PERIODS,
                  active=db.active_ban(identity), durations=DURATIONS,
                  staff=identity in db.staff_ids(), config_staff=identity in request.app[MANAGER].config_admins())


async def set_staff(request):
    require(request, "users")
    identity = request.match_info["identity"].lower()
    if not valid_identity(identity):
        raise web.HTTPNotFound(text="No such player.")
    on = (await request.post()).get("on") == "1"
    db = request.app[DB]
    db.set_staff(identity, request[USER]["username"], on)
    name = (db.player(identity) or {"name": identity})["name"]
    audit(request, "marked staff" if on else "unmarked staff", target=name)
    flash(request, f"{name} {'is now marked as an admin' if on else 'is no longer marked as an admin'}. "
                   "Server pages show it within a few seconds.")
    raise web.HTTPFound(f"/player/{identity}")


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

async def amp_servers(request):
    """Each server with its AMP admin list, or why it couldn't be read."""
    amp = request.app[AMP]
    states = list(request.app[MANAGER].states.values())
    try:
        instances = await amp.instances()
    except AmpError as exc:
        return [], str(exc)

    async def one(state):
        name = state.config.amp_instance or amp_instance_name(state.config.log_dir)
        row = {"state": state, "instance": instances.get(name), "ids": [], "error": "", "waiting": False}
        if not row["instance"]:
            row["error"] = f"No AMP instance called {name}." if name else "The panel doesn't know which AMP instance this is."
            return row
        try:
            ids = await amp.admins(row["instance"])
        except AmpError as exc:
            row["error"] = str(exc)
            return row
        live = request.app[MANAGER].server_config_admins(state)
        db = request.app[DB]
        row["ids"] = [{"id": i, "name": (db.player(i) or {"name": ""})["name"] if valid_identity(i) else "",
                       "steam": not valid_identity(i)} for i in ids]
        row["waiting"] = None if live is None else set(ids) != live
        return row
    return await asyncio.gather(*(one(s) for s in states)), ""


async def game_admins_page(request):
    require(request, "game_admins")
    if not request.app[AMP]:
        return render(request, "game_admins.html", rows=[], error="", setup=True, max_admins=MAX_ADMINS)
    rows, error = await amp_servers(request)
    return render(request, "game_admins.html", rows=rows, error=error, setup=False, max_admins=MAX_ADMINS)


async def amp_instance_for(request, server_id):
    state = server_or_404(request, server_id)
    name = state.config.amp_instance or amp_instance_name(state.config.log_dir)
    instance = (await request.app[AMP].instances()).get(name)
    if not instance:
        raise AmpError(f"AMP has no instance called {name or '(unknown)'} for {state.config.name}.")
    return state, instance


async def add_game_admin(request):
    require(request, "game_admins")
    if not request.app[AMP]:
        raise web.HTTPFound("/game-admins")
    form = await request.post()
    db = request.app[DB]
    typed = clean(form.get("player", ""), 64)
    identity = form.get("identity", "").strip().lower()
    if valid_identity(typed.lower()):
        identity = typed.lower()
    elif not valid_identity(identity):
        found = db.by_name(typed) if typed else []
        if len(found) != 1:
            flash(request, "Pick the player from the list as you type, or paste their identity ID." if not found else
                  f"{len(found)} players have gone by {typed}. Pick the right one from the list.", "error")
            raise web.HTTPFound("/game-admins")
        identity = found[0]["identity"]
    chosen = [s for s in form.getall("server", []) if request.app[CONFIG].server(s)]
    if not chosen:
        flash(request, "Tick at least one server.", "error")
        raise web.HTTPFound("/game-admins")
    name = (db.player(identity) or {"name": identity})["name"]
    done = []
    for server_id in chosen:
        try:
            state, instance = await amp_instance_for(request, server_id)
            await request.app[AMP].change_admins(instance, add=[identity])
        except AmpError as exc:
            audit(request, "made game admin", server_id, name, str(exc), ok=False)
            flash(request, f"{request.app[CONFIG].server(server_id).name}: {exc}", "error")
            continue
        audit(request, "made game admin", server_id, name, identity)
        done.append(state.config.name)
    if done:
        alert(request, f"In-game admin added: {name}", f"On {', '.join(done)}. Applies when the server restarts.")
        flash(request, f"{name} is on the admin list for {', '.join(done)}. It applies when the server restarts.")
    raise web.HTTPFound("/game-admins")


async def remove_game_admin(request):
    require(request, "game_admins")
    if not request.app[AMP]:
        raise web.HTTPFound("/game-admins")
    identity = clean((await request.post()).get("identity", ""), 64).lower()
    name = (request.app[DB].player(identity) or {"name": identity})["name"]
    try:
        state, instance = await amp_instance_for(request, request.match_info["id"])
        await request.app[AMP].change_admins(instance, remove=[identity])
    except AmpError as exc:
        audit(request, "removed game admin", request.match_info["id"], name, str(exc), ok=False)
        flash(request, str(exc), "error")
        raise web.HTTPFound("/game-admins")
    audit(request, "removed game admin", state.config.id, name, identity)
    alert(request, f"In-game admin removed: {name}", f"From {state.config.name}. Applies when the server restarts.",
          colour=RED)
    flash(request, f"{name} is off the admin list for {state.config.name}. It applies when the server restarts.")
    raise web.HTTPFound("/game-admins")


async def restart_through_amp(request):
    require(request, "power")
    if not request.app[AMP]:
        raise web.HTTPFound("/game-admins")
    try:
        state, instance = await amp_instance_for(request, request.match_info["id"])
        await request.app[AMP].restart(instance)
    except AmpError as exc:
        audit(request, "restart through AMP", request.match_info["id"], detail=str(exc), ok=False)
        flash(request, f"Restart failed: {exc}", "error")
        raise web.HTTPFound("/game-admins")
    audit(request, "restart through AMP", state.config.id)
    alert(request, f"Restart: {state.config.name}", "Through AMP, to apply the admin list.")
    flash(request, f"{state.config.name} is restarting. It's back in a couple of minutes.")
    raise web.HTTPFound("/game-admins")



CONTROL_SECONDS = 3600
CONTROL_ACTIONS = {"start": "Starting", "restart": "Restarting", "stop": "Stopping", "update": "Updating"}


PICTURE_TYPES = {"jpg": b"\xff\xd8\xff", "png": b"\x89PNG", "webp": b"RIFF"}
PICTURE_LIMIT = 5 * 1024 * 1024


def picture_file(folder, name):
    """A card picture on disk: <server id>.jpg/.png/.webp, or default.* for every server."""
    for ext in PICTURE_TYPES:
        path = folder / f"{name}.{ext}"
        if path.is_file():
            return path
    return None


def server_image(folder, server_id):
    """The address of a server's card picture: its own, else the uploaded default,
    else the dark forest that ships with the panel."""
    for name in (server_id, "default"):
        path = picture_file(folder, name)
        if path:
            return f"/server-picture/{path.name}?v={int(path.stat().st_mtime)}"
    return "/static/card-default.jpg"


def card_details(db):
    """What senior admins wrote for each server's card: a description and the join address."""
    row = db.discord_doc("cards")
    try:
        saved = json.loads(row["published"]) if row and row["published"] else {}
    except ValueError:
        saved = {}
    return saved if isinstance(saved, dict) else {}


def control_card(state, pictures=None, details=None):
    """One server's card on the home page."""
    q = state.query if (state.query or {}).get("ok") else {}
    view = server_view(state)
    mine = (details or {}).get(state.config.id) or {}
    return {**view, "image": server_image(pictures, state.config.id) if pictures else None,
            "description": mine.get("description", ""), "address": mine.get("address", ""),
            "version": q.get("version") or "",
            "count": len(state.players) if state.online else 0, "slots": q.get("slots"),
            "map": q.get("map"), "ping": q.get("ms"), "unreachable": state.unreachable >= UNREACHABLE_AFTER}


def server_overview(request, state):
    """The Overview tab of a server's page: its numbers, a map of recent kills,
    who's on and the last hour as charts."""
    db = request.app[DB]
    until = now()
    since = until - CONTROL_SECONDS
    rows = [dict(r) for r in db.server_samples(state.config.id, since)]
    for r in rows:
        r["rss_gb"] = r["rss"] / GB if r.get("rss") else None
    box_rows = [dict(r) for r in db.box_samples(since)]
    srv = monitor.downsample(rows, since, until, ("players", "proc_cpu", "rss_gb"))
    for r in box_rows:
        r["net_mbs"] = ((r.get("rx_mbps") or 0) + (r.get("tx_mbps") or 0)) / 8 if r.get("rx_mbps") is not None else None
    box = monitor.downsample(box_rows, since, until, ("net_mbs",))
    chart = lambda data, series, floor=None: monitor.chart(data, since, until, series, floor=floor, height=90)
    slots = (state.query or {}).get("slots") if (state.query or {}).get("ok") else None
    total_gb = box_rows[-1]["mem_total"] / GB if box_rows and box_rows[-1].get("mem_total") else None
    charts = [
        ("CPU usage", "cpu", chart(srv, [("proc_cpu", "Game", "a")], floor=100), "%"),
        ("Memory usage", "mem", chart(srv, [("rss_gb", "In use", "c")], floor=total_gb), " GB"),
        ("Players", "players", chart(srv, [("players", "Players", "ok")], floor=slots), ""),
        ("Network", "net", chart(box, [("net_mbs", "In and out", "c")]), " MB/s"),
    ]
    latest = rows[-1] if rows else {}
    box_latest = box_rows[-1] if box_rows else {}
    card = control_card(state)
    players = sorted(({"name": p["name"], "identity": p["identity"],
                       "played": now() - state.joined.get(p["identity"] or p["name"], now())}
                      for p in state.players), key=lambda p: -p["played"]) if state.online else []
    try:
        disk = shutil.disk_usage(state.config.log_dir or "/")
    except OSError:
        disk = None
    info = {"online": state.online, "up_for": card["up_for"], "map": card["map"], "players": len(players),
            "version": card["version"], "cores": os.cpu_count() or 1,
            "admins": [a["name"] for a in state.admins] if state.online else [],
            "disk_used": disk.used if disk else None, "disk_total": disk.total if disk else None,
            "slots": card["slots"], "fps": card["fps"], "cpu": latest.get("proc_cpu"),
            "memory": state.memory or latest.get("rss"), "mem_total": box_latest.get("mem_total"),
            "disk": box_latest.get("disk_pct"), "ping": card["ping"]}
    return {"info": info, "roster": players, "charts": charts, "has_map": mortar_map() is not None,
            "live_feed": db.feed(state.config.id)}


def attention(request, states):
    """What needs someone now, worst first: each is (level, text, when, link)."""
    db, out = request.app[DB], []
    for st in states:
        name = st.config.name
        if st.config.configured and not st.online:
            out.append(("bad", f"{name} is offline" + (f": {st.error}" if st.error else ""), "", f"/server/{st.config.id}"))
        elif st.unreachable >= UNREACHABLE_AFTER:
            out.append(("bad", f"{name} is running but can't be reached from outside", "", f"/server/{st.config.id}/monitor"))
        if st.online and len(st.players) >= 10 and not st.admins:
            out.append(("warn", f"No admin on {name} · {len(st.players)} players", "", f"/server/{st.config.id}#live"))
        if st.check and st.check.get("status") == "leak":
            out.append(("warn", f"{name}: memory didn't clear after the last mission restart", "", f"/server/{st.config.id}#health"))
    sus = db.all("SELECT server, at, text FROM feed WHERE kind = 'sus' AND at >= ? ORDER BY at DESC", now() - 3600)
    if sus:
        out.append(("warn", f"{len(sus)} sus flag{'s' if len(sus) != 1 else ''} in the last hour. Latest: {sus[0]['text'][:110]}",
                    _clock(sus[0]["at"]), f"/server/{sus[0]['server']}#live"))
    if allowed(request, "ips"):
        for a in db.attackers(now() - ATTACK_WINDOW, limit=3):
            if a["n"] >= ATTACK_LOGINS:
                out.append(("warn", f"Login attack on {a['sources'].replace(',', ' and ')} from {a['ip']} · {a['n']} tries",
                            _clock(a["last"]), "/attacks"))
    out.sort(key=lambda x: x[0] != "bad")
    return out



async def pictures_page(request):
    require(request, "cards")
    folder = request.app[PICTURES]
    details = card_details(request.app[DB])
    rows = [{"id": "default", "name": "Every server (used when a server has no picture of its own)",
             "image": server_image(folder, "default") if picture_file(folder, "default") else None}]
    rows += [{"id": st.config.id, "name": st.config.name, **(details.get(st.config.id) or {}),
              "image": server_image(folder, st.config.id) if picture_file(folder, st.config.id) else None}
             for st in request.app[MANAGER].states.values()]
    return render(request, "pictures.html", rows=rows, limit=PICTURE_LIMIT // (1024 * 1024))


def picture_target(request):
    target = request.match_info["id"]
    if target != "default" and target not in request.app[MANAGER].states:
        raise web.HTTPNotFound(text="No such server.")
    return target


async def upload_picture(request):
    require(request, "cards")
    target = picture_target(request)
    folder = request.app[PICTURES]
    folder.mkdir(parents=True, exist_ok=True)
    reader = await request.multipart()
    part = await reader.next()
    while part is not None and part.name != "picture":
        part = await reader.next()
    if part is None or not part.filename:
        flash(request, "Choose a picture to upload.", "error")
        raise web.HTTPFound("/settings/pictures")
    temp = folder / f".{secrets.token_hex(8)}.upload"
    size = 0
    try:
        with open(temp, "wb") as out:
            while chunk := await part.read_chunk(1 << 16):
                size += len(chunk)
                if size > PICTURE_LIMIT:
                    raise UploadError(f"That picture is over {PICTURE_LIMIT // (1024 * 1024)} MB.")
                out.write(chunk)
        with open(temp, "rb") as fh:
            head = fh.read(12)
        ext = next((e for e, magic in PICTURE_TYPES.items() if head.startswith(magic)), None)
        if ext == "webp" and head[8:12] != b"WEBP":
            ext = None
        if not ext:
            raise UploadError("That isn't a JPG, PNG or WebP picture.")
        old = picture_file(folder, target)
        if old:
            old.unlink()
        temp.replace(folder / f"{target}.{ext}")
    except UploadError as exc:
        flash(request, str(exc), "error")
        raise web.HTTPFound("/settings/pictures")
    finally:
        temp.unlink(missing_ok=True)
    audit(request, "card picture", None if target == "default" else target, detail=f"{size // 1024} KB")
    flash(request, "Picture saved. It shows on the card straight away.")
    raise web.HTTPFound("/settings/pictures")


ADDRESS = re.compile(r"[A-Za-z0-9.\-]{1,253}:\d{2,5}")


async def save_card_details(request):
    require(request, "cards")
    target = picture_target(request)
    if target == "default":
        raise web.HTTPNotFound()
    form = await request.post()
    address = clean(form.get("address", ""), 270).replace(" ", "")
    if address and not ADDRESS.fullmatch(address):
        flash(request, "The join address has to look like 185.123.45.67:2001.", "error")
        raise web.HTTPFound("/settings/pictures")
    details = card_details(request.app[DB])
    details[target] = {"description": clean(form.get("description", ""), 80), "address": address}
    request.app[DB].publish_discord_doc("cards", json.dumps(details), request[USER]["username"])
    audit(request, "card details", target, detail=f"{details[target]['description']} {address}".strip())
    flash(request, "Saved. It shows on the card straight away.")
    raise web.HTTPFound("/settings/pictures")


async def remove_picture(request):
    require(request, "cards")
    target = picture_target(request)
    old = picture_file(request.app[PICTURES], target)
    if old:
        old.unlink()
        audit(request, "removed card picture", None if target == "default" else target)
    flash(request, "Picture removed.")
    raise web.HTTPFound("/settings/pictures")


async def picture_serve(request):
    name = request.match_info["name"]
    stem, _, ext = name.rpartition(".")
    path = picture_file(request.app[PICTURES], stem) if ext in PICTURE_TYPES else None
    if not path or path.name != name:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})

async def control_redirect(request):
    raise web.HTTPFound("/")


async def control_power(request):
    require(request, "power")
    action = request.match_info["action"]
    back = request.headers.get("Referer", "/") if request.headers.get("Referer", "").startswith(site_url(request)) else "/"
    if action not in CONTROL_ACTIONS:
        raise web.HTTPNotFound()
    if not request.app[AMP]:
        flash(request, "The panel isn't connected to AMP, so it can't start or stop servers.", "error")
        raise web.HTTPFound(back)
    try:
        state, instance = await amp_instance_for(request, request.match_info["id"])
        await request.app[AMP].power(instance, action)
    except AmpError as exc:
        audit(request, f"{action} through AMP", request.match_info["id"], detail=str(exc), ok=False)
        flash(request, f"That didn't work: {exc}", "error")
        raise web.HTTPFound(back)
    audit(request, f"{action} through AMP", state.config.id)
    alert(request, f"{CONTROL_ACTIONS[action]} {state.config.name}", "From the panel.",
          colour=RED if action == "stop" else GOLD)
    flash(request, f"{CONTROL_ACTIONS[action]} {state.config.name}. AMP takes a minute or two.")
    raise web.HTTPFound(back)


# The log's hit zones, drawn as nine parts of a body and summed into four groups.
BODY_PARTS = {"head": ("Head",), "neck": ("Neck",), "chest": ("Chest",), "abdomen": ("Abdomen",), "hips": ("Hips",),
              "larm": ("LArm", "LForearm", "LHand"), "rarm": ("RArm", "RForearm", "RHand"),
              "lleg": ("LThigh", "LCalf", "LFoot"), "rleg": ("RThigh", "RCalf", "RFoot")}
BODY_GROUPS = (("Head", ("head", "neck")), ("Torso", ("chest", "abdomen", "hips")), ("Arms", ("larm", "rarm")),
               ("Legs", ("lleg", "rleg")))


def body_map(zones):
    counts = {part: sum(zones.get(z, 0) for z in names) for part, names in BODY_PARTS.items()}
    total = sum(counts.values())
    if not total:
        return None
    top = max(counts.values())
    return {"total": total, "share": {p: n / total for p, n in counts.items()},
            "heat": {p: n / top for p, n in counts.items()},
            "groups": [(label, sum(counts[p] for p in parts), sum(counts[p] for p in parts) / total)
                       for label, parts in BODY_GROUPS]}


_average = {"at": 0, "db": None, "value": None}


def combat_average(db):
    """Everyone's typical numbers, worked out at most every ten minutes."""
    if now() - _average["at"] > 600 or _average["db"] is not db:
        _average.update(at=now(), db=db, value=db.combat_average())
    return _average["value"]


# How far back the kill map can look, by the key its picker sends.
KILL_RANGES = {"30m": 1800, "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
KILL_MAP_LIMIT = 3000


async def control_kills(request):
    state = server_or_404(request, request.match_info["id"])
    since = now() - KILL_RANGES.get(request.query.get("range", "30m"), 1800)
    rows = request.app[DB].map_kills(state.config.id, since, KILL_MAP_LIMIT)
    return web.json_response({"capped": len(rows) == KILL_MAP_LIMIT, "kills": [{
        "at": r["at"], "teamkill": r["kind"] == "teamkill", "ai": not r["killer"],
        "killer": r["killer_name"] or "", "killer_id": r["killer"], "victim": r["victim_name"], "victim_id": r["victim"],
        "how": DAMAGE.get(r["damage"], (r["damage"] or "").lower()),
        "distance": round(r["distance"]) if r["distance"] else None, "zone": r["zone"] or None,
        "from": [r["killer_x"], r["killer_y"]] if r["killer_x"] is not None else None,
        "to": [r["victim_x"], r["victim_y"]]} for r in rows]})


async def control_map(request):
    mapped = mortar_map()
    if mapped is None:
        raise web.HTTPNotFound()
    return web.json_response({"map": mapped.describe()})


async def control_tile(request):
    mapped = mortar_map()
    path = mapped and tile_path(mapped, request.match_info["z"], request.match_info["x"], request.match_info["y"])
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})


async def control_picture(request):
    mapped = mortar_map()
    path = mapped and image_path(mapped)
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


async def control_leaflet(request):
    name = request.match_info["name"]
    if name not in ("leaflet.css", "leaflet.js"):
        raise web.HTTPNotFound()
    return web.FileResponse(MORTAR_STATIC / name, headers={"Content-Type": MORTAR_FILES[name],
                                                            "Cache-Control": "private, max-age=86400"})

def mortar_number(text, name, low=None, high=None):
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} has to be a number.")
    if value != value or (low is not None and value < low) or (high is not None and value > high):
        raise ValueError(f"{name} is out of range.")
    return value


async def mortar_page(request):
    """The mortar calculator, for owners to try before /mortar goes out in Discord.
    Same engine and tables as the command; nothing here is saved."""
    require(request, "mortar")
    loaded = profiles()
    q = request.query
    form = {k: clean(q.get(k, ""), 20) for k in ("tube", "gun", "target", "gun_alt", "target_alt", "wind", "wind_from")}
    weapon = loaded.get(form["tube"]) or next(iter(loaded.values()), None)
    result, error = None, ""
    if weapon and form["gun"] and form["target"]:
        try:
            gun, target = parse_grid(form["gun"]), parse_grid(form["target"])
            climb = (mortar_number(form["target_alt"], "Target height", -500, 1000)
                     - mortar_number(form["gun_alt"], "Mortar height", -500, 1000))
            wind = Wind(speed=mortar_number(form["wind"], "Wind speed", 0, 40),
                        bearing=mortar_number(form["wind_from"], "Wind direction", 0, 360) % 360)
            mils, distance = bearing(gun, target, weapon.mils)
            fired = apply_wind(weapon, distance, mils * 360 / weapon.mils, wind, climb)
            result = {"gun": gun, "target": target, "distance": distance, "climb": climb, "wind": wind,
                      "azimuth": (mils + fired.azimuth_mils) % weapon.mils, "base_azimuth": mils,
                      "degrees": ((mils + fired.azimuth_mils) % weapon.mils) * 360 / weapon.mils, "fired": fired,
                      "others": [r for r in fired.every if fired.final and r.ring != fired.final.ring]}
        except ValueError as exc:
            error = str(exc)
    ground = heights.load() is not None
    if not ground:
        asyncio.get_running_loop().run_in_executor(None, heights.ensure)
    return render(request, "mortar.html", loaded=loaded, weapon=weapon, form=form, result=result, error=error,
                  ground=ground, out_of_range=OUT_OF_RANGE, mapped=mortar_map(), job=maptiles.read_status(),
                  sharp=maptiles.SHARP, free=maptiles.free_memory(), sharp_memory=maptiles.SHARP_MEMORY)


MORTAR_STATIC = HERE.parent / "bot" / "web" / "static"
MORTAR_FILES = {"mortar.css": "text/css", "mortar.js": "application/javascript",
                "leaflet.css": "text/css", "leaflet.js": "application/javascript"}
# Leaflet positions everything with inline styles and the markers carry their own.
MAP_CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "frame-ancestors 'none'; form-action 'self'")


def mortar_map():
    """The calibrated map, picking up tiles the download has only just finished."""
    mapped = calibration()
    if mapped is None:
        calibration.cache_clear()
        mapped = calibration()
    return mapped


async def mortar_download(request):
    require(request, "mortar")
    if maptiles.read_status().get("state") in maptiles.RUNNING:
        flash(request, "The map is already being prepared.")
        raise web.HTTPFound("/mortar")
    form = await request.post()
    zoom = maptiles.SHARP if form.get("zoom") == str(maptiles.SHARP) else maptiles.ZOOM
    if zoom == maptiles.SHARP and (maptiles.free_memory() or 0) < maptiles.SHARP_MEMORY:
        flash(request, "This box doesn't have enough free memory for the sharper map right now.", "error")
        raise web.HTTPFound("/mortar")
    maptiles.OUT.mkdir(parents=True, exist_ok=True)
    with open(maptiles.OUT / "everon.log", "ab") as log_file:
        await asyncio.create_subprocess_exec(
            sys.executable, "-m", "panel.maptiles", "--zoom", str(zoom), cwd=str(HERE.parent), stdout=log_file,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True, preexec_fn=lambda: os.nice(10))
    audit(request, "download mortar map")
    flash(request, "Preparing the map. It takes about 10 to 15 minutes; this page shows how far it's got.")
    raise web.HTTPFound("/mortar")


async def mortar_map_page(request):
    require(request, "mortar")
    if mortar_map() is None:
        raise web.HTTPFound("/mortar")
    page = (MORTAR_STATIC / "mortar.html").read_text(encoding="utf-8")
    page = page.replace('data-token="__TOKEN__"', f'data-token="panel" data-csrf="{request[CSRF]}"')
    # Phones hold on to the old CSS and JS; a new file gets a new address.
    for name in MORTAR_FILES:
        version = int((MORTAR_STATIC / name).stat().st_mtime)
        page = page.replace(f'"/static/{name}"', f'"/mortar/panel/static/{name}?v={version}"')
    return web.Response(text=page, content_type="text/html", headers={"Content-Security-Policy": MAP_CSP})


async def mortar_asset(request):
    require(request, "mortar")
    name = request.match_info["name"]
    if name not in MORTAR_FILES:
        raise web.HTTPNotFound()
    return web.FileResponse(MORTAR_STATIC / name, headers={"Content-Type": MORTAR_FILES[name]})


async def mortar_loadouts(request):
    require(request, "mortar")
    mapped = mortar_map()
    if mapped is None:
        raise web.HTTPNotFound()
    return web.json_response({"map": mapped.describe(), "tubes": mortar_server.catalogue()})


async def mortar_tile(request):
    require(request, "mortar")
    mapped = mortar_map()
    path = mapped and tile_path(mapped, request.match_info["z"], request.match_info["x"], request.match_info["y"])
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=604800"})


async def mortar_picture(request):
    require(request, "mortar")
    mapped = mortar_map()
    path = mapped and image_path(mapped)
    if not path:
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


async def mortar_calculate(request):
    require(request, "mortar")
    return await mortar_answer(request)


async def site_mortar(request):
    """The mortar map on the public site, for anyone: what /mortar in Discord links to."""
    path, method = request.path.rstrip("/") or "/", request.method
    if method == "GET" and path == "/mortar":
        if mortar_map() is None:
            return web.Response(text="The mortar map isn't ready yet. Use /mortar in Discord and enter grids instead.",
                                status=503)
        page = (MORTAR_STATIC / "mortar.html").read_text(encoding="utf-8")
        page = page.replace('data-token="__TOKEN__"', 'data-token="site" data-base="/mortar" data-api="/mortar/calculate"')
        for name in MORTAR_FILES:
            version = int((MORTAR_STATIC / name).stat().st_mtime)
            page = page.replace(f'"/static/{name}"', f'"/mortar/static/{name}?v={version}"')
        return web.Response(text=page, content_type="text/html", headers={"Content-Security-Policy": MAP_CSP})
    parts = path.split("/")[2:]
    if method == "GET" and len(parts) == 2 and parts[0] == "static" and parts[1] in MORTAR_FILES:
        return web.FileResponse(MORTAR_STATIC / parts[1], headers={"Content-Type": MORTAR_FILES[parts[1]],
                                                                   "Cache-Control": "public, max-age=31536000"})
    mapped = mortar_map()
    if method == "GET" and parts == ["loadouts"] and mapped is not None:
        return web.json_response({"map": mapped.describe(), "tubes": mortar_server.catalogue()})
    if method == "GET" and len(parts) == 4 and parts[0] == "tiles" and mapped is not None:
        tile = tile_path(mapped, *parts[1:])
        if tile:
            return web.FileResponse(tile, headers={"Cache-Control": "public, max-age=604800"})
    if method == "GET" and parts == ["map"] and mapped is not None and image_path(mapped):
        return web.FileResponse(image_path(mapped), headers={"Cache-Control": "public, max-age=86400"})
    if method == "POST" and parts == ["calculate"]:
        return await mortar_answer(request)
    raise web.HTTPNotFound(text="Not found.")


async def mortar_answer(request):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return web.json_response({"valid": False, "reason": "bad_request"}, status=400)
    try:
        return web.json_response(mortar_server.calculate(body, mortar_map()))
    except Exception:
        log.exception("Mortar solution failed")
        return web.json_response({"valid": False, "reason": "failed"}, status=500)


SUS_GROUPS = (
    ("Teamkills", "One player teamkilling this many times within 10 minutes starts a line that keeps counting their "
     "teamkills for the rest of the game. Crashing into teammates doesn't count.",
     (("teamkills", "Teamkills", ""),)),
    ("Headshots", "Lots of rifle kills in 15 minutes with most of them to the head.",
     (("headshot_kills", "Kills at least", ""), ("headshot_share", "Headshots at least", "%"))),
    ("Rapid kills", "Many rifle kills in a very short time.",
     (("rapid_kills", "Kills", ""), ("rapid_window", "Within", "seconds"))),
    ("Long shots", "Several bullet kills from very far away in 15 minutes.",
     (("long_shot", "Further than", "metres"), ("long_shots", "How many", ""))),
    ("Teleporting", "A kill far from the same player's last kill, sooner than anything in the game could carry them, "
     "without dying in between. A helicopter does about 80 m/s.",
     (("teleport_distance", "Moved at least", "metres"), ("teleport_speed", "Faster than", "m/s"))),
    ("Victims far apart", "Two of one player's victims dying far apart from each other at almost the same moment.",
     (("spread", "Apart by at least", "metres"), ("spread_window", "Within", "seconds"))),
    ("Explosions credited to AI", "The pattern from the old cheater: players blown up with the kill credited to AI, "
     "so it never shows in the kill feed.",
     (("ai_explosions", "Deaths", ""), ("ai_explosions_window", "Within", "seconds"),
      ("same_second", "Or in the same second", "deaths"))),
    ("Script errors", "The server hitting script errors in this many separate seconds within 5 minutes.",
     (("script_errors", "Seconds with errors", ""),)),
    ("Everything", "How long before the same flag can fire again, and kills credited from further than this are "
     "ignored (they're the game mixing up who did it).",
     (("cooldown", "Same flag again after", "seconds"), ("max_distance", "Ignore kills from further than", "metres"))),
)


def sus_view(request, values, problems=()):
    shown = {k: round(v * 100) if k == "headshot_share" else v for k, v in values.items()}
    return render(request, "sus_settings.html", groups=SUS_GROUPS, values=shown, limits=SUS_LIMITS,
                  defaults={k: round(v * 100) if k == "headshot_share" else v for k, v in SUS_DEFAULTS.items()},
                  problems=problems)


async def sus_page(request):
    require(request, "sus")
    return sus_view(request, request.app[MANAGER].sus_settings())


async def sus_save(request):
    require(request, "sus")
    form = await request.post()
    if form.get("action") == "reset":
        raw = {}
    else:
        raw = {k: form.get(k, "") for k in SUS_LIMITS}
        if raw["headshot_share"] not in ("", None):
            try:
                raw["headshot_share"] = float(raw["headshot_share"]) / 100
            except ValueError:
                pass
    values, problems = check_sus(raw)
    if problems:
        return sus_view(request, {**request.app[MANAGER].sus_settings(), **values}, problems)
    # Only what differs from the defaults is kept, so a later change to a default still reaches it.
    base = {**SUS_DEFAULTS, **request.app[CONFIG].suspicion}
    changed = {k: v for k, v in values.items() if v != base[k]}
    request.app[DB].publish_discord_doc("sus", json.dumps(changed), request[USER]["username"])
    audit(request, "changed Sus settings", detail=", ".join(f"{k}={v}" for k, v in changed.items()) or "all defaults")
    flash(request, "Saved. The checks use the new settings from now on." if raw else "Back to the default settings.")
    raise web.HTTPFound("/sus-settings")


DISCORD_PAGES = (("welcome", "Start here message"), ("serverinfo", "Server info & rules"), ("names", "Server names"),
                 ("factions", "Factions"), ("matchping", "Match alerts"), ("weekly", "Weekly top 3"), ("staffalerts", "Staff alerts"), ("feedback", "Feedback"), ("greeting", "Join greeting"), ("bans", "Ban messages"),
                 ("posts", "Posts"), ("links", "Link requests"), ("channels", "Channels & roles"))


def bridge(request):
    """What the bot last reported: roles, channels, and how publishing went."""
    path = Path(request.app[CONFIG].oyb_data, "panel_bridge.json")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    data["age"] = now() - int(data.get("updated", 0))
    if not isinstance(data.get("channels"), list):
        # An older bot wrote how the Channels page went under the same name as the channel list.
        data["channels"] = []
    return data


def discord_state(request, key, defaults, check):
    row = request.app[DB].discord_doc(key)
    published = json.loads(row["published"]) if row and row["published"] else None
    draft = json.loads(row["draft"]) if row and row["draft"] else None
    doc = draft or published or defaults()
    return {"row": row, "doc": check(doc)[0], "draft": draft is not None, "live": published is not None}


# The Discord home page: every page grouped by what it's for, with one line on what it does.
DISCORD_GROUPS = (
    ("What new members see", (
        ("welcome", "The Start here message: rules to accept, linking their game account, picking a faction."),
        ("greeting", "A hello when someone joins, in a channel or by DM."),
        ("serverinfo", "The #servers card: each server's settings, and the in-game rules (also shown on the website)."),
        ("factions", "The faction roles members pick: names, colours and emojis."),
    )),
    ("What the bot posts by itself", (
        ("matchping", "\"Match started\" alerts, and who gets pinged for which server."),
        ("weekly", "The top 3 when the weekly leaderboard resets on Monday."),
        ("bans", "The DM a banned player gets, and the card in their ban ticket."),
        ("staffalerts", "Alerts to staff when someone teamkills several players in a short time."),
    )),
    ("Posting yourself", (
        ("posts", "Your own messages: announcements, events, FAQs, with buttons if you want."),
    )),
    ("Staff jobs", (
        ("links", "Approve or refuse members linking their game account; unlink people."),
        ("feedback", "Where members' feedback goes, the thank-you they get, and who's pinged."),
    )),
    ("Setup", (
        ("channels", "Channels for the boards and logs with no page of their own, and the member roles. Every other page picks its own channel at the top."),
        ("names", "What each game server is called in Discord."),
    )),
)


def discord_status(request, key, report):
    """One short status for the Discord home page."""
    db = request.app[DB]
    if key == "posts":
        live = [r for r in db.all("SELECT published FROM discord_docs WHERE key LIKE 'post:%' AND published IS NOT NULL")
                if not json.loads(r["published"]).get("deleted")]
        return ("on", f"{len(live)} up") if live else ("", "None yet")
    if key == "links":
        waiting = len((report.get("link_requests") or {}).get("pending", []))
        return ("gold", f"{waiting} waiting") if waiting else ("on", "Nothing waiting")
    row = db.discord_doc(key)
    reported = report.get("channels_doc" if key == "channels" else key)
    reported = reported if isinstance(reported, dict) else {}
    if row and row["published"] and reported.get("version") == row["version"] and reported.get("problems"):
        return ("down", "Problem")
    if row and row["draft"]:
        return ("gold", "Draft not published")
    if row and row["published"]:
        return ("on", "Live")
    return ("", "Bot's default")


async def discord_home(request):
    require(request, "discord")
    report = bridge(request)
    labels = dict(DISCORD_PAGES)
    groups = []
    for title, pages in DISCORD_GROUPS:
        items = [{"key": key, "label": labels[key], "about": about, "status": discord_status(request, key, report)}
                 for key, about in pages if allowed(request, f"discord:{key}")]
        if items:
            groups.append({"title": title, "pages": items})
    return render(request, "discord_home.html", groups=groups, bridge=report)


async def bot_data_page(request):
    require(request, "box")
    identity = request.query.get("player", "").strip().lower()
    report = request.app[STATS].health(identity if valid_identity(identity) else "")
    return render(request, "bot_data.html", report=report, identity=identity)


SETTINGS_PERMISSIONS = ("sus", "game_admins", "users", "cards", "website", "mortar")


async def settings_page(request):
    if not any(allowed(request, p) for p in SETTINGS_PERMISSIONS):
        raise web.HTTPForbidden(text="You do not have permission for that.")
    return render(request, "settings.html", is_owner=named(request))


async def data_page(request):
    if not any(allowed(request, p) for p in ("box", "ips", "audit")):
        raise web.HTTPForbidden(text="You do not have permission for that.")
    return render(request, "data.html")


NETCHECK = web.AppKey("netcheck", dict)
PICTURES = web.AppKey("pictures", Path)


def box_problems(box, servers):
    """What stood out on the box and the servers, worst first."""
    found = []

    def stretch(rows, test, title, why, level="bad"):
        hits = [r["at"] for r in rows if test(r)]
        if hits:
            found.append({"title": title, "why": why, "count": len(hits), "first": hits[0], "last": hits[-1],
                          "level": level})

    n = lambda r, key: r.get(key) or 0
    stretch(box, lambda r: n(r, "udp_sndbuf") > 0, "Outgoing game packets dropped",
            "The game's packets to players were thrown away before they left the box: voice and radio break up "
            "first. Raise the send buffer: sysctl -w net.core.wmem_max=16777216 net.core.wmem_default=4194304")
    stretch(box, lambda r: n(r, "udp_errors") > 0, "Incoming game packets dropped",
            "Players' packets reached the box but were thrown away before the game read them. Raise the receive "
            "buffer: sysctl -w net.core.rmem_max=16777216 net.core.rmem_default=4194304")
    stretch(box, lambda r: n(r, "nic_errors") > 0 or n(r, "rx_drop") > 0, "The network port lost packets",
            "Errors or drops on the network card itself: a faulty cable or switch port, or the card's buffer "
            "overflowing (ethtool -G <port> rx 4096).")
    stretch(box, lambda r: n(r, "softnet_drop") > 0, "The kernel couldn't keep up with packets",
            "Packets arrived faster than the CPU took them in.")
    stretch(box, lambda r: n(r, "tcp_retrans") > 120, "Lots of TCP resends",
            "More than two lost-and-resent packets a second: packet loss between the box and the internet.", "warn")
    stretch(box, lambda r: n(r, "cpu") >= 90, "The whole box was nearly out of CPU",
            "Something on the machine is using everything: the game, another server, or a backup or update.")
    stretch(box, lambda r: r.get("mem_total") and n(r, "mem_used") / r["mem_total"] >= 0.92, "Memory nearly full",
            "With memory this full the machine starts swapping and everything slows down.")
    stretch(box, lambda r: n(r, "disk_pct") >= 90, "Disk nearly full",
            "When the disk fills, logs and saves stop being written and servers can crash. Clear old logs or backups.")
    stretch(box, lambda r: n(r, "temp") >= 85, "Running hot",
            "Above 85 °C the CPU slows itself down to cool off, and the game's frame rate drops with it.", "warn")
    for name, rows in servers:
        stretch(rows, lambda r: n(r, "sock_drops") > 0, f"{name}: packets dropped on the game's ports",
                "Players' packets reached the server but the game didn't read them in time: radio cuts out and "
                "movement stutters.")
        stretch(rows, lambda r: r.get("query_ok") == 0, f"{name}: couldn't be reached from outside",
                "The server browser check went unanswered: the port or the network was down.")
        stretch(rows, lambda r: r.get("fps") is not None and r["fps"] < 30, f"{name}: server FPS under 30",
                "The server was struggling to keep up: lag, rubber-banding and late hits.")
        stretch(rows, lambda r: n(r, "proc_cpu") >= 95, f"{name}: the game maxed out a core",
                "Reforger's server does most of its work on one core; pinned at 100% the frame rate drops.", "warn")
    found.sort(key=lambda f: (f["level"] != "bad", -f["count"]))
    return found


def network_tuned(db):
    row = db.discord_doc("network_tune")
    return bool(row and row["published"])


def apply_network_tune():
    return netcheck.tune(sorted({r["interface"] for r in netcheck.default_routes()}))


async def network_tune(request):
    require(request, "box")
    form = await request.post()
    db = request.app[DB]
    if form.get("action") == "off":
        db.publish_discord_doc("network_tune", "", request[USER]["username"])
        audit(request, "network fix off")
        flash(request, "The panel won't set these again when it starts. What's set now stays until the box reboots.")
        raise web.HTTPFound("/network#check")
    steps = await asyncio.to_thread(apply_network_tune)
    done = all(ok for _, ok, _ in steps)
    if done:
        db.publish_discord_doc("network_tune", json.dumps({"on": True}), request[USER]["username"])
    audit(request, "network fix", ok=done, detail="; ".join(f"{what}: {words}" for what, _, words in steps))
    flash(request, " ".join(f"{what}: {words}" for what, _, words in steps), "ok" if done else "error")
    raise web.HTTPFound("/network#check")


CONSOLE = web.AppKey("console", dict)


async def console_page(request):
    """Run a command on the box and show what it printed. Managers only."""
    require(request, "console")
    history = request.app[CONSOLE]
    user = request[USER]
    if request.method == "POST":
        form = await request.post()
        command = form.get("command", "").strip()
        if command:
            result = await console.run(command)
            console.remember(history, user["id"], result)
            audit(request, "console", detail=command[:500], ok=result["code"] == 0)
        raise web.HTTPFound("/console")
    return render(request, "console.html", runs=history.get(user["id"], []), quick=console.QUICK,
                  timeout=console.TIMEOUT, keep=console.KEEP)


FILES_ROOT = web.AppKey("files_root", Path)


def server_folder(request, rel=""):
    try:
        return files.resolve(request.app[FILES_ROOT], request.match_info["inst"], rel)
    except files.FileError as exc:
        raise web.HTTPNotFound(text=str(exc))


def file_url(inst, rel=""):
    return "/files/" + inst + ("/" + quote(rel) if rel else "")


AMP_STATES = {0: "Stopped", 5: "Getting ready", 7: "Configuring", 10: "Starting", 20: "Running", 30: "Restarting",
              40: "Stopping", 45: "Going to sleep", 50: "Asleep", 60: "Waiting", 70: "Installing", 75: "Updating",
              80: "Waiting for input", 100: "Failed", 200: "Suspended", 250: "Maintenance"}
MANAGE_TABS = (("overview", "Overview", "power"), ("console", "Console", "files"), ("settings", "Settings", "amp_settings"),
               ("mods", "Mods", "mods"), ("files", "Files", "files"))


def manage_tabs(request, inst, current):
    """The sub-tabs along the top of one server's pages, only the ones this person can open."""
    urls = {"overview": f"/manage/{inst}", "console": f"/manage/{inst}/console", "settings": f"/manage/{inst}/settings",
            "mods": f"/mods/{inst}", "files": f"/files/{inst}"}
    return [{"key": key, "label": label, "url": urls[key], "on": key == current}
            for key, label, perm in MANAGE_TABS if allowed(request, perm)]


async def files_index(request):
    raise web.HTTPFound("/manage")


def manage_card(request, root, name, amp_info, panel_states):
    """What one server's card on the Server manager shows, all read from the box."""
    base = root / name
    try:
        settings = files.game_settings(base)
    except (OSError, ValueError):
        settings = {}
    game = files.game_process(base)
    live = panel_states.get(name)
    info = amp_info.get(name) or {}
    return {"name": name, "running": files.amp_running(base), "game": game,
            "friendly": info.get("FriendlyName") or "", "server_name": settings.get("name", ""),
            "mods": len(files.parse_mods(settings.get("mods", ""))), "admins": len(parse_ids(settings.get("admins", ""))),
            "max": settings.get("$MaxUsers", ""), "players": len(live.players) if live and live.online else None,
            "map": (live.query.get("map") if live and live.online else "") or "",
            "panel_id": live.config.id if live else ""}


async def manage_index(request):
    """Every AMP server on the box, like AMP's Instances page."""
    require(request, "files")
    root = request.app[FILES_ROOT]
    amp_info = {}
    if request.app[AMP]:
        try:
            amp_info = await request.app[AMP].instances()
        except AmpError:
            pass
    panel_states = {(st.config.amp_instance or amp_instance_name(st.config.log_dir)): st
                    for st in request.app[MANAGER].states.values()}
    folders = [manage_card(request, root, name, amp_info, panel_states) for name in files.instances(root)]
    totals = {"running": sum(1 for f in folders if f["game"]), "players": sum(f["players"] or 0 for f in folders),
              "memory": sum(f["game"]["memory"] for f in folders if f["game"])}
    return render(request, "manage_index.html", folders=folders, root=root, has_amp=bool(request.app[AMP]),
                  totals=totals, setups=sorted(request.app[SETUPS].items(), key=lambda item: -item[1]["at"]))


SETUPS = web.AppKey("setups", dict)
# What a new server copies from the one it's based on is everything in its game
# settings except these, which are this server's own.
OWN_SETTINGS = ("name", "password", "adminPassword", "publicAddress", "ManualPublicIP")


# Every setting the wizard asks for: (form field, game setting, kind). Numbers are checked.
WIZARD_FIELDS = (
    ("server_name", "name", "text"), ("scenario", "scenarioId", "text"), ("players", "$MaxUsers", "int"),
    ("queue", "maxSize", "int"), ("fps", "maxFPS", "int"), ("view", "serverMaxViewDistance", "int"),
    ("net_view", "networkViewDistance", "int"), ("grass", "serverMinGrassDistance", "int"), ("ai", "aiLimit", "int"),
    ("navmesh", "DisabledNavmeshComponents", "text"), ("freeze", "freezeCheck", "int"),
    ("autosave", "autoSaveInterval", "int"), ("retention", "saveRetention", "int"),
)
WIZARD_SWITCHES = (("visible", "visible"), ("crossPlatform", "crossPlatform"),
                   ("cross_radio", "VONCanTransmitCrossFaction"), ("load_save", "loadSessionSave2"))


def wizard_defaults(current):
    """What the wizard starts with: the copied server's values, or what our servers use."""
    header, args = current.get("missionHeader", ""), current.get("CustomServerArgs", "")
    us, ussr, fia, xp_arg = files.args_get(args)
    ours = {"$MaxUsers": "128", "maxSize": "50", "maxFPS": "120", "serverMaxViewDistance": "1800",
            "networkViewDistance": "800", "serverMinGrassDistance": "50", "aiLimit": "250",
            "DisabledNavmeshComponents": '"Soldiers","BTRlike"', "freezeCheck": "45", "autoSaveInterval": "10",
            "saveRetention": "10", "visible": "true", "crossPlatform": "true", "VONCanTransmitCrossFaction": "true",
            "loadSessionSave2": "true"}
    found = {key: current.get(key) or ours.get(key, "") for _, key, _ in WIZARD_FIELDS}
    found.update({key: str(current.get(key, ours.get(key, "false"))).lower() for _, key in WIZARD_SWITCHES})
    found.update(xp=files.header_get(header, "m_fXpMultiplier") or xp_arg or "1.5", us=us or "54", ussr=ussr or "54",
                 fia=fia or "20", supplies=files.header_get(header, "m_iStartingHQSupplies") or "1200",
                 points=files.header_get(header, "m_iControlPointsCap") or "6",
                 victory=files.header_get(header, "m_fVictoryTimeout") or "780",
                 header=header, crash=str(current.get("freezeCheckMode", "crash")) == "crash")
    return found


async def manage_new_page(request):
    require(request, "files")
    root = request.app[FILES_ROOT]
    folders = files.instances(root)
    if not folders:
        raise web.HTTPFound("/manage")
    source = request.query.get("from") if request.query.get("from") in folders else folders[0]
    base, _ = files.resolve(root, source)
    current = files.game_settings(base)
    return render(request, "manage_new.html", folders=folders, source=source, v=wizard_defaults(current),
                  scenarios=files.scenarios(base), scenario=current.get("scenarioId", ""),
                  admin_password=secrets.token_urlsafe(12).replace("-", "x").replace("_", "y"),
                  has_amp=bool(request.app[AMP]))


def new_server_settings(source_settings, form):
    """The game settings a new server is given: its source's (if copied), then
    everything the wizard asked, mission header and startup line included."""
    settings = {k: v for k, v in source_settings.items() if k not in OWN_SETTINGS} if form.get("copy") else {}
    if form.get("copy") and not form.get("copy_mods"):
        settings["mods"] = ""
    if form.get("copy") and not form.get("copy_admins"):
        settings["admins"] = ""
    for field, key, kind in WIZARD_FIELDS:
        value = form.get(field, "").strip()
        if not value:
            continue
        if kind == "int" and not re.fullmatch(r"-?\d{1,6}", value):
            raise files.FileError(f"{field.replace('_', ' ').capitalize()} has to be a whole number.")
        settings[key] = value[:100] if key == "name" else value
    for field, key in WIZARD_SWITCHES:
        settings[key] = "true" if form.get(field) else "false"
    settings["freezeCheckMode"] = "crash" if form.get("crash") else ""
    if settings.get("DisabledNavmeshComponents"):
        settings["disableNavmeshStreaming12"] = '"disableNavmeshStreaming":[ {{DisabledNavmeshComponents}} ],'
    for field in ("xp", "supplies", "points", "victory", "us", "ussr", "fia"):
        if form.get(field, "").strip() and not re.fullmatch(r"\d{1,6}(\.\d{1,3})?", form.get(field, "").strip()):
            raise files.FileError(f"{field.upper() if len(field) < 5 else field.capitalize()} has to be a number.")
    header = form.get("header", "") if form.get("header", "").strip() else settings.get("missionHeader", "")
    for field, key in files.HEADER_KEYS.items():
        if form.get(field, "").strip():
            header = files.header_set(header, key, form.get(field).strip())
    settings["missionHeader"] = header
    settings["CustomServerArgs"] = files.args_set(settings.get("CustomServerArgs", ""), form.get("us", "").strip(),
                                                  form.get("ussr", "").strip(), form.get("fia", "").strip(),
                                                  form.get("xp", "").strip())
    typed = {"password": form.get("password", ""), "adminPassword": form.get("admin_password", "")}
    settings.update({k: v for k, v in typed.items() if v})
    return settings


async def manage_create(request):
    """A new game server: made from an existing one's game template, then given its
    settings and (if asked) started, in the background, with progress on the list."""
    require(request, "files")
    root = request.app[FILES_ROOT]
    form = await request.post()
    name = form.get("name", "").strip()
    friendly = form.get("friendly", "").strip() or name
    source = form.get("copy_from", "")
    back = f"/manage/new?from={quote(source)}"
    try:
        if not request.app[AMP]:
            raise AmpError("The panel has no AMP login yet. Add it under Settings → AMP login.")
        if not files.INSTANCE_NAME.match(name):
            raise files.FileError("Server names are 3 to 31 letters and numbers, starting with a letter, like Arland02.")
        if (root / name).exists() or name in request.app[SETUPS]:
            raise files.FileError(f"There's already a server called {name}.")
        if not form.get("server_name", "").strip():
            raise files.FileError("Give it a server name for the in-game browser.")
        admin_password = form.get("admin_password", "")
        if admin_password and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", admin_password):
            raise files.FileError("The admin password can only use letters, numbers, - and _ (32 at most).")
        if form.get("password") and not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", form.get("password")):
            raise files.FileError("The server password can only use letters, numbers, - and _.")
        base, _ = files.resolve(root, source)
        provision = files.provision_from(base)
        settings = new_server_settings(files.game_settings(base), form)
        port = max(files.used_ports(root) | {8080}) + 1
        await request.app[AMP].create_instance(name, friendly[:60], port, provision)
    except (AmpError, files.FileError, OSError, ValueError) as exc:
        audit(request, "create server", name, detail=str(exc), ok=False)
        flash(request, f"{name or 'The server'} wasn't made: {exc}", "error")
        raise web.HTTPFound(back)
    audit(request, "create server", name, detail=f"like {source}, AMP port {port}")
    request.app[SETUPS][name] = {"step": "Created in AMP, waiting for it to start up", "done": False, "error": "",
                                 "at": now(), "by": request[USER]["username"]}
    task = asyncio.create_task(finish_setup(request.app, name, settings, bool(form.get("start")),
                                            quick_stop=bool(form.get("quick_stop"))))
    request.app[SETUP_TASKS].add(task)
    task.add_done_callback(request.app[SETUP_TASKS].discard)
    flash(request, f"Making {name}. Its progress shows below; you can leave this page.")
    raise web.HTTPFound("/manage")


SETUP_TASKS = web.AppKey("setup_tasks", set)
BUSY_STATES = (5, 7, 10, 70, 75)


async def finish_setup(app, name, settings, start, pause=5, patience=600, download=3600, quick_stop=False):
    """Wait for the new instance to answer, give it its settings, wait out the game
    download and start it. Every step is written where the Server manager shows it."""
    progress = app[SETUPS][name]
    amp = app[AMP]

    def step(text, **extra):
        progress.update(step=text, at=now(), **extra)

    try:
        instance = None
        for _ in range(int(patience / pause)):
            instance = (await amp.instances()).get(name)
            if instance:
                try:
                    await amp.status(instance)
                    break
                except AmpError:
                    pass
            await asyncio.sleep(pause)
        else:
            raise AmpError("AMP made it but it never answered. Check it in AMP.")
        step("Applying its settings")
        refused = await amp.set_configs(instance, {GAME_NODE + k: v for k, v in settings.items()})
        if refused:
            step("Settings applied, except " + ", ".join(n.rsplit(".", 1)[-1] for n in refused))
        if quick_stop:
            # How long AMP waits for the game to close before forcing it (30 by default, 5 on ours).
            await amp.set_configs(instance, {"GenericModule.App.ExitTimeout": "5"})
        if not start:
            step("Ready. Press Start on its page when you want it running.", done=True)
            return
        step("Downloading Arma Reforger (this takes a while)")
        for _ in range(int(download / (pause * 4))):
            state = (await amp.status(instance)).get("State")
            if state not in BUSY_STATES:
                break
            await asyncio.sleep(pause * 4)
        step("Starting the game server")
        await amp.power(instance, "start")
        step("Started", done=True)
    except (AmpError, OSError) as exc:
        step("Stopped", done=True, error=str(exc))


async def manage_delete(request):
    """Delete a whole server, carefully: named accounts only, its name typed out,
    stopped first and backed up before AMP removes it."""
    require(request, "delete_servers")
    inst = request.match_info["inst"]
    base, _ = server_folder(request)
    form = await request.post()
    back = f"/manage/{inst}"
    if form.get("confirm", "") != inst:
        flash(request, f"Type {inst} exactly to delete it. Nothing was deleted.", "error")
        raise web.HTTPFound(back)
    if not request.app[AMP]:
        flash(request, "The panel has no AMP login yet, so it can't delete servers.", "error")
        raise web.HTTPFound(back)
    try:
        if files.amp_running(base):
            await request.app[AMP].instance_power(inst, "stop")
            for _ in range(60):
                await asyncio.sleep(1)
                if not files.amp_running(base):
                    break
            else:
                raise AmpError(f"{inst} didn't stop within a minute, so it wasn't deleted.")
        saved, size = await asyncio.to_thread(files.backup, request.app[FILES_ROOT], base)
        audit(request, "backed up server", inst, detail=f"{saved} ({size // 1024 // 1024} MB)")
        await request.app[AMP].delete_instance(inst)
    except (AmpError, OSError) as exc:
        audit(request, "delete server", inst, detail=str(exc), ok=False)
        flash(request, f"{inst} wasn't deleted: {exc}", "error")
        raise web.HTTPFound(back)
    audit(request, "delete server", inst, detail=f"backup {saved}")
    alert(request, f"Server deleted: {inst}", f"By {request[USER]['username']}. Backup: {saved}", colour=RED)
    flash(request, f"Deleted {inst}. A backup of its settings, saves and logs is at {saved}.")
    raise web.HTTPFound("/manage")


async def manage_overview(request):
    require(request, "power")
    inst = request.match_info["inst"]
    base, _ = server_folder(request)
    running, status, error = files.amp_running(base), {}, ""
    if running:
        try:
            status = await request.app[AMP].status(await amp_instance(request, inst))
        except AmpError as exc:
            error = str(exc)
    metrics = [{"name": name, **m} for name, m in (status.get("Metrics") or {}).items() if isinstance(m, dict)]
    return render(request, "manage_overview.html", inst=inst, running=running, error=error, metrics=metrics,
                  state=AMP_STATES.get(status.get("State"), "Unknown") if status else "", uptime=status.get("Uptime", ""),
                  has_amp=bool(request.app[AMP]), can_delete=allowed(request, "delete_servers"),
                  tabs=manage_tabs(request, inst, "overview"))


async def manage_power(request):
    """The game server's own power buttons, through that server's AMP."""
    require(request, "power")
    inst, action = request.match_info["inst"], request.match_info["action"]
    server_folder(request)
    if action not in ("start", "stop", "restart", "update"):
        raise web.HTTPNotFound()
    try:
        await request.app[AMP].power(await amp_instance(request, inst), action)
    except AmpError as exc:
        audit(request, f"{action} through AMP", inst, detail=str(exc), ok=False)
        flash(request, str(exc), "error")
        raise web.HTTPFound(f"/manage/{inst}")
    audit(request, f"{action} through AMP", inst)
    flash(request, {"start": "Starting", "stop": "Stopping", "restart": "Restarting", "update": "Updating"}[action]
          + f" {inst}'s game server.")
    raise web.HTTPFound(f"/manage/{inst}")


async def manage_console(request):
    require(request, "files")
    inst = request.match_info["inst"]
    base, _ = server_folder(request)
    if request.method == "POST":
        form = await request.post()
        message = form.get("message", "").strip()
        if message:
            try:
                await request.app[AMP].send_console(await amp_instance(request, inst), message)
                audit(request, "game console", inst, detail=message[:500])
            except AmpError as exc:
                audit(request, "game console", inst, detail=f"{message[:200]}: {exc}", ok=False)
                flash(request, str(exc), "error")
        raise web.HTTPFound(f"/manage/{inst}/console")
    path, text = files.latest_console(base)
    if request.path.endswith(".txt"):
        return web.Response(text=text, headers={"Cache-Control": "no-store"})
    return render(request, "manage_console.html", inst=inst, text=text, path=path and files.relative(base, path),
                  running=files.amp_running(base), tabs=manage_tabs(request, inst, "console"))


# The game's own settings, sorted into sections by their field name. First match wins.
SETTING_SECTIONS = (
    ("Basics", ("name", "scenarioid", "custommission", "visible", "crossplatform", "password", "maxusers", "maxsize")),
    ("Admins & access", ("admins", "adminpassword", "maxclients", "rconpermission", "battleye")),
    ("Mods", ("mods", "addonsverify", "addonsrepair", "modsrequiredbydefault")),
    ("Gameplay", ("missionheader", "disablethirdperson", "vondisable", "voncan")),
    ("Performance", ("maxfps", "viewdistance", "grass", "navmesh", "ailimit", "disableai", "fastvalidation", "freezecheck")),
    ("Saves", ("autosave", "saveretention", "sessionsave", "playersavetime", "hiveid", "persistence")),
    ("Network", ("gameport", "queryport", "rconport", "ipbinding", "publicaddress", "publicip", "lobbyplayer",
                 "slotreservation")),
)


def setting_section(node):
    field = node.rsplit(".", 1)[-1].lower().lstrip("$")
    for title, keys in SETTING_SECTIONS:
        if field in keys:
            return title
    for title, keys in SETTING_SECTIONS:
        if any(len(key) > 4 and key in field for key in keys):
            return title
    return "Advanced"


def amp_settings_list(game, spec):
    """[(section, [setting, ...])]: the game's own settings in sections, then AMP's
    other groups. Hidden ones and repeats (AMP lists some twice) are left out."""
    def shown(setting):
        return (isinstance(setting, dict) and setting.get("Node") and not setting.get("Hidden")
                and str(setting.get("InputType", "")).lower() not in ("hidden", "none"))

    groups = {title: [] for title, _ in SETTING_SECTIONS}
    groups["Advanced"] = []
    names, nodes = set(), set()
    for setting in filter(shown, game):
        if setting.get("Name") in names:
            continue
        groups[setting_section(setting["Node"])].append(setting)
        names.add(setting.get("Name"))
        nodes.add(setting["Node"])
    for category, settings in spec.items():
        rest = []
        for setting in settings or []:
            if shown(setting) and setting["Node"] not in nodes and setting.get("Name") not in names:
                rest.append(setting)
                names.add(setting.get("Name"))
        if rest:
            groups.setdefault("AMP: " + str(category).split(":")[0], []).extend(rest)
    return [(title, items) for title, items in groups.items() if items]


def setting_text(setting):
    value = setting.get("CurrentValue")
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


async def manage_settings(request):
    require(request, "amp_settings")
    inst = request.match_info["inst"]
    base, _ = server_folder(request)
    running, groups, error = files.amp_running(base), [], ""
    if running:
        try:
            instance = await amp_instance(request, inst)
            game = await request.app[AMP].configs(instance, [GAME_NODE + f for f in files.game_fields(base)])
            groups = amp_settings_list(game, await request.app[AMP].settings_spec(instance))
        except AmpError as exc:
            error = str(exc)
    if request.method == "POST":
        form = await request.post()
        current = {s["Node"]: s for _, settings in groups for s in settings}
        changed = []
        try:
            instance = await amp_instance(request, inst)
            for node, setting in current.items():
                sent = form.getall(f"n:{node}", [])
                if not sent or setting.get("ReadOnly"):
                    continue
                value = sent[-1]
                if str(setting.get("InputType", "")).lower() == "password" and not value:
                    continue
                if value != setting_text(setting):
                    await request.app[AMP].set_config(instance, node, value)
                    changed.append(setting.get("Name") or node)
                    shown = "(hidden)" if str(setting.get("InputType", "")).lower() == "password" else value[:200]
                    audit(request, "changed AMP setting", inst, node, shown)
        except AmpError as exc:
            flash(request, str(exc), "error")
        if changed:
            flash(request, f"Saved {', '.join(changed)}. Most take effect when the server next restarts.")
        elif not error:
            flash(request, "Nothing was changed.")
        raise web.HTTPFound(f"/manage/{inst}/settings")
    admins_setting = next((st for _, items in groups for st in items if st["Node"].lower().endswith(".admins")), None)
    staff = request.app[DB].staff_ids()
    admins = admin_list(request.app[DB], setting_text(admins_setting)) if admins_setting else None
    listed = {a["id"] for a in admins or []}
    ready = [{"id": i, "name": (request.app[DB].player(i) or {"name": i})["name"]} for i in sorted(staff - listed)]
    return render(request, "manage_settings.html", inst=inst, running=running, error=error, groups=groups,
                  admins=admins, ready=ready, max_admins=MAX_ADMINS, can_admins=allowed(request, "game_admins"),
                  value=setting_text, slug=lambda title: re.sub(r"[^a-z]+", "-", title.lower()).strip("-"),
                  tabs=manage_tabs(request, inst, "settings"))


async def file_browser(request):
    require(request, "files")
    inst = request.match_info["inst"]
    base, path = server_folder(request, request.match_info.get("rel", ""))
    if not path.exists():
        raise web.HTTPNotFound(text="That file or folder isn't there any more.")
    rel = files.relative(base, path)
    crumbs, walked = [], ""
    for part in [p for p in rel.split("/") if p]:
        walked = f"{walked}/{part}" if walked else part
        crumbs.append((part, file_url(inst, walked)))
    if path.is_dir():
        return render(request, "files.html", inst=inst, rel=rel, crumbs=crumbs, entries=files.listing(path),
                      shortcuts=[] if rel else [(label, file_url(inst, target)) for label, target in files.shortcuts(base)],
                      tabs=manage_tabs(request, inst, "files"),
                      here=file_url(inst, rel), up=file_url(inst, rel.rpartition("/")[0]) if rel else "/files",
                      join=lambda name: file_url(inst, f"{rel}/{name}" if rel else name))
    if request.query.get("download"):
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})
    return render(request, "file_edit.html", inst=inst, rel=rel, crumbs=crumbs, name=path.name,
                  tabs=manage_tabs(request, inst, "files"),
                  size=path.stat().st_size, text=files.read_text(path), here=file_url(inst, rel),
                  up=file_url(inst, rel.rpartition("/")[0]), max_edit=files.MAX_EDIT)


async def file_action(request):
    require(request, "files")
    inst = request.match_info["inst"]
    base, path = server_folder(request, request.match_info.get("rel", ""))
    rel = files.relative(base, path)
    back = file_url(inst, rel)
    action = request.query.get("do", "")
    try:
        if action == "upload":
            if not path.is_dir():
                raise files.FileError("Uploads go into a folder.")
            reader = await request.multipart()
            done = []
            while (part := await reader.next()) is not None:
                if part.filename:
                    size = await files.save_upload(path, Path(part.filename).name, part_chunks(part))
                    done.append(part.filename)
                    audit(request, "uploaded file", inst, f"{rel}/{part.filename}".lstrip("/"), f"{size // 1024} KB")
            flash(request, f"Uploaded {', '.join(done)}." if done else "Choose a file to upload.",
                  "ok" if done else "error")
        elif action == "save":
            if not path.is_file():
                raise files.FileError("Only a file can be saved.")
            text = None
            if request.content_type.startswith("multipart/"):
                reader = await request.multipart()
                while (part := await reader.next()) is not None:
                    if part.name == "text":
                        text = await part.text()
            else:
                text = (await request.post()).get("text")
            if text is None or len(text.encode()) > files.MAX_EDIT:
                raise files.FileError("That's too big to save from here.")
            files.write_text(path, text)
            audit(request, "edited file", inst, rel)
            flash(request, f"Saved {path.name}.")
        else:
            form = await request.post()
            kind, name = form.get("action", ""), form.get("name", "").strip()
            if kind in ("folder", "file"):
                if not path.is_dir() or not name or "/" in name or name in (".", ".."):
                    raise files.FileError("Pick a name without slashes.")
                target = path / name
                if kind == "folder":
                    files.make_folder(target)
                else:
                    if target.exists():
                        raise files.FileError(f"{name} already exists.")
                    files.write_text(target, "")
                audit(request, f"made {kind}", inst, files.relative(base, target))
                back = file_url(inst, files.relative(base, target))
            elif kind == "rename":
                target = files.rename(path, name)
                audit(request, "renamed file", inst, rel, files.relative(base, target))
                back = file_url(inst, files.relative(base, target.parent))
            elif kind == "delete":
                files.delete(base, path)
                audit(request, "deleted file", inst, rel)
                flash(request, f"Deleted {path.name}.")
                back = file_url(inst, rel.rpartition("/")[0])
            else:
                raise web.HTTPBadRequest(text="Unknown action.")
    except (files.FileError, OSError) as exc:
        flash(request, str(exc), "error")
    raise web.HTTPFound(back)


async def amp_login_page(request):
    """Give the panel its AMP login from here. Only the panel's owners: it's the key to all of AMP."""
    if not named(request):
        raise web.HTTPForbidden(text="Only the panel's owners can change the AMP login.")
    amp, seen, error = request.app[AMP], [], ""
    if request.method == "POST":
        form = await request.post()
        url = form.get("url", "").strip() or "http://127.0.0.1:8080"
        username, password = form.get("username", "").strip(), form.get("password", "")
        if not url.startswith(("http://", "https://")) or not username or not password:
            flash(request, "Fill in the AMP username and password.", "error")
            raise web.HTTPFound("/settings/amp")
        trial = Amp(url, username, password)
        try:
            seen = sorted(await trial.instances())
        except AmpError as exc:
            audit(request, "set AMP login", detail=f"{username}: {exc}", ok=False)
            flash(request, f"Not saved: {exc}", "error")
            raise web.HTTPFound("/settings/amp")
        path = Path(os.getenv("PANEL_CONFIG", "panel.local.json"))
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["amp"] = {"url": url, "username": username, "password": password}
        path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            request.app[AMP] = trial
        audit(request, "set AMP login", detail=username)
        flash(request, f"Saved. The panel is logged into AMP as {username} and can see: {', '.join(seen) or 'no servers'}.")
        raise web.HTTPFound("/settings/amp")
    if amp:
        try:
            seen = sorted(await amp.instances())
        except AmpError as exc:
            error = str(exc)
    return render(request, "amp_login.html", amp=amp, seen=seen, error=error)


async def amp_instance(request, inst):
    if not request.app[AMP]:
        raise AmpError(f"{inst}'s AMP is running, so changes have to go through it, and the panel has no AMP login yet. "
                       "A manager adds it under Settings → AMP login.")
    instance = (await request.app[AMP].instances()).get(inst)
    if not instance:
        raise AmpError(f"AMP has no instance called {inst}.")
    return instance


def admin_list(db, value):
    """The admins in an AMP Admins value, with the names the panel knows them by."""
    ids = parse_ids(value)
    return [{"id": i, "name": (db.player(i) or {"name": ""})["name"] if valid_identity(i) else "",
             "steam": not valid_identity(i)} for i in ids]


async def manage_admins(request):
    """Add or take off one in-game admin on one server, from its Settings tab."""
    require(request, "game_admins")
    inst = request.match_info["inst"]
    server_folder(request)
    form = await request.post()
    db = request.app[DB]
    back = f"/manage/{inst}/settings#admins-access"
    remove = form.get("remove", "").strip().lower()
    # The picker's own box and a one-tap button both send an identity; whichever is filled in wins.
    identity = next((i.strip().lower() for i in form.getall("identity", []) if i.strip()), "")
    typed = clean(form.get("player", ""), 64)
    if not remove:
        if valid_identity(typed.lower()):
            identity = typed.lower()
        elif not valid_identity(identity):
            found = db.by_name(typed) if typed else []
            if len(found) != 1:
                flash(request, "Pick the player from the list as you type, or paste their identity ID." if not found else
                      f"{len(found)} players have gone by {typed}. Pick the right one from the list.", "error")
                raise web.HTTPFound(back)
            identity = found[0]["identity"]
    target = remove or identity
    name = (db.player(target) or {"name": target})["name"]
    try:
        await request.app[AMP].change_admins(await amp_instance(request, inst),
                                             add=[] if remove else [identity], remove=[remove] if remove else [])
    except AmpError as exc:
        audit(request, "removed game admin" if remove else "made game admin", inst, name, str(exc), ok=False)
        flash(request, str(exc), "error")
        raise web.HTTPFound(back)
    audit(request, "removed game admin" if remove else "made game admin", inst, name, target)
    flash(request, f"{name} is {'off' if remove else 'on'} {inst}'s admin list. It applies when the server restarts.")
    raise web.HTTPFound(back)


async def mods_page(request):
    require(request, "mods")
    inst = request.match_info["inst"]
    base, _ = server_folder(request)
    on_disk, mods, error = files.downloaded_mods(base), [], ""
    running = files.amp_running(base)
    try:
        if running:
            mods = files.parse_mods(await request.app[AMP].setting(await amp_instance(request, inst), "mods"))
        else:
            mods = files.parse_mods(files.kvp_mods(base))
    except (AmpError, files.FileError, OSError, ValueError) as exc:
        error = str(exc)
    using = {m["modId"] for m in mods}
    spare = sorted(((mod_id, info) for mod_id, info in on_disk.items() if mod_id not in using),
                   key=lambda item: item[1]["name"].lower())
    query = " ".join(request.query.get("q", "").split())[:80]
    sort = request.query.get("sort", "popularity")
    try:
        page = max(1, int(request.query.get("page", "1")))
    except ValueError:
        page = 1
    tag = request.query.get("tag", "") if request.query.get("tag", "") in workshop.CATEGORIES else ""
    browse, browse_error, links = None, "", {}
    try:
        browse = await request.app[WORKSHOP].search(query, sort, page, tag)
        links = workshop.paging(browse, f"/mods/{inst}")
    except workshop.WorkshopError as exc:
        browse_error = str(exc)

    def mods_link(**changes):
        values = {"q": query, "sort": sort, "tag": tag, **changes}
        return f"/mods/{inst}?" + urlencode({k: v for k, v in values.items() if v})
    sort_links = {value: mods_link(sort=value) for value in workshop.SORTS}
    category_links = {"": mods_link(tag="")} | {value: mods_link(tag=value) for value in workshop.CATEGORIES}
    return render(request, "mods.html", inst=inst, mods=mods, on_disk=on_disk, spare=spare, error=error,
                  running=running, can_files=allowed(request, "files"), tabs=manage_tabs(request, inst, "mods"),
                  using=using, browse=browse, browse_error=browse_error, browse_q=query, browse_sort=sort,
                  browse_tag=tag, categories=workshop.CATEGORIES, category_links=category_links,
                  sorts=workshop.SORTS, sort_links=sort_links, links=links,
                  csp="default-src 'self'; frame-ancestors 'none'; form-action 'self'; img-src 'self' https:")


async def mods_change(request):
    require(request, "mods")
    inst = request.match_info["inst"]
    base, _ = server_folder(request)
    form = await request.post()
    action = form.get("action", "")
    mod_id = form.get("mod_id", "").strip().upper()
    name = form.get("name", "").strip()
    version = form.get("version", "").strip()
    back = f"/mods/{inst}"
    if action not in ("add", "remove"):
        raise web.HTTPBadRequest(text="Unknown action.")
    if not files.MOD_ID.match(mod_id):
        flash(request, "A mod ID is 16 letters and numbers, like 60C4CE4888FF4621. It's on the mod's Workshop page.", "error")
        raise web.HTTPFound(back)
    if action == "add" and not name:
        name = files.downloaded_mods(base).get(mod_id, {}).get("name", mod_id)

    def change(value):
        mods = [m for m in files.parse_mods(value) if m["modId"] != mod_id]
        if action == "add":
            mod = {"modId": mod_id, "name": name}
            if version:
                mod["version"] = version
            mods.append(mod)
        return files.format_mods(mods)

    try:
        # A running AMP keeps its own copy of the settings, so it has to make the
        # change; a stopped one only has the file.
        if files.amp_running(base):
            await request.app[AMP].change_setting(await amp_instance(request, inst), "mods", change)
        else:
            files.set_kvp_mods(base, change)
    except (AmpError, files.FileError, OSError, ValueError) as exc:
        audit(request, f"{action} mod", inst, mod_id, str(exc), ok=False)
        flash(request, str(exc), "error")
        raise web.HTTPFound(back)
    audit(request, f"{action} mod", inst, mod_id, name)
    flash(request, f"{'Added' if action == 'add' else 'Removed'} {name or mod_id}. It applies when {inst} next starts.")
    raise web.HTTPFound(back)


async def instance_power(request):
    """Start or stop a whole AMP instance from the panel."""
    require(request, "power")
    inst, action = request.match_info["inst"], request.match_info["action"]
    server_folder(request)
    if action not in ("start", "stop"):
        raise web.HTTPNotFound()
    try:
        if not request.app[AMP]:
            raise AmpError("The panel has no AMP login yet. A manager adds it under Settings → AMP login.")
        await request.app[AMP].instance_power(inst, action)
    except AmpError as exc:
        audit(request, f"{action} AMP instance", inst, detail=str(exc), ok=False)
        flash(request, str(exc), "error")
        raise web.HTTPFound(request.headers.get("Referer", "/manage") if "/manage" in request.headers.get("Referer", "") else "/manage")
    audit(request, f"{action} AMP instance", inst)
    flash(request, f"{'Starting' if action == 'start' else 'Stopping'} {inst}'s AMP. Give it a few seconds, then reload.")
    raise web.HTTPFound(request.headers.get("Referer", "/manage") if "/manage" in request.headers.get("Referer", "") else "/manage")


async def network_page(request):
    require(request, "box")
    db, manager = request.app[DB], request.app[MANAGER]
    result = request.app[NETCHECK].get("last")
    if request.method == "POST":
        servers = [(st.config.id, st.config.name, st.pid or st.found_pid,
                    (st.config.query_host, st.config.query_port) if st.config.query_host and st.config.query_port else None)
                   for st in manager.states.values()]
        result = await netcheck.run(servers, log_dirs={st.config.id: st.config.log_dir for st in manager.states.values()})
        request.app[NETCHECK]["last"] = result
        audit(request, "network check")
    key = request.query.get("range", "6h")
    key, label, seconds, ago = next((r for r in MONITOR_RANGES if r[0] == key), MONITOR_RANGES[1])
    until = now()
    since = until - seconds
    box_rows = [dict(r) for r in db.box_samples(since)]
    for r in box_rows:
        r["mem_gb"] = r["mem_used"] / GB if r["mem_used"] else None
        r["swap_gb"] = r["swap_used"] / GB if r["swap_used"] is not None else None
    keys = ("cpu", "load1", "mem_gb", "swap_gb", "disk_pct", "disk_read", "disk_write", "temp", "conntrack",
            "rx_mbps", "tx_mbps", "rx_pps", "tx_pps", "udp_errors", "udp_sndbuf", "nic_errors", "rx_drop",
            "softnet_drop", "tcp_retrans")
    box = monitor.downsample(box_rows, since, until, keys,
                             peak=("udp_errors", "udp_sndbuf", "nic_errors", "rx_drop", "softnet_drop", "tcp_retrans"))
    chart = lambda rows, series, floor=None, height=110: monitor.chart(rows, since, until, series, floor=floor, height=height)
    charts = [
        ("CPU (%)", chart(box, [("cpu", "All cores", "a")], floor=100), ""),
        ("Load", chart(box, [("load1", "1 minute", "a")]), "How many programs are waiting for a CPU core. Above the core count, things queue."),
        ("Memory (GB)", chart(box, [("mem_gb", "In use", "a"), ("swap_gb", "Swap", "bad")]), "Swap above zero means memory ran out and the disk is standing in: slow."),
        ("Network (Mb/s)", chart(box, [("rx_mbps", "In", "a"), ("tx_mbps", "Out", "b")]), ""),
        ("Packets per second", chart(box, [("rx_pps", "In", "a"), ("tx_pps", "Out", "b")]), ""),
        ("Game packets lost (per minute)", chart(box, [("udp_errors", "Incoming", "c"), ("udp_sndbuf", "Outgoing", "bad")]),
         "Should sit at zero. Outgoing is the game's voice and updates to players being dropped."),
        ("Network port and kernel drops (per minute)", chart(box, [("nic_errors", "Port errors", "bad"), ("rx_drop", "Port drops", "c"),
                                                                  ("softnet_drop", "Kernel drops", "b")]), "Should sit at zero."),
        ("TCP resends (per minute)", chart(box, [("tcp_retrans", "Resent", "c")]), "Lost-and-resent packets on connections out of the box."),
        ("Disk used (%)", chart(box, [("disk_pct", "Used", "a")], floor=100), ""),
        ("Disk activity (MB/s)", chart(box, [("disk_read", "Read", "a"), ("disk_write", "Written", "b")]), ""),
        ("Temperature (°C)", chart(box, [("temp", "Hottest sensor", "c")], floor=90), ""),
        ("Connection tracking", chart(box, [("conntrack", "Connections", "a")]), "When this hits its limit, new traffic is dropped."),
    ]
    games, game_rows = [], []
    for st in manager.states.values():
        rows = [dict(r) for r in db.server_samples(st.config.id, since)]
        game_rows.append((st.config.name, rows))
        srv = monitor.downsample(rows, since, until, ("players", "fps", "proc_cpu", "sock_drops"),
                                 peak=("sock_drops",))
        latest = rows[-1] if rows else {}
        games.append({"id": st.config.id, "name": st.config.name, "online": st.online, "players": len(st.players) if st.online else None,
                      "latest": latest, "charts": [
                          ("Players", chart(srv, [("players", "Players", "a")], height=60)),
                          ("FPS", chart(srv, [("fps", "FPS", "a")], floor=60, height=60)),
                          ("Game CPU (% of a core)", chart(srv, [("proc_cpu", "CPU", "a")], floor=100, height=60)),
                          ("Port drops / min", chart(srv, [("sock_drops", "Dropped", "bad")], height=60))]})
    latest = defaultdict(lambda: None, box_rows[-1] if box_rows else {})
    top = await monitor.top_processes()
    counts, sections = {}, {}
    for row in (result or {}).get("lines", []):
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        sections.setdefault(row["section"], []).append(row)
    return render(request, "network.html", result=result, counts=counts, sections=sections,
                  text=netcheck.report_text(result) if result else "", ranges=MONITOR_RANGES, range_key=key,
                  range_label=label, range_ago=ago, charts=charts, games=games, latest=latest, gb=GB,
                  players=sum(g["players"] or 0 for g in games),
                  problems=box_problems(box_rows, game_rows), top=top,
                  attacks=db.attack_sources(until - 86400), attackers=db.attackers(until - 86400, limit=5),
                  tuned=network_tuned(db))


async def attacks_page(request):
    require(request, "ips")
    db, t = request.app[DB], now()
    reader = getattr(request.app[MANAGER], "attack_reader", None)
    ports = attacks.open_ports()
    return render(request, "attacks.html", day=db.attack_sources(t - 86400), week=db.attack_sources(t - 7 * 86400),
                  attackers=db.attackers(t - 7 * 86400), banned=db.banned_ips(),
                  ports=[(p, attacks.KNOWN_PORTS.get(p, "")) for p in ports],
                  guard=await asyncio.to_thread(attacks.protections),
                  unreadable=sorted(reader.unreadable) if reader else [], threshold=(ATTACK_LOGINS, ATTACK_WINDOW // 60),
                  trusted=db.trusted_ips(), my_ip=client_ip(request))


async def trust_ip(request):
    require(request, "ips")
    form = await request.post()
    try:
        network = str(ipaddress.ip_network(form.get("ip", "").strip(), strict=False))
    except ValueError:
        flash(request, "That isn't an IP address or range.", "error")
        raise web.HTTPFound("/attacks")
    name = form.get("name", "").strip()[:60]
    request.app[DB].trust_ip(network, name, request[USER]["username"])
    audit(request, "trust ip", target=network, detail=name)
    flash(request, f"{network} won't be flagged, and its earlier failed logins are cleared.")
    raise web.HTTPFound("/attacks")


async def untrust_ip(request):
    require(request, "ips")
    network = (await request.post()).get("ip", "")
    request.app[DB].untrust_ip(network)
    audit(request, "untrust ip", target=network)
    flash(request, f"{network} is off the admin list.")
    raise web.HTTPFound("/attacks")


async def ip_lookup_page(request):
    require(request, "ips")
    typed = request.query.get("ip", "").strip()
    ip = whois.clean_ip(typed)
    result = accounts = None
    if ip:
        result = await whois.lookup(ip)
        accounts = request.app[DB].ip_accounts(ip)
        audit(request, "ip lookup", target=ip)
    return render(request, "ip_lookup.html", typed=typed, ip=ip, result=result, accounts=accounts,
                  banned=ip in request.app[DB].banned_ips() if ip else False)


BOT_UNIT = "reforger-timer.service"


async def restart_bot(request):
    require(request, "power")
    try:
        await systemctl("restart", BOT_UNIT)
    except (RconError, OSError) as exc:
        audit(request, "restart bot", detail=str(exc), ok=False)
        flash(request, f"The bot didn't restart: {exc}", "error")
        raise web.HTTPFound("/discord")
    audit(request, "restart bot")
    flash(request, "The bot is restarting. It's back in Discord in about 30 seconds.")
    raise web.HTTPFound("/discord")


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


DISCORD_ABOUT = {key: about for _, pages in DISCORD_GROUPS for key, about in pages}


PAGE_CHANNELS = {page: (setting, default) for page, setting, default in welcome_doc.PAGE_CHANNELS}


def published_channels(db):
    row = db.discord_doc("channels")
    return json.loads(row["published"]) if row and row["published"] else welcome_doc.default_channels()


def page_channel(request, key):
    """The channel picker shown at the top of a Discord page, if that page has one."""
    if key not in PAGE_CHANNELS:
        return None
    setting, default = PAGE_CHANNELS[key]
    return {"setting": setting, "default": default,
            "picked": published_channels(request.app[DB])["channels"].get(setting, "")}


async def set_page_channel(request):
    """Change one page's channel straight away, apart from that page's own draft."""
    key = request.match_info["page"]
    require(request, f"discord:{key}")
    if key not in PAGE_CHANNELS:
        raise web.HTTPNotFound(text="No such page.")
    setting = PAGE_CHANNELS[key][0]
    form = await request.post()
    value = form.get("channel", "").strip()
    db = request.app[DB]
    doc = published_channels(db)
    if value:
        doc["channels"][setting] = value
    else:
        doc["channels"].pop(setting, None)
    doc, problems = welcome_doc.check_channels(doc)
    if problems:
        flash(request, problems[0])
        raise web.HTTPFound(f"/discord/{key}")
    db.publish_discord_doc("channels", json.dumps(doc), request[USER]["username"], keep_draft=True)
    names = {c["id"]: c["name"] for c in bridge(request).get("channels", [])}
    audit(request, f"set {dict(DISCORD_PAGES)[key]} channel", detail=f"#{names[value]}" if value in names else "default")
    flash(request, "Channel changed. The bot moves it there within a minute.")
    raise web.HTTPFound(f"/discord/{key}")


def discord_context(key, **extra):
    base = {"types": welcome_doc.TYPES, "placeholders": welcome_doc.PLACEHOLDERS,
            "ban_placeholders": welcome_doc.BAN_PLACEHOLDERS, "dm_title": welcome_doc.DM_TITLE,
            "dm_text": welcome_doc.DM_TEXT, "ticket_title": welcome_doc.TICKET_TITLE,
            "pages": DISCORD_PAGES, "page": key, "kind": key, "action_url": f"/discord/{key}", "heading": None,
            "about": DISCORD_ABOUT.get("posts" if key == "post" else key, ""),
            "text_ids": text_ids, "channel_settings": welcome_doc.CHANNEL_SETTINGS,
            "page_channels": welcome_doc.PAGE_CHANNELS,
            "role_settings": welcome_doc.ROLE_SETTINGS,
            "auto_lengths": [(s, length_text(s) if s else "Permanent") for s in welcome_doc.AUTO_BAN_LENGTHS]}
    return {**base, **extra}


async def discord_page(request):
    key = request.match_info["page"]
    require(request, f"discord:{key}")
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
    recent = request.app[DB].staff_alerts(10) if key == "staffalerts" else []
    return render(request, f"discord_{key}.html", bridge=report, default_doc=defaults(), problems=[], **state,
                  recent=recent, pick=page_channel(request, key), **discord_context(key))


async def discord_save(request):
    key = request.match_info["page"]
    require(request, f"discord:{key}")
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
    if key == "channels" and isinstance(raw, dict) and isinstance(raw.get("channels"), dict):
        # The pages that pick their own channel keep it; this page only sends its own.
        kept = published_channels(db)["channels"]
        raw["channels"] = {**{k: v for k, v in kept.items() if k not in dict(welcome_doc.CHANNEL_SETTINGS)},
                           **raw["channels"]}
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
                      pick=page_channel(request, key), **{**state, "doc": doc}, **context)
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
    guide = db.discord_doc(f"post:{ballistics.POST_IDS[0]}")
    guide = json.loads(guide["published"]) if guide and guide["published"] else None
    guide_channel = str(guide["channel_id"]) if guide and not guide.get("deleted") else ""
    return render(request, "discord_posts.html", posts=posts, bridge=report, guide_channel=guide_channel,
                  guide_channel_name=channels.get(guide_channel, ""), **discord_context("posts"))


async def post_guide(request):
    """Post the bullet drop guide to one channel, or update it where it is."""
    require(request, "discord:posts")
    form = await request.post()
    channel = form.get("channel", "").strip()
    if not channel.isdigit():
        flash(request, "Pick a channel for the guide.")
        raise web.HTTPFound("/discord/posts")
    db, by = request.app[DB], request[USER]["username"]
    for post_id, doc in ballistics.posts(int(channel)):
        clean, problems = welcome_doc.check_post(doc)
        if problems:
            raise web.HTTPInternalServerError(text=" ".join(problems))
        db.publish_discord_doc(f"post:{post_id}", json.dumps(clean), by)
    audit(request, "post bullet drop guide", detail=channel)
    flash(request, "Done. The bot posts the guide within a minute.")
    raise web.HTTPFound("/discord/posts")


async def new_post(request):
    require(request, "discord:posts")
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
    require(request, "discord:posts")
    post_id, key = post_key(request)
    defaults, check = DISCORD_DOCS["post"]
    state = discord_state(request, key, defaults, check)
    report = bridge(request)
    reported = report.get("posts", {}).get(post_id)
    return render(request, "discord_welcome.html", bridge={**report, "posts": None, "post": reported},
                  default_doc=defaults(), problems=[], **state, **post_context(post_id))


async def post_save(request):
    require(request, "discord:posts")
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
                  names=names, action_labels=LINK_ACTIONS, pick=page_channel(request, "links"),
                  **discord_context("links"))


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
                "staffalerts": (welcome_doc.default_staffalerts, welcome_doc.check_staffalerts),
                "feedback": (welcome_doc.default_feedback, welcome_doc.check_feedback),
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


# audit

async def audit_page(request):
    require(request, "audit")
    q = clean(request.query.get("q", ""), 64)
    server = request.query.get("server", "")
    return render(request, "audit.html", rows=request.app[DB].audit(q, 300, server), q=q, chosen=server)


# users

def users_view(request, created=None):
    db = request.app[DB]
    owners = {u["id"] for u in db.users() if named(request, u)}
    return render(request, "users.html", rows=db.users(), roles=assignable_roles(request), created=created,
                  grants=db.all_grants(), access_summary=access_summary, owners=owners, i_own=named(request))


def assignable_roles(request):
    """Only the panel's owners can make a manager."""
    return [r for r in auth.ROLES if named(request) or r != "manager"]


def access_summary(grants):
    taken = sum(g.startswith("-") for g in grants)
    parts = [f"{len(grants) - taken} extra" if len(grants) > taken else "", f"{taken} taken off" if taken else ""]
    return ", ".join(p for p in parts if p) or "Role only"


def grant_keys():
    return {key for key, _ in auth.GRANTS} | {f"discord:{key}" for key, _ in DISCORD_PAGES}


async def users_page(request):
    require(request, "users")
    return users_view(request)


async def access_page(request):
    require(request, "users")
    db = request.app[DB]
    target = db.user(int(request.match_info["user_id"]))
    if target is None or target["role"] == "owner" or named(request, target):
        raise web.HTTPFound("/users")
    if target["role"] == "manager" and not named(request):
        raise web.HTTPForbidden(text="Only the panel's owners can change a manager's access.")
    mine = db.grants(target["id"])
    return render(request, "user_access.html", target=target, mine=mine, grant_list=auth.GRANTS,
                  discord_pages=DISCORD_PAGES, role_can=auth.can,
                  has=lambda key: auth.allows(target["role"], mine, key))


async def add_user(request):
    require(request, "users")
    form = await request.post()
    db = request.app[DB]
    username = form.get("username", "").strip()
    role = form.get("role", "")
    if not username or len(username) > 32 or not username.replace("_", "").replace("-", "").isalnum():
        flash(request, "Usernames use letters, numbers, - and _ (32 max).", "error")
        raise web.HTTPFound("/users")
    if role not in assignable_roles(request):
        raise web.HTTPBadRequest(text="Unknown role.")
    if db.user_by_name(username):
        flash(request, "That username is taken.", "error")
        raise web.HTTPFound("/users")
    password = auth.temp_password()
    db.add_user(username, auth.hash_password(password), role, must_change=True)
    audit(request, "created account", target=username, detail=role)
    return users_view(request, created={"username": username, "password": password})


async def change_user(request):
    require(request, "users")
    db = request.app[DB]
    target = db.user(int(request.match_info["user_id"]))
    if target is None:
        raise web.HTTPNotFound(text="No such account.")
    if (target["role"] == "manager" or named(request, target)) and not named(request):
        raise web.HTTPForbidden(text="Only the panel's owners can change a manager's account.")
    form = await request.post()
    action = form.get("action", "")
    is_owner = target["role"] in auth.TOP and not target["disabled"]
    last_owner = is_owner and db.owner_count() <= 1
    if action == "role":
        role = form.get("role", "")
        if role not in assignable_roles(request):
            raise web.HTTPBadRequest(text="Unknown role.")
        if last_owner and role not in auth.TOP:
            flash(request, "There has to be at least one senior admin.", "error")
        else:
            db.write("UPDATE users SET role = ? WHERE id = ?", role, target["id"])
            audit(request, "changed role", target=target["username"], detail=f"{target['role']} → {role}")
            flash(request, f"{target['username']} is now {auth.ROLE_NAMES[role]}.")
    elif action == "grants":
        ticked = set(form.getall("grant", []))
        chosen = {g for g in grant_keys() if g in ticked and not auth.can(target["role"], g)}
        chosen |= {f"-{g}" for g, _ in auth.GRANTS if g not in ticked and auth.can(target["role"], g)}
        if "discord" in chosen:
            chosen = {g for g in chosen if not g.startswith("discord:")}
        before = db.grants(target["id"])
        db.set_grants(target["id"], chosen)
        if chosen != before:
            audit(request, "changed access", target=target["username"],
                  detail=", ".join(sorted(chosen)) or "role only")
        flash(request, f"{target['username']}'s extra access saved.")
    elif action in ("disable", "enable"):
        if action == "disable" and (target["id"] == request[USER]["id"] or last_owner):
            flash(request, "You can't disable yourself or the last senior admin.", "error")
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
        return users_view(request, created={"username": target["username"], "password": password})
    else:
        raise web.HTTPBadRequest(text="Unknown action.")
    raise web.HTTPFound("/users")


def create_app(config: PanelConfig, db: PanelDB | None = None, manager: ServerManager | None = None,
               start_manager: bool = True) -> web.Application:
    app = web.Application(middlewares=[security, public_site, session], client_max_size=64 * 1024)
    app[CONFIG] = config
    app[DB] = db or PanelDB(config.database)
    app[DB].first_manager("gazlagom")
    app[MANAGER] = manager or ServerManager(config, app[DB])
    app[THROTTLE] = auth.LoginThrottle()
    app[FLASH] = {}
    app[STATS] = OybStats(config.oyb_data)
    app[NETCHECK] = {}
    app[CONSOLE] = {}
    app[SETUPS] = {}
    app[SETUP_TASKS] = set()
    app[FILES_ROOT] = files.instances_root(config.servers)
    app[PICTURES] = Path(config.database).resolve().parent / "server-pictures"
    app[BM] = BanSync(app[DB], config.battlemetrics_token, config.battlemetrics_ban_list) \
        if config.battlemetrics_token else None
    app[AMP] = Amp(**config.amp) if config.amp else None
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(HERE / "templates"),
                             autoescape=True, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(axis=_axis, rolename=lambda role: auth.ROLE_NAMES.get(role, role), ts=_ts, ago=_ago, clock=_clock, hms=_hms, until=_until, span=_span, gb=memory.gb,
                       part=lambda zone: body_part(zone) if zone else "", filesize=_filesize)
    env.globals.update(ban_reasons=reason_options(BAN_REASONS), kick_reasons=reason_options(KICK_REASONS))
    app[JINJA] = env
    app[WORKSHOP] = workshop.Workshop()

    if start_manager:
        async def lifecycle(app):
            app[MANAGER].start()
            if app[BM]:
                app[BM].start()
            if heights.load() is None:
                asyncio.get_running_loop().run_in_executor(None, heights.ensure)
            if network_tuned(app[DB]):
                # A reboot clears the send queue length; nothing in it disturbs the link.
                asyncio.get_running_loop().run_in_executor(None, apply_network_tune)
            yield
            if app[BM]:
                await app[BM].stop()
            await app[MANAGER].stop()
            app[DB].close()
        app.cleanup_ctx.append(lifecycle)

    app.router.add_get("/login", login_page)
    app.router.add_post("/login", login)
    app.router.add_post("/logout", logout)
    app.router.add_get("/account", account_page)
    app.router.add_post("/account", account)
    app.router.add_post("/tour/done", tour_done)
    app.router.add_post("/tour/manage/done", manage_tour_done)
    app.router.add_get("/", dashboard)
    app.router.add_get("/cards.part", dashboard_part)
    app.router.add_get("/guide", guide)
    app.router.add_get("/guide/{page}", guide_page)
    app.router.add_get("/api/status", status_api)
    app.router.add_get("/server/{id}", server_page)
    app.router.add_get("/server/{id}/players.part", players_part)
    app.router.add_get("/server/{id}/memory.part", memory_part)
    app.router.add_get("/server/{id}/monitor", monitor_page)
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
    app.router.add_get("/bans", bans_page)
    app.router.add_post("/bans", add_ban)
    app.router.add_post("/bans/{ban_id:\\d+}/remove", remove_ban)
    app.router.add_post("/bans/{ban_id:\\d+}/edit", edit_ban)
    app.router.add_get("/players", players_page)
    app.router.add_get("/players/unusual", unusual_page)
    app.router.add_get("/players/search.json", player_search)
    app.router.add_get("/player/{identity}", player_page)
    app.router.add_post("/player/{identity}/notes", add_note)
    app.router.add_post("/player/{identity}/staff", set_staff)
    app.router.add_get("/sus-settings", sus_page)
    app.router.add_post("/sus-settings", sus_save)
    app.router.add_get("/game-admins", game_admins_page)
    app.router.add_get("/mortar", mortar_page)
    app.router.add_get("/player/{identity}/investigate", investigate_page)
    app.router.add_post("/mortar/download", mortar_download)
    app.router.add_get("/mortar/map", mortar_map_page)
    app.router.add_get("/mortar/panel/static/{name}", mortar_asset)
    app.router.add_get("/mortar/panel/loadouts", mortar_loadouts)
    app.router.add_get("/mortar/panel/tiles/{z}/{x}/{y}", mortar_tile)
    app.router.add_get("/mortar/panel/map", mortar_picture)
    app.router.add_post("/api/mortar/calculate", mortar_calculate)
    app.router.add_post("/game-admins", add_game_admin)
    app.router.add_post("/game-admins/{id}/remove", remove_game_admin)
    app.router.add_post("/game-admins/{id}/restart", restart_through_amp)
    app.router.add_get("/discord", discord_home)
    app.router.add_post("/bot/restart", restart_bot)
    app.router.add_get("/data", data_page)
    app.router.add_get("/settings", settings_page)
    app.router.add_get("/settings/pictures", pictures_page)
    app.router.add_post("/settings/pictures/{id}/picture/upload", upload_picture)
    app.router.add_post("/settings/pictures/{id}/remove", remove_picture)
    app.router.add_post("/settings/pictures/{id}/details", save_card_details)
    app.router.add_get("/server-picture/{name}", picture_serve)
    app.router.add_get("/control", control_redirect)
    app.router.add_post("/server/{id}/power/{action}", control_power)
    app.router.add_get("/server/{id}/kills.json", control_kills)
    app.router.add_get("/control-map/map.json", control_map)
    app.router.add_get("/control-map/tiles/{z}/{x}/{y}", control_tile)
    app.router.add_get("/control-map/picture", control_picture)
    app.router.add_get("/control-map/{name}", control_leaflet)
    app.router.add_get("/attacks", attacks_page)
    app.router.add_post("/attacks/trust", trust_ip)
    app.router.add_post("/attacks/untrust", untrust_ip)
    app.router.add_route("GET", "/settings/amp", amp_login_page)
    app.router.add_post("/settings/amp", amp_login_page)
    app.router.add_get("/manage", manage_index)
    app.router.add_get("/manage/new", manage_new_page)
    app.router.add_post("/manage/new", manage_create)
    app.router.add_get("/manage/{inst}", manage_overview)
    app.router.add_post("/manage/{inst}/delete", manage_delete)
    app.router.add_post("/manage/{inst}/power/{action}", manage_power)
    app.router.add_route("GET", "/manage/{inst}/console", manage_console)
    app.router.add_get("/manage/{inst}/console.txt", manage_console)
    app.router.add_post("/manage/{inst}/console", manage_console)
    app.router.add_route("GET", "/manage/{inst}/settings", manage_settings)
    app.router.add_post("/manage/{inst}/admins", manage_admins)
    app.router.add_post("/manage/{inst}/settings", manage_settings)
    app.router.add_get("/files", files_index)
    app.router.add_get("/files/{inst}", file_browser)
    app.router.add_post("/files/{inst}", file_action)
    app.router.add_get("/files/{inst}/{rel:.*}", file_browser)
    app.router.add_post("/files/{inst}/{rel:.*}", file_action)
    app.router.add_post("/instances/{inst}/{action}", instance_power)
    app.router.add_get("/mods/{inst}", mods_page)
    app.router.add_post("/mods/{inst}", mods_change)
    app.router.add_route("GET", "/console", console_page)
    app.router.add_post("/console", console_page)
    app.router.add_route("GET", "/network", network_page)
    app.router.add_post("/network", network_page)
    app.router.add_post("/network/tune", network_tune)
    app.router.add_get("/bot/data", bot_data_page)
    app.router.add_get("/ip-lookup", ip_lookup_page)
    app.router.add_post("/discord/posts/new", new_post)
    app.router.add_post("/discord/posts/guide", post_guide)
    app.router.add_get("/discord/posts/{post}", post_page)
    app.router.add_post("/discord/posts/{post}", post_save)
    app.router.add_post("/discord/{page}/channel", set_page_channel)
    app.router.add_get("/discord/{page}", discord_page)
    app.router.add_post("/discord/{page}", discord_save)
    app.router.add_get("/audit", audit_page)
    app.router.add_get("/users", users_page)
    app.router.add_post("/users", add_user)
    app.router.add_post("/users/{user_id:\\d+}", change_user)
    app.router.add_get("/users/{user_id:\\d+}/access", access_page)
    app.router.add_static("/static/", HERE / "static")
    app.router.add_static("/brand/", BRAND)
    return app
