#!/usr/bin/env python3
"""Check connectivity to an AC VRF controller and list its units.

Talks to the controller the same way the integration does: a plain TCP socket,
because the controller answers with HTTP/0.9-style responses that most HTTP
clients reject. Needs only the Python standard library.

Usage:
    python3 test_ac_vrf_connection.py 192.168.1.251
    python3 test_ac_vrf_connection.py 192.168.1.251 --username admin --password ""
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys

STATUS_PAGES = (0, 1)
TIMEOUT = 10


def fetch(host: str, path: str, username: str | None, password: str) -> str:
    """Send a GET request over a raw socket and return the response text."""
    hostname, _, port = host.partition(":")
    request = f"GET {path} HTTP/1.0\r\nHost: {hostname}\r\n"
    if username:
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        request += f"Authorization: Basic {creds}\r\n"
    request += "Connection: close\r\n\r\n"

    with socket.create_connection((hostname, int(port or 80)), timeout=TIMEOUT) as sock:
        sock.sendall(request.encode())
        response = b""
        while chunk := sock.recv(4096):
            response += chunk
    return response.decode("utf-8", errors="ignore")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="controller IP address (or ip:port)")
    parser.add_argument("--username", help="HTTP Basic Auth username")
    parser.add_argument("--password", default="", help="HTTP Basic Auth password (default: empty)")
    args = parser.parse_args()

    print(f"Testing AC VRF controller at {args.host}")
    print(f"Authentication: {'yes, user ' + args.username if args.username else 'no'}")
    print("-" * 60)

    total = 0
    ok = True
    for page in STATUS_PAGES:
        path = f"/cgi-bin/api.html?f=17&p={page}"
        print(f"\nPage {page}: http://{args.host}{path}")
        try:
            text = fetch(args.host, path, args.username, args.password)
        except OSError as err:
            print(f"  ❌ Connection failed: {err}")
            ok = False
            continue

        if not text.strip():
            print("  ❌ Empty response (controller closed the connection without replying)")
            ok = False
            continue

        start = text.find("{")
        if start < 0:
            print("  ❌ No JSON in response (wrong credentials or not a VRF controller?)")
            print(f"  Raw response: {text[:300]!r}")
            ok = False
            continue
        try:
            data, end = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError as err:
            print(f"  ❌ Invalid JSON: {err}")
            print(f"  Raw response: {text[:300]!r}")
            ok = False
            continue

        trailing = text[start + end:].strip()
        if trailing:
            print(f"  (ignored {len(trailing)} bytes after the JSON; normal for this controller)")

        if data.get("err") != 0:
            print(f"  ⚠️  Controller returned error code {data.get('err')!r}")
            ok = False
            continue

        units = data.get("unit", [])
        total += len(units)
        print(f"  ✅ {len(units)} unit(s)")
        for unit in units:
            print(
                f"     - {unit.get('nm')!s:<15} idx={unit.get('idx')} on={unit.get('on')} "
                f"mode={unit.get('mode')} set={unit.get('tempSet')}°C room={unit.get('tempIn')}°C "
                f"fan={unit.get('fan')}"
            )

    print("\n" + "=" * 60)
    print(f"{'✅' if ok else '⚠️ '} Found {total} unit(s) in total")
    return 0 if ok and total else 1


if __name__ == "__main__":
    sys.exit(main())
