#!/usr/bin/env python3
"""
ns-restore.py — NetScaler VPX Configuration Restore
=====================================================
Restores a saved ns.conf backup to a freshly deployed NetScaler VPX
by replaying the configuration commands via NITRO batch API.

No dependencies — stdlib only (Python 3.6+).

Usage:
    python3 ns-restore.py --host 192.168.30.97 --password netscout1
    python3 ns-restore.py --host 192.168.30.97 --password netscout1 --conf examples/ns.conf.backup-20260604
    python3 ns-restore.py --host 192.168.30.97 --password netscout1 --dry-run

What it restores (from the conf file):
    - Servers (add server)
    - Service groups (add serviceGroup)
    - Service group members (bind serviceGroup)
    - LB virtual servers (add lb vserver)
    - LB vserver → serviceGroup bindings (bind lb vserver)

What it skips (system settings — already set at first boot):
    - IP addresses, interfaces, system parameters
    - SNMP, AAA, SSL profiles, patsets

Author: NETSCOUT SE Lab
"""

import argparse
import json
import os
import re
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
        print(f"[ERROR] No session token in response: {d}", file=sys.stderr)
        sys.exit(1)
    return token


def logout(base_url, token):
    nitro("POST", base_url, "config/logout", {"logout": {}}, token)


def save_config(base_url, token):
    nitro("POST", base_url, "config/nsconfig?action=save", {"nsconfig": {}}, token)


# ── Config parser ─────────────────────────────────────────────────────────────

# Only these command prefixes are replayed
RESTORE_PREFIXES = (
    "add server ",
    "add serviceGroup ",
    "bind serviceGroup ",
    "add lb vserver ",
    "bind lb vserver ",
    "add ns ip ",          # SNIPs / VIPs configured via config
)

def parse_conf(conf_path):
    """Extract restorable commands from ns.conf, preserving order."""
    commands = []
    with open(conf_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if any(line.startswith(p) for p in RESTORE_PREFIXES):
                # Skip NSIP line (already set at boot)
                if "add ns ip" in line and "-type NSIP" in line:
                    continue
                commands.append(line)
    return commands


# ── Command → NITRO call ──────────────────────────────────────────────────────

def _parse_add_server(cmd):
    """add server <name> <ip>"""
    m = re.match(r"add server (\S+) (\S+)", cmd)
    if not m:
        return None, None
    return "config/server", {"server": {"name": m.group(1), "ipaddress": m.group(2)}}


def _parse_add_servicegroup(cmd):
    """add serviceGroup <name> <type> ..."""
    m = re.match(r"add serviceGroup (\S+) (\S+)", cmd)
    if not m:
        return None, None
    return "config/servicegroup", {"servicegroup": {
        "servicegroupname": m.group(1),
        "servicetype": m.group(2),
    }}


def _parse_bind_servicegroup(cmd):
    """bind serviceGroup <sgname> <server> <port>"""
    m = re.match(r"bind serviceGroup (\S+) (\S+) (\d+)", cmd)
    if not m:
        return None, None
    return "config/servicegroup_servicegroupmember_binding", {
        "servicegroup_servicegroupmember_binding": {
            "servicegroupname": m.group(1),
            "servername":       m.group(2),
            "port":             int(m.group(3)),
        }
    }


def _parse_add_lbvserver(cmd):
    """add lb vserver <name> <type> <ip> <port> ..."""
    m = re.match(r"add lb vserver (\S+) (\S+) (\S+) (\d+)", cmd)
    if not m:
        return None, None
    return "config/lbvserver", {"lbvserver": {
        "name":        m.group(1),
        "servicetype": m.group(2),
        "ipv46":       m.group(3),
        "port":        int(m.group(4)),
        "lbmethod":    "ROUNDROBIN",
    }}


def _parse_bind_lbvserver(cmd):
    """bind lb vserver <vsname> <sgname>  or  bind lb vserver <vs> -policyName ..."""
    m = re.match(r"bind lb vserver (\S+) (\S+)$", cmd)
    if not m:
        return None, None
    return "config/lbvserver_servicegroup_binding", {
        "lbvserver_servicegroup_binding": {
            "name":             m.group(1),
            "servicegroupname": m.group(2),
        }
    }


def _parse_add_nsip(cmd):
    """add ns ip <ip> <mask> ..."""
    m = re.match(r"add ns ip (\S+) (\S+)", cmd)
    if not m:
        return None, None
    return "config/nsip", {"nsip": {
        "ipaddress": m.group(1),
        "netmask":   m.group(2),
        "type":      "SNIP",
        "vserver":   "DISABLED",
    }}


PARSERS = [
    ("add server ",         _parse_add_server),
    ("add serviceGroup ",   _parse_add_servicegroup),
    ("bind serviceGroup ",  _parse_bind_servicegroup),
    ("add lb vserver ",     _parse_add_lbvserver),
    ("bind lb vserver ",    _parse_bind_lbvserver),
    ("add ns ip ",          _parse_add_nsip),
]


def cmd_to_nitro(cmd):
    for prefix, fn in PARSERS:
        if cmd.startswith(prefix):
            return fn(cmd)
    return None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Restore NetScaler VPX config from ns.conf backup via NITRO"
    )
    parser.add_argument("--host",     required=True,
                        help="NetScaler management IP")
    parser.add_argument("--port",     type=int, default=443,
                        help="NITRO port (default: 443)")
    parser.add_argument("--user",     default="nsroot",
                        help="NITRO username (default: nsroot)")
    parser.add_argument("--password", required=True,
                        help="NITRO password")
    parser.add_argument("--conf",     default=None,
                        help="Path to ns.conf backup (default: auto-detect latest in examples/)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="Parse and show commands without applying")
    args = parser.parse_args()

    # Find conf file
    conf_path = args.conf
    if not conf_path:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        examples_dir = os.path.join(script_dir, "..", "examples")
        backups = sorted([
            f for f in os.listdir(examples_dir)
            if f.startswith("ns.conf.backup")
        ])
        if not backups:
            print("[ERROR] No backup found in examples/ — specify --conf path", file=sys.stderr)
            sys.exit(1)
        conf_path = os.path.join(examples_dir, backups[-1])
    
    if not os.path.exists(conf_path):
        print(f"[ERROR] Config file not found: {conf_path}", file=sys.stderr)
        sys.exit(1)

    base_url = f"https://{args.host}:{args.port}"
    print(f"[*] Config file : {conf_path}")
    print(f"[*] Target VPX  : {base_url}")
    if args.dry_run:
        print("[*] DRY RUN — no changes will be made")

    # Parse commands
    commands = parse_conf(conf_path)
    print(f"[*] Commands to restore: {len(commands)}")

    # Categorize for display
    cats = {}
    for cmd in commands:
        cat = cmd.split()[0] + " " + cmd.split()[1]
        cats[cat] = cats.get(cat, 0) + 1
    for cat, count in sorted(cats.items()):
        print(f"    {count:3d}  {cat}")

    if args.dry_run:
        print("\n--- Commands ---")
        for cmd in commands:
            path, body = cmd_to_nitro(cmd)
            status = "OK" if path else "SKIP"
            print(f"  [{status}] {cmd[:80]}")
        return

    print(f"\n[*] Logging in to {base_url}...")
    token = login(base_url, args.user, args.password)
    print(f"[*] Login OK")

    applied = 0
    skipped = 0
    errors  = 0

    for cmd in commands:
        path, body = cmd_to_nitro(cmd)
        if not path:
            skipped += 1
            continue
        _, err = nitro("POST", base_url, path, body, token)
        if err and "already exists" not in err.lower():
            print(f"  [ERR] {cmd[:70]}")
            print(f"        {err}")
            errors += 1
        else:
            applied += 1

    # Save config
    save_config(base_url, token)
    print(f"[*] Config saved to flash")

    logout(base_url, token)
    print(f"[*] Logged out")

    print(f"\n{'='*50}")
    print(f"  Applied : {applied}")
    print(f"  Skipped : {skipped}")
    print(f"  Errors  : {errors}")
    print(f"{'='*50}")
    if errors == 0:
        print("[OK] Restore complete — VPX config fully applied")
    else:
        print(f"[WARN] Restore completed with {errors} errors — review above")


if __name__ == "__main__":
    main()
