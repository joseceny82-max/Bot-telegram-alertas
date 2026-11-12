"""Validacion y formato de los avisos de despliegue de Railway."""

from __future__ import annotations

import html
from typing import Any
from uuid import UUID


DEPLOYMENT_EVENTS = {
    "Deployment.deployed": ("✅", "Despliegue publicado"),
    "Deployment.failed": ("❌", "Despliegue fallido"),
    "Deployment.crashed": ("🚨", "Servicio detenido por un error"),
    "Deployment.oom_killed": ("🚨", "Servicio detenido por falta de memoria"),
}


def identifier(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("identificador inválido")
    try:
        return str(UUID(value))
    except ValueError:
        raise ValueError("identificador inválido") from None


def text(value: Any, limit: int = 60) -> str:
    if not isinstance(value, str):
        return ""
    return value.splitlines()[0][:limit] if value.splitlines() else ""


def deployment(payload: dict, projects: set[str], environments: set[str]) -> tuple[dict | None, str]:
    event_type = payload.get("type")
    if not isinstance(event_type, str) or event_type not in DEPLOYMENT_EVENTS:
        return None, "event"
    resource = payload.get("resource")
    details = payload.get("details", {})
    if not isinstance(resource, dict) or not isinstance(details, dict):
        raise ValueError("despliegue inválido")
    blocks = [resource.get(k) for k in ("project", "environment", "service", "deployment")]
    if not all(isinstance(block, dict) for block in blocks):
        raise ValueError("recurso inválido")
    project, environment, service, release = blocks
    ids = [identifier(block.get("id")) for block in blocks]
    if ids[0] not in projects:
        return None, "project"
    environment_name = text(environment.get("name"), 40)
    if environment.get("isEphemeral") or environment_name.lower() not in environments:
        return None, "environment"
    return {
        "type": event_type,
        "key": f"railway:{ids[0]}:{ids[3]}:{event_type}",
        "project": text(project.get("name")) or ids[0],
        "environment": environment_name,
        "service": text(service.get("name")) or ids[2],
        "url": f"https://railway.com/project/{ids[0]}/service/{ids[2]}?id={ids[3]}",
        "author": text(details.get("commitAuthor")),
        "commit": text(details.get("commitMessage"), 120),
        "source": text(details.get("source"), 30),
    }, ""


def format_deployment(event: dict) -> str:
    esc = lambda value: html.escape(value, quote=False)
    icon, title = DEPLOYMENT_EVENTS[event["type"]]
    lines = [f"{icon} <b>{title}</b>",
             f"<b>{esc(event['project'])}</b> · {esc(event['service'])}",
             f"Entorno: <code>{esc(event['environment'])}</code>"]
    if event["author"]:
        lines.append(f"✍️ {esc(event['author'])}")
    if event["source"]:
        lines.append(f"Origen: {esc(event['source'])}")
    if event["commit"]:
        lines.append(esc(event["commit"]))
    lines.append(f'<a href="{event["url"]}">Ver despliegue en Railway</a>')
    return "\n".join(lines)
