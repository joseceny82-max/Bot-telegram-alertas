"""GitHub push alerts -> Telegram.

GitHub sends push webhooks to this service. The signature, repository and
branch are checked before the push is forwarded to Telegram.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import logging
import os
from collections import deque
from typing import Any

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("github-telegram-alerts")


def csv(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
GITHUB_REPOS = csv(os.getenv("GITHUB_REPOS", ""))
WATCHED_BRANCHES = csv(os.getenv("WATCHED_BRANCHES", "main,master"))
PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/github/webhook")

MAX_TELEGRAM_MESSAGE = 4096
MAX_COMMITS_IN_MESSAGE = 20


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Validate GitHub's X-Hub-Signature-256 header."""
    if not secret or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def repo_is_allowed(full_name: str) -> bool:
    return not GITHUB_REPOS or full_name.lower() in GITHUB_REPOS


def branch_from_ref(ref: str) -> str:
    prefix = "refs/heads/"
    return ref[len(prefix):] if ref.startswith(prefix) else ref


def format_push(payload: dict[str, Any]) -> str:
    repository = payload.get("repository") or {}
    full_name = repository.get("full_name", "repositorio")
    branch = branch_from_ref(payload.get("ref", ""))
    sender = (payload.get("sender") or {}).get("login") or (payload.get("pusher") or {}).get("name") or "alguien"
    compare_url = payload.get("compare") or repository.get("html_url") or ""
    commits = payload.get("commits") or []

    lines = [
        "🚀 <b>Nuevo push</b>",
        f"<b>{esc(full_name)}</b> · <code>{esc(branch)}</code>",
        f"👤 {esc(sender)} · {len(commits)} commit{'s' if len(commits) != 1 else ''}",
    ]
    if compare_url:
        lines.append(f'<a href="{esc(compare_url)}">Ver cambios en GitHub</a>')

    if commits:
        lines.append("")
        for commit in commits[:MAX_COMMITS_IN_MESSAGE]:
            message = str(commit.get("message") or "").splitlines()[0]
            sha = str(commit.get("id") or "")[:7]
            url = commit.get("url") or compare_url
            lines.append(f'• <a href="{esc(url)}"><code>{esc(sha)}</code></a> {esc(message)}')
        remaining = len(commits) - MAX_COMMITS_IN_MESSAGE
        if remaining > 0:
            lines.append(f"• … y {remaining} commit(s) más")

    return truncate("\n".join(lines))


def truncate(text: str, limit: int = MAX_TELEGRAM_MESSAGE) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = chat_id
        self.session: aiohttp.ClientSession | None = None

    async def send(self, text: str) -> bool:
        self.session = self.session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for attempt in range(3):
            try:
                async with self.session.post(f"{self.base}/sendMessage", json=payload) as response:
                    data = await response.json()
                if data.get("ok"):
                    return True
                retry_after = (data.get("parameters") or {}).get("retry_after")
                if response.status == 429 and retry_after:
                    await asyncio.sleep(float(retry_after))
                    continue
                log.error("Telegram rechazó el mensaje (%s): %s", response.status, data.get("description"))
                return False
            except (aiohttp.ClientError, TimeoutError):
                if attempt == 2:
                    log.exception("No se pudo contactar con Telegram")
                    return False
                await asyncio.sleep(2**attempt)
        return False

    async def close(self) -> None:
        if self.session:
            await self.session.close()


def create_app(telegram: Telegram) -> web.Application:
    deliveries: deque[str] = deque(maxlen=1000)

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "service": "github-telegram-alerts"})

    async def github_webhook(request: web.Request) -> web.Response:
        body = await request.read()
        if not verify_signature(body, request.headers.get("X-Hub-Signature-256", ""), GITHUB_WEBHOOK_SECRET):
            return web.json_response({"error": "firma inválida"}, status=401)

        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        if delivery_id and delivery_id in deliveries:
            return web.json_response({"ok": True, "duplicate": True})

        event = request.headers.get("X-GitHub-Event", "")
        if event != "push":
            return web.json_response({"ok": True, "ignored": event or "unknown event"})

        try:
            payload = await request.json()
        except (ValueError, aiohttp.ContentTypeError):
            return web.json_response({"error": "JSON inválido"}, status=400)

        repository = payload.get("repository") or {}
        full_name = str(repository.get("full_name") or "")
        branch = branch_from_ref(str(payload.get("ref") or ""))
        if not repo_is_allowed(full_name):
            return web.json_response({"ok": True, "ignored": "repository"})
        if WATCHED_BRANCHES and branch.lower() not in WATCHED_BRANCHES:
            return web.json_response({"ok": True, "ignored": "branch"})
        if payload.get("deleted") or not payload.get("commits"):
            return web.json_response({"ok": True, "ignored": "empty or deleted push"})

        if delivery_id:
            deliveries.append(delivery_id)
        if not await telegram.send(format_push(payload)):
            if delivery_id in deliveries:
                deliveries.remove(delivery_id)
            return web.json_response({"error": "Telegram no aceptó el mensaje"}, status=502)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post(WEBHOOK_PATH, github_webhook)

    async def cleanup(_app: web.Application) -> None:
        await telegram.close()

    app.on_cleanup.append(cleanup)
    return app


def validate_config() -> None:
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    if not GITHUB_WEBHOOK_SECRET:
        missing.append("GITHUB_WEBHOOK_SECRET")
    if missing:
        raise SystemExit("Faltan variables en .env: " + ", ".join(missing))


def main() -> None:
    validate_config()
    log.info("Repositorios vigilados: %s", ", ".join(sorted(GITHUB_REPOS)) or "todos")
    log.info("Ramas vigiladas: %s", ", ".join(sorted(WATCHED_BRANCHES)) or "todas")
    web.run_app(create_app(Telegram(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)), host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
