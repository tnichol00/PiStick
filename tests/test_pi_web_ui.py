import hashlib
from pathlib import Path
import shutil
import subprocess
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "pistick_server" / "static"


class PiWebUIContractTests(unittest.TestCase):
    def test_visible_markup_and_styles_are_unchanged(self) -> None:
        expected = {
            "index.html": "e482f2e681ba003d096418e6f1c687e1f2bae19ba27a10f87dd2c7a002597362",
            "styles.css": "be4bcb972a4f3e17bec5e479fbce8c11e028d3327f4174b809c90d143aa77804",
        }
        for name, digest in expected.items():
            with self.subTest(name=name):
                actual = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()
                self.assertEqual(actual, digest)

    def test_pi_runtime_scheduling_and_player_security(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed")
        harness = textwrap.dedent(
            r"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            let source = fs.readFileSync(process.argv[1], "utf8");
            const bootstrapLine = '  document.addEventListener("DOMContentLoaded", bootstrap);';
            assert(source.includes(bootstrapLine));
            source = source.replace(
              bootstrapLine,
              "  globalThis.__pistickTest = { state: state, ui: ui, " +
              "createPlayerFrame: createPlayerFrame, pollGamepads: pollGamepads, " +
              "savePlayerProgress: savePlayerProgress };"
            );

            const timers = [];
            const document = {
              activeElement: null,
              body: { style: {} },
              documentElement: { classList: { toggle: function () {} } },
              hidden: false,
              addEventListener: function () {},
              createElement: function (tag) {
                assert.strictEqual(tag, "iframe");
                const attributes = new Map();
                return {
                  setAttribute: function (name, value) { attributes.set(name, String(value)); },
                  hasAttribute: function (name) { return attributes.has(name); },
                  removeAttribute: function (name) { attributes.delete(name); },
                  getAttribute: function (name) { return attributes.get(name); }
                };
              }
            };
            const window = {
              location: { search: "?platform=pi-zero-w" },
              addEventListener: function () {},
              clearTimeout: function () {},
              setTimeout: function (callback, delay) {
                timers.push({ callback: callback, delay: delay });
                return timers.length;
              }
            };
            const navigator = { getGamepads: function () { return []; } };
            const context = vm.createContext({
              URLSearchParams: URLSearchParams,
              Date: Date,
              console: console,
              document: document,
              fetch: function () { throw new Error("unexpected fetch"); },
              navigator: navigator,
              window: window
            });
            vm.runInContext(source, context, { filename: process.argv[1] });
            const hooks = context.__pistickTest;

            function installOldFrame() {
              const oldFrame = {};
              oldFrame.parentNode = {
                replaceChild: function (next, previous) {
                  assert.strictEqual(previous, oldFrame);
                  assert(next);
                }
              };
              hooks.ui.playerFrame = oldFrame;
            }

            installOldFrame();
            const videasy = hooks.createPlayerFrame(true);
            assert.strictEqual(videasy.hasAttribute("sandbox"), false);

            installOldFrame();
            const youtube = hooks.createPlayerFrame(false);
            assert.strictEqual(youtube.hasAttribute("sandbox"), true);
            assert(youtube.getAttribute("sandbox").includes("allow-scripts"));

            hooks.state.gamepadTimer = null;
            hooks.pollGamepads();
            assert.strictEqual(timers.at(-1).delay, 1000);

            navigator.getGamepads = function () {
              return [{
                index: 0,
                buttons: Array.from({ length: 16 }, function () { return { pressed: false }; }),
                axes: [0, 0]
              }];
            };
            hooks.state.gamepadTimer = null;
            hooks.pollGamepads();
            assert.strictEqual(timers.at(-1).delay, 50);

            let releaseFirst;
            const requests = [];
            context.fetch = function (path, settings) {
              requests.push({ path: path, body: JSON.parse(settings.body) });
              const response = { ok: true, json: async function () { return {}; } };
              if (requests.length === 1) {
                return new Promise(function (resolve) {
                  releaseFirst = function () { resolve(response); };
                });
              }
              return Promise.resolve(response);
            };
            hooks.state.activeProfile = { id: "profile-1" };
            hooks.state.player = {
              media: { id: 550, media_type: "movie", title: "Fight Club" },
              episode: null,
              trailer: false,
              position: 10,
              duration: 100,
              lastSavedAt: 0,
              lastSavedPosition: null,
              lastSavedDuration: null,
              saving: null
            };

            (async function () {
              const first = hooks.savePlayerProgress(false);
              await Promise.resolve();
              hooks.state.player.position = 20;
              const finalSave = hooks.savePlayerProgress(true);
              releaseFirst();
              await Promise.all([first, finalSave]);
              assert.strictEqual(requests.length, 2);
              assert.strictEqual(requests[1].body.position_seconds, 20);
              assert.strictEqual(requests[1].body.duration_seconds, 100);
            })().catch(function (error) {
              console.error(error);
              process.exitCode = 1;
            });
            """
        )
        completed = subprocess.run(
            [node, "-e", harness, str(STATIC / "app.js")],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_app_uses_pi_zero_friendly_idle_work(self) -> None:
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        self.assertIn("var PROGRESS_SAVE_INTERVAL_MS = 15000;", source)
        self.assertIn("var GAMEPAD_IDLE_POLL_MS = 1000;", source)
        self.assertIn("Promise.all([api(\"/api/status\"), api(\"/api/profiles\")])", source)
        self.assertNotIn("requestAnimationFrame(pollGamepads)", source)
        self.assertIn(r"player\.videasy\.(to|net)", source)
        self.assertIn("if (!videasy && !youtube)", source)

    def test_movie_dialog_renders_before_slow_metadata_request_finishes(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is not installed")
        harness = textwrap.dedent(
            r"""
            const assert = require("assert");
            const fs = require("fs");
            const vm = require("vm");

            let source = fs.readFileSync(process.argv[1], "utf8");
            const bootstrapLine = '  document.addEventListener("DOMContentLoaded", bootstrap);';
            assert(source.includes(bootstrapLine));
            source = source.replace(
              bootstrapLine,
              "  globalThis.__pistickDetailsTest = { state: state, ui: ui, openDetails: openDetails };"
            );
            const renderStart = source.indexOf("  function renderDetails(media) {");
            const renderEnd = source.indexOf("\n  function openDetails(media, refresh) {", renderStart);
            assert(renderStart >= 0 && renderEnd > renderStart);
            source = source.slice(0, renderStart) +
              "  function renderDetails(media) { state.detailsMedia = media; globalThis.__detailRenders.push(media); }\n" +
              source.slice(renderEnd + 1);

            const fetchCalls = [];
            let releaseFetch;
            const document = {
              documentElement: { classList: { toggle: function () {} } },
              addEventListener: function () {}
            };
            const window = {
              location: { search: "?platform=pi-zero-w" },
              addEventListener: function () {},
              clearTimeout: function () {},
              setTimeout: function () { return 1; }
            };
            const context = vm.createContext({
              URLSearchParams: URLSearchParams,
              console: console,
              document: document,
              fetch: function (path) {
                fetchCalls.push(path);
                return new Promise(function (resolve) { releaseFetch = resolve; });
              },
              window: window,
              __detailRenders: []
            });
            vm.runInContext(source, context, { filename: process.argv[1] });
            const hooks = context.__pistickDetailsTest;
            hooks.state.activeProfile = { id: "profile-1" };
            hooks.ui.detailsDialog = {
              open: false,
              dataset: {},
              showModal: function () { this.open = true; }
            };
            hooks.ui.detailsContent = {};
            hooks.ui.toast = { textContent: "", classList: { add: function () {}, remove: function () {} } };

            const movie = {
              id: 550,
              media_type: "movie",
              title: "Fight Club",
              overview: "Already available from the home screen."
            };
            hooks.openDetails(movie, false);
            assert.strictEqual(hooks.ui.detailsDialog.open, true);
            assert.strictEqual(context.__detailRenders.length, 1);
            assert.strictEqual(context.__detailRenders[0].title, "Fight Club");
            assert.strictEqual(fetchCalls.length, 1);
            assert.strictEqual(
              fetchCalls[0],
              "/api/media/movie/550/extras?profile_id=profile-1"
            );

            (async function () {
              releaseFetch({
                ok: true,
                json: async function () {
                  return { media: { id: 550, media_type: "movie", videos: { results: [{ key: "abc123", site: "YouTube", type: "Trailer" }] } } };
                }
              });
              await new Promise(setImmediate);
              await new Promise(setImmediate);
              assert.strictEqual(context.__detailRenders.length, 2);
              assert.strictEqual(
                context.__detailRenders[1].overview,
                "Already available from the home screen."
              );
              assert.strictEqual(context.__detailRenders[1].videos.results[0].key, "abc123");

              hooks.openDetails(movie, false);
              assert.strictEqual(context.__detailRenders.length, 3);
              assert.strictEqual(fetchCalls.length, 1);

              hooks.openDetails(movie, true);
              assert.strictEqual(context.__detailRenders.length, 4);
              assert.strictEqual(fetchCalls.length, 2);
              releaseFetch({
                ok: true,
                json: async function () {
                  return { media: { id: 550, media_type: "movie", videos: { results: [] } } };
                }
              });
              await new Promise(setImmediate);
              await new Promise(setImmediate);
              assert.strictEqual(context.__detailRenders.length, 5);
            })().catch(function (error) {
              console.error(error);
              process.exitCode = 1;
            });
            """
        )
        completed = subprocess.run(
            [node, "-e", harness, str(STATIC / "app.js")],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
