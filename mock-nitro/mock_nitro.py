"""
mock_nitro.py — netscaler-vip-emulator-for-ng1

Minimal NITRO v1 API mock. Serves canned responses from mock-config.yml
so the netscaler_adapter and tmos-shim can be tested without a real VPX.

Endpoints:
  POST /nitro/v1/config/login
  GET  /nitro/v1/config/lbvserver
  GET  /nitro/v1/config/lbvserver_servicegroup_binding/<vsname>
  GET  /nitro/v1/config/servicegroup
  GET  /nitro/v1/config/servicegroup_servicegroupmember_binding/<sgname>
  GET  /nitro/v1/config/nsip
  GET  /health
"""

import logging, os, time, uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s — %(message)s")
log = logging.getLogger("mock-nitro")

CONFIG_FILE = os.environ.get("CONFIG_FILE", "mock-config.yml")
SESSION_TTL = 3600

app = FastAPI(title="netscaler-vip-emulator-for-ng1", version="1.0.0")

# ── config ────────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

_config_cache = None
_config_mtime = 0.0

def cfg():
    global _config_cache, _config_mtime
    import os
    mtime = os.path.getmtime(CONFIG_FILE)
    if _config_cache is None or mtime != _config_mtime:
        _config_cache = load_config()
        _config_mtime = mtime
        log.info("Config reloaded — %d vservers, %d servicegroups",
                 len(_config_cache.get("lbvservers", [])),
                 len(_config_cache.get("servicegroups", [])))
    return _config_cache

# ── session store ─────────────────────────────────────────────────────────────

_sessions: dict = {}

def _new_session() -> str:
    sid = uuid.uuid4().hex
    _sessions[sid] = time.time()
    return sid

def _valid(sid: str) -> bool:
    t = _sessions.get(sid)
    if t is None:
        return False
    if time.time() - t > SESSION_TTL:
        del _sessions[sid]
        return False
    return True

def _check_auth(request: Request):
    sid = request.cookies.get("sessionid") or request.headers.get("Cookie", "")
    # extract sessionid= from Cookie header string if needed
    if "sessionid=" in sid:
        sid = dict(p.split("=", 1) for p in sid.split(";") if "=" in p).get("sessionid", "")
    if not _valid(sid):
        raise HTTPException(401, detail={"errorcode": 444, "message": "No session or session expired", "severity": "ERROR"})

# ── login ─────────────────────────────────────────────────────────────────────

@app.post("/nitro/v1/config/login")
async def login(request: Request):
    body = await request.json()
    creds = body.get("login", {})
    username = creds.get("username", "")
    password = creds.get("password", "")
    # Accept any non-empty credentials for the mock
    if not username or not password:
        return JSONResponse(
            status_code=401,
            content={"errorcode": 444, "message": "Invalid credentials", "severity": "ERROR"}
        )
    sid = _new_session()
    log.info("Login OK — user=%s session=%s...", username, sid[:8])
    response = JSONResponse(content={
        "errorcode": 0,
        "message": "Done",
        "severity": "NONE",
        "sessionid": sid,
    })
    response.set_cookie("sessionid", sid, httponly=True)
    return response

# ── lbvserver ─────────────────────────────────────────────────────────────────

@app.get("/nitro/v1/config/lbvserver")
def get_lbvservers(request: Request):
    _check_auth(request)
    data = cfg()
    items = []
    for vs in data.get("lbvservers", []):
        items.append({
            "name":        vs["name"],
            "ipv46":       vs.get("ipv46", "0.0.0.0"),
            "port":        vs.get("port", 80),
            "servicetype": vs.get("servicetype", "HTTP"),
            "curstate":    vs.get("curstate", "UP"),
            "lbmethod":    vs.get("lbmethod", "ROUNDROBIN"),
            "comment":     vs.get("comment", ""),
            "td":          "0",
            "hits":        "0",
            "statechangetimesec": "0",
            "type":        "ADDRESS",
        })
    log.info("GET lbvserver — returning %d entries", len(items))
    return {"errorcode": 0, "message": "Done", "severity": "NONE", "lbvserver": items}


@app.get("/nitro/v1/config/lbvserver_servicegroup_binding/{vsname}")
def get_lbvserver_sg_binding(vsname: str, request: Request):
    _check_auth(request)
    data = cfg()
    bindings = []
    for vs in data.get("lbvservers", []):
        if vs["name"] == vsname and vs.get("servicegroup"):
            bindings.append({
                "name":             vsname,
                "servicegroupname": vs["servicegroup"],
                "weight":           "1",
            })
    log.info("GET lbvserver_servicegroup_binding/%s — %d bindings", vsname, len(bindings))
    return {"errorcode": 0, "message": "Done", "severity": "NONE",
            "lbvserver_servicegroup_binding": bindings}

# ── servicegroup ──────────────────────────────────────────────────────────────

@app.get("/nitro/v1/config/servicegroup")
def get_servicegroups(request: Request):
    _check_auth(request)
    data = cfg()
    items = []
    for sg in data.get("servicegroups", []):
        items.append({
            "servicegroupname": sg["name"],
            "servicetype":      sg.get("servicetype", "HTTP"),
            "curstate":         "ENABLED",
            "lbmethod":         "ROUNDROBIN",
            "comment":          sg.get("comment", ""),
            "numofconnections":  "0",
        })
    log.info("GET servicegroup — returning %d entries", len(items))
    return {"errorcode": 0, "message": "Done", "severity": "NONE", "servicegroup": items}


@app.get("/nitro/v1/config/servicegroup_servicegroupmember_binding/{sgname}")
def get_sg_members(sgname: str, request: Request):
    _check_auth(request)
    data = cfg()
    members = []
    for sg in data.get("servicegroups", []):
        if sg["name"] == sgname:
            for m in sg.get("members", []):
                members.append({
                    "servicegroupname": sgname,
                    "servername":       m.get("servername", ""),
                    "ip":               m.get("ip", ""),
                    "port":             m.get("port", 80),
                    "svrstate":         m.get("svrstate", "UP"),
                    "curstate":         m.get("svrstate", "UP"),
                    "weight":           "1",
                    "delay":            "0",
                    "statechangetimesec": "0",
                })
            break
    log.info("GET sg_members/%s — %d members", sgname, len(members))
    return {"errorcode": 0, "message": "Done", "severity": "NONE",
            "servicegroup_servicegroupmember_binding": members}

# ── nsip (device identity) ────────────────────────────────────────────────────

@app.get("/nitro/v1/config/nsip")
def get_nsip(request: Request):
    _check_auth(request)
    data = cfg()
    nsip = data.get("nsip", "192.168.30.150")
    return {"errorcode": 0, "message": "Done", "severity": "NONE",
            "nsip": [{"ipaddress": nsip, "type": "NSIP", "netmask": "255.255.255.0",
                      "flags": "ENABLED", "hostroute": "DISABLED"}]}

# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    data = cfg()
    return {
        "status":      "ok",
        "vservers":    len(data.get("lbvservers", [])),
        "servicegroups": len(data.get("servicegroups", [])),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 9080))
    log.info("netscaler-vip-emulator-for-ng1 starting on port %d", port)
    uvicorn.run("mock_nitro:app", host="0.0.0.0", port=port, log_level="info")


@app.get("/nitro/v1/config/lbvserver_servicegroup_binding")
def get_all_lbvserver_sg_bindings(request: Request, bulkbindings: str = None):
    """Bulk endpoint — returns ALL VS→SG bindings in one call (requires bulkbindings=yes)."""
    _check_auth(request)
    if bulkbindings != "yes":
        return {"errorcode": 1238, "message": "No such resource", "severity": "ERROR"}
    data = cfg()
    bindings = []
    for vs in data.get("lbvservers", []):
        if vs.get("servicegroup"):
            bindings.append({
                "name":             vs["name"],
                "servicegroupname": vs["servicegroup"],
                "weight":           "1",
            })
    log.info("GET lbvserver_servicegroup_binding (bulk) — %d bindings", len(bindings))
    return {"errorcode": 0, "message": "Done", "severity": "NONE",
            "lbvserver_servicegroup_binding": bindings}
