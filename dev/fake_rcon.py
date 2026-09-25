"""A stand-in Reforger RCON server for trying the panel without a game running.

    .venv/bin/python dev/fake_rcon.py --port 19999 --password test
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from panel.rcon import packet, parse  # noqa: E402

SAMPLE_PLAYERS = [
    ("0", "Sgt Havoc", "5f1c2a90-8b1e-4a57-9a3e-2d4b6c8e0f11"),
    ("1", "Pte Rook", "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d"),
    ("3", "Cpl Fennel", "0e9d8c7b-6a5f-4e3d-8c2b-1a0f9e8d7c6b"),
]


class FakeRcon(asyncio.DatagramProtocol):
    def __init__(self, password, players=None, chunk=0, direct=False):
        """By default it answers like Reforger: an empty reply, then the output
        as server messages. direct=True puts the output in the reply itself."""
        self.password = password
        self.direct = direct
        self.message_seq = 0
        self.players = list(players if players is not None else SAMPLE_PLAYERS)
        self.chunk = chunk
        self.commands = []
        self.bans = {}
        self.clients = set()
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def players_text(self):
        lines = ["Players on server: [Player#] ; [Player UID] ; [Player Name]"]
        lines += [f"{pid} ; {identity} ; {name}" for pid, name, identity in self.players]
        return "\n".join(lines)

    def reply(self, text):
        if text == "#players":
            return self.players_text()
        words = text.split(" ", 4)
        if words[0] == "#kick" and len(words) > 1:
            self.players = [p for p in self.players if p[0] != words[1]]
            return f"Player {words[1]} kicked"
        if words[:2] == ["#ban", "create"] and len(words) >= 4:
            self.bans[words[2]] = words[4] if len(words) > 4 else ""
            self.players = [p for p in self.players if p[2] != words[2]]
            return f"Ban created for {words[2]}"
        if words[:2] == ["#ban", "remove"] and len(words) >= 3:
            self.bans.pop(words[2], None)
            return f"Ban removed for {words[2]}"
        if text.startswith("#unknown"):
            return f"unknown command '{text[1:]}'"
        return f"ok: {text}"

    def datagram_received(self, data, addr):
        parsed = parse(data)
        if parsed is None:
            return
        kind, body = parsed
        if kind == 0:
            ok = body.decode() == self.password
            if ok:
                self.clients.add(addr)
            self.transport.sendto(packet(0, b"\x01" if ok else b"\x00"), addr)
            if ok and not self.direct:
                self.say("Logged In! Client ID: #1")
        elif kind == 1 and addr in self.clients:
            seq, text = body[0], body[1:].decode()
            if text:
                self.commands.append(text)
            answer = self.reply(text).encode() if text else b""
            if text and not self.direct:
                self.transport.sendto(packet(1, bytes([seq])), addr)
                self.say(f"Processing Command: {text}")
                self.say(answer.decode())
            elif self.chunk and len(answer) > self.chunk:
                parts = [answer[i:i + self.chunk] for i in range(0, len(answer), self.chunk)]
                for index, part in reversed(list(enumerate(parts))):
                    self.transport.sendto(packet(1, bytes([seq, 0, len(parts), index]) + part), addr)
            else:
                self.transport.sendto(packet(1, bytes([seq]) + answer), addr)

    def say(self, text, seq=None):
        if seq is None:
            seq = self.message_seq
            self.message_seq = (self.message_seq + 1) % 256
        for addr in self.clients:
            self.transport.sendto(packet(2, bytes([seq]) + text.encode()), addr)


async def serve(port, password, host="127.0.0.1", **kwargs):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: FakeRcon(password, **kwargs), local_addr=(host, port))
    return transport, protocol


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=19999)
    parser.add_argument("--password", default="test")
    args = parser.parse_args()
    await serve(args.port, args.password)
    print(f"fake RCON on 127.0.0.1:{args.port}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
