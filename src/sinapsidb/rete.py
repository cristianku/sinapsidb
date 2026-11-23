"""Filtri di rete condivisi dal server MCP e dal portale."""

from __future__ import annotations

import ipaddress
import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("sinapsidb.rete")


def _normalizza_ip(valore: str) -> str:
    """``::ffff:10.0.0.1`` diventa ``10.0.0.1``; un valore non valido resta com'è."""
    try:
        ip = ipaddress.ip_address(valore)
    except ValueError:
        return valore
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return str(ip)


class FiltroClient:
    """Middleware ASGI: rifiuta con 403 le connessioni da IP non ammessi.

    Usa l'indirizzo del socket. Uvicorn va avviato con ``proxy_headers=False``,
    altrimenti un client locale potrebbe falsificarlo con X-Forwarded-For.
    """

    def __init__(self, app: ASGIApp, ammessi: list[str]) -> None:
        self.app = app
        self.ammessi = {_normalizza_ip(a) for a in ammessi}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            ip = _normalizza_ip(client[0]) if client else ""
            if ip not in self.ammessi:
                log.warning("connessione rifiutata da %s su %r", ip or "?", scope.get("path"))
                if scope["type"] == "http":
                    risposta = JSONResponse({"error": "forbidden"}, status_code=403)
                    await risposta(scope, receive, send)
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


def _nome_host(valore: str) -> str:
    """Nome o indirizzo dell'header Host, senza porta e in minuscolo. ``[::1]:8419`` diventa ``::1``."""
    valore = valore.strip().lower()
    if valore.startswith("["):
        return valore[1:].split("]", 1)[0]
    return valore.rsplit(":", 1)[0] if valore.count(":") == 1 else valore


class FiltroHost:
    """Middleware ASGI: risponde 421 se l'header Host non è tra quelli ammessi.

    Protegge dal DNS rebinding le pagine senza autenticazione. I nomi si confrontano
    senza porta; ``*`` ammette qualsiasi Host.
    """

    def __init__(self, app: ASGIApp, ammessi: list[str]) -> None:
        self.app = app
        self.tutti = "*" in ammessi
        self.ammessi = {_nome_host(a) for a in ammessi}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not self.tutti:
            host = ""
            for k, v in scope.get("headers", []):
                if k == b"host":
                    host = _nome_host(v.decode("latin-1"))
                    break
            if host not in self.ammessi:
                log.warning("header Host non ammesso: %r su %r", host, scope.get("path"))
                risposta = JSONResponse({"error": "misdirected_request"}, status_code=421)
                await risposta(scope, receive, send)
                return
        await self.app(scope, receive, send)
