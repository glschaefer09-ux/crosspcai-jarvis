#!/usr/bin/env python3
"""
catalog.py — every connector and option JARVIS offers, in one place.

Each entry declares an honest status, because a list that implies everything
works equally is worse than a short one:

    native     JARVIS speaks this service directly, with code behind it.
    template   No bespoke code, but it is a plain HTTP API and the generic
               connector reaches it. The entry pre-fills base URL, auth style
               and a test path, so "Add" produces a working connector in one
               click. This is a real capability, not a promise.
    planned    Not reachable yet. Choosing it records a request in Reports,
               which the customer may optionally send to CrossPCAI.

`auth` describes what the customer must supply, so the UI can render the right
fields and say where to get the credential instead of leaving them guessing.
"""

from __future__ import annotations

# Auth styles the generic HTTP connector already implements (registry.call).
BEARER = {"type": "bearer", "label": "API token"}
HEADER = {"type": "header", "label": "Header (Name: value)"}
QUERY = {"type": "query", "label": "Query parameter (key=value)"}
NONE = {"type": "none", "label": "No authentication"}

CATEGORIES = [
    ("core", "Core", "The parts of JARVIS itself"),
    ("ai", "AI and coding", "Models and coding agents"),
    ("messaging", "Messaging", "Where your team talks"),
    ("knowledge", "Knowledge and docs", "Where things get written down"),
    ("dev", "Development", "Code, issues and CI"),
    ("business", "Business", "Customers, money and support"),
    ("automation", "Automation", "Glue between everything else"),
    ("data", "Data", "Databases and warehouses"),
    ("infra", "Infrastructure", "Machines, containers and storage"),
]


def _e(id, name, category, status, description, auth=None, base_url="",
       test_path="", docs="", note="", fields=None):
    return {
        "id": id, "name": name, "category": category, "status": status,
        "description": description, "auth": auth or NONE,
        "base_url": base_url, "test_path": test_path, "docs": docs,
        "note": note, "fields": fields or [],
    }


CONNECTORS = [
    # -- core ------------------------------------------------------------------
    _e("hermes", "Hermes", "core", "native",
       "Automation daemon and task queue. JARVIS runs it for you.",
       note="Managed automatically. Nothing to configure."),
    _e("sandbox", "Sandbox", "core", "native",
       "Isolated workspace for commands and files.",
       note="Managed automatically. Commands never touch your real desktop."),
    _e("nodes", "Paired machines", "core", "native",
       "Drive every machine you installed JARVIS on from one window.",
       note="Add machines under Machines."),
    _e("mobile", "Phones and tablets", "core", "native",
       "Android and iOS handsets register and take commands."),

    # -- ai and coding ---------------------------------------------------------
    _e("anthropic", "Anthropic (Claude)", "ai", "native",
       "Claude models for chat and agents. The strongest option here.",
       auth=BEARER, docs="https://console.anthropic.com/settings/keys",
       note="Set it under Settings > AI model. Your key never leaves this machine."),
    _e("ollama", "Ollama (local)", "ai", "native",
       "Models running on this machine. No key, no data leaves the box.",
       base_url="http://127.0.0.1:11434",
       docs="https://ollama.com/download"),
    _e("openai", "OpenAI", "ai", "native",
       "GPT models for chat and agents.",
       auth=BEARER, docs="https://platform.openai.com/api-keys"),
    _e("opencode", "OpenCode", "ai", "native",
       "The coding agent on this machine. Hand it real work: it edits files, "
       "runs commands and reports back.",
       docs="https://opencode.ai",
       note="Needs OpenCode installed and a working provider inside it."),
    _e("openrouter", "OpenRouter", "ai", "template",
       "One key, most models. Useful for trying models you do not host.",
       auth=BEARER, base_url="https://openrouter.ai/api/v1",
       test_path="/models", docs="https://openrouter.ai/keys"),
    _e("huggingface", "Hugging Face", "ai", "template",
       "Inference endpoints and model metadata.",
       auth=BEARER, base_url="https://api-inference.huggingface.co",
       docs="https://huggingface.co/settings/tokens"),
    _e("bedrock", "AWS Bedrock", "ai", "planned",
       "Claude and other models through your AWS account.",
       note="Needs SigV4 request signing, which the generic connector cannot do."),

    # -- messaging -------------------------------------------------------------
    _e("slack", "Slack", "messaging", "native",
       "Read channels and post messages from inside JARVIS.",
       auth=BEARER, docs="https://api.slack.com/apps",
       note="Bot token (xoxb-). Scopes: channels:read, channels:history, "
            "chat:write, users:read."),
    _e("discord", "Discord", "messaging", "template",
       "Post to channels and read history with a bot token.",
       auth=HEADER, base_url="https://discord.com/api/v10",
       test_path="/users/@me", docs="https://discord.com/developers/applications",
       note="Header must be: Authorization: Bot YOUR_TOKEN"),
    _e("telegram", "Telegram", "messaging", "template",
       "A bot that can message you and take commands.",
       base_url="https://api.telegram.org", test_path="/getMe",
       docs="https://core.telegram.org/bots#botfather",
       note="Put the token in the base URL: https://api.telegram.org/bot<TOKEN>"),
    _e("teams", "Microsoft Teams", "messaging", "template",
       "Post to a channel through an incoming webhook.",
       base_url="https://outlook.office.com/webhook/...",
       docs="https://learn.microsoft.com/microsoftteams/platform/webhooks-and-connectors/how-to/add-incoming-webhook"),
    _e("smtp", "Email (SMTP)", "messaging", "planned",
       "Send mail directly from an agent.",
       note="SMTP is not HTTP, so it needs its own connector."),
    _e("twilio", "Twilio (SMS)", "messaging", "template",
       "Send SMS and WhatsApp messages.",
       auth=HEADER, base_url="https://api.twilio.com/2010-04-01",
       docs="https://console.twilio.com",
       note="Basic auth header from your Account SID and auth token."),

    # -- knowledge -------------------------------------------------------------
    _e("notion", "Notion", "knowledge", "native",
       "File reports and records into a Notion database.",
       auth=BEARER, base_url="https://api.notion.com/v1",
       test_path="/users/me", docs="https://www.notion.so/my-integrations",
       note="Also the destination for connector and tool reports."),
    _e("confluence", "Confluence", "knowledge", "template",
       "Read and write pages in an Atlassian space.",
       auth=HEADER, base_url="https://your-site.atlassian.net/wiki/api/v2",
       docs="https://id.atlassian.com/manage-profile/security/api-tokens"),
    _e("gdrive", "Google Drive", "knowledge", "planned",
       "Read and write documents in Drive.",
       note="Needs an OAuth flow, which the generic connector cannot run."),
    _e("obsidian", "Obsidian vault", "knowledge", "template",
       "Read and write notes in a local vault via the Local REST API plugin.",
       auth=BEARER, base_url="http://127.0.0.1:27123",
       docs="https://github.com/coddingtonbear/obsidian-local-rest-api"),

    # -- development -----------------------------------------------------------
    _e("github", "GitHub", "dev", "template",
       "Issues, pull requests, releases and repository contents.",
       auth=BEARER, base_url="https://api.github.com", test_path="/user",
       docs="https://github.com/settings/tokens"),
    _e("gitlab", "GitLab", "dev", "template",
       "Issues, merge requests and pipelines.",
       auth=HEADER, base_url="https://gitlab.com/api/v4",
       test_path="/user", docs="https://gitlab.com/-/user_settings/personal_access_tokens",
       note="Header must be: PRIVATE-TOKEN: YOUR_TOKEN"),
    _e("jira", "Jira", "dev", "template",
       "Read and update issues.",
       auth=HEADER, base_url="https://your-site.atlassian.net/rest/api/3",
       test_path="/myself", docs="https://id.atlassian.com/manage-profile/security/api-tokens"),
    _e("linear", "Linear", "dev", "template",
       "Issues and projects over GraphQL.",
       auth=HEADER, base_url="https://api.linear.app/graphql",
       docs="https://linear.app/settings/api",
       note="Header must be: Authorization: YOUR_KEY (no 'Bearer')."),
    _e("sentry", "Sentry", "dev", "template",
       "Errors and releases, so an agent can triage what broke.",
       auth=BEARER, base_url="https://sentry.io/api/0",
       docs="https://sentry.io/settings/account/api/auth-tokens/"),

    # -- business --------------------------------------------------------------
    _e("stripe", "Stripe", "business", "template",
       "Customers, subscriptions and payments. Read-only keys recommended.",
       auth=BEARER, base_url="https://api.stripe.com/v1",
       test_path="/customers?limit=1", docs="https://dashboard.stripe.com/apikeys",
       note="Use a restricted key. An agent does not need write access to money."),
    _e("hubspot", "HubSpot", "business", "template",
       "Contacts, deals and the pipeline.",
       auth=BEARER, base_url="https://api.hubapi.com",
       test_path="/crm/v3/objects/contacts?limit=1",
       docs="https://developers.hubspot.com/docs/api/private-apps"),
    _e("intercom", "Intercom", "business", "template",
       "Support conversations and contacts.",
       auth=BEARER, base_url="https://api.intercom.io", test_path="/me",
       docs="https://developers.intercom.com"),
    _e("shopify", "Shopify", "business", "template",
       "Orders, products and customers.",
       auth=HEADER, base_url="https://your-store.myshopify.com/admin/api/2024-10",
       docs="https://shopify.dev/docs/apps/auth/admin-app-access-tokens",
       note="Header must be: X-Shopify-Access-Token: YOUR_TOKEN"),
    _e("quickbooks", "QuickBooks", "business", "planned",
       "Invoices and accounting.",
       note="Needs an OAuth flow."),
    _e("apollo", "Apollo.io", "business", "template",
       "Lead search and enrichment.",
       auth=HEADER, base_url="https://api.apollo.io/v1",
       docs="https://developer.apollo.io"),

    # -- automation ------------------------------------------------------------
    _e("webhook", "Webhook / n8n", "automation", "native",
       "Fire events at any automation platform. Also where reports go.",
       base_url="https://your-n8n/webhook/jarvis",
       note="Set under Settings > Privacy for reports, or add as a connector."),
    _e("zapier", "Zapier", "automation", "template",
       "Trigger a Zap from an agent.",
       base_url="https://hooks.zapier.com/hooks/catch/...",
       docs="https://zapier.com/apps/webhook/integrations"),
    _e("make", "Make (Integromat)", "automation", "template",
       "Trigger a scenario from an agent.",
       base_url="https://hook.eu2.make.com/...",
       docs="https://www.make.com"),
    _e("ifttt", "IFTTT", "automation", "template",
       "Fire an applet event.",
       base_url="https://maker.ifttt.com/trigger", docs="https://ifttt.com/maker_webhooks"),

    # -- data ------------------------------------------------------------------
    _e("supabase", "Supabase", "data", "template",
       "Postgres tables over the REST API.",
       auth=BEARER, base_url="https://your-project.supabase.co/rest/v1",
       docs="https://supabase.com/dashboard/project/_/settings/api"),
    _e("airtable", "Airtable", "data", "template",
       "Records in a base.",
       auth=BEARER, base_url="https://api.airtable.com/v0",
       docs="https://airtable.com/create/tokens"),
    _e("postgres", "PostgreSQL", "data", "planned",
       "Query a database directly.",
       note="Needs a database driver rather than HTTP."),
    _e("mysql", "MySQL / MariaDB", "data", "planned",
       "Query a database directly.",
       note="Needs a database driver rather than HTTP."),
    _e("sqlite", "SQLite", "data", "planned",
       "Query a local database file.",
       note="Reachable today with a shell tool over the sandbox."),

    # -- infrastructure --------------------------------------------------------
    _e("docker", "Docker", "infra", "template",
       "List and control containers through the Docker socket proxy.",
       base_url="http://127.0.0.1:2375", test_path="/containers/json",
       docs="https://docs.docker.com/engine/api/",
       note="Exposing the Docker socket grants root-equivalent control. "
            "Bind it to loopback only."),
    _e("truenas", "TrueNAS SCALE", "infra", "template",
       "Pools, datasets, apps and alerts.",
       auth=BEARER, base_url="https://your-nas/api/v2.0",
       test_path="/system/info", docs="https://www.truenas.com/docs/api/"),
    _e("proxmox", "Proxmox", "infra", "template",
       "Virtual machines and containers.",
       auth=HEADER, base_url="https://your-host:8006/api2/json",
       note="Header must be: Authorization: PVEAPIToken=user@realm!id=secret"),
    _e("portainer", "Portainer", "infra", "template",
       "Container management with a friendlier API than Docker's.",
       auth=HEADER, base_url="https://your-host:9443/api",
       note="Header must be: X-API-Key: YOUR_KEY"),
    _e("homeassistant", "Home Assistant", "infra", "template",
       "Devices and automations.",
       auth=BEARER, base_url="http://homeassistant.local:8123/api",
       test_path="/", docs="https://developers.home-assistant.io/docs/api/rest/"),
    _e("s3", "S3 storage", "infra", "planned",
       "Object storage buckets.",
       note="Needs SigV4 request signing."),
    _e("cloudflare", "Cloudflare", "infra", "template",
       "DNS, tunnels and Workers.",
       auth=BEARER, base_url="https://api.cloudflare.com/client/v4",
       test_path="/user/tokens/verify",
       docs="https://dash.cloudflare.com/profile/api-tokens"),
]

BY_ID = {c["id"]: c for c in CONNECTORS}


def grouped(live: dict | None = None) -> list[dict]:
    """The catalog by category, with live state merged in where we know it.

    `live` maps connector id -> {"configured": bool, "healthy": bool} for the
    natives, so the UI shows what is actually connected rather than a guess.
    """
    live = live or {}
    out = []
    for cid, label, blurb in CATEGORIES:
        items = []
        for c in CONNECTORS:
            if c["category"] != cid:
                continue
            entry = dict(c)
            entry.update(live.get(c["id"], {}))
            entry.setdefault("configured", False)
            entry.setdefault("healthy", False)
            items.append(entry)
        if items:
            out.append({"id": cid, "label": label, "description": blurb,
                        "connectors": items})
    return out


def counts() -> dict:
    n = {"native": 0, "template": 0, "planned": 0}
    for c in CONNECTORS:
        n[c["status"]] = n.get(c["status"], 0) + 1
    n["total"] = len(CONNECTORS)
    n["categories"] = len(CATEGORIES)
    return n


def prefill(cid: str) -> dict | None:
    """Config for a template connector, ready for registry.create()."""
    c = BY_ID.get(cid)
    if not c or c["status"] != "template":
        return None
    return {
        "name": c["name"],
        "base_url": c["base_url"],
        "kind": "http",
        "auth_type": c["auth"]["type"],
        "test_path": c["test_path"],
        "notes": c["note"] or c["description"],
    }
