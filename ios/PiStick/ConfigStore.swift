import Foundation
import Security

final class ConfigStore {
    private let service: String
    private let account = "tmdb-credential"

    init(service: String = Bundle.main.bundleIdentifier ?? "app.pistick.PiStick") {
        self.service = service
    }

    var credential: String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else {
            return ""
        }
        return value
    }

    var isConfigured: Bool { !credential.isEmpty }

    static func isUsable(_ candidate: String) -> Bool {
        let value = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        return value.count >= 20 && value.count <= 2_048 && !value.contains(where: { $0.isWhitespace })
    }

    func save(_ candidate: String) throws {
        let value = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isUsable(value), let data = value.data(using: .utf8) else {
            throw PiStickFailure("Paste your TMDB API Read Access Token or v3 API key.")
        }

        let identity: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account
        ]
        let values: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]

        let updateStatus = SecItemUpdate(identity as CFDictionary, values as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var item = identity
            values.forEach { item[$0.key] = $0.value }
            let addStatus = SecItemAdd(item as CFDictionary, nil)
            guard addStatus == errSecSuccess else {
                throw PiStickFailure("PiStick could not save the TMDB credential (Keychain error \(addStatus)).")
            }
        } else if updateStatus != errSecSuccess {
            throw PiStickFailure("PiStick could not update the TMDB credential (Keychain error \(updateStatus)).")
        }
    }
}
