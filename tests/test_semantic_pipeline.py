"""Complete AIService orchestration with an explicitly simulated HTTP supplier.

The real adapters, retrieval, ID validation, semantic gates and blind review execute;
this is engineering verification, never evidence of actual LLM teaching quality.
"""
import asyncio
import json

import httpx

from backend.ai import AIService, split_pages


def test_full_ai_pipeline_without_replacing_service_methods(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "simulated-provider-only")
    records, reservations = [], []
    parsed = split_pages([{"page_index": 1, "text": "栈采用后进先出规则。\n栈只允许从栈顶删除元素。\n一次 pop 只删除并返回栈顶的一个元素。"}], "doc")
    sentence_ids = [s["id"] for s in parsed["sentences"]]
    stems = ["编辑器需要撤销刚刚完成的操作，使用栈存储历史时应如何处理？",
             "货架模拟器仅允许弹出栈顶货箱，弹出操作遵循什么规则？",
             "程序调用 pop 返回一个记录，此时数据结构会发生什么变化？"]
    generated, reviewed = [], []

    def reserve(document_id):
        reservations.append(document_id)
        return f"simulated-call-{len(reservations)}"

    def handler(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.8, 0.2]} for i in range(len(body["input"]))],
                                            "usage": {"prompt_tokens": len(body["input"])}})
        system = body["messages"][0]["content"]
        data = json.loads(body["messages"][1]["content"])
        if "课程知识点整理器" in system:
            result = {"topics": [{"title": "栈", "description": "栈顶删除与后进先出"}]}
        elif "课程误区整理器" in system:
            result = {"misconceptions": [
                {"label": "栈当队列", "description": "误认为栈应先删除最早放入的元素", "refute_sentence_ids": [sentence_ids[0]]},
                {"label": "任意位置删除", "description": "误认为可以跳过栈顶删除中间元素", "refute_sentence_ids": [sentence_ids[1]]},
                {"label": "一次弹空", "description": "误认为一次 pop 要删除全部元素", "refute_sentence_ids": [sentence_ids[2]]},
            ]}
        elif "独立证据审核员" in system:
            assert body["temperature"] == 0
            result = {"topic_supported": True, "judgments": [{"id": m["id"], "refuted_by_evidence": True,
                                                              "reason": "模拟审核：指定原文直接给出相反规则"} for m in data["misconceptions"]]}
        elif "单项选择题编写器" in system:
            mids = {m["label"]: m["id"] for m in data["misconceptions"]}
            result = {"insufficient": False, "questions": [{
                "stem": stems[len(generated)], "target_misconception_id": data["target_misconception_id"],
                "options": [
                    {"text": "取出并删除当前栈顶一个元素", "is_correct": True, "feedback": "讲义规定 pop 删除栈顶一个元素"},
                    {"text": "按最早入栈顺序选择元素", "is_correct": False, "feedback": "后进先出不能按最早优先", "misconception_id": mids["栈当队列"]},
                    {"text": "跳过栈顶直接取中间元素", "is_correct": False, "feedback": "栈只允许在栈顶操作", "misconception_id": mids["任意位置删除"]},
                    {"text": "每次删除都必须清空全部内容", "is_correct": False, "feedback": "一次 pop 只删除一个", "misconception_id": mids["一次弹空"]},
                ], "evidence_sentence_ids": sentence_ids, "explanation": "根据原文，删除只作用于最近压入的栈顶一个元素。",
            }]}
            generated.append(result["questions"][0])
        elif "盲答审题员" in system:
            assert all(set(o) == {"letter", "text"} for o in data["options"])
            assert "target_misconception_id" not in data
            catalog = {m["label"]: m["id"] for m in data["misconceptions"]}
            mapping, answer = {}, None
            for option in data["options"]:
                if "当前栈顶一个" in option["text"]:
                    answer, mapping[option["letter"]] = option["letter"], None
                elif "最早" in option["text"]: mapping[option["letter"]] = catalog["栈当队列"]
                elif "中间" in option["text"]: mapping[option["letter"]] = catalog["任意位置删除"]
                else: mapping[option["letter"]] = catalog["一次弹空"]
            result = {"answer_letter": answer, "evidence_sufficient": True, "other_defensible": [], "issues": [],
                      "option_misconception_ids": mapping}
            reviewed.append(data)
        else:
            raise AssertionError("Unexpected provider operation")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 12}})

    async def scenario():
        service = AIService(records.append, reserve, tmp_path)
        service.client.transport = httpx.MockTransport(handler)
        topics = await service.candidate_topics("doc", parsed["pages"])
        topic = {"id": "topic", **topics[0]}
        evidence = await service.build_evidence("doc", topic, parsed["chunks"], parsed["sentences"])
        misconceptions = await service.misconceptions("doc", topic, evidence)
        assert len(misconceptions) == 3
        assert all(m["source"] == "model_refutation_reviewed:zhizheng-v2.2" for m in misconceptions)
        published = []
        for misconception in misconceptions:
            published.extend(await service.generate_questions("doc", topic, misconceptions, misconception["id"], evidence,
                                                               [q["stem"] for q in published]))
        assert len(published) == 3
        assert len({q["target_misconception_id"] for q in published}) == 3
        assert all(q["prompt_version"] == "zhizheng-v2.2" for q in published)
        return published

    asyncio.run(scenario())
    assert len(reviewed) == 3 and len(records) == len(reservations) == 11
    assert {record["purpose"] for record in records} == {"candidate_topics", "embedding", "misconceptions", "review_misconceptions", "generate", "blind_review"}
    assert all(record["status"] == "succeeded" for record in records)
    assert "simulated-provider-only" not in json.dumps(records)
