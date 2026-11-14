# Niagara N4 BOX Protocol — Reading the Alarm Database

> A practical, protocol-level guide to reading alarms from Tridium Niagara N4 / JACE
> stations over the native BOX (Baja) HTTP + WebSocket interface.
>
> Verified on Niagara 4.15 (Edge10 platform) and N4 JACE hardware.

---

## Why this guide exists

Tridium documents oBIX and the REST automation API. It does **not** document the
BOX protocol used by the Workbench and the browser UI — the same protocol the
station's own JavaScript uses to render the alarm console.

Everything here was derived by reading the station's own front-end bundle and by
observing real traffic. It is provided so integrators can build their own
monitoring tooling without reverse-engineering it again.

---

## 1. What you need

| Item | Value |
|---|---|
| Station address | `http://<station-ip>` (port 80, or 443 with TLS) |
| Credentials | Any user with read access to the alarm service |
| Python | 3.8+ (standard library only — no third-party packages) |

The reference implementation in `scripts/n4_ok.py` uses nothing beyond the
Python standard library: `http.client`, `hashlib`, `hmac`, `base64`, `json`.

---

## 2. Authentication: SCRAM-SHA512

**Form login will not work.** Posting `j_username` / `j_password` to `/login`
returns the login page again and never issues a `JSESSIONID`. N4 uses
SCRAM-SHA512 challenge-response instead.

### Step 1 — Obtain an initial session

```http
GET /prelogin
```

Response sets `JSESSIONID`.

### Step 2 — Client first message

```http
POST /j_security_check/
Content-Type: application/x-niagara-login-support

action=sendClientFirstMessage&clientFirstMessage=n,,n=<USER>,r=<NONCE>
```

`NONCE` is 16 random bytes, base64 encoded.

Server replies with a bare SCRAM message:

```
r=<server-nonce>,s=<salt-base64>,i=<iterations>
```

Iteration count is typically 10000.

### Step 3 — Compute the proof

```python
salted     = pbkdf2_hmac("sha256", password.encode(), b64decode(salt), iterations, 32)
client_key = hmac(salted, b"Client Key", sha256)
stored_key = sha256(client_key)

channel_binding = "c=biws,r=" + server_nonce
auth_message    = client_first_bare + "," + server_first + "," + channel_binding

client_sig = hmac(stored_key, auth_message.encode(), sha256)
proof      = bytes(a ^ b for a, b in zip(client_key, client_sig))
```

### Step 4 — Client final message

```http
POST /j_security_check/
Content-Type: application/x-niagara-login-support

action=sendClientFinalMessage&clientFinalMessage=c=biws,r=<server-nonce>,p=<base64-proof>
```

The server returns `v=<server-signature>`. Verify it — this confirms the server
also knows the password:

```python
server_key = hmac(salted, b"Server Key", sha256)
expected   = b64encode(hmac(server_key, auth_message.encode(), sha256))
assert received_v == expected.decode()
```

### Step 5 — Commit the session

```http
GET /j_security_check/
```

Returns `302` and issues `JSESSIONID=<...>.node0`.

**Only `JSESSIONID` matters.** A `niagara_userid` cookie alone is not
sufficient — it is a UI hint, not an authenticated session.

### Two implementation notes

1. **Sleep ~0.6 s between the two POSTs.** Sending them back to back fails on
   some platforms.
2. **Do not reuse connections.** See the next section.

---

## 3. The connection pitfall

Edge10-based stations have a defective HTTP keep-alive implementation. If you
reuse a connection across the two SCRAM POSTs, the second one is reset with
`RemoteDisconnected`.

Create a fresh connection per request and close it immediately:

```python
def http(self, method, path, body=None, ctype=None):
    c = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
    try:
        ...
        return status, headers, data
    finally:
        c.close()
```

This single change fixes the most common class of login failure.

---

## 4. Creating a BOX session

BOX operations are JSON messages posted to `/box/`.

```http
POST /box/
Content-Type: application/json
Cookie: JSESSIONID=<...>

{"p":"box","v":"2.3","m":[{"r":1,"t":"rt","c":"ssession","k":"make","b":{}}],"n":1}
```

Response:

```json
{"v":"2.4","p":"box","n":1,
 "m":[{"c":"ssession","k":"make","r":1,"t":"rp",
       "b":"serverSessionH8cEyM8Q...$3d$3d"}]}
```

Notes:

- The endpoint is `/box/`, **not** `/ord/box`.
- Send protocol version `2.3`; the server answers `2.4`. Accept whatever it
  returns.
- The session id must be URL-unescaped before use: `$2f` → `/`, `$3d` → `=`,
  `$2b` → `+`.
- Session creation **must** happen over HTTP. Issuing `ssession make` over the
  WebSocket is rejected with *"Can only create server session over HTTP/HTTPS"*.

Every subsequent message carries the session id in the top-level `"id"` field.

---

## 5. Querying the alarm database

```http
POST /box/
Content-Type: application/json
Cookie: JSESSIONID=<...>

{"p":"box","v":"2.3","n":2,"id":"<serverSession...>",
 "m":[{"r":1,"t":"rt","c":"alarm","k":"queryAlarmDatabase",
       "b":{"t":"alarm:AlarmRecord","l":0,"o":0,"d":false,"c":true,"a":false}}]}
```

### Parameters

| Key | Meaning | Notes |
|:--|:--|:--|
| `t` | Record type | Always `alarm:AlarmRecord` |
| `l` | Limit | **Ignored by the station** |
| `o` | Offset | **Ignored by the station** |
| `d` | Deep query | `false` |
| `c` | Include column details | **Must be `true`**, otherwise no `s` array |
| `a` | Include archived | `false` |

### The limit trap

`l` and `o` are accepted without error but have **no effect**. A single request
always returns the entire alarm database. Client-side truncation only reduces
what you print — it does not reduce network transfer or station load.

Any code that assumes server-side paging is wrong.

### Response shape

```json
{
  "c": 60,
  "r": [
    {
      "nm": "p",
      "t": "alarm:AlarmRecord",
      "d": "timestamp = ..., sourceState = Normal, ...",
      "s": [
        {"n": "timestamp",  "v": "2026-10-30T15:22:10.285+08:00"},
        {"n": "uuid",       "v": "96c39f22-c268-460e-949d-6f060ec40964"},
        {"n": "sourceState","v": "normal"},
        {"n": "ackState",   "v": "unacked"},
        {"n": "source",     "v": "local:|station:|slot:/..."},
        {"n": "alarmClass", "v": "Red_High"},
        {"n": "priority",   "v": "255"},
        {"n": "normalTime", "v": "..."},
        {"n": "alarmData",  "v": "presentValue=s:false|msgText=s:...|"},
        {"n": "lastUpdate", "v": "..."}
      ]
    }
  ]
}
```

- `c` — total record count in the alarm database
- `r` — the records themselves
- `s[].n` — field name, `s[].v` — value, `s[].dn` — display name

### Ordering

Records come back in **ascending timestamp order**. The newest alarm is the
**last** element. Do not assume the opposite.

---

## 6. Field values and escaping

### Baja string escaping

Values use a `$XX` escape scheme:

| Escape | Character |
|:--|:--|
| `$20` | space |
| `$2e` | `.` |
| `$2f` | `/` |
| `$3d` | `=` |
| `$2b` | `+` |
| `$7b` | `{` |
| `$7d` | `}` |

### alarmData facets

`alarmData` is a pipe-delimited facet list with typed prefixes:

```
presentValue=s:false|fromState=s:normal|toState=s:offnormal|msgText=s:Alarm$20triggered$2e|Count=i:68
```

| Prefix | Type |
|:--|:--|
| `s:` | string |
| `b:` | boolean |
| `i:` | integer |
| `r:` | real |
| `z:` | timezone |

Split on `|`, then on the first `:`.

---

## 7. Which alarm-channel operations exist

The `alarm` box channel supports only a few keys. Everything else returns
`Unsupported Key`.

| Key | Works | Purpose |
|:--|:--|:--|
| `queryAlarmDatabase` | ✅ | Read alarm records |
| `getAlarmFields` | ✅ | List all field names |
| `getRecordCount` | ⚠️ | Requires a specific argument encoding |
| `ackAlarms` | ✅ | Acknowledge |
| `addNoteToAlarms` | ✅ | Attach a note |
| `clearRecords` / `clearOldRecords` | ✅ | Purge |

These **do not exist**:

```
getConsoles  listConsoles  getConsoleList  consoles  getAlarmConsoles
describe  keys  help  props  getFields  getAlarmClassMap  getSourceMap
```

### There is no "alarm console" concept on the wire

Alarm consoles are a **UI notion**. They are filtered views rendered in the
browser on top of the single alarm database. The filtering happens in JavaScript,
not in the station API.

If you need console-like behaviour, fetch the full database and filter locally by
`alarmClass`, `sourceState`, or `source`.

---

## 8. Filtering must be client-side

Since `l` and `o` are ignored, and there is no query predicate, the only way to
narrow results is post-filtering:

```python
recs = [r for r in data["r"]
        if field(r, "sourceState") == "offnormal"
        and field(r, "alarmClass") == "Red_High"]
```

Attempts to pass `alarmClass` or a nested `filter` object to
`queryAlarmDatabase` are silently ignored — the full database is returned
regardless.

---

## 9. WebSocket variant

For continuous subscriptions, upgrade `/wsbox` instead of polling `/box/`.

```http
GET /wsbox HTTP/1.1
Upgrade: websocket
Connection: Upgrade
Sec-WebSocket-Key: <base64 16 random bytes>
Sec-WebSocket-Version: 13
Cookie: JSESSIONID=<...>
Origin: http://<station-ip>
```

Expect `101 Switching Protocols`. Frames are JSON with the same BOX envelope,
including the session id.

Create the session over HTTP first, then attach with the WebSocket.

---

## 10. Building a channels for station components (makessc)

Reading component slots (point values, status flags) uses a separate path.

```json
{"p":"box","v":"2.3","n":2,"id":"<session-id>",
 "m":[{"r":2,"t":"rt","c":"ssession","k":"makessc",
       "b":{"id":"<session-id>",
            "scid":"station:",
            "scts":"box:ComponentSpaceSessionHandler",
            "scarg":"station:|"}}]}
```

Four things must all be correct — any one wrong yields a silent failure:

| Field | Must be |
|:--|:--|
| `b.id` | The session id **inside the body too**, not only at frame level |
| `scid` | Object-relative ORD, e.g. `"station:"` |
| `scarg` | An ORD, e.g. `"station:\|"` |
| `scts` | `"box:ComponentSpaceSessionHandler"` |

Only two session handler types exist:

- `box:ComponentSpaceSessionHandler` — with `scarg="station:|"`, `scid="station:"`
- `box:NavNodeSessionHandler` — navigation tree

Once the channel is open:

| Key | Purpose |
|:--|:--|
| `loadRoot` | Obtain the root component handle |
| `loadSlots` | Read slots: `{o: ord, d: depth}` |
| `syncTo` | Batch synchronisation |
| `commitSyncOps` | Commit changes |
| `mount` / `unmount` | Attach components |

---

## 11. Checklist for a new station

1. `ping` the address.
2. Confirm port 80 is open.
3. Log in with SCRAM — check that `JSESSIONID` is in the cookie jar.
4. Create a BOX session — check that the returned string starts with
   `serverSession`.
5. Query alarms — check the `c` field for the total, not the list length.
6. Confirm timestamp ordering is ascending.

Failure messages and their causes:

| Symptom | Cause |
|:--|:--|
| `SCRAM step1: 500` | Wrong `Content-Type`; must be `application/x-niagara-login-support` |
| `RemoteDisconnected` | Connection reuse; create one connection per request |
| `login failed: no JSESSIONID` | Proof computation wrong, or the final `GET` was skipped |
| `box HTTP 302` | No session — you never finished the login |
| `Unsupported Key` | That operation does not exist on this channel |

---

## 12. Reference implementation

`scripts/n4_ok.py` is a complete, dependency-free reader.

```bash
python3 n4_ok.py 20
python3 n4_ok.py --json
NIAGARA_HOST=192.168.1.10 NIAGARA_USER=<user> NIAGARA_PASS=<password> python3 n4_ok.py 5
```

All connection state, SCRAM handshake, BOX framing, and field decoding are
contained in a single file of roughly 350 lines.

---

## 13. Notes and caveats

- **Read-only.** Nothing here writes to the station. `ackAlarms` and
  `clearRecords` exist but are deliberately not exercised.
- **Alarm database capacity is finite.** When full, the station drops the oldest
  records. A monitoring loop that polls infrequently can miss events that were
  evicted between polls. Track the record count and the oldest timestamp to
  detect this.
- **Protocol version drifts.** Send `2.3`, tolerate `2.4`.
- **Session ids are single-use.** Create one per process run.
- **Polling frequency** trades freshness against station load. With a full
  database returned every time, a short interval is not free.

---

## Contact

If you need the full reference reader with multi-station support, or want to
discuss integration work, get in touch:

**jason.zhang@gline-net.com**

---

*This document describes interfaces discovered through independent observation of
a Niagara station's own client-side code. It is not affiliated with or endorsed
by Tridium, Inc. Niagara and Tridium are trademarks of Tridium, Inc.*
