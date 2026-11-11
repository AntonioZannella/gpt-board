import Foundation

struct BoardConnection {
    static let defaultAddress = "http://gpt-board.local:8788"

    static func url(from input: String) -> URL? {
        let text = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(), ["http", "https"].contains(scheme),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.port == nil || (1...65535).contains(parts.port!),
              let url = parts.url else { return nil }
        // The user selects the destination. Never accept executable/file URL schemes.
        return url
    }

    static func origin(_ url: URL) -> URL? {
        guard var parts = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return nil }
        parts.path = "/"; parts.query = nil; parts.fragment = nil
        parts.user = nil; parts.password = nil
        return parts.url
    }

    static func sameOrigin(_ a: URL, _ b: URL) -> Bool {
        func port(_ url: URL) -> Int { url.port ?? (url.scheme?.lowercased() == "https" ? 443 : 80) }
        return a.scheme?.lowercased() == b.scheme?.lowercased()
            && a.host?.lowercased() == b.host?.lowercased() && port(a) == port(b)
    }
}
