from __future__ import annotations

import json
import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(CURRENT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from cafe_auto_joiner.config import BrowserConfig, CaptchaConfig, JoinAutomationConfig
from cafe_auto_joiner.joiner import CafeJoinAutomation
from cafe_auto_joiner.logging_utils import configure_logging


def main() -> None:
    logger = configure_logging()

    config = JoinAutomationConfig(
        community_url="https://cafe.naver.com/examplecafe",
        nickname="가입닉네임",
        question_answers=[
            {
                "question_type": "text",
                "question_keywords": ["가입 인사", "자기소개"],
                "answer_text": "안녕하세요. 카페 규칙을 준수하며 활동하겠습니다.",
            },
            {
                "question_type": "choice",
                "question_keywords": ["연령대", "나이대"],
                "option_label": "30대",
            },
        ],
        browser=BrowserConfig(
            headless=False,
            slow_mo_ms=100,
            timeout_ms=15000,
            user_data_dir="./.playwright-profile",
            login_wait_timeout_sec=180,
        ),
        captcha=CaptchaConfig(
            provider_name="2captcha",
            api_key="YOUR_2CAPTCHA_API_KEY",
            enabled=True,
        ),
        selector_overrides={
            # "join_button": ['a:has-text("카페 가입하기")'],
            # "nickname": ['input#cafeNickName'],
        },
        extra_success_texts=["가입 신청 완료"],
    )

    automation = CafeJoinAutomation(config=config, logger=logger)
    result = automation.run()

    print(
        json.dumps(
            {
                "success": result.success,
                "current_url": result.current_url,
                "message": result.message,
                "steps_completed": result.steps_completed,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
