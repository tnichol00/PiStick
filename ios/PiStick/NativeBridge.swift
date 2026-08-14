import Foundation
import WebKit

@MainActor
final class NativeBridge: NSObject, WKScriptMessageHandler {
    private let api: PiStickAPI
    weak var webView: WKWebView?

    init(api: PiStickAPI? = nil) {
        self.api = api ?? PiStickAPI()
        super.init()
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "pistickAPI",
              message.frameInfo.isMainFrame,
              let request = message.body as? JSONObject else {
            return
        }
        let requestID = JSONTools.string(request["id"])
        let path = JSONTools.string(request["path"])
        let method = JSONTools.string(request["method"]).isEmpty ? "GET" : JSONTools.string(request["method"])
        let body = JSONTools.object(request["body"]) ?? [:]

        Task { @MainActor [weak self] in
            guard let self else { return }
            do {
                let result = try await api.handle(path: path, method: method, body: body)
                respond(requestID: requestID, succeeded: true, payload: result)
            } catch {
                let message = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
                respond(requestID: requestID, succeeded: false, payload: ["error": message])
            }
        }
    }

    private func respond(requestID: String, succeeded: Bool, payload: JSONObject) {
        guard let webView,
              let data = try? JSONTools.data(payload) else { return }
        let encoded = data.base64EncodedString()
        let quotedID: String
        if let idData = try? JSONEncoder().encode(requestID),
           let value = String(data: idData, encoding: .utf8) {
            quotedID = value
        } else {
            quotedID = "\"\""
        }
        let script = "window.PiStickNative.receive(\(quotedID), \(succeeded ? "true" : "false"), '\(encoded)');"
        webView.evaluateJavaScript(script)
    }
}
