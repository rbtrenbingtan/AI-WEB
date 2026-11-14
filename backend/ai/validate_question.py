"""Deterministic publishing gate. Never trusts model supplied letters or page numbers."""
from __future__ import annotations

import re
import secrets
import unicodedata
from difflib import SequenceMatcher
from itertools import combinations
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    text: str = Field(min_length=1, max_length=700)
    is_correct: bool
    feedback: str = Field(min_length=1, max_length=1500)
    misconception_id: str | None = None


class CandidateQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    target_misconception_id: str = Field(min_length=1)
    stem: str = Field(min_length=5, max_length=2000)
    options: list[Option] = Field(min_length=4, max_length=4)
    evidence_sentence_ids: list[str] = Field(min_length=1, max_length=12)
    explanation: str = Field(min_length=1, max_length=3000)


def normalize(text: str) -> str:
    # Mathematical operators carry meaning: M-1 and M+1 are distinct options.
    return re.sub(r"[^\w+*/%=<>-]+", "", unicodedata.normalize("NFKC", text).lower()).replace('_','')


def stem_similarity(first: str, second: str) -> float:
    def grams(text: str) -> set[str]:
        text = normalize(text)
        return {text[i:i + 3] for i in range(max(1, len(text) - 2))}
    a, b = grams(first), grams(second)
    return len(a & b) / max(1, len(a | b))


def validate_question(data: dict, misconception_ids: Iterable[str], evidence_ids: Iterable[str],
                      existing_stems: Iterable[str] = (), target_id: str | None = None) -> dict:
    """Return errors/flags and a shuffled question only when every rule passes."""
    errors, flags = [], []
    try:
        candidate = CandidateQuestion.model_validate(data)
    except ValidationError as exc:
        return {"errors": ["结构不合法：" + ".".join(map(str, e["loc"])) + " (" + e["type"] + ")"
                           for e in exc.errors(include_input=False)], "flags": [], "question": None}
    q = candidate.model_dump()
    mids, eids = set(misconception_ids), set(evidence_ids)
    if sum(o["is_correct"] for o in q["options"]) != 1:
        errors.append("必须恰好有一个正确选项")
    norm_options = [normalize(o["text"]) for o in q["options"]]
    if any(not x for x in norm_options) or len(set(norm_options)) != 4:
        errors.append("选项为空或归一化后重复")
    if any(SequenceMatcher(None, a, b).ratio() > 0.85 for a, b in combinations(norm_options, 2)):
        errors.append("选项文字过于相似")
    if any(re.search(r"以上[都均皆]|都不对|全部正确|全部错误|all\s+of\s+the\s+above|none\s+of\s+the\s+above",
                     o["text"], re.I) for o in q["options"]):
        errors.append("禁止概括式选项")
    wrong_ids = [o["misconception_id"] for o in q["options"] if not o["is_correct"]]
    if any(mid not in mids for mid in wrong_ids):
        errors.append("干扰项误区不属于当前知识点")
    if any(o["misconception_id"] is not None for o in q["options"] if o["is_correct"]):
        errors.append("正确选项不得标注误区")
    if q["target_misconception_id"] not in mids or q["target_misconception_id"] not in wrong_ids:
        errors.append("目标误区必须出现在当前知识点的干扰项中")
    if target_id is not None and q["target_misconception_id"] != target_id:
        errors.append("目标误区与本次生成任务不一致")
    if not set(q["evidence_sentence_ids"]).issubset(eids):
        errors.append("证据句不属于当前文档的证据包")
    if any(stem_similarity(q["stem"], previous) > 0.6 for previous in existing_stems):
        errors.append("题干与已有题过于相似")
    right = [o for o in q["options"] if o["is_correct"]]
    wrong = [o for o in q["options"] if not o["is_correct"]]
    if len(right) == 1 and wrong and len(right[0]["text"]) > 1.5 * sum(len(o["text"]) for o in wrong) / len(wrong):
        flags.append("correct_option_long")
    if errors:
        return {"errors": errors, "flags": flags, "question": None}
    secrets.SystemRandom().shuffle(q["options"])
    for letter, option in zip("ABCD", q["options"]):
        option["letter"] = letter
        if option["is_correct"]:
            q["answer_letter"] = letter
    q["evidence_sentence_ids"] = list(dict.fromkeys(q["evidence_sentence_ids"]))
    q["flags"] = flags
    return {"errors": [], "flags": flags, "question": q}
