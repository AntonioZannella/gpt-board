import SwiftUI
import UIKit
import WebKit

@main
struct GPTBoardPadApp: App {
    @State private var sharedFile: URL?
    var body: some Scene {
        WindowGroup { BoardScreen(sharedFile: $sharedFile).ignoresSafeArea(.container, edges: .bottom)
            .onOpenURL { sharedFile = $0 } }
    }
}

struct BoardScreen: UIViewControllerRepresentable {
    @Binding var sharedFile: URL?
    func makeUIViewController(context: Context) -> BoardViewController {
        BoardViewController()
    }
    func updateUIViewController(_ controller: BoardViewController, context: Context) {
        if let url = sharedFile {
            controller.receiveFile(url)
            DispatchQueue.main.async { sharedFile = nil }
        }
    }
}

final class BoardViewController: UIViewController, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate, UIPencilInteractionDelegate, NetServiceBrowserDelegate, NetServiceDelegate {
    private let discovery = NetServiceBrowser()
    private var services: [NetService] = []
    private var discovered: [String: URL] = [:]
    private var discovering = false
    private var failedToLoad = false
    private var retryTimer: Timer?
    private weak var connectionAlert: UIAlertController?
    private var pendingFile: [String: String]?
    private var receivingURL: URL?
    private var webView: WKWebView!
    private var serverOrigin: URL!
    private var downloads: [ObjectIdentifier: URL] = [:]
    private let addressKey = "GPTBoardServerAddress"
    private lazy var toggleScript: String = {
        guard let url = Bundle.main.url(forResource: "PencilToggle", withExtension: "js") else { return "null" }
        return (try? String(contentsOf: url, encoding: .utf8)) ?? "null"
    }()

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "GPT Board"
        view.backgroundColor = .systemBackground
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default() // Retains this app's pairing cookie and recovery database.
        config.allowsInlineMediaPlayback = true
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isOpaque = false
        webView.backgroundColor = .systemBackground
        webView.allowsBackForwardNavigationGestures = false
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(webView)
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            webView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            webView.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
        let pencil = UIPencilInteraction()
        pencil.delegate = self
        webView.addInteraction(pencil)
        discovery.delegate = self
        let saved = UserDefaults.standard.string(forKey: addressKey) ?? BoardConnection.defaultAddress
        load(BoardConnection.url(from: saved) ?? URL(string: BoardConnection.defaultAddress)!)
        retryTimer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            guard let self, UIApplication.shared.applicationState == .active else { return }
            self.deliverFile()
            if self.failedToLoad && !self.webView.isLoading {
                self.webView.load(URLRequest(url: self.serverOrigin, timeoutInterval: 8))
            }
        }
    }

    private func load(_ url: URL) {
        guard let origin = BoardConnection.origin(url) else { return }
        serverOrigin = origin
        // A pasted one-time pairing URL is used once, but its secret is never saved in defaults.
        UserDefaults.standard.set(origin.absoluteString, forKey: addressKey)
        webView.load(URLRequest(url: url, timeoutInterval: 8))
    }

    @objc private func connection() {
        guard presentedViewController == nil else { return }
        if !discovering {
            discovering = true
            discovery.searchForServices(ofType: "_gptboard._tcp.", inDomain: "local.")
        }
        let alert = UIAlertController(title: "Connect to your Mac", message: "Use the URL shown by Connect iPad in GPT Board. Keep both devices on the same Wi-Fi. This app pairs separately from Safari.", preferredStyle: .alert)
        alert.addTextField { field in
            field.text = self.serverOrigin?.appendingPathComponent("pair").absoluteString ?? BoardConnection.defaultAddress
            field.keyboardType = .URL
            field.autocapitalizationType = .none
            field.autocorrectionType = .no
            field.clearButtonMode = .whileEditing
        }
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        alert.addAction(UIAlertAction(title: "Connect", style: .default) { [weak self, weak alert] _ in
            guard let self else { return }
            guard let url = BoardConnection.url(from: alert?.textFields?.first?.text ?? "") else {
                self.message("Invalid address", "Enter a complete http:// or https:// address without a username or password.")
                return
            }
            self.confirmLeaving { self.load(url) }
        })
        for (name, url) in discovered.sorted(by: { $0.key < $1.key }).prefix(5) {
            addDiscoveredAction(name, url, to: alert)
        }
        connectionAlert = alert
        present(alert, animated: true)
    }

    @objc private func refresh() { confirmLeaving { self.webView.reload() } }

    private func confirmLeaving(_ action: @escaping () -> Void) {
        webView.evaluateJavaScript("document.querySelector('#status')?.textContent ?? null") { [weak self] value, _ in
            guard let self else { return }
            if let status = value as? String, !status.hasPrefix("Saved") {
                let alert = UIAlertController(title: "Writing may not be saved to the Mac", message: "Wait for Saved if possible. Unsent writing stays with this app and this server address; changing addresses does not transfer it.", preferredStyle: .alert)
                alert.addAction(UIAlertAction(title: "Stay here", style: .cancel))
                alert.addAction(UIAlertAction(title: "Continue", style: .default) { _ in action() })
                self.present(alert, animated: true)
            } else { action() }
        }
    }

    @objc private func toggleTool() {
        guard let url = webView.url, let origin = serverOrigin, BoardConnection.sameOrigin(url, origin) else { return }
        webView.evaluateJavaScript(toggleScript) { value, _ in
            if value is String { UISelectionFeedbackGenerator().selectionChanged() }
        }
    }

    func pencilInteractionDidTap(_ interaction: UIPencilInteraction) { handlePencilTap() }

    @available(iOS 17.5, *)
    func pencilInteraction(_ interaction: UIPencilInteraction, didReceiveTap tap: UIPencilInteraction.Tap) { handlePencilTap() }

    private func handlePencilTap() {
        guard UIPencilInteraction.preferredTapAction != .ignore else { return }
        toggleTool()
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.shouldPerformDownload,
           let current = webView.url, let origin = serverOrigin, BoardConnection.sameOrigin(current, origin),
           ["blob", "data"].contains(url.scheme ?? "") || BoardConnection.sameOrigin(url, origin) {
            decisionHandler(.download); return
        }
        // Let embedded video frames work, but keep the main app on the selected board origin.
        if navigationAction.targetFrame?.isMainFrame == false { decisionHandler(.allow); return }
        if let origin = serverOrigin, BoardConnection.sameOrigin(url, origin) { decisionHandler(.allow); return }
        if navigationAction.navigationType == .linkActivated && ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
            UIApplication.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, let origin = serverOrigin, BoardConnection.sameOrigin(url, origin) {
            webView.load(navigationAction.request)
        }
        return nil
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let destination = folder.appendingPathComponent(URL(fileURLWithPath: suggestedFilename).lastPathComponent)
            downloads[ObjectIdentifier(download)] = destination
            completionHandler(destination)
        } catch { completionHandler(nil); message("Export failed", error.localizedDescription) }
    }
    func downloadDidFinish(_ download: WKDownload) {
        guard let url = downloads.removeValue(forKey: ObjectIdentifier(download)) else { return }
        let share = UIActivityViewController(activityItems: [url], applicationActivities: nil)
        share.popoverPresentationController?.sourceView = view
        share.popoverPresentationController?.sourceRect = CGRect(x: view.bounds.midX, y: view.bounds.maxY - 20, width: 1, height: 1)
        share.completionWithItemsHandler = { _, _, _, _ in try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        present(share, animated: true)
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let url = downloads.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        message("Export failed", error.localizedDescription)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { navigationFailed(error) }
    private func navigationFailed(_ error: Error) {
        guard (error as NSError).code != NSURLErrorCancelled else { return }
        let alreadyFailed = failedToLoad
        failedToLoad = true
        if !alreadyFailed { connection() }
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(title: "Board paused", message: "Reload to reopen your board. Saved recovery data stays on this iPad.", preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        alert.addAction(UIAlertAction(title: "Reload", style: .default) { [weak self] _ in self?.webView.reload() })
        present(alert, animated: true)
    }

    deinit { retryTimer?.invalidate(); discovery.stop() }

    private func addDiscoveredAction(_ name: String, _ url: URL, to alert: UIAlertController) {
        alert.addAction(UIAlertAction(title: "Connect to " + name, style: .default) { [weak self] _ in
            self?.confirmLeaving { self?.load(url) }
        })
    }
    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        guard services.count < 5 else { return }
        services.append(service); service.delegate = self; service.resolve(withTimeout: 5)
    }
    func netServiceDidResolveAddress(_ sender: NetService) {
        guard let hostname = sender.hostName, sender.port > 0 else { return }
        let host = hostname.hasSuffix(".") ? String(hostname.dropLast()) : hostname
        guard host.lowercased().hasSuffix(".local"),
              let url = URL(string: "http://" + host + ":" + String(sender.port) + "/pair") else { return }
        if discovered[sender.name] == nil {
            discovered[sender.name] = url
            if let alert = connectionAlert { addDiscoveredAction(sender.name, url, to: alert) }
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        failedToLoad = false
        connectionAlert?.dismiss(animated: true)
        deliverFile()
    }

    // The web library uses these dialogs for names and confirmations.
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage text: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        guard presentedViewController == nil else { completionHandler(); return }
        let alert = UIAlertController(title: "GPT Board", message: text, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
        present(alert, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage text: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard presentedViewController == nil else { completionHandler(false); return }
        let alert = UIAlertController(title: "GPT Board", message: text, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
        alert.addAction(UIAlertAction(title: "Continue", style: .default) { _ in completionHandler(true) })
        present(alert, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        guard presentedViewController == nil else { completionHandler(nil); return }
        let alert = UIAlertController(title: "GPT Board", message: prompt, preferredStyle: .alert)
        alert.addTextField { $0.text = defaultText }
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(nil) })
        alert.addAction(UIAlertAction(title: "Save", style: .default) { [weak alert] _ in completionHandler(alert?.textFields?.first?.text) })
        present(alert, animated: true)
    }

    func receiveFile(_ url: URL) {
        guard url.isFileURL, receivingURL != url else { return }
        receivingURL = url
        defer { receivingURL = nil }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            guard ["pdf", "png", "jpg", "jpeg", "json"].contains(url.pathExtension.lowercased()),
                  let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize,
                  size > 0, size <= 16 * 1024 * 1024 else {
                message("Import unavailable", "Choose a PDF, image or notebook JSON up to 16 MiB."); return
            }
            guard pendingFile == nil else { message("Import pending", "Finish the current file import first."); return }
            pendingFile = ["name": url.lastPathComponent, "data": try Data(contentsOf: url).base64EncodedString()]
            deliverFile()
        } catch { message("Could not read file", error.localizedDescription) }
    }
    private func deliverFile() {
        guard let file = pendingFile, let url = webView?.url, let origin = serverOrigin,
              BoardConnection.sameOrigin(url, origin),
              let data = try? JSONSerialization.data(withJSONObject: file),
              let json = String(data: data, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.gptBoardImportFile?.(" + json + ") ?? false") { [weak self] value, _ in
            if value as? Bool == true { self?.pendingFile = nil }
        }
    }

    private func message(_ title: String, _ text: String) {
        guard presentedViewController == nil else { return }
        let alert = UIAlertController(title: title, message: text, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default))
        present(alert, animated: true)
    }
}
