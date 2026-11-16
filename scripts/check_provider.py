"""Inspect configuration offline; --live explicitly sends small provider probes.

This checks connectivity and response formats, not educational question quality.
Credentials, prompt contents, and provider response bodies are never printed.
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from backend.ai.client import LLMClient, ModelError


async def check_live(client):
    checks = []
    for name, review in (('generation_json', False), ('review_json', True)):
        try:
            result = await client.chat(
                'provider-check', name,
                'Return a JSON object with exactly one key: ok, with boolean value true.',
                {'task': 'Check JSON response availability.'}, review=review,
            )
            if result != {'ok': True}:
                raise ModelError('probe_content', 'JSON 探针结果不符合要求')
            checks.append({'check': name, 'status': 'passed'})
        except ModelError as exc:
            checks.append({'check': name, 'status': 'failed', 'error_type': exc.kind})
    try:
        vectors = await client.embeddings('provider-check', ['A queue is first in, first out.'])
        checks.append({'check': 'embedding', 'status': 'passed', 'dimensions': len(vectors[0])})
    except ModelError as exc:
        checks.append({'check': 'embedding', 'status': 'failed', 'error_type': exc.kind})
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='发送真实请求；最多三项各两次调用，可能计费')
    parser.add_argument('--output', type=Path, help='报告路径（默认 data/provider-checks/<UTC时间>.json）')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    calls = []
    client = LLMClient(logger=calls.append)
    config = {
        'generation': {'host': urlsplit(client.base_url).hostname, 'model': client.model, 'credential_present': bool(client.api_key)},
        'review': {'host': urlsplit(client.review_url).hostname, 'model': client.review_model, 'credential_present': bool(client.review_key)},
        'embedding': {'host': urlsplit(client.embed_url).hostname, 'model': client.embed_model, 'credential_present': bool(client.embed_key)},
    }
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': 'live' if args.live else 'configuration_only', 'configuration': config}
    if args.live:
        if not all(item['credential_present'] for item in config.values()):
            parser.error('缺少凭证；请先配置 .env。未发送任何请求。')
        report['checks'] = asyncio.run(check_live(client))
        report['calls'] = calls
        report['scope'] = '仅验证真实供应商连接、JSON输出与向量接口；不证明题目内容质量或学习效果。'
        destination = args.output or ROOT / 'data' / 'provider-checks' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'报告已保存：{destination.resolve()}')
    if not args.live:
        report['scope'] = '只检查配置，未访问网络。加 --live 才会发送可能计费的真实请求。'
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(args.live and any(c['status'] != 'passed' for c in report['checks']))


if __name__ == '__main__':
    raise SystemExit(main())
