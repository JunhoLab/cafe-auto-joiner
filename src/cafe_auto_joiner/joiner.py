from __future__ import annotations

import logging
import random
import time
from typing import Iterable, List, Optional, Union

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Frame, Locator, Page, TimeoutError as PlaywrightTimeoutError

from cafe_auto_joiner.browser import build_browser_session
from cafe_auto_joiner.config import JoinAutomationConfig, QUESTION_CONTAINER_SELECTORS
from cafe_auto_joiner.exceptions import (
    CaptchaResolutionError,
    ElementNotFoundError,
    PermanentCaptchaError,
    StepExecutionError,
)
from cafe_auto_joiner.models import JoinResult, QuestionAnswer, QuestionType

SearchRoot = Union[Page, Frame]


class CafeJoinAutomation:
    def __init__(self, config: JoinAutomationConfig, logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.completed_steps: List[str] = []
        # 가입 폼이 실제로 로드된 frame/page 루트 (None이면 전체 탐색)
        self._join_form_root: Optional[SearchRoot] = None
        # 실제 입력 요소가 포함된 가입 폼 컨테이너
        self._join_form_scope: Optional[Locator] = None
        self._join_form_scope_has_visible_controls = False

    def run(self) -> JoinResult:
        session = build_browser_session(self.config.browser)
        page = session.page

        try:
            self._open_community(page)
            self._ensure_logged_in(page)
            self._start_join_flow(page)
            self._fill_nickname(page)
            self._fill_questions(page)
            self._submit(page)
            success = self._verify_success(page)

            message = "가입 절차가 완료되었습니다." if success else "가입 완료 여부를 명확히 확인하지 못했습니다."
            return JoinResult(
                success=success,
                current_url=page.url,
                message=message,
                steps_completed=self.completed_steps.copy(),
            )
        except (PlaywrightTimeoutError, PlaywrightError, StepExecutionError, CaptchaResolutionError) as exc:
            if self._should_treat_as_success(page, exc):
                return JoinResult(
                    success=True,
                    current_url=page.url,
                    message="가입 절차가 완료되었습니다.",
                    steps_completed=self.completed_steps.copy(),
                )
            return JoinResult(
                success=False,
                current_url=page.url,
                message=str(exc),
                steps_completed=self.completed_steps.copy(),
            )
        finally:
            session.close()

    # ──────────────────────────────────────────────
    # Step 1: 카페 페이지 열기
    # ──────────────────────────────────────────────

    def _open_community(self, page: Page) -> None:
        self.logger.info("Step 1/6: 네이버 카페 페이지 열기")
        self._safe_goto(page, self.config.community_url)
        self.completed_steps.append("community_opened")

    # ──────────────────────────────────────────────
    # Step 2: 로그인 확인 / 자동 로그인
    # ──────────────────────────────────────────────

    def _ensure_logged_in(self, page: Page) -> None:
        self.logger.info("Step 2/6: 네이버 로그인 확인")
        if not self._requires_manual_login(page):
            self.logger.info("기존 로그인 세션 감지")
            self.completed_steps.append("login_confirmed")
            return

        if self.config.naver_id and self.config.naver_pw:
            self._auto_login(page)
        else:
            self.logger.info("ID/PW 미입력 — 브라우저에서 수동으로 로그인해주세요.")
            self._open_login_if_visible(page)
            self._wait_for_login(page, self.config.browser.login_wait_timeout_sec)

        self.completed_steps.append("login_confirmed")

    def _auto_login(self, page: Page) -> None:
        from urllib.parse import quote
        self.logger.info("자동 로그인 시도: %s", self.config.naver_id)

        # 로그인 성공 후 카페 URL로 바로 리다이렉트되도록 url 파라미터에 카페 주소 지정
        login_url = (
            "https://nid.naver.com/nidlogin.login?mode=form"
            f"&url={quote(self.config.community_url, safe='')}"
        )
        self._safe_goto(page, login_url)

        id_input = page.locator("#id").first
        pw_input = page.locator("#pw").first

        if not id_input.count():
            raise StepExecutionError("네이버 로그인 페이지 ID 필드를 찾을 수 없습니다.")

        # 네이버 로그인 폼은 keydown 이벤트로 버튼을 활성화하므로 press_sequentially 사용
        # (fill()은 input 이벤트만 발생 → 로그인 버튼이 활성화 안 됨)
        id_input.click()
        id_input.press_sequentially(self.config.naver_id, delay=80)
        time.sleep(random.uniform(0.4, 0.8))
        pw_input.click()
        pw_input.press_sequentially(self.config.naver_pw, delay=80)
        time.sleep(random.uniform(0.3, 0.6))

        login_btn = page.locator(".btn_login, button[type='submit']").first
        if not login_btn.count():
            raise StepExecutionError("로그인 버튼을 찾을 수 없습니다.")
        self.logger.info("로그인 버튼 클릭")
        login_btn.click()

        try:
            # nid.naver.com을 벗어날 때까지 대기 (폴링 대신 이벤트 기반)
            page.wait_for_url(
                lambda url: "nid.naver.com" not in url,
                timeout=15000,
            )
            self.logger.info("자동 로그인 성공, URL: %s", page.url)
            if self.config.community_url not in page.url:
                self._safe_goto(page, self.config.community_url)
            return
        except PlaywrightTimeoutError:
            pass

        # 15초 안에 리다이렉트 없으면 추가 인증 → 수동 대기
        self.logger.warning(
            "네이버 추가 인증이 필요합니다 (현재: %s). "
            "브라우저에서 직접 완료해주세요. 최대 %d초 대기.",
            page.url, self.config.browser.login_wait_timeout_sec,
        )
        self._wait_for_login(page, self.config.browser.login_wait_timeout_sec)
        if self.config.community_url not in page.url:
            self._safe_goto(page, self.config.community_url)

    def _wait_for_login(self, page: Page, timeout_sec: int) -> None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            self._post_action_wait(0.6, 1.0)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=2000)
            except PlaywrightTimeoutError:
                pass
            if not self._requires_manual_login(page):
                self.logger.info("로그인 완료 확인")
                return
        raise StepExecutionError("로그인 대기 시간이 초과되었습니다.")

    # ──────────────────────────────────────────────
    # Step 3: 가입하기 클릭
    # ──────────────────────────────────────────────

    def _dismiss_overlays(self, page: Page) -> None:
        """클릭을 방해하는 배너/팝업을 닫거나 숨긴다."""
        # 1) 닫기 버튼이 있으면 클릭
        close_selectors = [
            'button.btn_close', 'a.btn_close', '.ico_close',
            'button[aria-label="닫기"]', 'button[title="닫기"]',
            '.layer_close', '.popup_close', '.close_btn',
        ]
        for sel in close_selectors:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    el.evaluate("el => el.click()")
                    time.sleep(0.3)
            except PlaywrightError:
                pass

        # 2) link_more 배너(카페 등급 알림 등)가 join 버튼 위를 덮고 있으면 숨김
        try:
            page.evaluate("""
                document.querySelectorAll('a.link_more').forEach(el => {
                    let box = el.closest('.box-g, .wrap_notice, .layer_notify, div[class*="notice"]');
                    if (box) box.style.display = 'none';
                    else el.style.display = 'none';
                });
            """)
        except Exception:
            pass

    def _start_join_flow(self, page: Page) -> None:
        self.logger.info("Step 3/6: 가입하기 클릭")
        self._join_form_root = None
        self._join_form_scope = None
        self._join_form_scope_has_visible_controls = False

        # 로그인 직후 카페 페이지가 완전히 렌더링될 때까지 재시도 (최대 10초)
        join_button = None
        for attempt in range(5):
            try:
                join_button = self._find_first_visible(page, self.config.selectors_for("join_button"))
                break
            except ElementNotFoundError:
                self.logger.info("가입 버튼 탐색 중 (%d/5)...", attempt + 1)
                time.sleep(2)

        if join_button is None:
            raise StepExecutionError("가입 버튼을 찾을 수 없습니다. 이미 가입된 카페이거나 버튼 선택자를 확인하세요.")

        # 가입 버튼 위를 가리는 배너/팝업 제거 후 클릭
        self._dismiss_overlays(page)

        self.logger.info("가입 버튼 발견, 클릭")
        join_button.evaluate("el => el.click()")

        # URL은 변하지 않고 폼 내용만 바뀜 → 가입 폼 특정 요소가 나타날 때까지 대기
        JOIN_FORM_SELECTORS = [
            '.join_board',
            '.join_board .join_info_grid',
            '.join_qna_area',
            'input#cafeNicknameInput',
            'input#cafeNickName',
            'input[name="nickName"]',
            'input[name="nickname"]',
            'input[placeholder="별명"]',
            'input[placeholder*="닉네임"]',
            '#joinForm',
            '.cafe_join',
            '.join_wrap',
            'form[action*="join" i]',
            'form[action*="member" i]',
        ]
        best_candidate: Optional[tuple[SearchRoot, Locator, str, int]] = None
        for _ in range(15):  # 최대 15초 대기
            for root in self._search_roots(page):
                for sel in JOIN_FORM_SELECTORS:
                    try:
                        matches = root.locator(sel).all()
                        for match in matches[:5]:
                            scope = self._normalize_join_scope(match)
                            visible_count = self._count_visible_form_controls(scope)
                            if best_candidate is None or visible_count > best_candidate[3]:
                                best_candidate = (root, scope, sel, visible_count)
                            if visible_count > 0:
                                self._select_join_form_candidate(root, scope, sel, visible_count)
                                break
                    except PlaywrightError:
                        pass
                if self._join_form_root is not None:
                    break
            if self._join_form_root is not None:
                break
            time.sleep(1)

        if self._join_form_root is None and best_candidate is not None:
            root, scope, sel, visible_count = best_candidate
            self._select_join_form_candidate(root, scope, sel, visible_count)
            self.logger.warning("실제 입력 요소가 보이지 않는 가입 폼 후보만 감지되었습니다.")

        if self._join_form_root is None:
            self.logger.warning("가입 폼을 감지하지 못했습니다 — 계속 진행")
            self._post_action_wait(2.0, 3.0)
        else:
            self._log_form_structure()

        self._post_action_wait(0.5, 1.0)
        self.completed_steps.append("join_clicked")

    def _select_join_form_candidate(
        self, root: SearchRoot, scope: Locator, selector: str, visible_count: int
    ) -> None:
        root_name = getattr(root, "name", None) or getattr(root, "url", "page")
        self.logger.info(
            "가입 폼 확인: %s (frame: %s, visible controls: %d)",
            selector, root_name, visible_count,
        )
        self._join_form_root = root
        self._join_form_scope = scope
        self._join_form_scope_has_visible_controls = visible_count > 0

    @staticmethod
    def _normalize_join_scope(locator: Locator) -> Locator:
        try:
            tag = (locator.evaluate("el => el.tagName.toLowerCase()") or "").lower()
            if tag == "form":
                return locator
        except PlaywrightError:
            return locator

        try:
            return locator.locator(
                "xpath=ancestor-or-self::*[self::form or self::fieldset or self::section or self::div or self::li][1]"
            ).first
        except PlaywrightError:
            return locator

    @staticmethod
    def _count_visible_form_controls(scope: Locator) -> int:
        try:
            return scope.locator(
                'input:visible, textarea:visible, select:visible, button:visible'
            ).count()
        except PlaywrightError:
            return 0

    # ──────────────────────────────────────────────
    # Step 4: 닉네임 입력 (중복 시 여분 닉네임 사용)
    # ──────────────────────────────────────────────

    def _fill_nickname(self, page: Page) -> None:
        self.logger.info("Step 4/6: 닉네임 입력")
        try:
            nickname_input = self._find_first_visible_in_form(page, self.config.selectors_for("nickname"))
        except ElementNotFoundError:
            self.logger.info("닉네임 입력 필드 없음 — 닉네임 단계 건너뜀")
            return

        self._log_element_info("닉네임 필드 발견", nickname_input)
        self._human_fill(nickname_input, self.config.nickname)
        time.sleep(random.uniform(0.3, 0.6))

        # 중복 확인 버튼 클릭 (있을 경우)
        check_btn = self._find_in_form(page, self.config.selectors_for("nickname_duplicate_check"))
        if check_btn:
            check_btn.click()
            time.sleep(random.uniform(0.8, 1.4))

        # 중복 에러 감지
        if self._any_locator_exists_in_form(page, self.config.selectors_for("nickname_error")):
            if self.config.spare_nickname:
                self.logger.warning("닉네임 중복 감지 — 여분 닉네임 사용: %s", self.config.spare_nickname)
                nickname_input.fill("")
                self._human_fill(nickname_input, self.config.spare_nickname)
                if check_btn:
                    check_btn.click()
                    time.sleep(random.uniform(0.8, 1.4))
            else:
                self.logger.warning("닉네임 중복 감지, 여분 닉네임 없음 — 그대로 진행")

        self.completed_steps.append("nickname_filled")

    # ──────────────────────────────────────────────
    # Step 5: 가입 질문 답변
    # ──────────────────────────────────────────────

    def _fill_questions(self, page: Page) -> None:
        self.logger.info("Step 5/6: 가입 질문 처리")

        effective_answers = [a for a in self.config.answers if a and a.strip()]

        if effective_answers:
            answered_count = self._fill_questions_by_order(page, effective_answers)
            if answered_count < len(effective_answers):
                raise StepExecutionError(
                    f"가입 질문 답변 미완료: {answered_count}/{len(effective_answers)}개만 처리되었습니다."
                )
        elif self.config.question_answers:
            # 기존 JSON 기반 처리 (하위 호환)
            answered_count = self._fill_questions_by_json(page)
            if answered_count < len(self.config.question_answers):
                raise StepExecutionError(
                    f"가입 질문 답변 미완료: {answered_count}/{len(self.config.question_answers)}개만 처리되었습니다."
                )

        self.completed_steps.append("questions_answered")

    def _fill_questions_by_order(self, page: Page, answers: List[str]) -> int:
        """순서 기반 자동 처리: 가입 폼의 질문들을 감지하여 answers 순서대로 채움.

        답변 형식:
          - 일반 텍스트       → 주관식 입력
          - "button_N"       → 해당 컨테이너의 N번째 버튼/클릭 요소 선택 (1-based)
          - 텍스트 (button 없음) → 라디오·드롭다운·버튼 텍스트 매칭 순으로 시도
        """
        containers = self._find_all_question_containers(page)
        self.logger.info("질문 컨테이너 %d개 감지", len(containers))

        answer_idx = 0
        for container in containers:
            if answer_idx >= len(answers):
                break
            answer = answers[answer_idx]

            # button_N 형식 처리
            btn_index = self._parse_button_index(answer)
            if btn_index is not None:
                handled = self._try_click_button_by_index(container, btn_index)
            else:
                handled = (
                    self._try_fill_text(container, answer)
                    or self._try_select_radio(container, answer)
                    or self._try_select_dropdown(container, answer)
                    or self._try_click_button_option(container, answer)
                )

            if handled:
                self.logger.info("Q%d 처리 완료: %.30s", answer_idx + 1, answer)
                answer_idx += 1
                self._post_action_wait(0.3, 0.7)

        if answer_idx < len(answers):
            self.logger.warning(
                "일부 답변 미처리: %d/%d (컨테이너 감지 실패 가능)",
                answer_idx, len(answers),
            )
        else:
            self.logger.info("가입 질문 답변 완료: %d/%d", answer_idx, len(answers))

        return answer_idx

    def _fill_questions_by_json(self, page: Page) -> int:
        """JSON 기반 처리 (기존 방식, 하위 호환)."""
        answers = [QuestionAnswer.from_dict(item) for item in self.config.question_answers]
        answered_count = 0
        for answer in answers:
            if answer.question_type == QuestionType.TEXT:
                self._answer_text_question(page, answer)
                answered_count += 1
            elif answer.question_type == QuestionType.CHOICE:
                self._answer_choice_question(page, answer)
                answered_count += 1
        self.logger.info("가입 질문 답변 완료(JSON): %d/%d", answered_count, len(answers))
        return answered_count

    # ──────────────────────────────────────────────
    # Step 6: 제출 (캡차 처리 포함)
    # ──────────────────────────────────────────────

    def _submit(self, page: Page) -> None:
        self.logger.info("Step 6/6: 가입 신청 제출")

        solver = self._build_captcha_solver_once()

        # 포기하지 않음 — 가입 성공할 때까지 무제한 라운드
        # (사용자가 중지 요청하거나 외부에서 종료하기 전까지)
        ATTEMPTS_PER_CAPTCHA = 5
        refresh_round = 0

        while True:
            refresh_round += 1
            self.logger.info("──── 캡차 라운드 %d ────", refresh_round)

            for attempt in range(1, ATTEMPTS_PER_CAPTCHA + 1):
                if not self._is_captcha_present(page):
                    self.completed_steps.append("submitted")
                    return

                self.logger.info("  시도 %d/%d (같은 캡차 이미지)", attempt, ATTEMPTS_PER_CAPTCHA)
                filled = self._solve_and_fill_captcha(page, solver, attempt)
                if not filled:
                    time.sleep(1)
                    continue

                if not self._click_submit(page):
                    time.sleep(1)
                    continue
                self._post_action_wait()

                if not self._is_captcha_present(page):
                    self.completed_steps.append("submitted")
                    return

                self.logger.warning("  제출 후에도 캡차 남음 (오답/미제출)")

            self.logger.warning("같은 캡차 %d번 실패 → 새로고침 후 다음 라운드", ATTEMPTS_PER_CAPTCHA)
            self._refresh_captcha(page, refresh_round)
            time.sleep(2)

    def _click_submit(self, page: Page) -> bool:
        try:
            submit = self._find_first_visible_in_form(
                page, self.config.selectors_for("submit_application")
            )
        except ElementNotFoundError:
            raise StepExecutionError("제출 버튼을 찾을 수 없습니다.")
        self._log_element_info("제출 버튼", submit)
        try:
            submit.evaluate("el => el.click()")
            return True
        except PlaywrightError as exc:
            self.logger.warning("제출 클릭 실패: %s", exc)
            return False

    def _build_captcha_solver_once(self):
        """solver를 한 번만 생성 (매 시도마다 재생성하지 않음)."""
        from cafe_auto_joiner.captcha import build_captcha_solver, DummyCaptchaSolver
        self.logger.info("캡차 solver 초기화 (provider=%s)", self.config.captcha.provider_name)
        solver = build_captcha_solver(self.config.captcha)
        if isinstance(solver, DummyCaptchaSolver):
            self.logger.warning("2Captcha API 키가 없어 자동 캡차 해독을 사용할 수 없습니다.")
            solver = None
        else:
            self.logger.info("solver: %s", type(solver).__name__)
        return solver

    def _solve_and_fill_captcha(self, page: Page, solver, attempt: int) -> bool:
        """캡차 이미지를 읽어 입력 필드에 채운다. 성공 시 True."""
        if solver is None:
            self.logger.warning("사용 가능한 캡차 solver 없음 — 수동 입력 대기 60초")
            deadline = time.time() + 60
            while time.time() < deadline:
                time.sleep(2)
                if not self._is_captcha_present(page):
                    return True
            return False

        solver_name = type(solver).__name__
        self.logger.info("%s 캡차 이미지 분석 중...", solver_name)
        try:
            solution = solver.solve(page)
        except PermanentCaptchaError:
            raise
        except Exception as exc:
            self.logger.warning("%s 분석 실패: %s", solver_name, exc)
            return False

        if not solution:
            self.logger.warning("%s 결과 비어 있음", solver_name)
            return False
        if len(solution) < 4:
            self.logger.warning("%s 결과가 너무 짧음: %r", solver_name, solution)
            return False

        self.logger.info("%s 캡차 해독: %s", solver_name, solution)
        captcha_input = self._find_first_in_roots(page, self.config.selectors_for("captcha_input"))
        if not captcha_input:
            self.logger.warning("캡차 입력 필드를 찾지 못했습니다.")
            return False

        captcha_input.fill("")
        self._human_fill(captcha_input, solution)
        self.logger.info("캡차 입력 완료")
        return True

    def _refresh_captcha(self, page: Page, attempt: int) -> None:
        captcha_input = self._find_first_in_roots(page, self.config.selectors_for("captcha_input"))
        if captcha_input:
            try:
                captcha_input.fill("")
            except PlaywrightError:
                pass

        refresh_button = self._find_first_in_roots(page, self.config.selectors_for("captcha_refresh"))
        if refresh_button is None:
            self.logger.warning("캡차 새로고침 버튼을 찾지 못했습니다. 2초 후 다시 시도(%d)", attempt)
            time.sleep(2)
            return

        try:
            refresh_button.click()
            self.logger.info("캡차 새로고침 클릭(%d)", attempt)
        except PlaywrightError as exc:
            self.logger.warning("캡차 새로고침 실패(%d): %s", attempt, exc)
        time.sleep(2)

    # ──────────────────────────────────────────────
    # 성공 확인
    # ──────────────────────────────────────────────

    def _verify_success(self, page: Page) -> bool:
        indicators = self.config.selectors_for("success_indicators") + [
            f"text={text}" for text in self.config.extra_success_texts
        ]
        for selector in indicators:
            if self._locator_exists(page, selector):
                self.completed_steps.append("verified")
                self.logger.info("가입 성공 지시자 확인: %s", selector)
                return True
        self.logger.warning("가입 성공 지시자를 찾을 수 없음")
        return False

    def _should_treat_as_success(self, page: Page, exc: Exception) -> bool:
        message = str(exc)
        if "Frame was detached" not in message and "Target page, context or browser has been closed" not in message:
            return False

        self.logger.warning("제출 후 frame 전환 감지: page 기준 성공 여부를 재확인합니다.")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=3000)
        except PlaywrightError:
            pass

        # 성공 문구가 보이거나, 더 이상 가입 폼/캡차가 없으면 성공으로 간주한다.
        if self._verify_success(page):
            return True

        form_selectors = (
            self.config.selectors_for("submit_application")
            + self.config.selectors_for("captcha_input")
            + self.config.selectors_for("nickname")
        )
        try:
            if not self._any_locator_exists(page, form_selectors) and not self._is_captcha_present(page):
                self.logger.info("제출 후 가입 폼/캡차가 사라져 성공으로 간주합니다.")
                self.completed_steps.append("verified")
                return True
        except PlaywrightError:
            pass
        return False

    # ──────────────────────────────────────────────
    # 질문 컨테이너 탐지
    # ──────────────────────────────────────────────

    def _find_all_question_containers(self, page: Page) -> List[Locator]:
        for selector in QUESTION_CONTAINER_SELECTORS:
            for root in self._form_scopes(page):
                try:
                    items = root.locator(selector).all()
                    if len(items) >= 1:
                        self.logger.info("질문 컨테이너 선택자 사용: %s (%d개)", selector, len(items))
                        return items
                except PlaywrightError:
                    continue
        self.logger.warning("질문 컨테이너를 찾지 못했습니다. 폼 전체에서 입력 요소를 직접 탐색합니다.")
        return self._fallback_question_containers(page)

    def _fallback_question_containers(self, page: Page) -> List[Locator]:
        """컨테이너 감지 실패 시 입력 요소가 있는 div/li를 직접 수집."""
        results: List[Locator] = []
        seen_names: set = set()
        for root in self._form_scopes(page):
            try:
                inputs = root.locator(
                    'input[type="text"]:visible, textarea:visible, '
                    'input[type="radio"]:visible, input[type="checkbox"]:visible, select:visible'
                ).all()
                for inp in inputs:
                    name = inp.get_attribute("name") or ""
                    inp_id = inp.get_attribute("id") or ""
                    inp_type = inp.get_attribute("type") or "text"
                    self.logger.debug(
                        "fallback 입력 요소 발견: type=%s name=%s id=%s", inp_type, name, inp_id
                    )
                    # 라디오 같은 name은 한 번만
                    if inp_type == "radio" and name in seen_names:
                        continue
                    if name:
                        seen_names.add(name)
                    # 가장 가까운 조상 컨테이너
                    container = inp.locator(
                        "xpath=ancestor-or-self::*[self::div or self::li or self::fieldset][1]"
                    ).first
                    results.append(container)
            except PlaywrightError:
                continue
        return results

    # ──────────────────────────────────────────────
    # 질문 유형별 입력 처리
    # ──────────────────────────────────────────────

    def _try_fill_text(self, container: Locator, answer: str) -> bool:
        """주관식 텍스트 입력."""
        for selector in ['textarea:visible', 'input[type="text"]:visible', 'input:not([type]):visible']:
            try:
                el = container.locator(selector).first
                if el.count() and el.is_visible():
                    self._human_fill(el, answer)
                    return True
            except PlaywrightError:
                continue
        return False

    def _try_select_radio(self, container: Locator, answer: str) -> bool:
        """객관식 라디오 버튼 선택 (레이블 텍스트 매칭)."""
        try:
            radios = container.locator('input[type="radio"]').all()
            if not radios:
                return False

            # 라디오 레이블 텍스트 매칭
            for radio in radios:
                label = self._get_radio_label_text(container, radio)
                if label and answer.strip() in label:
                    radio.check(force=True)
                    return True

            # 부분 텍스트로 재시도
            clickable = container.get_by_text(answer, exact=False).first
            if clickable.count() and clickable.is_visible():
                clickable.click()
                return True
        except PlaywrightError:
            pass
        return False

    def _try_select_dropdown(self, container: Locator, answer: str) -> bool:
        """드롭다운(select) 옵션 선택."""
        try:
            sel = container.locator("select:visible").first
            if sel.count() and sel.is_visible():
                sel.select_option(label=answer)
                return True
        except PlaywrightError:
            # label 매칭 실패 시 value로 재시도
            try:
                sel = container.locator("select:visible").first
                if sel.count():
                    sel.select_option(value=answer)
                    return True
            except PlaywrightError:
                pass
        return False

    def _try_click_button_option(self, container: Locator, answer: str) -> bool:
        """버튼/링크 형태의 객관식 클릭 (텍스트 매칭)."""
        try:
            clickable = container.get_by_text(answer, exact=False).first
            if clickable.count() and clickable.is_visible():
                tag = clickable.evaluate("el => el.tagName.toLowerCase()")
                if tag in ("button", "a", "label", "li", "span", "div"):
                    clickable.click()
                    return True
        except PlaywrightError:
            pass
        return False

    def _try_click_button_by_index(self, container: Locator, index: int) -> bool:
        """button_N 형식: 컨테이너 내 클릭 가능한 요소의 N번째(1-based)를 클릭."""
        try:
            # 질문형 객관식은 input이 숨겨지고 label만 보이는 경우가 많다.
            radio_labels = container.locator(
                "label[for]:visible"
            ).all()
            if radio_labels:
                target_idx = index - 1
                if 0 <= target_idx < len(radio_labels):
                    label = radio_labels[target_idx]
                    target_for = label.get_attribute("for") or ""
                    if target_for:
                        related_input = container.locator(f"#{target_for}").first
                        try:
                            input_type = (related_input.get_attribute("type") or "").lower()
                            if input_type in ("radio", "checkbox"):
                                related_input.check(force=True)
                            else:
                                label.click()
                        except PlaywrightError:
                            label.click()
                    else:
                        label.click()
                    self.logger.info("button_%d 라벨 클릭 (총 %d개 중)", index, len(radio_labels))
                    return True

            # 일반 버튼/링크형 선택지 처리
            clickable = container.locator(
                "button:visible, a:visible, [role='button']:visible"
            ).all()
            if clickable:
                target_idx = index - 1
                if 0 <= target_idx < len(clickable):
                    clickable[target_idx].click()
                    self.logger.info("button_%d 클릭 (총 %d개 중)", index, len(clickable))
                    return True

            # 최후 fallback: 보이는 radio/checkbox input 자체 클릭
            inputs = container.locator(
                "input[type='radio']:visible, input[type='checkbox']:visible"
            ).all()
            # 1-based → 0-based
            target_idx = index - 1
            if 0 <= target_idx < len(inputs):
                inputs[target_idx].check(force=True)
                self.logger.info("button_%d 입력 선택 (총 %d개 중)", index, len(inputs))
                return True

            self.logger.warning(
                "button_%d 요청이 있으나 선택 가능한 라벨/버튼/input을 찾지 못했습니다.",
                index,
            )
        except PlaywrightError as exc:
            self.logger.warning("button_%d 클릭 실패: %s", index, exc)
        return False

    @staticmethod
    def _parse_button_index(answer: str) -> Optional[int]:
        """'button_N' 또는 '(button_N)' 형식이면 N(int)을 반환, 아니면 None.
        예: button_1 → 1, (button_3) → 3
        """
        import re
        normalized = answer.strip()
        m = re.fullmatch(r'\(?\s*button_(\d+)\s*\)?', normalized, re.IGNORECASE)
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _get_radio_label_text(container: Locator, radio: Locator) -> str:
        """라디오 버튼에 연결된 레이블 텍스트 반환."""
        try:
            radio_id = radio.get_attribute("id")
            if radio_id:
                label = container.locator(f'label[for="{radio_id}"]').first
                if label.count():
                    return label.inner_text().strip()
            # 인접 텍스트 노드 (label로 감싼 경우)
            parent_label = radio.locator("xpath=ancestor::label[1]").first
            if parent_label.count():
                return parent_label.inner_text().strip()
        except PlaywrightError:
            pass
        return ""

    # ──────────────────────────────────────────────
    # 기존 JSON 기반 질문 처리 메서드 (하위 호환)
    # ──────────────────────────────────────────────

    def _answer_text_question(self, page: Page, answer: QuestionAnswer) -> None:
        target = self._find_question_container(page, answer.question_keywords)
        if target is None:
            raise StepExecutionError(f"Text question not found for keywords: {answer.question_keywords}")
        input_box = self._find_input_in_container(target, ["textarea", 'input[type="text"]', "input"])
        if input_box is None:
            raise StepExecutionError(f"Text answer field not found for keywords: {answer.question_keywords}")
        self._human_fill(input_box, answer.answer_text or "")
        self.logger.info("주관식 답변 완료: %s", ", ".join(answer.question_keywords))

    def _answer_choice_question(self, page: Page, answer: QuestionAnswer) -> None:
        target = self._find_question_container(page, answer.question_keywords)
        if target is None:
            raise StepExecutionError(f"Choice question not found for keywords: {answer.question_keywords}")

        if answer.option_label:
            labeled = target.get_by_label(answer.option_label).first
            if labeled.count():
                labeled.check(force=True)
                self.logger.info("객관식 레이블 선택: %s", answer.option_label)
                return
            clickable = target.get_by_text(answer.option_label, exact=False).first
            if clickable.count():
                clickable.click()
                self.logger.info("객관식 텍스트 클릭: %s", answer.option_label)
                return

        if answer.option_value:
            radio = target.locator(f'input[type="radio"][value="{answer.option_value}"]').first
            if radio.count():
                radio.check(force=True)
                self.logger.info("객관식 value 선택: %s", answer.option_value)
                return

        raise StepExecutionError(f"Choice option not found for keywords: {answer.question_keywords}")

    def _find_question_container(self, page: Page, keywords: Iterable[str]) -> Optional[Locator]:
        for keyword in keywords:
            escaped = keyword.replace("'", "\\'")
            for selector in ["label", "legend", ".question", ".form-group", "dt", "th", "p", "div"]:
                locator = self._find_first_in_roots(page, [f"{selector}:has-text('{escaped}')"])
                if locator is not None:
                    return locator.locator(
                        "xpath=ancestor-or-self::*[self::div or self::li or self::tr or self::fieldset][1]"
                    ).first
        return None

    @staticmethod
    def _find_input_in_container(container: Locator, selectors: List[str]) -> Optional[Locator]:
        for selector in selectors:
            locator = container.locator(selector).first
            if locator.count():
                return locator
        return None

    # ──────────────────────────────────────────────
    # 공통 유틸리티
    # ──────────────────────────────────────────────

    def _find_first_visible(self, page: Page, selectors: Iterable[str]) -> Locator:
        for root in self._search_roots(page):
            for selector in selectors:
                locator = root.locator(selector).first
                try:
                    if locator.is_visible():
                        return locator
                except PlaywrightError:
                    continue
        raise ElementNotFoundError(f"Visible element not found for selectors: {list(selectors)}")

    def _find_first_visible_in_form(self, page: Page, selectors: Iterable[str]) -> Locator:
        """가입 폼 루트 우선으로 visible 요소 탐색."""
        sel_list = list(selectors)
        for root in self._form_scopes(page):
            for selector in sel_list:
                locator = root.locator(selector).first
                try:
                    if locator.is_visible():
                        return locator
                except PlaywrightError:
                    continue
        raise ElementNotFoundError(f"Visible element not found in form for selectors: {sel_list}")

    def _find_first_visible_with_form_fallback(self, page: Page, selectors: Iterable[str]) -> Locator:
        sel_list = list(selectors)
        try:
            return self._find_first_visible_in_form(page, sel_list)
        except ElementNotFoundError:
            self.logger.warning("폼 범위에서 visible 요소를 찾지 못했습니다. 전체 페이지로 재탐색합니다.")
            return self._find_first_visible(page, sel_list)

    def _find_first_in_roots(self, page: Page, selectors: List[str]) -> Optional[Locator]:
        for root in self._search_roots(page):
            for selector in selectors:
                locator = root.locator(selector).first
                try:
                    if locator.count():
                        return locator
                except PlaywrightError:
                    continue
        return None

    def _find_in_form(self, page: Page, selectors: List[str]) -> Optional[Locator]:
        """가입 폼 루트 우선으로 요소 탐색."""
        for root in self._form_scopes(page):
            for selector in selectors:
                locator = root.locator(selector).first
                try:
                    if locator.count():
                        return locator
                except PlaywrightError:
                    continue
        return None

    def _locator_exists(self, page: Page, selector: str) -> bool:
        for root in self._search_roots(page):
            locator = root.locator(selector).first
            try:
                if locator.count():
                    return True
            except PlaywrightError:
                continue
        return False

    def _any_locator_exists(self, page: Page, selectors: List[str]) -> bool:
        return any(self._locator_exists(page, s) for s in selectors)

    def _any_locator_exists_in_form(self, page: Page, selectors: List[str]) -> bool:
        for root in self._form_scopes(page):
            for selector in selectors:
                try:
                    if root.locator(selector).first.count():
                        return True
                except PlaywrightError:
                    continue
        return False

    def _log_element_info(self, label: str, locator: Locator) -> None:
        """디버그용: 찾은 요소의 tag/id/name/type을 로그로 출력."""
        try:
            tag = locator.evaluate("el => el.tagName.toLowerCase()")
            el_id = locator.get_attribute("id") or ""
            name = locator.get_attribute("name") or ""
            el_type = locator.get_attribute("type") or ""
            placeholder = locator.get_attribute("placeholder") or ""
            self.logger.info(
                "%s → <%s> id=%r name=%r type=%r placeholder=%r",
                label, tag, el_id, name, el_type, placeholder,
            )
        except PlaywrightError:
            self.logger.info("%s → (속성 읽기 실패)", label)

    def _log_form_structure(self) -> None:
        """가입 폼 루트의 입력 요소 목록을 진단 로그로 출력."""
        target = self._join_form_scope if self._join_form_scope_has_visible_controls else None
        if target is None:
            target = self._join_form_root
        if target is None:
            return
        try:
            inputs = target.locator(
                'input:visible, textarea:visible, select:visible, button:visible'
            ).all()
            self.logger.info("폼 루트 내 visible 요소 %d개:", len(inputs))
            for i, el in enumerate(inputs[:20]):  # 최대 20개만
                try:
                    tag = el.evaluate("e => e.tagName.toLowerCase()")
                    el_id = el.get_attribute("id") or ""
                    name = el.get_attribute("name") or ""
                    el_type = el.get_attribute("type") or ""
                    text = (el.inner_text() or "")[:30].strip()
                    self.logger.info(
                        "  [%d] <%s> id=%r name=%r type=%r text=%r",
                        i + 1, tag, el_id, name, el_type, text,
                    )
                except PlaywrightError:
                    pass
        except PlaywrightError:
            pass

    def _requires_manual_login(self, page: Page) -> bool:
        if "nid.naver.com" in page.url:
            return True
        for selector in self.config.selectors_for("login_indicators"):
            if self._locator_exists(page, selector):
                return True
        return False

    def _open_login_if_visible(self, page: Page) -> None:
        for selector in self.config.selectors_for("login_indicators"):
            locator = self._find_first_in_roots(page, [selector])
            if locator is None:
                continue
            try:
                locator.click()
                self._post_action_wait()
            except PlaywrightError:
                pass
            return

    @staticmethod
    def _search_roots(page: Page):
        yield page
        for frame in page.frames:
            if frame != page.main_frame:
                yield frame

    def _form_roots(self, page: Page):
        """가입 폼을 찾은 루트를 우선하고, 없으면 전체 탐색."""
        if self._join_form_root is not None:
            yield self._join_form_root
        else:
            yield from self._search_roots(page)

    def _form_scopes(self, page: Page):
        """가입 폼 컨테이너를 우선하고, 없으면 루트 전체를 사용."""
        yielded_scope = False
        if self._join_form_scope is not None and self._join_form_scope_has_visible_controls:
            yield self._join_form_scope
            yielded_scope = True
        if self._join_form_root is not None:
            yield self._join_form_root
        elif not yielded_scope:
            yield from self._search_roots(page)
        for root in self._search_roots(page):
            if self._join_form_root is not None and root == self._join_form_root:
                continue
            yield root

    @staticmethod
    def _human_fill(locator: Locator, value: str) -> None:
        # fill()로 DOM에 직접 설정 → Caps Lock / IME 상태 영향 없음
        # press_sequentially는 OS 키보드 상태(Caps Lock)에 영향을 받아 대소문자가 뒤바뀜
        locator.click()
        locator.fill(value)
        # 글자 수에 비례한 딜레이로 자연스러운 타이핑 흉내
        time.sleep(len(value) * random.uniform(0.05, 0.09))

    @staticmethod
    def _post_action_wait(min_seconds: float = 0.8, max_seconds: float = 1.6) -> None:
        time.sleep(random.uniform(min_seconds, max_seconds))

    @staticmethod
    def _safe_goto(page: Page, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded")
        try:
            # networkidle을 5초 안에 못 받아도 계속 진행 (네이버는 백그라운드 요청이 많아 무한 대기 가능)
            page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass

    @staticmethod
    def _is_captcha_present(page: Page) -> bool:
        selectors = [
            'img[alt*="captcha" i]',
            'img[src*="captcha" i]',
            'iframe[title*="captcha" i]',
            '[class*="captcha" i]',
            'text=자동입력방지',
            'text=보안문자',
        ]
        for root in [page, *[frame for frame in page.frames if frame != page.main_frame]]:
            for selector in selectors:
                try:
                    locator = root.locator(selector).first
                    if locator.count():
                        return True
                except PlaywrightError:
                    continue
        return False
