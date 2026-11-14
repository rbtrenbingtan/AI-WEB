"""SQLite storage; a fresh connection per transaction, all IDs are opaque."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, session_id TEXT, is_demo INTEGER NOT NULL DEFAULT 0,name TEXT NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,page_count INTEGER NOT NULL DEFAULT 0,source_note TEXT NOT NULL DEFAULT '',candidate_topics TEXT NOT NULL DEFAULT '[]',error TEXT,created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS owned_hash ON documents(session_id,sha256);
CREATE TABLE IF NOT EXISTS pages(document_id TEXT NOT NULL REFERENCES documents(id), page_index INTEGER NOT NULL,text TEXT NOT NULL,PRIMARY KEY(document_id,page_index));
CREATE TABLE IF NOT EXISTS sentences(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),page_index INTEGER NOT NULL,ord INTEGER NOT NULL,text TEXT NOT NULL,char_start INTEGER NOT NULL,char_end INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS sentence_doc ON sentences(document_id,ord);
CREATE TABLE IF NOT EXISTS extraction_metadata(document_id TEXT PRIMARY KEY REFERENCES documents(id), warnings TEXT NOT NULL, raw_pages TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),page_index INTEGER NOT NULL,first_sentence_ord INTEGER,last_sentence_ord INTEGER,text TEXT NOT NULL,sentence_ids TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS topics(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',source TEXT NOT NULL,status TEXT NOT NULL,reason TEXT,evidence_chunk_ids TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS misconceptions(id TEXT PRIMARY KEY,topic_id TEXT NOT NULL REFERENCES topics(id),label TEXT NOT NULL,description TEXT NOT NULL,refute_sentence_ids TEXT NOT NULL,source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS questions(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),topic_id TEXT NOT NULL REFERENCES topics(id),kind TEXT NOT NULL DEFAULT 'concept',target_misconception_id TEXT NOT NULL REFERENCES misconceptions(id),payload TEXT NOT NULL,answer_letter TEXT NOT NULL,parent_id TEXT,validation_status TEXT NOT NULL,review_note TEXT,prompt_version TEXT NOT NULL,model TEXT NOT NULL,origin TEXT NOT NULL DEFAULT 'bank',source_note TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS question_target ON questions(target_misconception_id);
CREATE TABLE IF NOT EXISTS quizzes(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),document_id TEXT NOT NULL REFERENCES documents(id),question_ids TEXT NOT NULL,retest_ids TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),question_id TEXT NOT NULL REFERENCES questions(id),quiz_id TEXT NOT NULL REFERENCES quizzes(id),selected TEXT NOT NULL,correct INTEGER NOT NULL,confidence TEXT NOT NULL,hit_misconception_id TEXT REFERENCES misconceptions(id),question_version TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(session_id,question_id));
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),session_id TEXT NOT NULL,type TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,error TEXT,payload TEXT NOT NULL DEFAULT '{}',result TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_calls(id TEXT PRIMARY KEY,document_id TEXT,purpose TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT,input_tokens INTEGER NOT NULL DEFAULT 0,output_tokens INTEGER NOT NULL DEFAULT 0,latency_ms REAL NOT NULL DEFAULT 0,status TEXT NOT NULL,error_type TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS call_reservations(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,day TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS call_budget ON call_reservations(document_id,day);
CREATE TABLE IF NOT EXISTS generation_runs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,topic_id TEXT NOT NULL,target_misconception_id TEXT NOT NULL,stats TEXT NOT NULL,created_at TEXT NOT NULL);
'''

def get_conn(db_path):
    conn = sqlite3.connect(str(db_path),timeout=30)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

@contextmanager
def connect(db_path):
    conn=get_conn(db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
