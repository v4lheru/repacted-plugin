"""
Location: pact-plugin/tests/test_scrub_webhook_basic.py
Summary: Boundary rows for the webhook and Basic-auth secret classes in hooks/shared/scrub.py: webhook URLs
         (Slack, Discord, Microsoft Teams, Google Chat), the `Authorization: Basic` header value
         and a GCP private_key_id. Real-shaped values are caught and redacted; documentation
         near-misses (a bare host, placeholder paths, a placeholder Basic pair, 'Basic' in prose,
         a git SHA) are not. The promote (R5) row lives in
         test_distill_promote.py.
Used by: pytest. Every credential-shaped value is generated at import from a fixed seed and is fake.
"""
from __future__ import annotations

import base64
import random
import string

import pytest

from shared.scrub import SECRET_ONLY_KINDS, scrub_text, secret_kind

_RND = random.Random(11)


def _rand(n: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    return "".join(_RND.choice(alphabet) for _ in range(n))


HEX = "0123456789abcdef"
SLACK_HOST, DISCORD_HOST = "hooks.slack" + ".com", "discord" + ".com"
SLACK_TOKEN = _rand(24)
SLACK_HOOK = f"https://{SLACK_HOST}/services/T{_rand(8).upper()}/B{_rand(8).upper()}/{SLACK_TOKEN}"
DISCORD_TOKEN = _rand(68, string.ascii_letters + string.digits + "_-")
OFFICE_PATH = (f"webhookb2/{_rand(8, HEX)}-{_rand(4, HEX)}-{_rand(4, HEX)}@{_rand(8, HEX)}"
               f"/IncomingWebhook/{_rand(32, HEX)}/{_rand(8, HEX)}")
CHAT_QUERY = f"key=AIza{_rand(35)}&token={_rand(43)}"
BASIC_OPAQUE = _rand(28)                                                  # the probe's value
BASIC_TOKEN_USER = base64.b64encode(f"{_rand(32)}:".encode()).decode()    # an API key as the user
BASIC_USER_PW = base64.b64encode(f"deploy:{_rand(14)}".encode()).decode()
KEY_ID = _rand(40, HEX)
LETTERS_TOKEN = _rand(24, string.ascii_letters)                         # no digit

CAUGHT = {
    "slack-services": (f"post to {SLACK_HOOK} after deploy", SLACK_TOKEN, "webhook-url"),
    "slack-workflows": (f"https://{SLACK_HOST}/workflows/T{_rand(8).upper()}/A{_rand(9).upper()}/"
                        f"{_rand(18, string.digits)}/{SLACK_TOKEN}", SLACK_TOKEN, "webhook-url"),
    "slack-letters-only": (f"https://{SLACK_HOST}/services/T{_rand(8).upper()}/B{_rand(8).upper()}/"
                           f"{LETTERS_TOKEN}", LETTERS_TOKEN, "webhook-url"),   # a review probe
    "discord": (f"https://{DISCORD_HOST}/api/webhooks/{_rand(18, string.digits)}/{DISCORD_TOKEN}",
                DISCORD_TOKEN, "webhook-url"),
    "discordapp-v10": (f"https://ptb.discordapp.com/api/v10/webhooks/{_rand(19, string.digits)}/"
                       f"{DISCORD_TOKEN}", DISCORD_TOKEN, "webhook-url"),
    "teams": (f"https://contoso.webhook.office.com/{OFFICE_PATH}", OFFICE_PATH, "webhook-url"),
    "google-chat": (f"https://chat.googleapis.com/v1/spaces/AAAA{_rand(7)}/messages?{CHAT_QUERY}",
                    CHAT_QUERY, "webhook-url"),
    "basic-opaque": (f"user: Authorization: Basic {BASIC_OPAQUE}", BASIC_OPAQUE, "basic-auth"),
    "basic-token-user": (f"-H 'Authorization: Basic {BASIC_TOKEN_USER}'", BASIC_TOKEN_USER, "basic-auth"),
    "basic-user-password": (f"Authorization: Basic {BASIC_USER_PW}", BASIC_USER_PW, "basic-auth"),
    "gcp-json": (f'  "private_key_id": "{KEY_ID}",', KEY_ID, "gcp-key-id"),
    "gcp-yaml": (f"private_key_id: {KEY_ID}", KEY_ID, "gcp-key-id"),
}

NOT_CAUGHT = {
    "slack-bare-host": f"Create an incoming webhook at https://{SLACK_HOST}/ first.",
    "slack-docs-path": f"Incoming webhooks live under {SLACK_HOST}/services for each workspace.",
    "slack-placeholder": f"https://{SLACK_HOST}/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
    "discord-placeholder": f"https://{DISCORD_HOST}/api/webhooks/123456789012345678/your_webhook_token"
                           "_goes_in_this_part_of_the_url_here",
    "discord-no-token": f"https://{DISCORD_HOST}/api/webhooks/123456789012345678",
    "teams-placeholder": "https://contoso.webhook.office.com/webhookb2/.../IncomingWebhook/.../...",
    "chat-placeholder": "https://chat.googleapis.com/v1/spaces/SPACE/messages?key=KEY&token=TOKEN",
    "basic-in-prose": "The server falls back to Basic authentication when no token is configured.",
    "basic-scheme-name": "Authorization: Basic is defined in RFC 7617.",
    "basic-placeholder-pair": "Authorization: Basic " + base64.b64encode(b"user:password").decode(),
    "basic-angle-placeholder": "Authorization: Basic <base64-credentials>",
    "basic-xxx": "Authorization: Basic xxxxxxxxxxxxxxxxxxxxxxxx",
    "git-sha": f"Fixed in commit {_rand(40, HEX)} on main.",
    "bare-40-hex-token": f"the token is {_rand(40, HEX)}",
    "gcp-placeholder": 'private_key_id: "' + "0" * 40 + '"',
}


@pytest.mark.parametrize("name", sorted(CAUGHT))
def test_real_shaped_value_is_caught_and_redacted(name):
    text, secret, kind = CAUGHT[name]
    assert kind in SECRET_ONLY_KINDS
    assert secret_kind(text) == kind
    scrubbed = scrub_text(text, kinds=SECRET_ONLY_KINDS)
    assert secret not in scrubbed and f"[REDACTED:{kind}]" in scrubbed


@pytest.mark.parametrize("name", sorted(NOT_CAUGHT))
def test_documentation_near_miss_is_not_caught(name):
    text = NOT_CAUGHT[name]
    assert secret_kind(text) is None
    assert scrub_text(text, kinds=SECRET_ONLY_KINDS) == text

