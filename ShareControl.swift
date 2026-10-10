import Foundation
import SystemConfiguration
import Darwin

// Root-owned helper. Browsers can invoke only four fixed operations.
// Configuration is validated and installed separately with administrator consent.
let stateDirectory = "/Library/Application Support/TV Internet Control"
let configurationPath = stateDirectory + "/configuration.json"
let prefID = "com.apple.nat.plist" as CFString
struct ControlError: Error { let message: String }
struct Configuration: Codable {
    let version: Int
    let upstreamService: String
    let downstreamDevice: String
    let vpnService: String
    let gateway: String
}
struct Service {
    let ref: SCNetworkService
    let id: String
    let name: String
    let device: String
    let type: String
    let vpn: Bool
    let provider: String
}
func fail(_ message: String) throws -> Never { throw ControlError(message: message) }
func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        print(String(decoding: data, as: UTF8.self))
    }
}
func run(_ path: String, _ arguments: [String]) -> String {
    let p = Process(), pipe = Pipe()
    p.executableURL = URL(fileURLWithPath: path); p.arguments = arguments
    p.standardOutput = pipe; p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return "" }
    let data = pipe.fileHandleForReading.readDataToEndOfFile(); p.waitUntilExit()
    return p.terminationStatus == 0 ? String(decoding: data, as: UTF8.self) : ""
}
func preferences() throws -> SCPreferences {
    guard let p = SCPreferencesCreate(nil, "TV Internet Control" as CFString, prefID) else {
        try fail("Cannot access Internet Sharing preferences.")
    }
    return p
}
func nat(_ p: SCPreferences) -> [String: Any] { SCPreferencesGetValue(p, "NAT" as CFString) as? [String: Any] ?? [:] }
func serviceList() throws -> [Service] {
    guard let p = SCPreferencesCreate(nil, "TV Internet Control" as CFString, nil),
          let set = SCNetworkSetCopyCurrent(p), let list = SCNetworkSetCopyServices(set) as? [SCNetworkService] else {
        try fail("Cannot inspect this Mac's network services.")
    }
    let vpnLines = run("/usr/sbin/scutil", ["--nc", "list"]).split(separator: "\n")
    return list.compactMap { service in
        guard SCNetworkServiceGetEnabled(service), let interface = SCNetworkServiceGetInterface(service),
              let id = SCNetworkServiceGetServiceID(service) as String? else { return nil }
        let name = SCNetworkServiceGetName(service) as String? ?? id
        let device = SCNetworkInterfaceGetBSDName(interface) as String? ?? ""
        let type = SCNetworkInterfaceGetInterfaceType(interface) as String? ?? ""
        let vpn = vpnLines.contains { $0.contains(id) && $0.contains("VPN (") && !$0.contains("(Invalid)") }
        let row = vpnLines.first { $0.contains(id) && $0.contains("VPN (") } ?? ""
        let provider = row.components(separatedBy: "VPN (").dropFirst().first?.components(separatedBy: ")").first ?? ""
        return Service(ref: service, id: id, name: name, device: device, type: type, vpn: vpn, provider: provider)
    }.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
}
func interfaceState(_ device: String) -> (String?, Bool) {
    let out = run("/sbin/ifconfig", [device])
    let ip = out.split(separator: "\n").compactMap { line -> String? in
        let w = line.split(whereSeparator: { $0.isWhitespace })
        return w.count > 1 && w[0] == "inet" ? String(w[1]) : nil
    }.first
    return (ip, out.contains("status: active"))
}
func globalInterface() -> String {
    guard let store = SCDynamicStoreCreate(nil, "TV Internet Control" as CFString, nil, nil),
          let state = SCDynamicStoreCopyValue(store, "State:/Network/Global/IPv4" as CFString) as? [String: Any] else { return "" }
    return state["PrimaryInterface"] as? String ?? ""
}
func vpnInterface(_ service: String) -> String {
    guard let store = SCDynamicStoreCreate(nil, "TV Internet Control" as CFString, nil, nil),
          let state = SCDynamicStoreCopyValue(store, "State:/Network/Service/\(service)/IPv4" as CFString) as? [String: Any] else { return "" }
    return state["InterfaceName"] as? String ?? ""
}
func natInterfaces() -> [String] {
    let out = run("/sbin/pfctl", ["-a", "com.apple.internet-sharing/shared_v4", "-s", "nat"]) + "\n" + run("/sbin/pfctl", ["-a", policyAnchor, "-s", "nat"])
    return Array(Set(out.split(separator: "\n").compactMap { line -> String? in
        let w = line.split(whereSeparator: { $0.isWhitespace })
        return w.count > 2 && w[0] == "nat" && w[1] == "on" ? String(w[2]) : nil
    })).sorted()
}
func hasGateway(_ gateway: String) -> Bool {
    run("/sbin/ifconfig", []).split(separator: "\n").contains { line in
        let w = line.split(whereSeparator: { $0.isWhitespace })
        return w.count > 1 && w[0] == "inet" && w[1] == Substring(gateway)
    }
}
func privateGateway(_ value: String) -> Bool {
    let parts = value.split(separator: ".", omittingEmptySubsequences: false)
    guard parts.count == 4, parts.allSatisfy({ Int($0).map { $0 >= 0 && $0 <= 255 } ?? false }),
          parts.map(String.init).joined(separator: ".") == parts.compactMap({ Int($0).map(String.init) }).joined(separator: "."), parts[3] == "1" else { return false }
    let first = Int(parts[0])!, second = Int(parts[1])!
    return first == 10 || (first == 172 && (16...31).contains(second)) || (first == 192 && second == 168)
}
func ipv4(_ text: String) -> UInt32? {
    let pieces = text.split(separator: ".", omittingEmptySubsequences: false)
    guard pieces.count == 4 else { return nil }
    var result: UInt32 = 0
    for piece in pieces {
        guard let number = UInt32(piece), number <= 255, String(number) == piece else { return nil }
        result = (result << 8) | number
    }
    return result
}
func interfaceMask(_ device: String) -> String? {
    for line in run("/sbin/ifconfig", [device]).split(separator: "\n") {
        let words = line.split(whereSeparator: { $0.isWhitespace })
        if words.count >= 4 && words[0] == "inet" && words[2] == "netmask" {
            let raw = String(words[3])
            if raw.hasPrefix("0x"), let value = UInt32(raw.dropFirst(2), radix: 16) {
                return [24,16,8,0].map { String((value >> $0) & 255) }.joined(separator: ".")
            }
            return ipv4(raw) != nil ? raw : nil
        }
    }
    return nil
}
let policyAnchor = "com.apple/000.relay"
let policyPath = stateDirectory + "/relay-pf.conf"
func sharedInterface(_ config: Configuration) -> String {
    var current = ""
    for line in run("/sbin/ifconfig", []).split(separator: "\n") {
        if !line.hasPrefix("\t") && !line.hasPrefix(" "), let name = line.split(separator: ":").first { current = String(name) }
        let words = line.split(whereSeparator: { $0.isWhitespace })
        if words.count > 1 && words[0] == "inet" && words[1] == Substring(config.gateway) {
            if current.range(of: "^(bridge|en)[0-9]+$", options: .regularExpression) != nil { return current }
        }
    }
    return ""
}
func upstreamRouter(_ service: String) -> String {
    guard let store = SCDynamicStoreCreate(nil, "Relay" as CFString, nil, nil),
          let state = SCDynamicStoreCopyValue(store, "State:/Network/Service/\(service)/IPv4" as CFString) as? [String: Any],
          let router = state["Router"] as? String, ipv4(router) != nil else { return "" }
    return router
}
func upstreamDNS(_ service: Service) -> String {
    guard let store = SCDynamicStoreCreate(nil, "Relay" as CFString, nil, nil) else { return upstreamRouter(service.id) }
    let own = interfaceState(service.device).0
    for prefix in ["State", "Setup"] {
        if let values = SCDynamicStoreCopyValue(store, "\(prefix):/Network/Service/\(service.id)/DNS" as CFString) as? [String: Any],
           let servers = values["ServerAddresses"] as? [String],
           let address = servers.first(where: { value in
               guard let ip = ipv4(value) else { return false }
               return ip != 0 && ip >> 24 != 127 && ip >> 24 < 224 && value != own
           }) { return address }
    }
    return upstreamRouter(service.id)
}
func pfHooks() -> Bool {
    let filters = run("/sbin/pfctl", ["-s", "rules"])
    let translations = run("/sbin/pfctl", ["-s", "nat"])
    return filters.contains("anchor \"com.apple/*\"") && translations.contains("nat-anchor \"com.apple/*\"")
}
func directPolicy(_ config: Configuration, _ upstream: Service) -> Bool {
    let bridge = sharedInterface(config), router = upstreamRouter(upstream.id)
    guard !bridge.isEmpty, !router.isEmpty, pfHooks() else { return false }
    let rules = run("/sbin/pfctl", ["-a", policyAnchor, "-s", "rules"])
    let translations = run("/sbin/pfctl", ["-a", policyAnchor, "-s", "nat"])
    return rules.contains("relay-direct") && rules.contains("route-to (\(upstream.device) \(router))") && rules.contains(bridge)
        && translations.contains("nat on \(upstream.device)") && rules.contains("relay-dns")
}
func pfLoad(_ rules: String) throws {
    try Data(rules.utf8).write(to: URL(fileURLWithPath: policyPath), options: .atomic)
    try FileManager.default.setAttributes([.ownerAccountID: 0, .groupOwnerAccountID: 0, .posixPermissions: 0o600], ofItemAtPath: policyPath)
    for arguments in [["-n", "-a", policyAnchor, "-f", policyPath], ["-a", policyAnchor, "-f", policyPath]] {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/sbin/pfctl"); process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice; process.standardError = FileHandle.nullDevice
        try process.run(); process.waitUntilExit()
        guard process.terminationStatus == 0 else { try fail("macOS rejected Relay's forwarding rules. Previous sharing will be restored.") }
    }
}
func installPolicy(_ mode: String, _ config: Configuration, _ upstream: Service) throws {
    let bridge = sharedInterface(config), router = upstreamRouter(upstream.id)
    guard !bridge.isEmpty, pfHooks() else { try fail("Relay needs the active sharing bridge and standard macOS firewall hooks.") }
    let incoming = bridge == config.downstreamDevice ? bridge : "{ \(bridge), \(config.downstreamDevice) }"
    let linkTable = "table <relay_link6> const { fe80::/10, ff02::/16 }\n"
    let ipv6 = "block return in quick on \(incoming) inet6 from any to ! <relay_link6> label \"relay-ipv4-only\"\n"
    if mode != "direct" { try pfLoad(linkTable + ipv6); return }
    guard !router.isEmpty, interfaceState(upstream.device).1 else {
        try fail("Direct sharing needs an active internet adapter and its IPv4 router.")
    }
    let dns = upstreamDNS(upstream)
    let subnet = config.gateway.split(separator: ".").prefix(3).joined(separator: ".") + ".0/24"
    // This anchor changes only forwarded client traffic, never the Mac's own VPN routes.
    // DNS addressed to the sharing proxy is sent to the upstream router, avoiding VPN fake-DNS addresses.
    let hosts = run("/sbin/ifconfig", []).split(separator: "\n").compactMap { line -> String? in
        let words = line.split(whereSeparator: { $0.isWhitespace })
        if words.count > 1 && words[0] == "inet" && ipv4(String(words[1])) != nil { return String(words[1]) }
        return nil
    }.joined(separator: ", ")
    let rules = """
    table <relay_local4> const { \(hosts), \(subnet), 224.0.0.0/4, 255.255.255.255 }
    table <relay_link6> const { fe80::/10, ff02::/16 }
    nat on \(upstream.device) inet from \(subnet) to any -> (\(upstream.device))
    rdr on \(incoming) inet proto { tcp, udp } from \(subnet) to \(config.gateway) port 53 -> \(dns) port 53
    pass in quick on \(incoming) route-to (\(upstream.device) \(router)) inet proto { tcp, udp } from \(subnet) to \(dns) port 53 keep state label "relay-dns"
    pass in quick on \(incoming) route-to (\(upstream.device) \(router)) inet from \(subnet) to ! <relay_local4> keep state label "relay-direct"
    block return in quick on \(incoming) inet6 from any to ! <relay_link6> label "relay-ipv4-only"
    """
    try pfLoad(rules + "\n")
    guard directPolicy(config, upstream) else { try fail("Direct forwarding rules were not confirmed. Previous sharing will be restored.") }
}
func clearClientStates(_ config: Configuration) {
    let subnet = config.gateway.split(separator: ".").prefix(3).joined(separator: ".") + ".0/24"
    _ = run("/sbin/pfctl", ["-k", subnet]) // Never flush the Mac's complete firewall/state table.
}
func validateConfiguration(_ config: Configuration) throws -> (Service, Service, Service?) {
    let list = try serviceList()
    guard config.version == 1, privateGateway(config.gateway),
          let upstream = list.first(where: { $0.id == config.upstreamService && !$0.vpn }),
          let output = list.first(where: { $0.device == config.downstreamDevice && !$0.vpn }),
          ["Ethernet", "IEEE80211"].contains(upstream.type),
          ["Ethernet", "IEEE80211"].contains(output.type),
          !upstream.device.isEmpty, upstream.device != output.device else {
        try fail("Select separate Wi-Fi/Ethernet adapters and an available macOS VPN profile. Use a private .1 address with a /24 subnet.")
    }
    guard [upstream.device, output.device].allSatisfy({ $0.range(of: "^en[0-9]+$", options: .regularExpression) != nil }) else {
        try fail("This release supports native Wi-Fi and Ethernet adapters with en interfaces.")
    }
    let shared = ipv4(config.gateway)! & 0xffffff00
    for service in list where !service.vpn && service.device != output.device {
        if let address = interfaceState(service.device).0, let ip = ipv4(address), let mask = interfaceMask(service.device) {
            let maskValue = ipv4(mask)!
            if (shared & maskValue) == (ip & maskValue) || (shared & 0xffffff00) == (ip & 0xffffff00) {
                try fail("The shared subnet overlaps another connected network. Choose a different shared address.")
            }
        }
    }
    return (upstream, output, list.first(where: { $0.id == config.vpnService && $0.vpn }))
}
func loadConfiguration() throws -> Configuration {
    let attrs = try FileManager.default.attributesOfItem(atPath: configurationPath)
    guard (attrs[.ownerAccountID] as? NSNumber)?.intValue == 0,
          ((attrs[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o022 == 0 else {
        try fail("The sharing configuration must be owned by root and protected from changes.")
    }
    let config = try JSONDecoder().decode(Configuration.self, from: Data(contentsOf: URL(fileURLWithPath: configurationPath)))
    _ = try validateConfiguration(config)
    return config
}
func configurationJSON(_ c: Configuration) -> [String: Any] {
    ["version": c.version, "upstreamService": c.upstreamService, "downstreamDevice": c.downstreamDevice, "vpnService": c.vpnService, "gateway": c.gateway]
}
func inventory() throws -> [String: Any] {
    let list = try serviceList(), prefs = try preferences(), sharing = nat(prefs)
    let rows: [[String: Any]] = list.filter { $0.vpn || ["Ethernet", "IEEE80211"].contains($0.type) }.map { service in
        let state = service.device.isEmpty ? (nil, false) : interfaceState(service.device)
        return ["id": service.id, "name": service.name, "device": service.device, "type": service.type, "vpn": service.vpn, "active": state.1, "address": state.0 ?? "", "mask": interfaceMask(service.device) ?? "24", "provider": service.provider]
    }
    // Return topology only, never the AirPort password or raw VPN settings.
    return ["ok": true, "services": rows, "primaryInterface": globalInterface(),
            "existing": ["enabled": (sharing["Enabled"] as? NSNumber)?.boolValue ?? false,
                         "devices": sharing["SharingDevices"] as? [String] ?? [],
                         "primaryService": sharing["PrimaryService"] as? String ?? "",
                         "gateway": sharing["SharingNetworkNumberStart"] as? String ?? ""],
            "configuration": (try? loadConfiguration()).map(configurationJSON) ?? [:]]
}
func status() throws -> [String: Any] {
    let config = try loadConfiguration(), (upstream, output, _) = try validateConfiguration(config)
    let sharing = nat(try preferences()), source = sharing["PrimaryService"] as? String ?? ""
    let list = try serviceList(), vpn = list.first { $0.vpn && $0.id == source }
    let active = list.first { $0.vpn && !vpnInterface($0.id).isEmpty && vpnInterface($0.id) == globalInterface() && globalInterface().hasPrefix("utun") }
    let mode = vpn != nil ? "vpn" : source == upstream.id ? "direct" : "other"
    return ["ok": true, "capabilities": ["activeVPNSelection"], "source": mode, "sharingEnabled": (sharing["Enabled"] as? NSNumber)?.boolValue ?? false,
            "sharingDevices": sharing["SharingDevices"] as? [String] ?? [],
            "gateway": hasGateway(config.gateway) ? config.gateway : "", "expectedGateway": config.gateway,
            "downstreamDevice": output.device, "downstreamName": output.name,
            "upstreamDevice": upstream.device, "upstreamName": upstream.name,
            "vpnService": vpn?.id ?? "", "vpnName": vpn?.name ?? active?.name ?? "VPN",
            "activeVPNService": active?.id ?? "", "activeVPNName": active?.name ?? "",
            "ethernetActive": interfaceState(output.device).1,
            "outboundInterface": globalInterface(), "vpnInterface": vpn.map { vpnInterface($0.id) } ?? "", "natInterfaces": natInterfaces(),
            "directPolicy": directPolicy(config, upstream), "ipv6Guard": run("/sbin/pfctl", ["-a", policyAnchor, "-s", "rules"]).contains("relay-ipv4-only"), "upstreamRouter": upstreamRouter(upstream.id), "directDNS": upstreamDNS(upstream), "sharedInterface": sharedInterface(config)]
}
func validateSharing(_ sharing: [String: Any], _ config: Configuration) throws {
    guard (sharing["Enabled"] as? NSNumber)?.boolValue == true,
          sharing["SharingDevices"] as? [String] == [config.downstreamDevice] else {
        try fail("Internet Sharing does not match the selected output adapter. Run Network setup on the Mac.")
    }
    guard interfaceState(config.downstreamDevice).1 else { try fail("Connect the selected output adapter to the router or client first.") }
}
func apply(_ value: [String: Any]?) throws {
    let prefs = try preferences()
    guard SCPreferencesLock(prefs, true) else { try fail("Internet Sharing preferences are busy.") }
    defer { SCPreferencesUnlock(prefs) }
    let stored = value.map { SCPreferencesSetValue(prefs, "NAT" as CFString, $0 as CFDictionary) } ?? SCPreferencesRemoveValue(prefs, "NAT" as CFString)
    guard stored, SCPreferencesCommitChanges(prefs), SCPreferencesApplyChanges(prefs) else {
        try fail("macOS could not apply Internet Sharing (\(String(cString: SCErrorString(SCError())))).")
    }
}
func sharingValue(_ old: [String: Any], _ config: Configuration, _ source: Service, _ direct: Bool) -> [String: Any] {
    var next = old
    next["Enabled"] = 1; next["SharingDevices"] = [config.downstreamDevice]; next["PrimaryService"] = source.id
    var primary = old["PrimaryInterface"] as? [String: Any] ?? [:]
    primary["Device"] = direct ? source.device : ""
    primary["PrimaryUserReadable"] = source.name; primary["HardwareKey"] = ""; primary["Enabled"] = 0
    next["PrimaryInterface"] = primary
    next["SharingNetworkNumberStart"] = config.gateway
    next["SharingNetworkNumberEnd"] = config.gateway.split(separator: ".").prefix(3).joined(separator: ".") + ".254"
    next["SharingNetworkMask"] = "255.255.255.0"
    return next
}
func change(_ mode: String) throws -> [String: Any] {
    guard geteuid() == 0 else { try fail("Administrator helper is required.") }
    let config = try loadConfiguration(), (upstream, _, _) = try validateConfiguration(config)
    // Only an enabled NetworkExtension service owning the full IPv4 tunnel can be shared.
    // No browser-controlled interface or new privileged command is accepted.
    let vpn = try serviceList().first { $0.vpn && !vpnInterface($0.id).isEmpty && vpnInterface($0.id) == globalInterface() && globalInterface().hasPrefix("utun") }
    let old = nat(try preferences()); try validateSharing(old, config)
    let outbound = globalInterface()
    if mode == "vpn" && vpn == nil {
        try fail("The selected VPN is not the active full internet tunnel. Connect it first or check its routing settings.")
    }
    let oldPolicy = (try? String(contentsOfFile: policyPath, encoding: .utf8)) ?? ""
    do {
        try apply(sharingValue(old, config, mode == "vpn" ? vpn! : upstream, mode == "direct"))
        for _ in 0..<40 {
            if hasGateway(config.gateway), natInterfaces().contains(mode == "direct" ? upstream.device : outbound) { break }
            Thread.sleep(forTimeInterval: 0.25)
        }
        try installPolicy(mode, config, upstream)
        let state = try status()
        guard state["gateway"] as? String == config.gateway, state["source"] as? String == mode,
              (state["natInterfaces"] as? [String] ?? []).contains(mode == "direct" ? upstream.device : outbound) else {
            try fail("The sharing gateway and NAT rule did not settle.")
        }
        clearClientStates(config)
        return state
    } catch {
        try? apply(old)
        try? pfLoad(oldPolicy)
        throw error
    }
}

func configure(_ path: String) throws -> [String: Any] {
    guard geteuid() == 0 else { try fail("Network setup requires macOS administrator authorization.") }
    let config = try JSONDecoder().decode(Configuration.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    let (upstream, output, _) = try validateConfiguration(config), prefs = try preferences(), old = nat(prefs)
    let oldConfig = try? Data(contentsOf: URL(fileURLWithPath: configurationPath))
    let oldPolicy = (try? String(contentsOfFile: policyPath, encoding: .utf8)) ?? ""
    // Wi-Fi credentials and AP setup stay in Apple's native UI. Adopt only an already enabled hotspot.
    if output.type == "IEEE80211" && ((old["Enabled"] as? NSNumber)?.boolValue != true || old["SharingDevices"] as? [String] != [output.device]) {
        try fail("Configure and enable Internet Sharing to Wi-Fi in System Settings first, including its network name and password. Then return to setup.")
    }
    let backup = stateDirectory + "/original-sharing.plist"
    if !FileManager.default.fileExists(atPath: backup) {
        let original: [String: Any] = old.isEmpty ? [:] : ["NAT": old]
        try PropertyListSerialization.data(fromPropertyList: original, format: .xml, options: 0).write(to: URL(fileURLWithPath: backup), options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: backup)
    }
    let encoded = try JSONEncoder().encode(config)
    try encoded.write(to: URL(fileURLWithPath: configurationPath), options: .atomic)
    try FileManager.default.setAttributes([.posixPermissions: 0o644, .ownerAccountID: 0, .groupOwnerAccountID: 0], ofItemAtPath: configurationPath)
    let currentServices = try serviceList()
    let alreadyMatches = (old["Enabled"] as? NSNumber)?.boolValue == true && old["SharingDevices"] as? [String] == [output.device] && old["SharingNetworkNumberStart"] as? String == config.gateway && (old["PrimaryService"] as? String == upstream.id || currentServices.contains { $0.vpn && $0.id == old["PrimaryService"] as? String })
    if alreadyMatches {
        do { try installPolicy(old["PrimaryService"] as? String == upstream.id ? "direct" : "vpn", config, upstream) }
        catch {
            try? pfLoad(oldPolicy)
            if let oldConfig { try? oldConfig.write(to: URL(fileURLWithPath: configurationPath), options: .atomic) }
            else { try? FileManager.default.removeItem(atPath: configurationPath) }
            throw error
        }
        return ["ok": true, "message": "Existing sharing preserved. Setup is complete."]
    }
    // New topology starts in Direct mode, without starting/stopping any VPN.
    // Reject a different active VPN before changing any sharing preferences.
    guard globalInterface() == upstream.device else {
        if let oldConfig { try oldConfig.write(to: URL(fileURLWithPath: configurationPath), options: .atomic) }
        else { try? FileManager.default.removeItem(atPath: configurationPath) }
        try fail("Connect the selected internet adapter and disconnect VPNs before applying a new sharing setup.")
    }
    do {
        try apply(sharingValue(old, config, upstream, true))
        for _ in 0..<40 {
            if hasGateway(config.gateway) && natInterfaces().contains(upstream.device) {
                try installPolicy("direct", config, upstream)
                return ["ok": true, "message": "Internet Sharing configured. Setup is complete."]
            }
            Thread.sleep(forTimeInterval: 0.25)
        }
        try fail("macOS did not activate the selected sharing network. Enable Internet Sharing in System Settings, then retry setup.")
    } catch {
        try? apply(old.isEmpty ? nil : old)
        try? pfLoad(oldPolicy)
        if let oldConfig { try? oldConfig.write(to: URL(fileURLWithPath: configurationPath), options: .atomic) }
        else { try? FileManager.default.removeItem(atPath: configurationPath) }
        throw error
    }
}
do {
    let args = Array(CommandLine.arguments.dropFirst())
    guard !args.isEmpty else { try fail("Use inventory, status, vpn, direct, restore, or administrator setup.") }
    if args.count == 2 && args[0] == "configure" { emit(try configure(args[1])) }
    else if args.count == 2 && args[0] == "check-config" {
        let config = try JSONDecoder().decode(Configuration.self, from: Data(contentsOf: URL(fileURLWithPath: args[1])))
        _ = try validateConfiguration(config); emit(["ok": true])
    }
    else {
        guard args.count == 1 else { try fail("Invalid arguments.") }
        switch args[0] {
        case "inventory": emit(try inventory())
        case "status": emit(try status())
        case "vpn", "direct": emit(try change(args[0]))
        case "restore":
            guard geteuid() == 0 else { try fail("Administrator helper is required.") }
            let data = try Data(contentsOf: URL(fileURLWithPath: stateDirectory + "/original-sharing.plist"))
            guard let original = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else { try fail("Original sharing backup is unavailable.") }
            try pfLoad(""); if let config = try? loadConfiguration() { clearClientStates(config) }; try apply(original["NAT"] as? [String: Any]); emit(["ok": true, "message": "Original sharing preferences restored."])
        default: try fail("Unknown action.")
        }
    }
} catch {
    emit(["ok": false, "error": (error as? ControlError)?.message ?? "Sharing setup is missing or invalid. Open Network setup on the Mac."])
    exit(1)
}
