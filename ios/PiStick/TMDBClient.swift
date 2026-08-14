import Foundation

final class TMDBClient {
    private let credentialProvider: () -> String
    private let session: URLSession
    private let apiBase = "https://api.themoviedb.org/3"

    init(credentialProvider: @escaping () -> String, session: URLSession = .shared) {
        self.credentialProvider = credentialProvider
        self.session = session
    }

    func validate(_ candidate: String) async throws {
        _ = try await request("/configuration", credentialOverride: candidate)
    }

    func home() async throws -> JSONObject {
        let definitions: [(String, String, String?)] = [
            ("Trending Now", "/trending/all/week", nil),
            ("Popular Movies", "/movie/popular", "movie"),
            ("Top Rated Movies", "/movie/top_rated", "movie"),
            ("Popular TV Shows", "/tv/popular", "tv"),
            ("Top Rated TV", "/tv/top_rated", "tv")
        ]
        var rows: [JSONObject] = []
        var firstError: Error?

        for (title, endpoint, kind) in definitions {
            do {
                let payload = try await request(endpoint, params: ["page": 1])
                rows.append(["title": title, "items": items(payload, mediaType: kind)])
            } catch {
                if firstError == nil { firstError = error }
                rows.append(["title": title, "items": [JSONObject]()])
            }
        }

        let hero = rows
            .flatMap { JSONTools.objects($0["items"]) }
            .first { !JSONTools.string($0["backdrop_path"]).isEmpty }
        if hero == nil, let firstError { throw firstError }
        let heroValue: Any
        if let hero { heroValue = hero } else { heroValue = NSNull() }
        return ["hero": heroValue, "rows": rows]
    }

    func discover(mediaType: String, page: Int) async throws -> JSONObject {
        guard mediaType == "movie" || mediaType == "tv" else {
            throw PiStickFailure("Discover type must be movie or tv.")
        }
        let selectedPage = max(1, min(page, 500))
        let payload = try await request("/\(mediaType)/popular", params: ["page": selectedPage])
        return [
            "page": JSONTools.int(payload["page"]) ?? selectedPage,
            "total_pages": JSONTools.int(payload["total_pages"]) ?? 1,
            "items": items(payload, mediaType: mediaType)
        ]
    }

    func search(query: String, page: Int) async throws -> JSONObject {
        let cleaned = String(query.trimmingCharacters(in: .whitespacesAndNewlines).prefix(120))
        if cleaned.isEmpty {
            return ["page": 1, "total_pages": 1, "items": [JSONObject]()]
        }
        let selectedPage = max(1, min(page, 500))
        let payload = try await request(
            "/search/multi",
            params: ["query": cleaned, "page": selectedPage, "include_adult": "false"]
        )
        return [
            "page": JSONTools.int(payload["page"]) ?? selectedPage,
            "total_pages": JSONTools.int(payload["total_pages"]) ?? 1,
            "items": items(payload)
        ]
    }

    func details(mediaType: String, id: Int) async throws -> JSONObject {
        guard mediaType == "movie" || mediaType == "tv", id > 0 else {
            throw PiStickFailure("The TMDB title is invalid.")
        }
        let payload = try await request(
            "/\(mediaType)/\(id)",
            params: ["append_to_response": "videos,credits"]
        )
        return normalize(payload, mediaType: mediaType)
    }

    func season(showID: Int, seasonNumber: Int) async throws -> JSONObject {
        guard showID > 0, seasonNumber >= 0 else {
            throw PiStickFailure("The show or season number is invalid.")
        }
        let payload = try await request("/tv/\(showID)/season/\(seasonNumber)")
        var result = payload
        let episodes = JSONTools.objects(payload["episodes"]).compactMap { candidate -> JSONObject? in
            var episode = candidate
            episode["season_number"] = JSONTools.int(episode["season_number"]) ?? seasonNumber
            let number = JSONTools.int(episode["episode_number"]) ?? 0
            episode["episode_number"] = number
            return number > 0 ? episode : nil
        }
        result["season_number"] = JSONTools.int(payload["season_number"]) ?? seasonNumber
        result["episodes"] = episodes
        return result
    }

    func normalize(_ candidate: JSONObject, mediaType: String? = nil) -> JSONObject {
        var result = candidate
        var kind = (mediaType ?? JSONTools.string(result["media_type"])).lowercased()
        if kind != "movie" && kind != "tv" {
            if result["title"] != nil { kind = "movie" }
            else if result["name"] != nil { kind = "tv" }
        }
        result["media_type"] = kind
        let date = JSONTools.string(result["release_date"]).isEmpty
            ? JSONTools.string(result["first_air_date"])
            : JSONTools.string(result["release_date"])
        result["year"] = date.count >= 4 ? String(date.prefix(4)) : ""
        return result
    }

    private func items(_ payload: JSONObject, mediaType: String? = nil) -> [JSONObject] {
        JSONTools.objects(payload["results"]).compactMap { candidate in
            let item = normalize(candidate, mediaType: mediaType)
            let kind = JSONTools.string(item["media_type"])
            guard (kind == "movie" || kind == "tv"), (JSONTools.int(item["id"]) ?? 0) > 0 else {
                return nil
            }
            return item
        }
    }

    private func request(
        _ endpoint: String,
        params: JSONObject = [:],
        credentialOverride: String? = nil
    ) async throws -> JSONObject {
        let credential = (credentialOverride ?? credentialProvider())
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard ConfigStore.isUsable(credential) else {
            throw PiStickFailure("Add your TMDB API credential in Settings.")
        }
        guard var components = URLComponents(string: apiBase + endpoint) else {
            throw PiStickFailure("PiStick could not create the TMDB request.")
        }

        var query = params
        query["language"] = "en-CA"
        let usesBearerToken = credential.hasPrefix("eyJ") || credential.count > 64
        if !usesBearerToken { query["api_key"] = credential }
        components.queryItems = query.keys.sorted().map {
            URLQueryItem(name: $0, value: JSONTools.string(query[$0]))
        }
        guard let url = components.url else {
            throw PiStickFailure("PiStick could not create the TMDB request.")
        }

        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("PiStick-iOS/1.0", forHTTPHeaderField: "User-Agent")
        if usesBearerToken {
            request.setValue("Bearer \(credential)", forHTTPHeaderField: "Authorization")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw PiStickFailure("Could not reach TMDB. Check this device's internet connection.")
        }
        guard let http = response as? HTTPURLResponse else {
            throw PiStickFailure("TMDB returned an invalid response.")
        }
        switch http.statusCode {
        case 200..<300:
            return try JSONTools.object(from: data)
        case 401, 403:
            throw PiStickFailure("TMDB rejected that API credential.")
        case 404:
            throw PiStickFailure("TMDB could not find that title.")
        default:
            throw PiStickFailure("TMDB request failed (\(http.statusCode)).")
        }
    }
}
