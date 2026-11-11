import Foundation
@main struct ConnectionTests {
    static func main() {
        for input in ["javascript:alert(1)", "file:///private/test", "http://user:password@mac.local", "http://", "not a URL", "http://mac.local:70000"] {
            precondition(BoardConnection.url(from: input) == nil, "Unsafe/invalid URL accepted: " + input)
        }
        let link = BoardConnection.url(from: " http://mac.local:8788/pair?code=SECRET ")!
        precondition(BoardConnection.origin(link)!.absoluteString == "http://mac.local:8788/")
        precondition(BoardConnection.sameOrigin(link, URL(string:"http://MAC.local:8788/?notebook=math")!))
        precondition(!BoardConnection.sameOrigin(link, URL(string:"http://other.local:8788/")!))
        precondition(!BoardConnection.sameOrigin(link, URL(string:"http://mac.local:8787/")!))
        precondition(!BoardConnection.sameOrigin(link, URL(string:"https://mac.local:8788/")!))
        precondition(BoardConnection.sameOrigin(URL(string:"https://mac.local")!, URL(string:"https://mac.local:443/page")!))
        print("PASS URL validation, pairing-secret removal, and origin isolation")
    }
}
