"""Checks for the Playwright browser runtime mounted by Docker Compose."""

import os
from pathlib import Path


def _first_executable(browser_root, patterns):
    for pattern in patterns:
        for path in browser_root.glob(pattern):
            if path.is_file() and os.access(path, os.X_OK):
                return path
    return None


def fallback_chromium_executable():
    """Return the full Chromium executable when Playwright's shell is absent.

    A full ``chromium-<revision>`` upload can still run headlessly.  Playwright
    normally prefers its smaller headless-shell package, but explicitly passing
    the full executable avoids making a deployment re-download another 100+ MB
    artifact just for that preference.
    """

    browser_root = Path(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")
    ).expanduser()
    return _first_executable(
        browser_root,
        (
            "chromium-*/chrome-linux64/chrome",
            "chromium-*/chrome-linux/chrome",
        ),
    )


def browser_runtime_error():
    """Return an operator-safe error when headless Chromium is unavailable.

    Playwright 1.57+ launches ``chromium_headless_shell`` for a normal headless
    Chromium launch.  A directory containing only ``chromium-<revision>`` looks
    plausible, but fails later when a worker starts its first search.  Check the
    executable before work is accepted so a missing or incomplete upload cannot
    consume a user's quota or leave them with a long Playwright traceback.
    """

    browser_root = Path(
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "~/.cache/ms-playwright")
    ).expanduser()
    headless_shell = _first_executable(browser_root, (
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/headless_shell",
    ))
    if headless_shell or fallback_chromium_executable():
        return None

    return (
        "浏览器运行环境未就绪：缺少可执行 Chromium。"
        "请使用同版本的 Linux x86_64 annas-api 镜像执行 "
        "`python -m playwright install chromium`，并重新上传完整的 "
        "playwright-browsers 目录。"
    )
