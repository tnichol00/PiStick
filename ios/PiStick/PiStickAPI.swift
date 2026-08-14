import Foundation

@MainActor
final class PiStickAPI {
    private let config: ConfigStore
    private let state: StateStore
    private let tmdb: TMDBClient

    init(config: ConfigStore = ConfigStore(), state: StateStore = StateStore()) {
        self.config = config
        self.state = state
        self.tmdb = TMDBClient { config.credential }
    }

    func handle(path rawPath: String, method rawMethod: String, body: JSONObject) async throws -> JSONObject {
        guard let url = URL(string: "https://pistick.invalid\(rawPath)"),
              let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            throw PiStickFailure("The app request is invalid.")
        }
        let method = rawMethod.uppercased()
        let parts = components.path.split(separator: "/").map(String.init)
        let query = Dictionary(
            components.queryItems?.map { ($0.name, $0.value ?? "") } ?? [],
            uniquingKeysWith: { first, _ in first }
        )

        if parts == ["api", "status"], method == "GET" {
            return [
                "name": "PiStick",
                "version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "1.0.0",
                "tmdb_configured": config.isConfigured
            ]
        }

        if parts == ["api", "profiles"], method == "GET" {
            return state.profilesPayload()
        }
        if parts == ["api", "profiles"], method == "POST" {
            return ["profile": try state.addProfile(name: JSONTools.string(body["name"]))]
        }
        if parts.count == 3, parts[0] == "api", parts[1] == "profiles", method == "PATCH" {
            return ["profile": try state.renameProfile(parts[2], name: JSONTools.string(body["name"]))]
        }
        if parts.count == 3, parts[0] == "api", parts[1] == "profiles", method == "DELETE" {
            try state.deleteProfile(parts[2])
            return ["ok": true]
        }
        if parts.count == 4,
           parts[0] == "api", parts[1] == "profiles",
           parts[3] == "activate",
           method == "POST" {
            return ["profile": try state.activateProfile(parts[2])]
        }

        if parts == ["api", "settings", "tmdb"], method == "POST" {
            let credential = JSONTools.string(body["token"])
            guard ConfigStore.isUsable(credential) else {
                throw PiStickFailure("Paste your TMDB API Read Access Token or v3 API key.")
            }
            try await tmdb.validate(credential)
            try config.save(credential)
            return ["ok": true, "tmdb_configured": true]
        }

        if parts == ["api", "home"], method == "GET" {
            let profileID = try requiredProfileID(query["profile_id"])
            let payload = try await tmdb.home()
            return try decorateHome(profileID: profileID, payload: payload)
        }
        if parts.count == 3,
           parts[0] == "api", parts[1] == "discover",
           (parts[2] == "movie" || parts[2] == "tv"),
           method == "GET" {
            let profileID = try requiredProfileID(query["profile_id"])
            let page = try integer(query["page"] ?? "1", name: "Page", minimum: 1, maximum: 500)
            var payload = try await tmdb.discover(mediaType: parts[2], page: page)
            payload["items"] = try JSONTools.objects(payload["items"]).map {
                try state.decorate(profileID: profileID, media: $0)
            }
            return payload
        }
        if parts == ["api", "search"], method == "GET" {
            let profileID = try requiredProfileID(query["profile_id"])
            let page = try integer(query["page"] ?? "1", name: "Page", minimum: 1, maximum: 500)
            var payload = try await tmdb.search(query: query["q"] ?? "", page: page)
            payload["items"] = try JSONTools.objects(payload["items"]).map {
                try state.decorate(profileID: profileID, media: $0)
            }
            return payload
        }

        if parts.count == 4,
           parts[0] == "api", parts[1] == "media",
           (parts[2] == "movie" || parts[2] == "tv"),
           method == "GET" {
            let profileID = try requiredProfileID(query["profile_id"])
            let id = try integer(parts[3], name: "TMDB ID", minimum: 1)
            let media = try await tmdb.details(mediaType: parts[2], id: id)
            var decorated = try state.decorate(profileID: profileID, media: media)
            if parts[2] == "tv" {
                let resume = try state.resumeEpisode(profileID: profileID, media: media)
                decorated["resume_episode"] = ["season_number": resume.0, "episode_number": resume.1]
            }
            return ["media": decorated]
        }

        if parts.count == 5,
           parts[0] == "api", parts[1] == "tv", parts[3] == "season",
           method == "GET" {
            let profileID = try requiredProfileID(query["profile_id"])
            let showID = try integer(parts[2], name: "TMDB ID", minimum: 1)
            let seasonNumber = try integer(parts[4], name: "Season", minimum: 0, maximum: 10_000)
            return try await seasonPayload(showID: showID, seasonNumber: seasonNumber, profileID: profileID)
        }

        if parts == ["api", "continue"], method == "GET" {
            return ["items": try state.continueWatching(profileID: requiredProfileID(query["profile_id"]))]
        }

        if parts == ["api", "play"], method == "POST" {
            let profileID = try requiredProfileID(JSONTools.string(body["profile_id"]))
            let media = try requiredMedia(body["media"])
            let kind = JSONTools.string(media["media_type"])
            let mediaID = try integer(media["id"], name: "TMDB ID", minimum: 1)
            if kind == "movie" {
                let saved = try state.entry(profileID: profileID, media: media) ?? [:]
                let resume = JSONTools.double(saved["position_seconds"]) ?? 0
                try state.markStarted(profileID: profileID, media: media)
                return [
                    "embed_url": playbackURL(path: "/movie/\(mediaID)", resumeSeconds: resume),
                    "resume_seconds": resume,
                    "kind": "movie"
                ]
            }
            guard let episode = JSONTools.object(body["episode"]) else {
                throw PiStickFailure("Choose an episode first.")
            }
            let seasonNumber = try integer(episode["season_number"], name: "Season", minimum: 0, maximum: 10_000)
            let episodeNumber = try integer(episode["episode_number"], name: "Episode", minimum: 1, maximum: 100_000)
            let saved = try state.episodeEntry(
                profileID: profileID,
                media: media,
                seasonNumber: seasonNumber,
                episodeNumber: episodeNumber
            ) ?? [:]
            let resume = JSONTools.double(saved["position_seconds"]) ?? 0
            try state.markEpisodeStarted(profileID: profileID, media: media, episode: episode)
            return [
                "embed_url": playbackURL(path: "/tv/\(mediaID)/\(seasonNumber)/\(episodeNumber)", resumeSeconds: resume),
                "resume_seconds": resume,
                "kind": "episode",
                "season_number": seasonNumber,
                "episode_number": episodeNumber
            ]
        }

        if parts == ["api", "watch", "progress"], method == "POST" {
            let profileID = try requiredProfileID(JSONTools.string(body["profile_id"]))
            let media = try requiredMedia(body["media"])
            let position = try number(body["position_seconds"], name: "Position")
            let duration = try number(body["duration_seconds"], name: "Duration")
            if let episode = JSONTools.object(body["episode"]) {
                try state.setEpisodePosition(
                    profileID: profileID,
                    media: media,
                    episode: episode,
                    positionSeconds: position,
                    durationSeconds: duration
                )
            } else {
                try state.setPosition(
                    profileID: profileID,
                    media: media,
                    positionSeconds: position,
                    durationSeconds: duration
                )
            }
            return ["ok": true]
        }

        if parts == ["api", "watch", "action"], method == "POST" {
            let profileID = try requiredProfileID(JSONTools.string(body["profile_id"]))
            let media = try requiredMedia(body["media"])
            switch JSONTools.string(body["action"]) {
            case "finished", "show_finished":
                try state.markFinished(profileID: profileID, media: media)
            case "unwatched":
                try state.markUnwatched(profileID: profileID, media: media)
            case "started":
                try state.markStarted(profileID: profileID, media: media)
            case "episode_finished":
                guard let episode = JSONTools.object(body["episode"]) else {
                    throw PiStickFailure("Episode data is invalid.")
                }
                try state.markEpisodeFinished(profileID: profileID, media: media, episode: episode)
            default:
                throw PiStickFailure("Watch-state action is invalid.")
            }
            return ["ok": true]
        }

        throw PiStickFailure("App route not found.")
    }

    private func decorateHome(profileID: String, payload: JSONObject) throws -> JSONObject {
        var rows: [JSONObject] = []
        let continuing = try state.continueWatching(profileID: profileID)
        if !continuing.isEmpty { rows.append(["title": "Continue Watching", "items": continuing]) }
        for row in JSONTools.objects(payload["rows"]) {
            let items = try JSONTools.objects(row["items"]).map {
                try state.decorate(profileID: profileID, media: $0)
            }
            let title = JSONTools.string(row["title"])
            rows.append(["title": title.isEmpty ? "Explore" : title, "items": items])
        }
        let hero: Any
        if let item = payload["hero"] as? JSONObject {
            hero = try state.decorate(profileID: profileID, media: item)
        } else {
            hero = NSNull()
        }
        return ["hero": hero, "rows": rows]
    }

    private func seasonPayload(showID: Int, seasonNumber: Int, profileID: String) async throws -> JSONObject {
        let show = try await tmdb.details(mediaType: "tv", id: showID)
        var season = try await tmdb.season(showID: showID, seasonNumber: seasonNumber)
        season["episodes"] = try JSONTools.objects(season["episodes"]).map { episode in
            var result = episode
            let selectedSeason = JSONTools.int(episode["season_number"]) ?? seasonNumber
            let selectedEpisode = JSONTools.int(episode["episode_number"]) ?? 0
            if let watch = try state.episodeEntry(
                profileID: profileID,
                media: show,
                seasonNumber: selectedSeason,
                episodeNumber: selectedEpisode
            ) {
                result["watch"] = watch.filter {
                    ["status", "progress", "position_seconds", "duration_seconds"].contains($0.key)
                }
            }
            return result
        }
        return ["season": season]
    }

    private func requiredProfileID(_ candidate: String?) throws -> String {
        let requested = (candidate ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let selected = requested.isEmpty ? (state.activeProfileID ?? "") : requested
        guard state.profile(selected) != nil else { throw PiStickFailure("Choose a profile first.") }
        return selected
    }

    private func requiredMedia(_ candidate: Any?) throws -> JSONObject {
        guard let media = JSONTools.object(candidate) else { throw PiStickFailure("A media object is required.") }
        let kind = JSONTools.string(media["media_type"])
        guard kind == "movie" || kind == "tv", (JSONTools.int(media["id"]) ?? 0) > 0 else {
            throw PiStickFailure("Media must contain a valid TMDB ID and type.")
        }
        return media
    }

    private func integer(
        _ candidate: Any?,
        name: String,
        minimum: Int,
        maximum: Int = 2_147_483_647
    ) throws -> Int {
        guard let value = JSONTools.int(candidate), value >= minimum, value <= maximum else {
            throw PiStickFailure("\(name) is outside the allowed range.")
        }
        return value
    }

    private func number(_ candidate: Any?, name: String) throws -> Double {
        guard let value = JSONTools.double(candidate), value.isFinite, value >= 0, value <= 10_000_000 else {
            throw PiStickFailure("\(name) must be a non-negative number.")
        }
        return value
    }

    private func playbackURL(path: String, resumeSeconds: Double) -> String {
        let base = "https://player.videasy.to\(path)"
        let seconds = max(0, Int(resumeSeconds))
        return seconds > 0 ? "\(base)?progress=\(seconds)" : base
    }
}
