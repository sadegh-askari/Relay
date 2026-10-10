# Relay verification

- 70 Python regression checks pass, including provider identity, stable connection status, route readiness, rollback, independent Mac VPN mode, setup validation, sessions/CSRF, LAN stylesheet access, saved-language HTML, catalog grouping, mobile assets and On Demand failure messaging. Eight adapter selection scenarios also pass.
- The exported source builds a universal Apple Silicon/Intel app. Its ad-hoc code signature verifies. The installer image checksum verifies.
- Home fits a 1280 × 720 viewport without scrolling. All four pages use a 44 px header and 40 px logo. Persian checking text is present on first display. Settings Save / Discard / Cancel was verified for language and adapter edits; the language save-and-exit path succeeded and English was restored.
- Refresh detection immediately disables its panel and displays a spinner. Network configuration changes were not applied during these UI checks.
- Live V2Box Pro disconnect and Direct sharing succeeded after disabling On Demand inside the VPN app. Reconnection and VPN sharing also succeeded. The user subsequently selected Direct mode. Relay does not modify On Demand automatically.

Earlier tests confirmed regular V2Box connection, status and sharing. Shadowrocket and AmneziaWG responded to control, but their configurations were not usable for an internet test. OpenVPN Connect and Happ accepted app-specific commands; reliable status and automatic sharing remain unverified and disabled. Cisco is omitted.

Hotspot setup adopts a hotspot configured once in macOS Internet Sharing. Actual Ethernet-to-Wi-Fi hotspot traffic on this Mac, a physical Intel Mac, earlier macOS versions and a fresh installation on another Mac remain unverified. Local route checks do not prove streaming throughput, VPN server authentication or internet reachability. iPhone home-screen metadata and icon assets were checked; installation on a physical phone was not tested.

This is an ad-hoc signed personal build, not an Apple-notarized distribution.

## Relay 1.3.0: dynamic discovery

72 regression tests pass. VPN selection discovers registered macOS connections without a provider allowlist; read-only status checks gate selection. Invalid, disabled, unresponsive and legacy connections that cannot be shared remain visible with reasons. Registered connections without installed app bundles can use system controls. OpenVPN/Happ app commands remain separate. New-provider connect/disconnect and missing-app behavior are covered with simulated system responses; newly discovered providers were not connected live. Existing matching tunnel and rollback checks remain in place. About is a short English/Persian purpose overview. Settings initial loading and action spacing were checked in the installed app.
