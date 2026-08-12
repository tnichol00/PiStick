import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PlaybackBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.main_source = (ROOT / "main.py").read_text(encoding="utf-8")
        module = ast.parse(cls.main_source)
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(
                isinstance(target, ast.Name)
                and target.id == "_PLAYBACK_FRAME_BRIDGE_SOURCE"
                for target in node.targets
            ):
                cls.bridge_template = ast.literal_eval(node.value)
                break
        else:
            raise AssertionError("Playback frame bridge source is missing")
        cls.bridge_source = cls.bridge_template.replace(
            "__PISTICK_BRIDGE_TOKEN__",
            json.dumps("test-bridge-token"),
        )

    def run_node(self, source: str) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run(
            [node, "-e", source],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_bridge_uses_messages_without_cross_origin_dom_reads(self) -> None:
        self.assertIn("window.top.postMessage", self.bridge_source)
        self.assertIn("window.frames[index].postMessage", self.bridge_source)
        for forbidden in (
            "window.parent.addEventListener",
            "iframe.contentWindow.document",
            "document.domain",
        ):
            self.assertNotIn(forbidden, self.bridge_source)

    def test_bridge_is_injected_into_subframes(self) -> None:
        self.assertIn("setRunsOnSubFrames(True)", self.main_source)
        self.assertIn('setName("pistick-cross-frame-media-bridge")', self.main_source)

    def test_main_uses_videasy_ids_and_documented_resume_parameter(self) -> None:
        module = ast.parse(self.main_source)
        movie_calls = []
        show_calls = []
        for node in ast.walk(module):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id == "getmovie":
                movie_calls.append(node)
            elif node.func.id == "getshow":
                show_calls.append(node)
        self.assertTrue(movie_calls)
        self.assertTrue(show_calls)
        self.assertTrue(all(len(call.args) == 1 for call in movie_calls))
        self.assertTrue(all(len(call.args) == 3 for call in show_calls))
        self.assertTrue(
            all(
                any(keyword.arg == "progress_seconds" for keyword in call.keywords)
                for call in movie_calls + show_calls
            )
        )
        self.assertNotIn("startAt", self.main_source)
        self.assertIn("resume_seconds=start_seconds", self.main_source)
        self.assertIn("progress_seconds=start_seconds", self.main_source)

    def test_webengine_profile_outlives_pages(self) -> None:
        self.assertIn("_PLAYBACK_WEB_PROFILE = profile", self.main_source)
        self.assertIn("_TRAILER_WEB_PROFILE = profile", self.main_source)
        self.assertIn("replacement_page = QWebEnginePage(self)", self.main_source)
        self.assertNotIn('QWebEngineProfile("pistick-embedded-media", self)', self.main_source)

    def test_generated_bridge_has_valid_javascript_syntax(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is unavailable")
        result = subprocess.run(
            [node, "--check", "-"],
            input=self.bridge_source,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_top_frame_collects_progress_and_relays_commands(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            const video = {{
                currentTime: 42,
                duration: 100,
                readyState: 4,
                clientWidth: 1280,
                clientHeight: 720,
                paused: false,
                ended: false,
                addEventListener() {{}},
                play() {{ this.played = true; this.paused = false; return {{ catch() {{}} }}; }},
                pause() {{ this.pausedByPiStick = true; this.paused = true; }}
            }};
            const childMessages = [];
            const child = {{ postMessage(data) {{ childMessages.push(data); }} }};
            const window = new EventTarget();
            window.top = window;
            window.frames = [child];
            window.setInterval = (callback) => {{ callback(); return 1; }};
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll: (selector) => selector === 'video' ? [video] : [],
                addEventListener() {{}}
            }};
            const context = {{ window, document, console, Number, Array, Math, Date }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            const state = window.__pistickPlaybackState;
            if (!state || state.currentTime !== 42 || state.duration !== 100) process.exit(1);
            const initialChildMessages = childMessages.length;
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'pause'
            }});
            if (
                !video.pausedByPiStick
                || !video.paused
                || childMessages.length !== initialChildMessages + 1
            ) process.exit(2);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'toggle'
            }});
            if (!video.played || video.paused || childMessages.length !== initialChildMessages + 2) process.exit(3);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'seek',
                positionSeconds: 65
            }});
            if (video.currentTime !== 65 || childMessages.length !== initialChildMessages + 3) process.exit(4);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'seek-relative',
                offsetSeconds: -10
            }});
            if (video.currentTime !== 55 || childMessages.length !== initialChildMessages + 4) process.exit(5);
            """
        )

    def test_bridge_autoplays_and_toggles_only_english_subtitles(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            const english = {{ language: 'en-US', label: 'English', mode: 'showing' }};
            const french = {{ language: 'fr', label: 'French', mode: 'showing' }};
            const video = {{
                currentTime: 0,
                duration: 120,
                readyState: 4,
                clientWidth: 1280,
                clientHeight: 720,
                paused: true,
                ended: false,
                textTracks: [english, french],
                addEventListener() {{}},
                play() {{
                    this.playedByPiStick = true;
                    this.paused = false;
                    return Promise.resolve();
                }},
                pause() {{ this.paused = true; }}
            }};
            const hls = {{
                levels: [],
                subtitleTracks: [
                    {{ lang: 'eng', name: 'English' }},
                    {{ lang: 'fra', name: 'French' }}
                ],
                subtitleTrack: 0,
                subtitleDisplay: true
            }};
            const window = new EventTarget();
            window.top = window;
            window.frames = [];
            window.__player = {{ state: {{ hls }} }};
            window.setInterval = (callback) => {{ callback(); return 1; }};
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll: (selector) => selector === 'video' ? [video] : [],
                addEventListener() {{}}
            }};
            const context = {{ window, document, console, Number, Array, Math, Date, String, Promise }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            if (!video.playedByPiStick || video.paused) process.exit(1);
            if (english.mode !== 'disabled' || french.mode !== 'disabled') process.exit(2);
            if (hls.subtitleTrack !== -1 || hls.subtitleDisplay !== false) process.exit(3);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'subtitles-english-toggle'
            }});
            if (english.mode !== 'showing' || french.mode !== 'disabled') process.exit(4);
            if (hls.subtitleTrack !== 0 || hls.subtitleDisplay !== true) process.exit(5);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'subtitles-english-toggle'
            }});
            if (english.mode !== 'disabled' || hls.subtitleTrack !== -1) process.exit(6);
            """
        )

    def test_bridge_advances_past_videasy_start_overlay_before_video_exists(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            const playIcon = {{}};
            const overlay = {{
                clicks: 0,
                querySelector(selector) {{
                    return selector === 'button svg path[d="M8 5v14l11-7z"]'
                        ? playIcon
                        : null;
                }},
                click() {{ this.clicks += 1; }}
            }};
            const video = {{
                currentTime: 0,
                duration: 120,
                readyState: 4,
                clientWidth: 1280,
                clientHeight: 720,
                paused: true,
                ended: false,
                textTracks: [],
                addEventListener() {{}},
                play() {{
                    this.playedByPiStick = true;
                    this.paused = false;
                    return Promise.resolve();
                }},
                pause() {{ this.paused = true; }}
            }};
            let videos = [];
            let intervalCallback = null;
            const window = new EventTarget();
            window.top = window;
            window.frames = [];
            window.location = {{ hostname: 'player.videasy.to' }};
            window.setInterval = (callback) => {{
                intervalCallback = callback;
                callback();
                return 1;
            }};
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll(selector) {{
                    if (selector === 'video') return videos;
                    if (
                        selector === 'div.fixed.inset-0.bg-black.cursor-pointer.select-none'
                    ) return [overlay];
                    return [];
                }},
                addEventListener() {{}}
            }};
            const context = {{
                window, document, console, Number, Array, Math, Date, String, Promise
            }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            if (overlay.clicks !== 1) process.exit(1);
            if (!intervalCallback) process.exit(2);
            videos = [video];
            intervalCallback();
            if (!video.playedByPiStick || video.paused) process.exit(3);
            if (overlay.clicks !== 1) process.exit(4);
            """
        )

    def test_controller_toggle_uses_videasy_state_and_reveals_controls(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            class MouseEvent {{
                constructor(type, options) {{
                    this.type = type;
                    this.bubbles = Boolean(options && options.bubbles);
                }}
            }}
            const video = {{
                currentTime: 20,
                duration: 100,
                readyState: 4,
                clientWidth: 1280,
                clientHeight: 720,
                paused: false,
                ended: false,
                controls: false,
                clicks: 0,
                mouseMoves: 0,
                addEventListener() {{}},
                click() {{
                    this.clicks += 1;
                    this.paused = !this.paused;
                }},
                dispatchEvent(event) {{
                    if (event && event.type === 'mousemove') this.mouseMoves += 1;
                    return true;
                }},
                play() {{ this.paused = false; return Promise.resolve(); }},
                pause() {{ this.paused = true; }}
            }};
            const window = new EventTarget();
            window.top = window;
            window.frames = [];
            window.location = {{ hostname: 'player.videasy.to' }};
            window.MouseEvent = MouseEvent;
            window.setInterval = (callback) => {{ callback(); return 1; }};
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll: (selector) => selector === 'video' ? [video] : [],
                addEventListener() {{}}
            }};
            const context = {{
                window, document, console, Number, Array, Math, Date, String, Promise
            }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'toggle'
            }});
            if (!video.paused || video.clicks !== 1 || video.mouseMoves < 1) process.exit(1);
            if (video.controls) process.exit(2);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'toggle'
            }});
            if (video.paused || video.clicks !== 2 || video.mouseMoves < 2) process.exit(3);
            """
        )

    def test_bridge_forces_the_1080p_hls_level_when_available(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            const hls = {{
                levels: [
                    {{ height: 480, bitrate: 800000 }},
                    {{ height: 1080, bitrate: 4500000 }},
                    {{ height: 2160, bitrate: 12000000 }}
                ],
                currentLevel: -1,
                nextLevel: -1,
                loadLevel: -1,
                autoLevelCapping: -1,
                capLevelToPlayerSize: true
            }};
            const playerState = {{
                hls,
                levels: hls.levels,
                quality: 'auto',
                settings: {{ quality: 'Auto' }}
            }};
            const window = new EventTarget();
            window.top = window;
            window.frames = [];
            window.__player = {{ state: playerState }};
            window.setInterval = (callback) => {{ callback(); return 1; }};
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll: () => [],
                addEventListener() {{}}
            }};
            const context = {{ window, document, console, Number, Array, Math, Date, String }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            if (hls.currentLevel !== 1 || hls.nextLevel !== 1 || hls.loadLevel !== 1) process.exit(1);
            if (hls.autoLevelCapping !== 1 || hls.capLevelToPlayerSize !== false) process.exit(2);
            if (playerState.quality !== 1 || playerState.settings.quality !== '1080p') process.exit(3);
            if (!window.__pistickForcedQuality || window.__pistickForcedQuality.selectedHeight !== 1080) process.exit(4);
            """
        )

    def test_bridge_accepts_videasy_player_event_strings(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            const window = new EventTarget();
            window.top = window;
            window.frames = [];
            window.setInterval = () => 1;
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll: () => [],
                addEventListener() {{}}
            }};
            const context = {{ window, document, console, Number, Array, Math, Date, JSON }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            window.postMessage(JSON.stringify({{
                type: 'PLAYER_EVENT',
                data: {{
                    event: 'timeupdate',
                    timestamp: 77,
                    duration: 200,
                    progress: 38.5,
                    type: 'movie',
                    id: '550'
                }}
            }}));
            const state = window.__pistickPlaybackState;
            if (!state || state.currentTime !== 77 || state.duration !== 200) process.exit(1);
            """
        )

    def test_subframe_posts_progress_without_reading_top_window(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            const posted = [];
            const topWindow = new Proxy(
                {{ postMessage(data) {{ posted.push(data); }} }},
                {{
                    get(target, property) {{
                        if (property === 'postMessage') return target.postMessage;
                        throw new Error('forbidden cross-origin read: ' + String(property));
                    }}
                }}
            );
            const listeners = new Map();
            const window = {{
                top: topWindow,
                frames: [],
                addEventListener(name, callback) {{ listeners.set(name, callback); }},
                setInterval(callback) {{ callback(); return 1; }},
                clearInterval() {{}}
            }};
            const video = {{
                currentTime: 18,
                duration: 90,
                readyState: 4,
                clientWidth: 640,
                clientHeight: 360,
                paused: false,
                ended: false,
                addEventListener() {{}}
            }};
            const document = {{
                readyState: 'complete',
                querySelectorAll: (selector) => selector === 'video' ? [video] : [],
                addEventListener() {{}}
            }};
            const context = {{ window, document, console, Number, Array, Math, Date }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            if (posted.length === 0) process.exit(1);
            if (posted[0].currentTime !== 18 || posted[0].duration !== 90) process.exit(2);
            listeners.get('message')({{
                data: {{
                    type: 'pistick-media-command',
                    bridgeToken: 'test-bridge-token',
                    action: 'seek',
                    positionSeconds: 45
                }}
            }});
            if (video.currentTime !== 45) process.exit(3);
            """
        )

    def test_resume_seek_waits_for_video_metadata(self) -> None:
        bridge = json.dumps(self.bridge_source)
        self.run_node(
            f"""
            const vm = require('vm');
            class EventTarget {{
                constructor() {{ this.listeners = new Map(); }}
                addEventListener(name, callback) {{
                    if (!this.listeners.has(name)) this.listeners.set(name, []);
                    this.listeners.get(name).push(callback);
                }}
                dispatch(name, event) {{
                    for (const callback of this.listeners.get(name) || []) callback(event);
                }}
            }}
            const videoListeners = new Map();
            const video = {{
                currentTime: 0,
                duration: Number.NaN,
                readyState: 0,
                clientWidth: 1280,
                clientHeight: 720,
                paused: true,
                ended: false,
                addEventListener(name, callback) {{
                    if (!videoListeners.has(name)) videoListeners.set(name, []);
                    videoListeners.get(name).push(callback);
                }}
            }};
            const window = new EventTarget();
            window.top = window;
            window.frames = [];
            window.setInterval = (callback) => {{ callback(); return 1; }};
            window.clearInterval = () => {{}};
            window.postMessage = (data) => window.dispatch('message', {{ data }});
            const document = {{
                readyState: 'complete',
                querySelectorAll: (selector) => selector === 'video' ? [video] : [],
                addEventListener() {{}}
            }};
            const context = {{ window, document, console, Number, Array, Math, Date }};
            vm.createContext(context);
            vm.runInContext({bridge}, context);
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'seek',
                positionSeconds: 75
            }});
            if (video.currentTime !== 0) process.exit(1);
            video.duration = 120;
            video.readyState = 1;
            for (const callback of videoListeners.get('loadedmetadata') || []) callback();
            if (video.currentTime !== 75) process.exit(2);
            """
        )


if __name__ == "__main__":
    unittest.main()
