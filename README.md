# Family Claude Bot

A Telegram bot for your family group chat, powered by Claude.

- **Ask it anything** with `/claude …`, an `@mention`, or by replying to one of its messages.
- **Sees photos:** attach one, or reply to someone's photo while asking.
- **Searches the web and does research**, citing its sources.
- **Sends pictures** it finds online when they help.
- **Forward messages in, then ask** ("summarize these", "is this true?").
- **5 questions per person per day** (configurable). You, the owner, are unlimited.
- **Only works in your family group.** It leaves any other group it's added to and turns away DMs from anyone but you.
- **Doesn't store your chats.** The only thing saved to disk is a count of questions per person per day.

Runs as a single Docker Compose service on your own machine. It needs no open ports or public URL.

---

## How it works

```
Telegram ──(long polling)──> bot (Python) ──> Claude Agent SDK ──> Claude
                              │                (WebSearch + WebFetch only)
                              └── SQLite: daily usage counts only
```

For each question, the bot builds the context from what's part of that message only:

1. the question (and any photo on it),
2. the message it replies to, or just the quoted part (text and photo), including one of the bot's own earlier answers,
3. messages **you** forwarded to the group in the last 2 minutes (kept in memory only, then discarded).

It sends that to Claude through the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview). Claude can only use **WebSearch** and **WebFetch**. It can't run commands, read files or touch anything else on your server.

## Setup

### Quick install

You need Docker with the Compose plugin, plus git. Make an empty folder and run the setup script in it:

```sh
mkdir family-bot && cd family-bot
curl -fsSL https://raw.githubusercontent.com/theTyster/telegram-claude-bot/main/setup.sh -o setup.sh
bash setup.sh
```

The script:
- downloads the bot into that folder
- checks your bot token and warns you if privacy mode is still on
- finds your user id and the family group's id for you: you send the bot a DM and post one message in the group
- asks about the API key or subscription token, timezone, daily limit and model
- writes `.env`, then starts the bot with Docker Compose

To update later, run `./setup.sh` again. To change settings, run `./setup.sh --reconfigure`.

The steps below are the same thing done by hand.

### 1. Create the bot

1. In Telegram, talk to [@BotFather](https://t.me/BotFather) → `/newbot`, and copy the **token**.
2. `/setprivacy` → pick your bot → **Disable**. The bot needs this to see `@mentions` and forwarded messages; Telegram only delivers `/commands` and replies to bots in privacy mode. It still only *reacts* to its triggers and stores nothing.
3. Add the bot to your family group. If it was already in the group before you changed privacy mode, remove it and add it again so the change takes effect.
4. Optional, after adding it: `/setjoingroups` → **Disable**, so nobody can add it anywhere else.

### 2. Find your IDs

- **Your user id (`OWNER_USER_ID`):** message [@userinfobot](https://t.me/userinfobot).
- **The family group id (`FAMILY_CHAT_ID`):** start the bot (step 4) with `FAMILY_CHAT_ID=0`, then send `/claude hi` in the family group. The bot leaves and logs `Leaving chat -…` (a negative number, often starting with -100). Put that number in `.env`, restart, and add the bot back to the group.

### 3. Choose how Claude is paid for

Set `AUTH_MODE` in `.env` and fill in the matching credential.

| `AUTH_MODE` | Credential | Cost |
|---|---|---|
| `api_key` (default) | `ANTHROPIC_API_KEY` from [console.anthropic.com](https://console.anthropic.com) | Pay per use. |
| `subscription` | `CLAUDE_CODE_OAUTH_TOKEN`, made by running `claude setup-token` on your computer | Uses your Claude Pro/Max subscription. |

> ⚠️ **About `subscription` mode.** It works, but your Claude subscription is for *your* use. Anthropic's consumer terms don't allow sharing an account, and letting family members use it through a bot can count as sharing. That could put your account at risk. `api_key` mode is fully within the terms. The choice is yours; switching is a one-line change.

**Rough API-key costs (Sonnet):** a simple question costs about $0.01–$0.03. A question that needs several searches and page reads costs about $0.05–$0.15. Five people using all 5 questions every day would add up to roughly $25–$110/month; normal use is usually much less. Guardrails:
- Each question stops at `MAX_COST_PER_CALL_USD` (default $0.50).
- Set a **monthly spend limit** in the Anthropic Console.
- `MODEL=haiku` is cheaper still.

### 4. Configure and start

```sh
git clone https://github.com/thetyster/telegram-claude-bot.git
cd telegram-claude-bot
cp .env.example .env && chmod 600 .env    # fill it in
docker compose up -d
docker compose logs -f                    # look for "Running as @YourBot"
```

That's it. Try `/claude hello` in the family group.

## Using it

| In the family group | What happens |
|---|---|
| `/claude what's a good lasagna recipe?` | Answers (counts as 1 question) |
| `@YourBot is it going to rain Saturday?` | Same as `/claude` |
| Reply to the bot's answer: `make it vegetarian` | Follow-up; it sees its previous answer |
| Reply to Grandma's photo: `/claude what flower is this?` | Looks at that photo |
| Forward 5 messages, then `/claude summarize` | Summarizes the forwarded messages |
| `/usage` | How many questions you have left today (free) |
| `/help` | How to use the bot (free) |

**Rules:**
- Questions reset at midnight in `TIMEZONE`.
- If a question fails or times out, it doesn't count.
- Each person can have one question running at a time.

You (the owner) can also DM the bot. Anything you send there is a question, which is handy for testing.

## Updating

`docker-compose.yml` is the single place to start, configure and update the bot.

```sh
docker compose pull && docker compose up -d     # prebuilt image from GHCR
# or, building from source:
git pull && docker compose up -d --build
```

Usage counts live in the `bot-data` volume and survive updates.

The prebuilt image is published to `ghcr.io/thetyster/telegram-claude-bot` by `.github/workflows/docker-publish.yml` on every push to `main`, for amd64 and arm64. Before `docker compose pull` will work, do one of these:
- make the package public: GitHub → your profile → Packages → telegram-claude-bot → Package settings,
- or run `docker login ghcr.io` on the server.

Until then, use the `--build` route.

## Settings

Secrets go in `.env`. Everything else has a default in `docker-compose.yml`, and you can override any of them by adding the same name to `.env`.

| Setting | Default | |
|---|---|---|
| `AUTH_MODE` | `api_key` | `api_key` or `subscription` |
| `MODEL` | `sonnet` | `sonnet`, `opus`, `haiku`, or a full model id |
| `DAILY_LIMIT` | `5` | questions per person per day (owner exempt) |
| `TIMEZONE` | `America/Chicago` | when the daily limit resets |
| `MAX_COST_PER_CALL_USD` | `0.50` | API-key mode: stop a single question past this cost; `0` turns it off |
| `MAX_TURNS` | `12` | maximum research steps per question |
| `CALL_TIMEOUT_SECONDS` | `300` | give up after this long (and refund the question) |
| `MAX_CONCURRENT_CALLS` | `3` | questions running at once across everyone |
| `FORWARD_BUFFER_SECONDS` | `120` | how long forwarded messages wait (in memory) for a question |
| `FORWARD_BUFFER_MAX_MESSAGES` | `20` | per person |
| `TRANSPORT` | `polling` | `polling`, or `webhook` (see below) |
| `EGRESS_FIREWALL` | `1` | block the bot from reaching private/LAN addresses |
| `LOG_LEVEL` | `INFO` | |

## Security notes

The container is locked down in `docker-compose.yml`:

- **Claude's tools are WebSearch and WebFetch only.** There's no shell, no file access and no other tools. Web pages and forwarded messages are marked as information, not instructions.
- **No LAN access.** At start-up the container blocks all private address ranges, so a malicious web page can't steer WebFetch at your router, NAS or other machines. It needs the `NET_ADMIN` capability once at start-up. After that the bot runs as an unprivileged user with **no** capabilities. If your host can't do this, set `EGRESS_FIREWALL=0` and firewall the container another way.
- **Read-only filesystem.** Only `/data` (usage counts) and a small `/tmp` are writable. Claude Code's session transcripts are turned off.
- **No exposed ports** in polling mode.
- **Secrets:**
  - Your `.env` is read once, and the secrets are removed from the process environment.
  - The Claude subprocess only receives the one credential it needs.
  - Images Claude suggests are sent to Telegram as URLs, so Telegram downloads them, not your server.

## Moving to Cloudflare later

The same image can run in a [Cloudflare Sandbox](https://developers.cloudflare.com/sandbox/) container. The pieces are already in place:

- **`TRANSPORT=webhook`** starts an HTTP server on port 8080 (`POST /telegram`, `GET /healthz`). It checks Telegram's `X-Telegram-Bot-Api-Secret-Token` header against `TELEGRAM_WEBHOOK_SECRET`. Set `WEBHOOK_PUBLIC_URL` and the bot registers the webhook with Telegram itself.
- **Usage storage** sits behind the small `UsageStore` interface in `bot/usage.py`. A container's disk is temporary on Cloudflare, so you'd add a store backed by D1 or a Durable Object and use it in `bot/main.py`.
- **`EGRESS_FIREWALL=0`**: use the Sandbox's outbound controls instead.
- **Build `linux/amd64`**, which the publish workflow already does.

What you'd add:
- the Workers Paid plan ($5/month),
- a Worker plus a Sandbox Durable Object that starts this image and forwards Telegram's webhook to port 8080,
- the D1-backed usage store.

## Development

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Layout:

| File | What it does |
|---|---|
| `bot/gate.py` | Who may call the bot and what counts as a call (pure functions) |
| `bot/context.py` | Reply, quote and forward context; the in-memory forward buffer |
| `bot/usage.py` | Daily limits (SQLite, behind `UsageStore`) |
| `bot/agent.py` | Claude Agent SDK options, system prompt, image extraction |
| `bot/core.py` | Ties it together; independent of polling vs webhook |
| `bot/transport.py` | Polling loop and webhook server |
| `bot/telegram_client.py` | Minimal Telegram Bot API client |
| `bot/telegram_fmt.py` | Markdown → Telegram HTML, splitting long answers |

### Manual checklist after deploying

- [ ] `/claude hello` in the group answers, ending with "(4 of 5 questions left today)"
- [ ] `@YourBot …` and a reply to the bot's answer both work
- [ ] Reply to a photo with `/claude what is this?`: it describes the photo
- [ ] `/claude show me a picture of a pangolin`: a photo arrives
- [ ] Forward a couple of messages, then `/claude summarize`: it summarizes them
- [ ] A family member's 6th question of the day is refused; yours never are
- [ ] A DM from someone else gets "I only answer in the family group chat"
- [ ] Adding the bot to another group makes it leave
