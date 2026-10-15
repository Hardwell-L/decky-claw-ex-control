"""Unprivileged Decky bridge. No shell and no sysfs writes."""
import asyncio
import json
import uuid

SOCKET = '/run/claw-cpu-control/control.sock'


class Plugin:
    async def _rpc(self, request):
        writer = None
        try:
            reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(SOCKET, limit=65536), 3)
            writer.write((json.dumps(request) + '\n').encode())
            await writer.drain()
            return json.loads(await asyncio.wait_for(reader.readline(), 30))
        except (OSError, asyncio.TimeoutError, ValueError) as e:
            return {'ok': False, 'error': 'CPU 服务不可用：' + str(e)}
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()

    async def get_status(self):
        return await self._rpc({'method': 'get_status'})

    async def get_fan_status(self):
        return await self._rpc({'method': 'fan_status'})

    async def mutate(self, request):
        if not isinstance(request, dict) or request.get('method') not in ('save_preset', 'apply', 'restore', 'configure', 'save_ui', 'set_turbo', 'fan_save', 'fan_apply', 'fan_restore', 'fan_autostart'):
            return {'ok': False, 'error': '不支持的操作'}
        return await self._rpc(request)

    async def _main(self):
        self._token = uuid.uuid4().hex
        self._stop = asyncio.Event()
        while not self._stop.is_set():
            await self._rpc({'method': 'heartbeat', 'session_token': self._token})
            try:
                await asyncio.wait_for(self._stop.wait(), 10)
            except asyncio.TimeoutError:
                pass

    async def _unload(self):
        if hasattr(self, '_stop'):
            self._stop.set()
        fan = await self.get_fan_status()
        if fan.get('has_transaction'):
            await self._rpc({'method': 'fan_restore', 'request_id': uuid.uuid4().hex,
                             'expected_revision': fan['revision']})
        status = await self.get_status()
        if status.get('has_transaction'):
            await self._rpc({'method': 'restore', 'request_id': uuid.uuid4().hex,
                             'expected_revision': status['revision']})
