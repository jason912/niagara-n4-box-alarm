# Niagara N4 BOX Protocol — Reading the Alarm Database

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Read alarms from a Tridium Niagara N4 / JACE station over the native BOX protocol.**
Dependency-free Python. No oBIX license, no REST add-on, no third-party packages.

---

## The problem

You have a Niagara station. You want its alarms in *your* system — a dashboard, a
WeChat/DingTalk/Teams webhook, a ticketing queue, an overnight on-call rotation.

Niagara's built-in alarm notification sends email, SMS or SNMP traps. It does not
give you a programmable feed, it is awkward to configure, and it cannot aggregate
across stations.

**There is no documented API for reading the alarm database.** oBIX does not
expose it. The REST automation API does not expose it. The station's own UI reads
it over a protocol called **BOX** — and that protocol is undocumented.

This repository documents it, with a working reference reader.

---

## What you can do with this

| Use case | What it looks like |
|:--|:--|
| **Multi-station alarm rollup** | Poll 5 JACE stations every minute, merge into one feed |
| **Custom notifications** | Push unacked alarms to WeChat Work / DingTalk / Slack / Teams |
| **Ticketing integration** | Auto-create a ticket when a `Red_High` alarm appears |
| **Overnight on-call** | Escalate to a phone call only for high-priority, unacknowledged alarms |
| **Compliance archiving** | Pull the full alarm history into your own database before it ages out |
| **Health monitoring** | Detect comm-loss alarms and page the network owner |

All of it read-only. This repository never writes to a station.

---

## Why not oBIX / REST / SNMP?

| Approach | Reads alarm DB | Extra licensing | Effort |
|:--|:--:|:--:|:--|
| **BOX (this repo)** | ✅ full database | none | low — plain HTTP |
| oBIX | ❌ point values only | oBIX driver | medium |
| REST automation API | ❌ point values only | REST add-on | medium |
| SNMP trap receiver | ⚠️ only what is configured to trap | none | high — config per alarm |
| Email parsing | ⚠️ fragile, text scraping | none | high — breaks on format change |

The alarm database is its own service. It is reachable through the `alarm` BOX
channel and nowhere else.

---

## Quick start

```bash
git clone https://github.com/jason912/niagara-n4-box-alarm.git
cd niagara-n4-box-alarm

NIAGARA_HOST=192.168.1.10 \
NIAGARA_USER=admin \
NIAGARA_PASS=yourpassword \
python3 scripts/n4_ok.py 10
```

Output:

```
alarm records in database: 60    shown: 10
note: limit=10 is a client-side trim; the station sent all 60 records

timestamp                  state      class          prio     source
---------------------------------------------------------------------------------
2026-10-30T15:22:10.285+08:00 normal  Gline_Alarm    255      Services/AlarmService/...
2026-10-30T15:22:15.368+08:00         Red_High       255      Drivers/SnmpNetwork/...
```

**JSON output**, for feeding another system:

```bash
NIAGARA_HOST=192.168.1.10 NIAGARA_USER=admin NIAGARA_PASS=yourpassword \
python3 scripts/n4_ok.py --json
```

Minimal embedding:

```python
import sys; sys.path.insert(0, "scripts")
from n4_ok import N4

n = N4("192.168.1.10", 80, "admin", "yourpassword")
n.login()
n.session()
data = n.alarms()

for rec in data["r"]:
    print(rec["s"])     # field list; use flatten() for a dict
```

---

## Read this before you build on it

Two facts change how you design a poller.

**1. The station ignores paging.** `queryAlarmDatabase` accepts `l` (limit) and
`o` (offset) without error but **returns the entire alarm database every time**.
There is no server-side filtering and no predicate support. Narrowing results is
entirely your job, client-side.

**2. The alarm database has a finite capacity.** When it fills, the station drops
the oldest records. **Events can be evicted between two polls.** A one-minute poll
on a station producing steady alarms is fine; a ten-minute poll on a station
generating bursts is not. Track the record count and the oldest timestamp to
detect that records were lost.

Everything else is in the guide below.

---

## Compatibility

| Station | Version | Status |
|:--|:--|:--|
| Edge10 platform | Niagara 4.15 | ✅ verified, 60-record database read end to end |
| N4 JACE | Niagara 4.15 | ✅ verified, 106-record database read end to end |
| Other N4 builds | 4.10 – 4.14 | expected to work; BOX changed little across N4 |
| Niagara AX / 3.x | — | ❌ different architecture, not applicable |

If you verify a build not listed here, an issue with the version string is
welcome.

---

## The guide

### 1. Authentication: SCRAM-SHA512

**Form login does not work.** Posting `j_username` / `j_password` to `/login`
returns the login page again and never issues a `JSESSIONID`. N4 uses
SCRAM-SHA512 challenge-response.

#### Step 1 — Obtain an initial session

```http
GET /prelogin
```

Response sets `JSESSIONID`.

#### Step 2 — Client first message

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

#### Step 3 — Compute the proof

```python
salted     = pbkdf2_hmac("sha256", password.encode(), b64decode(salt), iterations, 32)
client_key = hmac(salted, b"Client Key", sha256)
stored_key = sha256(client_key)

channel_binding = "c=biws,r=" + server_nonce
auth_message    = client_first_bare + "," + server_first + "," + channel_binding

client_sig = hmac(stored_key, auth_message.encode(), sha256)
proof      = bytes(a ^ b for a, b in zip(client_key, client_sig))
```

#### Step 4 — Client final message

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

#### Step 5 — Commit the session

```http
GET /j_security_check/
```

Returns `302` and issues `JSESSIONID=<...>.node0`.

**Only `JSESSIONID` matters.** A `niagara_userid` cookie alone is not sufficient —
it is a UI hint, not an authenticated session.

Two implementation notes:

1. **Sleep ~0.6 s between the two POSTs.** Sending them back to back fails on
   some platforms.
2. **Do not reuse connections.** See the next section.

---

### 2. The connection pitfall

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

### 3. Creating a BOX session

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

### 4. Querying the alarm database

```http
POST /box/
Content-Type: application/json
Cookie: JSESSIONID=<...>

{"p":"box","v":"2.3","n":2,"id":"<serverSession...>",
 "m":[{"r":1,"t":"rt","c":"alarm","k":"queryAlarmDatabase",
       "b":{"t":"alarm:AlarmRecord","l":0,"o":0,"d":false,"c":true,"a":false}}]}
```

#### Parameters

| Key | Meaning | Notes |
|:--|:--|:--|
| `t` | Record type | Always `alarm:AlarmRecord` |
| `l` | Limit | **Ignored by the station** |
| `o` | Offset | **Ignored by the station** |
| `d` | Deep query | `false` |
| `c` | Include column details | **Must be `true`**, otherwise no `s` array |
| `a` | Include archived | `false` |

#### Response shape

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

#### Ordering

Records come back in **ascending timestamp order**. The newest alarm is the
**last** element. Do not assume the opposite.

---

### 5. Field values and escaping

#### Baja string escaping

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

#### alarmData facets

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

### 6. Which alarm-channel operations exist

The `alarm` box channel supports only a few keys. Everything else returns
`Unsupported Key`.

| Key | Works | Purpose |
|:--|:--:|:--|
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

#### There is no "alarm console" concept on the wire

Alarm consoles are a **UI notion**. They are filtered views rendered in the
browser on top of the single alarm database. The filtering happens in JavaScript,
not in the station API.

If you need console-like behaviour, fetch the full database and filter locally by
`alarmClass`, `sourceState`, or `source`.

---

### 7. Filtering must be client-side

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

### 8. WebSocket variant

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

### 9. Building a channel for station components (makessc)

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

### 10. Checklist for a new station

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

### 11. Building a poller that does not lose events

If you are going to run this on a schedule, three things matter more than the
protocol details.

**Keep the interval short enough.** The database drops its oldest records when
full. A station with a 60-record database and a one-minute poll can only lose
events if more than 60 alarms occur inside that minute.

**Detect eviction.** Each poll, compare the record count against its maximum and
look at the oldest timestamp. If the oldest record is newer than the newest
record from your previous poll, records were dropped between polls. Log that.

**Queue before you send.** If your downstream notification channel is down, do
not discard the alarms. Persist them locally and flush when the channel
recovers — the same behaviour Niagara's own notification service has.

---

### 12. Notes and caveats

- **Read-only.** Nothing here writes to the station. `ackAlarms` and
  `clearRecords` exist but are deliberately not exercised.
- **Protocol version drifts.** Send `2.3`, tolerate `2.4`.
- **Session ids are single-use.** Create one per process run.
- **Polling frequency costs the station.** Every request ships the whole
  database. A short interval is not free.

---

## Repository layout

```
README.md            this document
LICENSE              MIT
scripts/n4_ok.py     reference reader, standard library only
```

---

## Contributing

Corrections, additional verified station models and failure-mode reports are
welcome. Please open an issue with the Niagara build string and what you
observed.

If you found this useful, a ⭐ helps other integrators find it.

---

## Contact

Need multi-station aggregation, a production poller, or integration work?

**jason.zhang@gline-net.com**

---

## License

MIT — see [LICENSE](LICENSE).

---

*This document describes interfaces discovered through independent observation of
a Niagara station's own client-side code. It is not affiliated with or endorsed
by Tridium, Inc. Niagara and Tridium are trademarks of Tridium, Inc.*
