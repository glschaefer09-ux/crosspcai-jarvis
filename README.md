# JARVIS — the CrossPCAI control centre

One app. Chat, agents, a sandbox and Slack behind a single icon, on every
machine you install it on.

A customer downloads one file, launches one icon, answers four questions, and
JARVIS sets itself up — including the background services it needs. There is
nothing else to install and no systemd unit to hand-write.

---

## What it is

| Pane | What it does |
|---|---|
| **Chat** | Talk to a model that can act — queue work on Hermes, run commands in the sandbox, post to Slack |
| **Agents** | Create agents with a name, a standing brief and a schedule; three ship ready to use |
| **Sandbox** | A jailed workspace with a terminal and file browser — commands never touch the real desktop |
| **Slack** | Read channels and post, without leaving the app |
| **Connectors** | What JARVIS can reach. Wire any HTTP API yourself, or ask for one |
| **Tools** | Named actions agents can call: HTTP, shell or prompt macros |
| **Machines** | Pair every install and drive them all from one window |
| **Reports** | Everything you needed and did not have — sent only if you send it |
| **System** | Start, stop, restart and read the logs of the services JARVIS runs |

---

## Install

### Windows
```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```
Puts `JARVIS.exe` in `%LOCALAPPDATA%\Programs\JARVIS` with a Desktop and Start
Menu icon. Add `-Startup` to launch it at sign-in.

### Linux / CrossPC AI OS
```bash
sudo ./install.sh
```
Installs to `/opt/jarvis`, adds `jarvis` to your PATH and one icon to the
applications menu. Add `--server` to also enable the background service.

A `.deb` is available too:
```bash
python3 packaging/build.py --deb
sudo apt install ./dist/jarvis_1.0.0_amd64.deb
```

### macOS
```bash
python3 packaging/build.py
cp -R dist/JARVIS.app /Applications/
```

### TrueNAS SCALE / any Docker host
```bash
docker compose -f packaging/docker-compose.yml up -d
```
Then open `http://<host>:5580`. On TrueNAS, point the `/data` volume at a
dataset so config and the sandbox survive an app update.

### From source (any platform)
```bash
pip install pywebview pystray pillow   # optional: native window and tray
python3 -m jarvis
```

---

## One app, many roles

The shipped product is a single executable. Background daemons are not separate
downloads — the supervisor re-launches the same binary with a flag:

```
JARVIS.exe --server --port 5580     the app and its UI
JARVIS.exe --service hermes         the task queue      (port 5562)
JARVIS.exe --service sandbox        the workspace       (port 5561)
```

That is why there is one icon and one thing to update.

**It attaches rather than clobbers.** If something already serves port 5562 —
an existing `crosspcai-hermes.service`, say — JARVIS connects to it and reports
the service as `external` instead of starting a rival process on the same port.

| Command | What it does |
|---|---|
| `jarvis` | Desktop app: native window plus tray icon |
| `jarvis --server` | Headless; serves the UI over the network |
| `jarvis --status` | One-shot health check for scripts and CI |
| `jarvis --reset-setup` | Run the first-run wizard again |
| `jarvis --no-supervise` | Do not start or manage background services |

---

## One interface, every machine

Install JARVIS on the Windows laptop, the Ubuntu box and the NAS. Pair them
once — **Machines › Pair a machine**, or **Scan network** to find them — and the
switcher in the title bar points the whole interface at whichever one you want.
Every pane, including the sandbox terminal and the service controls, then
operates that machine. Calls are proxied over HTTP with a shared token.

Loopback traffic is trusted, so the desktop window needs no login. Anything
arriving over the network must carry the token, so `--server` on a shared
network does not hand the machine away.

### Phones and tablets

Desktops are **nodes** — JARVIS dials into them. Phones are **devices** — they
dial in to JARVIS. A handset cannot host Hermes or a sandbox and comes and goes
from the network, so it registers itself instead of being dialled, and then
long-polls for work. Nothing listens on the handset: no inbound port, no push
dependency in the control path.

The Android and iOS apps live in the CrossPCAI repo under `mobile/android` and
`mobile/ios`. Pair one and it appears under **Machines › Phones and tablets**,
where you can see its battery, network, app version and last check-in, queue a
command, revoke it, or remove it.

Two built-in agents own the fleet — **Android Fleet** (`android-fleet`) and
**iOS Fleet** (`ios-fleet`). They read `/api/mobile/devices`, flag handsets that
have gone stale, are stuck on an old build, or are reporting an error, and queue
commands rather than asking you to pick the phone up. The commands are an
allowlist (`mobile.COMMANDS`), matched by an identical allowlist in each app, so
a confused agent cannot invent an instruction the app was never built to refuse:

| Command | What it does |
| --- | --- |
| `ping` | Ask the device to check in immediately |
| `refresh` | Re-register and clear cached state |
| `collect_logs` | Return model, OS, app version, battery, network, last error |
| `report_needs` | Push anything the device recorded as missing |
| `update_check` | Report the build it is running |
| `sign_out` | Un-pair it — the app returns to the pairing screen |

A device presenting the shared token is already trusted to drive the API, so
pairing adds no second secret. What it does add is an `approved` flag: revoke a
lost handset from Machines without rotating the token on every other machine.

---

## Reports: how customer needs reach the build queue

When you ask for a connector or a tool that does not exist — or an agent
reaches for one — JARVIS writes it down. **Nothing is transmitted by that.**
Reports live on your machine until you press **Send report**.

Sending is off entirely until you switch it on in setup or
**Settings › Privacy**. Before each send you see the exact payload.

**A report contains** the name of the thing you asked for, its category, your
reason, and how many times you asked.

**A report never contains** your conversations, files, command output, Slack
messages, API keys, or anything identifying you personally. This is enforced by
an allowlist in `telemetry.py` — fields outside it are dropped before the
payload is built, and any value that looks like a credential is discarded.

Point reports at any HTTPS endpoint. In the CrossPCAI stack that is an n8n
webhook that files them into Notion, where the agent workforce turns them into
built connectors and upgraded tools. Notion's API is also supported directly
(`telemetry.notion_token` + `notion_database_id`).

---

## Configuration

Everything lives in `~/.crosspcai` (`%USERPROFILE%\.crosspcai` on Windows,
`/data` in the container):

```
agent_token           shared bearer token, reused by the whole CrossPCAI stack
jarvis.json           settings written by the wizard and the Settings pane
jarvis.db             chat sessions, agents, tools, connectors, reports
license.json          activation state
sandbox/              the sandbox workspace
logs/                 per-service output
```

Environment overrides: `CROSSPCAI_HOME`, `JARVIS_PORT`, `JARVIS_BIND`,
`CROSSPCAI_HOST`, `SLACK_BOT_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`.

### Connectors

Connectors are browsable in the app under **Connectors**, grouped by category
with a filter box. Every entry declares an honest status, because a catalogue
that implies everything works equally is worse than a short one:

| Status | Meaning |
|---|---|
| **built in** | JARVIS speaks it directly, with code behind it |
| **ready to wire** | No bespoke code, but it is a plain HTTP API the generic connector reaches. **Add** pre-fills base URL, auth style and a test path, so one click plus your credential gives a working connector |
| **not yet** | Not reachable — usually OAuth, SigV4 signing or a database driver. **Request it** files it in Reports |

48 entries across 9 categories: core, AI and coding, messaging, knowledge,
development, business, automation, data and infrastructure — Slack, Notion,
GitHub, GitLab, Jira, Linear, Sentry, Stripe, HubSpot, Intercom, Shopify,
Discord, Telegram, Supabase, Airtable, Docker, TrueNAS, Proxmox, Cloudflare,
Home Assistant and more. Anything missing can be built by hand as a custom HTTP
connector, or requested.

### Models
Ollama is the default and needs no key — it runs on the machine and nothing
leaves it. Anthropic and OpenAI work with a key entered in setup or Settings.
Keys are stored locally and are never included in a report.

### Slack
Create a Slack app with a bot token carrying `channels:read`,
`channels:history`, `chat:write` and `users:read`, then paste it into
**Slack › Connect**. Messages are only ever posted when you press Send.

---

## Licensing

Every install starts on a 14-day trial with chat, sandbox and agents. A key
unlocks the rest:

| Tier | Seats | Adds |
|---|---|---|
| Basic | 1 | Slack |
| Plus | 3 | Connectors, tools |
| Premium | 10 | Machine pairing, priority support |

Stamp a real signing secret before a release build:

```bash
python3 packaging/build.py --sign-key "your-secret"
```

The offline check gates honest customers and keeps entitlements tidy. A secret
inside a desktop binary is discoverable, so anything that costs money to serve
must be checked server-side as well.

---

## Building

```bash
pip install pyinstaller pillow
python3 packaging/build.py            # binary for this platform
python3 packaging/build.py --deb      # plus a .deb (Linux)
python3 packaging/build.py --icons    # regenerate icons only
```

Build on the platform you are shipping to — PyInstaller does not cross-compile.
Output lands in `dist/`.

---

## Safety

The sandbox and the Hermes queue both refuse a blocklist of destructive
commands (`rm -rf /`, `mkfs`, `dd` to a device, fork bombs, piping a download
straight into a shell, `format`, and so on). Sandbox paths are resolved inside
the workspace, so traversal out of it fails. The container runs as a non-root
user.

This is a guard rail against accidents, not a security boundary. Do not give
the sandbox credentials you would not give the person at the keyboard.
