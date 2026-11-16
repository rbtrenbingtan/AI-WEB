import asyncio
import hashlib
import json
import os
import secrets
import shutil
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.db import connect, init_db

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'.env')

def now(): return datetime.now(timezone.utc).isoformat(timespec='microseconds')
def uid(prefix): return prefix+'_'+secrets.token_hex(12)
def dumps(value): return json.dumps(value,ensure_ascii=False)


def store_upload(folder: Path, document_id: str, content: bytes) -> Path:
    """Publish a complete file atomically before the document transaction commits."""
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f'{document_id}.pdf'
    temporary = folder / f'{document_id}.part'
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return destination

class TopicInput(BaseModel):
    title: str=Field(min_length=1,max_length=80)
    description: str=Field(default='',max_length=500)
class TopicsInput(BaseModel):
    topics: list[TopicInput]=Field(min_length=1,max_length=6)
class QuizInput(BaseModel):
    document_id: str
    topic_ids: list[str]=Field(min_length=1,max_length=6)
class AttemptInput(BaseModel):
    quiz_id: str
    selected: Literal['A','B','C','D']
    confidence: Literal['sure','unsure','guess']

def create_app(data_dir=None):
    data_dir=Path(data_dir or os.environ.get('DATA_DIR',ROOT/'data')).resolve()
    data_dir.mkdir(parents=True,exist_ok=True)
    db_path=data_dir/'app.sqlite'
    if not db_path.exists():
        seed=ROOT/'seed'/'demo.sqlite'
        if seed.exists(): shutil.copyfile(seed,db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        if not conn.execute('SELECT 1 FROM documents WHERE is_demo=1').fetchone():
            from backend.seed_data import populate_seed
            populate_seed(conn)
    live=bool(os.environ.get('LLM_API_KEY','').strip())
    uploads=live and os.environ.get('UPLOAD_ENABLED',os.environ.get('ALLOW_UPLOADS','true')).lower()=='true'
    running=set()
    semaphore=asyncio.Semaphore(max(1,int(os.environ.get('GEN_CONCURRENCY','3'))))

    @asynccontextmanager
    async def lifespan(app):
        with connect(db_path) as conn:
            conn.execute("UPDATE documents SET status='interrupted' WHERE id IN (SELECT document_id FROM jobs WHERE status IN ('running','queued'))")
            conn.execute("UPDATE jobs SET status='interrupted',error='服务重启，任务已中断，可继续' WHERE status IN ('running','queued')")
        yield
        tasks=list(running)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)
        with connect(db_path) as conn:
            conn.execute("UPDATE documents SET status='interrupted' WHERE id IN (SELECT document_id FROM jobs WHERE status IN ('running','queued'))")
            conn.execute("UPDATE jobs SET status='interrupted',error='服务关闭，任务已中断，可继续' WHERE status IN ('running','queued')")

    app=FastAPI(title='知证学伴 API',version='2.0.0',lifespan=lifespan)
    app.state.db_path=db_path
    app.state.data_dir=data_dir
    from backend.upload_limit import UploadLimitMiddleware
    app.add_middleware(UploadLimitMiddleware)

    @app.middleware('http')
    async def session_middleware(request,call_next):
        if request.method in ('POST','PUT','DELETE','PATCH') and request.headers.get('origin'):
            if urlsplit(request.headers['origin']).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'请求来源不匹配'},status_code=403)
        sid=request.cookies.get('zz_session','')
        with connect(db_path) as conn:
            exists=conn.execute('SELECT 1 FROM sessions WHERE id=?',(sid,)).fetchone() if len(sid)<100 else None
            if not exists:
                sid=secrets.token_urlsafe(32)
                conn.execute('INSERT INTO sessions VALUES (?,?)',(sid,now()))
        request.state.session_id=sid
        response=await call_next(request)
        if not exists:
            response.set_cookie('zz_session',sid,httponly=True,samesite='lax',secure=os.environ.get('COOKIE_SECURE','false').lower()=='true',max_age=86400*365)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        if request.url.path.startswith('/api'): response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(Exception)
    async def unhandled(request,exc):
        # Deliberately omit provider error bodies and credentials from HTTP responses.
        return JSONResponse({'detail':'服务暂时无法处理请求，请稍后重试'},status_code=500)

    def doc_owned(conn,doc_id,sid,mutable=False):
        doc=conn.execute('SELECT * FROM documents WHERE id=? AND (is_demo=1 OR session_id=?)',(doc_id,sid)).fetchone()
        if not doc: raise HTTPException(404,'资料不存在或无权访问')
        if mutable and doc['is_demo']: raise HTTPException(403,'示例资料为只读')
        return dict(doc)

    def owned_question(conn,qid,sid):
        q=conn.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone()
        if not q: raise HTTPException(404,'题目不存在')
        doc_owned(conn,q['document_id'],sid)
        return dict(q)

    def quiz_owned(conn,qid,sid):
        q=conn.execute('SELECT * FROM quizzes WHERE id=? AND session_id=?',(qid,sid)).fetchone()
        if not q: raise HTTPException(404,'练习不存在或无权访问')
        return dict(q)

    def public_question(conn,q,retest=False,is_variant=False):
        payload=json.loads(q['payload'])
        title=conn.execute('SELECT title FROM topics WHERE id=?',(q['topic_id'],)).fetchone()[0]
        result=dict(id=q['id'],topic_id=q['topic_id'],topic_title=title,stem=payload['stem'],options=[{'letter':o['letter'],'text':o['text']} for o in payload['options']],origin=q['origin'],source_note=q['source_note'],kind=q['kind'],is_retest=retest)
        if is_variant:
            result['target_label']=conn.execute('SELECT label FROM misconceptions WHERE id=?',(q['target_misconception_id'],)).fetchone()[0]
        return result

    def result_for(conn,q,attempt,duplicate=False):
        p=json.loads(q['payload'])
        misconception=None
        ids=list(p['evidence_sentence_ids'])
        if attempt['hit_misconception_id']:
            m=conn.execute('SELECT * FROM misconceptions WHERE id=?',(attempt['hit_misconception_id'],)).fetchone()
            if m:
                misconception={k:m[k] for k in ('id','label','description')}
                misconception['refute_sentence_ids']=json.loads(m['refute_sentence_ids'])
                ids=list(dict.fromkeys(ids+misconception['refute_sentence_ids']))
        return dict(question_id=q['id'],correct=bool(attempt['correct']),selected=attempt['selected'],confidence=attempt['confidence'],answer_letter=q['answer_letter'],explanation=p['explanation'],options=[{k:o[k] for k in ('letter','text','feedback','is_correct')} for o in p['options']],misconception=misconception,evidence_sentence_ids=ids,document_id=q['document_id'],duplicate=duplicate)

    def save_quiz(conn,sid,doc_id,questions,retest_ids=None):
        quiz_id=uid('quiz')
        conn.execute('INSERT INTO quizzes VALUES (?,?,?,?,?,?)',(quiz_id,sid,doc_id,dumps([q['id'] for q in questions]),dumps(retest_ids or []),now()))
        return quiz_id

    def review_data(conn,sid):
        rows=[dict(r) for r in conn.execute('SELECT a.*,q.topic_id,q.target_misconception_id,q.document_id,q.payload FROM attempts a JOIN questions q ON q.id=a.question_id WHERE a.session_id=? ORDER BY a.created_at,a.id',(sid,))]
        quadrants={c+'_'+r:0 for c in ('sure','unsure','guess') for r in ('correct','wrong')}
        topics={}
        affected={r['hit_misconception_id'] for r in rows if r['hit_misconception_id']}
        mistakes=[]
        for r in rows:
            quadrants[r['confidence']+'_'+('correct' if r['correct'] else 'wrong')]+=1
            if r['topic_id'] not in topics:
                title=conn.execute('SELECT title FROM topics WHERE id=?',(r['topic_id'],)).fetchone()[0]
                topics[r['topic_id']]=dict(id=r['topic_id'],title=title,correct=0,total=0)
            topics[r['topic_id']]['total']+=1
            topics[r['topic_id']]['correct']+=r['correct']
        for mid in affected:
            m=dict(conn.execute('SELECT m.*,t.title topic_title,t.document_id FROM misconceptions m JOIN topics t ON t.id=m.topic_id WHERE m.id=?',(mid,)).fetchone())
            errors=[r for r in rows if r['hit_misconception_id']==mid]
            last=errors[-1]
            good={r['question_id'] for r in rows if r['correct'] and r['target_misconception_id']==mid and r['created_at']>last['created_at']}
            mistakes.append(dict(id=mid,topic_id=m['topic_id'],topic_title=m['topic_title'],label=m['label'],description=m['description'],status='corrected' if len(good)>=2 else 'improving' if good else 'pending',wrong_count=len(errors),sure_wrong_count=sum(r['confidence']=='sure' for r in errors),last_wrong_at=last['created_at'],distinct_correct=len(good),document_id=m['document_id'],question_id=last['question_id'],refute_sentence_ids=json.loads(m['refute_sentence_ids'])))
        mistakes.sort(key=lambda m:(m['sure_wrong_count']>0,m['last_wrong_at'],m['wrong_count']),reverse=True)
        attempts=[dict(question_id=r['question_id'],quiz_id=r['quiz_id'],stem=json.loads(r['payload'])['stem'],correct=bool(r['correct']),confidence=r['confidence'],created_at=r['created_at'],document_id=r['document_id']) for r in reversed(rows)]
        return dict(total=len(rows),correct=sum(r['correct'] for r in rows),quadrants=quadrants,topics=list(topics.values()),misconceptions=mistakes,attempts=attempts,guess_correct=[a for a in attempts if a['correct'] and a['confidence']=='guess'])

    def doc_summary(conn,row):
        d=dict(row)
        return {**{k:d[k] for k in ('id','name','page_count','status','is_demo','source_note')},'question_count':conn.execute('SELECT COUNT(*) FROM questions WHERE document_id=?',(d['id'],)).fetchone()[0],'topic_count':conn.execute('SELECT COUNT(*) FROM topics WHERE document_id=?',(d['id'],)).fetchone()[0]}

    def start_task(coro):
        task=asyncio.create_task(coro)
        running.add(task)
        task.add_done_callback(running.discard)

    def reserve_call(document_id):
        cid=uid('call'); stamp=now()
        with connect(db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            perdoc=conn.execute('SELECT COUNT(*) FROM call_reservations WHERE document_id=?',(document_id,)).fetchone()[0]
            daily=conn.execute('SELECT COUNT(*) FROM call_reservations WHERE day=?',(stamp[:10],)).fetchone()[0]
            if perdoc>=int(os.environ.get('MAX_CALLS_PER_DOC','100')) or daily>=int(os.environ.get('MAX_CALLS_PER_DAY','300')):
                raise RuntimeError('模型调用预算已用尽，请调整预算后继续')
            conn.execute('INSERT INTO call_reservations VALUES (?,?,?,?)',(cid,document_id,stamp[:10],stamp))
        return cid

    def record_call(record):
        with connect(db_path) as conn:
            conn.execute('INSERT OR REPLACE INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?,?)',(record.get('call_id') or uid('call'),record.get('document_id'),record.get('purpose','unknown'),record.get('model','unknown'),record.get('prompt_version','v2'),record.get('input_tokens',0) or 0,record.get('output_tokens',0) or 0,record.get('latency_ms',0),record.get('status','error'),record.get('error_type'),now()))

    def service():
        from backend.ai import AIService
        return AIService(logger=record_call,budget_reserver=reserve_call,cache_dir=data_dir/'vectors')

    def job_update(jid,status,progress=None,error=None):
        with connect(db_path) as conn:
            conn.execute('UPDATE jobs SET status=?,progress=COALESCE(?,progress),error=? WHERE id=?',(status,progress,error,jid))

    async def parse_job(jid,doc_id):
        try:
            job_update(jid,'running',5)
            from backend.ai import parse_pdf
            async with semaphore:
                parsed=await asyncio.to_thread(parse_pdf,data_dir/'uploads'/(doc_id+'.pdf'),doc_id)
            with connect(db_path) as conn:
                conn.execute('INSERT OR REPLACE INTO extraction_metadata VALUES (?,?,?)',(doc_id,dumps(parsed.get('warnings',[])),dumps([{ 'page_index':p['page_index'],'text':p.get('raw_text',p['text']) } for p in parsed['pages']])))
                for p in parsed['pages']:
                    conn.execute('INSERT OR REPLACE INTO pages VALUES (?,?,?)',(doc_id,p['page_index'],p['text']))
                for s in parsed['sentences']:
                    conn.execute('INSERT OR REPLACE INTO sentences VALUES (?,?,?,?,?,?,?)',(s['id'],doc_id,s['page_index'],s['ord'],s['text'],s['char_start'],s['char_end']))
                for c in parsed['chunks']:
                    conn.execute('INSERT OR REPLACE INTO chunks VALUES (?,?,?,?,?,?,?)',(c['id'],doc_id,c['page_index'],c['first_sentence_ord'],c['last_sentence_ord'],c['text'],dumps(c['sentence_ids'])))
                conn.execute("UPDATE documents SET page_count=?,status='indexing' WHERE id=?",(parsed['page_count'],doc_id))
            job_update(jid,'running',65)
            async with semaphore:
                candidates=await service().candidate_topics(doc_id,parsed['pages'])
            with connect(db_path) as conn:
                conn.execute("UPDATE documents SET status='awaiting_topics',candidate_topics=?,error=NULL WHERE id=?",(dumps(candidates),doc_id))
            job_update(jid,'completed',100)
        except asyncio.CancelledError: raise
        except Exception as exc:
            message=safe_error(exc)
            job_update(jid,'failed',error=message)
            with connect(db_path) as conn: conn.execute("UPDATE documents SET status='failed',error=? WHERE id=?",(message,doc_id))

    def safe_error(exc):
        # Only controlled errors are safe to expose. Provider bodies may include original text.
        if isinstance(exc,(ValueError,RuntimeError)):
            value=str(exc)
            if not any(x in value.lower() for x in ('bearer','api_key','sk-','http')): return value[:300]
        return '处理失败（'+type(exc).__name__+'），请检查服务端配置后重试'

    def load_source(conn,doc_id):
        sentences=[dict(r) for r in conn.execute('SELECT * FROM sentences WHERE document_id=? ORDER BY ord',(doc_id,))]
        chunks=[{**dict(r),'sentence_ids':json.loads(r['sentence_ids'])} for r in conn.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY page_index,first_sentence_ord',(doc_id,))]
        return sentences,chunks

    def persist_question(conn,doc_id,topic_id,q,origin='bank',parent_id=None):
        payload=q.get('payload',q)
        qid=uid('q')
        answer=q.get('answer_letter') or payload.get('answer_letter') or next(o['letter'] for o in payload['options'] if o['is_correct'])
        conn.execute('INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(qid,doc_id,topic_id,q.get('kind','concept'),payload['target_misconception_id'],dumps(payload),answer,parent_id,'reviewed',dumps(q.get('review_note','盲答复核通过')),q.get('prompt_version','v2'),q.get('model',os.environ.get('LLM_MODEL','unknown')),origin,'模型生成 · 代码校验与盲答复核通过',now()))
        return dict(conn.execute('SELECT * FROM questions WHERE id=?',(qid,)).fetchone())

    async def build_topic(topic,only_target=None,parent_id=None):
        async with semaphore:
            ai=service(); doc_id=topic['document_id']
            with connect(db_path) as conn:
                sentences,chunks=load_source(conn,doc_id)
                misconceptions=[{**dict(m),'refute_sentence_ids':json.loads(m['refute_sentence_ids'])} for m in conn.execute('SELECT * FROM misconceptions WHERE topic_id=?',(topic['id'],))]
            required=[sid for m in misconceptions if not only_target or m['id']==only_target for sid in m['refute_sentence_ids']]
            search_topic=dict(topic)
            if only_target:
                search_topic['description']+=' '+next(m['description'] for m in misconceptions if m['id']==only_target)
            evidence=await ai.build_evidence(doc_id,search_topic,chunks,sentences,required_ids=required)
            if not evidence['sentences']: raise RuntimeError('证据包为空，依据不足')
            with connect(db_path) as conn: conn.execute('UPDATE topics SET evidence_chunk_ids=? WHERE id=?',(dumps(evidence['chunk_ids']),topic['id']))
            if any(m['source'] != ai.misconception_review_source for m in misconceptions):
                # Old live databases retain their IDs/history, but must pass the current
                # semantic gate before generating more questions under the new version.
                # Include every stored correction, not only the requested variant target.
                legacy_required=[sid for m in misconceptions for sid in m['refute_sentence_ids']]
                evidence=await ai.build_evidence(doc_id,topic,chunks,sentences,required_ids=legacy_required)
                verified=await ai.verify_misconceptions(doc_id,topic,evidence,misconceptions)
                if len(verified)!=len(misconceptions):
                    raise RuntimeError('既有误区未全部通过当前版本原文复核，请重新导入资料；旧记录未改标为已复核')
                misconceptions=verified
                with connect(db_path) as conn:
                    conn.execute('UPDATE topics SET evidence_chunk_ids=? WHERE id=?',(dumps(evidence['chunk_ids']),topic['id']))
                    for m in misconceptions:
                        conn.execute('UPDATE misconceptions SET source=? WHERE id=?',(m['source'],m['id']))
            if not misconceptions:
                misconceptions=await ai.misconceptions(doc_id,topic,evidence)
                if len(misconceptions)<3:
                    raise RuntimeError('有原文支持的误区不足 3 条')
                with connect(db_path) as conn:
                    for m in misconceptions:
                        conn.execute('INSERT INTO misconceptions VALUES (?,?,?,?,?,?)',(m['id'],topic['id'],m['label'],m['description'],dumps(m['refute_sentence_ids']),m.get('source','model')))
            if len(misconceptions)<3: raise RuntimeError('有原文支持的误区不足 3 条')
            new_questions=[]
            for m in misconceptions:
                if only_target and m['id']!=only_target: continue
                with connect(db_path) as conn:
                    count=conn.execute('SELECT COUNT(*) FROM questions WHERE target_misconception_id=?',(m['id'],)).fetchone()[0]
                    stems=[json.loads(r[0])['stem'] for r in conn.execute('SELECT payload FROM questions WHERE document_id=?',(doc_id,))]
                if not only_target and count>=2: continue
                try:
                    questions=await ai.generate_questions(doc_id,topic,misconceptions,m['id'],evidence,stems,parent_id=parent_id)
                finally:
                    with connect(db_path) as conn:
                        conn.execute('INSERT INTO generation_runs VALUES (?,?,?,?,?,?)',(uid('gen'),doc_id,topic['id'],m['id'],dumps(ai.last_generation_stats),now()))
                with connect(db_path) as conn:
                    for q in questions:
                        new_questions.append(persist_question(conn,doc_id,topic['id'],q,'live' if only_target else 'bank',parent_id))
                    total=conn.execute('SELECT COUNT(DISTINCT target_misconception_id) FROM questions WHERE topic_id=?',(topic['id'],)).fetchone()[0]
                    conn.execute('UPDATE topics SET status=?,reason=? WHERE id=?',('ready' if total>=3 else 'generating',None,topic['id']))
            with connect(db_path) as conn:
                total=conn.execute('SELECT COUNT(DISTINCT target_misconception_id) FROM questions WHERE topic_id=?',(topic['id'],)).fetchone()[0]
                conn.execute('UPDATE topics SET status=?,reason=? WHERE id=?',('ready' if total>=3 else 'insufficient',None if total>=3 else '多次生成未获得足够的合格题目',topic['id']))
            return new_questions

    async def generation_job(jid,doc_id):
        job_update(jid,'running',0)
        with connect(db_path) as conn:
            topics=[dict(t) for t in conn.execute('SELECT * FROM topics WHERE document_id=?',(doc_id,))]
            conn.execute("UPDATE documents SET status='generating',error=NULL WHERE id=?",(doc_id,))
        finished=0; errors=[]
        async def run_topic(t):
            nonlocal finished
            try: await build_topic(t)
            except asyncio.CancelledError: raise
            except Exception as exc:
                err=safe_error(exc); errors.append(err)
                with connect(db_path) as conn:
                    total=conn.execute('SELECT COUNT(DISTINCT target_misconception_id) FROM questions WHERE topic_id=?',(t['id'],)).fetchone()[0]
                    conn.execute('UPDATE topics SET status=?,reason=? WHERE id=?',('ready' if total>=3 else 'insufficient',err,t['id']))
            finished+=1; job_update(jid,'running',round(100*finished/max(len(topics),1)))
        await asyncio.gather(*(run_topic(t) for t in topics))
        with connect(db_path) as conn:
            ready=conn.execute("SELECT COUNT(*) FROM topics WHERE document_id=? AND status='ready'",(doc_id,)).fetchone()[0]
            conn.execute('UPDATE documents SET status=?,error=? WHERE id=?',('ready' if ready else 'failed','；'.join(errors) or None,doc_id))
        job_update(jid,'completed' if not errors else 'failed',100,'；'.join(errors) or None)

    async def variant_job(jid,doc_id,sid,original,mid):
        try:
            job_update(jid,'running',10)
            with connect(db_path) as conn: topic=dict(conn.execute('SELECT * FROM topics WHERE id=?',(original['topic_id'],)).fetchone())
            questions=await build_topic(topic,mid,original['id'])
            if not questions: raise RuntimeError('暂时无法生成合格的新题，请先回看原文')
            with connect(db_path) as conn:
                q=questions[0]; quiz_id=save_quiz(conn,sid,doc_id,[q])
                result=dict(quiz_id=quiz_id,question=public_question(conn,q,is_variant=True))
                conn.execute("UPDATE jobs SET status='completed',progress=100,result=? WHERE id=?",(dumps(result),jid))
        except asyncio.CancelledError: raise
        except Exception as exc: job_update(jid,'failed',error=safe_error(exc))

    def new_job(conn,doc_id,sid,kind,payload=None):
        jid=uid('job')
        conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)',(jid,doc_id,sid,kind,'queued',0,None,dumps(payload or {}),None,now()))
        return jid

    def guard_active(conn,doc_id):
        if conn.execute("SELECT 1 FROM jobs WHERE document_id=? AND status IN ('running','queued')",(doc_id,)).fetchone(): raise HTTPException(409,'资料已有处理任务，请等待完成')

    @app.get('/api/health')
    def health(): return dict(mode='live' if live else 'demo',version='2.0.0',upload_enabled=uploads)

    @app.get('/api/documents')
    def documents(request:Request):
        with connect(db_path) as conn:
            return {'documents':[doc_summary(conn,r) for r in conn.execute('SELECT * FROM documents WHERE is_demo=1 OR session_id=? ORDER BY is_demo DESC,created_at DESC',(request.state.session_id,))]}

    @app.get('/api/documents/{doc_id}')
    def document(doc_id:str,request:Request):
        with connect(db_path) as conn:
            d=doc_owned(conn,doc_id,request.state.session_id)
            result=doc_summary(conn,d); topics=[]
            for t in conn.execute('SELECT * FROM topics WHERE document_id=? ORDER BY rowid',(doc_id,)):
                ms=[]
                for m in conn.execute('SELECT * FROM misconceptions WHERE topic_id=? ORDER BY rowid',(t['id'],)):
                    ms.append(dict(id=m['id'],label=m['label'],description=m['description'],refute_sentence_ids=json.loads(m['refute_sentence_ids']),question_count=conn.execute('SELECT COUNT(*) FROM questions WHERE target_misconception_id=?',(m['id'],)).fetchone()[0]))
                topics.append({**{k:t[k] for k in ('id','title','description','status','reason')},'question_count':conn.execute('SELECT COUNT(*) FROM questions WHERE topic_id=?',(t['id'],)).fetchone()[0],'misconceptions':ms})
            metadata=conn.execute('SELECT warnings FROM extraction_metadata WHERE document_id=?',(doc_id,)).fetchone()
            return {**result,'topics':topics,'samples':[dict(r) for r in conn.execute('SELECT page_index,text FROM pages WHERE document_id=? ORDER BY page_index LIMIT 3',(doc_id,))],'jobs':[dict(r) for r in conn.execute('SELECT id,status,progress,error FROM jobs WHERE document_id=? ORDER BY created_at DESC LIMIT 5',(doc_id,))],'candidate_topics':json.loads(d['candidate_topics']),'error':d['error'],'warnings':json.loads(metadata[0]) if metadata else []}

    @app.get('/api/documents/{doc_id}/ai')
    def ai_overview(doc_id:str,request:Request):
        from backend.ai.client import LLMClient, PROMPT_VERSION
        with connect(db_path) as conn:
            document=doc_owned(conn,doc_id,request.state.session_id)
            client=LLMClient()
            counts={table:conn.execute(f'SELECT COUNT(*) FROM {table} WHERE document_id=?',(doc_id,)).fetchone()[0]
                    for table in ('pages','sentences','chunks','questions')}
            chunks=[dict(id=row['id'],page_index=row['page_index'],text=row['text'],sentence_count=len(json.loads(row['sentence_ids'])))
                    for row in conn.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY page_index LIMIT 8',(doc_id,))]
            misconceptions=[]
            for row in conn.execute('SELECT m.* FROM misconceptions m JOIN topics t ON m.topic_id=t.id WHERE t.document_id=? ORDER BY m.rowid',(doc_id,)):
                refs=json.loads(row['refute_sentence_ids'])
                text=''.join(conn.execute('SELECT text FROM sentences WHERE id=? AND document_id=?',(sid,doc_id)).fetchone()[0] for sid in refs)
                misconceptions.append(dict(id=row['id'],label=row['label'],refutation=text))
            call_summary=conn.execute('SELECT COUNT(*) AS total,COALESCE(SUM(input_tokens+output_tokens),0) AS tokens,COALESCE(AVG(latency_ms),0) AS average_ms FROM llm_calls WHERE document_id=?',(doc_id,)).fetchone()
            records=[dict(row) for row in conn.execute('SELECT id,purpose,model,input_tokens,output_tokens,status FROM llm_calls WHERE document_id=? ORDER BY created_at DESC LIMIT 20',(doc_id,))]
            stats=[json.loads(row[0]) for row in conn.execute('SELECT stats FROM generation_runs WHERE document_id=?',(doc_id,))]
            return dict(is_demo=bool(document['is_demo']),prompt_version=PROMPT_VERSION,
                        models=[dict(role='生成模型',name=client.model,configured=bool(client.api_key)),
                                dict(role='复核模型',name=client.review_model,configured=bool(client.review_key)),
                                dict(role='向量模型',name=client.embed_model,configured=bool(client.embed_key))],
                        counts=counts,chunks=chunks,misconceptions=misconceptions,
                        generation=dict(accepted=sum(s.get('accepted',0) for s in stats),
                                        rejected=sum(s.get('code_rejected',0)+s.get('review_rejected',0) for s in stats),
                                        repairs=sum(s.get('repair_attempted',0) for s in stats)),
                        calls={**dict(call_summary),'records':records})

    @app.post('/api/documents',status_code=202)
    async def upload(request:Request,file:UploadFile=File(...)):
        if not uploads: raise HTTPException(403,'当前模式未开放上传；请配置模型凭证并开启上传')
        content=await file.read(10*1024*1024+1)
        await file.close()
        if len(content)>10*1024*1024: raise HTTPException(413,'PDF 不能超过 10 MB')
        if not content.startswith(b'%PDF-'): raise HTTPException(400,'文件不是有效的 PDF')
        sha=hashlib.sha256(content).hexdigest(); sid=request.state.session_id
        stored_path = None
        try:
            with connect(db_path) as conn:
                existing=conn.execute('SELECT * FROM documents WHERE sha256=? AND session_id=?',(sha,sid)).fetchone()
                if existing: return doc_summary(conn,existing)
                recent=conn.execute("SELECT COUNT(*) FROM jobs WHERE session_id=? AND created_at>?",(sid,datetime.fromtimestamp(datetime.now(timezone.utc).timestamp()-60,timezone.utc).isoformat())).fetchone()[0]
                if recent>=5: raise HTTPException(429,'操作过于频繁，请一分钟后再试')
                doc_id=uid('doc')
                stored_path=store_upload(data_dir/'uploads',doc_id,content)
                name=Path((file.filename or '未命名.pdf').replace('\\','/')).name[:200]
                conn.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)',(doc_id,sid,0,name,sha,'parsing',0,'用户上传 · 等待模型质检','[]',None,now()))
                jid=new_job(conn,doc_id,sid,'ingest')
        except Exception as exc:
            if stored_path is not None:
                with suppress(OSError):
                    stored_path.unlink(missing_ok=True)
            if isinstance(exc,OSError):
                raise HTTPException(503,'资料未能保存，请检查服务器磁盘空间后重试') from exc
            raise
        start_task(parse_job(jid,doc_id))
        return dict(id=doc_id,status='parsing',job_id=jid)

    @app.put('/api/documents/{doc_id}/topics')
    async def confirm_topics(doc_id:str,body:TopicsInput,request:Request):
        sid=request.state.session_id
        if not live: raise HTTPException(403,'演示模式不支持生成')
        with connect(db_path) as conn:
            doc_owned(conn,doc_id,sid,True); guard_active(conn,doc_id)
            if not conn.execute('SELECT 1 FROM sentences WHERE document_id=?',(doc_id,)).fetchone(): raise HTTPException(409,'请等待资料解析完成')
            if conn.execute('SELECT 1 FROM topics WHERE document_id=?',(doc_id,)).fetchone(): raise HTTPException(409,'知识点已确认，请使用继续生成')
            if len({t.title.strip() for t in body.topics})!=len(body.topics) or any(not t.title.strip() for t in body.topics): raise HTTPException(422,'知识点名称不能为空或重复')
            for t in body.topics:
                conn.execute('INSERT INTO topics VALUES (?,?,?,?,?,?,?,?)',(uid('topic'),doc_id,t.title.strip(),t.description,'confirmed','generating',None,'[]'))
            jid=new_job(conn,doc_id,sid,'generate')
        start_task(generation_job(jid,doc_id)); return {'job_id':jid}

    @app.post('/api/documents/{doc_id}/resume')
    async def resume(doc_id:str,request:Request):
        if not live: raise HTTPException(403,'演示模式不支持生成')
        sid=request.state.session_id
        with connect(db_path) as conn:
            d=doc_owned(conn,doc_id,sid,True); guard_active(conn,doc_id)
            has_topics=conn.execute('SELECT 1 FROM topics WHERE document_id=?',(doc_id,)).fetchone()
            jid=new_job(conn,doc_id,sid,'generate' if has_topics else 'ingest')
        start_task(generation_job(jid,doc_id) if has_topics else parse_job(jid,doc_id)); return {'job_id':jid}

    @app.get('/api/documents/{doc_id}/sentences')
    def sentences(doc_id:str,request:Request,ids:str=''):
        requested=list(dict.fromkeys(ids.split(',')))
        if not ids or len(requested)>20: raise HTTPException(422,'请提供 1–20 个句子 ID')
        with connect(db_path) as conn:
            doc=doc_owned(conn,doc_id,request.state.session_id); found=[]
            for sentence_id in requested:
                s=conn.execute('SELECT * FROM sentences WHERE id=? AND document_id=?',(sentence_id,doc_id)).fetchone()
                if not s: raise HTTPException(404,'证据句不存在于当前资料')
                context=[dict(id=r['id'],text=r['text'],highlight=r['id'] in requested) for r in conn.execute('SELECT id,text FROM sentences WHERE document_id=? AND page_index=? AND ord BETWEEN ? AND ? ORDER BY ord',(doc_id,s['page_index'],s['ord']-1,s['ord']+1))]
                found.append({**{k:s[k] for k in ('id','text','page_index','char_start','char_end')},'context':context})
            return dict(sentences=found,document_name=doc['name'])

    @app.post('/api/quizzes',status_code=201)
    def create_quiz(body:QuizInput,request:Request):
        sid=request.state.session_id
        with connect(db_path) as conn:
            doc_owned(conn,body.document_id,sid)
            topics={r[0] for r in conn.execute('SELECT id FROM topics WHERE document_id=?',(body.document_id,))}
            if not set(body.topic_ids)<=topics: raise HTTPException(422,'知识点不属于当前资料')
            placeholders=','.join('?' for _ in body.topic_ids)
            candidates=[dict(q) for q in conn.execute(f'SELECT * FROM questions WHERE topic_id IN ({placeholders}) AND id NOT IN (SELECT question_id FROM attempts WHERE session_id=?)',(*body.topic_ids,sid))]
            secrets.SystemRandom().shuffle(candidates)
            selected=[]; retest_ids=[]; mids=set()
            for m in review_data(conn,sid)['misconceptions']:
                if m['status']=='corrected': continue
                q=next((q for q in candidates if q['target_misconception_id']==m['id']),None)
                if q:
                    selected.append(q); retest_ids.append(q['id']); mids.add(q['target_misconception_id']); break
            for q in candidates:
                if len(selected)==3: break
                if q['target_misconception_id'] in mids: continue
                selected.append(q); mids.add(q['target_misconception_id'])
            if len(selected)<3: raise HTTPException(409,'当前知识点未做过的题或不同误区不足 3 个，请选择更多知识点')
            quiz_id=save_quiz(conn,sid,body.document_id,selected,retest_ids)
            return dict(id=quiz_id,questions=[public_question(conn,q,q['id'] in retest_ids) for q in selected])

    @app.get('/api/quizzes/{quiz_id}')
    def quiz(quiz_id:str,request:Request):
        with connect(db_path) as conn:
            quiz=quiz_owned(conn,quiz_id,request.state.session_id); result=[]
            question_ids=json.loads(quiz['question_ids'])
            for qid in question_ids:
                q=owned_question(conn,qid,request.state.session_id)
                public=public_question(conn,q,qid in json.loads(quiz['retest_ids']),is_variant=len(question_ids)==1)
                a=conn.execute('SELECT * FROM attempts WHERE session_id=? AND question_id=?',(request.state.session_id,qid)).fetchone()
                if a: public['result']=result_for(conn,q,a)
                result.append(public)
            return dict(id=quiz_id,questions=result)

    @app.post('/api/questions/{question_id}/attempts')
    def attempt(question_id:str,body:AttemptInput,request:Request):
        sid=request.state.session_id
        with connect(db_path) as conn:
            conn.execute('BEGIN IMMEDIATE')
            quiz=quiz_owned(conn,body.quiz_id,sid)
            if question_id not in json.loads(quiz['question_ids']): raise HTTPException(403,'题目不属于本次练习')
            q=owned_question(conn,question_id,sid)
            existing=conn.execute('SELECT * FROM attempts WHERE session_id=? AND question_id=?',(sid,question_id)).fetchone()
            if existing: return result_for(conn,q,existing,True)
            p=json.loads(q['payload']); option=next(o for o in p['options'] if o['letter']==body.selected)
            correct=body.selected==q['answer_letter']
            mid=None if correct else option['misconception_id']
            conn.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)',(uid('attempt'),sid,question_id,body.quiz_id,body.selected,int(correct),body.confidence,mid,q['prompt_version'],now()))
            a=conn.execute('SELECT * FROM attempts WHERE session_id=? AND question_id=?',(sid,question_id)).fetchone()
            return result_for(conn,q,a)

    @app.post('/api/questions/{question_id}/variant')
    async def variant(question_id:str,request:Request):
        sid=request.state.session_id
        with connect(db_path) as conn:
            original=owned_question(conn,question_id,sid)
            a=conn.execute('SELECT * FROM attempts WHERE session_id=? AND question_id=?',(sid,question_id)).fetchone()
            if not a or a['correct']: raise HTTPException(409,'请先完成这道题；答错后可针对误区补练')
            mid=a['hit_misconception_id']
            candidates=[dict(q) for q in conn.execute('SELECT * FROM questions WHERE target_misconception_id=? AND id<>? AND id NOT IN (SELECT question_id FROM attempts WHERE session_id=?)',(mid,question_id,sid))]
            from backend.ai.validate_question import stem_similarity
            candidates=[q for q in candidates if stem_similarity(json.loads(q['payload'])['stem'],json.loads(original['payload'])['stem'])<=0.6]
            if candidates:
                q=secrets.choice(candidates); quiz_id=save_quiz(conn,sid,original['document_id'],[q])
                return dict(quiz_id=quiz_id,question=public_question(conn,q,is_variant=True))
            is_demo=conn.execute('SELECT is_demo FROM documents WHERE id=?',(original['document_id'],)).fetchone()[0]
            if not live or is_demo: raise HTTPException(409,'当前误区库存已练完，暂时无法提供新题，请回看原文')
            guard_active(conn,original['document_id'])
            recent=conn.execute("SELECT COUNT(*) FROM jobs WHERE session_id=? AND created_at>?",(sid,datetime.fromtimestamp(datetime.now(timezone.utc).timestamp()-60,timezone.utc).isoformat())).fetchone()[0]
            if recent>=5: raise HTTPException(429,'操作过于频繁，请一分钟后再试')
            jid=new_job(conn,original['document_id'],sid,'variant',dict(question_id=question_id,misconception_id=mid))
        start_task(variant_job(jid,original['document_id'],sid,original,mid)); return {'job_id':jid}

    @app.get('/api/jobs/{job_id}')
    def job(job_id:str,request:Request):
        with connect(db_path) as conn:
            j=conn.execute('SELECT * FROM jobs WHERE id=? AND session_id=?',(job_id,request.state.session_id)).fetchone()
            if not j: raise HTTPException(404,'任务不存在或无权访问')
            return {**{k:j[k] for k in ('id','status','progress','error')},**(json.loads(j['result']) if j['result'] else {})}

    @app.get('/api/review')
    def review(request:Request):
        with connect(db_path) as conn: return review_data(conn,request.state.session_id)

    dist=ROOT/'frontend'/'dist'
    if dist.exists():
        app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
        @app.get('/favicon.svg',include_in_schema=False)
        def favicon():
            return FileResponse(dist/'favicon.svg',media_type='image/svg+xml')
        @app.get('/{path:path}')
        def spa(path:str):
            if path=='api' or path.startswith('api/'): raise HTTPException(404,'接口不存在')
            return FileResponse(dist/'index.html')
    return app

app=create_app()
