# netscaler-export.py — Usage Guide

Export VIP configuration from a NetScaler VPX or CPX appliance to a YAML
file compatible with the ng1-f5-emulator-netscaler mock emulator.

---

## Requirements

- Python 3.6+ — no external packages (stdlib only)
- Network access to the NetScaler management IP on port 443 (HTTPS)
- An account with at least read access to the NITRO API

---

## Basic Commands

**VPX with HTTPS (self-signed cert — most common):**
```bash
python3 netscaler-export.py --host 192.168.30.97 --ssl --no-verify \
  --user nsroot --password netscout1
```

**VPX with explicit port:**
```bash
python3 netscaler-export.py --host 192.168.1.10 --port 443 --ssl --no-verify \
  --user nsroot --password mypassword
```

**CPX (Docker — HTTP on port 9080):**
```bash
python3 netscaler-export.py --host localhost --port 9080 \
  --user nsroot --password nsroot
```

**Specify output file:**
```bash
python3 netscaler-export.py --host 192.168.30.97 --ssl --no-verify \
  --user nsroot --password netscout1 --out customer-netscaler.yml
```

**JSON output:**
```bash
python3 netscaler-export.py --host 192.168.30.97 --ssl --no-verify \
  --user nsroot --password netscout1 --format json
```

**Windows (Command Prompt):**
```cmd
python netscaler-export.py --host 192.168.30.97 --ssl --no-verify ^
  --user nsroot --password netscout1
```

---

## All Options

| Option | Default | Description |
|---|---|---|
| `--host` | required | NetScaler management IP or hostname |
| `--port` | 80 | NITRO API port (use 443 for HTTPS VPX) |
| `--user` | nsroot | NITRO username |
| `--password` | nsroot | NITRO password |
| `--ssl` | off | Use HTTPS instead of HTTP |
| `--no-verify` | off | Skip SSL certificate verification (self-signed) |
| `--timeout` | 15 | HTTP timeout in seconds |
| `--out` | auto | Output file path |
| `--format` | yaml | Output format: yaml or json |

> **Note:** For any NetScaler VPX always use `--ssl --no-verify` together.
> The `--no-verify` flag alone does not switch to HTTPS.

---

## What the Script Collects

Read-only NITRO calls — nothing is modified on the device:

| NITRO Endpoint | What it collects |
|---|---|
| `POST /nitro/v1/config/login` | Session token |
| `GET /nitro/v1/config/nsip` | NSIP (management IP) |
| `GET /nitro/v1/config/lbvserver` | All LB virtual servers |
| `GET /nitro/v1/config/lbvserver_servicegroup_binding?bulkbindings=yes` | VS→pool bindings (bulk) |
| `GET /nitro/v1/config/servicegroup` | All service groups |
| `GET /nitro/v1/config/servicegroup_servicegroupmember_binding?bulkbindings=yes` | Members (bulk) |
| `POST /nitro/v1/config/logout` | Session cleanup |

Total: **4 data calls** regardless of VIP count (bulk fetch).

---

## Output File Format

The script produces a YAML file compatible with `mock-nitro/mock-config.yml`:

```yaml
# netscaler-export — 2026-06-04T17:00:00
# Source: 192.168.30.97:443  NSIP: 192.168.30.97

nsip: 192.168.30.97
lbvservers:
  - name: vip-WEB-HTTP
    ipv46: 192.168.30.200
    port: 80
    servicetype: HTTP
    curstate: DOWN
    lbmethod: ROUNDROBIN
    servicegroup: sg-WEB-HTTP

servicegroups:
  - servicegroupname: sg-WEB-HTTP
    servicetype: HTTP
    members:
      - servername: web-srv-01
        serverip: 192.168.30.101
        port: 80
        state: ENABLED
```

---

## Using the Output with the Emulator

1. Run the export and save the `.yml` file.
2. Copy to the emulator host:
```bash
cp ns-export-192.168.30.97-*.yml \
  /home/installer/Projects/ng1-f5-emulator-netscaler/mock-nitro/mock-config.yml
```
3. Restart the mock-nitro container (hot-reload happens automatically via file watch).
4. nG1 BIG-IP Discovery rediscovers the customer VIP topology.

---

## Permissions — Read-Only Account

Recommended: create a dedicated read-only account instead of using `nsroot`.

**Via NetScaler CLI:**
```
add system user netscout-ro <password>
bind system user netscout-ro read-only 100
```

Then use `--user netscout-ro --password <password>`.

---

## Troubleshooting

**SSL handshake failure:**
Always use both `--ssl` and `--no-verify` together for VPX with self-signed certs.

**HTTP 401 / Invalid credentials:**
Verify username/password. Note: after a VPX reboot the password reverts to
`nsroot` only if it was never saved. Run `save ns config` after changing password.

**0 VIPs returned:**
Check that LB virtual servers exist: `show lb vserver` in the NetScaler CLI.
Also verify the NITRO session is authenticated (errorcode 0 on login).

**`Input/output error` (errorcode 261):**
The NetScaler packet engine (nsppe) is not running. This happens when:
- The VPX just rebooted — wait 60s and retry
- Less than 2 vCPUs assigned
- License not activated (VPX freemium requires GUI first-boot acceptance)

**Python not found on Windows:**
Download Python 3.x from https://python.org — check "Add to PATH" during install.
