"""Bounded incremental reads of dated console logs; work runs off the event loop."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import logging
from pathlib import Path
import re
import sqlite3

from combat_parser import parse_kill
from combat_store import record

LOG = logging.getLogger('reforger.combat')
STAMP = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d{3})')
FOLDER = re.compile(r'^logs_(\d{4}-\d{2}-\d{2})[_-]\d{2}[-_]\d{2}[-_]\d{2}$')


def scan(db, server, path, budget=4*1024*1024):
    date = FOLDER.fullmatch(path.parent.name)
    if not date:
        return 0
    initial_date = datetime.strptime(date[1],'%Y-%m-%d').date()
    processed, added = 0, 0
    with db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT position,anchor,day,clock FROM combat_sources WHERE server=? AND path=?',
                         (server,str(path))).fetchone()
        position,anchor,day,clock = row if row else (0,'',0,None)
        with path.open('rb') as stream:
            stream.seek(0,2)
            size = stream.tell()
            stream.seek(max(0,position-256))
            actual = hashlib.sha256(stream.read(min(position,256))).hexdigest()
            if size < position or (anchor and actual != anchor):
                # Retain the previous cursor. A rewritten file is ambiguous;
                # guessing its new epoch could credit old events on a new day.
                LOG.error('Combat log rewritten/truncated; skipping ambiguous source: %s',path)
                return 0
            stream.seek(position)
            while processed < budget:
                raw = stream.readline(65537)
                if not raw or not raw.endswith(b'\n'):
                    if len(raw)>65536:
                        LOG.error('Oversized combat source line at %s:%s; source paused',path,position)
                    break
                processed += len(raw)
                position = stream.tell()
                line = raw.decode('utf-8','replace').rstrip('\r\n')
                stamp = STAMP.match(line)
                if stamp:
                    current = stamp[1]
                    if int(current[:2]) > 23 or int(current[3:5]) > 59 or int(current[6:8]) > 59:
                        continue
                    # Midnight is a large backwards jump, not a delayed message.
                    def seconds(value):
                        h,m,s = value.split(':')
                        return int(h)*3600+int(m)*60+float(s)
                    event_day = day
                    if clock and seconds(clock)-seconds(current)>43200:
                        day += 1
                        event_day = day
                        clock = current
                    elif clock and day > 0 and seconds(current)-seconds(clock)>43200:
                        event_day = day-1  # Late previous-day entry, no clock rewind.
                    elif clock is None or current >= clock:
                        clock = current
                event = parse_kill(line)
                if event:
                    occurred = f'{initial_date+timedelta(days=event_day)}T{event.clock}'
                    added += int(record(db,server,occurred,event))
                elif ': KILL ' in line:
                    LOG.warning('Unrecognised combat event at %s:%s; not counted',path,position)
            stream.seek(max(0,position-256))
            anchor = hashlib.sha256(stream.read(min(position,256))).hexdigest()
        db.execute('INSERT OR REPLACE INTO combat_sources VALUES (?,?,?,?,?,?)',
                   (server,str(path),position,anchor,day,clock))
    return added


def ingest(database, servers):
    added = 0
    # Separate connection to the SAME database; created and closed on this worker.
    with closing(sqlite3.connect(database,timeout=10)) as db:
        from config import configure_connection
        configure_connection(db)
        for server in servers:
            if not server.enabled:
                continue
            paths = sorted(Path(server.log_dir).glob('logs_*/console.log'))
            # Import available history in bounded passes, then follow new runs.
            work = 0
            for path in paths:
                try:
                    row = db.execute('SELECT position FROM combat_sources WHERE server=? AND path=?',
                                     (server.id,str(path))).fetchone()
                    if row and path.stat().st_size == row[0]:
                        continue
                    added += scan(db,server.id,path)
                    after = db.execute('SELECT position FROM combat_sources WHERE server=? AND path=?',
                                       (server.id,str(path))).fetchone()
                    if after and (not row or after[0] > row[0]):
                        work += 1
                    if work >= 8:
                        break
                except (OSError,sqlite3.Error,ValueError):
                    LOG.exception('Combat source could not be read; will retry: %s',path)
    return added


class CombatIngestor:
    def __init__(self, bot):
        self.database = bot.account_links.db.execute('PRAGMA database_list').fetchone()[2]
        self.servers = bot.config.servers

    async def run(self):
        LOG.info('Combat stats ingestion ready; vanilla kill logs; AI kill counts unavailable')
        while True:
            try:
                task = asyncio.create_task(asyncio.to_thread(ingest,self.database,self.servers))
                try:
                    added = await asyncio.shield(task)
                except asyncio.CancelledError:
                    await task  # Finish/close the worker connection before shutdown.
                    raise
                if added:
                    LOG.info('Recorded %s new combat events',added)
            except (OSError,sqlite3.Error):
                LOG.exception('Combat ingestion failed; will retry')
            await asyncio.sleep(15)
