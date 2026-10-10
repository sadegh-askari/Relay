# Relay 1.2.0 verification

70 automated tests pass: provider selection, connection stability, matching-route readiness, automatic app launch, rollback, independent Mac VPN preferences, manual and experimental modes, network layout validation, hotspot adoption, local sessions/CSRF and restricted hotspot-settings access. All JavaScript files parse successfully.

## Live VPN checks

Both V2Box Pro and regular V2Box connected, reported Connected, and entered VPN sharing with the correct active service and utun route. Both returned to Direct sharing and Disconnected without errors. The regression was an incorrect readiness comparison against the currently shared VPN interface, which is empty while still in Direct mode. Readiness now uses the selected active service and its outbound tunnel.

Shadowrocket and AmneziaWG respond to macOS connection control. The user confirmed manually disconnecting them because no valid profile was configured. Successful provider internet connectivity is not claimed from those tests.

OpenVPN Connect 3.8.2 accepted connect/disconnect shortcut commands; its old profile failed authentication, then returned to Ready to connect. Settings now exposes experimental commands using the last attempted profile. A command acknowledgment does not mean Connected. Happ 2.9.1 responded to happ://connect and happ://disconnect in Proxy mode. It is also experimental. Both lack verified automatic sharing/status support, which remains disabled. Cisco was inspected but omitted, as requested.

## Routerless setup

Settings includes a router layout and an Ethernet-input/Wi-Fi-output hotspot layout. The selection validates distinct adapters, subnet overlap and matching preconfigured Internet Sharing input/output. Topology is inferred from the configured output, including older helpers. The main diagram smoothly hides Router for hotspot mode and uses the selected VPN app name at the VPN node. Client uses a person icon. All user-facing TV and Device wording was replaced; legacy internal paths and HTTP identifiers remain for compatibility.

The Configure hotspot button was tested live: it opened the Internet Sharing sheet directly. No network settings were changed during this inspection. Hotspot name/password/security still require a one-time step in that sheet. Public CoreWLAN IBSS creation is deprecated and supports older ad-hoc security; no supported public secure Internet Sharing credential configuration API was identified. Relay does not use guessed password blobs, obsolete encryption or new Accessibility permissions.

The hotspot diagram and simulated VPN/Direct transitions were inspected using an isolated preview. Actual Ethernet-to-Wi-Fi hotspot traffic and VPN switching over that hotspot were not tested: the current Mac receives modem internet over Wi-Fi and shares to the router by Ethernet. Hardware rewiring is required for that test. The existing wired configuration and Persian preference were preserved; new installations default to English.

## Adapter defaults and RTL

Internet input and sharing output stay side by side. Layout changes remember per-layout selections, retain compatible choices, rank active adapters with addresses for missing inputs, and avoid selecting the same physical adapter for both directions. Shared address and VPN preferences are preserved. Eight selection scenarios pass. Persian text routes use left arrows; graphical route arrows and packet flow follow right-to-left layout.

## Guide page

A short English/Persian Guide compares With Router and Without Router using illustrated flows, adapter and cable labels, and three connection steps per layout. Home retains Guide and Settings at the top right. Secondary pages replace those links with a single Close button that returns Home. It explains that VPN/Direct switching works in either layout.

## Network Settings rendering fix

Network clients now receive setup.css; a broad /setup prefix restriction previously blocked that stylesheet and left Settings unstyled at the LAN address. The exact /setup page and adapter/hotspot APIs retain their local-only checks. The focused HTTP regression passes: remote Settings CSS returns text/css while network setup access remains denied.

## Package and limits

Universal Apple Silicon/Intel app and runtime; native and Rosetta runtime checks pass. Deep strict ad-hoc code signature validation passes. The app was installed and relaunched. The DMG includes the updated app, short README, Applications shortcut and removal script.

Earlier macOS versions, a physical Intel Mac, fresh setup on another Mac, and successful OpenVPN/Happ full-tunnel forwarding remain unverified. Route checks establish local configuration, not throughput or server authentication.

Primary references: [Apple Internet Sharing](https://support.apple.com/en-am/guide/mac-help/mchlp1540/mac), [CoreWLAN IBSS API](https://developer.apple.com/documentation/corewlan/cwinterface/startibssmode(withssid:security:channel:password:)), [OpenVPN shortcut commands](https://support.openvpn.com/hc/en-us/articles/41389237225499-OpenVPN-Connect-Workaround-for-CLI-Based-Connect-Disconnect-on-macOS-and-Windows), [Happ developer docs](https://www.happ.su/main/dev-docs).
