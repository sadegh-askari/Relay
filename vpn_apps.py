"""Discover VPN connections registered with macOS and optional app controls.
Only identities and first-line states are retained; no VPN configuration is logged.
"""
import json
from pathlib import Path
import plistlib
import re
import subprocess
import threading
import time
from xml.parsers.expat import ExpatError

_cache = None
_cached_at = 0
_lock = threading.Lock()
STATES = ('Connected', 'Connecting', 'Disconnected', 'Disconnecting')
EXPERIMENTAL = {'app:openvpn': ('org.openvpn.client.app', 'OpenVPN Connect'),
                'app:happ': ('com.happ.www', 'Happ')}

def installed_apps(roots=None, paths=False):
    result = {}
    for root in roots or (Path('/Applications'), Path.home() / 'Applications'):
        for app in list(root.glob('*.app')) + list(root.glob('*/*.app')):
            files = [app / 'Contents/Info.plist', app / 'Info.plist']
            files += list(app.glob('Wrapper/*.app/Info.plist'))
            for file in files:
                try:
                    info = plistlib.loads(file.read_bytes())
                    identity = info.get('CFBundleIdentifier')
                    if isinstance(identity, str): result[identity] = str(app) if paths else app.stem
                except (OSError, ValueError, plistlib.InvalidFileException, ExpatError, AttributeError, TypeError):
                    # macOS accepts some plist XML declarations that Expat rejects (including Happ).
                    # Extract only the public bundle identity with Apple's parser.
                    if not file.is_file(): continue
                    try:
                        check = subprocess.run(['/usr/bin/plutil', '-extract', 'CFBundleIdentifier', 'raw', '-o', '-', str(file)], capture_output=True, text=True, timeout=3)
                        identity = check.stdout.strip()
                        if check.returncode == 0 and re.fullmatch(r'[A-Za-z0-9_.-]+', identity): result[identity] = str(app) if paths else app.stem
                    except (OSError, subprocess.TimeoutExpired): pass
    return result

def discover(refresh=False):
    """Discover registered connections, not a provider allowlist.

    Status access is checked read-only. It does not prove authentication or a
    shareable full tunnel; those are verified during an actual transition.
    """
    global _cache, _cached_at
    with _lock:
        if _cache is not None and not refresh and time.monotonic() - _cached_at < 30:
            return json.loads(json.dumps(_cache))
        installed = installed_apps()
        try:
            listing = subprocess.run(['/usr/sbin/scutil', '--nc', 'list'], capture_output=True, text=True, timeout=10)
            if listing.returncode: raise OSError()
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('VPN connection detection failed. Try again on the Mac.') from None
        groups = {}
        for line in listing.stdout.splitlines():
            match = re.match(r'^\s*(\*)?\s*\(([^)]+)\)\s+([A-Fa-f0-9-]{36})\s+VPN \(([^)]+)\)\s+"([^"\n]*)"', line)
            legacy = re.match(r'^\s*(\*)?\s*\(([^)]+)\)\s+([A-Fa-f0-9-]{36})\s+PPP --> (?:L2TP|PPTP|IPSec)\s+"([^"\n]*)"', line)
            if not match:
                if legacy:
                    enabled, state, service, name = legacy.groups()
                    groups['system:' + service] = {'id': 'system:' + service, 'name': name or 'VPN', 'manual': False, 'installed': False, 'available': False, 'unavailableReason': 'This VPN type is not supported for client sharing.', 'profiles': [{'id': service, 'name': name, 'state': state}]}
                continue
            enabled, state, service, provider, name = match.groups()
            reason = ''
            first = state
            if not enabled or state not in STATES:
                reason = 'This VPN connection is disabled or invalid in macOS.'
            else:
                try:
                    check = subprocess.run(['/usr/sbin/scutil', '--nc', 'status', service], capture_output=True, text=True, timeout=5)
                    first = check.stdout.splitlines()[0].strip() if check.stdout else ''
                    if check.returncode or first not in STATES:
                        reason = 'macOS connection status is unavailable.'
                except (OSError, subprocess.TimeoutExpired):
                    reason = 'macOS connection status is unavailable.'
            app = groups.setdefault(provider, {'id': provider, 'name': installed.get(provider) or name or provider, 'manual': False, 'installed': provider in installed, 'available': False, 'profiles': []})
            app['profiles'].append({'id': service, 'name': name, 'state': first, 'available': not reason})
            if not reason:
                app['available'] = True
                app.pop('unavailableReason', None)
            elif not app['available']:
                app['unavailableReason'] = reason
        # Only integrations that cannot use macOS control need fixed vendor code.
        for service, (provider, name) in EXPERIMENTAL.items():
            if provider in installed and provider not in groups:
                groups[provider] = {'id': provider, 'name': name, 'manual': False, 'experimental': True, 'installed': True, 'available': True, 'profiles': [{'id': service, 'name': name, 'state': 'Unknown'}]}
        _cache = sorted(groups.values(), key=lambda a: (bool(a.get('experimental')), a['name'].casefold()))
        _cached_at = time.monotonic()
        return json.loads(json.dumps(_cache))

def profile(service):
    for app in discover():
        if app.get('available') is False: continue
        for item in app['profiles']:
            if item['id'] == service and item.get('available') is not False:
                return {**item, 'app': app['name'], 'provider': app['id'], 'manual': app.get('manual', False), 'experimental': app.get('experimental', False)}
    return None

def catalog():
    """Refresh registered connections every time Settings opens."""
    return discover(refresh=True)

def is_manual(service):
    return service == 'other'

def is_experimental(service):
    return service in EXPERIMENTAL
