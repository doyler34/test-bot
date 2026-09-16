"""Combat tables in the existing account-links SQLite database; never touch XP."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from zoneinfo import ZoneInfo
from bot.ranks.rank_rules import XP_PER_KILL, XP_PER_MATCH, XP_PER_TEAMKILL
from bot.storage.rank_persistence import backup_before

# Vanilla records a damage type but never the weapon; gunfire is the only class
# that makes a fair "longest kill", so explosives and vehicles are excluded.
GUNFIRE = 'KINETIC'
# The week turns over at midnight where the community is, not where the box is.
BOSTON = ZoneInfo('America/New_York')

NAME = '''(SELECT r.name FROM link_requests r
         WHERE r.guild=a.guild AND r.discord_id=a.discord_id
           AND r.identity=a.identity AND r.status='approved'
         ORDER BY r.created DESC, r.token LIMIT 1)'''


def migrate(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='combat_events'").fetchone():
        backup_before(db, 'combat-v1')
        with db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''CREATE TABLE combat_events (
                server TEXT, event_key TEXT, occurred TEXT, victim TEXT, killer TEXT,
                relation TEXT, PRIMARY KEY(server,event_key))''')
            db.execute('''CREATE TABLE combat_totals (
                identity TEXT PRIMARY KEY, player_kills INTEGER NOT NULL DEFAULT 0,
                deaths INTEGER NOT NULL DEFAULT 0, teamkills INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE combat_sources (
                server TEXT, path TEXT, position INTEGER NOT NULL, anchor TEXT NOT NULL,
                day INTEGER NOT NULL, clock TEXT, PRIMARY KEY(server,path))''')
    # Per-faction totals were added later; ensure it exists on older databases too.
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS combat_faction_totals (
            identity TEXT NOT NULL, faction TEXT NOT NULL,
            player_kills INTEGER NOT NULL DEFAULT 0, deaths INTEGER NOT NULL DEFAULT 0,
            teamkills INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
            PRIMARY KEY(identity, faction))''')
        # Distance and damage type arrived with the weekly board; older rows keep
        # NULL, which every window query already treats as "not reported".
        columns = {row[1] for row in db.execute('PRAGMA table_info(combat_events)')}
        if 'distance' not in columns:
            db.execute('ALTER TABLE combat_events ADD COLUMN distance REAL')
        if 'damage_type' not in columns:
            db.execute('ALTER TABLE combat_events ADD COLUMN damage_type TEXT')
        # Every window query filters on time first.
        db.execute('CREATE INDEX IF NOT EXISTS combat_events_occurred ON combat_events(occurred)')
        # Combat XP is recomputed per member on every rank tick, so both sides
        # of a kill need to be reachable without scanning the table.
        db.execute('CREATE INDEX IF NOT EXISTS combat_events_killer ON combat_events(killer)')
        db.execute('CREATE INDEX IF NOT EXISTS combat_events_victim ON combat_events(victim)')
        # Which kills belonged to which game. The monitor knows a match's span
        # while it runs but nothing persisted it, so per-game stats need this.
        db.execute('''CREATE TABLE IF NOT EXISTS combat_matches (
            server TEXT NOT NULL, started TEXT NOT NULL, ended TEXT NOT NULL,
            name TEXT, PRIMARY KEY(server, started))''')


def record(db, server, occurred, event):
    """Call inside the same transaction as the source checkpoint."""
    key = hashlib.sha256(json.dumps([occurred,event.victim,event.killer,event.relation],separators=(',',':')).encode()).hexdigest()
    cursor = db.execute('''INSERT OR IGNORE INTO combat_events
        (server,event_key,occurred,victim,killer,relation,distance,damage_type)
        VALUES (?,?,?,?,?,?,?,?)''',
        (server,key,occurred,event.victim,event.killer,event.relation,
         getattr(event,'distance',None),getattr(event,'damage_type',None)))
    if not cursor.rowcount:
        return False
    observed = datetime.now(timezone.utc).isoformat()
    victim_faction = getattr(event, 'victim_faction', None)
    killer_faction = getattr(event, 'killer_faction', None)
    # A death counts only when another player did the killing: AI kills arrive
    # with no killer, and a suicide is the victim killing themselves. Both are
    # deliberately excluded so Deaths stays a player-vs-player figure.
    if event.killer is not None and event.killer != event.victim:
        db.execute('''INSERT INTO combat_totals(identity,deaths,updated_at) VALUES (?,1,?)
            ON CONFLICT(identity) DO UPDATE SET deaths=deaths+1,updated_at=excluded.updated_at''', (event.victim,observed))
        if victim_faction:
            db.execute('''INSERT INTO combat_faction_totals(identity,faction,deaths,updated_at) VALUES (?,?,1,?)
                ON CONFLICT(identity,faction) DO UPDATE SET deaths=deaths+1,updated_at=excluded.updated_at''',
                (event.victim,victim_faction,observed))
        kills, teamkills = int(event.relation == 'ENEMY'), int(event.relation == 'TK')
        db.execute('''INSERT INTO combat_totals(identity,player_kills,teamkills,updated_at) VALUES (?,?,?,?)
            ON CONFLICT(identity) DO UPDATE SET player_kills=player_kills+excluded.player_kills,
            teamkills=teamkills+excluded.teamkills,updated_at=excluded.updated_at''',
            (event.killer,kills,teamkills,observed))
        if killer_faction:
            db.execute('''INSERT INTO combat_faction_totals(identity,faction,player_kills,teamkills,updated_at) VALUES (?,?,?,?,?)
                ON CONFLICT(identity,faction) DO UPDATE SET player_kills=player_kills+excluded.player_kills,
                teamkills=teamkills+excluded.teamkills,updated_at=excluded.updated_at''',
                (event.killer,killer_faction,kills,teamkills,observed))
    return True


def totals(db, identity):
    row = db.execute('SELECT player_kills,deaths,teamkills,updated_at FROM combat_totals WHERE identity=?',(identity,)).fetchone()
    if row is None:
        return None
    return dict(player_kills=row[0],deaths=row[1],teamkills=row[2],ai_kills=None,updated_at=row[3])


def faction_totals(db, identity):
    """Per-faction combat stats for one player, ordered by most kills."""
    rows = db.execute('''SELECT faction,player_kills,deaths,teamkills FROM combat_faction_totals
        WHERE identity=? ORDER BY player_kills DESC, deaths ASC, faction''', (identity,)).fetchall()
    return [dict(faction=r[0],player_kills=r[1],deaths=r[2],teamkills=r[3],ai_kills=None) for r in rows]


def stamp(moment):
    """Render an instant the way combat_events.occurred is written: the game
    server's own local clock, naive, to the millisecond."""
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]


def day_start(now=None):
    """Today's 00:00 in Boston, as an aware instant. The box may well be on UTC,
    so the boundary is found in Boston and converted for comparison by stamp()
    rather than assumed to be local midnight."""
    moment = datetime.now().astimezone() if now is None else now
    if moment.tzinfo is None:
        moment = moment.astimezone()  # a naive clock here means the box's own
    return moment.astimezone(BOSTON).replace(hour=0, minute=0, second=0, microsecond=0)


def week_start(now=None):
    """The most recent Monday 00:00 in Boston."""
    boston = day_start(now)
    return boston - timedelta(days=boston.weekday())


def window(start, end=None):
    return stamp(start), stamp(end) if end is not None else '9999'


# A death or a kill is only ever credited on a player-vs-player row.
SCORED = "killer IS NOT NULL AND killer<>victim"


def window_totals(db, identity, start, end=None):
    """Combat figures for one player inside a time window, straight from events."""
    low, high = window(start, end)
    row = db.execute(f'''SELECT
        COALESCE(SUM(CASE WHEN killer=?1 AND relation='ENEMY' THEN 1 END),0),
        COALESCE(SUM(CASE WHEN killer=?1 AND relation='TK' THEN 1 END),0),
        COALESCE(SUM(CASE WHEN victim=?1 THEN 1 END),0),
        COUNT(*)
        FROM combat_events
        WHERE occurred>=?2 AND occurred<?3 AND {SCORED} AND (killer=?1 OR victim=?1)''',
        (identity, low, high)).fetchone()
    if not row or not row[3]:
        return None
    return dict(player_kills=row[0], teamkills=row[1], deaths=row[2], ai_kills=None,
                longest_kill=longest_kill(db, identity, start, end))


def longest_kill(db, identity, start, end=None):
    """Longest gunfire kill in the window; explosives and vehicles don't qualify."""
    low, high = window(start, end)
    row = db.execute(f'''SELECT MAX(distance) FROM combat_events
        WHERE occurred>=? AND occurred<? AND {SCORED}
          AND killer=? AND relation='ENEMY' AND damage_type=? AND distance IS NOT NULL''',
        (low, high, identity, GUNFIRE)).fetchone()
    return row[0] if row else None


def combat_xp(db, identity):
    """Kill, teamkill and match XP for one player, over their whole history.

    Derived from the events rather than banked, so a re-parse or a late account
    link corrects the balance on the next read with nothing to recompute.
    """
    kills, teamkills = db.execute(f'''SELECT
        COALESCE(SUM(CASE WHEN relation='ENEMY' THEN 1 END),0),
        COALESCE(SUM(CASE WHEN relation='TK' THEN 1 END),0)
        FROM combat_events WHERE killer=? AND {SCORED}''', (identity,)).fetchone()
    # A match only pays out if they actually fought in it, so idling in the
    # lobby earns nothing.
    matches = db.execute(f'''SELECT COUNT(*) FROM combat_matches m
        WHERE EXISTS (SELECT 1 FROM combat_events e
            WHERE e.occurred>=m.started AND e.occurred<=m.ended
              AND (e.killer=?1 OR e.victim=?1) AND {SCORED})''', (identity,)).fetchone()[0]
    return kills*XP_PER_KILL + teamkills*XP_PER_TEAMKILL + matches*XP_PER_MATCH


def record_match(db, server, name, start, end):
    """Remember a finished match so its kills can be sliced out again later."""
    with db:
        db.execute('INSERT OR REPLACE INTO combat_matches VALUES (?,?,?,?)',
                   (server, stamp(start), stamp(end), name))


def recent_matches(db, identity, limit=10, scan=80):
    """The player's last few matches, most recent first. Matches they took no
    part in are skipped rather than listed as a row of zeroes."""
    rows = db.execute(f'''SELECT name, started, kills, deaths FROM (
            SELECT m.name AS name, m.started AS started,
              (SELECT COUNT(*) FROM combat_events e
                 WHERE e.occurred>=m.started AND e.occurred<=m.ended
                   AND e.killer=?1 AND {SCORED} AND e.relation='ENEMY') AS kills,
              (SELECT COUNT(*) FROM combat_events e
                 WHERE e.occurred>=m.started AND e.occurred<=m.ended
                   AND e.victim=?1 AND {SCORED}) AS deaths
            FROM combat_matches m ORDER BY m.started DESC LIMIT ?3)
        WHERE kills>0 OR deaths>0 ORDER BY started DESC LIMIT ?2''',
        (identity, limit, scan)).fetchall()
    return [dict(name=row[0], started=datetime.strptime(row[1], '%Y-%m-%dT%H:%M:%S.%f'),
                 kills=row[2], deaths=row[3]) for row in rows]


def window_standings(db, guild, start, end=None):
    """One row per linked player with activity in the window, best kills first."""
    low, high = window(start, end)
    return db.execute(f'''
        WITH scored AS (
            SELECT victim, killer, relation FROM combat_events
            WHERE occurred>=? AND occurred<? AND {SCORED}
        ), tallied AS (
            SELECT identity, SUM(kills) AS kills, SUM(deaths) AS deaths FROM (
                SELECT killer AS identity, CASE WHEN relation='ENEMY' THEN 1 ELSE 0 END AS kills,
                       0 AS deaths FROM scored
                UNION ALL
                SELECT victim AS identity, 0, 1 FROM scored
            ) GROUP BY identity
        )
        SELECT a.discord_id, t.kills, t.deaths, {NAME}
        FROM account_links a JOIN tallied t ON t.identity=a.identity
        WHERE a.guild=?
        ORDER BY t.kills DESC, t.deaths ASC, a.discord_id ASC''',
        (low, high, guild)).fetchall()
