"""Simulated provider tests: validates HTTP protocol, never claims real model quality."""
import asyncio
import json

import httpx
import pytest

from backend.ai.client import LLMClient, ModelError


def run(coro):
    return asyncio.run(coro)


def response(content):
    return httpx.Response(200, json={'choices':[{'message':{'content':content}}], 'usage':{'prompt_tokens':15,'completion_tokens':7}})


def test_chat_and_embedding_log_usage_without_secrets(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY', 'unit-test-secret')
    calls, requests, reservations = [], [], []
    def handler(request):
        requests.append(json.loads(request.content))
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200,json={'data':[{'index':0,'embedding':[0.2,0.8]}], 'usage':{'prompt_tokens':4}})
        return response('{"ok":true}')
    def reserve(doc):
        reservations.append(doc)
        return f'call-{len(reservations)}'
    client = LLMClient(calls.append, reserve, transport=httpx.MockTransport(handler))
    assert run(client.chat('doc','test','private source',{'text':'private source'})) == {'ok':True}
    assert run(client.embeddings('doc',['private source'])) == [[0.2,0.8]]
    assert len(calls) == 2 and len(reservations) == 2
    assert calls[0]['input_tokens'] == 15 and calls[0]['output_tokens'] == 7
    assert all(c['status'] == 'succeeded' for c in calls)
    assert 'unit-test-secret' not in json.dumps(calls)
    assert 'private source' not in json.dumps(calls)


@pytest.mark.parametrize('status,expected', [(429,2),(500,2),(401,1),(403,1)])
def test_http_failures_have_bounded_retry(monkeypatch, status, expected):
    monkeypatch.setenv('LLM_API_KEY','unit-test-secret')
    logs=[]
    client=LLMClient(logs.append, transport=httpx.MockTransport(lambda req:httpx.Response(status,json={'error':'private secret'})))
    with pytest.raises(ModelError):
        run(client.chat('doc','test','system',{}))
    assert len(logs) == expected
    assert all(x['status']=='failed' for x in logs)
    assert 'private secret' not in json.dumps(logs)


def test_invalid_content_is_failed_not_success(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY','unit-test-secret')
    logs=[]
    client=LLMClient(logs.append, transport=httpx.MockTransport(lambda req:response('not JSON')))
    with pytest.raises(ModelError) as error:
        run(client.chat('doc','test','system',{}))
    assert error.value.kind == 'invalid_json'
    assert len(logs)==1 and logs[0]['status']=='failed'
    assert logs[0]['input_tokens']==15


def test_budget_prevents_network_request(monkeypatch):
    monkeypatch.setenv('LLM_API_KEY','unit-test-secret')
    def deny(doc):
        raise RuntimeError('budget exhausted')
    def never(request):
        raise AssertionError('must not call provider after budget exhausted')
    client=LLMClient(budget_reserver=deny,transport=httpx.MockTransport(never))
    with pytest.raises(RuntimeError,match='budget'):
        run(client.chat('doc','test','system',{}))


@pytest.mark.parametrize('vector', [[0,0], [float('nan'),1], ['wrong',1]])
def test_invalid_vectors_rejected(monkeypatch,vector):
    monkeypatch.setenv('LLM_API_KEY','unit-test-secret')
    logs=[]
    def handler(request):
        body=json.dumps({'data':[{'index':0,'embedding':vector}]})
        return httpx.Response(200,content=body)
    client=LLMClient(logs.append,transport=httpx.MockTransport(handler))
    with pytest.raises(ModelError):
        run(client.embeddings('doc',['query']))
    assert logs[0]['status']=='failed'
