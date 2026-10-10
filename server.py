#!/usr/bin/env python3
"""Local Client control page. HTTP runs as the logged-in user, never as root."""
import argparse
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import urlsplit
from setup import SetupManager, SetupError, configuration, topology, open_hotspot_settings
import vpn_apps
import vendor_control

BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
HELPER = '/Library/PrivilegedHelperTools/local.tv-internet-control.sharectl'
PORT = 8765
STATE = Path.home() / "Library/Application Support/TV Internet Control/website/.runtime"

class ControlError(Exception):
    pass

def run(args, timeout=15):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ControlError('The Mac did not respond. Check the connection on the Mac.') from None
    return p.returncode, p.stdout

def vpn_snapshot(selected=None):
    config = configuration()
    if not config:
        raise ControlError('Open Network setup on the Mac to choose a VPN profile and adapters.')
    service = selected or config['vpnService']
    code, listing = run(['/usr/sbin/scutil', '--nc', 'list'])
    rows = []
    for line in listing.splitlines():
        match = re.match(r'^\*\s+\((Connected|Connecting|Disconnected|Disconnecting)\)\s+([A-Fa-f0-9-]{36})\s+VPN \(([^)]+)\)', line)
        if match: rows.append(match.groups())
    selected_row = next((row for row in rows if row[1] == service), None)
    if code or not selected_row or not vpn_apps.profile(service):
        raise ControlError('The selected VPN profile is unavailable. Open its app once, then refresh Network setup.')
    # The app owns its profile selection. Follow its running connection without
    # ever choosing or stopping a different provider's tunnel.
    active = [row for row in rows if row[2] == selected_row[2] and row[0] in ('Connected', 'Connecting')]
    if len(active) == 1: service = active[0][1]
    code, output = run(['/usr/sbin/scutil', '--nc', 'status', service])
    # Raw VPN output may contain credentials. Never expose or log it.
    state = output.splitlines()[0].strip() if output else 'Unknown'
    if code or state not in ('Connected', 'Connecting', 'Disconnected', 'Disconnecting'):
        raise ControlError('The selected VPN status is unavailable. Check its app on the Mac.')
    return service, state

def sharing(action='status'):
    if action not in ('status', 'vpn', 'direct', 'restore'):
        raise ControlError('Invalid sharing action.')
    if not Path(HELPER).is_file():
        raise ControlError('Open Relay on the Mac and complete Network setup.')
    code, output = run(['/usr/bin/sudo', '-n', HELPER, action], timeout=35)
    try:
        result = json.loads(output)
    except (ValueError, TypeError):
        raise ControlError('The sharing helper is unavailable. Run the installer on the Mac.') from None
    if code or not result.get('ok'):
        raise ControlError(result.get('error', 'macOS could not change Internet Sharing.'))
    return result

def wait_vpn(service, wanted):
    stable = 0
    for _ in range(60):
        code, output = run(['/usr/sbin/scutil', '--nc', 'status', service])
        if not code and output.splitlines() and output.splitlines()[0].strip() == wanted:
            stable += 1
            if stable >= 3: return
        else:
            stable = 0
        time.sleep(0.5)
    if wanted == 'Disconnected':
        raise ControlError('The VPN could not stay disconnected. Connect On Demand or automatic reconnection may be enabled. Disable it in your VPN app or macOS VPN settings, then try again.')
    raise ControlError('The selected VPN did not stay ' + wanted.lower() + '. Check its connection and auto-connect settings on the Mac.')

def set_vpn(service, connected):
    launch_vpn_app(service)
    code, _ = run(['/usr/sbin/scutil', '--nc', 'start' if connected else 'stop', service])
    if code:
        raise ControlError('macOS could not ' + ('connect' if connected else 'disconnect') + ' the selected VPN.')
    wait_vpn(service, 'Connected' if connected else 'Disconnected')

def launch_vpn_app(service):
    try:
        profile = vpn_apps.profile(service)
    except RuntimeError:
        profile = None
    if not profile or profile.get('experimental'):
        raise ControlError('The selected VPN connection is unavailable. Refresh detection in Settings.')
    # Launch Services reuses an existing process. -g keeps the current app in front;
    # no new instance, profile selection or credentials are supplied.
    path = vpn_apps.installed_apps(paths=True).get(profile['provider'])
    if not path:
        # A registered system connection can be controlled without an app bundle.
        return
    code, _ = run(['/usr/bin/open', '-g', '-a', path])
    if code:
        raise ControlError('Relay could not open the selected VPN app. Open it manually on the Mac and try again.')

def wait_share_ready(service):
    # NetworkExtension can report Connected before its default route is ready.
    # Wait before changing sharing so a normal startup delay cannot trigger rollback.
    for _ in range(20):
        status = sharing()
        if (status.get('activeVPNService') == service
                and status.get('outboundInterface', '').startswith('utun')):
            return
        _, state = vpn_snapshot(service)
        if state not in ('Connected', 'Connecting'):
            raise ControlError('The VPN disconnected before its sharing route was ready. Check its connection on the Mac.')
        time.sleep(0.5)
    raise ControlError('The VPN is connected but has no usable full internet route. Check its routing settings on the Mac.')

class Controller:
    def __init__(self):
        self.lock = threading.Lock()
        self.operation = {'busy': False, 'id': None, 'message': '', 'error': False}
        self.cached = None
        self.cached_at = 0
        self.poll_lock = threading.Lock()
        self.keep_mac = False
        self.desired_mode = None
        self.vpn_service = None
        self.language = "en"
        try:
            prefs = json.loads((STATE / 'preferences.json').read_text())
            self.keep_mac = prefs.get('keepMacVPN') is True
            self.vpn_service = prefs.get('vpnService') if isinstance(prefs.get('vpnService'), str) else None
            self.language = prefs.get('language') if prefs.get('language') in ('en', 'fa') else 'en'
            self.desired_mode = prefs.get('tvMode') if prefs.get('tvMode') in ('direct', 'vpn') else None
        except (OSError, ValueError, AttributeError):
            pass
        self.stable_target = None
        self.stable_since = 0
        self.retry_after = 0

    def selected_service(self):
        return self.vpn_service or (configuration() or {}).get('vpnService', 'other')

    def preference_values(self):
        return {'keepMacVPN': self.keep_mac, 'vpnService': self.selected_service(), 'language': self.language}

    def settings(self):
        try:
            apps = vpn_apps.catalog()
        except RuntimeError as e:
            raise ControlError(str(e)) from None
        return {**self.preference_values(), 'apps': apps}

    def save_preferences(self):
        STATE.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = STATE / 'preferences.new'
        temporary.write_text(json.dumps({**self.preference_values(), 'tvMode': self.desired_mode}))
        temporary.chmod(0o600)
        temporary.replace(STATE / 'preferences.json')

    def preferences(self, data):
        if not data or not set(data) <= {'keepMacVPN', 'vpnService', 'language'}:
            raise ControlError('Invalid preference.')
        if 'keepMacVPN' in data and not isinstance(data['keepMacVPN'], bool):
            raise ControlError('Choose whether to keep the Mac VPN unchanged.')
        if 'language' in data and data['language'] not in ('en', 'fa'):
            raise ControlError('Choose English or Persian.')
        if 'vpnService' in data:
            value = data['vpnService']
            if not isinstance(value, str) or (value != 'other' and not vpn_apps.profile(value)):
                raise ControlError('The selected VPN connection or profile is unavailable. Refresh detection in Settings.')
        if 'keepMacVPN' in data and vpn_apps.is_experimental(data.get('vpnService', self.selected_service())):
            raise ControlError('This connection requires app-specific controls. Use the commands in Settings.')
        if 'keepMacVPN' in data and vpn_apps.is_manual(data.get('vpnService', self.selected_service())):
            raise ControlError('Other Apps must be managed manually on the Mac.')
        if not self.lock.acquire(blocking=False):
            raise ControlError('Wait for the connection change to finish.')
        try:
            old = self.keep_mac, self.desired_mode, self.vpn_service, self.language
            # Saving a checkbox never waits for the expensive network poll.
            if self.cached and self.cached.get('mode') in ('vpn', 'direct'):
                self.desired_mode = self.cached['mode']
            self.keep_mac = data.get('keepMacVPN', self.keep_mac)
            self.vpn_service = data.get('vpnService', self.vpn_service)
            self.language = data.get('language', self.language)
            try:
                self.save_preferences()
            except OSError:
                self.keep_mac, self.desired_mode, self.vpn_service, self.language = old
                raise ControlError('Relay could not save your preference.') from None
            self.cached_at = 0
            self.retry_after = time.monotonic() + 5
            return self.preference_values()
        finally:
            self.lock.release()

    def snapshot(self):
        with self.poll_lock:
            if self.cached and time.monotonic() - self.cached_at < 2:
                return {**self.cached, **self.preference_values(), 'manualVPN': vpn_apps.is_manual(self.selected_service()), 'operation': dict(self.operation)}
            state, vpn_error = 'Unknown', ''
            share, setup_error = {}, ''
            try:
                share = sharing()
            except ControlError as e:
                setup_error = str(e)
            selected = self.selected_service()
            effective = selected
            try:
                profile = None if vpn_apps.is_manual(selected) else vpn_apps.profile(selected)
                app_name = profile['app'] if profile else 'VPN'
                shared_profile = vpn_apps.profile(share.get('activeVPNService')) if share.get('activeVPNService') else None
                shared_app_name = shared_profile['app'] if shared_profile else 'VPN'
            except RuntimeError:
                app_name, shared_app_name = 'VPN', 'VPN'
            try:
                if vpn_apps.is_experimental(selected):
                    state = 'Unknown'
                elif vpn_apps.is_manual(selected):
                    state = 'Connected' if share.get('activeVPNService') else 'Disconnected'
                else:
                    effective, state = vpn_snapshot(selected)
            except ControlError as e:
                vpn_error = str(e)
            outbound = share.get('outboundInterface', '')
            source = share.get('source')
            local_ok = (share.get('sharingEnabled') and bool(share.get('expectedGateway'))
                        and share.get('gateway') == share.get('expectedGateway')
                        and share.get('sharingDevices') == [share.get('downstreamDevice')] and share.get('ethernetActive'))
            mode = 'unknown'
            if local_ok:
                if source == 'vpn' and share.get('ipv6Guard') and not share.get('directPolicy') and (share.get('activeVPNService') == share.get('vpnService') if 'activeVPNService' in share else state == 'Connected') and outbound.startswith('utun') and outbound == share.get('vpnInterface') and outbound in share.get('natInterfaces', []):
                    mode = 'vpn'
                elif source == 'direct' and share.get('upstreamDevice') in share.get('natInterfaces', []) and share.get('directPolicy'):
                    mode = 'direct'
            mode_error = ''
            if mode == 'unknown' and share and not self.operation['busy']:
                mode_error = 'Sharing is not confirmed. Relay will check the route; use the connection button to retry.'
            config = configuration()
            try:
                network_topology = topology(BASE, (config or {}).get('downstreamDevice', '')) if config else 'router'
            except SetupError:
                network_topology = 'router'
            self.cached = {'topology': network_topology, 'mode': mode, 'vpnState': state, 'sharing': share, 'modeError': mode_error,
                           'setupError': setup_error or vpn_error, 'installed': bool(share),
                           'ready': bool(share), 'needsSetup': config is None,
                           'vpnName': share.get('vpnName', 'VPN'), 'selectedVPNAppName': app_name,
                           'sharedVPNAppName': shared_app_name, 'effectiveVPNService': effective, 'experimentalVPN': vpn_apps.is_experimental(selected),
                           'selectedVPNError': vpn_error, 'upstreamName': share.get('upstreamName', 'internet adapter'),
                           'tvUrl': 'http://' + (share.get('expectedGateway') or (config or {}).get('gateway', '')) + ':' + str(PORT)}
            self.cached_at = time.monotonic()
            return {**self.cached, **self.preference_values(), 'manualVPN': vpn_apps.is_manual(self.selected_service()), 'operation': dict(self.operation)}

    def start(self, action, automatic=False):
        if vpn_apps.is_experimental(self.selected_service()):
            raise ControlError('Use the app controls in Settings. Automatic client sharing is unavailable for this connection.')
        if action not in ('vpn', 'direct', 'reconnect'):
            raise ControlError('Choose VPN or Direct internet.')
        if not self.lock.acquire(blocking=False):
            raise ControlError('A connection change is already in progress.')
        self.operation = {'busy': True, 'id': secrets.token_hex(8), 'action': action,
                          'message': 'Switching connection…', 'error': False}
        threading.Thread(target=self._switch, args=(action, automatic), daemon=True).start()
        return {'accepted': True, 'operation': dict(self.operation)}

    def _switch(self, action, automatic=False):
        previous_share, previous_state, service, vpn_changed, sharing_attempted = None, None, None, False, False
        manual = vpn_apps.is_manual(self.selected_service())
        keep = self.keep_mac or automatic or manual
        try:
            previous_share = sharing()
            if previous_share.get('sharingDevices') != [previous_share.get('downstreamDevice')] or not previous_share.get('sharingEnabled'):
                raise ControlError('Run Network setup on the Mac to select and enable the sharing adapters.')
            if not previous_share.get('ethernetActive'):
                raise ControlError('Connect the selected output adapter to the router or client first.')
            if manual:
                service = previous_share.get('activeVPNService') or 'other'
                previous_state = 'Connected' if previous_share.get('activeVPNService') else 'Disconnected'
            else:
                service, previous_state = vpn_snapshot(self.selected_service())
            if previous_state not in ('Connected', 'Disconnected'):
                raise ControlError('Wait for the VPN to finish its current connection change.')
            if keep and action != 'direct' and previous_state != 'Connected':
                raise ControlError('Connect your VPN manually on the Mac first.' if manual else 'Connect the selected VPN on the Mac first, or uncheck “Keep Mac VPN unchanged”.')
            if keep and action == 'reconnect':
                raise ControlError('Reconnect the VPN in its Mac app while “Keep Mac VPN unchanged” is enabled.')
            active = previous_share.get('activeVPNService')
            if action != 'direct' and not manual and active and active != service:
                raise ControlError('Another VPN is active. Disconnect it manually before connecting the selected app.')
            if not keep:
                wanted = action != 'direct'
                if action == 'reconnect' and previous_state == 'Connected':
                    vpn_changed = True
                    set_vpn(service, False)
                    time.sleep(1)
                if previous_state != ('Connected' if wanted else 'Disconnected') or action == 'reconnect':
                    vpn_changed = True
                    self.operation['message'] = 'Connecting the Mac VPN…' if wanted else 'Disconnecting the Mac VPN…'
                    set_vpn(service, wanted)
            self.operation['message'] = 'Confirming the client sharing route…'
            if action != 'direct' and not manual: wait_share_ready(service)
            sharing_attempted = True
            confirmed = sharing('direct' if action == 'direct' else 'vpn')
            if action != 'direct' and not manual and confirmed.get('vpnService', service) != service:
                raise ControlError('The selected VPN is not the active sharing tunnel.')
            if not automatic:
                self.desired_mode = 'direct' if action == 'direct' else 'vpn'
                self.save_preferences()
            self.operation.update(message='VPN is shared.' if action != 'direct' else 'Direct internet is shared.', error=False)
        except Exception as e:
            error = str(e) if isinstance(e, ControlError) else 'The connection could not be changed.'
            if (vpn_changed or sharing_attempted) and service and previous_state:
                try:
                    if vpn_changed: set_vpn(service, previous_state == 'Connected')
                    source = previous_share.get('source')
                    if source in ('vpn', 'direct'): sharing(source)
                    error += ' Previous connection restored.'
                except Exception:
                    error += ' Recovery needs attention. Check the VPN and Internet Sharing on the Mac.'
            self.operation.update(message=error, error=True)
        finally:
            self.cached_at = 0
            self.operation['busy'] = False
            self.retry_after = time.monotonic() + (30 if self.operation['error'] else 5)
            self.lock.release()

    def reconcile_once(self):
        if vpn_apps.is_experimental(self.selected_service()):
            self.stable_target = None
            return
        if self.lock.locked() or time.monotonic() < self.retry_after:
            self.stable_target = None
            return
        status = self.snapshot()
        if not status['ready'] or status['vpnState'] not in ('Connected', 'Disconnected'):
            self.stable_target = None
            return
        if (status['sharing'].get('activeVPNService') and not vpn_apps.is_manual(self.selected_service())
                and status['sharing']['activeVPNService'] != status.get('effectiveVPNService', self.selected_service())):
            # Selecting an app never steals another app's running tunnel.
            self.stable_target = None
            return
        if self.keep_mac or vpn_apps.is_manual(self.selected_service()):
            target = self.desired_mode or status['sharing'].get('source')
            if status['vpnState'] == 'Disconnected': target = 'direct'
        else:
            target = 'vpn' if status['vpnState'] == 'Connected' else 'direct'
        if target not in ('vpn', 'direct') or status['mode'] == target:
            self.stable_target = None
            return
        if self.stable_target != target:
            self.stable_target, self.stable_since = target, time.monotonic()
        elif time.monotonic() - self.stable_since >= 3:
            try: self.start(target, automatic=True)
            except ControlError: pass
            self.stable_target = None

    def watch(self, stop):
        while not stop.wait(2):
            try: self.reconcile_once()
            except Exception:
                self.retry_after = time.monotonic() + 30

    def experimental_command(self, data):
        if set(data) != {'action'} or data['action'] not in ('open', 'connect', 'disconnect'):
            raise ControlError('Choose a valid experimental VPN command.')
        if not vpn_apps.is_experimental(self.selected_service()):
            raise ControlError('Select a connection with app-specific controls first.')
        if not self.lock.acquire(blocking=False):
            raise ControlError('Wait for the connection change to finish.')
        self.operation = {'busy': True, 'id': secrets.token_hex(8), 'message': 'Sending VPN command…', 'error': False}
        try:
            vendor_control.command(self.selected_service(), data['action'])
            message = 'Request sent. Check the VPN connection on the Mac. Client sharing is unchanged.'
            self.operation.update(message=message)
            return {'message': message}
        except vendor_control.VendorError as e:
            self.operation.update(message=str(e), error=True)
            raise ControlError(str(e)) from None
        finally:
            self.operation['busy'] = False
            self.cached_at = 0
            self.lock.release()

class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    def server_bind(self):
        # HTTPServer's default reverse-DNS lookup can stall on a blocked VPN.
        TCPServer.server_bind(self)
        self.server_name = 'Relay'
        self.server_port = self.server_address[1]
    def __init__(self, address, controller):
        super().__init__(address, Handler)
        self.controller = controller
        self.sessions = {}
        self.auth_lock = threading.Lock()
        self.setup = SetupManager(BASE)

class Handler(BaseHTTPRequestHandler):
    server_version = 'Relay'
    def log_message(self, *_):
        pass  # Never log session cookies or VPN configuration.

    def reply(self, code, data, kind='application/json', cookie=None):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            host = urlsplit('http://' + self.headers.get('Host', '')).hostname
            if not (ip.is_private or ip.is_loopback) or not host:
                return False
            # Numeric LAN hosts avoid DNS rebinding. No public hostnames or proxies.
            if host != 'localhost':
                target = ipaddress.ip_address(host)
                if not (target.is_private or target.is_loopback):
                    return False
                local_ips = set(re.findall(r'\binet (\d+\.\d+\.\d+\.\d+)', run(['/sbin/ifconfig'])[1]))
                if host not in local_ips:
                    return False
            return True
        except (ValueError, ControlError):
            return False

    def local_setup(self):
        host = urlsplit('http://' + self.headers.get('Host', '')).hostname
        return ipaddress.ip_address(self.client_address[0]).is_loopback and host in ('localhost', '127.0.0.1', '::1')

    def session(self):
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            key = cookies['tv_session'].value
        except (KeyError, ValueError):
            return None
        with self.server.auth_lock:
            entry = self.server.sessions.get(key)
            if entry and entry['expires'] > time.time():
                return entry
            self.server.sessions.pop(key, None)
            return None

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error': 'Local network access only.'})
        path = urlsplit(self.path).path
        assets = {'/about': ('about.html', 'text/html; charset=utf-8'),
                  '/about.js': ('about.js', 'text/javascript; charset=utf-8'),
                  '/app-info.json': ('app-info.json', 'application/json; charset=utf-8'),
                  '/shared.css': ('shared.css', 'text/css; charset=utf-8'),
                  '/guide': ('guide.html', 'text/html; charset=utf-8'),
                  '/guide.js': ('guide.js', 'text/javascript; charset=utf-8'),
                  '/guide.css': ('guide.css', 'text/css; charset=utf-8'),
                  '/': ('index.html', 'text/html; charset=utf-8'),
                  '/i18n.js': ('i18n.js', 'text/javascript; charset=utf-8'),
                  '/settings': ('settings.html', 'text/html; charset=utf-8'),
                  '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
                  '/apple-touch-icon.png': ('apple-touch-icon.png', 'image/png'),
                  '/icon-192.png': ('icon-192.png', 'image/png'),
                  '/icon-512.png': ('icon-512.png', 'image/png'),
                  '/preferences-exit.js': ('preferences-exit.js', 'text/javascript; charset=utf-8'),
                  '/settings.js': ('settings.js', 'text/javascript; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/favicon.svg': ('favicon.svg', 'image/svg+xml'),
                  '/fonts.css': ('fonts.css', 'text/css; charset=utf-8'),
                  '/setup': ('settings.html', 'text/html; charset=utf-8'),
                  '/network-defaults.js': ('network-defaults.js', 'text/javascript; charset=utf-8'),
                  '/network.js': ('setup.js', 'text/javascript; charset=utf-8'),
                  '/setup.css': ('setup.css', 'text/css; charset=utf-8')}
        for weight in ('Regular', 'Medium', 'SemiBold', 'Bold'):
            name = 'fonts/Vazirmatn-' + weight + '.woff2'
            assets['/' + name] = (name, 'font/woff2')
        if path == '/setup' and not self.local_setup():
            return self.reply(403, {'error': 'Network setup is available only on the Mac at localhost.'})
        if path in assets:
            file, mime = assets[path]
            body = (BASE / file).read_bytes()
            if mime.startswith('text/html'):
                language = 'fa' if getattr(self.server.controller, 'language', 'en') == 'fa' else 'en'
                direction = 'rtl' if language == 'fa' else 'ltr'
                body = body.decode('utf-8').replace('<html lang="en"', f'<html lang="{language}" dir="{direction}" data-language="{language}"', 1).encode('utf-8')
            return self.reply(200, body, mime)
        session = self.session()
        if path == '/api/session':
            cookie = None
            if not session:
                with self.server.auth_lock:
                    now = time.time()
                    self.server.sessions = {k: v for k, v in self.server.sessions.items() if v['expires'] > now}
                    if len(self.server.sessions) >= 100:
                        oldest = min(self.server.sessions, key=lambda k: self.server.sessions[k]['expires'])
                        self.server.sessions.pop(oldest)
                    token = secrets.token_urlsafe(32)
                    session = {'expires': now + 30 * 86400, 'csrf': secrets.token_urlsafe(32)}
                    self.server.sessions[token] = session
                cookie = f'tv_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000'
            return self.reply(200, {'authenticated': True, 'csrf': session['csrf'], 'localSetup': self.local_setup(), 'language': getattr(self.server.controller, 'language', 'en')}, cookie=cookie)
        if path == '/api/status':
            if not session:
                return self.reply(401, {'error': 'Refresh the control page to reconnect.'})
            return self.reply(200, self.server.controller.snapshot())
        if path == '/api/settings':
            if not session:
                return self.reply(401, {'error': 'Refresh the control page to reconnect.'})
            try:
                return self.reply(200, self.server.controller.settings())
            except ControlError as e:
                return self.reply(409, {'error': str(e)})
        if path == '/api/setup':
            if not self.local_setup():
                return self.reply(403, {'error': 'Network setup is available only on the Mac at localhost.'})
            if not session:
                return self.reply(401, {'error': 'Refresh the setup page.'})
            try:
                return self.reply(200, self.server.setup.snapshot())
            except SetupError as e:
                return self.reply(409, {'error': str(e)})
        self.reply(404, {'error': 'Page not found.'})

    def do_POST(self):
        if not self.allowed():
            return self.reply(403, {'error': 'Local network access only.'})
        origin = self.headers.get('Origin')
        expected = 'http://' + self.headers.get('Host', '')
        if origin and origin != expected:
            return self.reply(403, {'error': 'This request came from a different website.'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'JSON is required.'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError()
            self.connection.settimeout(5)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError()
        except (ValueError, socket.timeout):
            return self.reply(400, {'error': 'Invalid request.'})
        path = urlsplit(self.path).path
        session = self.session()
        if not session:
            return self.reply(401, {'error': 'Refresh the control page to reconnect.'})
        if not hmac.compare_digest(self.headers.get('X-TV-CSRF', ''), session['csrf']):
            return self.reply(403, {'error': 'Refresh the control page and try again.'})
        if path == '/api/hotspot-settings':
            if not self.local_setup():
                return self.reply(403, {'error': 'Network setup is available only on the Mac at localhost.'})
            if data != {'action': 'open'}:
                return self.reply(400, {'error': 'Invalid request.'})
            try:
                return self.reply(200, open_hotspot_settings())
            except (SetupError, OSError, subprocess.TimeoutExpired):
                return self.reply(409, {'error': 'Could not open macOS Sharing settings.'})
        if path == '/api/setup':
            if not self.local_setup():
                return self.reply(403, {'error': 'Network setup is available only on the Mac at localhost.'})
            try:
                return self.reply(202, self.server.setup.start(data, self.server.controller))
            except SetupError as e:
                return self.reply(409, {'error': str(e)})
        if path == '/api/preferences':
            try:
                return self.reply(200, self.server.controller.preferences(data))
            except ControlError as e:
                return self.reply(409, {'error': str(e)})
        if path == '/api/vpn-command':
            try:
                return self.reply(200, self.server.controller.experimental_command(data))
            except ControlError as e:
                return self.reply(409, {'error': str(e)})
        if path == '/api/mode':
            try:
                result = self.server.controller.start(data.get('mode'))
                return self.reply(202, result)
            except ControlError as e:
                return self.reply(409, {'error': str(e)})
        self.reply(404, {'error': 'Action not found.'})

def main():
    global BASE, PORT, STATE
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--assets', type=Path)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--state-dir', type=Path)
    args = parser.parse_args()
    if args.assets:
        BASE = args.assets.resolve()
    PORT = args.port
    if args.state_dir: STATE = args.state_dir.resolve()
    if args.check:
        print('Relay runtime OK; ' + sys.platform, flush=True)
        return
    if os.geteuid() == 0:
        raise SystemExit('Run this website as your normal Mac user, never as root.')
    runtime = STATE
    runtime.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(runtime, 0o700)
    server = AppServer(('0.0.0.0', args.port), Controller())
    stop = threading.Event()
    threading.Thread(target=server.controller.watch, args=(stop,), daemon=True).start()
    print('Relay is running. Open Network setup on this Mac for the client address.', flush=True)
    (runtime / 'server.pid').write_text(str(os.getpid()))
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        (runtime / 'server.pid').unlink(missing_ok=True)

if __name__ == '__main__':
    main()
