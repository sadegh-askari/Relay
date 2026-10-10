"""Experimental fixed vendor commands. No connection state is inferred from exit codes."""
import json
from pathlib import Path
import re
import subprocess
import vpn_apps

class VendorError(Exception): pass

def command(service, action):
    if service not in vpn_apps.EXPERIMENTAL or action not in ('open', 'connect', 'disconnect'):
        raise VendorError('Choose a valid experimental VPN command.')
    provider, name = vpn_apps.EXPERIMENTAL[service]
    path = vpn_apps.installed_apps(paths=True).get(provider)
    if not path: raise VendorError('The selected VPN app is unavailable. Refresh detection in Settings.')
    try:
        launched = subprocess.run(['/usr/bin/open', '-g', '-a', path], capture_output=True, timeout=15)
        if launched.returncode: raise VendorError('Relay could not open the selected VPN app. Open it manually on the Mac and try again.')
        if action == 'open': return
        if service == 'app:happ':
            args = ['/usr/bin/open', '-g', '-a', path, 'happ://' + action]
        else:
            binary = str(Path(path) / 'Contents/MacOS/OpenVPN Connect')
            # Connect's shortcut CLI needs an internal ID. Reuse its last attempted
            # connection; do not choose an arbitrary profile or expose credentials.
            data = json.loads((Path.home() / 'Library/Application Support/OpenVPN Connect/config.json').read_text())
            root = json.loads(data['persist:root'])
            status = json.loads(root['status']) if isinstance(root['status'], str) else root['status']
            previous = status.get('connectedProfile') or status.get('previouslyConnectedProfile') or {}
            identity = str(previous.get('id', ''))
            if not re.fullmatch(r'[0-9]{1,30}', identity):
                raise VendorError('Attempt your chosen connection once in OpenVPN Connect, then return to Relay. Relay uses that last attempted connection.')
            listing = subprocess.run([binary, '--list-profiles'], capture_output=True, text=True, timeout=15)
            rows = json.loads(listing.stdout)
            if isinstance(rows, dict): rows = rows.get('profiles', [])
            if listing.returncode or not isinstance(rows, list) or not any(str(row.get('id')) == identity for row in rows if isinstance(row, dict)):
                raise VendorError('The last OpenVPN connection is unavailable. Choose it in OpenVPN Connect first.')
            args = [binary, '--' + action + '-shortcut=' + identity]
        result = subprocess.run(args, capture_output=True, timeout=15)
        if result.returncode: raise VendorError('The VPN app rejected the command. Check its window on the Mac.')
    except VendorError: raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired):
        raise VendorError('The VPN command could not be completed. Check the app on the Mac.') from None
