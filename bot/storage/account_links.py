"""Durable Discord/Reforger identity links, independent of match resets."""
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from bot.config import configure_connection

LOG = logging.getLogger("reforger.account_links")
# Reforger creates a Game Identity per platform and a Bohemia account can hold
# one of each, so three covers PC, Xbox and PlayStation with nothing spare.
MAX_IDENTITIES = 3


class LinkConflict(ValueError):
    pass


class AccountLinks:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        configure_connection(self.db)
        # Keyed by identity, not by member: Reforger issues one Game Identity
        # per platform, so a player on PC and Xbox genuinely owns two. An
        # identity still belongs to at most one member.
        self.db.execute('''CREATE TABLE IF NOT EXISTS account_links (
            guild INTEGER NOT NULL, discord_id INTEGER NOT NULL,
            identity TEXT NOT NULL, linked_at REAL NOT NULL,
            verified_by TEXT NOT NULL,
            PRIMARY KEY(guild,identity))''')
        self.db.commit()
        self._widen_links()
        self.db.executescript('''CREATE TABLE IF NOT EXISTS link_requests (
            token TEXT PRIMARY KEY, guild INTEGER NOT NULL, discord_id INTEGER NOT NULL,
            identity TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, reviewer INTEGER);
            CREATE TABLE IF NOT EXISTS join_channel (
            guild INTEGER PRIMARY KEY, channel INTEGER NOT NULL, message INTEGER);
            CREATE TABLE IF NOT EXISTS link_review (
            guild INTEGER PRIMARY KEY, channel INTEGER, control INTEGER, reviewer_role INTEGER);
            CREATE TABLE IF NOT EXISTS faction_choice (
            guild INTEGER NOT NULL, discord_id INTEGER NOT NULL, faction TEXT NOT NULL,
            PRIMARY KEY(guild, discord_id));
            CREATE TABLE IF NOT EXISTS faction_roles (
            guild INTEGER NOT NULL, faction TEXT NOT NULL, role INTEGER NOT NULL,
            PRIMARY KEY(guild, faction));
        ''')
        # Older databases predate the stored Discord display name.
        if "discord_name" not in {r[1] for r in self.db.execute("PRAGMA table_info(link_requests)")}:
            self.db.execute("ALTER TABLE link_requests ADD COLUMN discord_name TEXT")
        self.db.commit()

    def _widen_links(self):
        """Older databases keyed account_links by member, which capped a player
        at one platform. Rebuild them keyed by identity; the rows are already
        one-per-member so nothing is lost."""
        sql = self.db.execute("SELECT sql FROM sqlite_master WHERE name='account_links'").fetchone()
        if not sql or "PRIMARYKEY(guild,identity)" in sql[0].replace(" ", ""):
            return
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute('''CREATE TABLE account_links_v2 (
                guild INTEGER NOT NULL, discord_id INTEGER NOT NULL,
                identity TEXT NOT NULL, linked_at REAL NOT NULL,
                verified_by TEXT NOT NULL, PRIMARY KEY(guild,identity))''')
            self.db.execute("INSERT INTO account_links_v2 SELECT guild,discord_id,identity,linked_at,verified_by FROM account_links")
            self.db.execute("DROP TABLE account_links")
            self.db.execute("ALTER TABLE account_links_v2 RENAME TO account_links")
        LOG.info("account_links rebuilt: a member may now hold one identity per platform")

    def identities(self, guild, discord_id):
        """Every game account this member holds, oldest link first. The first
        is their original one, which callers use when they can only show one."""
        return [row[0] for row in self.db.execute(
            "SELECT identity FROM account_links WHERE guild=? AND discord_id=? ORDER BY linked_at, identity",
            (guild, discord_id))]

    def owner(self, guild, identity):
        row = self.db.execute("SELECT discord_id FROM account_links WHERE guild=? AND identity=?",
                              (guild, identity)).fetchone()
        return row[0] if row else None

    def lookup(self, guild, discord_id):
        """The member's first linked identity, or None. Prefer identities()."""
        found = self.identities(guild, discord_id)
        return found[0] if found else None

    def verified_link(self, guild, discord_id, identity, verified_by):
        """Call only after ownership verification. Never silently replace a link."""
        identity = str(uuid.UUID(identity))
        if not verified_by:
            raise ValueError("Verification evidence is required")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self._insert_link(guild, discord_id, identity, verified_by)

    def _insert_link(self, guild, discord_id, identity, verified_by):
        held = self.identities(guild, discord_id)
        if identity in held:
            return
        if len(held) >= MAX_IDENTITIES:
            raise LinkConflict(
                f"This Discord account already holds {MAX_IDENTITIES} game accounts, "
                "which is one per platform. Ask an admin to remove one first.")
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
            held = self.identities(guild, discord_id)
            if identity in held:
                raise LinkConflict("That game account is already linked to you.")
            if len(held) >= MAX_IDENTITIES:
                raise LinkConflict(f"You already have {MAX_IDENTITIES} linked game accounts, "
                                   "which is one per platform. Ask an admin to remove one first.")
            owner = self.owner(guild, identity)
            if owner is not None:
                raise LinkConflict("That game account is already linked to another member. Contact an admin.")
            self.db.execute("UPDATE link_requests SET status='replaced' WHERE guild=? AND discord_id=? AND status='pending'", (guild, discord_id))
            self.db.execute("INSERT INTO link_requests"
                            "(token,guild,discord_id,identity,name,status,created,reviewer,discord_name)"
                            " VALUES (?,?,?,?,?,'pending',?,NULL,?)",
                            (token, guild, discord_id, identity, name, time.time(), discord_name))
        return token

    def auto_link(self, guild, discord_id, identity, name, discord_name=""):
        """Link a member the tracker already identified, with no admin step.

        The approved request is written alongside the link because that row is
        where every board reads a player's name from; without it a linked
        player shows up as their Discord id.
        """
        identity = str(uuid.UUID(identity))
        token = uuid.uuid4().hex
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            owner = self.owner(guild, identity)
            if owner is not None:
                raise LinkConflict("That game account is already linked to another member. Contact an admin.")
            self._insert_link(guild, discord_id, identity, "auto:tracker")
            self.db.execute("UPDATE link_requests SET status='replaced' WHERE guild=? AND discord_id=? AND status='pending'", (guild, discord_id))
            self.db.execute("INSERT INTO link_requests"
                            "(token,guild,discord_id,identity,name,status,created,reviewer,discord_name)"
                            " VALUES (?,?,?,?,?,'approved',?,NULL,?)",
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

    def unlink(self, guild, discord_id, identity=None):
        """Remove an approved link so an abused account stops earning XP.

        With no identity, every link the member holds goes. The game accounts'
        tracked playtime and XP are untouched, so a genuine owner can re-link
        later and keep their history. Returns the identities removed.
        """
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            held = self.identities(guild, discord_id)
            going = [i for i in held if identity is None or i == identity]
            for one in going:
                self.db.execute("DELETE FROM account_links WHERE guild=? AND identity=?", (guild, one))
        return going

    def status(self, guild, discord_id):
        held = self.identities(guild, discord_id)
        if held:
            more = "" if len(held) >= MAX_IDENTITIES else (
                " Playing on another platform? Link that account too — Reforger gives you a separate "
                "ID per platform and your stats are added together.")
            return (f"Your Reforger account is linked ({len(held)} of {MAX_IDENTITIES}). "
                    f"Previously tracked playtime stays with your game account.{more}")
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

    def set_faction(self, guild, discord_id, faction):
        with self.db:
            self.db.execute("INSERT INTO faction_choice(guild,discord_id,faction) VALUES (?,?,?) "
                            "ON CONFLICT(guild,discord_id) DO UPDATE SET faction=excluded.faction",
                            (guild, discord_id, faction))

    def faction(self, guild, discord_id):
        row = self.db.execute("SELECT faction FROM faction_choice WHERE guild=? AND discord_id=?",
                              (guild, discord_id)).fetchone()
        return row[0] if row else None

    def save_faction_role(self, guild, faction, role):
        with self.db:
            self.db.execute("INSERT INTO faction_roles(guild,faction,role) VALUES (?,?,?) "
                            "ON CONFLICT(guild,faction) DO UPDATE SET role=excluded.role",
                            (guild, faction, role))

    def faction_role(self, guild, faction):
        row = self.db.execute("SELECT role FROM faction_roles WHERE guild=? AND faction=?",
                              (guild, faction)).fetchone()
        return row[0] if row else None

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
