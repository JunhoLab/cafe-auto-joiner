from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import openpyxl

STATUS_PENDING = "대기"
STATUS_SUCCESS = "완료"
STATUS_FAILED = "실패"
STATUS_CAPTCHA = "캡차필요"


@dataclass
class AccountRow:
    row_index: int
    naver_id: str
    naver_pw: str
    cafe_url: str
    nickname: str
    spare_nickname: str
    answers: List[str] = field(default_factory=list)
    status: str = STATUS_PENDING

    @property
    def effective_answers(self) -> List[str]:
        return [a for a in self.answers if a and a.strip()]


def load_excel(path: str) -> List[AccountRow]:
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    rows: List[AccountRow] = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        cells = list(row) + [None] * max(0, 11 - len(row))
        if not cells[0]:
            continue
        rows.append(AccountRow(
            row_index=row_idx,
            naver_id=str(cells[0] or "").strip(),
            naver_pw=str(cells[1] or "").strip(),
            cafe_url=str(cells[2] or "").strip(),
            nickname=str(cells[3] or "").strip(),
            spare_nickname=str(cells[4] or "").strip(),
            answers=[str(cells[i] or "").strip() for i in range(5, 10)],
            status=str(cells[10] or STATUS_PENDING).strip(),
        ))
    return rows


def update_status(path: str, row_index: int, status: str) -> None:
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    ws.cell(row=row_index, column=11, value=status)
    wb.save(path)
