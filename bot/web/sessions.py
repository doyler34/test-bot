"""Short-lived links to the mortar map.

A link is a random token and nothing else. It carries no Discord token, no
account details and no signature to verify anywhere else - the server holds the
list, the link holds the key, and both forget it when it expires.
"""
import secrets
import time

TTL = 3600
SWEEP = 600
# 32 bytes from the system source: not guessable, and short enough to tap.
SIZE = 32


class Sessions:
    def __init__(self, ttl=TTL, clock=time.time):
        self.ttl = max(60, int(ttl))
        self.clock = clock
        self.open_until = {}
        self.holders = {}
        self.swept = 0.0

    def open(self, holder=None):
        """A fresh link. `holder` is kept for support questions, never shown to
        the browser and never written to the log."""
        self.sweep()
        token = secrets.token_urlsafe(SIZE)
        self.open_until[token] = self.clock() + self.ttl
        if holder is not None:
            self.holders[token] = holder
        return token

    def valid(self, token):
        """Is this link still good? Expiry is checked on the way in, so a link
        stops working the moment it runs out rather than when it is swept."""
        if not isinstance(token, str) or not token:
            return False
        expires = self.open_until.get(token)
        if expires is None:
            return False
        if expires <= self.clock():
            self.drop(token)
            return False
        return True

    def holder(self, token):
        return self.holders.get(token)

    def drop(self, token):
        self.open_until.pop(token, None)
        self.holders.pop(token, None)

    def sweep(self):
        """Drop what has run out. Cheap, and only every so often."""
        now = self.clock()
        if now - self.swept < SWEEP:
            return
        self.swept = now
        for token in [t for t, expires in self.open_until.items() if expires <= now]:
            self.drop(token)

    def __len__(self):
        return len(self.open_until)
