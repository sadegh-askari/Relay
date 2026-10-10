"""Local-only setup: discover adapters, validate topology, stage a reviewed root install."""
from functools import lru_cache
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile
import threading
import vpn_apps

CONFIG_PATH = Path('/Library/Application Support/TV Internet Control/configuration.json')
PRIVATE_NETWORKS = tuple(ipaddress.ip_network(s) for s in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))

class SetupError(Exception):
    pass

def configuration():
    try:
        value = json.loads(CONFIG_PATH.read_text())
        if not isinstance(value, dict) or set(value) != {'version', 'upstreamService', 'downstreamDevice', 'vpnService', 'gateway'} or value.get('version') != 1 or not all(isinstance(value[k], str) and value[k] for k in ('upstreamService', 'downstreamDevice', 'vpnService', 'gateway')):
            raise ValueError()
        return value
    except (OSError, ValueError, AttributeError):
        return None

def inventory(base):
    try:
        result = subprocess.run([str(base / 'sharectl'), 'inventory'], capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        if result.returncode or not data.get('ok'):
            raise SetupError(data.get('error', 'Adapter discovery failed.'))
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise SetupError('Could not detect network adapters. Reopen the installer on the Mac.') from None

@lru_cache(maxsize=8)
def topology(base, downstream):
    """Infer topology from the configured physical adapter, including older helpers."""
    data = inventory(base)
    output = next((s for s in data['services'] if not s['vpn'] and s['device'] == downstream), {})
    return 'hotspot' if output.get('type') == 'IEEE80211' else 'router'

def open_hotspot_settings():
    # Fixed native destination; no remote browser values or credentials are passed.
    result = subprocess.run(['/usr/bin/open', 'x-apple.systempreferences:com.apple.Sharing-Settings.extension?Internet'], capture_output=True, timeout=10)
    if result.returncode:
        raise SetupError('Could not open macOS Sharing settings.')
    return {'message': 'Configure the hotspot in Internet Sharing, then return to Relay and refresh detection.'}

def validate_selection(value, detected):
    if not isinstance(value, dict) or set(value) not in ({'version', 'upstreamService', 'downstreamDevice', 'vpnService', 'gateway'}, {'version', 'upstreamService', 'downstreamDevice', 'vpnService', 'gateway', 'topology'}):
        raise SetupError('Choose the internet adapter, output adapter, VPN profile, and shared address.')
    services = detected.get('services', [])
    upstream = next((s for s in services if not s['vpn'] and s['id'] == value['upstreamService']), None)
    output = next((s for s in services if not s['vpn'] and s['device'] == value['downstreamDevice']), None)
    vpn = next((s for s in services if s['vpn'] and s['id'] == value['vpnService']), None)
    if value['version'] != 1 or not upstream or not output or (not vpn and not vpn_apps.is_manual(value["vpnService"])):
        raise SetupError('A selected adapter or VPN profile is no longer available. Refresh detection.')
    if upstream['type'] not in ('Ethernet', 'IEEE80211') or output['type'] not in ('Ethernet', 'IEEE80211') or upstream['device'] == output['device']:
        raise SetupError('The internet and output adapters must be different Wi-Fi/Ethernet ports.')
    mode = value.get('topology', 'hotspot' if output['type'] == 'IEEE80211' else 'router')
    if mode not in ('router', 'hotspot') or (mode == 'hotspot' and (upstream['type'] != 'Ethernet' or output['type'] != 'IEEE80211')) or (mode == 'router' and output['type'] != 'Ethernet'):
        raise SetupError('Hotspot mode requires Ethernet internet input and Wi-Fi output. Router mode requires Ethernet output.')
    try:
        address = ipaddress.IPv4Address(value['gateway'])
        if not any(address in net for net in PRIVATE_NETWORKS) or int(address) & 255 != 1:
            raise ValueError()
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise SetupError('Use a private address ending in .1, such as 192.168.2.1. The shared subnet is /24.') from None
    subnet = ipaddress.ip_network(str(address) + '/24', strict=False)
    for service in services:
        if service['vpn'] or service['device'] == output['device'] or not service.get('address'):
            continue
        connected = ipaddress.ip_network(service['address'] + '/' + service.get('mask', '24'), strict=False)
        if subnet.overlaps(connected):
            raise SetupError('The shared subnet overlaps another connected network. Choose a different shared address.')
    if output['type'] == 'IEEE80211':
        existing = detected.get('existing', {})
        if not existing.get('enabled') or existing.get('devices') != [output['device']] or existing.get('primaryService') != upstream['id']:
            raise SetupError('Use Configure hotspot to choose the selected Ethernet input, Wi-Fi output, a network name, and WPA2/WPA3 password in macOS. Enable Internet Sharing, then refresh detection.')
    return {key: item for key, item in value.items() if key != 'topology'}

class SetupManager:
    def __init__(self, base):
        self.base = base
        self.lock = threading.Lock()
        self.operation = {'busy': False, 'complete': False, 'error': '', 'message': ''}

    def snapshot(self):
        detected = inventory(self.base)
        return {**detected, 'operation': dict(self.operation)}

    def start(self, value, controller):
        # Share the mode-operation lock so changing topology cannot race a VPN switch.
        if not self.lock.acquire(blocking=False):
            raise SetupError('Setup is already running.')
        if not controller.lock.acquire(blocking=False):
            self.lock.release()
            raise SetupError('Wait for the current connection change to finish.')
        try:
            selection = validate_selection(value, inventory(self.base))
        except Exception:
            controller.lock.release(); self.lock.release(); raise
        self.operation = {'busy': True, 'complete': False, 'error': '', 'message': 'Approve the macOS administrator dialog on this Mac.'}
        threading.Thread(target=self._install, args=(selection, controller), daemon=True).start()
        return {'accepted': True, 'operation': dict(self.operation)}

    def _install(self, selection, controller):
        try:
            with tempfile.TemporaryDirectory(prefix='tv-internet-control.', dir='/private/tmp') as folder:
                stage = Path(folder)
                for filename in ('sharectl', 'install-helper.zsh'):
                    shutil.copy2(self.base / filename, stage / filename)
                (stage / 'configuration.json').write_text(json.dumps(selection))
                account = pwd.getpwuid(os.getuid()).pw_name
                # Fixed script and paths are passed as argv, not interpolated shell/browser text.
                script = '''on run argv
set commandText to "/bin/zsh " & quoted form of item 1 of argv & " " & quoted form of item 2 of argv
do shell script commandText with administrator privileges
end run'''
                result = subprocess.run(['/usr/bin/osascript', '-e', script, str(stage / 'install-helper.zsh'), account], capture_output=True, text=True, timeout=180)
                if result.returncode:
                    text = result.stderr.strip()
                    if '-128' in text:
                        raise SetupError('Setup was cancelled. Your previous sharing configuration is preserved.')
                    # Helper errors contain topology only; never collect raw VPN or Wi-Fi credentials.
                    raise SetupError('Setup could not complete. ' + text[-900:])
            if not (selection['vpnService'] == 'other' and vpn_apps.is_experimental(controller.selected_service())):
                controller.vpn_service = selection['vpnService']
            topology.cache_clear()
            controller.desired_mode = None
            controller.save_preferences()
            controller.retry_after = __import__('time').monotonic() + 5
            self.operation = {'busy': False, 'complete': True, 'error': '', 'message': 'Setup complete. Manage automatic startup with Relay’s Launch at login option.'}
        except (OSError, subprocess.TimeoutExpired):
            self.operation = {'busy': False, 'complete': False, 'error': 'The installer did not respond. Check the macOS dialog and try again.', 'message': ''}
        except Exception as error:
            self.operation = {'busy': False, 'complete': False, 'error': str(error) if isinstance(error, SetupError) else 'Setup could not complete.', 'message': ''}
        finally:
            controller.cached_at = 0
            controller.lock.release(); self.lock.release()
