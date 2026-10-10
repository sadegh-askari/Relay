"""Discover installed bundles with usable macOS NetworkExtension control profiles.
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
# Explicit user-approved providers. Installed apps outside this list are never offered or controlled.
SUPPORTED = {
 'dev.hexasoftware.V2Box': 'V2Box Pro', 'hossin.asaadi.V2Box': 'V2Box',
 'com.liguangming.Shadowrocket': 'Shadowrocket', 'com.effect.streisand': 'Streisand',
 'com.wireguard.macos': 'WireGuard', 'org.amnezia.awg': 'AmneziaWG',
 'com.domosekai.vpnclient': 'SSTP Connect', 'com.happ.www': 'Happ'
}
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
    global _cache, _cached_at
    with _lock:
        if _cache is not None and not refresh and time.monotonic() - _cached_at < 30:
            return json.loads(json.dumps(_cache))
        installed = installed_apps()
        try:
            listing = subprocess.run(['/usr/sbin/scutil', '--nc', 'list'], capture_output=True, text=True, timeout=10)
            if listing.returncode: raise OSError()
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('VPN app detection failed. Try again on the Mac.') from None
        groups = {}
        for line in listing.stdout.splitlines():
            match = re.match(r'^\*\s+\((Connected|Connecting|Disconnected|Disconnecting)\)\s+([A-Fa-f0-9-]{36})\s+VPN \(([^)]+)\)\s+"([^"]*)"', line)
            if not match: continue
            state, service, provider, profile = match.groups()
            if provider not in installed or provider not in SUPPORTED: continue
            # A configured profile must answer the same macOS interface used to start/stop it.
            try:
                check = subprocess.run(['/usr/sbin/scutil', '--nc', 'status', service], capture_output=True, text=True, timeout=5)
                first = check.stdout.splitlines()[0].strip() if check.stdout else ''
                if check.returncode or first not in STATES: continue
            except (OSError, subprocess.TimeoutExpired): continue
            app = groups.setdefault(provider, {'id': provider, 'name': SUPPORTED[provider], 'manual': False, 'profiles': []})
            app['profiles'].append({'id': service, 'name': profile, 'state': first})
        _cache = sorted(groups.values(), key=lambda a: a['name'].casefold())
        for service, (provider, name) in EXPERIMENTAL.items():
            if provider in installed and provider not in groups:
                _cache.append({'id': provider, 'name': name, 'manual': False, 'experimental': True,
                               'profiles': [{'id': service, 'name': name, 'state': 'Unknown'}]})
        _cache.sort(key=lambda a: a['name'].casefold())
        _cached_at = time.monotonic()
        return json.loads(json.dumps(_cache))

def profile(service):
    for app in discover():
        for item in app['profiles']:
            if item['id'] == service: return {**item, 'app': app['name'], 'provider': app['id'], 'manual': app.get('manual', False), 'experimental': app.get('experimental', False)}
    return None

def catalog():
    """Fresh installed-app inventory for Settings; only configured entries are selectable."""
    detected = {app['id']: app for app in discover(refresh=True)}
    installed = installed_apps()
    providers = {**SUPPORTED, **{provider: name for provider, name in EXPERIMENTAL.values()}}
    result = []
    for provider, name in providers.items():
        app = detected.get(provider, {'id': provider, 'name': name, 'profiles': [], 'manual': False})
        result.append({**app, 'installed': provider in installed, 'available': provider in detected})
    return sorted(result, key=lambda app: (not app['installed'], app['name'].casefold()))

def is_manual(service):
    return service == 'other'

def is_experimental(service):
    return service in EXPERIMENTAL
