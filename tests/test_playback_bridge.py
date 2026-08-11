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

    def test_webengine_profile_outlives_pages(self) -> None:
        self.assertIn("_MEDIA_WEB_PROFILE = profile", self.main_source)
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
                clientWidth: 1280,
                clientHeight: 720,
                paused: false,
                ended: false,
                addEventListener() {{}},
                play() {{ this.played = true; return {{ catch() {{}} }}; }},
                pause() {{ this.pausedByPiStick = true; }}
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
            window.postMessage({{
                type: 'pistick-media-command',
                bridgeToken: 'test-bridge-token',
                action: 'pause'
            }});
            if (!video.pausedByPiStick || childMessages.length !== 1) process.exit(2);
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
            """
        )


if __name__ == "__main__":
    unittest.main()
