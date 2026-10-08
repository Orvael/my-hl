# Created: WIB 2026-10-08 19:0x — Hyperliquid public-WS recorder (my-hl)
# Zero dependencies: stdlib socket + ssl + manual WebSocket frames.
# Bounds (his orders: no bloat): ONE coin, 40MB rotating files, 3 files max
# (~120MB disk hard cap), streaming line-by-line writes, no accumulation.
import socket
import ssl
import base64
import os
import json
import time
import sys

WS_HOST = "api.hyperliquid.xyz"
MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_FILES = 3


def ws_connect(host, path="/ws"):
    raw = socket.create_connection((host, 443), timeout=10)
    ctx = ssl.create_default_context()
    sock = ctx.wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
           "Sec-WebSocket-Version: 13\r\n\r\n" % (path, host, key))
    sock.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("closed during handshake")
        resp += chunk
    status = resp.split(b"\r\n")[0]
    if b"101" not in status:
        raise ConnectionError("handshake refused: %r" % status)
    head, _, rest = resp.partition(b"\r\n\r\n")
    return sock, rest  # rest = frame bytes already received


class FrameReader:
    """Minimal server->client frame parser (server frames are unmasked)."""

    def __init__(self, sock, leftover=b""):
        self.sock = sock
        self.buf = bytearray(leftover)

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf.extend(chunk)

    def read_frame(self):
        self._need(2)
        b1 = self.buf[0]
        op = b1 & 0x0F
        ln = self.buf[1] & 0x7F
        idx = 2
        if ln == 126:
            self._need(4)
            ln = int.from_bytes(self.buf[2:4], "big")
            idx = 4
        elif ln == 127:
            self._need(10)
            ln = int.from_bytes(self.buf[2:10], "big")
            idx = 10
        self._need(idx + ln)
        payload = bytes(self.buf[idx:idx + ln])
        del self.buf[:idx + ln]
        return op, payload


def send_pong(sock, payload):
    frame = bytes([0x8A, len(payload)]) + payload
    sock.sendall(frame)


def client_frame(payload):
    """RFC6455: client->server frames MUST be masked."""
    data = payload.encode() if isinstance(payload, str) else payload
    mask = os.urandom(4)
    ln = len(data)
    if ln < 126:
        head = bytes([0x81, 0x80 | ln])
    elif ln < 65536:
        head = bytes([0x81, 0x80 | 126]) + ln.to_bytes(2, "big")
    else:
        head = bytes([0x81, 0x80 | 127]) + ln.to_bytes(8, "big")
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return head + mask + masked


def rotate(dirpath, cur_name):
    files = sorted(f for f in os.listdir(dirpath) if f.endswith(".jsonl"))
    while len(files) >= MAX_FILES:
        os.remove(os.path.join(dirpath, files[0]))
        files.pop(0)


def main():
    coin = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    outdir = sys.argv[2] if len(sys.argv) > 2 else "hldata"
    os.makedirs(outdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    cur_name = "%s_%s.jsonl" % (coin.lower(), stamp)
    cur_path = os.path.join(outdir, cur_name)
    fp = open(cur_path, "a")
    size = os.path.getsize(cur_path)
    backoff = 1
    while True:
        try:
            sock, leftover = ws_connect(WS_HOST)
            fr = FrameReader(sock, leftover)
            sub = {"method": "subscribe",
                   "subscription": {"type": "l2Book", "coin": coin}}
            sock.sendall(client_frame(json.dumps(sub)))
            sub2 = {"method": "subscribe",
                    "subscription": {"type": "trades", "coin": coin}}
            sock.sendall(client_frame(json.dumps(sub2)))
            print("recording %s -> %s" % (coin, cur_path), flush=True)
            backoff = 1
            while True:
                op, payload = fr.read_frame()
                if op == 9:  # ping -> pong
                    send_pong(sock, payload)
                    continue
                if op == 8:
                    raise ConnectionError("server close")
                if op != 1:
                    continue
                try:
                    msg = json.loads(payload)
                except Exception:
                    continue
                ch = msg.get("channel")
                if ch not in ("l2Book", "trades"):
                    continue
                line = json.dumps({"ts": time.time(), "ch": ch,
                                   "d": msg.get("data")}, separators=(",", ":"))
                fp.write(line + "\n")
                size += len(line) + 1
                fp.flush()
                if size >= MAX_FILE_BYTES:
                    fp.close()
                    rotate(outdir, cur_name)
                    stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
                    cur_name = "%s_%s.jsonl" % (coin.lower(), stamp)
                    cur_path = os.path.join(outdir, cur_name)
                    fp = open(cur_path, "a")
                    size = 0
        except Exception as exc:
            print("reconnect in %ds: %s" % (backoff, exc), flush=True)
            try:
                fp.close()
            except Exception:
                pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
            stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
            cur_name = "%s_%s.jsonl" % (coin.lower(), stamp)
            cur_path = os.path.join(outdir, cur_name)
            fp = open(cur_path, "a")
            size = 0


if __name__ == "__main__":
    main()
