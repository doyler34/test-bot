"""Combat tables in the existing account-links SQLite database; never touch XP."""
from datetime import datetime, timezone
import hashlib
import json
from bot.storage.rank_persistence import backup_before


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


def record(db, server, occurred, event):
    """Call inside the same transaction as the source checkpoint."""
    key = hashlib.sha256(json.dumps([occurred,event.victim,event.killer,event.relation],separators=(',',':')).encode()).hexdigest()
    cursor = db.execute('INSERT OR IGNORE INTO combat_events VALUES (?,?,?,?,?,?)',
                        (server,key,occurred,event.victim,event.killer,event.relation))
    if not cursor.rowcount:
        return False
    observed = datetime.now(timezone.utc).isoformat()
    victim_faction = getattr(event, 'victim_faction', None)
    killer_faction = getattr(event, 'killer_faction', None)
    # Only count a death when a player did the killing (killer is None for AI),
    # so Deaths matches Kills as a player-vs-player figure. Suicides still count.
    if event.killer is not None:
        db.execute('''INSERT INTO combat_totals(identity,deaths,updated_at) VALUES (?,1,?)
            ON CONFLICT(identity) DO UPDATE SET deaths=deaths+1,updated_at=excluded.updated_at''', (event.victim,observed))
        if victim_faction:
            db.execute('''INSERT INTO combat_faction_totals(identity,faction,deaths,updated_at) VALUES (?,?,1,?)
                ON CONFLICT(identity,faction) DO UPDATE SET deaths=deaths+1,updated_at=excluded.updated_at''',
                (event.victim,victim_faction,observed))
    if event.killer and event.killer != event.victim:
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
