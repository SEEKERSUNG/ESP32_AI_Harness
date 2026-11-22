"""Run from MicroPython raw REPL: mpremote connect <PORT> run tools/diagnose_deepseek.py"""

import gc
import json
import socket
import ssl
import time

from harness.config import Config
from harness.device import Network


config = Config()
network = Network()
print("STAGE wifi")
status = network.connect(config.secret("wifi_ssid"), config.secret("wifi_password"), 25)
print("WIFI", status)
print("STAGE ntp")
network.sync_time(config.get("ntp_server"))
print("NTP_OK")

print("STAGE dns")
started = time.ticks_ms()
info = socket.getaddrinfo("api.deepseek.com", 443)
print("DNS_MS", time.ticks_diff(time.ticks_ms(), started))
for entry in info:
    print("ADDR", entry[0], entry[4])
address = info[0][-1]
print("SELECTED", address)

print("STAGE tcp")
sock = socket.socket()
sock.settimeout(10)
started = time.ticks_ms()
sock.connect(address)
print("TCP_MS", time.ticks_diff(time.ticks_ms(), started))

print("STAGE tls")
context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.verify_mode = ssl.CERT_REQUIRED
context.load_verify_locations(cafile=config.get("ca_file"))
started = time.ticks_ms()
wrapped = context.wrap_socket(sock, server_hostname="api.deepseek.com")
print("TLS_MS", time.ticks_diff(time.ticks_ms(), started))

print("STAGE http")
payload = {
    "model": config.get("model"),
    "messages": [{"role": "user", "content": "Reply with OK only."}],
    "stream": False,
    "max_tokens": 16,
}
body = json.dumps(payload).encode()
lines = [
    "POST /chat/completions HTTP/1.1",
    "Host: api.deepseek.com",
    "Content-Length: " + str(len(body)),
    "Content-Type: application/json",
    "Accept: application/json",
    "Accept-Encoding: identity",
    "Connection: close",
    "Authorization: Bearer " + config.secret("api_key"),
]
request = ("\r\n".join(lines) + "\r\n\r\n").encode() + body
view = memoryview(request)
sent = 0
while sent < len(request):
    sent += wrapped.send(view[sent:])
print("SENT_BYTES", sent)
started = time.ticks_ms()
received = bytearray()
while True:
    try:
        chunk = wrapped.recv(1024)
    except OSError as exc:
        print("RECV_TIMEOUT", time.ticks_diff(time.ticks_ms(), started), exc)
        break
    print("RECV_CHUNK", len(chunk), time.ticks_diff(time.ticks_ms(), started))
    if not chunk:
        break
    received.extend(chunk)
    if len(received) > 32768:
        print("RESPONSE_TOO_LARGE")
        break
print("RESPONSE_BYTES", len(received), time.ticks_diff(time.ticks_ms(), started))
print(received[:2000].decode())
wrapped.close()
gc.collect()
print("DIAG_HTTP_OK", gc.mem_free())
