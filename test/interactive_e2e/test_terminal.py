"""Real WSS → SOCKS → tailnet → Access → authenticated local PTY broker."""
import asyncio
import json
import pytest
import websockets
from websockets.exceptions import ConnectionClosed
from interactive_access.protocol import Parser, Type, encode, json_bytes
from conftest import wait
from test_faults import fault

RESOURCE = 'resource-terminal'


class Terminal:
    def __init__(self, ws):
        self.ws = ws
        self.parser = Parser()
        self.records = []

    async def record(self):
        async with asyncio.timeout(8):
            while not self.records:
                data = await self.ws.recv()
                assert isinstance(data, bytes), 'terminal transport must be binary'
                self.records.extend(self.parser.feed(data))
        return self.records.pop(0)

    async def send(self, kind, payload=b'', fragment=False):
        data = encode(kind, payload)
        if fragment:
            for offset in range(0, len(data), 3):
                await self.ws.send(data[offset:offset + 3])
        else:
            await self.ws.send(data)

    async def output(self, needle):
        output = b''
        async with asyncio.timeout(8):
            while needle not in output:
                kind, payload = await self.record()
                assert kind == Type.STDOUT, 'unexpected terminal record'
                output += payload
        return output


async def connect(ticket, service='terminal'):
    ws = await websockets.connect(f'ws://tailscale:8030/v1/connect/{RESOURCE}/{service}', max_size=65536)
    await ws.send(json.dumps({'type': 'authenticate', 'ticket': ticket}))
    assert json.loads(await ws.recv())['type'] == 'ready'
    return Terminal(ws)


async def shell(controller):
    issued = await controller.ticket(resource=RESOURCE)
    terminal = await connect(issued['ticket'])
    await terminal.send(Type.OPEN, json_bytes({'columns': 80, 'rows': 24, 'shell': 'default'}), fragment=True)
    assert (await terminal.record())[0] == Type.OPENED
    return terminal, issued


async def reaped(controller):
    async def check():
        return (await controller.agent('terminal', 'pty-status', method='GET'))['children'] == 0
    await wait(check, timeout=10)


@pytest.mark.asyncio
async def test_terminal_pty_security_faults_and_revocation(controller, records):
    record = await controller.request('fixture/prepare', {'resource': RESOURCE, 'generation': 't1', 'agent': 'terminal', 'owner': 'user-a'})
    await wait(lambda: controller.ticket(resource=RESOURCE))
    await controller.ticket('user-b', RESOURCE, expected=403)
    issued = await controller.ticket(resource=RESOURCE)
    with pytest.raises(ConnectionClosed):
        await connect(issued['ticket'], service='echo')
    terminal = await connect(issued['ticket'])
    await terminal.send(Type.OPEN, json_bytes({'columns': 80, 'rows': 24, 'shell': 'default'}), True)
    assert (await terminal.record())[0] == Type.OPENED
    await terminal.send(Type.STDIN, b'pwd; printf "probe:%s\\n" "$FAKE_WORKLOAD"\n', True)
    output = await terminal.output(b'probe:yes')
    correct_directory = b'/fake-workload' in output
    assert correct_directory, 'PTY must use fake workload directory'
    await terminal.send(Type.RESIZE, json_bytes({'columns': 120, 'rows': 40}), True)
    await terminal.send(Type.STDIN, b'stty size\n')
    await terminal.output(b'40 120')
    await terminal.send(Type.STDIN, b'exit 0\n')
    while (await terminal.record())[0] != Type.EXIT:
        pass
    await terminal.ws.close()
    await reaped(controller)
    with pytest.raises(ConnectionClosed):
        await connect(issued['ticket'])
    # resource-a has been revoked by the preceding suite. Use the unrelated
    # sentinel's actual tailnet IP to prove endpoint lateral denial.
    sentinel = await controller.agent('sentinel', 'status', method='GET')
    ip = next(ip for ip in sentinel['TailscaleIPs'] if '.' in ip)
    assert not (await controller.agent('terminal', 'tcp', {'ip': ip, 'port': 9000}))['connected']

    for action in ('restart-access', 'stop-gateway', 'stop-management', 'offline'):
        terminal, _ = await shell(controller)
        if action == 'offline':
            await controller.agent('terminal', 'offline')
        else:
            await fault(action)
        try:
            # Drain any prompt bytes before waiting for transport closure.
            async with asyncio.timeout(10):
                with pytest.raises(ConnectionClosed):
                    while True:
                        await terminal.ws.recv()
            await reaped(controller)
        finally:
            if action == 'offline':
                await controller.agent('terminal', 'online')
            elif action.startswith('stop-'):
                await fault(action.replace('stop-', 'start-'))
            await terminal.ws.close()
        await wait(lambda: controller.ticket(resource=RESOURCE))

    terminal, issued = await shell(controller)
    await controller.internal('DELETE', 'access-grants/' + issued['grant_id'])
    async with asyncio.timeout(10):
        with pytest.raises(ConnectionClosed):
            while True:
                await terminal.ws.recv()
    await reaped(controller)
    await terminal.ws.close()

    terminal, _ = await shell(controller)
    await controller.internal('DELETE', 'resources/' + RESOURCE)
    await controller.ticket(resource=RESOURCE, expected=410)
    async with asyncio.timeout(10):
        with pytest.raises(ConnectionClosed):
            while True:
                await terminal.ws.recv()
    await reaped(controller)
    await terminal.ws.close()
    async def revoked():
        state = await controller.internal('GET', 'enrollments/' + record['enrollment']['enrollment_id'])
        return state['state'] == 'REVOKED'
    await wait(revoked)
