"""Export measured database facts, without source text, answers, or session IDs."""
import argparse
import collections
import csv
import json
import sqlite3
import statistics
from pathlib import Path


def export(db_path, output):
    if not db_path.is_file():
        raise SystemExit(f'Database does not exist: {db_path}')
    db = sqlite3.connect(f'{db_path.resolve().as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    calls = [dict(r) for r in db.execute('SELECT purpose,model,prompt_version,input_tokens,output_tokens,latency_ms,status,error_type,created_at FROM llm_calls ORDER BY created_at')]
    questions = [dict(r) for r in db.execute('SELECT answer_letter,validation_status,model,origin,payload FROM questions')]
    output.mkdir(parents=True, exist_ok=True)
    with (output/'llm_calls.csv').open('w', encoding='utf-8-sig', newline='') as f:
        fields = ['purpose','model','prompt_version','input_tokens','output_tokens','latency_ms','status','error_type','created_at']
        writer=csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(calls)
    latencies = [c['latency_ms'] for c in calls]
    generation = collections.Counter()
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='generation_runs'").fetchone():
        for row in db.execute('SELECT stats FROM generation_runs'):
            generation.update(json.loads(row[0]))
    long_answers = 0
    for q in questions:
        opts = json.loads(q['payload'])['options']
        lengths = [len(o['text']) for o in opts if not o['is_correct']]
        right = next(len(o['text']) for o in opts if o['is_correct'])
        long_answers += int(right > 1.5 * statistics.mean(lengths))
    summary = {
        'note': '真实数据库计数；没有调用时延和人工质量测量时填 null。种子规则检查不等于模型盲答或人工审定。',
        'questions': len(questions),
        'answer_positions': dict(collections.Counter(q['answer_letter'] for q in questions)),
        'validation_statuses': dict(collections.Counter(q['validation_status'] for q in questions)),
        'long_correct_option_count': long_answers,
        'model_call_count': len(calls),
        'generation_totals': dict(generation),
        'call_statuses': dict(collections.Counter(c['status'] for c in calls)),
        'input_tokens': sum(c['input_tokens'] for c in calls),
        'output_tokens': sum(c['output_tokens'] for c in calls),
        'latency_ms_median': statistics.median(latencies) if latencies else None,
        'latency_ms_max': max(latencies) if latencies else None,
        'human_quality_rate': None,
        'attempt_count': db.execute('SELECT count(*) FROM attempts').fetchone()[0],
    }
    db.close()
    (output/'metrics.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=Path('data/app.sqlite'))
    p.add_argument('--output', type=Path, default=Path('eval/results'))
    args = p.parse_args()
    export(args.db, args.output)
