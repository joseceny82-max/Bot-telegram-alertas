import asyncio
import hashlib
import hmac
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

import bot
from railway_events import DEPLOYMENT_EVENTS, deployment, format_deployment


PROJECT = "11111111-1111-4111-8111-111111111111"
ENVIRONMENT = "22222222-2222-4222-8222-222222222222"
SERVICE = "33333333-3333-4333-8333-333333333333"
RELEASE = "44444444-4444-4444-8444-444444444444"
SECRET = "test-secret-" + "x" * 32


def payload(event_type="Deployment.deployed"):
    return {
        "type": event_type,
        "details": {"source": "CLI", "commitAuthor": "Farid", "commitMessage": "Login <Google>"},
        "resource": {
            "project": {"id": PROJECT, "name": "Silent"},
            "environment": {"id": ENVIRONMENT, "name": "production", "isEphemeral": False},
            "service": {"id": SERVICE, "name": "silent-portal"},
            "deployment": {"id": RELEASE},
        },
    }


class Telegram:
    def __init__(self, outcomes=None, gate=None):
        self.messages = []
        self.outcomes = list(outcomes or [])
        self.gate = gate
        self.entered = asyncio.Event()

    async def send(self, message):
        self.messages.append(message)
        self.entered.set()
        if self.gate:
            await self.gate.wait()
        return self.outcomes.pop(0) if self.outcomes else True

    async def close(self):
        pass


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(bot, "RAILWAY_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(bot, "RAILWAY_PROJECT_IDS", {PROJECT})
    monkeypatch.setattr(bot, "RAILWAY_ENVIRONMENTS", {"production"})


@pytest.mark.parametrize("event_type", DEPLOYMENT_EVENTS)
def test_format_terminal_event(event_type):
    event, reason = deployment(payload(event_type), {PROJECT}, {"production"})
    assert not reason
    message = format_deployment(event)
    assert "Farid" in message and "Silent" in message and "CLI" in message
    assert "Login &lt;Google&gt;" in message
    assert PROJECT in message and RELEASE in message


def test_bounded_html_and_manual_deployment_without_author():
    data = payload()
    data["resource"]["project"]["name"] = "<>&" * 3000
    data["resource"]["service"]["name"] = "<>&" * 3000
    data["details"]["commitAuthor"] = "<>&" * 3000
    data["details"]["commitMessage"] = "<>&" * 3000
    event, _ = deployment(data, {PROJECT}, {"production"})
    assert len(format_deployment(event)) < 4096
    data["details"] = {"source": "CLI"}
    event, _ = deployment(data, {PROJECT}, {"production"})
    assert "Farid" not in format_deployment(event), "No inventar el autor de un despliegue manual"


def test_empty_commit_message_can_still_be_notified():
    result = bot.format_push({"ref": "refs/heads/main", "repository": {"full_name": "acme/app"},
                              "commits": [{"id": "abc1234", "message": "", "author": {"name": "Farid"}}]})
    assert "(sin mensaje)" in result and "Autor: Farid" in result


@pytest.mark.asyncio
async def test_auth_filtering_and_malformed_requests(configured):
    telegram = Telegram()
    async with TestClient(TestServer(bot.create_app(telegram))) as client:
        assert (await client.post("/railway/webhook", json=payload())).status == 401
        assert (await client.post("/railway/webhook", json=payload(), headers={"Authorization": "Bearer wrong"})).status == 401
        headers = {"Authorization": "Bearer " + SECRET}
        assert (await client.post("/railway/webhook", json=[], headers=headers)).status == 400
        assert (await client.post("/railway/webhook", data="{", headers={**headers, "Content-Type": "application/json"})).status == 400
        assert (await client.post("/railway/webhook", json={"type": "Deployment.deployed"}, headers=headers)).status == 400
        for event_type in ["Deployment.building", "Webhook.test", []]:
            r = await client.post("/railway/webhook", json={"type": event_type}, headers=headers)
            assert (await r.json())["ignored"] == "event"
        denied = payload()
        denied["resource"]["project"]["id"] = "55555555-5555-4555-8555-555555555555"
        assert (await (await client.post("/railway/webhook", json=denied, headers=headers)).json())["ignored"] == "project"
        denied = payload()
        denied["resource"]["environment"]["name"] = "preview"
        assert (await (await client.post("/railway/webhook", json=denied, headers=headers)).json())["ignored"] == "environment"
        denied["resource"]["environment"].update(name="production", isEphemeral=True)
        assert (await (await client.post("/railway/webhook", json=denied, headers=headers)).json())["ignored"] == "environment"
        denied = payload()
        denied["resource"]["deployment"]["id"] = 'bad\"><a href="evil"'
        assert (await client.post("/railway/webhook", json=denied, headers=headers)).status == 400
    assert not telegram.messages


@pytest.mark.asyncio
async def test_missing_configuration_is_closed(monkeypatch):
    monkeypatch.setattr(bot, "RAILWAY_WEBHOOK_SECRET", "")
    async with TestClient(TestServer(bot.create_app(Telegram()))) as client:
        assert (await client.post("/railway/webhook", json=payload())).status == 503


@pytest.mark.asyncio
async def test_retry_after_telegram_failure_and_dedup_per_event(configured):
    telegram = Telegram([False, True, True])
    async with TestClient(TestServer(bot.create_app(telegram))) as client:
        async def post(event_type="Deployment.deployed"):
            return await client.post("/railway/webhook", json=payload(event_type), headers={"Authorization": "Bearer " + SECRET})
        assert (await post()).status == 502
        assert (await post()).status == 200
        assert (await (await post()).json())["duplicate"] is True
        assert len(telegram.messages) == 2
        assert (await post("Deployment.crashed")).status == 200
        assert len(telegram.messages) == 3, "La caida del mismo despliegue debe avisarse"


@pytest.mark.asyncio
async def test_concurrent_duplicate_waits_for_actual_telegram_result(configured):
    gate = asyncio.Event()
    telegram = Telegram(gate=gate)
    async with TestClient(TestServer(bot.create_app(telegram))) as client:
        async def post():
            return await client.post("/railway/webhook", json=payload(), headers={"Authorization": "Bearer " + SECRET})
        first = asyncio.create_task(post())
        await telegram.entered.wait()
        second = asyncio.create_task(post())
        await asyncio.sleep(0.02)
        assert not first.done() and not second.done(), "No confirmar entrega antes de que Telegram acepte"
        gate.set()
        responses = await asyncio.gather(first, second)
        assert all(response.status == 200 for response in responses)
        assert len(telegram.messages) == 1


@pytest.mark.asyncio
async def test_github_retry_stays_possible_and_author_is_preserved(monkeypatch):
    monkeypatch.setattr(bot, "GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(bot, "GITHUB_REPOS", {"acme/app"})
    monkeypatch.setattr(bot, "WATCHED_BRANCHES", {"main"})
    telegram = Telegram([False, True])
    data = {"ref": "refs/heads/main", "repository": {"full_name": "acme/app"},
            "sender": {"login": "github-account"},
            "commits": [{"id": "abc1234", "message": "Cambio", "author": {"name": "Farid"}}]}
    raw = json.dumps(data).encode()
    headers = {"Content-Type": "application/json", "X-GitHub-Event": "push", "X-GitHub-Delivery": "retry",
               "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()}
    async with TestClient(TestServer(bot.create_app(telegram))) as client:
        assert (await client.post("/github/webhook", data=raw, headers=headers)).status == 502
        assert (await client.post("/github/webhook", data=raw, headers=headers)).status == 200
        assert (await (await client.post("/github/webhook", data=raw, headers=headers)).json())["duplicate"]
    assert len(telegram.messages) == 2
    assert "Autor: Farid" in telegram.messages[1]
    assert "github-account" in telegram.messages[1]
