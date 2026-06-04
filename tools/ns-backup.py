#!/usr/bin/env python3
"""
ns-backup.py — NetScaler VPX Configuration Backup
===================================================
Downloads ns.conf from a live NetScaler VPX via NITRO and saves it locally.
Run this after any configuration change to keep the backup current.

No dependencies — stdlib only (Python 3.6+).

Usage:
    python3 ns-backup.py --host 192.168.30.97 --password netscout1
    python3 ns-backup.py --host 192.168.30.97 --password netscout1 --out /tmp/my-vpx.conf
    python3 ns-backup.py --host 192.168.30.97 --password netscout1 --no-save-flash

Author: NETSCOUT SE Lab
"""

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime

# ── SSL context ───────────────────────────────────────────────────────────────

def _ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("HIGH:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK")
    return ctx

CTX = _ctx()

# ── NITRO helpers ─────────────────────────────────────────────────────────────

def nitro(method, base_url, path, body=None, token=None):
    url = f"{base_url}/nitro/v1/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Cookie", f"sessionid={token}")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=15) as r:
            txt = r.read()
            return json.loads(txt) if txt.strip() else {}, None
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", "?")
        except Exception:
            msg = str(e)
        return {}, f"HTTP {e.code}: {msg}"
    except Exception as e:
        return {}, str(e)


def login(base_url, user, password):
    d, err = nitro("POST", base_url, "config/login",
                   {"login": {"username": user, "password": password}})
    if err:
        print(f"[ERROR] Login failed: {err}", file=sys.stderr)
        sys.exit(1)
    token = d.get("sessionid", "")
    if not token:
        print(f"[ERROR] No session token: {d}", file=sys.stderr)
        sys.exit(1)
    return token


def logout(base_url, token):
    nitro("POST", base_url, "config/logout", {"logout": {}}, token)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Backup NetScaler VPX ns.conf via NITRO"
    )
    parser.add_argument("--host",          required=True,
                        help="NetScaler management IP or hostname")
    parser.add_argument("--port",          type=int, default=443,
                        help="NITRO port (default: 443)")
    parser.add_argument("--user",          default="nsroot",
                        help="NITRO username (default: nsroot)")
    parser.add_argument("--password",      required=True,
                        help="NITRO password")
    parser.add_argument("--out",           default=None,
                        help="Output file path (default: examples/ns.conf.backup-YYYYMMDD)")
    parser.add_argument("--no-save-flash", action="store_true",
                        help="Skip saving running config to flash before backup")
    args = parser.parse_args()

    base_url = f"https://{args.host}:{args.port}"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Default output path — alongside this script in ../examples/
    if args.out:
        out_path = args.out
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        examples_dir = os.path.join(script_dir, "..", "examples")
        os.makedirs(examples_dir, exist_ok=True)
        out_path = os.path.join(examples_dir, f"ns.conf.backup-{ts[:8]}")

    print(f"[*] Connecting to {base_url} as {args.user}...")
    token = login(base_url, args.user, args.password)
    print(f"[*] Login OK")

    # Save running config to flash first (unless skipped)
    if not args.no_save_flash:
        _, err = nitro("POST", base_url, "config/nsconfig?action=save",
                       {"nsconfig": {}}, token)
        if err:
            print(f"[WARN] Save to flash failed: {err} — backing up current flash anyway")
        else:
            print(f"[*] Running config saved to flash")

    # Download ns.conf
    print(f"[*] Downloading ns.conf...")
    d, err = nitro("GET", base_url,
                   "config/systemfile?args=filename:ns.conf,filelocation:%2Fnsconfig",
                   token=token)
    if err:
        print(f"[ERROR] Failed to download ns.conf: {err}", file=sys.stderr)
        logout(base_url, token)
        sys.exit(1)

    files = d.get("systemfile", [])
    if not files:
        print(f"[ERROR] ns.conf not found in response: {d}", file=sys.stderr)
        logout(base_url, token)
        sys.exit(1)

    content = base64.b64decode(files[0]["filecontent"]).decode("utf-8", "ignore")

    logout(base_url, token)
    print(f"[*] Logged out")

    # Write backup
    with open(out_path, "w") as f:
        f.write(content)

    # Summary
    vips    = content.count("add lb vserver ")
    sgs     = content.count("add serviceGroup ")
    servers = content.count("add server ")
    size_kb = len(content) / 1024

    print(f"\n{'='*55}")
    print(f"  Backup saved : {out_path}")
    print(f"  Size         : {size_kb:.1f} KB")
    print(f"  LB vServers  : {vips}")
    print(f"  Service groups: {sgs}")
    print(f"  Servers      : {servers}")
    print(f"  Timestamp    : {datetime.now().isoformat(timespec='seconds')}")
    print(f"{'='*55}")
    print(f"\n[OK] Restore with: python3 ns-restore.py --host {args.host} --password <pw>")


if __name__ == "__main__":
    main()
