"""Durable one-to-one Discord/Reforger identity links, independent of match resets."""
import sqlite3
import time
import uuid
from pathlib import Path

from bot.config import configure_connection


class LinkConflict(ValueError):
    pass


class AccountLinks:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        configure_connection(self.db)
        self.db.execute('''CREATE TABLE IF NOT EXISTS account_links (
            guild INTEGER NOT NULL, discord_id INTEGER NOT NULL,
            identity TEXT NOT NULL, linked_at REAL NOT NULL,
            verified_by TEXT NOT NULL,
            PRIMARY KEY(guild,discord_id), UNIQUE(guild,identity))''')
        self.db.commit()
        self.db.executescript('''CREATE TABLE IF NOT EXISTS link_requests (
            token TEXT PRIMARY KEY, guild INTEGER NOT NULL, discord_id INTEGER NOT NULL,
            identity TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, reviewer INTEGER);
            CREATE TABLE IF NOT EXISTS join_channel (
            guild INTEGER PRIMARY KEY, channel INTEGER NOT NULL, message INTEGER);
            CREATE TABLE IF NOT EXISTS link_review (
            guild INTEGER PRIMARY KEY, channel INTEGER, control INTEGER, reviewer_role INTEGER);
        ''')
        # Older databases predate the stored Discord display name.
        if "discord_name" not in {r[1] for r in self.db.execute("PRAGMA table_info(link_requests)")}:
            self.db.execute("ALTER TABLE link_requests ADD COLUMN discord_name TEXT")
        self.db.commit()

    def lookup(self, guild, discord_id):
        row = self.db.execute("SELECT identity FROM account_links WHERE guild=? AND discord_id=?",
                              (guild, discord_id)).fetchone()
        return row[0] if row else None

    def verified_link(self, guild, discord_id, identity, verified_by):
        """Call only after ownership verification. Never silently replace a link."""
        identity = str(uuid.UUID(identity))
        if not verified_by:
            raise ValueError("Verification evidence is required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._insert_link(guild, discord_id, identity, verified_by)

    def _insert_link(self, guild, discord_id, identity, verified_by):
        existing = self.lookup(guild, discord_id)
        if existing == identity:
            return
        if existing:
            raise LinkConflict("This Discord account already has a linked game account.")
        try:
            self.db.execute("INSERT INTO account_links VALUES (?,?,?,?,?)",
                            (guild, discord_id, identity, time.time(), verified_by))
        except sqlite3.IntegrityError as exc:
            raise LinkConflict("This game account is already linked to another Discord account.") from exc

    def submit(self, guild, discord_id, identity, name, discord_name=""):
        identity = str(uuid.UUID(identity))
        token = uuid.uuid4().hex
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.lookup(guild, discord_id):
                raise LinkConflict("Your Discord account is already linked.")
            if self.db.execute("SELECT 1 FROM account_links WHERE guild=? AND identity=?", (guild, identity)).fetchone():
                raise LinkConflict("That game account is already linked. Contact an admin.")
            self.db.execute("UPDATE link_requests SET status='replaced' WHERE guild=? AND discord_id=? AND status='pending'", (guild, discord_id))
            self.db.execute("INSERT INTO link_requests"
                            "(token,guild,discord_id,identity,name,status,created,reviewer,discord_name)"
                            " VALUES (?,?,?,?,?,'pending',?,NULL,?)",
                            (token, guild, discord_id, identity, name, time.time(), discord_name))
        return token

    def pending(self, guild):
        return self.db.execute("SELECT token,discord_id,identity,name,discord_name FROM link_requests WHERE guild=? AND status='pending' ORDER BY created LIMIT 25", (guild,)).fetchall()

    def review(self, guild, token, reviewer, approve):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT discord_id,identity FROM link_requests WHERE guild=? AND token=? AND status='pending'", (guild, token)).fetchone()
            if not row:
                raise LinkConflict("This request has already been handled or replaced.")
            if approve:
                self._insert_link(guild, row[0], row[1], f"admin:{reviewer}")
            self.db.execute("UPDATE link_requests SET status=?,reviewer=? WHERE token=?",
                            ("approved" if approve else "rejected", reviewer, token))

    def status(self, guild, discord_id):
        identity = self.lookup(guild, discord_id)
        if identity:
            return "Your Reforger account is linked. Previously tracked playtime stays with your game account."
        row = self.db.execute("SELECT status FROM link_requests WHERE guild=? AND discord_id=? ORDER BY created DESC LIMIT 1", (guild, discord_id)).fetchone()
        if not row:
            return "No linking request yet. Use Link Reforger account to submit your in-game name."
        return {"pending": "Your request is waiting for admin approval.",
                "rejected": "Your request was rejected. Check with an admin before submitting again."}.get(row[0], "Submit a new linking request.")

    def request(self, guild, token):
        """One pending/handled request by token, for the staff review alert."""
        return self.db.execute(
            "SELECT discord_id,identity,name,status,discord_name FROM link_requests WHERE guild=? AND token=?",
            (guild, token)).fetchone()

    def review_settings(self, guild):
        row = self.db.execute("SELECT channel,control,reviewer_role FROM link_review WHERE guild=?",
                              (guild,)).fetchone()
        return dict(channel=row[0], control=row[1], reviewer_role=row[2]) if row else \
            dict(channel=None, control=None, reviewer_role=None)

    def save_review_settings(self, guild, channel=None, control=None, reviewer_role=None):
        # COALESCE keeps existing values when a field is not being updated.
        with self.db:
            self.db.execute(
                "INSERT INTO link_review(guild,channel,control,reviewer_role) VALUES(?,?,?,?) "
                "ON CONFLICT(guild) DO UPDATE SET channel=COALESCE(?,channel),"
                "control=COALESCE(?,control),reviewer_role=COALESCE(?,reviewer_role)",
                (guild, channel, control, reviewer_role, channel, control, reviewer_role))

    def close(self):
        self.db.close()
