from __future__ import annotations

import base64
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import requests
from playwright.sync_api import Locator, Page

from cafe_auto_joiner.config import CaptchaConfig
from cafe_auto_joiner.exceptions import CaptchaResolutionError, PermanentCaptchaError

logger = logging.getLogger(__name__)


class CaptchaSolver(ABC):
    @abstractmethod
    def solve(self, page: Page) -> Optional[str]:
        raise NotImplementedError


def _find_captcha_image(page: Page) -> Optional[Locator]:
    candidates = [
        ".join_captcha_area img",
        'img[alt="캡차이미지"]',
        'img[alt*="captcha" i]',
        'img[src*="captcha" i]',
        'img[src*="보안문자" i]',
        '[class*="captcha" i] img',
        ".captcha_area img",
        "#captchaImg",
    ]
    for selector in candidates:
        for root in [page, *[f for f in page.frames if f != page.main_frame]]:
            try:
                locator = root.locator(selector).first
                if locator.count():
                    return locator
            except Exception:
                continue
    return None


class DummyCaptchaSolver(CaptchaSolver):
    def solve(self, page: Page) -> Optional[str]:
        return None


class TwoCaptchaSolver(CaptchaSolver):
    """2captcha.com API 이미지 캡차 해결."""

    SUBMIT_URL = "http://2captcha.com/in.php"
    RESULT_URL = "http://2captcha.com/res.php"
    POLL_INTERVAL = 3
    MAX_POLLS = 20
    PERMANENT_ERRORS = {
        "ERROR_ZERO_BALANCE": "2Captcha 잔액이 0입니다. 충전 후 다시 시도하세요.",
        "ERROR_KEY_DOES_NOT_EXIST": "2Captcha API 키가 올바르지 않습니다.",
        "ERROR_WRONG_USER_KEY": "2Captcha API 키 형식이 올바르지 않습니다.",
        "ERROR_ACCOUNT_SUSPENDED": "2Captcha 계정이 정지되었습니다.",
        "ERROR_IP_NOT_ALLOWED": "현재 IP가 2Captcha 허용 목록에 없습니다.",
    }

    def __init__(self, api_key: str):
        self.api_key = api_key

    def solve(self, page: Page) -> Optional[str]:
        captcha_img = _find_captcha_image(page)
        if captcha_img is None:
            return None

        img_bytes = captcha_img.screenshot()
        encoded = base64.b64encode(img_bytes).decode("ascii")

        resp = requests.post(
            self.SUBMIT_URL,
            data={"key": self.api_key, "method": "base64", "body": encoded},
            timeout=30,
        )
        resp.raise_for_status()
        if not resp.text.startswith("OK|"):
            self._raise_if_permanent(resp.text)
            raise CaptchaResolutionError(f"2captcha 제출 실패: {resp.text}")

        captcha_id = resp.text.split("|", 1)[1]
        for _ in range(self.MAX_POLLS):
            time.sleep(self.POLL_INTERVAL)
            result = requests.get(
                self.RESULT_URL,
                params={"key": self.api_key, "action": "get", "id": captcha_id},
                timeout=15,
            )
            result.raise_for_status()
            if result.text.startswith("OK|"):
                return result.text.split("|", 1)[1]
            self._raise_if_permanent(result.text)
            if result.text != "CAPCHA_NOT_READY":
                raise CaptchaResolutionError(f"2captcha 오류: {result.text}")

        raise CaptchaResolutionError("2captcha 타임아웃")

    def _raise_if_permanent(self, response_text: str) -> None:
        if response_text in self.PERMANENT_ERRORS:
            raise PermanentCaptchaError(self.PERMANENT_ERRORS[response_text])


def build_captcha_solver(config: CaptchaConfig) -> CaptchaSolver:
    if config.provider_name == "2captcha" and config.api_key:
        return TwoCaptchaSolver(config.api_key)
    return DummyCaptchaSolver()
