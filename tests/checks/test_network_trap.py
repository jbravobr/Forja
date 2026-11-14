"""The network trap: what lands in it, what is recorded of it, and what is left alone."""

import asyncio
import socket
import urllib.error
import urllib.request

import pytest

from forja.checks.network_trap import UNKNOWN, NetworkTrap, destination_of


async def recorded(trap: NetworkTrap) -> tuple[str, ...]:
    """What the trap recorded, as soon as it has recorded the connection."""
    async with asyncio.timeout(10):
        while not trap.destinations:
            await asyncio.sleep(0.01)
    return trap.destinations


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("CONNECT api.example.org:443 HTTP/1.1", "api.example.org:443"),
        ("CONNECT [2001:db8::1]:8443 HTTP/1.1", "[2001:db8::1]:8443"),
        ("GET http://example.org/secret?token=1 HTTP/1.1", "example.org:80"),
        ("GET https://example.org/x HTTP/1.1", "example.org:443"),
        ("POST http://example.org:8080/a HTTP/1.1", "example.org:8080"),
        ("get http://Example.ORG/ HTTP/1.1", "example.org:80"),
        ("GET /relative HTTP/1.1", UNKNOWN),
        ("CONNECT nohost HTTP/1.1", UNKNOWN),
        ("CONNECT host:notaport HTTP/1.1", UNKNOWN),
        ("GET", UNKNOWN),
        ("", UNKNOWN),
        ("\x00\x01garbage", UNKNOWN),
    ],
)
def test_destination_is_host_and_port_and_never_the_path(line: str, expected: str) -> None:
    assert destination_of(line) == expected


def _fetch(url: str, proxy: str) -> str:
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)
    try:
        opener.open(url, timeout=5)
    except urllib.error.HTTPError as error:
        return str(error.code)
    except urllib.error.URLError as error:
        return str(error.reason)
    return "ok"


def test_a_client_that_honours_the_proxy_is_refused_and_its_destination_kept() -> None:
    async def scenario() -> tuple[str, str, tuple[str, ...]]:
        async with NetworkTrap() as trap:
            plain = await asyncio.to_thread(_fetch, "http://blocked.test/a/b?x=1", trap.url)
            tunnel = await asyncio.to_thread(_fetch, "https://blocked.test/a", trap.url)
            again = await asyncio.to_thread(_fetch, "http://blocked.test/c", trap.url)
            return plain, tunnel + again, trap.destinations

    plain, rest, seen = asyncio.run(scenario())
    assert plain == "403"
    assert "403" in rest
    assert seen == ("blocked.test:80", "blocked.test:443")  # first attempt order, no repeats


def test_a_fake_on_loopback_stays_out_of_the_trap_under_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    def call(url: str) -> str:
        body: bytes = urllib.request.urlopen(url, timeout=5).read()  # noqa: S310
        return body.decode()

    async def scenario() -> tuple[str, tuple[str, ...]]:
        server = await asyncio.start_server(fake, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with NetworkTrap() as trap, server:
            for name in ("HTTP_PROXY", "http_proxy"):
                monkeypatch.setenv(name, trap.url)
            for name in ("NO_PROXY", "no_proxy"):
                monkeypatch.setenv(name, "localhost,127.0.0.1,::1")
            body = await asyncio.to_thread(call, f"http://127.0.0.1:{port}/")
            return body, trap.destinations

    assert asyncio.run(scenario()) == ("ok", ())


def test_nothing_is_recorded_when_nobody_calls() -> None:
    async def scenario() -> tuple[str, ...]:
        async with NetworkTrap() as trap:
            return trap.destinations

    assert asyncio.run(scenario()) == ()


def test_a_connection_that_sends_nothing_is_recorded_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("forja.checks.network_trap.READ_TIMEOUT", 0.2)

    async def scenario() -> tuple[str, ...]:
        async with NetworkTrap() as trap:
            _, writer = await asyncio.open_connection("127.0.0.1", int(trap.url.rsplit(":", 1)[1]))
            seen = await recorded(trap)
            writer.close()
            return seen

    assert asyncio.run(scenario()) == (UNKNOWN,)


def test_a_huge_request_line_is_cut_before_it_is_read_in_full() -> None:
    async def scenario() -> tuple[str, ...]:
        async with NetworkTrap() as trap:
            port = int(trap.url.rsplit(":", 1)[1])
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET http://big.test/" + b"a" * 100_000 + b" HTTP/1.1\r\n")
            await writer.drain()
            seen = await recorded(trap)
            writer.close()
            return seen

    assert asyncio.run(scenario()) == ("big.test:80",)


def test_the_port_is_released_when_the_block_ends() -> None:
    async def scenario() -> int:
        async with NetworkTrap() as trap:
            return int(trap.url.rsplit(":", 1)[1])

    port = asyncio.run(scenario())
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0


def test_only_the_first_line_of_the_request_is_read() -> None:
    async def scenario() -> tuple[str, ...]:
        async with NetworkTrap() as trap:
            port = int(trap.url.rsplit(":", 1)[1])
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET\nhttp://second-line.test/ HTTP/1.1\r\n")
            await writer.drain()
            seen = await recorded(trap)
            writer.close()
            return seen

    assert asyncio.run(scenario()) == (UNKNOWN,)


def test_the_address_before_the_trap_opens_has_no_port() -> None:
    assert NetworkTrap().url == "http://127.0.0.1:0"
