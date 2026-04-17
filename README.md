# Cafe Auto Joiner

Playwright 기반으로 네이버카페 가입 절차를 자동화하는 Python GUI 프로젝트다.

## 동작 방식

- 네이버카페 URL을 연다.
- 로그인 세션이 없으면 브라우저에서 직접 네이버 로그인을 완료한다.
- 로그인 완료가 감지되면 가입 버튼 클릭, 닉네임 입력, 가입 질문 자동 응답을 진행한다.
- CAPTCHA는 2Captcha API를 사용해 자동 처리한다.
- 가입 완료 여부를 확인하고 결과를 로그로 출력한다.

## 구조

```text
src/cafe_auto_joiner/
  browser.py        # 브라우저/컨텍스트 생성
  config.py         # 입력 스키마와 selector 설정
  exceptions.py     # 단계별 예외
  joiner.py         # 네이버카페 가입 오케스트레이션
  logging_utils.py  # 로깅 설정
  models.py         # 질문/결과 모델
gui_app.py          # PySide6 GUI 실행기
main.py             # GUI 엔트리포인트
run_example.py      # 코드 직접 실행 예제
```

## 설치

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 실행

```bash
python main.py
```

GUI에서 `2Captcha API 키`를 입력한 뒤 실행한다.

## Windows EXE 빌드

맥에서 작업하고 윈도우 `exe`만 받아가려면 GitHub Actions를 사용한다.

1. 저장소를 GitHub에 push
2. GitHub `Actions` 탭에서 `Build Windows EXE` 실행
3. 완료 후 artifact `CafeAutoJoiner-windows` 다운로드
4. `CafeAutoJoiner-windows.zip` 압축 해제
5. 압축을 푼 폴더 안의 `CafeAutoJoiner.exe` 실행

로컬 윈도우 PC에서 직접 빌드할 경우:

```bash
build_windows.bat
```

주의:

- Windows `exe`는 Windows에서 빌드해야 한다.
- `build_windows.bat`는 `.venv` 생성, 의존성 설치, `playwright install chromium`, `PyInstaller` 빌드를 한 번에 수행한다.
- 결과물은 `dist/CafeAutoJoiner/` 아래에 생성된다.

## 응답 데이터 예시

```python
question_answers = [
    {
        "question_type": "text",
        "question_keywords": ["자기소개", "가입 인사"],
        "answer_text": "안녕하세요. 활동 규칙을 준수하며 참여하겠습니다.",
    },
    {
        "question_type": "choice",
        "question_keywords": ["연령대", "나이대"],
        "option_label": "30대",
    },
]
```
