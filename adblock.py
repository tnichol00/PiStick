"""Playback-only ad, tracker, pop-up, and unsafe-site filtering for PiStick.

PiStick combines a small built-in safety list with a locally cached, maintained
hosts list.  Windows WebView2 traffic can additionally pass through the local
``PlaybackAdBlockProxy`` so requests from cross-origin player frames are
filtered without decrypting HTTPS traffic or weakening browser security.
"""

from __future__ import annotations

import json
import os
import select
import socket
import socketserver
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_REMOTE_BLOCKLIST_URL = "https://big.oisd.nl/domainswild"
REMOTE_BLOCKLIST_MAX_AGE_SECONDS = 24 * 60 * 60
REMOTE_BLOCKLIST_MAX_BYTES = 32 * 1024 * 1024
REMOTE_BLOCKLIST_MAX_HOSTS = 300_000
REMOTE_BLOCKLIST_MIN_HOSTS = 10_000


DEFAULT_AD_HOST_SUFFIXES = frozenset(
    {
        "2mdn.net",
        "adform.net",
        "adition.com",
        "adnxs.com",
        "adsrvr.org",
        "adservice.google.com",
        "adsterra.com",
        "adsterratech.com",
        "amazon-adsystem.com",
        "casalemedia.com",
        "clickadu.com",
        "doubleclick.net",
        "exoclick.com",
        "exosrv.com",
        # VidCore currently injects this pop-under loader twice per minute.
        # It is intentionally built in so first-run playback is protected
        # before the maintained online list has finished downloading.
        "ferocitycandour.com",
        "googleadservices.com",
        "googlesyndication.com",
        "googletagservices.com",
        "highperformancecpm.com",
        "highperformanceformat.com",
        "hilltopads.net",
        "imrworldwide.com",
        "juicyads.com",
        "media.net",
        "moatads.com",
        "monetag.com",
        "onclicka.com",
        "openx.net",
        "outbrain.com",
        "popads.net",
        "popcash.net",
        "profitableratecpm.com",
        "propellerads.com",
        "pubmatic.com",
        "pushground.com",
        "quantserve.com",
        "revcontent.com",
        "rubiconproject.com",
        "scorecardresearch.com",
        "smartadserver.com",
        "taboola.com",
        "trafficjunky.net",
        "zedo.com",
    }
)

_COSMETIC_SELECTORS = (
    ".adsbygoogle",
    "ins.adsbygoogle",
    "[data-ad-client]",
    "[data-ad-slot]",
    "[data-ad-unit]",
    "[aria-label='Advertisement']",
    "[aria-label='advertisement']",
    "[id^='google_ads_']",
    ".ad-banner",
    ".ad-container",
    ".ad-overlay",
    ".advertisement",
    "[id^='ad_']",
    "[id^='ad-']",
    "[class^='ad_']",
    "[class^='ad-']",
    "[class*=' ad_']",
    "[class*=' ad-']",
    "[id*='popunder' i]",
    "[class*='popunder' i]",
    "[id*='popup-ad' i]",
    "[class*='popup-ad' i]",
    "[id*='interstitial-ad' i]",
    "[class*='interstitial-ad' i]",
)

_SAFE_IFRAME_SANDBOX = (
    "allow-forms allow-orientation-lock allow-pointer-lock allow-presentation "
    "allow-same-origin allow-scripts"
)

_SAFE_IFRAME_FEATURES = (
    "autoplay; encrypted-media; fullscreen; picture-in-picture"
)


@dataclass
class AdBlockSettings:
    enabled: bool
    blocked_hosts: frozenset[str]
    allowed_hosts: frozenset[str] = field(default_factory=frozenset)
    script_hosts: frozenset[str] = field(default_factory=frozenset)
    configured_hosts: frozenset[str] = field(default_factory=frozenset, repr=False)
    online_list_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.configured_hosts:
            self.configured_hosts = frozenset(self.blocked_hosts)
        if not self.script_hosts:
            self.script_hosts = frozenset(self.configured_hosts)

    def replace_online_hosts(self, hosts: Iterable[str]) -> None:
        """Atomically replace cached online entries while retaining local rules."""
        self.blocked_hosts = frozenset(self.configured_hosts.union(hosts))


def _config_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _normalize_host(value: object) -> str:
    if not isinstance(value, str):
        return ""
    host = value.strip().lower().rstrip(".")
    while host.startswith("*."):
        host = host[2:]
    host = host.lstrip(".")
    if not host or len(host) > 253 or any(char in host for char in "/:@?#"):
        return ""
    labels = host.split(".")
    if len(labels) < 2:
        return ""
    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(char.isalnum() or char == "-" for char in label)
        ):
            return ""
    return host


def _custom_hosts(values: object) -> Iterable[str]:
    if not isinstance(values, list):
        return ()
    normalized: list[str] = []
    for value in values[:256]:
        host = _normalize_host(value)
        if host:
            normalized.append(host)
    return normalized


def _host_matches(host: str, suffixes: frozenset[str]) -> bool:
    """Match an exact host or one of its parent suffixes in constant-time steps."""
    candidate = str(host or "").lower().rstrip(".")
    while candidate:
        if candidate in suffixes:
            return True
        dot = candidate.find(".")
        if dot < 0:
            return False
        candidate = candidate[dot + 1 :]
    return False


def parse_hosts_blocklist(
    value: object,
    *,
    maximum_hosts: int = REMOTE_BLOCKLIST_MAX_HOSTS,
) -> frozenset[str]:
    """Parse either a hosts-format list or a one-domain-per-line list."""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="ignore")
    elif isinstance(value, str):
        text = value
    else:
        return frozenset()

    hosts: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if not fields:
            continue
        if fields[0] in {"0", "0.0.0.0", "127.0.0.1", "::", "::1"}:
            candidates = fields[1:]
        elif len(fields) == 1:
            candidates = fields
        else:
            continue
        for candidate in candidates:
            host = _normalize_host(candidate)
            if host and host not in {"localhost", "localhost.localdomain"}:
                hosts.add(host)
                if len(hosts) >= maximum_hosts:
                    return frozenset(hosts)
    return frozenset(hosts)


def _read_cached_hosts(cache_path: Optional[Path]) -> frozenset[str]:
    if cache_path is None:
        return frozenset()
    try:
        data = Path(cache_path).read_bytes()
    except OSError:
        return frozenset()
    if len(data) > REMOTE_BLOCKLIST_MAX_BYTES:
        return frozenset()
    return parse_hosts_blocklist(data)


def load_adblock_settings(
    config_path: Path,
    cache_path: Optional[Path] = None,
) -> AdBlockSettings:
    """Load safe ad-block preferences without exposing private config values."""
    payload: object = {}
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        pass
    if not isinstance(payload, dict):
        payload = {}

    enabled = _config_bool(payload.get("adblock_enabled"), True)
    online_list_enabled = _config_bool(payload.get("adblock_online_lists"), True)
    configured_hosts = set(DEFAULT_AD_HOST_SUFFIXES)
    configured_hosts.update(_custom_hosts(payload.get("adblock_domains")))
    allowed_hosts = set(_custom_hosts(payload.get("adblock_allow_domains")))
    playback_host = url_host(payload.get("playback_base_url"))
    if playback_host:
        allowed_hosts.add(playback_host)

    configured = frozenset(configured_hosts)
    cached_hosts = (
        _read_cached_hosts(cache_path)
        if enabled and online_list_enabled
        else frozenset()
    )
    return AdBlockSettings(
        enabled=enabled,
        blocked_hosts=frozenset(configured.union(cached_hosts)),
        allowed_hosts=frozenset(allowed_hosts),
        script_hosts=configured,
        configured_hosts=configured,
        online_list_enabled=online_list_enabled,
    )


def url_host(url: object) -> str:
    try:
        parsed = urlsplit(str(url))
        return (parsed.hostname or "").lower().rstrip(".")
    except (TypeError, ValueError):
        return ""


def is_blocked_ad_url(url: object, settings: AdBlockSettings) -> bool:
    if not settings.enabled:
        return False
    host = url_host(url)
    return is_blocked_ad_host(host, settings)


def is_blocked_ad_host(host: object, settings: AdBlockSettings) -> bool:
    if not settings.enabled:
        return False
    normalized = str(host or "").lower().rstrip(".")
    if not normalized:
        return False
    if _host_matches(normalized, settings.allowed_hosts):
        return False
    return _host_matches(normalized, settings.blocked_hosts)


def refresh_adblock_cache(
    settings: AdBlockSettings,
    cache_path: Path,
    *,
    source_url: str = DEFAULT_REMOTE_BLOCKLIST_URL,
    opener: Optional[Callable[..., object]] = None,
    minimum_hosts: int = REMOTE_BLOCKLIST_MIN_HOSTS,
) -> bool:
    """Download, validate, atomically cache, and activate the maintained list."""
    if not settings.enabled or not settings.online_list_enabled:
        return False

    request = Request(
        source_url,
        headers={
            "Accept": "text/plain",
            "User-Agent": "PiStick-AdBlock/2.0",
        },
    )
    open_request = opener or urlopen
    response = open_request(request, timeout=12)
    try:
        data = response.read(REMOTE_BLOCKLIST_MAX_BYTES + 1)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    if len(data) > REMOTE_BLOCKLIST_MAX_BYTES:
        raise ValueError("Ad-block list is larger than the safe download limit")

    hosts = parse_hosts_blocklist(data)
    if len(hosts) < max(1, int(minimum_hosts)):
        raise ValueError("Ad-block list did not contain enough valid hosts")

    destination = Path(cache_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(f"# Source: {source_url}\n")
            temporary.write("# Cached by PiStick; one normalized host per line.\n")
            for host in sorted(hosts):
                temporary.write(host)
                temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass

    settings.replace_online_hosts(hosts)
    return True


def start_adblock_cache_refresh(
    settings: AdBlockSettings,
    cache_path: Path,
    *,
    maximum_age_seconds: int = REMOTE_BLOCKLIST_MAX_AGE_SECONDS,
) -> Optional[threading.Thread]:
    """Refresh a stale list in the background without delaying the PiStick UI."""
    if not settings.enabled or not settings.online_list_enabled:
        return None
    try:
        age = max(0.0, time.time() - Path(cache_path).stat().st_mtime)
        if age <= max(0, int(maximum_age_seconds)):
            return None
    except OSError:
        pass

    def worker() -> None:
        try:
            if refresh_adblock_cache(settings, cache_path):
                print(
                    f"[PiStick] Strong ad-block list ready: "
                    f"{len(settings.blocked_hosts):,} hosts"
                )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            print(
                "[PiStick] Strong ad-block list refresh unavailable; "
                f"using cached/built-in rules ({exc.__class__.__name__})."
            )

    thread = threading.Thread(
        target=worker,
        name="pistick-adblock-refresh",
        daemon=True,
    )
    thread.start()
    return thread


def same_origin(first_url: object, second_url: object) -> bool:
    """Return whether two HTTP(S) URLs have the same scheme, host, and port."""
    try:
        first = urlsplit(str(first_url))
        second = urlsplit(str(second_url))
        if first.scheme.lower() not in {"http", "https"}:
            return False
        if second.scheme.lower() not in {"http", "https"}:
            return False

        def origin(parsed) -> tuple[str, str, int]:
            scheme = parsed.scheme.lower()
            default_port = 443 if scheme == "https" else 80
            return scheme, (parsed.hostname or "").lower(), parsed.port or default_port

        return origin(first) == origin(second)
    except (TypeError, ValueError):
        return False


class _PlaybackProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, settings: AdBlockSettings):
        self.settings = settings
        super().__init__(("127.0.0.1", 0), _PlaybackProxyHandler)


class _PlaybackProxyHandler(socketserver.BaseRequestHandler):
    """Small HTTP CONNECT proxy that filters hosts without decrypting content."""

    server: _PlaybackProxyServer
    _MAX_HEADER_BYTES = 64 * 1024

    @staticmethod
    def _authority(value: str, default_port: int) -> tuple[str, int]:
        try:
            parsed = urlsplit(f"//{value.strip()}")
            host = (parsed.hostname or "").lower().rstrip(".")
            port = parsed.port or default_port
        except ValueError:
            return "", 0
        if not host or not 0 < port <= 65535:
            return "", 0
        return host, port

    def _read_request(self) -> tuple[bytes, bytes]:
        received = bytearray()
        while b"\r\n\r\n" not in received:
            chunk = self.request.recv(16 * 1024)
            if not chunk:
                return b"", b""
            received.extend(chunk)
            if len(received) > self._MAX_HEADER_BYTES:
                return b"", b""
        header, body = bytes(received).split(b"\r\n\r\n", 1)
        return header, body

    def _reject(self, status: str = "403 Forbidden") -> None:
        try:
            self.request.sendall(
                f"HTTP/1.1 {status}\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n".encode("ascii")
            )
        except OSError:
            pass

    @staticmethod
    def _relay(client: socket.socket, upstream: socket.socket) -> None:
        sockets = (client, upstream)
        while True:
            try:
                readable, _, _ = select.select(sockets, (), (), 30)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            for source in readable:
                destination = upstream if source is client else client
                try:
                    data = source.recv(64 * 1024)
                    if not data:
                        return
                    destination.sendall(data)
                except OSError:
                    return

    def handle(self) -> None:
        self.request.settimeout(15)
        try:
            header, body = self._read_request()
            if not header:
                self._reject("400 Bad Request")
                return
            lines = header.decode("iso-8859-1", errors="replace").split("\r\n")
            request_parts = lines[0].split(" ", 2)
            if len(request_parts) != 3:
                self._reject("400 Bad Request")
                return
            method, target, version = request_parts
            headers = lines[1:]

            if method.upper() == "CONNECT":
                host, port = self._authority(target, 443)
                if not host:
                    self._reject("400 Bad Request")
                    return
                if is_blocked_ad_host(host, self.server.settings):
                    self._reject()
                    return
                try:
                    upstream = socket.create_connection((host, port), timeout=15)
                except OSError:
                    self._reject("502 Bad Gateway")
                    return
                with upstream:
                    self.request.sendall(
                        b"HTTP/1.1 200 Connection Established\r\n\r\n"
                    )
                    self.request.settimeout(None)
                    upstream.settimeout(None)
                    self._relay(self.request, upstream)
                return

            parsed = urlsplit(target)
            host_header = next(
                (
                    line.split(":", 1)[1].strip()
                    for line in headers
                    if line.lower().startswith("host:")
                ),
                "",
            )
            if parsed.hostname:
                host = parsed.hostname.lower().rstrip(".")
                try:
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                except ValueError:
                    self._reject("400 Bad Request")
                    return
                path = parsed.path or "/"
                if parsed.query:
                    path += f"?{parsed.query}"
            else:
                host, port = self._authority(host_header, 80)
                path = target or "/"
            if not host or is_blocked_ad_host(host, self.server.settings):
                self._reject("403 Forbidden" if host else "400 Bad Request")
                return

            try:
                upstream = socket.create_connection((host, port), timeout=15)
            except OSError:
                self._reject("502 Bad Gateway")
                return
            forwarded_headers = [
                line
                for line in headers
                if not line.lower().startswith(("proxy-connection:", "proxy-authorization:"))
            ]
            request_head = (
                f"{method} {path} {version}\r\n"
                + "\r\n".join(forwarded_headers)
                + "\r\n\r\n"
            ).encode("iso-8859-1", errors="replace")
            with upstream:
                upstream.sendall(request_head + body)
                self.request.settimeout(None)
                upstream.settimeout(None)
                self._relay(self.request, upstream)
        except (OSError, UnicodeError, ValueError):
            self._reject("400 Bad Request")


class PlaybackAdBlockProxy:
    """Lifecycle wrapper for PiStick's loopback-only playback proxy."""

    def __init__(self, settings: AdBlockSettings):
        self._server = _PlaybackProxyServer(settings)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="pistick-adblock-proxy",
            daemon=True,
        )
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)


def start_playback_adblock_proxy(
    settings: AdBlockSettings,
) -> Optional[PlaybackAdBlockProxy]:
    if not settings.enabled:
        return None
    return PlaybackAdBlockProxy(settings)


def add_webview_proxy_argument(existing: str, port: int) -> tuple[str, bool]:
    """Add PiStick's loopback proxy unless the user already supplied one."""
    value = str(existing or "").strip()
    proxy_markers = (
        "--no-proxy-server",
        "--proxy-auto-detect",
        "--proxy-pac-url",
        "--proxy-server",
    )
    if any(marker in value for marker in proxy_markers):
        return value, False
    argument = f"--proxy-server=http://127.0.0.1:{int(port)}"
    return f"{value} {argument}".strip(), True


def build_playback_adblock_script(settings: AdBlockSettings) -> str:
    """Build an idempotent in-page blocker for pop-ups and late ad requests."""
    blocked_hosts = json.dumps(sorted(settings.script_hosts), separators=(",", ":"))
    allowed_hosts = json.dumps(sorted(settings.allowed_hosts), separators=(",", ":"))
    selectors = json.dumps(list(_COSMETIC_SELECTORS), separators=(",", ":"))
    iframe_sandbox = json.dumps(_SAFE_IFRAME_SANDBOX)
    iframe_features = json.dumps(_SAFE_IFRAME_FEATURES)
    enabled = "true" if settings.enabled else "false"
    return f"""
(() => {{
    if (!{enabled} || window.__pistickAdBlockInstalled) return;
    window.__pistickAdBlockInstalled = true;

    const blockedHosts = new Set({blocked_hosts});
    const allowedHosts = new Set({allowed_hosts});
    const cosmeticSelectors = {selectors};
    const hostMatches = (host, rules) => {{
        const normalized = String(host || '').toLowerCase().replace(/\\.$/, '');
        if (!normalized) return false;
        let candidate = normalized;
        while (candidate) {{
            if (rules.has(candidate)) return true;
            const dot = candidate.indexOf('.');
            if (dot < 0) return false;
            candidate = candidate.slice(dot + 1);
        }}
        return false;
    }};
    const hostBlocked = (host) =>
        !hostMatches(host, allowedHosts) && hostMatches(host, blockedHosts);
    const urlBlocked = (value) => {{
        try {{
            return hostBlocked(new URL(String(value || ''), document.baseURI).hostname);
        }} catch (_error) {{
            return false;
        }}
    }};

    // Some embedded players register an ad timer in their own first-party
    // document and rotate the external loader hostname.  Stop only callbacks
    // that contain multiple advertising markers plus destructive storage or
    // script-injection behavior; ordinary player/HLS timers remain untouched.
    const isKnownAdScheduler = (handler) => {{
        let source = '';
        try {{
            source = typeof handler === 'function'
                ? Function.prototype.toString.call(handler)
                : String(handler || '');
        }} catch (_error) {{
            return false;
        }}
        const markers = [
            '_popads',
            'adsbygoogle',
            'googletag.pubads',
            'ferocitycandour'
        ];
        const markerCount = markers.reduce(
            (count, marker) => count + (source.includes(marker) ? 1 : 0),
            0
        );
        const injectsAds = source.includes('createElement')
            || source.includes('appendChild')
            || source.includes('document.cookie');
        const clearsStorage = source.includes('localStorage.clear')
            || source.includes('sessionStorage.clear');
        return markerCount >= 2 && (injectsAds || clearsStorage);
    }};
    const protectScheduler = (name) => {{
        const nativeScheduler = window[name];
        if (typeof nativeScheduler !== 'function') return;
        const protectedScheduler = function(handler, delay, ...args) {{
            if (isKnownAdScheduler(handler)) return 0;
            return nativeScheduler.call(window, handler, delay, ...args);
        }};
        try {{
            Object.defineProperty(window, name, {{
                configurable: false,
                writable: false,
                value: protectedScheduler
            }});
        }} catch (_error) {{
            window[name] = protectedScheduler;
        }}
    }};
    protectScheduler('setInterval');
    protectScheduler('setTimeout');

    // A harmless object makes common pop-under bootstrap code take its
    // already-initialized branch without loading or displaying an advert.
    try {{
        if (!('_popads' in window)) {{
            Object.defineProperty(window, '_popads', {{
                configurable: false,
                writable: false,
                value: Object.freeze({{ show: () => null }})
            }});
        }}
    }} catch (_error) {{}}

    try {{
        Object.defineProperty(window, 'open', {{
            configurable: false,
            writable: false,
            value: () => null
        }});
    }} catch (_error) {{
        window.open = () => null;
    }}

    if (typeof window.fetch === 'function') {{
        const nativeFetch = window.fetch.bind(window);
        window.fetch = (input, init) => {{
            const target = typeof input === 'string' ? input : input && input.url;
            if (urlBlocked(target)) return Promise.reject(new TypeError('Blocked by PiStick'));
            return nativeFetch(input, init);
        }};
    }}

    if (window.XMLHttpRequest && XMLHttpRequest.prototype.open) {{
        const nativeXhrOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
            const target = urlBlocked(url) ? 'data:,' : url;
            return nativeXhrOpen.call(this, method, target, ...rest);
        }};
    }}

    if (navigator.sendBeacon) {{
        const nativeBeacon = navigator.sendBeacon.bind(navigator);
        navigator.sendBeacon = (url, data) => urlBlocked(url) ? false : nativeBeacon(url, data);
    }}

    const elementUrl = (node) => {{
        if (!node || node.nodeType !== 1) return '';
        return node.src || node.href || node.getAttribute('data-src') || '';
    }};
    const blockInsertedNode = (node) => {{
        if (!urlBlocked(elementUrl(node))) return false;
        try {{ node.remove(); }} catch (_error) {{}}
        return true;
    }};

    // MutationObserver runs after insertion, which can be late enough for a
    // dynamic script request to leave the browser.  Block known-host nodes
    // synchronously at the DOM insertion point as a no-proxy fallback.
    if (window.Node && Node.prototype) {{
        const nativeAppendChild = Node.prototype.appendChild;
        if (typeof nativeAppendChild === 'function') {{
            Node.prototype.appendChild = function(node) {{
                if (blockInsertedNode(node)) return node;
                return nativeAppendChild.call(this, node);
            }};
        }}
        const nativeInsertBefore = Node.prototype.insertBefore;
        if (typeof nativeInsertBefore === 'function') {{
            Node.prototype.insertBefore = function(node, reference) {{
                if (blockInsertedNode(node)) return node;
                return nativeInsertBefore.call(this, node, reference);
            }};
        }}
        const nativeReplaceChild = Node.prototype.replaceChild;
        if (typeof nativeReplaceChild === 'function') {{
            Node.prototype.replaceChild = function(node, previous) {{
                if (blockInsertedNode(node)) return previous;
                return nativeReplaceChild.call(this, node, previous);
            }};
        }}
    }}
    const hideAd = (node) => {{
        if (!node || !node.style) return;
        node.style.setProperty('display', 'none', 'important');
        node.setAttribute('aria-hidden', 'true');
    }};
    const hardenFrame = (frame) => {{
        if (!frame || String(frame.tagName || '').toLowerCase() !== 'iframe') return frame;
        if (urlBlocked(elementUrl(frame))) {{
            frame.remove();
            return null;
        }}
        if (frame.dataset && frame.dataset.pistickSandboxed === '1') return frame;
        frame.setAttribute('sandbox', {iframe_sandbox});
        frame.setAttribute('allow', {iframe_features});
        frame.removeAttribute('allowfullscreen');
        frame.removeAttribute('allowpaymentrequest');
        if (frame.dataset) frame.dataset.pistickSandboxed = '1';

        // A sandbox added after a frame has loaded applies on its next
        // navigation. Reload an already-live frame once so the restrictions
        // are active immediately on Windows' top-frame-only WebView API while
        // preserving the iframe element and any listeners held by the wrapper.
        if (frame.contentWindow && document.readyState !== 'loading' && frame.parentNode) {{
            const source = frame.getAttribute('src');
            if (source && source !== 'about:blank') frame.setAttribute('src', source);
        }}
        return frame;
    }};
    const removeAds = (root) => {{
        if (!root || !root.querySelectorAll) return;
        if (root.matches && root.matches('iframe')) hardenFrame(root);
        for (const frame of root.querySelectorAll('iframe')) hardenFrame(frame);
        for (const selector of cosmeticSelectors) {{
            if (root.matches && root.matches(selector)) {{
                hideAd(root);
            }}
            for (const node of root.querySelectorAll(selector)) hideAd(node);
        }}
        for (const node of root.querySelectorAll('[src], [href], [data-src]')) {{
            if (urlBlocked(elementUrl(node))) node.remove();
        }}
    }};

    const stopUnsafeActivation = (event) => {{
        const target = event.target;
        const anchor = target && target.closest ? target.closest('a[href]') : null;
        if (!anchor) return;
        if (urlBlocked(anchor.href) || String(anchor.target || '').toLowerCase() === '_blank') {{
            event.preventDefault();
            event.stopImmediatePropagation();
        }}
    }};
    for (const eventName of ['pointerdown', 'mousedown', 'touchstart', 'auxclick', 'click']) {{
        document.addEventListener(eventName, stopUnsafeActivation, true);
    }}
    document.addEventListener('submit', (event) => {{
        const form = event.target;
        if (!form) return;
        if (urlBlocked(form.action) || String(form.target || '').toLowerCase() === '_blank') {{
            event.preventDefault();
            event.stopImmediatePropagation();
        }}
    }}, true);

    const observer = new MutationObserver((mutations) => {{
        for (const mutation of mutations) {{
            for (const node of mutation.addedNodes || []) {{
                if (urlBlocked(elementUrl(node))) node.remove();
                else removeAds(node);
            }}
        }}
    }});
    observer.observe(document, {{ childList: true, subtree: true }});

    const startFiltering = () => {{
        removeAds(document);
    }};
    window.addEventListener('pagehide', () => observer.disconnect(), {{ once: true }});
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', startFiltering, {{ once: true }});
    }} else {{
        startFiltering();
    }}
}})();
""".strip()
