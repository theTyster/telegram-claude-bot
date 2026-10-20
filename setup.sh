#!/usr/bin/env bash
# Install, configure and start the family Claude bot in the current directory.
#
#   mkdir family-bot && cd family-bot
#   curl -fsSL https://raw.githubusercontent.com/theTyster/telegram-claude-bot/main/setup.sh -o setup.sh
#   bash setup.sh
#
# Run it again inside the install to update. Options:
#   --reconfigure   ask all the questions again and rewrite .env
#   --no-start      set up the code and .env, but don't start the bot
#   -h, --help      show this help
#
# Any setting already in the environment is used without asking, e.g.
#   TELEGRAM_BOT_TOKEN=... FAMILY_CHAT_ID=... OWNER_USER_ID=... \
#   AUTH_MODE=api_key ANTHROPIC_API_KEY=... bash setup.sh
# Other overrides: REPO_URL, BRANCH, BUILD_LOCAL=1 (skip pulling the
# prebuilt image), SKIP_TELEGRAM_CHECK=1, TELEGRAM_API_BASE.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/theTyster/telegram-claude-bot.git}"
BRANCH="${BRANCH:-main}"
TELEGRAM_API_BASE="${TELEGRAM_API_BASE:-https://api.telegram.org}"
SERVICE="family-claude-bot"

RECONFIGURE=0
START=1
for arg in "$@"; do
    case "$arg" in
        --reconfigure) RECONFIGURE=1 ;;
        --no-start) START=0 ;;
        -h | --help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown option: $arg (try --help)" >&2
            exit 2
            ;;
    esac
done

# --- Output helpers ---------------------------------------------------------

if [ -t 1 ]; then
    BOLD=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GREEN=$'\e[32m' YELLOW=$'\e[33m' RESET=$'\e[0m'
else
    BOLD="" DIM="" RED="" GREEN="" YELLOW="" RESET=""
fi
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }
info() { printf '    %s\n' "$*"; }
ok() { printf '    %s✓ %s%s\n' "$GREEN" "$*" "$RESET"; }
warn() { printf '    %s! %s%s\n' "$YELLOW" "$*" "$RESET"; }
die() {
    printf '\n%sError: %s%s\n' "$RED" "$*" "$RESET" >&2
    exit 1
}

HAVE_TTY=0
if { : </dev/tty; } 2>/dev/null; then HAVE_TTY=1; fi

# require_match VAR REGEX "message": with no terminal to re-ask on, a value
# that's set but malformed is fatal.
require_match() {
    if [ "$HAVE_TTY" != 1 ] && [ -n "${!1:-}" ] && ! [[ "${!1}" =~ $2 ]]; then
        die "$1: $3"
    fi
}

# ask VAR "Question" [default] [secret]
# Uses $VAR if it's already set; otherwise prompts on the terminal.
ask() {
    local var="$1" question="$2" default="${3:-}" secret="${4:-}" answer=""
    if [ -n "${!var:-}" ]; then
        return
    fi
    if [ "$HAVE_TTY" != 1 ]; then
        [ -n "$default" ] || die "$var is not set and there's no terminal to ask on."
        printf -v "$var" '%s' "$default"
        return
    fi
    while :; do
        if [ -n "$default" ]; then
            printf '    %s %s[%s]%s: ' "$question" "$DIM" "$default" "$RESET" >/dev/tty
        else
            printf '    %s: ' "$question" >/dev/tty
        fi
        if [ -n "$secret" ]; then
            IFS= read -rs answer </dev/tty
            printf '\n' >/dev/tty
        else
            IFS= read -r answer </dev/tty
        fi
        answer="${answer:-$default}"
        [ -n "$answer" ] && break
    done
    printf -v "$var" '%s' "$answer"
}

confirm() { # confirm "Question" default(y/n)
    local reply="" def="${2:-y}"
    [ "$HAVE_TTY" = 1 ] || { [ "$def" = y ]; return; }
    printf '    %s %s[%s]%s ' "$1" "$DIM" "$([ "$def" = y ] && echo Y/n || echo y/N)" "$RESET" >/dev/tty
    IFS= read -r reply </dev/tty
    reply="${reply:-$def}"
    [[ "$reply" =~ ^[Yy] ]]
}

# Telegram API calls go through python3 (if present) so the token never
# appears in a process list. Prints the JSON "result", or fails.
tg() { # tg METHOD [JSON_PARAMS]
    local params="${2:-}"
    [ -n "$params" ] || params='{}'
    TG_API="$TELEGRAM_API_BASE" TG_TOKEN="$TELEGRAM_BOT_TOKEN" python3 - "$1" "$params" <<'PY'
import json, os, sys, urllib.error, urllib.request
method, params = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    f"{os.environ['TG_API']}/bot{os.environ['TG_TOKEN']}/{method}",
    data=params.encode(), headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=40) as r:
        body = json.load(r)
except urllib.error.HTTPError as e:
    body = json.load(e)
except Exception as e:
    print(f"network: {e}", file=sys.stderr); sys.exit(2)
if not body.get("ok"):
    print(body.get("description", "error"), file=sys.stderr); sys.exit(1)
print(json.dumps(body["result"]))
PY
}

# Wait (up to 2 minutes) for a new message of a given kind and print
# "<id>\t<name>". KIND is "private" (prints the sender) or "group" (the chat).
wait_for_message() {
    TG_API="$TELEGRAM_API_BASE" TG_TOKEN="$TELEGRAM_BOT_TOKEN" python3 - "$1" 2>/dev/null <<'PY'
import json, os, sys, time, urllib.request
kind = sys.argv[1]
api = f"{os.environ['TG_API']}/bot{os.environ['TG_TOKEN']}"
def call(method, **params):
    req = urllib.request.Request(f"{api}/{method}", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.load(r)["result"]
# Skip anything sent before we started waiting.
last = call("getUpdates", offset=-1, timeout=0)
offset = last[-1]["update_id"] + 1 if last else None
deadline = time.time() + 120
while time.time() < deadline:
    for u in call("getUpdates", offset=offset, timeout=20, allowed_updates=["message"]):
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        chat, user = m.get("chat") or {}, m.get("from") or {}
        if kind == "private" and chat.get("type") == "private" and not user.get("is_bot"):
            name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
            found = f"{user['id']}\t{name}"
        elif kind == "group" and chat.get("type") in ("group", "supergroup"):
            found = f"{chat['id']}\t{chat.get('title', '')}"
        else:
            continue
        # Mark it as handled so the bot doesn't answer it when it starts.
        call("getUpdates", offset=offset, timeout=0)
        print(found)
        sys.exit(0)
sys.exit(1)
PY
}

json_field() { # json_field FIELD  (reads a JSON object on stdin)
    python3 -c 'import json,sys; v=json.load(sys.stdin).get(sys.argv[1]); print("" if v is None else str(v).lower() if isinstance(v,bool) else v)' "$1"
}

# --- 1. Prerequisites -------------------------------------------------------

step "Checking prerequisites"
command -v docker >/dev/null || die "Docker isn't installed. See https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 ||
    die "The Docker Compose plugin isn't available ('docker compose'). See https://docs.docker.com/compose/install/"
docker info >/dev/null 2>&1 ||
    die "Can't talk to Docker. Is it running, and is your user in the 'docker' group (or use sudo)?"
command -v git >/dev/null || die "git isn't installed (e.g. 'sudo apt install git')."
HAVE_PY=0
if command -v python3 >/dev/null; then HAVE_PY=1; fi
ok "docker, docker compose and git found"
[ "$HAVE_PY" = 1 ] || warn "python3 not found: skipping the Telegram checks; you'll type the IDs in yourself."

# --- 2. Code ----------------------------------------------------------------

step "Getting the code into $(pwd)"
if [ -d .git ] && [ -f docker-compose.yml ] && git remote get-url origin 2>/dev/null | grep -q "telegram-claude-bot"; then
    info "Existing install found; updating."
    # Follows whichever branch the install was set up from.
    git pull --ff-only -q ||
        die "Couldn't update with 'git pull' (see above). Local changes show up in 'git status'."
    ok "Code is up to date ($(git rev-parse --short HEAD))"
else
    other="$(find . -mindepth 1 -maxdepth 1 ! -name setup.sh -print -quit)"
    [ -z "$other" ] ||
        die "This folder isn't empty (found ${other#./}). Run setup.sh in an empty folder, e.g.:
    mkdir family-bot && cd family-bot && bash /path/to/setup.sh"
    git init -q
    git symbolic-ref HEAD "refs/heads/$BRANCH"
    git remote add origin "$REPO_URL"
    git fetch -q --depth 1 origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH" ||
        die "Couldn't download $REPO_URL (branch $BRANCH)."
    # reset (not checkout) so the setup.sh already here is simply replaced.
    git reset -q --hard "origin/$BRANCH"
    git branch -q --set-upstream-to="origin/$BRANCH"
    chmod +x setup.sh 2>/dev/null || true
    ok "Downloaded ($(git rev-parse --short HEAD))"
fi

# --- 3. Configuration (.env) ------------------------------------------------

configure=1
if [ -f .env ] && [ "$RECONFIGURE" = 0 ]; then
    step "Configuration"
    if confirm "A .env file already exists. Keep it?" y; then
        configure=0
        ok "Keeping .env (run with --reconfigure to change it)"
    fi
fi

if [ "$configure" = 1 ]; then
    step "Telegram bot"
    info "Create a bot with @BotFather (/newbot) if you haven't yet, then paste its token."
    ask TELEGRAM_BOT_TOKEN "Bot token" "" secret
    BOT_USERNAME="YourBot"
    if [ "$HAVE_PY" = 1 ] && [ "${SKIP_TELEGRAM_CHECK:-0}" != 1 ]; then
        err_file="$(mktemp)"
        if me="$(tg getMe 2>"$err_file")"; then
            BOT_USERNAME="$(json_field username <<<"$me")"
            ok "Token works: @$BOT_USERNAME"
            if [ "$(json_field can_read_all_group_messages <<<"$me")" != "true" ]; then
                warn "Privacy mode is ON, so the bot won't see @mentions or forwarded messages."
                info "Fix: @BotFather → /setprivacy → @$BOT_USERNAME → Disable."
                info "If the bot is already in your group, remove it and add it back afterwards."
            else
                ok "Privacy mode is off (needed for @mentions and forwards)"
            fi
        else
            err="$(cat "$err_file")"
            rm -f "$err_file"
            case "$err" in
                network:*) warn "Couldn't reach Telegram to check the token ($err). Continuing." ;;
                *) die "Telegram rejected the token: $err" ;;
            esac
        fi
        rm -f "$err_file"
    fi

    can_detect=0
    if [ "$HAVE_PY" = 1 ] && [ "$HAVE_TTY" = 1 ] && [ "${SKIP_TELEGRAM_CHECK:-0}" != 1 ]; then
        can_detect=1
        # Detection reads Telegram updates itself, which clashes with a running
        # bot; it's started again at the end.
        if [ -n "$(docker compose ps -q "$SERVICE" 2>/dev/null)" ]; then
            docker compose stop "$SERVICE" >/dev/null 2>&1 || true
        fi
        # getUpdates doesn't work while a webhook is set.
        tg deleteWebhook >/dev/null 2>&1 || true
    fi

    step "Your Telegram user id (you get unlimited questions)"
    if [ -z "${OWNER_USER_ID:-}" ] && [ "$can_detect" = 1 ] &&
        confirm "Detect it automatically? (you'll send the bot a DM)" y; then
        info "Now send /start to @$BOT_USERNAME in a private chat. Waiting up to 2 minutes…"
        if found="$(wait_for_message private)"; then
            OWNER_USER_ID="${found%%$'\t'*}"
            ok "Found you: ${found#*$'\t'} ($OWNER_USER_ID)"
        else
            warn "Didn't see a message; enter it by hand (ask @userinfobot)."
        fi
    fi
    require_match OWNER_USER_ID '^[0-9]+$' "a user id is digits only."
    until [[ "${OWNER_USER_ID:-}" =~ ^[0-9]+$ ]]; do
        [ -z "${OWNER_USER_ID:-}" ] || warn "That doesn't look like a user id (digits only)."
        OWNER_USER_ID=""
        ask OWNER_USER_ID "Your user id (from @userinfobot)"
    done

    step "Family group"
    if [ -z "${FAMILY_CHAT_ID:-}" ] && [ "$can_detect" = 1 ] &&
        confirm "Detect the group automatically? (you'll send a message in it)" y; then
        info "Add @$BOT_USERNAME to the family group if it isn't there yet."
        info "Then send this in the group: /claude@$BOT_USERNAME hi"
        info "Waiting up to 2 minutes…"
        if found="$(wait_for_message group)"; then
            if confirm "Use \"${found#*$'\t'}\" (${found%%$'\t'*}) as the family group?" y; then
                FAMILY_CHAT_ID="${found%%$'\t'*}"
                ok "Family group set"
            fi
        else
            warn "Didn't see a group message; enter the id by hand."
        fi
    fi
    require_match FAMILY_CHAT_ID '^-[0-9]+$' "group ids are negative numbers, e.g. -1001234567890."
    until [[ "${FAMILY_CHAT_ID:-}" =~ ^-[0-9]+$ ]]; do
        [ -z "${FAMILY_CHAT_ID:-}" ] || warn "Group ids are negative numbers, e.g. -1001234567890."
        FAMILY_CHAT_ID=""
        ask FAMILY_CHAT_ID "Family group id (negative number)"
    done

    step "How Claude is paid for"
    if [ -z "${AUTH_MODE:-}" ]; then
        info "1) Anthropic API key: pay per use, roughly \$0.01–\$0.15 a question (recommended)"
        info "2) Your Claude Pro/Max subscription, via 'claude setup-token'"
        choice=""
        ask choice "Choose 1 or 2" 1
        case "$choice" in
            2) AUTH_MODE=subscription ;;
            *) AUTH_MODE=api_key ;;
        esac
    fi
    case "$AUTH_MODE" in
        api_key)
            info "Create a key at https://console.anthropic.com and set a monthly spend limit there."
            ask ANTHROPIC_API_KEY "Anthropic API key" "" secret
            [[ "$ANTHROPIC_API_KEY" == sk-ant-* ]] || warn "That doesn't look like an Anthropic key (sk-ant-…)."
            CREDENTIAL_LINE="ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
            ;;
        subscription)
            warn "Your subscription is for your own use. Anthropic's terms don't allow sharing an"
            warn "account, and family members using it through a bot can count as sharing, which"
            warn "could put your account at risk. API-key mode avoids this."
            # Unattended runs that set AUTH_MODE=subscription have already chosen.
            if [ "$HAVE_TTY" = 1 ]; then
                confirm "Use your subscription anyway?" n || die "Re-run and choose option 1 (API key)."
            fi
            info "On a computer with Claude Code, run: claude setup-token"
            ask CLAUDE_CODE_OAUTH_TOKEN "Paste the token" "" secret
            CREDENTIAL_LINE="CLAUDE_CODE_OAUTH_TOKEN=$CLAUDE_CODE_OAUTH_TOKEN"
            ;;
        *) die "AUTH_MODE must be api_key or subscription (got '$AUTH_MODE')." ;;
    esac

    step "A few settings (Enter keeps the default)"
    detected_tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    [ -n "$detected_tz" ] || detected_tz="$(cat /etc/timezone 2>/dev/null || true)"
    [ -n "$detected_tz" ] || detected_tz="$(readlink /etc/localtime 2>/dev/null | sed 's#.*/zoneinfo/##')"
    ask TIMEZONE "Timezone for the daily reset" "${detected_tz:-America/Chicago}"
    require_match DAILY_LIMIT '^[0-9]+$' "must be a whole number."
    until [[ "${DAILY_LIMIT:-}" =~ ^[0-9]+$ ]]; do
        DAILY_LIMIT=""
        ask DAILY_LIMIT "Questions per person per day" 5
    done
    ask MODEL "Model (sonnet, opus or haiku)" sonnet

    if [ -f .env ]; then
        (umask 077 && cp .env .env.bak)
        info "Saved the old settings to .env.bak"
    fi
    (
        umask 077
        cat >.env <<EOF
# Written by setup.sh on $(date '+%Y-%m-%d %H:%M'). Re-run ./setup.sh --reconfigure to change.
# Keep this file private: it holds your bot token and Claude credential.
TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
FAMILY_CHAT_ID=$FAMILY_CHAT_ID
OWNER_USER_ID=$OWNER_USER_ID

AUTH_MODE=$AUTH_MODE
$CREDENTIAL_LINE

MODEL=$MODEL
DAILY_LIMIT=$DAILY_LIMIT
TIMEZONE=$TIMEZONE
# More settings (and their defaults) are listed in docker-compose.yml.
EOF
    )
    chmod 600 .env
    ok "Wrote .env"
fi

if [ "$START" = 0 ]; then
    step "Done (not started because of --no-start)"
    info "Start it with: docker compose up -d"
    exit 0
fi

# --- 4. Image ---------------------------------------------------------------

step "Getting the Docker image"
if [ "${BUILD_LOCAL:-0}" != 1 ] && docker compose pull -q "$SERVICE" 2>/dev/null; then
    ok "Pulled the prebuilt image"
else
    [ "${BUILD_LOCAL:-0}" = 1 ] || info "No prebuilt image available (it may be private or not published yet); building it here."
    docker compose build "$SERVICE" || die "The image build failed (see above)."
    ok "Built the image"
fi

# --- 5. Start ---------------------------------------------------------------

step "Starting the bot"
if ! out="$(docker compose up -d "$SERVICE" 2>&1)"; then
    printf '%s\n' "$out" | sed 's/^/    /'
    die "docker compose couldn't start the bot (see above)."
fi
for _ in $(seq 1 60); do
    logs="$(docker compose logs --no-log-prefix "$SERVICE" 2>&1 || true)"
    if grep -q "Running as @" <<<"$logs"; then
        ok "$(grep -o 'Running as @[A-Za-z0-9_]*' <<<"$logs" | tail -1)"
        cat <<EOF

${GREEN}${BOLD}All set!${RESET} Try ${BOLD}/claude hello${RESET} in the family group.

    Logs:     docker compose logs -f
    Update:   ./setup.sh   (or: docker compose pull && docker compose up -d)
    Settings: ./setup.sh --reconfigure, or edit .env then: docker compose up -d
    Stop:     docker compose down
EOF
        exit 0
    fi
    if grep -qE "Configuration problem|ERROR: " <<<"$logs"; then
        grep -E "Configuration problem|ERROR: |Give the container|set EGRESS_FIREWALL" <<<"$logs" | sed 's/^/    /'
        docker compose stop "$SERVICE" >/dev/null 2>&1 || true
        die "The bot couldn't start. Fix the problem above, then run ./setup.sh again."
    fi
    sleep 1
done

warn "The bot hasn't connected to Telegram within a minute. Last log lines:"
docker compose logs --no-log-prefix --tail 10 "$SERVICE" 2>&1 | sed 's/^/    /'
info "It keeps retrying in the background; watch it with: docker compose logs -f"
exit 1
