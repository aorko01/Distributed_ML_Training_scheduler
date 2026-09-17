import asyncio
import ipaddress
import struct


class TailnetDialer:
    def __init__(self, host="127.0.0.1", port=1055, timeout=3):
        if host != "127.0.0.1":
            raise ValueError("SOCKS must be namespace-private loopback")
        self.host, self.port, self.timeout = host, port, timeout

    async def dial(self, ip, port):
        address = ipaddress.ip_address(ip)
        networks = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
        if port != 9000 or not any(address.version == n.version and address in n for n in networks):
            raise ValueError("invalid tailnet destination")
        writer = None
        try:
            async with asyncio.timeout(self.timeout):
                # This is the only open_connection: exclusively the private proxy.
                reader, writer = await asyncio.open_connection(self.host, self.port, limit=65536)
                writer.write(b"\x05\x01\x00")
                await writer.drain()
                if await reader.readexactly(2) != b"\x05\x00":
                    raise OSError("SOCKS negotiation failed")
                writer.write(b"\x05\x01\x00" + bytes([1 if address.version == 4 else 4])
                             + address.packed + struct.pack("!H", port))
                await writer.drain()
                header = await reader.readexactly(4)
                if header[:3] != b"\x05\x00\x00":
                    raise OSError("SOCKS connection denied")
                if header[3] == 1:
                    size = 4
                elif header[3] == 4:
                    size = 16
                elif header[3] == 3:
                    size = (await reader.readexactly(1))[0]
                else:
                    raise OSError("invalid SOCKS response")
                await reader.readexactly(size + 2)
                return reader, writer
        except BaseException:
            if writer:
                writer.close()
                try:
                    async with asyncio.timeout(1):
                        await writer.wait_closed()
                except (OSError, TimeoutError):
                    pass
            raise

    async def ready(self):
        async with asyncio.timeout(1):
            reader, writer = await asyncio.open_connection(self.host, self.port)
            try:
                writer.write(b"\x05\x01\x00")
                await writer.drain()
                return await reader.readexactly(2) == b"\x05\x00"
            finally:
                writer.close()
                await writer.wait_closed()
