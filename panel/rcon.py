"""BattlEye RCon over UDP, which is what Reforger speaks on its rcon port."""

import asyncio
import struct
import zlib


class RconError(Exception):
    pass


def packet(kind: int, body: bytes) -> bytes:
    payload = b"\xff" + bytes([kind]) + body
    return b"BE" + struct.pack("<I", zlib.crc32(payload) & 0xFFFFFFFF) + payload


def parse(data: bytes) -> tuple[int, bytes] | None:
    if len(data) < 8 or data[:2] != b"BE" or data[6] != 0xFF:
        return None
    if struct.unpack("<I", data[2:6])[0] != zlib.crc32(data[6:]) & 0xFFFFFFFF:
        return None
    return data[7], data[8:]


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, client):
        self.client = client

    def datagram_received(self, data, addr):
        self.client._received(data)

    def error_received(self, exc):
        self.client._failed(exc)

    def connection_lost(self, exc):
        self.client._failed(exc or RconError("connection closed"))


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.on_message = None
        self._transport = None
        self._login = None
        self._pending: dict[int, asyncio.Future] = {}
        self._parts: dict[int, dict[int, bytes]] = {}
        self._seq = 0
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._transport is not None and not self._transport.is_closing()

    async def connect(self):
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _Protocol(self), remote_addr=(self.host, self.port))
        self._login = loop.create_future()
        self._transport.sendto(packet(0, self.password.encode()))
        try:
            ok = await asyncio.wait_for(self._login, self.timeout)
        except asyncio.TimeoutError:
            self.close()
            raise RconError("no answer from the server") from None
        except ConnectionRefusedError:
            self.close()
            raise RconError("nothing is listening on the RCON port (server down, or RCON not set up in server.json)") from None
        except Exception as exc:
            self.close()
            raise RconError(str(exc) or "could not reach the server") from None
        if not ok:
            self.close()
            raise RconError("wrong rcon password")

    def close(self):
        if self._transport:
            self._transport.close()
        self._transport = None
        self._failed(RconError("connection closed"))

    async def command(self, text: str) -> str:
        if not self.connected:
            raise RconError("not connected")
        async with self._lock:
            seq = self._seq
            self._seq = (self._seq + 1) % 256
            future = asyncio.get_running_loop().create_future()
            self._pending[seq] = future
            self._parts.pop(seq, None)
            self._transport.sendto(packet(1, bytes([seq]) + text.encode()))
        try:
            return await asyncio.wait_for(future, self.timeout)
        except asyncio.TimeoutError:
            raise RconError("command timed out") from None
        finally:
            self._pending.pop(seq, None)
            self._parts.pop(seq, None)

    def _received(self, data: bytes):
        parsed = parse(data)
        if parsed is None:
            return
        kind, body = parsed
        if kind == 0 and self._login and not self._login.done():
            self._login.set_result(body[:1] == b"\x01")
        elif kind == 1 and body:
            self._answer(body[0], body[1:])
        elif kind == 2 and body:
            self._transport.sendto(packet(2, body[:1]))
            if self.on_message:
                self.on_message(body[1:].decode(errors="replace"))

    def _answer(self, seq: int, body: bytes):
        future = self._pending.get(seq)
        if future is None or future.done():
            return
        if len(body) >= 3 and body[0] == 0:
            total, index = body[1], body[2]
            parts = self._parts.setdefault(seq, {})
            parts[index] = body[3:]
            if len(parts) < total:
                return
            body = b"".join(parts[i] for i in range(total))
        future.set_result(body.decode(errors="replace"))

    def _failed(self, exc):
        futures = list(self._pending.values())
        if self._login:
            futures.append(self._login)
        for future in futures:
            if not future.done():
                future.set_exception(exc)
