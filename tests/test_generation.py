"""Simulated provider tests: checks workflow contracts, not real model quality."""
import asyncio
import copy
import json

import httpx
import pytest

from backend.ai import AIService
from backend.ai.client import LLMClient, ModelError
from backend.ai.service import MisconceptionReview, Review


@pytest.fixture
def material():
    question = {
        "target_misconception_id": "m1", "stem": "编辑器的撤销历史存入栈，新操作记录应该按什么顺序取出？",
        "options": [
            {"text": "优先取出最近压入的记录", "is_correct": True, "feedback": "栈是后进先出。"},
            {"text": "按照最早录入的顺序处理", "is_correct": False, "feedback": "这混淆了队列。", "misconception_id": "m1"},
            {"text": "随机选择任意历史节点", "is_correct": False, "feedback": "栈只能在栈顶操作。", "misconception_id": "m2"},
            {"text": "必须清空全部内容后才能操作", "is_correct": False, "feedback": "删除一次只移除一个元素。", "misconception_id": "m3"},
        ],
        "evidence_sentence_ids": ["s1"], "explanation": "依据栈后进先出规则，最近的记录应先取出。",
    }
    topic = {"id": "topic", "title": "栈", "description": "操作顺序"}
    misconceptions = [{"id": mid, "topic_id": "topic", "source": AIService.misconception_review_source} for mid in ("m1", "m2", "m3")]
    evidence = {"sentences": [{"id": "s1", "document_id": "doc", "text": "栈后进先出，操作发生在栈顶。"}]}
    return question, topic, misconceptions, evidence


def invoke(service, material):
    _, topic, misconceptions, evidence = material
    return asyncio.run(service.generate_questions("doc", topic, misconceptions, "m1", evidence, []))


def reviewed(data, question, **overrides):
    right_text = next(o["text"] for o in question["options"] if o["is_correct"])
    answer = next(o["letter"] for o in data["options"] if o["text"] == right_text)
    by_text = {o["text"]: None if o["is_correct"] else o["misconception_id"] for o in question["options"]}
    mapping = {o["letter"]: by_text[o["text"]] for o in data["options"]}
    return {"answer_letter": answer, "evidence_sufficient": True, "other_defensible": [], "issues": [],
            "option_misconception_ids": mapping, **overrides}


def test_blind_review_http_request_never_sends_answer_or_feedback(tmp_path, monkeypatch, material):
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    question, *_ = material
    logs, reviews = [], []

    def handle(request):
        body = json.loads(request.content)
        data = json.loads(body["messages"][1]["content"])
        if body["temperature"] == 0:
            reviews.append(data)
            assert set(data) == {"stem", "options", "sentences", "misconceptions"}
            assert all(set(option) == {"letter", "text"} for option in data["options"])
            assert all(set(m) == {"id", "label", "description"} for m in data["misconceptions"])
            assert "target_misconception_id" not in json.dumps(data)
            output = reviewed(data, question)
        else:
            output = {"insufficient": False, "questions": [question]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 12}})

    service = AIService(logger=logs.append, cache_dir=tmp_path)
    service.client.transport = httpx.MockTransport(handle)
    published = invoke(service, material)
    assert len(published) == 1 and len(reviews) == 1
    assert published[0]["validation_status"] == "verified"
    assert [entry["purpose"] for entry in logs] == ["generate", "blind_review"]
    assert service.last_generation_stats["accepted"] == 1


@pytest.mark.parametrize("review_problem", [
    {"other_defensible": ["B"]}, {"evidence_sufficient": False}, {"issues": ["题干含糊"]}, {"answer_letter": "?"},
    {"option_misconception_ids": {"A": None, "B": "foreign", "C": "m2", "D": "m3"}},
])
def test_failed_blind_review_repaired_at_most_once(tmp_path, material, review_problem):
    question, *_ = material
    service = AIService(cache_dir=tmp_path)
    calls = []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        if purpose == "blind_review":
            return Review(**reviewed(data, question, **review_problem))
        return {"questions": [question]}

    service.client.chat = chat
    assert invoke(service, material) == []
    assert calls == ["generate", "blind_review", "repair", "blind_review"]
    assert service.last_generation_stats == {"requested": 1, "code_rejected": 0, "review_rejected": 2,
                                             "repair_attempted": 1, "accepted": 0}


def test_invalid_reference_can_be_repaired_then_blind_reviewed(tmp_path, material):
    question, *_ = material
    broken = copy.deepcopy(question)
    broken["evidence_sentence_ids"] = ["foreign_sentence"]
    service = AIService(cache_dir=tmp_path)
    calls = []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        if purpose == "blind_review":
            return Review(**reviewed(data, question))
        if purpose == "repair":
            assert data["repair_feedback"] and data["candidate"] == broken
            return {"questions": [question]}
        return {"questions": [broken]}

    service.client.chat = chat
    assert len(invoke(service, material)) == 1
    assert calls == ["generate", "repair", "blind_review"]
    assert service.last_generation_stats["code_rejected"] == 1


def test_failed_repair_never_publishes_or_calls_reviewer(tmp_path, material):
    question, *_ = material
    broken = copy.deepcopy(question)
    broken["options"][1]["is_correct"] = True
    service = AIService(cache_dir=tmp_path)
    calls = []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        return {"questions": [broken]}

    service.client.chat = chat
    assert invoke(service, material) == []
    assert calls == ["generate", "repair"]
    assert service.last_generation_stats["code_rejected"] == 2


def test_wrong_topic_or_foreign_evidence_never_calls_provider(tmp_path, material):
    question, topic, misconceptions, evidence = material
    service = AIService(cache_dir=tmp_path)

    async def never(*args, **kwargs):
        raise AssertionError("No model call should occur without trusted IDs")

    service.client.chat = never
    misconceptions[0]["topic_id"] = "other-topic"
    assert invoke(service, material) == []
    misconceptions[0]["topic_id"] = "topic"
    evidence["sentences"][0]["document_id"] = "other-document"
    assert invoke(service, material) == []


def test_retrieval_always_adds_required_correction_and_invalidates_cache(tmp_path):
    service = AIService(cache_dir=tmp_path)
    topic = {"id": "topic", "title": "检索主题", "description": "测试"}
    sentences = [{"id": f"s{i}", "document_id": "doc", "page_index": i + 1, "ord": i, "text": f"内容{i}"} for i in range(8)]
    chunks = [{"id": f"c{i}", "document_id": "doc", "page_index": i + 1, "first_sentence_ord": i,
               "last_sentence_ord": i, "text": f"内容{i}", "sentence_ids": [f"s{i}"]} for i in range(8)]
    batches = []

    async def embeddings(doc, texts):
        batches.append(list(texts))
        if len(texts) == 1 and texts[0].startswith("检索主题"):
            return [[1.0, 0.0]]
        return [[1.0, float(index)] for index in range(len(texts))]

    service.client.embeddings = embeddings

    async def scenario():
        initial = await service.build_evidence("doc", topic, chunks, sentences, required_ids=["s7"])
        assert initial["chunk_ids"] == ["c0", "c1", "c2", "c3", "c7"]
        assert "s7" in {s["id"] for s in initial["sentences"]}
        await service.build_evidence("doc", topic, chunks, sentences)
        assert len([batch for batch in batches if len(batch) == 8]) == 1
        service.client.embed_model = "a-new-model"
        await service.build_evidence("doc", topic, chunks, sentences)
        assert len([batch for batch in batches if len(batch) == 8]) == 2
        chunks[0]["text"] += "新版内容"
        await service.build_evidence("doc", topic, chunks, sentences)
        assert len([batch for batch in batches if len(batch) == 8]) == 3

    asyncio.run(scenario())
    assert len(list(tmp_path.glob("*.npz"))) == 3


def test_candidates_use_rule_headings_and_model_output_validation(tmp_path):
    service = AIService(cache_dir=tmp_path)

    async def chat(doc, purpose, system, data, **kwargs):
        assert purpose == "candidate_topics"
        assert "第一章 栈" in data["headings"]
        assert data["sections"][0]["page_index"] == 2
        assert "不可信数据" in system
        return {"topics": [{"title": " 栈 ", "description": "先后顺序"}, {"title": "栈", "description": "重复"},
                           {"title": None}, {"title": "队列", "description": "入队出队"}]}

    service.client.chat = chat
    result = asyncio.run(service.candidate_topics("doc", [{"page_index": 2, "text": "第一章 栈\n栈使用后进先出规则。"}]))
    assert result == [{"title": "栈", "description": "先后顺序"}, {"title": "队列", "description": "入队出队"}]


def test_misconception_ids_stable_and_foreign_refutations_dropped(tmp_path):
    service = AIService(cache_dir=tmp_path)
    topic = {"id": "topic", "title": "栈"}
    evidence = {"sentences": [{"id": "s1", "document_id": "doc", "text": "栈后进先出。"}]}
    raw = [{"label": label, "description": "待纠正认识", "refute_sentence_ids": ["s1"]} for label in ("先进先出", "任意位置出栈", "整栈删除")]
    raw.append({"label": "跨资料引用", "description": "错误引用", "refute_sentence_ids": ["foreign"]})

    async def chat(document, purpose, system, data, **kwargs):
        if purpose == "review_misconceptions":
            assert kwargs["review"] is True
            return MisconceptionReview(topic_supported=True, judgments=[{"id": m["id"], "refuted_by_evidence": True, "reason": "模拟证据核对"} for m in data["misconceptions"]])
        return {"misconceptions": raw}

    service.client.chat = chat
    first = asyncio.run(service.misconceptions("doc", topic, evidence))
    second = asyncio.run(service.misconceptions("doc", topic, evidence))
    assert len(first) == 3
    assert [m["id"] for m in first] == [m["id"] for m in second]
    assert len({m["id"] for m in first}) == 3
    assert all(m["topic_id"] == "topic" and m["refute_sentence_ids"] == ["s1"] for m in first)
    raw.pop(0)
    assert asyncio.run(service.misconceptions("doc", topic, evidence)) == []


@pytest.mark.parametrize("topic_supported,refutation_supported", [(False, True), (True, False)])
def test_irrelevant_topic_or_non_refuting_quotes_not_accepted(tmp_path, topic_supported, refutation_supported):
    service = AIService(cache_dir=tmp_path)
    raw = [{"label": f"误区{i}", "description": f"待验证的错误认识{i}", "refute_sentence_ids": ["s1"]} for i in range(3)]
    calls = []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        if purpose == "misconceptions":
            return {"misconceptions": raw}
        return MisconceptionReview(topic_supported=topic_supported, judgments=[
            {"id": m["id"], "refuted_by_evidence": refutation_supported, "reason": "模拟复核：原句不足以推翻该认识"}
            for m in data["misconceptions"]])

    service.client.chat = chat
    operation = service.misconceptions("doc", {"id": "topic", "title": "量子力学"},
                                       {"sentences": [{"id": "s1", "document_id": "doc", "text": "栈是后进先出。"}]})
    if not topic_supported:
        with pytest.raises(ModelError) as failure:
            asyncio.run(operation)
        assert failure.value.kind == "insufficient_topic_evidence"
    else:
        assert asyncio.run(operation) == []
    assert calls == ["misconceptions", "review_misconceptions"]
    assert service.last_misconception_stats["review_rejected"] == 3
    assert service.last_misconception_stats["accepted"] == 0


@pytest.mark.parametrize("bad_ids", ["missing", "duplicate", "foreign"])
def test_semantic_review_cannot_omit_duplicate_or_invent_misconceptions(tmp_path, bad_ids):
    service = AIService(cache_dir=tmp_path)

    async def chat(document, purpose, system, data, **kwargs):
        if purpose == "misconceptions":
            return {"misconceptions": [{"label": f"误区{i}", "description": "错误认识", "refute_sentence_ids": ["s1"]} for i in range(3)]}
        ids = [m["id"] for m in data["misconceptions"]]
        if bad_ids == "missing": ids.pop()
        elif bad_ids == "duplicate": ids[-1] = ids[0]
        else: ids[-1] = "not-provided"
        return MisconceptionReview(topic_supported=True, judgments=[{"id": mid, "refuted_by_evidence": True, "reason": "模拟复核"} for mid in ids])

    service.client.chat = chat
    with pytest.raises(ModelError, match="逐条覆盖"):
        asyncio.run(service.misconceptions("doc", {"id": "topic", "title": "栈"},
                                          {"sentences": [{"id": "s1", "document_id": "doc", "text": "栈是后进先出。"}]}))


def test_required_correction_must_exist_and_have_a_chunk(tmp_path):
    service = AIService(cache_dir=tmp_path)
    sentences = [{"id": "s1", "document_id": "doc", "page_index": 1, "ord": 0, "text": "栈后进先出。"},
                 {"id": "s2", "document_id": "doc", "page_index": 2, "ord": 1, "text": "只在栈顶操作。"}]
    chunks = [{"id": "c1", "document_id": "doc", "page_index": 1, "first_sentence_ord": 0,
               "last_sentence_ord": 0, "text": "栈后进先出。", "sentence_ids": ["s1"]}]

    async def embeddings(doc, texts):
        return [[1.0, 0.0] for _ in texts]

    service.client.embeddings = embeddings
    for required in (["foreign"], ["s2"]):
        with pytest.raises(ModelError) as error:
            asyncio.run(service.build_evidence("doc", {"id": "t", "title": "栈"}, chunks, sentences, required_ids=required))
        assert error.value.kind == "missing_refutation"


def test_review_structure_failure_is_logged_as_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    calls = []
    invalid = {"answer_letter": "A", "evidence_sufficient": "true", "other_defensible": [], "issues": []}
    client = LLMClient(logger=calls.append, transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": json.dumps(invalid)}}], "usage": {"prompt_tokens": 3}})))
    with pytest.raises(ModelError) as error:
        asyncio.run(client.chat("doc", "blind_review", "system", {}, review=True, output_schema=Review))
    assert error.value.kind == "invalid_schema"
    assert calls[0]["status"] == "failed" and calls[0]["error_type"] == "invalid_schema"
    assert calls[0]["input_tokens"] == 3


def test_cancelled_provider_call_has_interrupted_log(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    calls = []

    async def cancelled(request):
        raise asyncio.CancelledError()

    client = LLMClient(logger=calls.append, transport=httpx.MockTransport(cancelled))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.chat("doc", "test", "system", {}))
    assert len(calls) == 1 and calls[0]["status"] == "interrupted" and calls[0]["error_type"] == "cancelled"


def test_wrong_option_binding_is_repaired_then_independently_rechecked(tmp_path, material):
    question, *_ = material
    broken = copy.deepcopy(question)
    broken["options"][1]["misconception_id"], broken["options"][2]["misconception_id"] = "m2", "m1"
    service, calls = AIService(cache_dir=tmp_path), []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        if purpose == "blind_review":
            return Review(**reviewed(data, question))  # Independent mapping reflects the option text.
        if purpose == "repair":
            assert any("误区归属" in issue for issue in data["repair_feedback"])
            return {"questions": [question]}
        return {"questions": [broken]}

    service.client.chat = chat
    result = invoke(service, material)
    assert len(result) == 1 and result[0]["target_misconception_id"] == "m1"
    assert calls == ["generate", "blind_review", "repair", "blind_review"]
    assert service.last_generation_stats["review_rejected"] == 1


def test_repair_cannot_switch_the_target_misconception(tmp_path, material):
    question, *_ = material
    swapped = copy.deepcopy(question)
    swapped["target_misconception_id"] = "m2"
    service, calls = AIService(cache_dir=tmp_path), []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        if purpose == "blind_review":
            return Review(**reviewed(data, question, evidence_sufficient=False))
        return {"questions": [swapped if purpose == "repair" else question]}

    service.client.chat = chat
    assert invoke(service, material) == []
    assert calls == ["generate", "blind_review", "repair"]
    assert service.last_generation_stats["code_rejected"] == 1


def test_later_model_failure_does_not_count_undelivered_batch_as_accepted(tmp_path, material):
    question, *_ = material
    second = copy.deepcopy(question)
    second["stem"] = "货架模拟器只允许从容器顶端移出物件，删除操作遵循哪项规则？"
    service, reviews = AIService(cache_dir=tmp_path), []

    async def chat(document, purpose, system, data, **kwargs):
        if purpose == "generate":
            return {"questions": [question, second]}
        if purpose == "blind_review":
            reviews.append(data)
            if len(reviews) == 2:
                raise ModelError("timeout", "模拟超时")
            return Review(**reviewed(data, question))
        raise AssertionError("Network failure should not become a repair loop")

    service.client.chat = chat
    with pytest.raises(ModelError, match="模拟超时"):
        invoke(service, material)
    assert service.last_generation_stats["accepted"] == 0
    assert service.last_generation_stats["validated_unpublished"] == 1


def test_old_semantic_version_cannot_generate_without_recheck(tmp_path, material):
    _, _, misconceptions, _ = material
    misconceptions[0]["source"] = "model"
    service = AIService(cache_dir=tmp_path)

    async def never(*args, **kwargs):
        raise AssertionError("Old semantic claims must be reviewed before generating")

    service.client.chat = never
    assert invoke(service, material) == []
