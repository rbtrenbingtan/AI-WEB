"""Small OpenAI-compatible HTTP adapter. Credentials and prompts never enter call logs."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from typing import Callable

import httpx

PROMPT_VERSION = "zhizheng-v2.1"


class ModelError(RuntimeError):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


async def invoke(callback: Callable | None, *args):
    if callback is None:
        return None
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


class LLMClient:
    def __init__(self, logger=None, budget_reserver=None, *, transport=None):
        self.logger = logger
        self.budget_reserver = budget_reserver
        self.transport = transport  # httpx.MockTransport may be injected in protocol tests.
        self.base_url = (os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("LLM_API_KEY", "")
        self.model = os.getenv("LLM_MODEL") or "gpt-4o-mini"
        self.review_model = os.getenv("REVIEW_MODEL") or self.model
        self.review_url = (os.getenv("REVIEW_BASE_URL") or self.base_url).rstrip("/")
        self.review_key = os.getenv("REVIEW_API_KEY") or self.api_key
        self.embed_url = (os.getenv("EMBED_BASE_URL") or self.base_url).rstrip("/")
        self.embed_key = os.getenv("EMBED_API_KEY") or self.api_key
        self.embed_model = os.getenv("EMBED_MODEL") or "text-embedding-3-small"
        self.timeout = max(1, min(120, float(os.getenv("LLM_TIMEOUT_SECONDS") or "45")))

    async def _post(self, document_id: str, purpose: str, model: str, url: str, key: str, body: dict, decode=None):
        if not key:
            raise ModelError("missing_credentials", "当前未配置模型凭证，实时处理不可用")
        for attempt in range(2):
            call_id = await invoke(self.budget_reserver, document_id)
            started = time.perf_counter()
            record = {"call_id": call_id, "document_id": document_id, "purpose": purpose, "model": model,
                      "prompt_version": PROMPT_VERSION, "input_tokens": 0, "output_tokens": 0,
                      "latency_ms": 0, "status": "failed", "error_type": None}
            retryable = False
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=min(15, self.timeout)), follow_redirects=False,
                                             transport=self.transport) as client:
                    async with asyncio.timeout(self.timeout + 5):
                        async with client.stream("POST", url, headers={"Authorization": f"Bearer {key}"}, json=body) as response:
                            if response.status_code >= 400:
                                retryable = response.status_code == 429 or response.status_code >= 500
                                kind = "rate_limit" if response.status_code == 429 else "authentication" if response.status_code in (401, 403) else "http_error"
                                raise ModelError(kind, f"模型服务请求失败（HTTP {response.status_code}）")
                            chunks, size = [], 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > 4 * 1024 * 1024:
                                    raise ModelError("response_too_large", "模型响应超过大小限制")
                                chunks.append(chunk)
                try:
                    data = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError) as exc:
                    retryable = True
                    raise ModelError("invalid_json", "模型服务返回无效 JSON") from exc
                if not isinstance(data, dict):
                    raise ModelError("invalid_structure", "模型服务返回格式不正确")
                usage = data.get("usage") or {}
                record["input_tokens"] = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
                record["output_tokens"] = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
                decoded = decode(data) if decode else data
                record["status"] = "succeeded"
                return decoded
            except (httpx.TimeoutException, TimeoutError) as exc:
                error = ModelError("timeout", "模型请求超时，请稍后继续生成")
                record["error_type"], retryable = error.kind, True
            except httpx.HTTPError as exc:
                error = ModelError("network", "模型服务暂时无法连接")
                record["error_type"], retryable = error.kind, True
            except ModelError as exc:
                error = exc
                record["error_type"] = exc.kind
            except (ValueError, TypeError) as exc:
                error = ModelError("invalid_structure", "模型服务返回格式不正确")
                record["error_type"] = error.kind
            finally:
                record["latency_ms"] = round((time.perf_counter() - started) * 1000)
                await invoke(self.logger, record)
            if not retryable or attempt == 1:
                raise error
            await asyncio.sleep(0.6)
        raise ModelError("unknown", "模型请求失败")

    async def chat(self, document_id: str, purpose: str, system: str, data: dict, *, review=False) -> dict:
        model = self.review_model if review else self.model
        body = {"model": model, "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": json.dumps(data, ensure_ascii=False)}],
                "temperature": 0 if review else 0.35, "max_tokens": 6500,
                "response_format": {"type": "json_object"}}
        def decode(result):
            try:
                content = result["choices"][0]["message"]["content"]
                output = json.loads(content)
                if not isinstance(output, dict):
                    raise ValueError()
                return output
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ModelError("invalid_json", "模型未返回要求的 JSON 对象") from exc
        return await self._post(document_id, purpose, model,
                                (self.review_url if review else self.base_url) + "/chat/completions",
                                self.review_key if review else self.api_key, body, decode)

    async def embeddings(self, document_id: str, texts: list[str]) -> list[list[float]]:
        def decode(result):
            import math
            try:
                rows = sorted(result["data"], key=lambda item: item["index"])
                if [r["index"] for r in rows] != list(range(len(texts))):
                    raise ValueError()
                vectors = [r["embedding"] for r in rows]
                if not vectors or not vectors[0] or len({len(v) for v in vectors}) != 1:
                    raise ValueError()
                if any(type(v) not in (int, float) or not math.isfinite(v) for row in vectors for v in row):
                    raise ValueError()
                if any(not any(value != 0 for value in row) for row in vectors):
                    raise ValueError()
                return vectors
            except (KeyError, ValueError, TypeError, IndexError) as exc:
                raise ModelError("invalid_embedding", "向量服务返回格式不正确") from exc
        return await self._post(document_id, "embedding", self.embed_model, self.embed_url + "/embeddings",
                                self.embed_key, {"model": self.embed_model, "input": texts}, decode)
