"""Telegram client really goes through the configured SOCKS5/HTTP proxy.

Local fake Bot API server + minimal SOCKS5 and HTTP CONNECT proxies that record
what they tunnel.
"""

from __future__ import annotations

import asyncio
import base64
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from aiohttp_socks import ProxyConnector
from pydantic import SecretStr

from app.bot.sender import AiogramNotificationSender, build_bot, build_proxy, build_session
from app.core.config import TelegramSettings
from app.services.telegram_sender import TransientDeliveryError


@dataclass
class ProxyLog:
    targets: list[str] = field(default_factory=list)
    credentials: list[tuple[str, str]] = field(default_factory=list)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def _tunnel(
    client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter, host: str, port: int
) -> None:
    remote_r, remote_w = await asyncio.open_connection(host, port)
    await asyncio.gather(_pipe(client_r, remote_w), _pipe(remote_r, client_w))


def socks5_handler(log: ProxyLog, auth: tuple[str, str] | None):  # type: ignore[no-untyped-def]
    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        _, n_methods = await r.readexactly(2)
        await r.readexactly(n_methods)
        if auth is None:
            w.write(b"\x05\x00")
        else:
            w.write(b"\x05\x02")
            await w.drain()
            _, ulen = await r.readexactly(2)
            username = (await r.readexactly(ulen)).decode()
            plen = (await r.readexactly(1))[0]
            password = (await r.readexactly(plen)).decode()
            log.credentials.append((username, password))
            ok = (username, password) == auth
            w.write(b"\x01\x00" if ok else b"\x01\x01")
            if not ok:
                w.close()
                return
        await w.drain()
        _, _, _, atyp = await r.readexactly(4)
        if atyp == 1:
            host = socket.inet_ntoa(await r.readexactly(4))
        else:
            host = (await r.readexactly((await r.readexactly(1))[0])).decode()
        port = int.from_bytes(await r.readexactly(2), "big")
        log.targets.append(f"{host}:{port}")
        w.write(b"\x05\x00\x00\x01" + bytes(4) + b"\x00\x00")
        await w.drain()
        await _tunnel(r, w, host, port)

    return handle


def http_connect_handler(log: ProxyLog):  # type: ignore[no-untyped-def]
    async def handle(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        head = (await r.readuntil(b"\r\n\r\n")).decode()
        request_line, *headers = head.split("\r\n")
        method, target, _ = request_line.split(" ")
        assert method == "CONNECT"
        for header in headers:
            if header.lower().startswith("proxy-authorization: basic "):
                user, _, pw = base64.b64decode(header.split()[-1]).decode().partition(":")
                log.credentials.append((user, pw))
        log.targets.append(target)
        host, port = target.rsplit(":", 1)
        w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await w.drain()
        await _tunnel(r, w, host, int(port))

    return handle


async def _serve(handler) -> tuple[asyncio.Server, int]:  # type: ignore[no-untyped-def]
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.fixture
async def fake_api() -> AsyncIterator[tuple[TelegramAPIServer, int]]:
    async def get_me(_: web.Request) -> web.Response:
        return web.json_response(
            {"ok": True, "result": {"id": 42, "is_bot": True, "first_name": "SLA"}}
        )

    async def send_message(_: web.Request) -> web.Response:
        chat = {"id": -100777, "type": "supergroup", "title": "x"}
        result = {"message_id": 321, "date": 1700000000, "chat": chat, "text": "ok"}
        return web.json_response({"ok": True, "result": result})

    app = web.Application()
    app.router.add_post("/bot{token}/getMe", get_me)
    app.router.add_post("/bot{token}/sendMessage", send_message)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    yield TelegramAPIServer.from_base(f"http://127.0.0.1:{port}"), port
    await runner.cleanup()


def tg_settings(**kwargs: object) -> TelegramSettings:
    return TelegramSettings(bot_token=SecretStr("42:TOKEN"), **kwargs)  # type: ignore[arg-type]


def test_session_uses_proxy_connector() -> None:
    assert build_proxy(tg_settings()) is None
    session = build_session(tg_settings(proxy_url="socks5://127.0.0.1:1080"))
    assert session._connector_type is ProxyConnector
    assert session._connector_init["host"] == "127.0.0.1"
    proxy = build_proxy(
        tg_settings(proxy_url="http://p:3128", proxy_username="u@x", proxy_password="p:w/@")
    )
    assert proxy == "http://u%40x:p%3Aw%2F%40@p:3128"


@pytest.mark.parametrize("separate_credentials", [False, True])
async def test_bot_api_through_socks5(
    fake_api: tuple[TelegramAPIServer, int], separate_credentials: bool
) -> None:
    api, api_port = fake_api
    log = ProxyLog()
    server, port = await _serve(socks5_handler(log, auth=("bot", "p@ss:1")))
    if separate_credentials:
        settings = tg_settings(
            proxy_url=f"socks5://127.0.0.1:{port}",
            proxy_username="bot",
            proxy_password="p@ss:1",
        )
    else:
        settings = tg_settings(proxy_url=f"socks5h://bot:p%40ss%3A1@127.0.0.1:{port}")
    async with server, build_bot(settings, api) as bot:
        me = await bot.get_me()
    assert me.id == 42
    assert log.credentials == [("bot", "p@ss:1")]
    assert log.targets == [f"127.0.0.1:{api_port}"]


async def test_bot_api_through_http_proxy(fake_api: tuple[TelegramAPIServer, int]) -> None:
    api, api_port = fake_api
    log = ProxyLog()
    server, port = await _serve(http_connect_handler(log))
    settings = tg_settings(proxy_url=f"http://u:pw@127.0.0.1:{port}")
    async with server, build_bot(settings, api) as bot:
        assert (await bot.get_me()).id == 42
    assert log.targets == [f"127.0.0.1:{api_port}"]
    assert log.credentials == [("u", "pw")]


async def test_worker_sender_through_proxy(fake_api: tuple[TelegramAPIServer, int]) -> None:
    api, _ = fake_api
    log = ProxyLog()
    server, port = await _serve(socks5_handler(log, auth=None))
    sender = AiogramNotificationSender(tg_settings(proxy_url=f"socks5://127.0.0.1:{port}"), api)
    async with server:
        message_id = await asyncio.to_thread(
            sender.send_notification, chat_id=-100777, thread_id=5, text="hi", pending_reply_id=1
        )
    assert message_id == 321
    assert len(log.targets) == 1


async def test_unreachable_proxy_is_transient_error(
    fake_api: tuple[TelegramAPIServer, int],
) -> None:
    api, _ = fake_api
    with socket.socket() as s:  # grab a free port with nothing listening on it
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    sender = AiogramNotificationSender(
        tg_settings(proxy_url=f"socks5://127.0.0.1:{dead_port}", request_timeout_seconds=3), api
    )
    with pytest.raises(TransientDeliveryError):
        await asyncio.to_thread(
            sender.send_notification, chat_id=-1, thread_id=None, text="x", pending_reply_id=1
        )
