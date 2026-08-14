import Foundation

@MainActor
final class StateStore {
    static let avatars = ["red", "blue", "green", "purple", "orange", "teal"]
    static let maximumProfiles = 8

    private let fileURL: URL
    private(set) var data: JSONObject

    init(fileURL: URL? = nil) {
        if let fileURL {
            self.fileURL = fileURL
        } else {
            let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
            self.fileURL = base.appendingPathComponent("PiStick", isDirectory: true)
                .appendingPathComponent("state.json")
        }

        if let stored = try? Data(contentsOf: self.fileURL),
           let object = try? JSONTools.object(from: stored) {
            data = object
        } else {
            data = Self.defaultData()
        }
        normalize()
        try? save()
    }

    static func defaultData() -> JSONObject {
        return [
            "active_profile": NSNull(),
            "profiles": [["id": "profile-1", "name": "Profile 1", "avatar": "red"]],
            "watch_state": ["profile-1": JSONObject()]
        ]
    }

    func profilesPayload() -> JSONObject {
        let active: Any
        if let activeProfileID { active = activeProfileID } else { active = NSNull() }
        return [
            "profiles": profiles,
            "active_profile": active,
            "max_profiles": Self.maximumProfiles
        ]
    }

    var profiles: [JSONObject] { JSONTools.objects(data["profiles"]) }

    var activeProfileID: String? {
        let value = JSONTools.string(data["active_profile"])
        return profile(value) == nil ? nil : value
    }

    func profile(_ id: String?) -> JSONObject? {
        guard let id, !id.isEmpty else { return nil }
        return profiles.first { JSONTools.string($0["id"]) == id }
    }

    func activateProfile(_ id: String) throws -> JSONObject {
        let selected = try requireProfile(id)
        data["active_profile"] = id
        var watch = watchState
        if watch[id] == nil { watch[id] = JSONObject() }
        data["watch_state"] = watch
        try save()
        return selected
    }

    func addProfile(name: String) throws -> JSONObject {
        var current = profiles
        guard current.count < Self.maximumProfiles else {
            throw PiStickFailure("PiStick supports up to \(Self.maximumProfiles) profiles.")
        }
        var cleaned = String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(40))
        if cleaned.isEmpty { cleaned = "Profile \(current.count + 1)" }
        let item: JSONObject = [
            "id": "profile-\(UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(10))",
            "name": cleaned,
            "avatar": Self.avatars[current.count % Self.avatars.count]
        ]
        current.append(item)
        data["profiles"] = current
        var watch = watchState
        watch[JSONTools.string(item["id"])] = JSONObject()
        data["watch_state"] = watch
        try save()
        return item
    }

    func renameProfile(_ id: String, name: String) throws -> JSONObject {
        _ = try requireProfile(id)
        let cleaned = String(name.trimmingCharacters(in: .whitespacesAndNewlines).prefix(40))
        guard !cleaned.isEmpty else { throw PiStickFailure("Profile names cannot be blank.") }
        var current = profiles
        guard let index = current.firstIndex(where: { JSONTools.string($0["id"]) == id }) else {
            throw PiStickFailure("Choose a valid profile first.")
        }
        current[index]["name"] = cleaned
        data["profiles"] = current
        try save()
        return current[index]
    }

    func deleteProfile(_ id: String) throws {
        _ = try requireProfile(id)
        guard profiles.count > 1 else { throw PiStickFailure("PiStick must keep at least one profile.") }
        data["profiles"] = profiles.filter { JSONTools.string($0["id"]) != id }
        var watch = watchState
        watch.removeValue(forKey: id)
        data["watch_state"] = watch
        if activeProfileID == id { data["active_profile"] = NSNull() }
        try save()
    }

    func entry(profileID: String, media: JSONObject) throws -> JSONObject? {
        let history = try history(profileID)
        return history[try mediaKey(media)] as? JSONObject
    }

    func decorate(profileID: String, media: JSONObject) throws -> JSONObject {
        var result = media
        if let saved = try entry(profileID: profileID, media: media) {
            result["watch"] = selected(saved, keys: [
                "status", "progress", "position_seconds", "duration_seconds", "updated_at", "last_episode"
            ])
        }
        return result
    }

    func markStarted(profileID: String, media: JSONObject) throws {
        var history = try history(profileID)
        let key = try mediaKey(media)
        let previous = history[key] as? JSONObject ?? [:]
        let previousProgress = JSONTools.double(previous["progress"]) ?? 0
        let progress = JSONTools.string(previous["status"]) == "finished" ? 0.03 : max(0.03, previousProgress)
        var value: JSONObject = [
            "status": "in_progress",
            "progress": min(progress, 0.97),
            "updated_at": Date().timeIntervalSince1970,
            "media": snapshot(media)
        ]
        for field in ["position_seconds", "duration_seconds"] where previous[field] != nil {
            value[field] = previous[field]
        }
        history[key] = value
        try setHistory(history, profileID: profileID)
    }

    func markFinished(profileID: String, media: JSONObject) throws {
        var history = try history(profileID)
        let key = try mediaKey(media)
        let previous = history[key] as? JSONObject ?? [:]
        var value: JSONObject = [
            "status": "finished",
            "progress": 1.0,
            "updated_at": Date().timeIntervalSince1970,
            "media": snapshot(media).isEmpty ? (previous["media"] as? JSONObject ?? [:]) : snapshot(media)
        ]
        if let episodes = previous["episodes"] as? JSONObject { value["episodes"] = episodes }
        history[key] = value
        try setHistory(history, profileID: profileID)
    }

    func markUnwatched(profileID: String, media: JSONObject) throws {
        var history = try history(profileID)
        history.removeValue(forKey: try mediaKey(media))
        try setHistory(history, profileID: profileID)
    }

    func setPosition(
        profileID: String,
        media: JSONObject,
        positionSeconds: Double,
        durationSeconds: Double
    ) throws {
        let duration = max(0, durationSeconds)
        let position = duration > 0 ? min(max(0, positionSeconds), duration) : max(0, positionSeconds)
        let progress = duration > 0 ? position / duration : 0.03
        var history = try history(profileID)
        let key = try mediaKey(media)
        let previous = history[key] as? JSONObject ?? [:]
        var value: JSONObject
        if progress >= 0.98 {
            value = [
                "status": "finished",
                "progress": 1.0,
                "updated_at": Date().timeIntervalSince1970,
                "media": snapshot(media).isEmpty ? (previous["media"] as? JSONObject ?? [:]) : snapshot(media)
            ]
            if let episodes = previous["episodes"] as? JSONObject { value["episodes"] = episodes }
        } else {
            value = [
                "status": "in_progress",
                "progress": max(0.03, min(0.97, progress)),
                "updated_at": Date().timeIntervalSince1970,
                "media": snapshot(media)
            ]
        }
        value["position_seconds"] = rounded(position)
        value["duration_seconds"] = rounded(duration)
        history[key] = value
        try setHistory(history, profileID: profileID)
    }

    func episodeEntry(
        profileID: String,
        media: JSONObject,
        seasonNumber: Int,
        episodeNumber: Int
    ) throws -> JSONObject? {
        let show = try entry(profileID: profileID, media: media) ?? [:]
        let episodes = show["episodes"] as? JSONObject ?? [:]
        return episodes[Self.episodeKey(seasonNumber, episodeNumber)] as? JSONObject
    }

    func resumeEpisode(profileID: String, media: JSONObject) throws -> (Int, Int) {
        let show = try entry(profileID: profileID, media: media) ?? [:]
        let episodes = (show["episodes"] as? JSONObject ?? [:]).values.compactMap { $0 as? JSONObject }
        if episodes.isEmpty {
            let regular = availableSeasons(media).compactMap { season -> Int? in
                let number = JSONTools.int(season["season_number"]) ?? 0
                return number > 0 ? number : nil
            }
            return (regular.first ?? 1, 1)
        }
        let latest = episodes.max {
            (JSONTools.double($0["updated_at"]) ?? 0) < (JSONTools.double($1["updated_at"]) ?? 0)
        } ?? [:]
        let season = JSONTools.int(latest["season_number"]) ?? 1
        let episode = JSONTools.int(latest["episode_number"]) ?? 1
        if JSONTools.string(latest["status"]) == "finished",
           let following = nextEpisodePosition(media, seasonNumber: season, episodeNumber: episode) {
            return following
        }
        return (season, episode)
    }

    func markEpisodeStarted(profileID: String, media: JSONObject, episode: JSONObject) throws {
        let season = JSONTools.int(episode["season_number"]) ?? 1
        let number = JSONTools.int(episode["episode_number"]) ?? 1
        let previous = try episodeEntry(
            profileID: profileID,
            media: media,
            seasonNumber: season,
            episodeNumber: number
        ) ?? [:]
        var progress = JSONTools.double(previous["progress"]) ?? 0
        if JSONTools.string(previous["status"]) == "finished" { progress = 0.03 }
        try setEpisodeProgress(
            profileID: profileID,
            media: media,
            episode: episode,
            progress: max(0.03, progress)
        )
    }

    func markEpisodeFinished(profileID: String, media: JSONObject, episode: JSONObject) throws {
        try setEpisodeProgress(profileID: profileID, media: media, episode: episode, progress: 1)
    }

    func setEpisodePosition(
        profileID: String,
        media: JSONObject,
        episode: JSONObject,
        positionSeconds: Double,
        durationSeconds: Double
    ) throws {
        let duration = max(0, durationSeconds)
        let position = duration > 0 ? min(max(0, positionSeconds), duration) : max(0, positionSeconds)
        try setEpisodeProgress(
            profileID: profileID,
            media: media,
            episode: episode,
            progress: duration > 0 ? position / duration : 0.03,
            position: position,
            duration: duration
        )
    }

    func continueWatching(profileID: String) throws -> [JSONObject] {
        let values = try history(profileID).values.compactMap { $0 as? JSONObject }
            .filter {
                JSONTools.string($0["status"]) == "in_progress" && $0["media"] is JSONObject
            }
            .sorted {
                (JSONTools.double($0["updated_at"]) ?? 0) > (JSONTools.double($1["updated_at"]) ?? 0)
            }
        return values.compactMap { value in
            guard var media = value["media"] as? JSONObject else { return nil }
            media["watch"] = selected(value, keys: [
                "status", "progress", "position_seconds", "duration_seconds", "updated_at", "last_episode"
            ])
            return media
        }
    }

    func availableSeasons(_ media: JSONObject) -> [JSONObject] {
        let seasons = JSONTools.objects(media["seasons"])
            .filter { (JSONTools.int($0["episode_count"]) ?? 0) > 0 }
        let regular = seasons
            .filter { (JSONTools.int($0["season_number"]) ?? 0) > 0 }
            .sorted { (JSONTools.int($0["season_number"]) ?? 0) < (JSONTools.int($1["season_number"]) ?? 0) }
        let specials = seasons
            .filter { (JSONTools.int($0["season_number"]) ?? 0) == 0 }
            .sorted { JSONTools.string($0["name"]) < JSONTools.string($1["name"]) }
        return regular + specials
    }

    static func episodeKey(_ season: Int, _ episode: Int) -> String { "\(season):\(episode)" }

    private func setEpisodeProgress(
        profileID: String,
        media: JSONObject,
        episode: JSONObject,
        progress: Double,
        position: Double? = nil,
        duration: Double? = nil
    ) throws {
        guard JSONTools.string(media["media_type"]) == "tv" else {
            throw PiStickFailure("Episode progress requires a TV show.")
        }
        let season = JSONTools.int(episode["season_number"]) ?? 1
        let number = JSONTools.int(episode["episode_number"]) ?? 1
        guard season >= 0, number > 0 else { throw PiStickFailure("Episode numbers are invalid.") }
        let bounded = max(0, min(1, progress))
        let now = Date().timeIntervalSince1970
        var history = try history(profileID)
        let key = try mediaKey(media)
        let previousShow = history[key] as? JSONObject ?? [:]
        var episodes = previousShow["episodes"] as? JSONObject ?? [:]
        var episodeValue: JSONObject = [
            "status": bounded >= 0.98 ? "finished" : "in_progress",
            "progress": bounded >= 0.98 ? 1.0 : max(0.03, bounded),
            "updated_at": now,
            "season_number": season,
            "episode_number": number,
            "episode": episodeSnapshot(episode)
        ]
        if let position { episodeValue["position_seconds"] = rounded(max(0, position)) }
        if let duration { episodeValue["duration_seconds"] = rounded(max(0, duration)) }
        episodes[Self.episodeKey(season, number)] = episodeValue

        let following = nextEpisodePosition(media, seasonNumber: season, episodeNumber: number)
        let showFinished = JSONTools.string(episodeValue["status"]) == "finished" && following == nil
        history[key] = [
            "status": showFinished ? "finished" : "in_progress",
            "progress": showFinished ? 1.0 : (JSONTools.string(episodeValue["status"]) == "finished"
                ? 0.03 : max(0.03, min(0.97, JSONTools.double(episodeValue["progress"]) ?? 0.03))),
            "updated_at": now,
            "media": snapshot(media).isEmpty ? (previousShow["media"] as? JSONObject ?? [:]) : snapshot(media),
            "episodes": episodes,
            "last_episode": ["season_number": season, "episode_number": number]
        ]
        try setHistory(history, profileID: profileID)
    }

    private func nextEpisodePosition(
        _ media: JSONObject,
        seasonNumber: Int,
        episodeNumber: Int
    ) -> (Int, Int)? {
        let seasons = availableSeasons(media)
        let numbers = seasons.map { JSONTools.int($0["season_number"]) ?? 0 }
        let counts = Dictionary(uniqueKeysWithValues: seasons.map {
            (JSONTools.int($0["season_number"]) ?? 0, JSONTools.int($0["episode_count"]) ?? 0)
        })
        if (counts[seasonNumber] ?? 0) > episodeNumber { return (seasonNumber, episodeNumber + 1) }
        guard let index = numbers.firstIndex(of: seasonNumber) else { return nil }
        for next in numbers.dropFirst(index + 1) where next > 0 && (counts[next] ?? 0) > 0 {
            return (next, 1)
        }
        return nil
    }

    private var watchState: JSONObject { data["watch_state"] as? JSONObject ?? [:] }

    private func history(_ profileID: String) throws -> JSONObject {
        _ = try requireProfile(profileID)
        return watchState[profileID] as? JSONObject ?? [:]
    }

    private func setHistory(_ history: JSONObject, profileID: String) throws {
        var watch = watchState
        watch[profileID] = history
        data["watch_state"] = watch
        try save()
    }

    private func requireProfile(_ id: String?) throws -> JSONObject {
        guard let found = profile(id) else { throw PiStickFailure("Choose a valid profile first.") }
        return found
    }

    private func mediaKey(_ media: JSONObject) throws -> String {
        let kind = JSONTools.string(media["media_type"]).lowercased()
        let id = JSONTools.int(media["id"]) ?? 0
        guard (kind == "movie" || kind == "tv"), id > 0 else {
            throw PiStickFailure("Media must contain a valid TMDB ID and type.")
        }
        return "\(kind):\(id)"
    }

    private func snapshot(_ media: JSONObject) -> JSONObject {
        selected(media, keys: [
            "id", "media_type", "title", "name", "year", "release_date", "first_air_date",
            "poster_path", "backdrop_path", "overview", "vote_average", "number_of_seasons", "seasons"
        ])
    }

    private func episodeSnapshot(_ episode: JSONObject) -> JSONObject {
        selected(episode, keys: [
            "id", "name", "overview", "air_date", "still_path", "runtime", "season_number", "episode_number"
        ])
    }

    private func selected(_ object: JSONObject, keys: [String]) -> JSONObject {
        var result: JSONObject = [:]
        for key in keys {
            if let value = object[key], !(value is NSNull) { result[key] = value }
        }
        return result
    }

    private func rounded(_ value: Double) -> Double { (value * 10).rounded() / 10 }

    private func normalize() {
        var normalizedProfiles: [JSONObject] = []
        var seen = Set<String>()
        for (index, candidate) in JSONTools.objects(data["profiles"]).enumerated() {
            var id = JSONTools.string(candidate["id"]).trimmingCharacters(in: .whitespacesAndNewlines)
            if id.isEmpty || seen.contains(id) { id = "profile-\(UUID().uuidString.prefix(10).lowercased())" }
            seen.insert(id)
            var name = String(JSONTools.string(candidate["name"]).trimmingCharacters(in: .whitespacesAndNewlines).prefix(40))
            if name.isEmpty { name = "Profile \(index + 1)" }
            var avatar = JSONTools.string(candidate["avatar"]).lowercased()
            if !Self.avatars.contains(avatar) { avatar = Self.avatars[index % Self.avatars.count] }
            normalizedProfiles.append(["id": id, "name": name, "avatar": avatar])
            if normalizedProfiles.count == Self.maximumProfiles { break }
        }
        if normalizedProfiles.isEmpty { normalizedProfiles = JSONTools.objects(Self.defaultData()["profiles"]) }

        let existingWatch = watchState
        var normalizedWatch: JSONObject = [:]
        for item in normalizedProfiles {
            let id = JSONTools.string(item["id"])
            normalizedWatch[id] = existingWatch[id] as? JSONObject ?? [:]
        }
        let active = JSONTools.string(data["active_profile"])
        let normalizedActive: Any
        if normalizedProfiles.contains(where: { JSONTools.string($0["id"]) == active }) {
            normalizedActive = active
        } else {
            normalizedActive = NSNull()
        }
        data = [
            "active_profile": normalizedActive,
            "profiles": normalizedProfiles,
            "watch_state": normalizedWatch
        ]
    }

    private func save() throws {
        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true,
            attributes: nil
        )
        try JSONTools.data(data).write(to: fileURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
}
