import SwiftUI

@main
struct PiStickApp: App {
    var body: some Scene {
        WindowGroup {
            AppRootView()
        }
    }
}

private struct AppRootView: View {
    private var isRunningUnitTests: Bool {
        ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil
    }

    @ViewBuilder
    var body: some View {
        if isRunningUnitTests {
            Color.black.ignoresSafeArea()
        } else {
            ContentView()
                .preferredColorScheme(.dark)
        }
    }
}
