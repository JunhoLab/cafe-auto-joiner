from __future__ import annotations

import logging
import os
import sys
from typing import List, Optional

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(CURRENT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from cafe_auto_joiner.config import BrowserConfig, CaptchaConfig, JoinAutomationConfig
from cafe_auto_joiner.excel_loader import (
    AccountRow,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCESS,
    STATUS_CAPTCHA,
    load_excel,
    update_status,
)
from cafe_auto_joiner.joiner import CafeJoinAutomation
from cafe_auto_joiner.logging_utils import configure_logging

# ──────────────────────────────────────────────
# 테이블 컬럼 정의
# ──────────────────────────────────────────────

COL_HEADERS = ["ID", "PW", "카페 URL", "닉네임", "여분닉네임",
               "답변1", "답변2", "답변3", "답변4", "답변5", "상태"]
COL_ID, COL_PW, COL_URL, COL_NICK, COL_SPARE = 0, 1, 2, 3, 4
COL_A1, COL_A2, COL_A3, COL_A4, COL_A5, COL_STATUS = 5, 6, 7, 8, 9, 10


# ──────────────────────────────────────────────
# Qt 로그 핸들러
# ──────────────────────────────────────────────

class QtLogHandler:
    def __init__(self, callback):
        self.callback = callback

    def write(self, message: str) -> None:
        text = message.strip()
        if text:
            self.callback(text)

    def flush(self) -> None:
        return


# ──────────────────────────────────────────────
# 배치 자동화 워커
# ──────────────────────────────────────────────

class BatchWorker(QObject):
    log_message = Signal(str)
    row_status_changed = Signal(int, str)   # (table_row_idx, status)
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        rows: List[AccountRow],
        table_row_indices: List[int],
        excel_path: str,
        captcha_api_key: str,
        headless: bool,
        ip_change_pause: bool,
    ):
        super().__init__()
        self.rows = rows
        self.table_row_indices = table_row_indices
        self.excel_path = excel_path
        self.captcha_api_key = captcha_api_key
        self.headless = headless
        self.ip_change_pause = ip_change_pause
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        logger = self._build_logger()
        self._log_runtime_diagnostics(logger)
        try:
            for i, (row, tbl_idx) in enumerate(zip(self.rows, self.table_row_indices)):
                if self._stop_requested:
                    logger.info("중단 요청됨 — 배치 실행 종료")
                    break

                logger.info("=" * 60)
                logger.info("[%d/%d] ID: %s | 카페: %s", i + 1, len(self.rows), row.naver_id, row.cafe_url)

                config = self._build_config(row)
                automation = CafeJoinAutomation(config=config, logger=logger)
                result = automation.run()

                if result.success:
                    status = STATUS_SUCCESS
                elif "캡차" in result.message or "captcha" in result.message.lower():
                    status = STATUS_CAPTCHA
                else:
                    status = STATUS_FAILED

                self.row_status_changed.emit(tbl_idx, status)
                try:
                    update_status(self.excel_path, row.row_index, status)
                except Exception as exc:
                    logger.warning("엑셀 상태 저장 실패: %s", exc)

                logger.info("결과: %s | %s", status, result.message)

                # 계정 간 IP 변경 안내 (마지막 계정 제외)
                if self.ip_change_pause and i < len(self.rows) - 1 and not self._stop_requested:
                    logger.info("다음 계정 실행 전 IP를 변경해주세요. GUI에서 [계속] 버튼을 누르세요.")
                    # 실제 일시정지는 GUI 레벨에서 처리하기 어려우므로 5초 대기
                    import time; time.sleep(5)

        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    @staticmethod
    def _log_runtime_diagnostics(logger: logging.Logger) -> None:
        logger.info("Python executable: %s", sys.executable)
        logger.info("Python version: %s", sys.version.split()[0])

    def _build_config(self, row: AccountRow) -> JoinAutomationConfig:
        return JoinAutomationConfig(
            community_url=row.cafe_url,
            nickname=row.nickname,
            answers=row.answers,
            naver_id=row.naver_id,
            naver_pw=row.naver_pw,
            spare_nickname=row.spare_nickname,
            captcha_api_key=self.captcha_api_key,
            browser=BrowserConfig(
                headless=self.headless,
                slow_mo_ms=100,
                timeout_ms=15000,
            ),
            captcha=CaptchaConfig(
                provider_name="2captcha",
                api_key=self.captcha_api_key or None,
                enabled=bool(self.captcha_api_key),
            ),
        )

    def _build_logger(self) -> logging.Logger:
        logger = configure_logging()
        logger.handlers = []
        logger.propagate = False

        handler = logging.StreamHandler(QtLogHandler(self.log_message.emit))
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return logger


# ──────────────────────────────────────────────
# 메인 윈도우
# ──────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cafe Auto Joiner — 네이버 카페 자동 가입")
        self.resize(1200, 800)

        self._excel_path: Optional[str] = None
        self._rows: List[AccountRow] = []
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[BatchWorker] = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_table(), stretch=3)
        root.addWidget(self._build_log_panel(), stretch=2)

    # ──────────────────────────────────────────────
    # UI 빌더
    # ──────────────────────────────────────────────

    def _build_toolbar(self) -> QGroupBox:
        group = QGroupBox("설정")
        layout = QHBoxLayout(group)
        layout.setSpacing(10)

        # 엑셀 파일
        self._file_label = QLabel("파일 선택 안 됨")
        self._file_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn_open = QPushButton("엑셀 파일 열기")
        btn_open.clicked.connect(self._open_excel)

        captcha_label = QLabel("2Captcha API 키:")
        self._captcha_key_input = QLineEdit()
        self._captcha_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._captcha_key_input.setPlaceholderText("2Captcha API 키")
        self._captcha_key_input.setFixedWidth(300)

        # 옵션
        self._headless_check = QCheckBox("Headless")
        self._ip_pause_check = QCheckBox("계정 간 IP변경 대기(5초)")
        self._ip_pause_check.setToolTip("계정이 바뀔 때 테더링 IP 변경을 위해 5초 일시정지")

        # 실행 버튼
        self._run_all_btn = QPushButton("전체 실행")
        self._run_all_btn.setFixedWidth(100)
        self._run_all_btn.clicked.connect(self._run_all)
        self._run_sel_btn = QPushButton("선택 실행")
        self._run_sel_btn.setFixedWidth(100)
        self._run_sel_btn.clicked.connect(self._run_selected)
        self._stop_btn = QPushButton("중지")
        self._stop_btn.setFixedWidth(80)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._request_stop)

        layout.addWidget(btn_open)
        layout.addWidget(self._file_label)
        layout.addWidget(captcha_label)
        layout.addWidget(self._captcha_key_input)
        layout.addWidget(self._headless_check)
        layout.addWidget(self._ip_pause_check)
        layout.addStretch(1)
        layout.addWidget(self._run_all_btn)
        layout.addWidget(self._run_sel_btn)
        layout.addWidget(self._stop_btn)
        return group

    def _build_table(self) -> QGroupBox:
        group = QGroupBox("계정 목록 (엑셀 A~K 컬럼: id / pw / cafe_url / nickname / spare_nickname / answer_1~5 / status)")
        layout = QVBoxLayout(group)

        self._table = QTableWidget(0, len(COL_HEADERS))
        self._table.setHorizontalHeaderLabels(COL_HEADERS)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(COL_URL, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(COL_A1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(COL_A2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(COL_A3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(COL_A4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(COL_A5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setAlternatingRowColors(True)

        layout.addWidget(self._table)
        return group

    def _build_log_panel(self) -> QGroupBox:
        group = QGroupBox("실행 로그")
        layout = QVBoxLayout(group)
        self._log_output = QPlainTextEdit()
        self._log_output.setReadOnly(True)
        self._log_output.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._log_output)
        return group

    # ──────────────────────────────────────────────
    # 엑셀 파일 열기
    # ──────────────────────────────────────────────

    def _open_excel(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "엑셀 파일 선택", "", "Excel Files (*.xlsx *.xls)"
        )
        if not path:
            return
        try:
            rows = load_excel(path)
        except Exception as exc:
            QMessageBox.critical(self, "파일 오류", f"엑셀 파일을 읽을 수 없습니다:\n{exc}")
            return

        self._excel_path = path
        self._rows = rows
        self._file_label.setText(os.path.basename(path))
        self._populate_table(rows)
        self._append_log(f"엑셀 로드 완료: {len(rows)}개 행 — {path}")

    def _populate_table(self, rows: List[AccountRow]) -> None:
        self._table.setRowCount(0)
        for row in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, COL_ID,    QTableWidgetItem(row.naver_id))
            self._table.setItem(r, COL_PW,    QTableWidgetItem("●" * min(len(row.naver_pw), 8)))
            self._table.setItem(r, COL_URL,   QTableWidgetItem(row.cafe_url))
            self._table.setItem(r, COL_NICK,  QTableWidgetItem(row.nickname))
            self._table.setItem(r, COL_SPARE, QTableWidgetItem(row.spare_nickname))
            for offset, answer in enumerate(row.answers[:5]):
                self._table.setItem(r, COL_A1 + offset, QTableWidgetItem(answer))
            status_item = QTableWidgetItem(row.status)
            self._color_status_item(status_item, row.status)
            self._table.setItem(r, COL_STATUS, status_item)

    # ──────────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────────

    def _run_all(self) -> None:
        if not self._rows:
            QMessageBox.information(self, "안내", "먼저 엑셀 파일을 불러오세요.")
            return
        self._start_batch(list(range(len(self._rows))), self._rows)

    def _run_selected(self) -> None:
        selected = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not selected:
            QMessageBox.information(self, "안내", "실행할 행을 선택해주세요.")
            return
        rows = [self._rows[i] for i in selected]
        self._start_batch(selected, rows)

    def _start_batch(self, table_indices: List[int], rows: List[AccountRow]) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            QMessageBox.information(self, "진행 중", "이미 실행 중입니다.")
            return
        if not self._excel_path:
            QMessageBox.critical(self, "오류", "엑셀 파일이 로드되지 않았습니다.")
            return
        captcha_api_key = self._captcha_key_input.text().strip()
        if not captcha_api_key:
            QMessageBox.information(self, "안내", "2Captcha API 키를 입력해주세요.")
            return

        self._append_log(f"배치 실행 시작: {len(rows)}개 계정")
        self._set_running(True)

        self._worker_thread = QThread()
        self._worker = BatchWorker(
            rows=rows,
            table_row_indices=table_indices,
            excel_path=self._excel_path,
            captcha_api_key=captcha_api_key,
            headless=self._headless_check.isChecked(),
            ip_change_pause=self._ip_pause_check.isChecked(),
        )
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.log_message.connect(self._append_log)
        self._worker.row_status_changed.connect(self._update_row_status)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._cleanup_worker)
        self._worker.failed.connect(self._cleanup_worker)
        self._worker_thread.start()

    def _request_stop(self) -> None:
        if self._worker:
            self._worker.stop()
            self._append_log("중단 요청됨 — 현재 계정 완료 후 종료됩니다.")

    # ──────────────────────────────────────────────
    # 콜백
    # ──────────────────────────────────────────────

    def _update_row_status(self, table_row: int, status: str) -> None:
        item = QTableWidgetItem(status)
        self._color_status_item(item, status)
        self._table.setItem(table_row, COL_STATUS, item)

    def _on_finished(self) -> None:
        self._append_log("배치 실행 완료.")
        self._set_running(False)

    def _on_failed(self, error: str) -> None:
        self._append_log(f"오류 발생: {error}")
        self._set_running(False)

    def _cleanup_worker(self, *_) -> None:
        if self._worker_thread:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
        self._worker = None

    def closeEvent(self, event) -> None:
        """창 닫기 / Ctrl+C 시 실행 중인 스레드를 먼저 정리."""
        if self._worker:
            self._worker.stop()
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(3000)
        event.accept()

    # ──────────────────────────────────────────────
    # 헬퍼
    # ──────────────────────────────────────────────

    def _append_log(self, message: str) -> None:
        self._log_output.appendPlainText(message)
        self._log_output.verticalScrollBar().setValue(
            self._log_output.verticalScrollBar().maximum()
        )

    def _set_running(self, running: bool) -> None:
        self._run_all_btn.setEnabled(not running)
        self._run_sel_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

    @staticmethod
    def _color_status_item(item: QTableWidgetItem, status: str) -> None:
        from PySide6.QtGui import QColor
        colors = {
            STATUS_SUCCESS: "#c8e6c9",
            STATUS_FAILED:  "#ffcdd2",
            STATUS_CAPTCHA: "#fff9c4",
            STATUS_PENDING: "#ffffff",
        }
        bg = colors.get(status, "#ffffff")
        item.setBackground(QColor(bg))


# ──────────────────────────────────────────────

def main() -> None:
    import signal
    app = QApplication(sys.argv)
    # Ctrl+C가 앱을 정상 종료하도록 (abort 방지)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
