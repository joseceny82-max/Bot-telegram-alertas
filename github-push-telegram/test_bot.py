import hashlib
import hmac
import json

from aiohttp.test_utils import TestClient, TestServer
import pytest

import bot


def test_signature_and_format(monkeypatch):
    body = b'{"ok":true}'
    signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert bot.verify_signature(body, signature, "secret")
    assert not bot.verify_signature(body, signature, "wrong")

    text = bot.format_push({
        "ref": "refs/heads/main",
        "compare": "https://github.com/acme/app/compare/a...b",
        "repository": {"full_name": "acme/app"},
        "sender": {"login": "ana"},
        "commits": [{"id": "abcdef123456", "message": "Fix <bug>", "url": "https://github.com/acme/app/commit/a"}],
    })
    assert "acme/app" in text and "main" in text and "Fix &lt;bug&gt;" in text
    assert "abcdef1" in text


@pytest.mark.asyncio
async def test_webhook_filters_and_sends(monkeypatch):
    monkeypatch.setattr(bot, "GITHUB_WEBHOOK_SECRET", "secret")
    monkeypatch.setattr(bot, "GITHUB_REPOS", {"acme/app"})
    monkeypatch.setattr(bot, "WATCHED_BRANCHES", {"main"})

    class FakeTelegram:
        def __init__(self):
            self.messages = []

        async def send(self, text):
            self.messages.append(text)
            return True

        async def close(self):
            pass

    telegram = FakeTelegram()
    app = bot.create_app(telegram)
    async with TestClient(TestServer(app)) as client:
        payload = {
            "ref": "refs/heads/main",
            "repository": {"full_name": "acme/app"},
            "sender": {"login": "ana"},
            "commits": [{"id": "abcdef1", "message": "Update", "url": "https://github.com/c"}],
        }
        raw = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        response = await client.post("/github/webhook", data=raw, headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-1",
        })
        assert response.status == 200
        assert len(telegram.messages) == 1

        duplicate = await client.post("/github/webhook", data=raw, headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
            "X-GitHub-Delivery": "delivery-1",
        })
        assert (await duplicate.json())["duplicate"] is True
        assert len(telegram.messages) == 1


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected(monkeypatch):
    monkeypatch.setattr(bot, "GITHUB_WEBHOOK_SECRET", "secret")
    class FakeTelegram:
        async def send(self, _text):
            return True

        async def close(self):
            pass

    telegram = FakeTelegram()
    app = bot.create_app(telegram)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/github/webhook", data=b"{}", headers={
            "X-Hub-Signature-256": "sha256=bad",
            "X-GitHub-Event": "push",
        })
        assert response.status == 401
