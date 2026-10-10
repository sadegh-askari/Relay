import AppKit
import WebKit
import ServiceManagement
import CoreServices

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var statusItem: NSStatusItem!
    var loginItem: NSMenuItem!
    var stateItem: NSMenuItem!
    var window: NSWindow?
    var webView: WKWebView?
    var backend: Process?
    var restarting = false
    var quitting = false
    var timer: Timer?
    var terminationSignal: DispatchSourceSignal?
    let monitoringSession: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieAcceptPolicy = .always
        config.timeoutIntervalForRequest = 3
        return URLSession(configuration: config)
    }()
    let url = URL(string: "http://127.0.0.1:8765/")!
    let support = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/TV Internet Control/website/.runtime")
    let persian: [String: String] = [
        "Open Relay": "باز کردن Relay", "Settings…": "تنظیمات…", "Network setup…": "راه‌اندازی شبکه…", "Open in browser": "باز کردن در مرورگر",
        "Launch at login": "اجرا هنگام ورود", "macOS Login Items…": "موارد ورود macOS…", "Quit Relay": "خروج از Relay", "Edit": "ویرایش",
        "Cut": "برش", "Copy": "کپی", "Paste": "چسباندن", "Select All": "انتخاب همه",
        "Relay · checking connection": "Relay · در حال بررسی اتصال", "Relay · switching connection…": "Relay · در حال تغییر اتصال…",
        "Client · VPN active": "کاربر · وی‌پی‌ان فعال", "Client · Direct internet": "کاربر · اینترنت مستقیم", "Relay · sharing needs attention": "Relay · اشتراک نیاز به بررسی دارد",
        "Enable Relay in macOS Login Items": "Relay را در موارد ورود macOS فعال کنید", "Relay server stopped · check network setup": "سرور Relay متوقف شد · تنظیمات شبکه را بررسی کنید",
        "Relay runtime could not start": "اجرای Relay شروع نشد", "Relay could not start": "Relay اجرا نشد",
        "The bundled runtime is missing or could not run. Reinstall Relay.": "محیط اجرای برنامه موجود نیست یا اجرا نمی‌شود. Relay را دوباره نصب کنید.",
        "Check macOS Login Items": "موارد ورود macOS را بررسی کنید", "macOS could not update Relay’s login setting. Open System Settings → General → Login Items & Extensions and enable Relay.": "macOS نتوانست تنظیم ورود Relay را تغییر دهد. در تنظیمات سیستم، General ← Login Items & Extensions را باز کنید و Relay را فعال کنید."
    ]
    var isPersian: Bool {
        guard let data = try? Data(contentsOf: support.appendingPathComponent("preferences.json")),
              let prefs = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return prefs["language"] as? String == "fa"
    }
    func tr(_ text: String) -> String { isPersian ? persian[text] ?? text : text }
    func translateMenu(_ menu: NSMenu?) {
        guard let menu else { return }
        for item in menu.items where item != stateItem {
            if let original = item.representedObject as? String { item.title = tr(original) }
            else if !item.isSeparatorItem { item.representedObject = item.title; item.title = tr(item.title) }
            translateMenu(item.submenu)
        }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let firstRun = !UserDefaults.standard.bool(forKey: "hasOpenedRelay")
        let event = NSAppleEventManager.shared().currentAppleEvent
        let loginLaunch = event?.eventID == kAEOpenApplication &&
            (event?.paramDescriptor(forKeyword: keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem ||
             event?.paramDescriptor(forKeyword: keyAELaunchedAsLogInItem)?.booleanValue == true)
        UserDefaults.standard.set(true, forKey: "hasOpenedRelay")
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        migrateAgent()
        signal(SIGTERM, SIG_IGN)
        terminationSignal = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
        terminationSignal?.setEventHandler { NSApp.terminate(nil) }
        terminationSignal?.resume()
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem(); let appMenu = NSMenu(title: "Relay")
        appMenu.addItem(withTitle: "Quit Relay", action: #selector(quit), keyEquivalent: "q").target = self
        appMenuItem.submenu = appMenu; mainMenu.addItem(appMenuItem)
        let editItem = NSMenuItem(); let editMenu = NSMenu(title: "Edit")
        for (title, action, key) in [("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            editMenu.addItem(withTitle: title, action: NSSelectorFromString(action), keyEquivalent: key)
        }
        editItem.submenu = editMenu; mainMenu.addItem(editItem); NSApp.mainMenu = mainMenu
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let image = NSImage(systemSymbolName: "point.3.connected.trianglepath.dotted", accessibilityDescription: "Relay") { image.isTemplate = true; statusItem.button?.image = image }
        let menu = NSMenu()
        stateItem = NSMenuItem(title: "Relay · checking connection", action: nil, keyEquivalent: "")
        menu.addItem(stateItem); menu.addItem(.separator())
        item(menu, "Open Relay", #selector(openRelay), "o")
        item(menu, "Settings…", #selector(openSettings), "")
        item(menu, "Open in browser", #selector(openBrowser), "")
        menu.addItem(.separator())
        loginItem = NSMenuItem(title: "Launch at login", action: #selector(toggleLogin), keyEquivalent: "")
        loginItem.target = self; menu.addItem(loginItem)
        item(menu, "macOS Login Items…", #selector(openLoginItems), "")
        menu.addItem(.separator()); item(menu, "Quit Relay", #selector(quit), "q")
        statusItem.menu = menu
        // User requested startup; initialize the native setting once. macOS remains authoritative.
        if !UserDefaults.standard.bool(forKey: "loginPreferenceInitialized") {
            do { try SMAppService.mainApp.register(); UserDefaults.standard.set(true, forKey: "loginPreferenceInitialized") }
            catch { stateItem.title = "Enable Relay in macOS Login Items" }
        }
        updateLogin()
        startBackend()
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.updateStatus() }
        if !FileManager.default.fileExists(atPath: "/Library/Application Support/TV Internet Control/configuration.json") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.show(path: "/settings") }
        } else if firstRun || !loginLaunch {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.show(path: "/") }
        }
    }
    func item(_ menu: NSMenu, _ title: String, _ action: Selector, _ key: String) { let i = NSMenuItem(title: title, action: action, keyEquivalent: key); i.target = self; menu.addItem(i) }
    func migrateAgent() {
        let agent = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/LaunchAgents/local.tv-internet-control.plist")
        guard FileManager.default.fileExists(atPath: agent.path) else { return }
        let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/launchctl"); p.arguments = ["bootout", "gui/\(getuid())/local.tv-internet-control"]
        p.standardOutput = FileHandle.nullDevice; p.standardError = FileHandle.nullDevice
        try? p.run(); p.waitUntilExit()
        try? FileManager.default.removeItem(at: agent)
    }
    func startBackend() {
        guard !quitting, backend?.isRunning != true, let resources = Bundle.main.resourceURL else { return }
        let p = Process(); p.executableURL = resources.appendingPathComponent("runtime/relay-server"); p.arguments = ["--assets", resources.appendingPathComponent("website").path, "--state-dir", support.path]
        let log = support.appendingPathComponent("relay.log")
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil, attributes: [.posixPermissions: 0o600]) }
        if let handle = try? FileHandle(forWritingTo: log) { _ = try? handle.seekToEnd(); p.standardOutput = handle; p.standardError = handle }
        p.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self, !self.quitting else { return }
                self.stateItem.title = "Relay server stopped · check network setup"
                self.backend = nil
                // A bind conflict must not trigger an endless restart loop.
                if process.terminationStatus != 1 { DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.startBackend() } }
            }
        }
        do { try p.run(); backend = p }
        catch { stateItem.title = "Relay runtime could not start"; alert("Relay could not start", "The bundled runtime is missing or could not run. Reinstall Relay.") }
    }
    func updateStatus() {
        updateLogin()
        translateMenu(statusItem.menu); translateMenu(NSApp.mainMenu)
        guard backend?.isRunning == true else { return }
        let session = monitoringSession
        session.dataTask(with: url.appendingPathComponent("api/session")) { _, _, error in
            guard error == nil else { return }
            session.dataTask(with: self.url.appendingPathComponent("api/status")) { data, _, _ in
                if let data, let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                    DispatchQueue.main.async {
                        let operation = value["operation"] as? [String: Any]
                        let mode = value["mode"] as? String ?? "unknown"
                        self.stateItem.title = self.tr(operation?["busy"] as? Bool == true ? "Relay · switching connection…" : mode == "vpn" ? "Client · VPN active" : mode == "direct" ? "Client · Direct internet" : "Relay · sharing needs attention")
                        self.statusItem.button?.toolTip = self.stateItem.title
                    }
                }

            }.resume()
        }.resume()
    }
    func show(path: String) {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 780), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
            w.title = "Relay"; w.minSize = NSSize(width: 400, height: 620); w.isReleasedWhenClosed = false; w.center()
            let view = WKWebView(frame: w.contentView!.bounds); view.autoresizingMask = [.width, .height]; view.navigationDelegate = self
            w.contentView = view; window = w; webView = view
        }
        webView?.load(URLRequest(url: URL(string: "http://127.0.0.1:8765" + path)!))
        window?.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let target = navigationAction.request.url else { decisionHandler(.cancel); return }
        if target.scheme == "https", target.host == "github.com" { NSWorkspace.shared.open(target); decisionHandler(.cancel); return }
        decisionHandler(target.scheme == "http" && target.host == "127.0.0.1" && target.port == 8765 ? .allow : .cancel)
    }
    @objc func openRelay() { show(path: "/") }
    @objc func openSettings() { show(path: "/settings") }
    @objc func openSetup() { show(path: "/settings") }
    @objc func openBrowser() { NSWorkspace.shared.open(url) }
    func updateLogin() { loginItem?.state = SMAppService.mainApp.status == .enabled ? .on : .off }
    @objc func toggleLogin() {
        do {
            if SMAppService.mainApp.status == .enabled { try SMAppService.mainApp.unregister() }
            else { try SMAppService.mainApp.register() }
            UserDefaults.standard.set(true, forKey: "loginPreferenceInitialized")
        } catch { alert("Check macOS Login Items", "macOS could not update Relay’s login setting. Open System Settings → General → Login Items & Extensions and enable Relay.") }
        updateLogin()
    }
    @objc func openLoginItems() { SMAppService.openSystemSettingsLoginItems() }
    @objc func quit() { NSApp.terminate(nil) }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { show(path: "/"); return true }
    func applicationWillTerminate(_ notification: Notification) { quitting = true; timer?.invalidate(); backend?.terminate() }
    func alert(_ title: String, _ text: String) { let alert = NSAlert(); alert.messageText = tr(title); alert.informativeText = tr(text); alert.runModal() }
}
if CommandLine.arguments.contains("--remove-login-item") {
    do { try SMAppService.mainApp.unregister() }
    catch {
        if SMAppService.mainApp.status == .enabled { fputs("Disable Relay in macOS Login Items before removing it.\n", stderr); exit(1) }
    }
    exit(0)
}
let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.run()
