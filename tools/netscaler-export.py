#!/usr/bin/env python3
"""
netscaler-export.py — NetScaler VIP Configuration Exporter
===========================================================
Connects to a NetScaler VPX/CPX via NITRO REST API, collects all LB virtual
servers, service groups, and members, then saves the result as a YAML file
compatible with netscaler-vip-emulator-for-ng1 / mock-config.yml.

Usage:
    python3 netscaler-export.py --host 192.168.1.1 --user nsroot --password nsroot
    python3 netscaler-export.py --host 192.168.1.1 --user nsroot --password nsroot --ssl --port 443
    python3 netscaler-export.py --host 192.168.1.1 --user nsroot --password nsroot --out my-config.yml

Requirements:
    Python 3.6+  (no external packages needed — stdlib only)

Output:
    netscaler-export-<host>-<timestamp>.yml   (or --out path)
    Compatible directly with mock-config.yml for the emulator.

Author: NETSCOUT SE Lab
"""

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# ── NITRO filter: skip internal CPX/system entries ───────────────────────────
SKIP_PREFIXES = ("cpx_default_", "_NSSVC_", "_NS_", "ns_")

def _skip(name):
    return any(name.startswith(p) for p in SKIP_PREFIXES)


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _make_ctx(verify_ssl):
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def nitro_login(base_url, user, password, verify_ssl, timeout):
    url  = f"{base_url}/nitro/v1/config/login"
    body = json.dumps({"login": {"username": user, "password": password}}).encode()
    req  = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=_make_ctx(verify_ssl), timeout=timeout) as r:
            resp = json.loads(r.read())
            if resp.get("errorcode") == 0:
                return resp["sessionid"]
            print(f"[ERROR] NITRO login failed: {resp.get('message', 'unknown')}", file=sys.stderr)
            sys.exit(1)
    except urllib.error.URLError as e:
        print(f"[ERROR] Cannot reach {base_url}: {e}", file=sys.stderr)
        sys.exit(1)


def nitro_get(base_url, resource, sessid, verify_ssl, timeout, params=None):
    url = f"{base_url}/nitro/v1/config/{resource}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Cookie", f"sessionid={sessid}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=_make_ctx(verify_ssl), timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        body = e.read().decode()[:200]
        print(f"[WARN] NITRO GET {resource} HTTP {e.code}: {body}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"[WARN] NITRO GET {resource} error: {e}", file=sys.stderr)
        return {}


def nitro_get_nsip(base_url, sessid, verify_ssl, timeout):
    data = nitro_get(base_url, "nsip", sessid, verify_ssl, timeout)
    for entry in data.get("nsip", []):
        if entry.get("type") == "NSIP":
            return entry.get("ipaddress", "unknown")
    return "unknown"


# ── collection ────────────────────────────────────────────────────────────────

def collect(base_url, sessid, verify_ssl, timeout):
    print("[*] Fetching NSIP...")
    nsip = nitro_get_nsip(base_url, sessid, verify_ssl, timeout)
    print(f"    NSIP: {nsip}")

    # ── lbvservers ────────────────────────────────────────────────────────────
    print("[*] Fetching lbvservers...")
    raw_vs = nitro_get(base_url, "lbvserver", sessid, verify_ssl, timeout).get("lbvserver", [])
    raw_vs = [v for v in raw_vs if not _skip(v.get("name", ""))]
    print(f"    Found {len(raw_vs)} lbvserver(s)")

    # ── servicegroup binding per vserver ──────────────────────────────────────
    vs_to_sg = {}
    for vs in raw_vs:
        vsname = vs["name"]
        bindings = nitro_get(
            base_url, f"lbvserver_servicegroup_binding/{vsname}",
            sessid, verify_ssl, timeout
        ).get("lbvserver_servicegroup_binding", [])
        if bindings:
            vs_to_sg[vsname] = bindings[0]["servicegroupname"]

    # ── servicegroups ─────────────────────────────────────────────────────────
    print("[*] Fetching servicegroups...")
    raw_sg = nitro_get(base_url, "servicegroup", sessid, verify_ssl, timeout).get("servicegroup", [])
    raw_sg = [sg for sg in raw_sg if not _skip(sg.get("servicegroupname", ""))]
    print(f"    Found {len(raw_sg)} servicegroup(s)")

    # Build lbmethod lookup per bound servicegroup (vserver is authoritative)
    sg_lb = {}
    for vs in raw_vs:
        vsname  = vs["name"]
        lb_raw  = vs.get("lbmethod", "ROUNDROBIN")
        sg_name = vs_to_sg.get(vsname)
        if sg_name:
            sg_lb[sg_name] = lb_raw

    # ── members per servicegroup ──────────────────────────────────────────────
    print("[*] Fetching servicegroup members...")
    sg_members = {}
    for sg in raw_sg:
        sgname = sg["servicegroupname"]
        mbr_data = nitro_get(
            base_url, f"servicegroup_servicegroupmember_binding/{sgname}",
            sessid, verify_ssl, timeout
        ).get("servicegroup_servicegroupmember_binding", [])
        members = []
        for m in mbr_data:
            members.append({
                "servername": m.get("servername", m.get("ip", "")),
                "ip":         m.get("ip", ""),
                "port":       int(m.get("port", 80)),
                "svrstate":   (m.get("svrstate") or m.get("curstate") or "UNKNOWN").upper(),
            })
        sg_members[sgname] = members
        print(f"    {sgname}: {len(members)} member(s)")

    # ── assemble output ───────────────────────────────────────────────────────
    lbvservers = []
    for vs in raw_vs:
        vsname  = vs["name"]
        sg_name = vs_to_sg.get(vsname, f"{vsname}-sp")
        lbvservers.append({
            "name":         vsname,
            "ipv46":        vs.get("ipv46", "0.0.0.0"),
            "port":         int(vs.get("port", 80)),
            "servicetype":  vs.get("servicetype", "HTTP"),
            "curstate":     vs.get("curstate", "UNKNOWN").upper(),
            "lbmethod":     vs.get("lbmethod", "ROUNDROBIN").upper(),
            "comment":      vs.get("comment", ""),
            "servicegroup": sg_name,
        })

    servicegroups = []
    for sg in raw_sg:
        sgname = sg["servicegroupname"]
        servicegroups.append({
            "name":        sgname,
            "servicetype": sg.get("servicetype", "HTTP"),
            "lbmethod":    sg_lb.get(sgname, sg.get("lbmethod", "ROUNDROBIN")).upper(),
            "members":     sg_members.get(sgname, []),
        })

    return {
        "nsip":          nsip,
        "lbvservers":    lbvservers,
        "servicegroups": servicegroups,
    }


# ── serialisation ─────────────────────────────────────────────────────────────

def _to_yaml_str(data):
    """Produce YAML — uses PyYAML if available, hand-rolled fallback otherwise."""
    if HAS_YAML:
        return yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)

    lines = []
    lines.append(f"nsip: {data['nsip']}\n")

    lines.append("\nlbvservers:")
    for vs in data["lbvservers"]:
        lines.append(f"  - name: {vs['name']}")
        lines.append(f"    ipv46: {vs['ipv46']}")
        lines.append(f"    port: {vs['port']}")
        lines.append(f"    servicetype: {vs['servicetype']}")
        lines.append(f"    curstate: {vs['curstate']}")
        lines.append(f"    lbmethod: {vs['lbmethod']}")
        comment = vs['comment'].replace('"', '\\"')
        lines.append(f'    comment: "{comment}"')
        lines.append(f"    servicegroup: {vs['servicegroup']}")

    lines.append("\nservicegroups:")
    for sg in data["servicegroups"]:
        lines.append(f"  - name: {sg['name']}")
        lines.append(f"    servicetype: {sg['servicetype']}")
        lines.append(f"    lbmethod: {sg['lbmethod']}")
        if sg["members"]:
            lines.append("    members:")
            for m in sg["members"]:
                lines.append(f"      - servername: {m['servername']}")
                lines.append(f"        ip: {m['ip']}")
                lines.append(f"        port: {m['port']}")
                lines.append(f"        svrstate: {m['svrstate']}")
        else:
            lines.append("    members: []")

    return "\n".join(lines) + "\n"


def _to_json_str(data):
    return json.dumps(data, indent=2)


# ── summary ───────────────────────────────────────────────────────────────────

def print_summary(data):
    print("\n" + "="*60)
    print("  EXPORT SUMMARY")
    print("="*60)
    print(f"  NSIP          : {data['nsip']}")
    print(f"  LB vServers   : {len(data['lbvservers'])}")
    print(f"  Service Groups: {len(data['servicegroups'])}")
    total_members = sum(len(sg["members"]) for sg in data["servicegroups"])
    print(f"  Total Members : {total_members}")
    print()
    for vs in data["lbvservers"]:
        state     = vs["curstate"]
        sg        = vs["servicegroup"]
        mbr_count = next(
            (len(s["members"]) for s in data["servicegroups"] if s["name"] == sg), 0
        )
        print(f"  [{state:7s}]  {vs['name']:35s}  {vs['ipv46']}:{vs['port']}"
              f"  ->  {sg} ({mbr_count} members)")
    print("="*60)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Export NetScaler VIP config to YAML for netscaler-vip-emulator-for-ng1"
    )
    parser.add_argument("--host",      required=True,
                        help="NetScaler management IP or hostname")
    parser.add_argument("--port",      type=int, default=None,
                        help="NITRO port (default: 443 with --ssl, 80 without)")
    parser.add_argument("--user",      default="nsroot",
                        help="NITRO username (default: nsroot)")
    parser.add_argument("--password",  default="nsroot",
                        help="NITRO password (default: nsroot)")
    parser.add_argument("--ssl",       action="store_true",
                        help="Use HTTPS (recommended for VPX)")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip SSL certificate verification (self-signed certs)")
    parser.add_argument("--timeout",   type=int, default=15,
                        help="HTTP timeout in seconds (default: 15)")
    parser.add_argument("--out",       default=None,
                        help="Output file path (default: netscaler-export-<host>-<ts>.yml)")
    parser.add_argument("--format",    choices=["yaml", "json"], default="yaml",
                        help="Output format (default: yaml)")
    args = parser.parse_args()

    use_ssl    = args.ssl
    nitro_port = args.port or (443 if use_ssl else 80)
    proto      = "https" if use_ssl else "http"
    base_url   = f"{proto}://{args.host}:{nitro_port}"
    verify     = not args.no_verify

    ts       = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.out or f"netscaler-export-{args.host}-{ts}.{args.format}"

    print(f"[*] Connecting to {base_url} as {args.user}...")
    if use_ssl and not verify:
        print("[!] SSL verification disabled")

    sessid = nitro_login(base_url, args.user, args.password, verify, args.timeout)
    print(f"[*] Login OK (session={sessid[:8]}...)")

    data = collect(base_url, sessid, verify, args.timeout)

    print_summary(data)

    header = (
        f"# netscaler-export — {datetime.now().isoformat(timespec='seconds')}\n"
        f"# Source: {args.host}:{nitro_port}  NSIP: {data['nsip']}\n"
        f"# Compatible with: netscaler-vip-emulator-for-ng1 mock-config.yml\n"
        f"# To use: copy this file to mock-nitro/mock-config.yml and restart the emulator\n\n"
    )

    content = header + _to_yaml_str(data) if args.format == "yaml" else _to_json_str(data)

    with open(out_path, "w") as f:
        f.write(content)

    print(f"\n[OK] Saved: {out_path}")
    print(f"[OK] Drop into mock-nitro/mock-config.yml to load into the emulator")


if __name__ == "__main__":
    main()
