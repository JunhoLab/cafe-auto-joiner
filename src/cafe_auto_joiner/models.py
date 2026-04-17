from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class QuestionType(str, Enum):
    TEXT = "text"
    CHOICE = "choice"


@dataclass
class QuestionAnswer:
    question_type: QuestionType
    question_keywords: List[str]
    answer_text: Optional[str] = None
    option_label: Optional[str] = None
    option_value: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: dict) -> "QuestionAnswer":
        return cls(
            question_type=QuestionType(raw["question_type"]),
            question_keywords=list(raw.get("question_keywords", [])),
            answer_text=raw.get("answer_text"),
            option_label=raw.get("option_label"),
            option_value=raw.get("option_value"),
        )


@dataclass
class JoinResult:
    success: bool
    current_url: str
    message: str
    steps_completed: List[str] = field(default_factory=list)

