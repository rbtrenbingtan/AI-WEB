import {useCallback,useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,BookOpen,Check,ChevronRight,CircleHelp,FileText,Layers3,Loader2,PanelLeftClose,RefreshCw,ShieldCheck,Sparkles,Target,X} from 'lucide-react';
import {api,post} from './api';
import type {Candidate,Confidence,DocumentSummary,Evidence,Health,Job,Page,Quiz,Result,Review,StudyDocument} from './types';
import {DocumentsPage} from './DocumentsPage';
import {PracticePage} from './PracticePage';
import {ReviewPage} from './ReviewPage';

const nav = [{id:'documents' as const,label:'学习资料',icon:BookOpen,sub:'从你的课程出发'},{id:'practice' as const,label:'证据练习',icon:Target,sub:'理解，而不只是答对'},{id:'review' as const,label:'我的复习',icon:Layers3,sub:'看见理解的变化'}];
function initialPage():Page {return ['documents','practice','review'].includes(location.hash.slice(1))?location.hash.slice(1) as Page:'documents';}
function storedQuiz():string|null {try{return localStorage.getItem('zhizheng.quiz');}catch{return null;}}
function saveQuiz(id:string){try{localStorage.setItem('zhizheng.quiz',id);}catch{/* optional persistence */}}
const errorMessage=(e:unknown)=>e instanceof Error?e.message:'操作未完成，请稍后再试。';

export default function App(){
  const [page,setPage]=useState<Page>(initialPage), [health,setHealth]=useState<Health|null>(null);
  const [documents,setDocuments]=useState<DocumentSummary[]>([]),[docId,setDocId]=useState(''),[doc,setDoc]=useState<StudyDocument|null>(null),[topics,setTopics]=useState<string[]>([]);
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [quiz,setQuiz]=useState<Quiz|null>(null),[index,setIndex]=useState(0),[parentQuiz,setParentQuiz]=useState<{quiz:Quiz;index:number}|null>(null);
  const [review,setReview]=useState<Review|null>(null),[reviewLoading,setReviewLoading]=useState(false);
  const [evidence,setEvidence]=useState<Evidence|null>(null),[evidenceOpen,setEvidenceOpen]=useState(false),[evidenceLoading,setEvidenceLoading]=useState(false),[evidenceError,setEvidenceError]=useState('');
  const [variantJob,setVariantJob]=useState<string|null>(null),[jobStatus,setJobStatus]=useState(''),[mobileNav,setMobileNav]=useState(false);
  const mounted=useRef(true),docRequest=useRef(0),evidenceRequest=useRef(0);
  const go=useCallback((next:Page)=>{setPage(next);location.hash=next;setMobileNav(false);setError('');window.scrollTo({top:0,behavior:'smooth'});},[]);
  const refreshDocuments=useCallback(async()=>{const data=await api<{documents:DocumentSummary[]}>('/documents');setDocuments(data.documents);return data.documents;},[]);
  const fetchReview=useCallback(async()=>{setReviewLoading(true);try{setReview(await api<Review>('/review'));}catch(e){setError(errorMessage(e));}finally{setReviewLoading(false);}},[]);
  const refreshDoc=useCallback(async(id:string,reset=false)=>{
    const request=++docRequest.current;
    const data=await api<StudyDocument>(`/documents/${encodeURIComponent(id)}`);
    if(request!==docRequest.current||!mounted.current)return;
    setDoc(data);
    if(reset)setTopics(data.topics.filter(t=>t.question_count>=3&&t.status==='ready').slice(0,1).map(t=>t.id));
  },[]);
  useEffect(()=>{mounted.current=true;let active=true;
    (async()=>{try{
      const [h,list]=await Promise.all([api<Health>('/health'),api<{documents:DocumentSummary[]}>('/documents')]);
      if(!active)return;setHealth(h);setDocuments(list.documents);if(list.documents[0])setDocId(list.documents[0].id);
      const saved=storedQuiz();if(saved){try{const q=await api<Quiz>(`/quizzes/${encodeURIComponent(saved)}`);if(active){setQuiz(q);setIndex(Math.max(0,q.questions.findIndex(x=>!x.result)));}}catch{try{localStorage.removeItem('zhizheng.quiz');}catch{/* no persistence */}}}
    }catch(e){if(active)setError(errorMessage(e));}finally{if(active)setLoading(false);}})();
    const onHash=()=>setPage(initialPage());window.addEventListener('hashchange',onHash);
    return()=>{active=false;mounted.current=false;window.removeEventListener('hashchange',onHash);};
  },[]);
  useEffect(()=>{if(!docId)return;setDoc(null);refreshDoc(docId,true).catch(e=>setError(errorMessage(e)));},[docId,refreshDoc]);
  useEffect(()=>{if(!docId||!doc)return;const active=doc.jobs?.some(j=>['pending','queued','running'].includes(j.status))||['parsing','indexing','generating','processing'].includes(doc.status);if(!active)return;
    const timer=setInterval(()=>{refreshDoc(docId).catch(e=>setError(errorMessage(e)));refreshDocuments().catch(()=>{});},2200);return()=>clearInterval(timer);
  },[docId,doc?.status,doc?.jobs,refreshDoc,refreshDocuments]);
  useEffect(()=>{if(page==='review')void fetchReview();},[page,fetchReview]);
  useEffect(()=>{if(!variantJob)return;let active=true;const check=async()=>{try{const job=await api<Job>(`/jobs/${encodeURIComponent(variantJob)}`);if(!active)return;
    setJobStatus(job.status==='running'?'正在生成并复核新题，请稍候…':'补练任务已排队，请稍候…');
    if(job.question&&job.quiz_id){const next={id:job.quiz_id,questions:[job.question]};setQuiz(next);setIndex(0);saveQuiz(next.id);setVariantJob(null);setBusy('');setJobStatus('');go('practice');}
    else if(['failed','interrupted','completed','done','succeeded','cancelled'].includes(job.status)){setVariantJob(null);setBusy('');setError(job.error||'暂时无法生成合格的新题，请先回顾原文。');setJobStatus('');}
  }catch(e){if(active){setVariantJob(null);setBusy('');setError(errorMessage(e));}}};void check();const timer=setInterval(check,2000);return()=>{active=false;clearInterval(timer);};},[variantJob,go]);
  const selectDocument=(id:string)=>{setDocId(id);setError('');setNotice('');};
  async function startQuiz(){if(!docId||!topics.length)return;setBusy('start');setError('');try{const q=await post<Quiz>('/quizzes',{document_id:docId,topic_ids:topics});setQuiz(q);setIndex(0);setParentQuiz(null);saveQuiz(q.id);go('practice');}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
  async function submit(selected:string,confidence:Confidence){if(!quiz)return;const question=quiz.questions[index];setBusy('submit');setError('');try{const result=await post<Result>(`/questions/${encodeURIComponent(question.id)}/attempts`,{quiz_id:quiz.id,selected,confidence});setQuiz(q=>q&&({...q,questions:q.questions.map(v=>v.id===question.id?{...v,result}:v)}));}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
  async function variant(questionId:string){setBusy('variant');setError('');try{const result=await post<{quiz_id?:string;question?:Quiz['questions'][0];job_id?:string}>(`/questions/${encodeURIComponent(questionId)}/variant`);if(quiz)setParentQuiz({quiz,index});
    if(result.question&&result.quiz_id){const next={id:result.quiz_id,questions:[result.question]};setQuiz(next);setIndex(0);saveQuiz(next.id);go('practice');setBusy('');}
    else if(result.job_id){setVariantJob(result.job_id);setJobStatus('补练任务已排队，完成后会自动展示。');}
    else {setBusy('');setError('服务没有返回补练题，请稍后重试。');}
  }catch(e){setBusy('');setError(errorMessage(e));}}
  async function showEvidence(documentId:string,ids:string[]){setEvidenceOpen(true);setEvidence(null);setEvidenceError('');setEvidenceLoading(true);const request=++evidenceRequest.current;try{const result=await api<Evidence>(`/documents/${encodeURIComponent(documentId)}/sentences?ids=${encodeURIComponent([...new Set(ids)].join(','))}`);if(request===evidenceRequest.current)setEvidence(result);}catch(e){if(request===evidenceRequest.current)setEvidenceError(errorMessage(e));}finally{if(request===evidenceRequest.current)setEvidenceLoading(false);}}
  async function upload(file:File){if(file.size>10*1024*1024){setError('文件超过 10 MB，请压缩或拆分后再上传。');return;}if(!file.name.toLowerCase().endsWith('.pdf')){setError('请选择文本型 PDF 文件。');return;}setBusy('upload');setError('');try{const body=new FormData();body.append('file',file);const result=await api<{id:string}>('/documents',{method:'POST',body});await refreshDocuments();setDocId(result.id);setNotice('资料已收到，正在后台解析。你可以在这里查看进度。');}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
  async function confirmTopics(candidates:Candidate[]){if(!doc)return;setBusy('topics');setError('');try{await api(`/documents/${encodeURIComponent(doc.id)}/topics`,{method:'PUT',body:JSON.stringify({topics:candidates})});await refreshDoc(doc.id);setNotice('知识点已确认，正在后台准备证据与练习。');}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
  async function resume(){if(!doc)return;setBusy('resume');setError('');try{await post(`/documents/${encodeURIComponent(doc.id)}/resume`);await refreshDoc(doc.id);setNotice('已恢复处理，将从现有进度继续。');}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
  async function revisit(quizId:string,questionId:string){setBusy('revisit');setError('');try{const q=await api<Quiz>(`/quizzes/${encodeURIComponent(quizId)}`);setQuiz(q);setIndex(Math.max(0,q.questions.findIndex(x=>x.id===questionId)));setParentQuiz(null);saveQuiz(q.id);go('practice');}catch(e){setError(errorMessage(e));}finally{setBusy('');}}
  const returnParent=()=>{if(!parentQuiz)return;setQuiz(parentQuiz.quiz);setIndex(parentQuiz.index);saveQuiz(parentQuiz.quiz.id);setParentQuiz(null);setError('');};
  const pageTitle=nav.find(n=>n.id===page)!.label;
  return <div className="app-shell">
    {mobileNav&&<button className="nav-scrim" aria-label="关闭导航" onClick={()=>setMobileNav(false)}/>}
    <aside className={`sidebar ${mobileNav?'open':''}`}>
      <a className="brand" href="#documents" onClick={()=>go('documents')} aria-label="知证学伴首页"><span className="brand-mark"><BookOpen size={25}/><Check size={17}/></span><span>知证学伴<small>ZHIZHENG · STUDY</small></span></a>
      <div className="sidebar-label">我的学习空间</div>
      <nav aria-label="主导航">{nav.map(n=><button key={n.id} className={`nav-item ${page===n.id?'active':''}`} onClick={()=>go(n.id)} aria-current={page===n.id?'page':undefined}><n.icon size={21}/><span>{n.label}<small>{n.sub}</small></span>{page===n.id&&<span className="nav-dot"/>}</button>)}</nav>
      <div className="sidebar-note"><div className="tiny-symbol">“</div><p>每一次理解，<br/>都有据可循。</p><div className="note-line"/><small>回到原文，找到证据。<br/>从一个误区，走向真正理解。</small><div className="note-art"><span/><span/><span/></div></div>
      <div className="session-note"><ShieldCheck size={17}/><p>你的浏览器，就是学习空间<small>记录绑定当前会话，清除 Cookie 后将无法找回。</small></p></div>
      <div className="sidebar-bottom"><span className="status-dot"/>知证学伴<span>V{health?.version||'1.0'}</span></div>
    </aside>
    <main className="main-content">
      <header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" onClick={()=>setMobileNav(v=>!v)} aria-label="展开导航"><PanelLeftClose size={22}/></button><span>学习空间</span><ChevronRight size={15}/><strong>{pageTitle}</strong></div><div className="topbar-right"><span className={`mode-pill ${health?.mode==='live'?'live':''}`}><span/>{health?health.mode==='live'?'实时模式':'演示模式 · 无需 Key':'连接服务中'}</span><span className="avatar" title="当前浏览器会话">学</span></div></header>
      <div className="page-content">
        {error&&<div className="alert error" role="alert"><CircleHelp size={19}/><span>{error}</span><button aria-label="关闭错误提示" onClick={()=>setError('')}><X size={17}/></button></div>}
        {notice&&page==='documents'&&<div className="alert info" role="status"><Check size={18}/><span>{notice}</span><button aria-label="关闭提示" onClick={()=>setNotice('')}><X size={17}/></button></div>}
        {loading?<div className="loading-state"><Loader2 className="spin" size={30}/><h2>正在打开学习空间</h2><p>载入资料与练习记录…</p></div>:<>
          {page==='documents'&&<DocumentsPage documents={documents} document={doc} selectedId={docId} selectedTopics={topics} health={health} busy={busy} onSelect={selectDocument} onTopics={setTopics} onStart={startQuiz} onUpload={upload} onConfirmTopics={confirmTopics} onResume={resume} onRefresh={()=>{refreshDocuments().then(list=>{if(!docId&&list[0])setDocId(list[0].id);return docId?refreshDoc(docId):undefined;}).catch(e=>setError(errorMessage(e)));}}/>}
          {page==='practice'&&<PracticePage quiz={quiz} index={index} busy={busy} jobStatus={jobStatus} hasParent={!!parentQuiz} onSubmit={submit} onVariant={variant} onEvidence={showEvidence} onIndex={i=>{setIndex(i);setError('');window.scrollTo({top:0,behavior:'smooth'});}} onReturn={returnParent} onDocuments={()=>go('documents')} onReview={()=>go('review')}/>}
          {page==='review'&&<ReviewPage review={review} loading={reviewLoading} busy={busy} onRefresh={fetchReview} onPractice={()=>go('documents')} onEvidence={showEvidence} onVariant={variant} onRevisit={revisit}/>}
        </>}
        <footer className="page-footer"><span><ShieldCheck size={14}/> 证据来自课程原文 · 反馈仅描述本次练习表现</span><span>以证据理解，以练习巩固</span></footer>
      </div>
    </main>
    {evidenceOpen&&<EvidenceDrawer evidence={evidence} loading={evidenceLoading} error={evidenceError} onClose={()=>setEvidenceOpen(false)}/>}
  </div>;
}

function EvidenceDrawer({evidence,loading,error,onClose}:{evidence:Evidence|null;loading:boolean;error:string;onClose:()=>void}){
  const closeRef=useRef<HTMLButtonElement>(null);
  const panelRef=useRef<HTMLElement>(null);
  useEffect(()=>{const previous=document.activeElement as HTMLElement;closeRef.current?.focus();const overflow=document.body.style.overflow;document.body.style.overflow='hidden';const key=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose();if(event.key==='Tab'){const elements=panelRef.current?.querySelectorAll<HTMLElement>('button,a[href],[tabindex="0"]');if(!elements?.length)return;const first=elements[0],last=elements[elements.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}};window.addEventListener('keydown',key);return()=>{document.body.style.overflow=overflow;window.removeEventListener('keydown',key);previous?.focus();};},[onClose]);
  return <div className="drawer-layer"><button className="drawer-scrim" onClick={onClose} aria-label="关闭原文抽屉"/><section className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title" ref={panelRef}><div className="drawer-header"><span className="eyebrow">BACK TO THE SOURCE</span><button className="icon-button" ref={closeRef} onClick={onClose} aria-label="关闭原文"><X size={22}/></button><h2 id="evidence-title">让原文来解释。</h2><p><FileText size={16}/>{evidence?.document_name||'课程原文'}</p></div><div className="drawer-body">{loading?<div className="loading-state"><Loader2 className="spin"/>正在查找证据句…</div>:error?<div className="alert error">{error}</div>:evidence?.sentences.length?evidence.sentences.map(s=><article className="source-page" key={s.id}><div className="source-page-head"><span>物理第 {s.page_index} 页</span><code>{s.id}</code></div><div className="source-context">{s.context?.length?s.context.map(c=><p key={c.id} className={c.highlight||c.id===s.id?'highlighted':''}>{c.highlight||c.id===s.id?<mark>{c.text}</mark>:c.text}</p>):<p><mark>{s.text}</mark></p>}</div><div className="source-foot"><Check size={14}/> 原文直接回填 · 保留句子编号</div></article>):<div className="empty-state"><BookOpen/><p>这道题暂时没有可展示的证据句。</p></div>}<p className="drawer-note"><ShieldCheck size={16}/>引文由服务端按句子编号读取。句子存在不等于语义必然支持答案；如有疑问，请结合上下文判断。</p></div></section></div>;
}
