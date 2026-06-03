# ng1-f5-emulator-netscaler

F5 BIG-IP iControl REST API emulator for nGeniusONE BIG-IP Discovery — Citrix
NetScaler edition.

Allows nGeniusONE to discover and monitor virtual servers (VIPs) from a
Citrix NetScaler (CPX or VPX) by translating NITRO REST API responses into
F5 iControl REST format.

Sister project to [`ng1-f5-emulator-a10`](https://github.com/dmaffei/ng1-f5-emulator-a10).

## Architecture

```
nGeniusONE BIG-IP Discovery
    ↓  HTTPS  F5 iControl REST
ng1-f5-emulator-netscaler (Docker container, port 8446)
    ↓  HTTP(S) NITRO REST API
NetScaler CPX (Docker container) — or — NetScaler VPX (VM)
```

One container, one backend. Listens on port 8446.

## NITRO mapping

| NetScaler object | → | F5 BIG-IP object |
|---|---|---|
| `lbvserver`        | → | virtual server |
| `servicegroup`     | → | pool |
| `servicegroup_servicegroupmember_binding` | → | pool members |
| `nsip`             | → | device management IP |

## Quick Start

```bash
git clone https://github.com/dmaffei/ng1-f5-emulator-netscaler
cd ng1-f5-emulator-netscaler
bash install.sh
```

Installer walks through prerequisites, NetScaler connection details, TLS
cert generation, and container deployment.

## NetScaler target options

### CPX (NetScaler Express — free, Docker)

```bash
docker run -d --name netscaler-cpx --privileged \
  -e EULA=yes -p 9080:9080 -p 9443:9443 \
  quay.io/netscaler/netscaler-cpx:14.1-60.52
```

⚠️ Avoid `--network=host` and `CPX_NW_DEV=<host_primary_nic>` — under host
networking, CPX hijacks the host's primary interface namespace.

### VPX (NetScaler Virtual Edition — 90-day free trial)

Download `NSVPX-ESX-*.ova` or `NSVPX-KVM-*.tgz` from
[citrix.com/downloads](https://www.citrix.com/downloads/netscaler-adc/virtual-appliances/).

Default credentials: `nsroot` / `nsroot` (must change at first login).

NITRO HTTPS port: 443 (set `ssl: true` and `port: 443` in `backends/netscaler.yml`).

## nG1 Configuration

In nGeniusONE → Configuration → BIG-IP Discovery → Add:

| Field | Value |
|---|---|
| Name | `netscaler-proxy` |
| Host | `<shim-host-ip>` |
| Port | `8446` |
| Username | `admin` (from `.env` `TMOS_USERNAME`) |
| Password | `netscout1` (from `.env` `TMOS_PASSWORD`) |
| Login Provider | `tmos` |

## File Structure

```
ng1-f5-emulator-netscaler/
├── main.py                       # FastAPI server — F5 endpoints + auth
├── adapters/
│   └── netscaler_adapter.py      # NITRO REST adapter
├── core/
│   └── tmos_base.py              # F5 response shape helpers
├── backends/                     # Per-instance config (git-ignored)
│   └── netscaler.yml             # NetScaler credentials and host
├── docker-compose.yml            # tmos-shim-netscaler service (:8446)
├── Dockerfile
├── requirements.txt
├── install.sh                    # Interactive installer
├── .env-example
├── scripts/gen-cert.sh
└── examples/netscaler/           # Sample NS config and notes
```

## Repo

Private — NETSCOUT SE Lab
`github.com/dmaffei/ng1-f5-emulator-netscaler`
