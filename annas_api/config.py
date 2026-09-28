"""
Configuration management for Anna's Archive Ferry.
Decouples runtime persistent state from package source directories.
"""

import os
import json
from pathlib import Path

# User persistent runtime paths
USER_CONFIG_DIR = Path.home() / ".annas_ferry"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.json"
CACHE_DIR = Path.home() / ".annas_ferry_cache"

def ensure_user_dirs():
    """Lazily and safely ensures user config and cache directories exist."""
    try:
        USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

DEFAULT_CONFIG = {
    "primary_mirror": "https://zh.annas-archive.gl",
    "official_fallbacks": [
        "https://annas-archive.gl",
        "https://annas-archive.pk",
        "https://annas-archive.gd"
    ],
    "beacons": [
        "https://shadowlibraries.github.io/DirectDownloads/AnnasArchive/",
        "https://open-slum.pages.dev/"
    ],
    "heavy_threshold_mb": 30,
    "proxy": "auto",
    # External rotating proxy pool. When ``proxy_pool_url`` is set it takes
    # precedence over ``proxy``/``proxy_bypass_hosts`` for all outbound traffic
    # (browser and downloads). The URL carries provider credentials, so it is
    # supplied through FERRY_PROXY_POOL_URL rather than committed to config.
    "proxy_pool_url": "",
    "proxy_pool_scheme": "http",
    # Pool IPs typically fail the mirror's DDoS-Guard challenge, so browser
    # traffic stays direct unless this is explicitly enabled.
    "proxy_pool_browser": False,
    # Hosts that should be reached directly even when a general-purpose proxy
    # is configured. This keeps site-specific routing explicit and editable.
    "proxy_bypass_hosts": [
        "annas-archive.gl",
        "annas-archive.pk",
        "annas-archive.gd",
        "shadowlibraries.github.io",
        "open-slum.pages.dev"
    ],
    "default_download_dir": "~/Downloads/AnnasFerry",
    "auto_convert_djvu": True,
    "headless": True
}

def get_default_config():
    """Returns a copy of the default configuration."""
    return dict(DEFAULT_CONFIG)

def load_config(custom_path=None, expand_paths=True):
    """Loads configuration with fallback hierarchy:
    1. custom_path (if provided and exists)
    2. USER_CONFIG_FILE (~/.annas_ferry/config.json)
    3. DEFAULT_CONFIG
    """
    cfg = get_default_config()

    candidate_files = []
    if custom_path:
        candidate_files.append(Path(custom_path))
    candidate_files.append(USER_CONFIG_FILE)

    for f in candidate_files:
        if f.exists() and f.is_file():
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    if isinstance(data, dict):
                        cfg.update(data)
                        break
            except Exception:
                pass

    if expand_paths:
        raw_dir = cfg.get("default_download_dir", "~/Downloads/AnnasFerry")
        cfg["default_download_dir"] = str(Path(os.path.expandvars(os.path.expanduser(raw_dir))))

    # Secrets live in the environment (e.g. docker env_file), never in config files.
    pool_url = os.environ.get("FERRY_PROXY_POOL_URL")
    if pool_url:
        cfg["proxy_pool_url"] = pool_url.strip()
    return cfg

def save_dynamic_config(updates):
    """Safely updates dynamic configuration into ~/.annas_ferry/config.json."""
    ensure_user_dirs()
    # Load raw config without path expansion to preserve portable ~ paths
    current = load_config(expand_paths=False)
    current.update(updates)
    # Environment-backed secrets must never be written to disk.
    current.pop("proxy_pool_url", None)
    try:
        with open(USER_CONFIG_FILE, "w", encoding="utf-8") as fp:
            json.dump(current, fp, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[-] 写入用户配置失败: {e}", flush=True)
        return False
