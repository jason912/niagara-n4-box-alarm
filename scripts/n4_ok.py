#!/usr/bin/env python3
"""Niagara N4 alarm reader — BOX protocol over plain HTTP.

Dependency-free reference implementation. Standard library only.

    python3 n4_ok.py 20
    python3 n4_ok.py --json
    NIAGARA_HOST=192.168.1.10 NIAGARA_USER=admin NIAGARA_PASS=secret python3 n4_ok.py 5

Important, learned the hard way:

    The station ignores the `l` (limit) and `o` (offset) arguments of
    queryAlarmDatabase. Every request returns the whole alarm database.
    The client-side limit below only trims what is printed.

    Edge10-based stations have a broken HTTP keep-alive. A reused connection
    gets reset mid-SCRAM. Every request uses a fresh connection.
"""

import base64
import hashlib
import hmac
import http.client
import json
import os
import sys
import time

HOST = os.environ.get("NIAGARA_HOST", "192.168.1.10")
PORT = int(os.environ.get("NIAGARA_PORT", "80"))
USER = os.environ.get("NIAGARA_USER", "admin")
PWD = os.environ.get("NIAGARA_PASS", "")


class N4:
    def __init__(self, host=HOST, port=PORT, user=USER, pwd=PWD, timeout=25):
        self.host, self.port = host, port
        self.user, self.pwd = user, pwd
        self.timeout = timeout
        self.jar = {}
        self.ssid = None
        self.fid = 0

    # ---- HTTP: one connection per request, always closed ----
    def _cookie(self):
        return "; ".join("%s=%s" % (k, v) for k, v in self.jar.items())

    def _absorb(self, headers):
        for k, v in headers:
            if k.lower() != "set-cookie":
                continue
            name, _, val = v.split(";")[0].partition("=")
            if "Max-Age=0" in v or val == "":
                self.jar.pop(name, None)
            else:
                self.jar[name] = val

    def http(self, method, path, body=None, ctype=None):
        c = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            h = {}
            if self.jar:
                h["Cookie"] = self._cookie()
            if ctype:
                h["Content-Type"] = ctype
            c.request(method, path, body, headers=h)
            r = c.getresponse()
            data = r.read()
            self._absorb(r.getheaders())
            return r.status, data
        finally:
            c.close()

    # ---- SCRAM-SHA512 ----
    @staticmethod
    def _parse(msg):
        out = {}
        for part in msg.split(","):
            i = part.find("=")
            if i > 0:
                out[part[:i]] = part[i + 1:]
        return out

    def login(self, gap=0.6):
        self.jar.clear()

        st, _ = self.http("GET", "/prelogin")
        if st != 200:
            raise RuntimeError("prelogin failed: HTTP %s" % st)

        nonce = base64.b64encode(os.urandom(16)).decode()
        first_bare = "n=%s,r=%s" % (self.user, nonce)

        st, srv1 = self.http(
            "POST", "/j_security_check/",
            "action=sendClientFirstMessage&clientFirstMessage=n,,%s" % first_bare,
            "application/x-niagara-login-support")
        if st != 200:
            raise RuntimeError("SCRAM step 1: HTTP %s %s"
                               % (st, srv1[:150].decode("utf-8", "replace")))
        srv1s = srv1.decode()
        p = self._parse(srv1s)

        salted = hashlib.pbkdf2_hmac(
            "sha256", self.pwd.encode(), base64.b64decode(p["s"]), int(p["i"]), 32)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()

        cfwp = "c=biws,r=%s" % p["r"]
        auth_msg = "%s,%s,%s" % (first_bare, srv1s, cfwp)
        client_sig = hmac.new(stored_key, auth_msg.encode(), hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, client_sig))

        time.sleep(gap)

        st, srv2 = self.http(
            "POST", "/j_security_check/",
            "action=sendClientFinalMessage&clientFinalMessage=%s,p=%s"
            % (cfwp, base64.b64encode(proof).decode()),
            "application/x-niagara-login-support")
        if st != 200:
            raise RuntimeError("SCRAM step 2: HTTP %s %s"
                               % (st, srv2[:150].decode("utf-8", "replace")))

        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        expect = base64.b64encode(
            hmac.new(server_key, auth_msg.encode(), hashlib.sha256).digest()).decode()
        if self._parse(srv2.decode()).get("v") != expect:
            raise RuntimeError("server signature mismatch")

        st, _ = self.http("GET", "/j_security_check/")
        if "JSESSIONID" not in self.jar:
            raise RuntimeError("login failed: no JSESSIONID issued")
        return True

    # ---- BOX ----
    def box(self, msgs, use_id=True):
        self.fid += 1
        body = {"p": "box", "v": "2.3", "m": msgs, "n": self.fid}
        if use_id and self.ssid:
            body["id"] = self.ssid
        st, d = self.http("POST", "/box/", json.dumps(body), "application/json")
        if st != 200:
            raise RuntimeError("box HTTP %s: %s"
                               % (st, d[:200].decode("utf-8", "replace")))
        return json.loads(d.decode("utf-8", "replace"))

    def session(self):
        r = self.box([{"r": 1, "t": "rt", "c": "ssession", "k": "make", "b": {}}],
                     use_id=False)
        m = r["m"][0]
        if m.get("t") == "e":
            raise RuntimeError("session: %s" % (m.get("b", {}).get("m"),))
        self.ssid = m["b"]
        return self.ssid

    def alarms(self, limit=None, offset=0):
        """Fetch the alarm database.

        The station ignores limit/offset, so this always transfers everything.
        Trimming happens client-side and does not reduce station load.
        """
        r = self.box([{"r": 1, "t": "rt", "c": "alarm", "k": "queryAlarmDatabase",
                       "b": {"t": "alarm:AlarmRecord", "l": 0, "o": 0,
                             "d": False, "c": True, "a": False}}])
        m = r["m"][0]
        if m.get("t") == "e":
            raise RuntimeError("alarm: %s" % (m.get("b", {}).get("m"),))
        data = m.get("b") or {}
        recs = data.get("r") or []
        recs = recs[max(0, offset or 0):]
        if limit is not None:
            recs = recs[:limit]
        data["r"] = recs
        data["total"] = data.get("c")
        data["returned"] = len(recs)
        return data


# ---- decoding helpers ----

def unescape_ord(s):
    """Undo Baja $XX escaping."""
    if not isinstance(s, str):
        return s
    out = []
    i = 0
    while i < len(s):
        if s[i] == "$" and i + 2 < len(s):
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
                continue
            except ValueError:
                pass
        out.append(s[i])
        i += 1
    return "".join(out)


def decode_typed(v):
    """Decode a typed facet value such as 's:foo' or 'i:42'."""
    if not isinstance(v, str) or ":" not in v:
        return v
    prefix, _, rest = v.partition(":")
    if prefix == "b":
        return rest.lower() == "true"
    if prefix == "i":
        try:
            return int(rest)
        except ValueError:
            return rest
    if prefix == "r":
        try:
            return float(rest)
        except ValueError:
            return rest
    return rest


def parse_alarm_data(raw):
    """Split a pipe-delimited facet string into a dict."""
    out = {}
    for part in (raw or "").split("|"):
        k, sep, v = part.partition("=")
        if sep:
            out[k] = decode_typed(v)
    return out


def flatten(rec):
    """Turn a record into a flat dict keyed by field name."""
    out = {}
    for c in rec.get("s", []):
        name = c.get("n")
        val = c.get("v")
        if val is None:
            val = c.get("d")
        out[name] = val
    if "source" in out:
        out["source"] = unescape_ord(out["source"])
    if "alarmData" in out:
        out["alarmData"] = parse_alarm_data(out["alarmData"])
    return out


def main(argv):
    limit = None
    offset = 0
    as_json = False

    args = argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__)
            return 0
        if a == "--json":
            as_json = True
        elif a == "--offset":
            i += 1
            offset = int(args[i])
        elif a.isdigit():
            limit = int(a)
        else:
            print("unknown argument: %s" % a, file=sys.stderr)
            return 2
        i += 1

    if not PWD:
        print("set NIAGARA_PASS (and optionally NIAGARA_USER / NIAGARA_HOST)",
              file=sys.stderr)
        return 2

    n = N4()
    n.login()
    n.session()
    data = n.alarms(limit=limit, offset=offset)

    if as_json:
        print(json.dumps({
            "total": data.get("total"),
            "returned": data.get("returned"),
            "offset": offset,
            "records": [flatten(r) for r in data["r"]],
        }, indent=2, ensure_ascii=False))
        return 0

    print("alarm records in database: %s    shown: %s"
          % (data.get("total"), data.get("returned")))
    if limit is not None:
        print("note: limit=%d is a client-side trim; the station sent all %s records"
              % (limit, data.get("total")))
    print()
    print("%-26s %-10s %-14s %-8s %s"
          % ("timestamp", "state", "class", "prio", "source"))
    print("-" * 110)
    for r in data["r"]:
        f = flatten(r)
        print("%-26s %-10s %-14s %-8s %s" % (
            f.get("timestamp", ""),
            f.get("sourceState") or "",
            f.get("alarmClass") or "-",
            f.get("priority") or "",
            (f.get("source") or "")[:60],
        ))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
