"""netscaler_adapter.py — NetScaler NITRO REST API -> F5 TMOS translation.

Reads NetScaler CPX/VPX via NITRO v1 API and maps:
  lbvserver        -> F5 virtual servers
  servicegroup     -> F5 pools
  servicegroup members -> F5 pool members

Auth: POST /nitro/v1/config/login -> sessionid cookie
"""

import logging, socket, urllib.request, urllib.error, json, urllib.parse
from core.tmos_base import TmosBase, virtual_server, pool, pool_member

logger = logging.getLogger(__name__)

LB_METHOD_MAP = {
    "ROUNDROBIN":       "round-robin",
    "LEASTCONNECTION":  "least-connections-node",
    "LEASTRESPONSETIME":"fastest-node",
    "URLHASH":          "uri",
    "DOMAINHASH":       "uri",
    "DESTINATIONIPHASH":"destination-address",
    "SOURCEIPHASH":     "source-address",
    "TOKEN":            "round-robin",
    "SRCIPDESTIPHASH":  "source-address",
    "SRCIPSRCPORTHASH": "source-address",
    "CALLIDHASH":       "round-robin",
    "CUSTOMLOAD":       "round-robin",
}


def _resolve(host):
    try:
        return socket.gethostbyname(host)
    except socket.gaierror:
        return host


class NetScalerAdapter(TmosBase):
    """
    Adapter that reads NetScaler CPX/VPX via NITRO REST API and exposes
    LB virtual servers as F5 virtual servers, service groups as F5 pools.
    """
    adapter_name = "netscaler-tmos-shim"
    f5_version   = "14.1.0"

    def __init__(self, nitro_url="http://localhost:9080", vip_host="localhost",
                 nitro_user="nsroot", nitro_pass="nsroot",
                 username="admin", password="admin", timeout=10, verify_ssl=True):
        super().__init__(username=username, password=password)
        self.nitro_url  = nitro_url.rstrip("/")
        self.vip_host   = vip_host
        self.nitro_user = nitro_user
        self.nitro_pass = nitro_pass
        self.timeout    = timeout
        self._sessid    = None
        self._ctx       = None
        if nitro_url.startswith("https"):
            import ssl as _ssl
            ctx = _ssl.SSLContext(_ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            ctx.set_ciphers("HIGH:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!MD5:!PSK")
            self._ctx = ctx
        logger.info(f"[netscaler] Adapter init — NITRO at {self.nitro_url}")

    # ------------------------------------------------------------------
    # NITRO session management
    # ------------------------------------------------------------------
    def _login(self):
        """Establish a NITRO session and cache the session ID."""
        url  = f"{self.nitro_url}/nitro/v1/config/login"
        body = json.dumps({"login": {"username": self.nitro_user,
                                     "password": self.nitro_pass}}).encode()
        req  = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout) as resp:
                r = json.loads(resp.read())
                if r.get("errorcode") == 0:
                    self._sessid = r["sessionid"]
                    logger.info("[netscaler] NITRO login OK")
                    return True
                logger.error(f"[netscaler] NITRO login failed: {r.get('message')}")
                return False
        except Exception as e:
            logger.error(f"[netscaler] NITRO login error: {e}")
            return False

    def _nitro_get(self, resource, params=None):
        """GET /nitro/v1/config/<resource> or /nitro/v1/stat/<resource>."""
        if not self._sessid and not self._login():
            return {}
        url = f"{self.nitro_url}/nitro/v1/config/{resource}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
        req.add_header("Cookie", f"sessionid={self._sessid}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # Session expired — re-login once
                logger.warning("[netscaler] Session expired, re-logging in")
                self._sessid = None
                if self._login():
                    return self._nitro_get(resource, params)
            body = e.read().decode()[:200]
            logger.error(f"[netscaler] NITRO GET {resource} HTTP {e.code}: {body}")
            return {}
        except Exception as e:
            logger.error(f"[netscaler] NITRO GET {resource} error: {e}")
            return {}

    def _nitro_stat(self, resource):
        """GET /nitro/v1/stat/<resource>."""
        if not self._sessid and not self._login():
            return {}
        url = f"{self.nitro_url}/nitro/v1/stat/{resource}"
        req = urllib.request.Request(url)
        req.add_header("Cookie", f"sessionid={self._sessid}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"[netscaler] NITRO STAT {resource} error: {e}")
            return {}

    # ------------------------------------------------------------------
    # TmosBase implementation
    # ------------------------------------------------------------------
    def _bulk_vs_sg_map(self):
        """Fetch all lbvserver→servicegroup bindings in one call (count field on lbvserver)."""
        # NetScaler NITRO supports ?attrs=name,servicegroupname on the binding endpoint
        # but not a global list — we use the lbvserver list which includes 'lbvstype' etc.
        # Fastest: re-use already-fetched lbvserver data and parse 'lbvserver' servicegroupname
        # field if present, otherwise fall back to per-VS call only for missing entries.
        vs_list = self._nitro_get("lbvserver").get("lbvserver", [])
        sg_map = {}
        missing = []
        for vs in vs_list:
            name = vs.get("name", "")
            # Some NITRO versions return servicegroupname inline on the lbvserver object
            inline_sg = vs.get("servicegroupname", "")
            if inline_sg:
                sg_map[name] = inline_sg
            else:
                missing.append(name)
        # For VIPs without inline binding, fetch all bindings in parallel-ish
        # using a single bulk binding call if NITRO supports it, else individual
        if missing:
            # Try bulk: GET lbvserver_servicegroup_binding (no vsname = all bindings)
            bulk = self._nitro_get("lbvserver_servicegroup_binding", params={"bulkbindings": "yes"}).get(
                "lbvserver_servicegroup_binding", [])
            if bulk:
                for b in bulk:
                    vn = b.get("name", "")
                    sg = b.get("servicegroupname", "")
                    if vn and sg:
                        sg_map[vn] = sg
            else:
                # Last resort: individual calls only for still-missing entries
                for name in missing:
                    if name not in sg_map:
                        bindings = self._nitro_get(
                            f"lbvserver_servicegroup_binding/{name}"
                        ).get("lbvserver_servicegroup_binding", [])
                        if bindings:
                            sg_map[name] = bindings[0]["servicegroupname"]
        return sg_map

    def get_virtuals(self):
        """Return list of F5 virtual_server dicts from NetScaler lbvservers."""
        data     = self._nitro_get("lbvserver")
        vservers = data.get("lbvserver", [])
        vip_ip   = _resolve(self.vip_host)
        virtuals = []

        # Filter out CPX internal system vservers (DNS, management)
        vservers = [v for v in vservers if not v.get('name','').startswith('cpx_default_')]

        # Bulk-fetch all VS→SG bindings in one shot instead of N serial calls
        sg_map = self._bulk_vs_sg_map()

        for vs in vservers:
            name    = vs.get("name", "")
            ip      = vs.get("ipv46", vip_ip)
            port    = str(vs.get("port", 80))
            state   = vs.get("curstate", "DOWN").upper() == "UP"
            comment = vs.get("comment", "")
            svctype = vs.get("servicetype", "HTTP").lower()
            proto   = "tcp" if svctype not in ("udp",) else "udp"

            pool_name = sg_map.get(name, f"{name}-pool")

            desc = comment or f"{name.replace('-', ' ').replace('_', ' ').title()} VIP"
            v = virtual_server(
                name        = name,
                destination = f"{ip}:{port}",
                pool        = pool_name,
                description = desc,
                protocol    = proto,
                enabled     = state,
            )
            v["netscalerServiceType"] = svctype
            virtuals.append(v)

        logger.info(f"[netscaler] Discovered {len(virtuals)} virtual server(s)")
        return {"kind": "tm:ltm:virtual:virtualcollectionstats", "items": virtuals}

    def get_pools(self):
        """Return list of F5 pool dicts from NetScaler servicegroups."""
        data   = self._nitro_get("servicegroup")
        groups = data.get("servicegroup", [])

        # Build vserver lb_method lookup — use bulk binding fetch (no N serial calls)
        vserver_lb = {}
        vs_data = self._nitro_get("lbvserver")
        sg_map  = self._bulk_vs_sg_map()
        for vs in vs_data.get("lbvserver", []):
            vsname = vs.get("name", "")
            lb_raw = vs.get("lbmethod", "ROUNDROBIN")
            sgname = sg_map.get(vsname, "")
            if sgname:
                vserver_lb[sgname] = lb_raw

        pools  = []

        # Filter out CPX internal system servicegroups
        groups = [g for g in groups if not g.get('servicegroupname','').startswith('cpx_default_')]
        for sg in groups:
            name      = sg.get("servicegroupname", "")
            # Prefer LB method from bound vserver (authoritative), fall back to SG
            lb_raw    = vserver_lb.get(name, sg.get("lbmethod", "ROUNDROBIN"))
            lb_method = LB_METHOD_MAP.get(lb_raw.upper(), "round-robin")
            svctype   = sg.get("servicetype", "HTTP").lower()
            monitor   = "https" if svctype in ("ssl", "https") else "http"
            comment   = sg.get("comment", "")

            # Skip per-SG member count call — count is fetched lazily on get_pool_members
            members = []

            p = pool(
                name        = name,
                description = comment or f"{name.replace('-',' ').replace('_',' ').title()} Pool",
                lb_method   = lb_method,
                monitor     = monitor,
            )
            p["memberCount"]    = len(members)
            p["netscalerSGName"] = name
            pools.append(p)

        logger.info(f"[netscaler] Discovered {len(pools)} pool(s)")
        return {"kind": "tm:ltm:pool:poolcollectionstats", "items": pools}

    def get_pool_members(self, pool_name):
        """Return list of F5 pool_member dicts from NetScaler servicegroup members."""
        data    = self._nitro_get(
            f"servicegroup_servicegroupmember_binding/{pool_name}"
        )
        entries = data.get("servicegroup_servicegroupmember_binding", [])
        members = []

        for m in entries:
            # Member can be IP-based or server-name-based
            ip      = m.get("ip") or _resolve(m.get("servername", ""))
            port    = str(m.get("port", 80))
            state   = (m.get("svrstate") or m.get("curstate") or "DOWN").upper()
            f5state = "up" if state in ("UP", "ENABLED") else "down"
            addr    = f"{ip}:{port}"
            members.append(pool_member(address=addr, name=addr, state=f5state))

        # nG1 requires at least 2 members (JSONArray[1] guard)
        if len(members) == 1:
            orig = members[0]
            members.append(pool_member(address=orig["name"], name=orig["name"], state="down"))
        elif len(members) == 0:
            logger.warning(f"[netscaler] No members for pool '{pool_name}'")

        logger.info(f"[netscaler] Pool {pool_name}: {len(members)} member(s)")
        return {"kind": "tm:ltm:pool:members:memberscollectionstats", "items": members}
