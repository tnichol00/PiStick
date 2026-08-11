"""Lightweight, playback-only ad and pop-up filtering for PiStick.

The blocker deliberately avoids broad URL rules that could catch video, HLS,
subtitle, or playback API requests.  A small built-in host list can be extended
privately through ``adblock_domains`` in PiStick's local config file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit


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
)


@dataclass(frozen=True)
class AdBlockSettings:
    enabled: bool
    blocked_hosts: frozenset[str]


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


def load_adblock_settings(config_path: Path) -> AdBlockSettings:
    """Load safe ad-block preferences without exposing private config values."""
    payload: object = {}
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        pass
    if not isinstance(payload, dict):
        payload = {}

    enabled = _config_bool(payload.get("adblock_enabled"), True)
    hosts = set(DEFAULT_AD_HOST_SUFFIXES)
    hosts.update(_custom_hosts(payload.get("adblock_domains")))
    return AdBlockSettings(enabled=enabled, blocked_hosts=frozenset(hosts))


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
    if not host:
        return False
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in settings.blocked_hosts)


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


def build_playback_adblock_script(settings: AdBlockSettings) -> str:
    """Build an idempotent in-page blocker for pop-ups and late ad requests."""
    blocked_hosts = json.dumps(sorted(settings.blocked_hosts), separators=(",", ":"))
    selectors = json.dumps(list(_COSMETIC_SELECTORS), separators=(",", ":"))
    enabled = "true" if settings.enabled else "false"
    return f"""
(() => {{
    if (!{enabled} || window.__pistickAdBlockInstalled) return;
    window.__pistickAdBlockInstalled = true;

    const blockedHosts = new Set({blocked_hosts});
    const cosmeticSelectors = {selectors};
    const hostBlocked = (host) => {{
        const normalized = String(host || '').toLowerCase().replace(/\\.$/, '');
        if (!normalized) return false;
        for (const suffix of blockedHosts) {{
            if (normalized === suffix || normalized.endsWith('.' + suffix)) return true;
        }}
        return false;
    }};
    const urlBlocked = (value) => {{
        try {{
            return hostBlocked(new URL(String(value || ''), document.baseURI).hostname);
        }} catch (_error) {{
            return false;
        }}
    }};

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
    const hideAd = (node) => {{
        if (!node || !node.style) return;
        node.style.setProperty('display', 'none', 'important');
        node.setAttribute('aria-hidden', 'true');
    }};
    const removeAds = (root) => {{
        if (!root || !root.querySelectorAll) return;
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

    document.addEventListener('click', (event) => {{
        const target = event.target;
        const anchor = target && target.closest ? target.closest('a[href]') : null;
        if (!anchor) return;
        if (urlBlocked(anchor.href) || String(anchor.target || '').toLowerCase() === '_blank') {{
            event.preventDefault();
            event.stopImmediatePropagation();
        }}
    }}, true);
    document.addEventListener('submit', (event) => {{
        const form = event.target;
        if (!form) return;
        if (urlBlocked(form.action) || String(form.target || '').toLowerCase() === '_blank') {{
            event.preventDefault();
            event.stopImmediatePropagation();
        }}
    }}, true);

    const startFiltering = () => {{
        removeAds(document);
        if (!document.documentElement) return;
        const observer = new MutationObserver((mutations) => {{
            for (const mutation of mutations) {{
                for (const node of mutation.addedNodes || []) {{
                    if (urlBlocked(elementUrl(node))) node.remove();
                    else removeAds(node);
                }}
            }}
        }});
        observer.observe(document.documentElement, {{ childList: true, subtree: true }});
        window.addEventListener('pagehide', () => observer.disconnect(), {{ once: true }});
    }};
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', startFiltering, {{ once: true }});
    }} else {{
        startFiltering();
    }}
}})();
""".strip()
