"""Simulated provider tests: checks workflow contracts, not real model quality."""
import asyncio
import copy
import json

import httpx
import pytest

from backend.ai import AIService


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
    misconceptions = [{"id": mid, "topic_id": "topic"} for mid in ("m1", "m2", "m3")]
    evidence = {"sentences": [{"id": "s1", "document_id": "doc", "text": "栈后进先出，操作发生在栈顶。"}]}
    return question, topic, misconceptions, evidence


def invoke(service, material):
    _, topic, misconceptions, evidence = material
    return asyncio.run(service.generate_questions("doc", topic, misconceptions, "m1", evidence, []))


def reviewed(data, question, **overrides):
    right_text = next(o["text"] for o in question["options"] if o["is_correct"])
    answer = next(o["letter"] for o in data["options"] if o["text"] == right_text)
    return {"answer_letter": answer, "evidence_sufficient": True, "other_defensible": [], "issues": [], **overrides}


def test_blind_review_http_request_never_sends_answer_or_feedback(tmp_path, monkeypatch, material):
    monkeypatch.setenv("LLM_API_KEY", "test-only-key")
    question, *_ = material
    logs, reviews = [], []

    def handle(request):
        body = json.loads(request.content)
        data = json.loads(body["messages"][1]["content"])
        if body["temperature"] == 0:
            reviews.append(data)
            assert set(data) == {"stem", "options", "sentences"}
            assert all(set(option) == {"letter", "text"} for option in data["options"])
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
])
def test_failed_blind_review_repaired_at_most_once(tmp_path, material, review_problem):
    question, *_ = material
    service = AIService(cache_dir=tmp_path)
    calls = []

    async def chat(document, purpose, system, data, **kwargs):
        calls.append(purpose)
        if purpose == "blind_review":
            return reviewed(data, question, **review_problem)
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
            return reviewed(data, question)
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

    async def chat(*args, **kwargs):
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
