"""Black-box API regression tests; answer access is test-only via SQLite."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.main import create_app
    return create_app(tmp_path)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def make_quiz(client):
    docs = client.get('/api/documents').json()['documents']
    doc = client.get(f"/api/documents/{docs[0]['id']}").json()
    response = client.post('/api/quizzes', json={
        'document_id': doc['id'], 'topic_ids': [t['id'] for t in doc['topics']]})
    assert response.status_code in (200, 201), response.text
    return doc, response.json()


def internal_question(app, qid):
    with sqlite3.connect(str(app.state.db_path)) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone())
        row['payload'] = json.loads(row['payload'])
        return row


def submit(client, quiz, q, answer, confidence='sure'):
    return client.post(f"/api/questions/{q['id']}/attempts", json={
        'quiz_id': quiz['id'], 'selected': answer, 'confidence': confidence})


def test_demo_health_and_upload_disabled(client):
    health = client.get('/api/health').json()
    assert health['mode'] == 'demo' and not health['upload_enabled']
    r = client.post('/api/documents', files={'file': ('test.pdf', b'%PDF-1.4\n', 'application/pdf')})
    assert r.status_code in (403, 409)


def test_quiz_hides_answers_and_requires_confidence(client):
    _, quiz = make_quiz(client)
    assert len(quiz['questions']) == 3
    assert len({q['id'] for q in quiz['questions']}) == 3
    forbidden = ('answer_letter', 'is_correct', 'explanation', 'evidence_sentence_ids', 'target_misconception_id', 'target_label', 'feedback')
    encoded = json.dumps(quiz)
    assert all(key not in encoded for key in forbidden)
    restored = json.dumps(client.get(f"/api/quizzes/{quiz['id']}").json())
    assert all(key not in restored for key in forbidden)
    q = quiz['questions'][0]
    assert {o['letter'] for o in q['options']} == set('ABCD')
    r = client.post(f"/api/questions/{q['id']}/attempts", json={'quiz_id': quiz['id'], 'selected': 'A'})
    assert r.status_code == 422


def test_first_answer_idempotent_and_persists(client, app):
    _, quiz = make_quiz(client)
    q = quiz['questions'][0]
    internal = internal_question(app, q['id'])
    correct = internal['answer_letter']
    wrong = next(x for x in 'ABCD' if x != correct)
    result = submit(client, quiz, q, wrong).json()
    assert result['correct'] is False and result['misconception']
    duplicate = submit(client, quiz, q, correct, 'guess').json()
    assert duplicate['duplicate'] is True
    assert duplicate['correct'] is False and duplicate['selected'] == wrong
    review = client.get('/api/review').json()
    assert review['total'] == 1
    assert review['quadrants']['sure_wrong'] == 1
    restored = client.get(f"/api/quizzes/{quiz['id']}").json()
    assert restored['questions'][0]['result']['selected'] == wrong


def test_session_cannot_read_another_quiz(app, client):
    _, quiz = make_quiz(client)
    with TestClient(app) as outsider:
        assert outsider.get(f"/api/quizzes/{quiz['id']}").status_code in (403, 404)
        r = submit(outsider, quiz, quiz['questions'][0], 'A')
        assert r.status_code in (403, 404)
        assert outsider.get('/api/review').json()['total'] == 0


def test_wrong_answer_evidence_variant_and_retest(client, app):
    doc, quiz = make_quiz(client)
    q = quiz['questions'][0]
    original = internal_question(app, q['id'])
    payload = original['payload']
    options = payload['options']
    targeted = next(o for o in options if not o['is_correct'] and o.get('misconception_id') == original['target_misconception_id'])
    result = submit(client, quiz, q, targeted['letter']).json()
    mid = result['misconception']['id']
    ids = result['evidence_sentence_ids'] + result['misconception']['refute_sentence_ids']
    evidence = client.get(f"/api/documents/{doc['id']}/sentences", params={'ids': ','.join(ids)})
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()['sentences']
    for s in evidence.json()['sentences']:
        assert s['text'] and 1 <= s['page_index'] <= doc['page_count']
        assert any(c['highlight'] for c in s['context'])
    variant = client.post(f"/api/questions/{q['id']}/variant", json={})
    assert variant.status_code == 200, variant.text
    body = variant.json()
    newq = body['question']
    assert newq['id'] != q['id']
    assert newq['target_label'] == result['misconception']['label']
    restored = client.get(f"/api/quizzes/{body['quiz_id']}").json()['questions'][0]
    assert restored['target_label'] == newq['target_label']
    iq = internal_question(app, newq['id'])
    assert iq['target_misconception_id'] == mid
    r = submit(client, {'id': body['quiz_id']}, newq, iq['answer_letter'])
    assert r.json()['correct']
    review = client.get('/api/review').json()
    state = next(m for m in review['misconceptions'] if m['id'] == mid)
    assert state['status'] == 'improving'
    next_quiz = client.post('/api/quizzes', json={'document_id': doc['id'], 'topic_ids': [q['topic_id']]}).json()
    retest = next(x for x in next_quiz['questions'] if x['is_retest'])
    assert internal_question(app, retest['id'])['target_misconception_id'] == mid
    ir = internal_question(app, retest['id'])
    submit(client, next_quiz, retest, ir['answer_letter'])
    review = client.get('/api/review').json()
    assert next(m for m in review['misconceptions'] if m['id'] == mid)['status'] == 'corrected'


def test_unknown_resources_and_invalid_answers(client):
    _, quiz = make_quiz(client)
    assert client.get('/api/documents/no-such-document').status_code == 404
    assert client.get('/api/jobs/no-such-job').status_code == 404
    assert submit(client, quiz, quiz['questions'][0], 'E').status_code == 422
    assert submit(client, quiz, quiz['questions'][0], 'A', 'certain').status_code == 422
    assert client.post(f"/api/questions/{quiz['questions'][0]['id']}/variant", json={}).status_code in (400, 403, 409)


def test_seed_readonly_and_no_cross_doc_sentence_leak(client):
    doc, _ = make_quiz(client)
    assert client.put(f"/api/documents/{doc['id']}/topics", json={'topics': [{'title':'改名','description':''}]}).status_code in (403, 409)
    r = client.get(f"/api/documents/{doc['id']}/sentences", params={'ids': 'alien_sentence'})
    assert r.status_code in (400, 404, 422)


def test_guess_correct_review_entry(client, app):
    _, quiz = make_quiz(client)
    q = quiz['questions'][0]
    submit(client, quiz, q, internal_question(app, q['id'])['answer_letter'], 'guess')
    review = client.get('/api/review').json()
    assert review['quadrants']['guess_correct'] == 1
    assert review['guess_correct'][0]['question_id'] == q['id']


def test_oversized_body_rejected_before_multipart_parsing(client):
    result=client.post('/api/documents',content=b'abc',headers={'content-length':str(12*1024*1024),'content-type':'multipart/form-data; boundary=x'})
    assert result.status_code==413


def test_cross_origin_submission_rejected(client):
    doc,quiz=make_quiz(client)
    result=client.post('/api/quizzes',json={'document_id':doc['id'],'topic_ids':[doc['topics'][0]['id']]},headers={'origin':'https://unrelated.example'})
    assert result.status_code==403


def test_upload_atomic_write_removes_partial_file_on_failure(tmp_path, monkeypatch):
    from pathlib import Path
    from backend.main import store_upload
    original_write = Path.write_bytes

    def interrupted_write(path, content):
        original_write(path, content[:3])
        raise OSError('interrupted write')

    monkeypatch.setattr(Path, 'write_bytes', interrupted_write)
    with pytest.raises(OSError):
        store_upload(tmp_path, 'owned', b'%PDF-test-content')
    assert not (tmp_path / 'owned.part').exists()
    assert not (tmp_path / 'owned.pdf').exists()


def test_ai_workbench_uses_stored_facts_without_exposing_credentials(client, monkeypatch):
    monkeypatch.setenv('REVIEW_API_KEY', 'test-private-value-not-for-display')
    response = client.get('/api/documents/demo/ai')
    assert response.status_code == 200
    body = response.json()
    assert body['is_demo'] is True
    assert body['counts']['questions'] == 48
    assert body['counts']['pages'] == 8
    assert body['calls'] == {'total': 0, 'tokens': 0, 'average_ms': 0, 'records': []}
    assert body['generation'] == {'accepted': 0, 'rejected': 0, 'repairs': 0}
    assert body['misconceptions'][0]['refutation']
    assert 'test-private-value-not-for-display' not in response.text
    assert 'answer_letter' not in response.text


def test_ai_workbench_cannot_read_another_session_document(client, app):
    with sqlite3.connect(app.state.db_path) as db:
        db.execute("INSERT INTO documents(id,session_id,is_demo,name,sha256,status,created_at) VALUES('private','other',0,'私有讲义','hash','ready','2026-11-16')")
    assert client.get('/api/documents/private/ai').status_code == 404


def test_seed_evidence_stays_sentence_level_after_misconception_merge(app):
    with sqlite3.connect(app.state.db_path) as db:
        for payload, refs in db.execute('SELECT q.payload,m.refute_sentence_ids FROM questions q JOIN misconceptions m ON q.target_misconception_id=m.id'):
            evidence = json.loads(payload)['evidence_sentence_ids']
            assert 1 <= len(evidence) <= 2
            assert len(set(evidence + json.loads(refs))) <= 3
            for sid in evidence:
                assert not sid.endswith('_s00')
