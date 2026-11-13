"""Unprivileged Decky bridge. No shell and no sysfs writes."""
import asyncio
import json
import uuid
import os
import re
from pathlib import Path

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
            return {'ok': False, 'error': 'CPU service unavailable: ' + str(e)}
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()

    async def get_ui_language(self):
        # Read only fixed user configuration paths; no shell or hardware access.
        for path in [Path.home()/'.steam/registry.vdf', Path.home()/'.local/share/Steam/config/config.vdf']:
            try:
                match = re.search(r'"language"\s*"([^"\r\n]+)"', path.read_text()[:2000000], re.I)
                if match:
                    value = match.group(1).lower()
                    return {'language': 'zh' if value in ('schinese','tchinese','chinese') or value.startswith('zh') else 'en'}
            except (OSError, UnicodeError):
                pass
        value = os.environ.get('LC_ALL') or os.environ.get('LC_MESSAGES') or os.environ.get('LANG', '')
        if not value or value in ('C','C.UTF-8','POSIX'):
            try:
                match = re.search(r'^LANG=["\']?([^"\'\n]+)', Path('/etc/locale.conf').read_text(), re.M)
                if match: value = match.group(1)
            except OSError:
                pass
        return {'language': 'zh' if value.lower().startswith('zh') else 'en'}

    async def get_status(self):
        return await self._rpc({'method': 'get_status'})

    async def get_fan_status(self):
        return await self._rpc({'method': 'fan_status'})

    async def get_power_status(self):
        return await self._rpc({'method': 'power_status'})

    async def get_boot_status(self):
        return await self._rpc({'method':'boot_status'})

    async def mutate(self, request):
        if not isinstance(request, dict) or request.get('method') not in ('save_preset', 'apply', 'restore', 'configure', 'save_ui', 'set_turbo', 'fan_save', 'fan_apply', 'fan_restore', 'fan_autostart', 'power_save', 'power_apply', 'power_restore', 'boot_set'):
            return {'ok': False, 'error': 'Unsupported operation'}
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
        power = await self.get_power_status()
        if power.get('has_transaction'):
            await self._rpc({'method': 'power_restore', 'request_id': uuid.uuid4().hex,
                             'expected_revision': power['revision']})
        fan = await self.get_fan_status()
        if fan.get('has_transaction'):
            await self._rpc({'method': 'fan_restore', 'request_id': uuid.uuid4().hex,
                             'expected_revision': fan['revision']})
        status = await self.get_status()
        if status.get('has_transaction'):
            await self._rpc({'method': 'restore', 'request_id': uuid.uuid4().hex,
                             'expected_revision': status['revision']})
