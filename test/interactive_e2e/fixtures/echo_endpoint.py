"""Fixture only: loopback TCP listeners; never a production endpoint."""
import asyncio
import os


async def echo(reader, writer):
    try:
        writer.write(os.environ.get("ECHO_PREFIX", "endpoint:").encode())
        await writer.drain()
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    servers = [await asyncio.start_server(echo, "127.0.0.1", port)
               for port in map(int, os.getenv("ECHO_PORTS", "9000,9001").split(","))]
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
