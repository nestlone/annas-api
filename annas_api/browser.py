"""Checks for the Playwright browser runtime mounted by Docker Compose."""

import os
from pathlib import Path


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
    executable_patterns = (
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/headless_shell",
    )
    for pattern in executable_patterns:
        if any(path.is_file() and os.access(path, os.X_OK) for path in browser_root.glob(pattern)):
            return None

    return (
        "浏览器运行环境未就绪：缺少 chromium_headless_shell。"
        "请使用同版本的 Linux x86_64 annas-api 镜像执行 "
        "`python -m playwright install chromium`，并重新上传完整的 "
        "playwright-browsers 目录；不能只上传 chromium-<版本> 目录。"
    )
