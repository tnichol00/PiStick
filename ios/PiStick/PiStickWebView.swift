import SwiftUI
import UIKit
import WebKit

struct PiStickWebView: UIViewRepresentable {
    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        let bridge = NativeBridge()

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let url = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }

            if navigationAction.targetFrame == nil {
                // Player pop-ups never become new in-app web views.
                decisionHandler(.cancel)
                return
            }
            if navigationAction.targetFrame?.isMainFrame == true {
                let allowed = url.isFileURL || url.scheme == "about"
                decisionHandler(allowed ? .allow : .cancel)
                return
            }
            decisionHandler(.allow)
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptAlertPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping () -> Void
        ) {
            presentDialog(on: webView, title: nil, message: message, field: false) { _ in completionHandler() }
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptConfirmPanelWithMessage message: String,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (Bool) -> Void
        ) {
            guard let presenter = webView.window?.rootViewController?.topPresenter else {
                completionHandler(false)
                return
            }
            let alert = UIAlertController(title: nil, message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(true) })
            presenter.present(alert, animated: true)
        }

        func webView(
            _ webView: WKWebView,
            runJavaScriptTextInputPanelWithPrompt prompt: String,
            defaultText: String?,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping (String?) -> Void
        ) {
            presentDialog(on: webView, title: nil, message: prompt, field: true, defaultText: defaultText, completion: completionHandler)
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            nil
        }

        private func presentDialog(
            on webView: WKWebView,
            title: String?,
            message: String,
            field: Bool,
            defaultText: String? = nil,
            completion: @escaping (String?) -> Void
        ) {
            guard let presenter = webView.window?.rootViewController?.topPresenter else {
                completion(nil)
                return
            }
            let alert = UIAlertController(title: title, message: message, preferredStyle: .alert)
            if field {
                alert.addTextField { $0.text = defaultText }
                alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completion(nil) })
            }
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in
                completion(field ? alert.textFields?.first?.text : "")
            })
            presenter.present(alert, animated: true)
        }
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.userContentController.add(context.coordinator.bridge, name: "pistickAPI")

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0.035, green: 0.035, blue: 0.043, alpha: 1)
        webView.scrollView.backgroundColor = webView.backgroundColor
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.allowsBackForwardNavigationGestures = false
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        context.coordinator.bridge.webView = webView

        guard let webRoot = Bundle.main.resourceURL?.appendingPathComponent("Web", isDirectory: true),
              let indexURL = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "Web") else {
            webView.loadHTMLString("<h1 style='color:white'>PiStick resources are missing.</h1>", baseURL: nil)
            return webView
        }
        webView.loadFileURL(indexURL, allowingReadAccessTo: webRoot)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        webView.configuration.userContentController.removeScriptMessageHandler(forName: "pistickAPI")
        webView.stopLoading()
    }
}

private extension UIViewController {
    var topPresenter: UIViewController {
        if let presentedViewController { return presentedViewController.topPresenter }
        if let navigation = self as? UINavigationController { return navigation.visibleViewController?.topPresenter ?? navigation }
        if let tabs = self as? UITabBarController { return tabs.selectedViewController?.topPresenter ?? tabs }
        return self
    }
}
