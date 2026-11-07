"""Explicit IPv4 UDP endpoint, no discovery or Wi-Fi management.

Single calling thread, shared by session commands and stream reception. Closing
and reopening creates a new local endpoint; it never restores a device lease.
"""

from __future__ import annotations

import ipaddress
import errno
import math
import socket
import time

from ..wire.envelope import MAX_DGRAM

DEFAULT_PORT = 3333  # firmware ADR-0004 local control profile


class UDPTransport:
    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        address = ipaddress.IPv4Address(host)
        if address.is_unspecified or address.is_multicast or int(address) == 0xFFFFFFFF:
            raise ValueError("a unicast device IPv4 address is required")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("UDP port must be 1..65535")
        self.endpoint = (str(address), port)
        self._socket: socket.socket | None = None
        self.counters = dict(wrong_source=0, oversize=0)
        self.open()

    @property
    def closed(self) -> bool:
        return self._socket is None

    @property
    def local_endpoint(self) -> tuple[str, int]:
        return self._get_socket().getsockname()

    def _get_socket(self) -> socket.socket:
        if self._socket is None:
            raise OSError("UDP transport is closed")
        return self._socket

    def open(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("0.0.0.0", 0))
        except OSError:
            sock.close()
            raise
        self._socket = sock

    def send(self, datagram: bytes) -> None:
        if len(datagram) > MAX_DGRAM:
            raise ValueError("UDP datagram exceeds the protocol MTU")
        self._get_socket().sendto(datagram, self.endpoint)

    def receive(self, timeout_s: float) -> list[bytes]:
        if not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("receive timeout must be finite and nonnegative")
        sock = self._get_socket()
        deadline = time.monotonic() + timeout_s
        # Bound work even when foreign senders keep the socket readable.
        for _ in range(64):
            sock.settimeout(max(0.0, deadline - time.monotonic()))
            try:
                raw, source = sock.recvfrom(MAX_DGRAM + 1)
            except (BlockingIOError, socket.timeout):
                return []
            except OSError as exc:
                # Winsock consumes an oversized UDP datagram but raises
                # WSAEMSGSIZE instead of returning its truncated bytes.
                if exc.errno == errno.EMSGSIZE or getattr(exc, "winerror", None) == 10040:
                    self.counters["oversize"] += 1
                    continue
                raise
            if source != self.endpoint:
                self.counters["wrong_source"] += 1
            elif len(raw) > MAX_DGRAM:
                self.counters["oversize"] += 1
            else:
                return [raw]
            if timeout_s and time.monotonic() >= deadline:
                break
        return []

    def close(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            sock.close()

    def __enter__(self) -> UDPTransport:
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()
