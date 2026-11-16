"""Retrieval, constrained generation and independent blind review."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .client import LLMClient, ModelError, PROMPT_VERSION
from .ingestion import parse_pdf
from .validate_question import validate_question

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_cache_locks: dict[str, asyncio.Lock] = {}


def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")


def _normalized(values) -> np.ndarray:
    array = np.asarray(values, dtype=np.float32)
    if array.ndim != 2 or not np.isfinite(array).all():
        raise ModelError("invalid_embedding", "向量维度或数值无效")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if np.any(norms < 1e-12):
        raise ModelError("invalid_embedding", "向量服务返回零向量")
    return array / norms


class MisconceptionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=15)
    description: str = Field(min_length=1, max_length=1000)
    refute_sentence_ids: list[str] = Field(min_length=1, max_length=8)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answer_letter: str
    evidence_sufficient: bool
    other_defensible: list[str]
    issues: list[str]
    option_misconception_ids: dict[str, str | None]


class MisconceptionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    id: str
    refuted_by_evidence: bool
    reason: str = Field(min_length=1, max_length=1200)


class MisconceptionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    topic_supported: bool
    judgments: list[MisconceptionJudgment] = Field(max_length=6)


class AIService:
    misconception_review_source = f"model_refutation_reviewed:{PROMPT_VERSION}"

    def __init__(self, logger=None, budget_reserver=None, cache_dir: str | Path = "data/vectors"):
        self.client = LLMClient(logger, budget_reserver)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_generation_stats = {}
        self.last_misconception_stats = {}

    async def candidate_topics(self, document_id: str, pages: list[dict]) -> list[dict]:
        headings = []
        for page in pages:
            for line in page["text"].splitlines():
                line = line.strip()
                if 2 <= len(line) <= 35 and not re.search(r"[。！？]", line):
                    headings.append(line)
        data = {"headings": headings[:60], "sections": [{"page_index": p["page_index"], "text": p["text"][:800]} for p in pages]}
        result = await self.client.chat(document_id, "candidate_topics", prompt("topics"), data)
        candidates = result.get("topics", [])
        if not isinstance(candidates, list):
            raise ModelError("invalid_topics", "模型未返回有效知识点，请手动确认知识点")
        topics, seen = [], set()
        for item in candidates[:6]:
            if not isinstance(item, dict):
                continue
            title, description = item.get("title"), item.get("description", "")
            if isinstance(title, str) and 1 <= len(title.strip()) <= 80 and isinstance(description, str) and title.strip() not in seen:
                seen.add(title.strip())
                topics.append({"title": title.strip(), "description": description[:1000]})
        return topics

    async def _chunk_vectors(self, document_id: str, chunks: list[dict]) -> np.ndarray:
        version = hashlib.sha256(json.dumps([(c["id"], c["text"]) for c in chunks], ensure_ascii=False).encode()).hexdigest()
        cache_key = hashlib.sha256(f"{document_id}|{self.client.embed_url}|{self.client.embed_model}|{version}".encode()).hexdigest()
        path = self.cache_dir / f"{cache_key}.npz"
        lock = _cache_locks.setdefault(str(path), asyncio.Lock())
        async with lock:
            if path.exists():
                try:
                    with np.load(path, allow_pickle=False) as cached:
                        matrix = cached["vectors"]
                        if (str(cached["model"]) == self.client.embed_model and str(cached["version"]) == version
                                and int(cached["dimension"]) == matrix.shape[1] and matrix.shape[0] == len(chunks)):
                            return _normalized(matrix)
                except (ValueError, KeyError, IndexError, OSError):
                    pass  # An incomplete/corrupt cache is rebuilt from the configured provider.
            vectors = []
            for offset in range(0, len(chunks), 32):
                vectors.extend(await self.client.embeddings(document_id, [c["text"] for c in chunks[offset:offset + 32]]))
            matrix = _normalized(vectors)
            temporary = path.with_name(f"{cache_key}.{uuid.uuid4().hex}.tmp.npz")
            np.savez_compressed(temporary, vectors=matrix, model=self.client.embed_model, version=version,
                                dimension=matrix.shape[1], document_id=document_id,
                                chunk_ids=np.asarray([c["id"] for c in chunks], dtype=str))
            os.replace(temporary, path)
            return matrix

    async def build_evidence(self, document_id: str, topic: dict, chunks: list[dict], sentences: list[dict],
                             required_ids: list[str] | None = None) -> dict:
        valid_sentences = {s["id"]: s for s in sentences if s.get("document_id", document_id) == document_id}
        chunks = [c for c in chunks if c.get("document_id", document_id) == document_id]
        if not chunks or not valid_sentences:
            return {"sentences": [], "chunk_ids": []}
        if not set(required_ids or []).issubset(valid_sentences):
            raise ModelError("missing_refutation", "纠正句不属于当前资料，不能生成针对该误区的题目")
        # Backend may store only first/last sentence order rather than redundant IDs/text.
        chunks = [dict(c) for c in chunks]
        for c in chunks:
            if "sentence_ids" not in c:
                c["sentence_ids"] = [s["id"] for s in valid_sentences.values()
                                     if c["first_sentence_ord"] <= s["ord"] <= c["last_sentence_ord"]]
            c.setdefault("text", "\n".join(valid_sentences[sid]["text"] for sid in c["sentence_ids"] if sid in valid_sentences))
        matrix = await self._chunk_vectors(document_id, chunks)
        query = topic["title"] + " " + topic.get("description", "")
        query_vector = _normalized(await self.client.embeddings(document_id, [query]))[0]
        if matrix.shape[1] != len(query_vector):
            raise ModelError("embedding_dimension", "向量维度不一致，请确认服务模型版本后重新导入")
        selected = list(np.argsort(-(matrix @ query_vector), kind="stable")[:4])
        keywords = [word for word in re.findall(r"[\w\u4e00-\u9fff]+", topic["title"]) if len(word) >= 2]
        # Literal topic phrases plus component bigrams keep Chinese keyword lookup dependency-free.
        for phrase in list(keywords):
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", phrase):
                keywords.extend(phrase[i:i + 2] for i in range(len(phrase) - 1))
        extra = [i for i, c in enumerate(chunks) if i not in selected and any(word in c["text"] for word in keywords)]
        selected.extend(extra[:2])
        for i, chunk in enumerate(chunks):
            if set(required_ids or []) & set(chunk["sentence_ids"]) and i not in selected:
                selected.append(i)
        ids = {sid for i in selected for sid in chunks[i]["sentence_ids"]}
        if not set(required_ids or []).issubset(ids):
            raise ModelError("missing_refutation", "纠正句没有对应检索片段，请重新建立资料索引")
        evidence = [s for s in valid_sentences.values() if s["id"] in ids]
        evidence.sort(key=lambda s: (s["page_index"], s.get("ord", 0)))
        return {"sentences": evidence, "chunk_ids": [chunks[i]["id"] for i in selected]}

    async def misconceptions(self, document_id: str, topic: dict, evidence: dict) -> list[dict]:
        self.last_misconception_stats = {"candidates": 0, "invalid": 0, "review_rejected": 0, "accepted": 0}
        sentences = evidence["sentences"]
        if not sentences:
            return []
        result = await self.client.chat(document_id, "misconceptions", prompt("misconceptions"),
                                        {"topic": topic, "sentences": [{"id": s["id"], "text": s["text"]} for s in sentences]})
        raw = result.get("misconceptions", [])
        if not isinstance(raw, list):
            raise ModelError("invalid_misconceptions", "模型误区结构不正确")
        valid_ids = {s["id"] for s in sentences if s.get("document_id", document_id) == document_id}
        items, labels = [], set()
        for item in raw[:6]:
            self.last_misconception_stats["candidates"] += 1
            try:
                candidate = MisconceptionCandidate.model_validate(item).model_dump()
            except ValidationError:
                self.last_misconception_stats["invalid"] += 1
                continue
            if not set(candidate["refute_sentence_ids"]).issubset(valid_ids) or candidate["label"] in labels:
                self.last_misconception_stats["invalid"] += 1
                continue
            labels.add(candidate["label"])
            digest = hashlib.sha256((topic["id"] + candidate["label"] + "|".join(candidate["refute_sentence_ids"])).encode()).hexdigest()[:16]
            candidate.update(id="m_" + digest, topic_id=topic["id"], source="model_evidence_anchored")
            items.append(candidate)
        if len(items) < 3:
            return []
        return await self.verify_misconceptions(document_id, topic, evidence, items, preserve_stats=True)

    async def verify_misconceptions(self, document_id: str, topic: dict, evidence: dict,
                                   items: list[dict], *, preserve_stats: bool = False) -> list[dict]:
        """Recheck stored claims without changing stable IDs or silently regenerating them."""
        if not preserve_stats:
            self.last_misconception_stats = {"candidates": len(items), "invalid": 0, "review_rejected": 0, "accepted": 0}
        sentences = evidence["sentences"]
        valid_ids = {s["id"] for s in sentences if s.get("document_id", document_id) == document_id}
        if not 3 <= len(items) <= 6 or len({m.get("id") for m in items}) != len(items):
            raise ModelError("invalid_misconceptions", "误区复核需要 3—6 条不同的有效误区")
        for item in items:
            try:
                parsed = MisconceptionCandidate.model_validate({field: item.get(field) for field in
                    ("label", "description", "refute_sentence_ids")})
            except ValidationError as exc:
                raise ModelError("invalid_misconceptions", "既有误区结构不完整，无法进行证据复核") from exc
            if (not isinstance(item.get("id"), str) or not item["id"] or item.get("topic_id") != topic["id"]
                    or not set(parsed.refute_sentence_ids).issubset(valid_ids)):
                raise ModelError("invalid_misconceptions", "误区或纠正句不属于当前知识点的证据包")
        # Existence of a quote is not semantic support. Independently check that the
        # retrieved material covers the topic and the specific cited sentences refute each claim.
        verdict = await self.client.chat(document_id, "review_misconceptions", prompt("review_misconceptions"),
                                        {"topic": {"title": topic["title"], "description": topic.get("description", "")},
                                         "misconceptions": items,
                                         "sentences": [{"id": s["id"], "text": s["text"]} for s in sentences if s["id"] in valid_ids]},
                                        review=True, output_schema=MisconceptionReview)
        if not verdict.topic_supported:
            self.last_misconception_stats["review_rejected"] = len(items)
            raise ModelError("insufficient_topic_evidence", "检索原文不能充分支持这个知识点，请调整知识点或补充资料")
        expected = {m["id"] for m in items}
        actual = [j.id for j in verdict.judgments]
        if set(actual) != expected or len(actual) != len(expected):
            raise ModelError("invalid_schema", "误区复核必须逐条覆盖所有候选误区且不得重复")
        supported = {j.id for j in verdict.judgments if j.refuted_by_evidence}
        self.last_misconception_stats["review_rejected"] = len(items) - len(supported)
        verified = [{**m, "source": self.misconception_review_source} for m in items if m["id"] in supported]
        if len(verified) < 3:
            return []
        self.last_misconception_stats["accepted"] = len(verified)
        return verified

    async def _review(self, document_id: str, question: dict, evidence: dict, misconceptions: list[dict]) -> tuple[bool, list[str]]:
        selected = set(question["evidence_sentence_ids"])
        data = {"stem": question["stem"], "options": [{"letter": o["letter"], "text": o["text"]} for o in question["options"]],
                "sentences": [{"id": s["id"], "text": s["text"]} for s in evidence["sentences"] if s["id"] in selected],
                "misconceptions": [{"id": m["id"], "label": m.get("label", ""), "description": m.get("description", "")} for m in misconceptions]}
        # Only the catalog is sent; no target ID or author-assigned option mapping is disclosed.
        try:
            review = await self.client.chat(document_id, "blind_review", prompt("review"), data, review=True, output_schema=Review)
        except ModelError as exc:
            if exc.kind in ("invalid_json", "invalid_schema"):
                return False, ["复核返回格式不合法"]
            raise
        errors = []
        if review.answer_letter != question["answer_letter"]:
            errors.append("盲答与预期答案不同")
        if not review.evidence_sufficient:
            errors.append("证据不足")
        if review.other_defensible:
            errors.append("存在其他可辩护选项：" + "、".join(review.other_defensible))
        expected_mapping = {o["letter"]: None if o["is_correct"] else o["misconception_id"] for o in question["options"]}
        if review.option_misconception_ids != expected_mapping:
            errors.append("独立复核对选项的误区归属与作者标注不一致，需修正选项或误区绑定")
        errors.extend(review.issues)
        return not errors, errors

    async def generate_questions(self, document_id: str, topic: dict, misconceptions: list[dict], target_id: str,
                                  evidence: dict, existing_stems: list[str], parent_id: str | None = None) -> list[dict]:
        stats = {"requested": 0, "code_rejected": 0, "review_rejected": 0, "repair_attempted": 0, "accepted": 0}
        self.last_generation_stats = stats
        allowed = {m["id"] for m in misconceptions if m.get("topic_id") == topic["id"]
                   and m.get("source") == self.misconception_review_source}
        valid_evidence = {s["id"] for s in evidence["sentences"] if s.get("document_id", document_id) == document_id}
        if len(allowed) < 3 or target_id not in allowed or not valid_evidence:
            return []
        misconceptions = [m for m in misconceptions if m["id"] in allowed]
        request = {"topic": topic, "misconceptions": misconceptions, "target_misconception_id": target_id,
                   "sentences": [{"id": s["id"], "text": s["text"]} for s in evidence["sentences"] if s["id"] in valid_evidence],
                   "existing_stems": existing_stems[-150:]}
        output = await self.client.chat(document_id, "generate", prompt("generate"), request)
        if output.get("insufficient") is True:
            return []
        candidates = output.get("questions", [])
        if not isinstance(candidates, list):
            raise ModelError("invalid_questions", "模型题目格式不正确")
        published, seen_stems = [], list(existing_stems)
        try:
            for candidate in candidates[:2]:
                stats["requested"] += 1
                for revision in range(2):
                    checked = validate_question(candidate, allowed, valid_evidence, seen_stems, target_id)
                    errors = checked["errors"]
                    question = checked["question"]
                    if errors:
                        stats["code_rejected"] += 1
                    if not errors:
                        accepted, errors = await self._review(document_id, question, evidence, misconceptions)
                        if accepted:
                            question.update({"id": "q_" + uuid.uuid4().hex, "document_id": document_id, "topic_id": topic["id"],
                                             "kind": "concept", "parent_id": parent_id, "validation_status": "verified",
                                             "review_note": "代码校验、独立盲答与误区绑定复核通过；不代表人工审定",
                                             "prompt_version": PROMPT_VERSION, "model": self.client.model,
                                             "review_model": self.client.review_model, "origin": "live" if parent_id else "bank",
                                             "source_note": "模型生成 · 代码校验及盲答复核通过"})
                            published.append(question)
                            seen_stems.append(question["stem"])
                            stats["accepted"] += 1
                            break
                        stats["review_rejected"] += 1
                    if revision == 0:
                        stats["repair_attempted"] += 1
                        repaired = await self.client.chat(document_id, "repair", prompt("generate"),
                                                          {**request, "candidate": candidate, "repair_feedback": errors,
                                                           "existing_stems": seen_stems[-150:]})
                        repaired_questions = repaired.get("questions", [])
                        if repaired.get("insufficient") is True or not isinstance(repaired_questions, list) or not repaired_questions:
                            break
                        candidate = repaired_questions[0]
        except (Exception, asyncio.CancelledError):
            # A failed batch never reaches persistence. Keep passed-but-undelivered gates
            # separate instead of counting them as accepted returned questions.
            stats["validated_unpublished"] = stats["accepted"]
            stats["accepted"] = 0
            raise
        return published


async def ingest_pdf(path, document_id, record_call=None, reserve_call=None, cache_dir="data/vectors"):
    service = AIService(record_call, reserve_call, cache_dir)
    data = await asyncio.to_thread(parse_pdf, path, document_id)
    data["candidate_topics"] = await service.candidate_topics(document_id, data["pages"])
    if data["chunks"]:
        await service._chunk_vectors(document_id, data["chunks"])
    return data


async def generate_topic(document_id, topic_id, title, description, sentences, chunks, existing_stems,
                         record_call=None, reserve_call=None, cache_dir="data/vectors", existing_misconceptions=None,
                         target_id=None, parent_id=None):
    service = AIService(record_call, reserve_call, cache_dir)
    topic = {"id": topic_id, "title": title, "description": description}
    misconceptions = existing_misconceptions or []
    needs_review = any(m.get("source") != service.misconception_review_source for m in misconceptions)
    required = [sid for m in misconceptions if needs_review or target_id is None or m["id"] == target_id for sid in m["refute_sentence_ids"]]
    evidence = await service.build_evidence(document_id, topic, chunks, sentences, required)
    if not misconceptions:
        misconceptions = await service.misconceptions(document_id, topic, evidence)
    if needs_review:
        verified = await service.verify_misconceptions(document_id, topic, evidence, misconceptions)
        if len(verified) != len(misconceptions):
            raise ModelError("insufficient_misconception_evidence", "既有误区未全部通过当前版本原文复核，请重新导入资料")
        misconceptions = verified
    questions, stats = [], []
    for misconception in misconceptions:
        if target_id is not None and misconception["id"] != target_id:
            continue
        batch = await service.generate_questions(document_id, topic, misconceptions, misconception["id"], evidence,
                                                 existing_stems + [q["stem"] for q in questions], parent_id)
        questions.extend(batch)
        stats.append(dict(service.last_generation_stats))
    return {"misconceptions": misconceptions, "questions": questions, "evidence_chunk_ids": evidence["chunk_ids"],
            "stats": stats, "reason": "" if questions else "证据不足，或生成题目未通过校验与盲答复核"}
