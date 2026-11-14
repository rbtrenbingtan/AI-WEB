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


class AIService:
    def __init__(self, logger=None, budget_reserver=None, cache_dir: str | Path = "data/vectors"):
        self.client = LLMClient(logger, budget_reserver)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.last_generation_stats = {}

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
        evidence = [s for s in valid_sentences.values() if s["id"] in ids]
        evidence.sort(key=lambda s: (s["page_index"], s.get("ord", 0)))
        return {"sentences": evidence, "chunk_ids": [chunks[i]["id"] for i in selected]}

    async def misconceptions(self, document_id: str, topic: dict, evidence: dict) -> list[dict]:
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
            try:
                candidate = MisconceptionCandidate.model_validate(item).model_dump()
            except ValidationError:
                continue
            if not set(candidate["refute_sentence_ids"]).issubset(valid_ids) or candidate["label"] in labels:
                continue
            labels.add(candidate["label"])
            digest = hashlib.sha256((topic["id"] + candidate["label"] + "|".join(candidate["refute_sentence_ids"])).encode()).hexdigest()[:16]
            candidate.update(id="m_" + digest, topic_id=topic["id"], source="model_evidence_anchored")
            items.append(candidate)
        return items if len(items) >= 3 else []

    async def _review(self, document_id: str, question: dict, evidence: dict) -> tuple[bool, list[str]]:
        selected = set(question["evidence_sentence_ids"])
        data = {"stem": question["stem"], "options": [{"letter": o["letter"], "text": o["text"]} for o in question["options"]],
                "sentences": [{"id": s["id"], "text": s["text"]} for s in evidence["sentences"] if s["id"] in selected]}
        output = await self.client.chat(document_id, "blind_review", prompt("review"), data, review=True)
        try:
            review = Review.model_validate(output)
        except ValidationError:
            return False, ["复核返回格式不合法"]
        errors = []
        if review.answer_letter != question["answer_letter"]:
            errors.append("盲答与预期答案不同")
        if not review.evidence_sufficient:
            errors.append("证据不足")
        if review.other_defensible:
            errors.append("存在其他可辩护选项：" + "、".join(review.other_defensible))
        errors.extend(review.issues)
        return not errors, errors

    async def generate_questions(self, document_id: str, topic: dict, misconceptions: list[dict], target_id: str,
                                  evidence: dict, existing_stems: list[str], parent_id: str | None = None) -> list[dict]:
        stats = {"requested": 0, "code_rejected": 0, "review_rejected": 0, "repair_attempted": 0, "accepted": 0}
        self.last_generation_stats = stats
        allowed = {m["id"] for m in misconceptions if m.get("topic_id", topic["id"]) == topic["id"]}
        valid_evidence = {s["id"] for s in evidence["sentences"] if s.get("document_id", document_id) == document_id}
        if target_id not in allowed or not valid_evidence:
            return []
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
        for candidate in candidates[:2]:
            stats["requested"] += 1
            for revision in range(2):
                checked = validate_question(candidate, allowed, valid_evidence, seen_stems, target_id)
                errors = checked["errors"]
                question = checked["question"]
                if errors:
                    stats["code_rejected"] += 1
                else:
                    accepted, errors = await self._review(document_id, question, evidence)
                    if accepted:
                        question.update({"id": "q_" + uuid.uuid4().hex, "document_id": document_id, "topic_id": topic["id"],
                                         "kind": "concept", "parent_id": parent_id, "validation_status": "verified",
                                         "review_note": "代码校验与独立盲答复核通过；不代表人工审定",
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
    required = [sid for m in misconceptions if m["id"] == target_id for sid in m["refute_sentence_ids"]]
    evidence = await service.build_evidence(document_id, topic, chunks, sentences, required)
    if not misconceptions:
        misconceptions = await service.misconceptions(document_id, topic, evidence)
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
