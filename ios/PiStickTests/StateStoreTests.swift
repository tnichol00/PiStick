import XCTest
@testable import PiStick

@MainActor
final class StateStoreTests: XCTestCase {
    private func temporaryStateURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("PiStickTests-\(UUID().uuidString)", isDirectory: true)
            .appendingPathComponent("state.json")
    }

    func testDefaultProfileAndProfileLifecycle() throws {
        let url = temporaryStateURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = StateStore(fileURL: url)

        XCTAssertEqual(store.profiles.count, 1)
        XCTAssertNil(store.activeProfileID)
        let profile = try store.addProfile(name: "Living Room")
        let id = JSONTools.string(profile["id"])
        XCTAssertEqual(store.profiles.count, 2)
        _ = try store.activateProfile(id)
        XCTAssertEqual(store.activeProfileID, id)
        XCTAssertEqual(JSONTools.string(try store.renameProfile(id, name: "Family")["name"]), "Family")
        try store.deleteProfile(id)
        XCTAssertNil(store.activeProfileID)
        XCTAssertEqual(store.profiles.count, 1)
    }

    func testMovieProgressAppearsInContinueWatching() throws {
        let url = temporaryStateURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = StateStore(fileURL: url)
        let profileID = JSONTools.string(store.profiles[0]["id"])
        _ = try store.activateProfile(profileID)
        let movie: JSONObject = [
            "id": 550,
            "media_type": "movie",
            "title": "Example Movie",
            "poster_path": "/poster.jpg"
        ]

        try store.setPosition(
            profileID: profileID,
            media: movie,
            positionSeconds: 300,
            durationSeconds: 1_000
        )
        let continuing = try store.continueWatching(profileID: profileID)
        XCTAssertEqual(continuing.count, 1)
        XCTAssertEqual(JSONTools.double(JSONTools.object(continuing[0]["watch"])?["progress"]) ?? -1, 0.3, accuracy: 0.001)

        try store.markFinished(profileID: profileID, media: movie)
        XCTAssertTrue(try store.continueWatching(profileID: profileID).isEmpty)
    }

    func testFinishedEpisodeResumesAtNextEpisode() throws {
        let url = temporaryStateURL()
        defer { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        let store = StateStore(fileURL: url)
        let profileID = JSONTools.string(store.profiles[0]["id"])
        _ = try store.activateProfile(profileID)
        let show: JSONObject = [
            "id": 1399,
            "media_type": "tv",
            "name": "Example Show",
            "seasons": [["season_number": 1, "episode_count": 3]]
        ]
        let episode: JSONObject = [
            "id": 1,
            "season_number": 1,
            "episode_number": 1,
            "name": "Episode 1"
        ]

        try store.markEpisodeFinished(profileID: profileID, media: show, episode: episode)
        let resume = try store.resumeEpisode(profileID: profileID, media: show)
        XCTAssertEqual(resume.0, 1)
        XCTAssertEqual(resume.1, 2)
    }
}
