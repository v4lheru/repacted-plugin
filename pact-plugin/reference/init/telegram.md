# Init: Telegram setup

> Loaded by `/repacted:init telegram` ([init.md](../../commands/init.md)). Run the steps as written.

<!-- Maintainers: the pact-telegram config path below matches a default-root pin in code; do not migrate it to the config-dir seam. -->

Walk the user through configuring the pact-telegram bridge. Use `AskUserQuestion` at each step and Bash for automation.

**Tell the user first**: the bridge connects Claude Code sessions to a Telegram bot they own. Sessions notify them on finish or blocker, ask questions and wait for a text or voice reply. It is opt-in: nothing starts or installs until this setup ends, and it can be switched off (Step 10).

**Security**: NEVER echo, log, or display a bot token or API key, and never ask the user to paste one into the chat. The user writes secrets into the config file; read them only through `ENV_GET`, which hands a key to one command without printing it. Curl always runs with `-s`.

Below, `ENV_GET` is `"${CLAUDE_PLUGIN_ROOT}/bin/env-get"`, `TG_DIR` is `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pact-telegram"` (where the server reads its config), `TG_ENV` is `TG_DIR/.env` and `DEPS` is `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m telegram.deps`.

## Step 1: Check Existing Configuration

```bash
test -f TG_ENV && echo "EXISTS" || echo "MISSING"
```

- **EXISTS**: ask "(A) reconfigure from scratch, (B) test the existing setup, or (C) cancel?" A: `mv TG_ENV TG_ENV.bak`, go to Step 2. B: Step 9. C: stop.
- **MISSING**: Step 2.

## Step 2: Create a Bot and Store Its Token

1. Create the file, empty and private:
   ```bash
   mkdir -p TG_DIR && touch TG_ENV && chmod 600 TG_ENV
   ```
2. Tell the user: "Message **@BotFather** in Telegram, send `/newbot` and follow the prompts. Do not paste the token here: open `<TG_ENV, expanded>` in your editor, add `TELEGRAM_BOT_TOKEN=<token>` and save."
3. Confirm with `AskUserQuestion`.

## Step 3: Validate Token Format

```bash
ENV_GET TG_ENV TELEGRAM_BOT_TOKEN -- python3 -c 'import os, re, sys; sys.exit(0 if re.fullmatch(r"\d+:[A-Za-z0-9_-]{35}", os.environ["TELEGRAM_BOT_TOKEN"]) else 1)' && echo VALID || echo INVALID
```

INVALID or a missing key: ask the user to fix the line; retry up to 2 more times, then stop with an error.

## Step 4: Detect Chat ID

1. Ask the user to send `/start` to the bot, and confirm (`AskUserQuestion`).
2. Read the chat IDs (only they are printed):
   ```bash
   ENV_GET TG_ENV TELEGRAM_BOT_TOKEN -- sh -c 'curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"' | python3 -c 'import json, sys; d = json.load(sys.stdin); print(sorted({u["message"]["chat"]["id"] for u in d.get("result", []) if "message" in u}))'
   ```
3. Empty list: ask for another message and retry (3 attempts, 3 s apart). Then ask: "Detected chat ID: **{chat_id}**. Is this correct?"

## Step 5: Optional Voice Transcription

Offer: "To transcribe voice notes (Whisper API, ~$0.006/min), add `OPENAI_API_KEY=<key>` to the same file. Skip if not wanted." If added, check it without printing:

```bash
ENV_GET TG_ENV OPENAI_API_KEY -- python3 -c 'import os, sys; sys.exit(0 if os.environ["OPENAI_API_KEY"].startswith("sk-") else 1)' && echo VALID || echo INVALID
```

## Step 6: Write Configuration

```bash
printf 'TELEGRAM_CHAT_ID=%s\nPACT_TELEGRAM_MODE=passive\n' '<chat_id>' >> TG_ENV && chmod 600 TG_ENV && ENV_GET --keys TG_ENV
git -C TG_DIR rev-parse 2>/dev/null
```

Expect each key name once. If `git` exits 0, warn that the config directory is inside a git repository and credentials could be committed.

## Step 7: Install Python Dependencies

`DEPS install` installs `mcp` and `httpx` (pinned in `telegram/requirements.txt`): to the user site-packages when the plugin declares the server, else to `${CLAUDE_PLUGIN_DATA}/telegram-deps`, which survives plugin updates. On failure show the error and suggest `pip install mcp httpx`.

## Step 8: Enable MCP Server

Run `DEPS mode`.

**`plugin`** (the plugin declares the server):
1. Check for an old user-scope registration:
   ```bash
   claude mcp list 2>/dev/null | grep -q "pact-telegram" && echo "OLD_REGISTRATION" || echo "CLEAN"
   ```
   If **OLD_REGISTRATION**: tell the user it would run a duplicate server and run `claude mcp remove pact-telegram`.
2. Tell the user: "Run `/plugin`, find **repacted** in the Installed list, expand it, and toggle **pact-telegram MCP** on. You can toggle it off from the same view."
3. Confirm with `AskUserQuestion`.

**`user`** (opt-in install, the plugin starts no server):
1. `DEPS register` registers the server at user scope through `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/protocols/pact-plugin"`, the link each session start keeps on the current plugin version, so it survives updates. If it reports the link missing, ask for a new session and rerun.
2. Verify: `claude mcp get pact-telegram 2>&1 | grep -E 'Scope|Status'` shows `✔ Connected`; if not, rerun Step 7.
3. Tell the user the tools appear after a restart.

## Step 9: Send Test Notification

```bash
ENV_GET TG_ENV TELEGRAM_BOT_TOKEN,TELEGRAM_CHAT_ID -- sh -c 'curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" -H "Content-Type: application/json" -d "{\"chat_id\": \"${TELEGRAM_CHAT_ID}\", \"text\": \"pact-telegram setup complete!\"}"' | python3 -c 'import json, sys; print("ok" if json.load(sys.stdin).get("ok") else "failed")'
```

Expect `ok`, then ask whether the message arrived. If not, check token, chat ID and connectivity; offer to retry or reconfigure.

## Step 10: Finalize

Tell the user:

> **Setup complete!**
> - **Passive mode** (default): a notification when a session ends, summarizing what was done.
> - **Active mode**: the orchestrator can ask you questions and wait for your reply. Set `PACT_TELEGRAM_MODE=active` in `TG_ENV`.
> - **MCP tools**: `telegram_notify`, `telegram_ask`, `telegram_check_replies`, `telegram_status`.
> - **Multi-session**: each message names its project; swipe-reply (mobile) or click-reply (desktop) so the answer reaches the right session.
> - **Toggle**: plugin-declared server: `/plugin` → repacted → pact-telegram MCP. Opt-in registration: `claude mcp remove pact-telegram -s user` to stop, rerun this setup to restart.
