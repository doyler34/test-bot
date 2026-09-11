"""Combat tables in the existing account-links SQLite database; never touch XP."""
from datetime import datetime, timezone
import hashlib
import json
from bot.storage.rank_persistence import backup_before


def migrate(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='combat_events'").fetchone():
        return
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


def record(db, server, occurred, event):
    """Call inside the same transaction as the source checkpoint."""
    key = hashlib.sha256(json.dumps([occurred,event.victim,event.killer,event.relation],separators=(',',':')).encode()).hexdigest()
    cursor = db.execute('INSERT OR IGNORE INTO combat_events VALUES (?,?,?,?,?,?)',
                        (server,key,occurred,event.victim,event.killer,event.relation))
    if not cursor.rowcount:
        return False
    observed = datetime.now(timezone.utc).isoformat()
    db.execute('''INSERT INTO combat_totals(identity,deaths,updated_at) VALUES (?,1,?)
        ON CONFLICT(identity) DO UPDATE SET deaths=deaths+1,updated_at=excluded.updated_at''', (event.victim,observed))
    if event.killer and event.killer != event.victim:
        kills, teamkills = int(event.relation == 'ENEMY'), int(event.relation == 'TK')
        db.execute('''INSERT INTO combat_totals(identity,player_kills,teamkills,updated_at) VALUES (?,?,?,?)
            ON CONFLICT(identity) DO UPDATE SET player_kills=player_kills+excluded.player_kills,
            teamkills=teamkills+excluded.teamkills,updated_at=excluded.updated_at''',
            (event.killer,kills,teamkills,observed))
    return True


def totals(db, identity):
    row = db.execute('SELECT player_kills,deaths,teamkills,updated_at FROM combat_totals WHERE identity=?',(identity,)).fetchone()
    if row is None:
        return None
    return dict(player_kills=row[0],deaths=row[1],teamkills=row[2],ai_kills=None,updated_at=row[3])
