"""Build the reproducible offline seed; never invent an online review trail."""
import argparse
import sys
import sqlite3
import os
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.db import init_db,connect
from backend.seed_data import populate_seed,source_markdown

def main():
    parser=argparse.ArgumentParser(description='导出离线 AI 编写、待独立复核的示例题库')
    parser.add_argument('--output',type=Path,default=ROOT/'seed'/'demo.sqlite')
    parser.add_argument('--force',action='store_true',help='仅覆盖指定的种子输出文件')
    parser.add_argument('--source-db',type=Path,help='从真实运行数据库导出已经盲答复核的文档')
    parser.add_argument('--document-id',help='与 --source-db 一起使用；不导出作答、会话或调用日志')
    args=parser.parse_args()
    output=args.output.resolve()
    if output.exists():
        if not args.force: parser.error('输出已存在；确认目标后使用 --force')
    if bool(args.source_db)!=bool(args.document_id):
        parser.error('--source-db 和 --document-id 必须同时提供')
    if args.source_db and args.source_db.resolve()==output:
        parser.error('导出路径不能与运行数据库相同；请使用独立的输出文件')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        temporary=Path(folder)/'seed.sqlite'
        init_db(temporary)
        with connect(temporary) as conn:
            if args.source_db:
                source=args.source_db.resolve()
                if not source.is_file(): parser.error('源数据库不存在')
                with sqlite3.connect(f'{source.as_uri()}?mode=ro',uri=True) as src:
                    src.row_factory=sqlite3.Row
                    # All tables must describe the same generation snapshot, even
                    # while the live app continues to publish questions in WAL.
                    src.execute('BEGIN')
                    doc=src.execute('SELECT * FROM documents WHERE id=?',(args.document_id,)).fetchone()
                    if not doc: parser.error('源文档不存在')
                    doc=dict(doc)
                    if doc['is_demo']: parser.error('此入口仅导出真实处理的上传文档；离线示例请使用默认命令')
                    count=src.execute("SELECT count(*) FROM questions WHERE document_id=? AND validation_status='reviewed'",(args.document_id,)).fetchone()[0]
                    if count<3: parser.error('源文档至少需要 3 道已复核题')
                    doc.update(session_id=None,is_demo=1,status='ready',source_note='真实模型生成 · 代码及盲答复核通过 · 人工审定状态请查团队记录')
                    conn.execute('INSERT INTO documents VALUES ('+','.join('?' for _ in doc)+')',tuple(doc.values()))
                    queries={
                        'pages':'SELECT * FROM pages WHERE document_id=?',
                        'sentences':'SELECT * FROM sentences WHERE document_id=?',
                        'chunks':'SELECT * FROM chunks WHERE document_id=?',
                        'topics':'SELECT * FROM topics WHERE document_id=?',
                        'misconceptions':'SELECT m.* FROM misconceptions m JOIN topics t ON m.topic_id=t.id WHERE t.document_id=?',
                        'questions':"SELECT * FROM questions WHERE document_id=? AND validation_status='reviewed'",
                    }
                    for table,query in queries.items():
                        for row in src.execute(query,(args.document_id,)):
                            conn.execute(f'INSERT INTO {table} VALUES ('+','.join('?' for _ in row)+')',tuple(row))
            if not args.source_db:
                populate_seed(conn)
        with connect(temporary) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        os.replace(temporary,output)
    if not args.source_db:
        (ROOT/'examples').mkdir(exist_ok=True)
        (ROOT/'examples'/'source_notes.md').write_text(source_markdown(),encoding='utf-8')
    print(f'已生成 {output}；来源与复核状态保留在数据库中，未导出用户会话与学习记录。')

if __name__=='__main__': main()
