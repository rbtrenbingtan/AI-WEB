"""Legacy semantic-review integration; model decisions are explicitly simulated."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from backend.ai import AIService
from backend.db import connect
from backend.main import create_app


@pytest.mark.parametrize("approve_all", [True, False])
def test_resuming_legacy_claims_preserves_ids_and_only_marks_full_pass(tmp_path, monkeypatch, approve_all):
    monkeypatch.setenv("LLM_API_KEY", "simulated-unused-key")
    checked, generations = [], []

    async def evidence(self, document, topic, chunks, sentences, required_ids=None):
        return {"sentences": sentences, "chunk_ids": [c["id"] for c in chunks]}

    async def verify(self, document, topic, evidence, items, **kwargs):
        checked.append([m["id"] for m in items])
        return [{**m, "source": self.misconception_review_source} for m in (items if approve_all else items[:2])]

    async def generate(self, document, topic, misconceptions, target, evidence, stems, parent_id=None):
        generations.append(target)
        assert all(m["source"] == self.misconception_review_source for m in misconceptions)
        return []

    monkeypatch.setattr(AIService, "build_evidence", evidence)
    monkeypatch.setattr(AIService, "verify_misconceptions", verify)
    monkeypatch.setattr(AIService, "generate_questions", generate)
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        client.get("/api/health")
        sid = client.cookies["zz_session"]
        with connect(app.state.db_path) as db:
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       ("legacy", sid, 0, "legacy.pdf", "hash", "interrupted", 1, "旧版模型资料", "[]", None, "2026-11-14"))
            db.execute("INSERT INTO topics VALUES (?,?,?,?,?,?,?,?)", ("legacy-topic", "legacy", "栈", "顺序", "confirmed", "insufficient", None, "[]"))
            ids = [f"legacy-s{i}" for i in range(3)]
            for i, sentence_id in enumerate(ids):
                db.execute("INSERT INTO sentences VALUES (?,?,?,?,?,?,?)", (sentence_id, "legacy", 1, i, "栈在栈顶后进先出。", 0, 10))
                db.execute("INSERT INTO misconceptions VALUES (?,?,?,?,?,?)",
                           (f"legacy-m{i}", "legacy-topic", f"旧误区{i}", "用于恢复流程测试", json.dumps([sentence_id]), "model"))
            db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?)", ("legacy-c", "legacy", 1, 0, 2, "栈在栈顶后进先出。", json.dumps(ids)))
        response = client.post("/api/documents/legacy/resume")
        assert response.status_code == 200
        for _ in range(200):
            job = client.get("/api/jobs/" + response.json()["job_id"]).json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert checked == [["legacy-m0", "legacy-m1", "legacy-m2"]]
        with connect(app.state.db_path) as db:
            rows = list(db.execute("SELECT id,source FROM misconceptions WHERE topic_id='legacy-topic' ORDER BY id"))
        assert [r[0] for r in rows] == checked[0]
        if approve_all:
            assert all(r[1] == AIService.misconception_review_source for r in rows)
            assert len(generations) == 3
        else:
            assert job["status"] == "failed" and "旧记录未改标" in job["error"]
            assert all(r[1] == "model" for r in rows) and not generations
