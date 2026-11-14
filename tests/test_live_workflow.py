"""Live-mode plumbing with a simulated provider, real PDF extraction and real DB."""
import json
import sqlite3
import time
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas


def wait_job(client, job_id):
    for _ in range(250):
        job=client.get('/api/jobs/'+job_id).json()
        if job['status'] in ('completed','failed','interrupted'):
            return job
        time.sleep(.02)
    raise AssertionError('job did not finish')


def pdf_bytes(tmp_path):
    path=tmp_path/'source.pdf'
    c=canvas.Canvas(str(path))
    c.drawString(50,780,'Queue fundamentals and evidence')
    for i, text in enumerate([
        'A queue processes the first item inserted before the last item inserted.',
        'Enqueue inserts at the rear; dequeue removes at the front.',
        'An empty queue cannot remove an item because no item is stored.',
    ]):
        c.drawString(50,740-i*25,text)
    c.save()
    return path.read_bytes()


def test_import_confirm_generate_and_session_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY','test-only-never-sent')
    monkeypatch.setenv('UPLOAD_ENABLED','true')
    monkeypatch.setenv('ALLOW_UPLOADS','true')
    from backend.main import create_app
    from backend.ai import AIService
    async def candidates(self,document_id,pages):
        return [{'title':'队列','description':'测试模型候选知识点'}]
    async def evidence(self,document_id,topic,chunks,sentences,required_ids=None):
        return {'sentences':sentences,'chunk_ids':[c['id'] for c in chunks]}
    async def misconceptions(self,document_id,topic,evidence):
        return [{'id':f"{topic['id']}_m{i}",'topic_id':topic['id'],'label':f'误区{i}','description':f'用于接口测试的误区{i}',
                 'refute_sentence_ids':[evidence['sentences'][0]['id']]} for i in range(3)]
    async def generate(self,document_id,topic,misconceptions,target_id,evidence,existing_stems,parent_id=None):
        return [{
            'target_misconception_id':target_id,'stem':f'用于测试生成持久化的题目 {target_id}',
            'options':[{'letter':letter,'text':f'选项{letter}','is_correct':letter=='B','feedback':'测试解释','misconception_id':None if letter=='B' else target_id} for letter in 'ABCD'],
            'answer_letter':'B','explanation':'模拟供应商输出，只用于工程测试',
            'evidence_sentence_ids':[evidence['sentences'][0]['id']],
            'prompt_version':'test-only','model':'simulated',
        }]
    monkeypatch.setattr(AIService,'candidate_topics',candidates)
    monkeypatch.setattr(AIService,'build_evidence',evidence)
    monkeypatch.setattr(AIService,'misconceptions',misconceptions)
    monkeypatch.setattr(AIService,'generate_questions',generate)
    app=create_app(tmp_path/'data')
    with TestClient(app) as client:
        assert client.get('/api/health').json()['mode']=='live'
        content=pdf_bytes(tmp_path)
        response=client.post('/api/documents',files={'file':('notes.pdf',content,'application/pdf')})
        assert response.status_code==202,response.text
        upload=response.json()
        job=wait_job(client,upload['job_id'])
        assert job['status']=='completed',job
        doc=client.get('/api/documents/'+upload['id']).json()
        assert doc['status']=='awaiting_topics' and doc['samples'][0]['text']
        duplicate=client.post('/api/documents',files={'file':('again.pdf',content,'application/pdf')}).json()
        assert duplicate['id']==doc['id']
        confirm=client.put(f"/api/documents/{doc['id']}/topics",json={'topics':doc['candidate_topics']})
        assert confirm.status_code==200,confirm.text
        generated=wait_job(client,confirm.json()['job_id'])
        assert generated['status']=='completed',generated
        ready=client.get('/api/documents/'+doc['id']).json()
        assert ready['status']=='ready' and ready['question_count']==3
        quiz=client.post('/api/quizzes',json={'document_id':doc['id'],'topic_ids':[ready['topics'][0]['id']]})
        assert quiz.status_code==201,quiz.text
        exported=tmp_path/'exported.sqlite'
        process=subprocess.run([sys.executable,'scripts/build_seed.py','--source-db',str(app.state.db_path),'--document-id',doc['id'],'--output',str(exported)],capture_output=True)
        assert process.returncode==0,process.stderr.decode(errors='replace')
        with sqlite3.connect(exported) as snapshot:
            assert snapshot.execute('SELECT count(*) FROM questions').fetchone()[0]==3
            assert snapshot.execute('SELECT count(*) FROM sessions').fetchone()[0]==0
            assert snapshot.execute('SELECT count(*) FROM quizzes').fetchone()[0]==0
            assert snapshot.execute('SELECT count(*) FROM attempts').fetchone()[0]==0
            assert snapshot.execute('SELECT is_demo,session_id FROM documents').fetchone()==(1,None)
        with TestClient(app) as other:
            assert other.get('/api/documents/'+doc['id']).status_code==404
            assert other.get('/api/jobs/'+upload['job_id']).status_code==404
            assert all(d['id']!=doc['id'] for d in other.get('/api/documents').json()['documents'])


def test_restarted_jobs_become_interrupted(tmp_path, monkeypatch):
    monkeypatch.setenv('LLM_API_KEY','')
    from backend.main import create_app
    from backend.db import connect
    app=create_app(tmp_path)
    with connect(app.state.db_path) as db:
        db.execute("INSERT INTO sessions VALUES ('test-session','2026-11-14')")
        db.execute("INSERT INTO documents VALUES ('owned','test-session',0,'test.pdf','hash','generating',1,'test','[]',NULL,'2026-11-14')")
        db.execute("INSERT INTO jobs VALUES ('interrupted-job','owned','test-session','generate','running',20,NULL,'{}',NULL,'2026-11-14')")
    with TestClient(app):
        with connect(app.state.db_path) as db:
            assert db.execute("SELECT status FROM jobs WHERE id='interrupted-job'").fetchone()[0]=='interrupted'
            assert db.execute("SELECT status FROM documents WHERE id='owned'").fetchone()[0]=='interrupted'
