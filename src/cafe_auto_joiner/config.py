from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


DEFAULT_SELECTORS: Dict[str, List[str]] = {
    "login_indicators": [
        'a:has-text("로그인")',
        'button:has-text("로그인")',
        'text=로그인',
    ],
    "join_button": [
        'a:has-text("카페 가입하기")',
        'button:has-text("카페 가입하기")',
        'a:has-text("가입하기")',
        'button:has-text("가입하기")',
        'a:has-text("카페가입")',
        'button:has-text("카페가입")',
    ],
    "nickname": [
        'input#cafeNicknameInput',
        'input#cafeNickName',
        'input[name="nickName"]',
        'input[name="nickname"]',
        'input[name="nick"]',
        'input[placeholder="별명"]',
        'input[placeholder*="별명"]',
        'input[placeholder*="닉네임"]',
        'input[aria-label*="닉네임"]',
    ],
    "nickname_duplicate_check": [
        'button:has-text("중복확인")',
        'button:has-text("닉네임 중복확인")',
        'a:has-text("중복확인")',
    ],
    "nickname_error": [
        'text=이미 사용 중',
        'text=이미 사용중',
        'text=중복된 닉네임',
        'text=사용할 수 없는 닉네임',
        'text=이미 등록된 닉네임',
        '.error:has-text("닉네임")',
        '.caution:has-text("닉네임")',
    ],
    "submit_application": [
        # 네이버 카페 신형 UI 실제 버튼 (BaseButton + skinGreen)
        'a.BaseButton--skinGreen:has-text("가입하기")',
        'a.BaseButton:has-text("가입하기")',
        'a.BaseButton:has-text("동의")',
        'a[role="button"].BaseButton:has-text("가입")',
        'a[role="button"]:has-text("동의 후 가입")',
        # 폼 내부 범위
        '.join_board a.BaseButton',
        '.join_board button:has-text("가입")',
        '.join_board a:has-text("가입하기")',
        '.btn_area a.BaseButton',
        # 범용 fallback
        'button:has-text("가입신청")',
        'a:has-text("가입신청")',
        '[role="button"]:has-text("가입하기")',
        'button[type="submit"]',
        'input[type="submit"]',
    ],
    "success_indicators": [
        'text=가입 완료',
        'text=가입이 완료',
        'text=신청 완료',
        'text=가입 신청이 완료',
        'text=승인 대기',
        'text=가입되었습니다',
        'text=카페가입이 완료',
        'text=가입 승인 대기',
        'text=가입이 신청',
        'button:has-text("완료")',
        'a:has-text("완료")',
        '[role="button"]:has-text("완료")',
    ],
    "captcha_input": [
        'input[name="captchaCharCode"]',   # 네이버 카페 가입 캡차 실제 name
        'input[name="code"]',
        'input#captcha',
        'input[name="captcha"]',
        'input[name="captchaInput"]',
        'input[placeholder*="자동입력방지"]',
        'input[placeholder*="보안문자"]',
        'input[placeholder*="캡차"]',
        '.join_captcha_area ~ * input[type="text"]',
        '.captcha_area input[type="text"]',
        '[class*="captcha"] input[type="text"]',
    ],
    "captcha_refresh": [
        'button:has-text("새로고침")',
        'a:has-text("새로고침")',
        '[role="button"]:has-text("새로고침")',
        'button[title*="새로고침"]',
        'a[title*="새로고침"]',
        '.join_captcha_info button:has-text("새로고침")',
        '.join_captcha_info a:has-text("새로고침")',
        '[class*="captcha"] button:has-text("새로고침")',
        '[class*="captcha"] a:has-text("새로고침")',
    ],
}

# 질문 컨테이너 탐지에 사용하는 선택자 목록 (순서 중요: 구체적 → 범용)
QUESTION_CONTAINER_SELECTORS = [
    ".join_qna_area",
    ".join_board .join_info_grid",
    ".join-answer-area .answer_item",
    ".answer_list > li",
    ".join_info_list > li",
    ".joinForm .form_row",
    ".member_join .row",
    "fieldset",
    ".form-group",
    ".question_wrap",
    ".qa_row",
    ".cafe_join > ul > li",
]


@dataclass
class BrowserConfig:
    slow_mo_ms: int = 80
    timeout_ms: int = 15000
    user_agent: Optional[str] = None
    locale: str = "ko-KR"
    timezone_id: str = "Asia/Seoul"
    viewport_width: int = 1440
    viewport_height: int = 900
    user_data_dir: Optional[str] = None
    login_wait_timeout_sec: int = 180


@dataclass
class CaptchaConfig:
    provider_name: str = "2captcha"
    api_key: Optional[str] = None
    endpoint: Optional[str] = None
    enabled: bool = False


@dataclass
class JoinAutomationConfig:
    community_url: str
    nickname: str
    # 순서 기반 답변 (Excel answer_1~5)
    answers: List[str] = field(default_factory=list)
    # 기존 JSON 기반 답변 (하위 호환)
    question_answers: List[dict] = field(default_factory=list)
    # 네이버 로그인
    naver_id: str = ""
    naver_pw: str = ""
    # 닉네임 중복 시 여분 닉네임
    spare_nickname: str = ""
    # 레거시 캡차 API 키 필드 (현재 로컬 OCR 우선)
    captcha_api_key: str = ""
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    captcha: CaptchaConfig = field(default_factory=CaptchaConfig)
    selector_overrides: Dict[str, List[str]] = field(default_factory=dict)
    extra_success_texts: List[str] = field(default_factory=list)

    def selectors_for(self, key: str) -> List[str]:
        if key in self.selector_overrides:
            return self.selector_overrides[key]
        return DEFAULT_SELECTORS.get(key, [])
