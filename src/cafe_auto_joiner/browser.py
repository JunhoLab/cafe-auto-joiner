from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from cafe_auto_joiner.config import BrowserConfig


# 네이버가 자동화를 탐지하는 주요 지표를 숨기는 스크립트
_ANTI_DETECTION_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['ko-KR', 'ko', 'en-US', 'en'] });
"""

# Playwright 기본 UA는 "HeadlessChrome" 포함 → 실제 Chrome UA로 교체
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",  # webdriver 탐지 차단 핵심 플래그
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--disable-infobars",
    "--no-sandbox",
    "--disable-setuid-sandbox",
]


@dataclass
class BrowserSession:
    playwright: Playwright
    browser: Optional[Browser]
    context: BrowserContext
    page: Page

    def close(self) -> None:
        self.context.close()
        if self.browser is not None:
            self.browser.close()
        self.playwright.stop()


def _configure_playwright_browsers_path() -> None:
    if getattr(sys, "frozen", False):
        app_dir = os.path.dirname(sys.executable)
        bundled_browsers = os.path.join(app_dir, "ms-playwright")
        if os.path.isdir(bundled_browsers):
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = bundled_browsers


def build_browser_session(config: BrowserConfig) -> BrowserSession:
    _configure_playwright_browsers_path()
    playwright = sync_playwright().start()

    user_agent = config.user_agent or _DEFAULT_USER_AGENT
    common_kwargs = {
        "user_agent": user_agent,
        "locale": config.locale,
        "timezone_id": config.timezone_id,
        "viewport": {"width": config.viewport_width, "height": config.viewport_height},
    }

    browser: Optional[Browser] = None
    if config.user_data_dir:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=config.user_data_dir,
            headless=config.headless,
            slow_mo=config.slow_mo_ms,
            args=_LAUNCH_ARGS,
            **common_kwargs,
        )
        page = context.pages[0] if context.pages else context.new_page()
    else:
        browser = playwright.chromium.launch(
            headless=config.headless,
            slow_mo=config.slow_mo_ms,
            args=_LAUNCH_ARGS,
        )
        context = browser.new_context(**common_kwargs)
        page = context.new_page()

    # 모든 페이지에 적용 (새 페이지·팝업 포함)
    context.add_init_script(_ANTI_DETECTION_SCRIPT)
    context.set_default_timeout(config.timeout_ms)

    return BrowserSession(
        playwright=playwright,
        browser=browser,
        context=context,
        page=page,
    )
