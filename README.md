# Niagara N4 BOX Protocol — Reading the Alarm Database

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Standard library only](https://img.shields.io/badge/dependencies-none-success.svg)](#)

**Read alarms from a Tridium Niagara N4 / JACE station over the native BOX protocol.**
Dependency-free Python. No oBIX license, no REST add-on, no third-party packages.

Structured JSON out, which means it drops straight into an agent or an LLM
pipeline: **classify severity, group cascade alarms, summarise a shift, draft the
first-line response.** Nothing about the station is exposed to the model except
the alarm records you hand it.

---

## Why this matters now: alarms are the last thing AI cannot see

Building operators are wiring assistants into energy data, work orders and
schedules. The one feed they cannot get is the alarm feed — because Niagara has
no documented API for it. That gap is why this repository exists.

If you are building an assistant that answers *"what is wrong with this building
right now"*, the alarm feed is the missing input. This is how you get it.

The reader hands you each alarm as a clean object:

```json
{
  "timestamp": "2026-10-30T15:22:15.368+08:00",
  "uuid": "d0402dcc-5ea6-4658-a781-a924f761d87d",
  "sourceState": "offnormal",
  "ackState": "unacked",
  "alarmClass": "Red_High",
  "priority": "255",
  "source": "Drivers/SnmpNetwork/.../points/UPS_in_battery_mode",
  "alarmData": {
    "msgText": "UPS battery time is increasing. Please check.",
    "Count": 68
  }
}
```

That is enough context for a useful prompt without shipping the station.

### What models are genuinely good at here

| Task | Why it works |
|:--|:--|
| **Normalising message text** | Alarms arrive as free text with vendor-specific wording; a model rewrites them into one consistent form |
| **Deduplicating cascades** | One failed controller raises forty point alarms; a model groups them under one root event |
| **Classifying severity** | Rules miss nuance — "UPS on battery" at 2 a.m. is not the same as at 2 p.m. |
| **Drafting the first-line response** | "Check the switch port; this point faulted at the same time as three others on the same trunk" |
| **Summarising a shift** | "Twelve alarms overnight, nine were the same chiller restarting" |

### What they must not be trusted with

- Deciding whether an alarm is a real safety event
- Any action that writes to the station
- Anything where a wrong answer is expensive and unverifiable

Keep a human in the loop for the last mile. The model triages; a person decides.

---

## The problem

You have a Niagara station. You want its alarms in *your* system — a dashboard, a
WeChat/DingTalk/WhatsApp webhook, a ticketing queue, an overnight on-call rotation.

Niagara's built-in alarm notification sends email, SMS or SNMP traps. It does not
give you a programmable feed, it is awkward to configure, it cannot aggregate
across stations, and **the SMS option is a separately licensed feature**.

**There is no documented API for reading the alarm database.** oBIX does not
expose it. The REST automation API does not expose it. The station's own UI reads
it over a protocol called **BOX** — and that protocol is undocumented.

This repository documents it, with a working reference reader.

---

## What you can do with this

| Use case | What it looks like |
|:--|:--|
| **Multi-station alarm rollup** | Poll 5 JACE stations every minute, merge into one feed |
| **Custom notifications** | Push unacked alarms to WeChat Work / DingTalk / WhatsApp / Slack / Teams |
| **Ticketing integration** | Auto-create a ticket when a `Red_High` alarm appears |
| **Overnight on-call** | Escalate to a phone call only for high-priority, unacknowledged alarms |
| **Compliance archiving** | Pull the full alarm history into your own database before it ages out |
| **Health monitoring** | Detect comm-loss alarms and page the network owner |
| **AI triage** | Feed alarms to an LLM for classification, summarisation and first-line diagnosis |

All of it read-only. This repository never writes to a station.

---

## Drop your Niagara SMS license

Niagara's SMS notification is a **paid add-on**. If your only requirement is
"an alarm fires, a phone rings", you do **not** need that add-on — this reader
plus a gateway you already own will get you there:

```
Niagara SMS add-on:   per-station license  +  modem  +  SIM  +  per-alarm config
This route:           this reader  +  any SMS/voice gateway you already own
```

You keep full control of the message format, the routing rules and the escalation
policy. Nothing about it is tied to a Tridium license, and nothing about it stops
you from using the official add-on alongside it if that suits you better.

There is also an operational reason to prefer it: messages leave through a
gateway you control, so alarm text and site identifiers never pass through a
third-party messaging provider unless you choose one.

See [Notification channels](#notification-channels) for wiring it to SMS, voice
calls, WhatsApp, WeChat or anything else.

---

## Why not oBIX / REST / SNMP?

| Approach | Reads alarm DB | Extra licensing | Effort |
|:--|:--:|:--:|:--|
| **BOX (this repo)** | ✅ full database | none | low — plain HTTP |
| oBIX | ❌ point values only | oBIX driver | medium |
| REST automation API | ❌ point values only | REST add-on | medium |
| Niagara SMS notification | ⚠️ what you route to it | **SMS license** | medium — per alarm config |
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

## Notification channels

The reader hands you a list of alarms. What you do with them is open. All of the
following are plain outbound HTTP or a serial port — nothing needs a Tridium
license.

| Channel | How | Notes |
|:--|:--|:--|
| **SMS** | HTTP API of any SMS provider, or a GSM modem on a serial port | see note below |
| **Voice call** | Same providers usually offer TTS calls; or an LTE module with AT commands | ring the phone for `Red_High` only |
| **WeChat Work** | `POST` a `webhook/send` JSON body to your bot URL | group robot, no auth setup |
| **DingTalk** | Same pattern, `oapi.dingtalk.com/robot/send` | markdown message type renders well |
| **WhatsApp** | Cloud API or a local gateway (e.g. a phone running a bridge) | template messages needed for cold sends |
| **Telegram** | `api.telegram.org/bot<token>/sendMessage` | easiest to start with |
| **Slack / Teams** | Incoming webhook URL | incoming webhook is a two-minute setup |
| **Email** | SMTP | the one thing Niagara already does well |
| **HTTP POST** | Any endpoint you control | the generic escape hatch |

Two design notes:

- **Escalate, do not broadcast.** Send a chat message for everything;
  place a voice call only for unacknowledged `Red_High` alarms that have been
  open longer than N minutes. Calls wake people up and cost attention.
- **Rate-limit and coalesce.** One message per event will flood a chat channel
  during a network outage. Group alarms raised inside the same minute into a
  single message with a count.

---

## AI-assisted alarm triage in practice

Because the reader emits structured JSON, it plugs into an LLM workflow without a
glue layer.

```
poller  →  new alarms (JSON)
        →  prompt: alarm class, source, message text, recent history
        →  model returns: severity, likely cause, suggested first action
        →  route: chat message for low, ticket for medium, call for high
```

The section at the top of this document lists what models do well and what they
should not touch. The practical value is not a smarter alarm — it is a **smaller
number of alarms a human has to read**, grouped and pre-digested, with the obvious
noise already removed.

What models are actually good at here:

| Task | Why it works |
|:--|:--|
| **Normalising message text** | Alarms arrive as free text with vendor-specific wording; a model rewrites them into one consistent form |
| **Deduplicating cascades** | One failed controller raises forty point alarms; a model groups them under one root event |
| **Classifying severity** | Rules miss nuance ("UPS on battery" at 2 a.m. is not the same as at 2 p.m.) |
| **Drafting the first-line response** | "Check the network switch port, this point went fault at the same time as three others on the same trunk" |
| **Summarising a shift** | "Twelve alarms overnight, nine were the same chiller restarting" |

What they are **not** good at, and should not be trusted with:

- Deciding whether an alarm is a real safety event
- Any action that touches the station
- Anything where a wrong answer is expensive and unverifiable

Keep a human in the loop for the last mile. The model triages; a person decides.

The alarm JSON includes `uuid`, `timestamp`, `alarmClass`, `priority`,
`sourceState`, `ackState` and a decoded `alarmData` facet map — enough context for
a useful prompt without shipping the whole station.

### Prompt pack

Three prompts that work on the reader's output as-is. Copy them, replace nothing
but the alarm array, and check the results against your own history before you
put them on a live feed.

**1. Collapse a cascade into root events**

```
You are given alarms from one station covering the last 15 minutes.
Several of them are consequences of a single fault.

Group them. Return JSON only, no prose:
{
  "groups": [
    {
      "root_cause": "one sentence, plain language",
      "alarm_uuids": ["...", "..."],
      "confidence": 0.0
    }
  ],
  "unclassified": ["uuid", "uuid"]
}

Rules:
- Only use uuids present in the input. Never invent one.
- If two alarms share a source path prefix, they are probably related — but
  say so with a lower confidence, not a higher one.
- If grouping is unclear, put the alarm in unclassified instead of guessing.

Alarms:
<PASTE THE JSON ARRAY HERE>
```

**2. Decide what a human actually needs to see**

```
You are triaging alarms for an on-call engineer at 03:00.

For each alarm decide one of: page_now, ticket, digest.
Base the decision on:
- alarmClass and priority
- how many alarms share the same source
- whether the same alarm repeated within the window

Return JSON only:
{"decisions": [{"uuid": "...", "route": "page_now|ticket|digest", "why": "max 12 words"}]}

Be conservative. When evidence is thin, downgrade one level rather than upgrade.

Alarms:
<PASTE THE JSON ARRAY HERE>
```

**3. Write the shift handover**

```
Write a handover note for the next shift from these alarms.

Format:
- Headline: one sentence, what happened tonight
- Repeated issues: bullets, each naming the source and how many times
- Still open: bullets, unacked alarms only
- Suggested first checks: max three bullets

Do not speculate about causes that are not supported by the alarm text.
If the input is quiet, say so in one line and stop.

Alarms:
<PASTE THE JSON ARRAY HERE>
```

What to check before trusting any of this: run the prompts over a month of
archived alarms where you already know the answer. If a prompt invents a uuid, a
cause, or an alarm that was not there, do not deploy it.

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

## Need the pieces that are not in this repo?

This repository stops at "you have the alarms as JSON". Building the rest —
poller, queue, routing rules, SMS and voice gateways — is integration work.

If you want help with any of it:

- **Multi-station aggregation** — one feed from many JACE / Edge stations
- **SMS and voice delivery** — including an on-premise SMS and voice gateway
  driven over standard AT commands: messages never leave your network, and there
  is no per-message provider fee
- **WeChat / DingTalk / WhatsApp routing** — with escalation policy
- **AI triage** — severity classification, cascade grouping, shift summaries
- **On-site integration** — Niagara, BACnet, Modbus, DALI in one system

**jason.zhang@gline-net.com**

---

## License

MIT — see [LICENSE](LICENSE).

---

*This document describes interfaces discovered through independent observation of
a Niagara station's own client-side code. It is not affiliated with or endorsed
by Tridium, Inc. Niagara and Tridium are trademarks of Tridium, Inc.*
