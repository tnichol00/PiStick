import Foundation

typealias JSONObject = [String: Any]

struct PiStickFailure: LocalizedError {
    let message: String

    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? { message }
}

enum JSONTools {
    static func string(_ value: Any?) -> String {
        if let value = value as? String { return value }
        if let value = value as? NSNumber { return value.stringValue }
        return ""
    }

    static func int(_ value: Any?) -> Int? {
        if let value = value as? Int { return value }
        if let value = value as? NSNumber { return value.intValue }
        return Int(string(value))
    }

    static func double(_ value: Any?) -> Double? {
        if let value = value as? Double { return value }
        if let value = value as? NSNumber { return value.doubleValue }
        return Double(string(value))
    }

    static func object(_ value: Any?) -> JSONObject? {
        value as? JSONObject
    }

    static func objects(_ value: Any?) -> [JSONObject] {
        if let values = value as? [JSONObject] { return values }
        return (value as? [Any] ?? []).compactMap { $0 as? JSONObject }
    }

    static func data(_ value: Any) throws -> Data {
        guard JSONSerialization.isValidJSONObject(value) else {
            throw PiStickFailure("PiStick could not encode its local data.")
        }
        return try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    }

    static func object(from data: Data) throws -> JSONObject {
        guard let value = try JSONSerialization.jsonObject(with: data) as? JSONObject else {
            throw PiStickFailure("PiStick received an invalid response.")
        }
        return value
    }
}
