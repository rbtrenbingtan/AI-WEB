import { useCallback, useEffect, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  CircleHelp,
  Cpu,
  Layers3,
  Loader2,
  Menu,
  RefreshCw,
  Target,
  X,
} from "lucide-react";
import { api, post, ApiError } from "./api";
import { readSession, writeSession } from "./persistence";
import type { QuizLocation } from "./persistence";
import type {
  Candidate,
  Confidence,
  DocumentSummary,
  Evidence,
  Health,
  Job,
  Page,
  Quiz,
  Result,
  Review,
  StudyDocument,
} from "./types";
import { DocumentsPage } from "./DocumentsPage";
import { PracticePage } from "./PracticePage";
import { ReviewPage } from "./ReviewPage";
import { AIWorkbench } from "./AIWorkbench";
import { EvidenceDrawer } from "./EvidencePanel";

const nav = [
  { id: "documents" as const, label: "学习资料", icon: BookOpen },
  { id: "practice" as const, label: "证据练习", icon: Target },
  { id: "review" as const, label: "我的复习", icon: Layers3 },
  { id: "ai" as const, label: "AI 工作台", icon: Cpu },
];
function initialPage(): Page {
  return ["documents", "practice", "review", "ai"].includes(
    location.hash.slice(1),
  )
    ? (location.hash.slice(1) as Page)
    : "documents";
}

const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : "操作未完成，请稍后再试。";

export default function App() {
  const [page, setPage] = useState<Page>(initialPage),
    [health, setHealth] = useState<Health | null>(null);
  const [documents, setDocuments] = useState<DocumentSummary[]>([]),
    [docId, setDocId] = useState(""),
    [doc, setDoc] = useState<StudyDocument | null>(null),
    [topics, setTopics] = useState<string[]>([]);
  const [loading, setLoading] = useState(true),
    [busy, setBusy] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [quiz, setQuiz] = useState<Quiz | null>(null),
    [index, setIndex] = useState(0),
    [parents, setParents] = useState<QuizLocation[]>([]);
  const [review, setReview] = useState<Review | null>(null),
    [reviewLoading, setReviewLoading] = useState(false);
  const [evidence, setEvidence] = useState<Evidence | null>(null),
    [evidenceOpen, setEvidenceOpen] = useState(false),
    [evidenceLoading, setEvidenceLoading] = useState(false),
    [evidenceError, setEvidenceError] = useState("");
  const [variantJob, setVariantJob] = useState<string | null>(null),
    [jobStatus, setJobStatus] = useState(""),
    [mobileNav, setMobileNav] = useState(false);
  const mounted = useRef(true),
    docRequest = useRef(0),
    evidenceRequest = useRef(0);
  const [initialized, setInitialized] = useState(false),
    [bootRetry, setBootRetry] = useState(0),
    [jobPaused, setJobPaused] = useState(false),
    [jobRetry, setJobRetry] = useState(0);
  const errorRef = useRef<HTMLDivElement>(null),
    sidebarRef = useRef<HTMLElement>(null),
    mainRef = useRef<HTMLElement>(null),
    menuRef = useRef<HTMLButtonElement>(null),
    navWasOpen = useRef(false);
  const closeEvidence = useCallback(() => setEvidenceOpen(false), []);
  useEffect(() => {
    if (error) {
      errorRef.current?.focus();
      errorRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [error]);
  useEffect(() => {
    if (initialized)
      writeSession({
        current: quiz ? { id: quiz.id, index } : null,
        parents,
        pendingJob: variantJob,
      });
  }, [initialized, quiz?.id, index, parents, variantJob]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 760px)");
    const sync = () => {
      if (sidebarRef.current)
        sidebarRef.current.inert = query.matches && !mobileNav;
      if (mainRef.current) mainRef.current.inert = query.matches && mobileNav;
    };
    sync();
    query.addEventListener("change", sync);
    if (mobileNav)
      sidebarRef.current?.querySelector<HTMLAnchorElement>("a")?.focus();
    if (!mobileNav && navWasOpen.current) menuRef.current?.focus();
    navWasOpen.current = mobileNav;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNav(false);
    };
    window.addEventListener("keydown", escape);
    return () => {
      query.removeEventListener("change", sync);
      window.removeEventListener("keydown", escape);
    };
  }, [mobileNav]);
  const go = useCallback((next: Page) => {
    setPage(next);
    location.hash = next;
    setMobileNav(false);
    setError("");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);
  const refreshDocuments = useCallback(async () => {
    const data = await api<{ documents: DocumentSummary[] }>("/documents");
    setDocuments(data.documents);
    return data.documents;
  }, []);
  const fetchReview = useCallback(async () => {
    setReviewLoading(true);
    try {
      setReview(await api<Review>("/review"));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setReviewLoading(false);
    }
  }, []);
  const refreshDoc = useCallback(async (id: string, reset = false) => {
    const request = ++docRequest.current;
    const data = await api<StudyDocument>(
      `/documents/${encodeURIComponent(id)}`,
    );
    if (request !== docRequest.current || !mounted.current) return;
    setDoc(data);
    if (reset)
      setTopics(
        data.topics
          .filter((t) => t.question_count >= 3 && t.status === "ready")
          .slice(0, 1)
          .map((t) => t.id),
      );
  }, []);
  useEffect(() => {
    mounted.current = true;
    let active = true;
    setLoading(true);
    setInitialized(false);
    (async () => {
      try {
        // Establish the HttpOnly session before parallel session-owned requests.
        const h = await api<Health>("/health");
        if (!active) return;
        const list = await api<{ documents: DocumentSummary[] }>("/documents");
        if (!active) return;
        setHealth(h);
        setDocuments(list.documents);
        if (list.documents[0]) setDocId(list.documents[0].id);
        const saved = readSession();
        if (saved.current) {
          try {
            const q = await api<Quiz>(
              `/quizzes/${encodeURIComponent(saved.current.id)}`,
            );
            if (!active) return;
            setQuiz(q);
            setIndex(
              Math.min(
                saved.current.index,
                Math.max(0, q.questions.length - 1),
              ),
            );
            setParents(saved.parents);
          } catch (e) {
            if (!(e instanceof ApiError) || ![403, 404].includes(e.status))
              throw e;
            setQuiz(null);
            setParents([]);
          }
        }
        if (!active) return;
        if (saved.pendingJob) {
          setVariantJob(saved.pendingJob);
          setBusy("variant");
          setJobStatus("正在恢复补练任务进度…");
        }
        setInitialized(true);
        setError("");
      } catch (e) {
        if (active) setError(errorMessage(e));
      } finally {
        if (active) setLoading(false);
      }
    })();
    const onHash = () => {
      setPage(initialPage());
      setMobileNav(false);
    };
    window.addEventListener("hashchange", onHash);
    return () => {
      active = false;
      mounted.current = false;
      window.removeEventListener("hashchange", onHash);
    };
  }, [bootRetry]);
  useEffect(() => {
    if (!docId) return;
    setDoc(null);
    refreshDoc(docId, true).catch((e) => setError(errorMessage(e)));
  }, [docId, refreshDoc]);
  useEffect(() => {
    if (!docId || !doc) return;
    const active =
      doc.jobs.some((j) =>
        ["pending", "queued", "running"].includes(j.status),
      ) ||
      ["parsing", "indexing", "generating", "processing"].includes(doc.status);
    if (!active) return;
    const timer = setInterval(() => {
      refreshDoc(docId).catch((e) => setError(errorMessage(e)));
      refreshDocuments().catch((e) => setError(errorMessage(e)));
    }, 2200);
    return () => clearInterval(timer);
  }, [docId, doc?.status, doc?.jobs, refreshDoc, refreshDocuments]);
  useEffect(() => {
    if (initialized && page === "review") void fetchReview();
  }, [page, initialized, fetchReview]);
  useEffect(() => {
    if (!variantJob || jobPaused || !initialized) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      try {
        const job = await api<Job>(`/jobs/${encodeURIComponent(variantJob)}`);
        if (!active) return;
        setJobStatus(
          job.status === "running"
            ? "正在生成并复核新题，请稍候…"
            : "补练任务已排队，请稍候…",
        );
        if (job.question && job.quiz_id) {
          const next = { id: job.quiz_id, questions: [job.question] };
          setQuiz(next);
          setIndex(0);
          setVariantJob(null);
          setBusy("");
          setJobStatus("");
          go("practice");
          return;
        }
        if (["failed", "interrupted", "completed"].includes(job.status)) {
          setVariantJob(null);
          setParents((v) =>
            v[v.length - 1]?.id === quiz?.id ? v.slice(0, -1) : v,
          );
          setBusy("");
          setError(job.error || "暂时无法生成合格的新题，请先回顾原文。");
          setJobStatus("");
          return;
        }
        timer = setTimeout(check, 2000);
      } catch (e) {
        if (!active) return;
        setBusy("");
        setError(errorMessage(e));
        if (e instanceof ApiError && [403, 404].includes(e.status)) {
          setVariantJob(null);
          setParents((value) =>
            value[value.length - 1]?.id === quiz?.id
              ? value.slice(0, -1)
              : value,
          );
          setJobStatus("");
          return;
        }
        setJobPaused(true);
        setJobStatus("进度读取中断，可重新连接。");
      }
    };
    void check();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [variantJob, go, initialized, jobPaused, jobRetry, quiz?.id]);
  const selectDocument = (id: string) => {
    setDocId(id);
    setError("");
    setNotice("");
  };
  async function startQuiz(documentId = docId, topicIds = topics) {
    if (busy || variantJob || !documentId || !topicIds.length) return;
    setBusy("start");
    setError("");
    try {
      const q = await post<Quiz>("/quizzes", {
        document_id: documentId,
        topic_ids: topicIds,
      });
      setQuiz(q);
      setIndex(0);
      setParents([]);
      go("practice");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function submit(selected: string, confidence: Confidence) {
    if (busy || !quiz) return;
    const question = quiz.questions[index];
    setBusy("submit");
    setError("");
    try {
      const result = await post<Result>(
        `/questions/${encodeURIComponent(question.id)}/attempts`,
        { quiz_id: quiz.id, selected, confidence },
      );
      setQuiz(
        (q) =>
          q && {
            ...q,
            questions: q.questions.map((v) =>
              v.id === question.id ? { ...v, result } : v,
            ),
          },
      );
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function variant(questionId: string) {
    if (busy || variantJob) return;
    setBusy(`variant:${questionId}`);
    setError("");
    try {
      const result = await post<
        { quiz_id: string; question: Quiz["questions"][0] } | { job_id: string }
      >(`/questions/${encodeURIComponent(questionId)}/variant`);
      setParents((previous) =>
        page === "practice" && quiz
          ? [...previous, { id: quiz.id, index }]
          : [],
      );
      if ("question" in result) {
        setQuiz({ id: result.quiz_id, questions: [result.question] });
        setIndex(0);
        go("practice");
        setBusy("");
        return;
      }
      setJobPaused(false);
      setVariantJob(result.job_id);
      setJobStatus("补练任务已排队。");
    } catch (e) {
      setBusy("");
      setError(errorMessage(e));
    }
  }
  async function showEvidence(documentId: string, ids: string[]) {
    setEvidenceOpen(true);
    setEvidence(null);
    setEvidenceError("");
    setEvidenceLoading(true);
    const request = ++evidenceRequest.current;
    try {
      const result = await api<Evidence>(
        `/documents/${encodeURIComponent(documentId)}/sentences?ids=${encodeURIComponent([...new Set(ids)].join(","))}`,
      );
      if (request === evidenceRequest.current) setEvidence(result);
    } catch (e) {
      if (request === evidenceRequest.current)
        setEvidenceError(errorMessage(e));
    } finally {
      if (request === evidenceRequest.current) setEvidenceLoading(false);
    }
  }
  async function upload(file: File) {
    if (busy) return;
    if (file.size > 10 * 1024 * 1024) {
      setError("文件超过 10 MB，请压缩或拆分后再上传。");
      return;
    }
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setError("请选择文本型 PDF 文件。");
      return;
    }
    setBusy("upload");
    setError("");
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await api<{ id: string }>("/documents", {
        method: "POST",
        body,
      });
      await refreshDocuments();
      setDocId(result.id);
      setNotice("资料已收到，正在后台解析。你可以在这里查看进度。");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function confirmTopics(candidates: Candidate[]) {
    if (busy || !doc) return;
    setBusy("topics");
    setError("");
    try {
      await api(`/documents/${encodeURIComponent(doc.id)}/topics`, {
        method: "PUT",
        body: JSON.stringify({ topics: candidates }),
      });
      await refreshDoc(doc.id);
      setNotice("知识点已确认，正在后台准备证据与练习。");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function resume() {
    if (busy || !doc) return;
    setBusy("resume");
    setError("");
    try {
      await post(`/documents/${encodeURIComponent(doc.id)}/resume`);
      await refreshDoc(doc.id);
      setNotice("已恢复处理，将从现有进度继续。");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function revisit(quizId: string, questionId: string) {
    if (busy || variantJob) return;
    setBusy("revisit");
    setError("");
    try {
      const q = await api<Quiz>(`/quizzes/${encodeURIComponent(quizId)}`);
      setQuiz(q);
      setIndex(
        Math.max(
          0,
          q.questions.findIndex((x) => x.id === questionId),
        ),
      );
      setParents([]);
      go("practice");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function returnParent() {
    if (busy) return;
    const previous = parents[parents.length - 1];
    if (!previous) {
      go("review");
      return;
    }
    setBusy("return");
    setError("");
    try {
      const q = await api<Quiz>(`/quizzes/${encodeURIComponent(previous.id)}`);
      setQuiz(q);
      setIndex(Math.min(previous.index, q.questions.length - 1));
      setParents((v) => v.slice(0, -1));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  }
  async function quickStart() {
    const sample = documents.find((document) => document.is_demo);
    if (!sample) return;
    try {
      const content =
        doc?.id === sample.id
          ? doc
          : await api<StudyDocument>(`/documents/${sample.id}`);
      const topic = content.topics.find(
        (item) => item.status === "ready" && item.question_count >= 3,
      );
      if (!topic) {
        setError("示例题库正在准备中。");
        return;
      }
      setDocId(sample.id);
      setTopics([topic.id]);
      await startQuiz(sample.id, [topic.id]);
    } catch (e) {
      setError(errorMessage(e));
    }
  }
  const actionBusy = busy || (variantJob ? "variant" : "");
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          mainRef.current?.focus();
        }}
      >
        跳转到主要内容
      </a>
      {mobileNav && (
        <button
          className="nav-scrim"
          aria-label="关闭导航"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside
        id="main-navigation"
        ref={sidebarRef}
        className={`sidebar ${mobileNav ? "open" : ""}`}
      >
        <button
          className="icon-button mobile-nav-close"
          aria-label="关闭导航"
          onClick={() => setMobileNav(false)}
        >
          <X size={22} />
        </button>
        <a
          className="brand"
          href="#documents"
          onClick={() => go("documents")}
          aria-label="知证学伴首页"
        >
          <span className="brand-mark">
            <BookOpen size={26} />
          </span>
          <span>知证学伴</span>
        </a>
        <nav aria-label="主导航">
          {nav.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              onClick={() => go(item.id)}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={22} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
      </aside>
      <main
        id="main-content"
        ref={mainRef}
        tabIndex={-1}
        className="main-content"
      >
        <button
          ref={menuRef}
          className="icon-button mobile-menu"
          onClick={() => setMobileNav((value) => !value)}
          aria-label="展开导航"
          aria-controls="main-navigation"
          aria-expanded={mobileNav}
        >
          <Menu size={24} />
        </button>
        <div className="page-content">
          {error && (
            <div
              className="alert error"
              role="alert"
              ref={errorRef}
              tabIndex={-1}
            >
              <CircleHelp size={20} />
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="关闭错误提示"
                onClick={() => setError("")}
              >
                <X size={18} />
              </button>
            </div>
          )}
          {notice && page === "documents" && (
            <div className="alert info" role="status">
              <Check size={20} />
              <span>{notice}</span>
              <button
                className="icon-button"
                aria-label="关闭提示"
                onClick={() => setNotice("")}
              >
                <X size={18} />
              </button>
            </div>
          )}
          {variantJob && (
            <div className="alert info" role="status">
              <Loader2 size={20} className={jobPaused ? "" : "spin"} />
              <span>{jobStatus}</span>
              {jobPaused && (
                <button
                  className="button secondary"
                  onClick={() => {
                    setJobPaused(false);
                    setJobRetry((value) => value + 1);
                    setBusy("variant");
                    setError("");
                  }}
                >
                  重新读取进度
                </button>
              )}
            </div>
          )}
          {!loading && !initialized && (
            <div className="empty-state card">
              <h2>学习空间暂时未能载入</h2>
              <button
                className="button primary"
                onClick={() => setBootRetry((value) => value + 1)}
              >
                重新连接
                <RefreshCw size={18} />
              </button>
            </div>
          )}
          {loading && (
            <div className="loading-state">
              <Loader2 className="spin" size={28} />
              正在打开学习空间…
            </div>
          )}
          {!loading && initialized && (
            <>
              {page === "documents" && (
                <DocumentsPage
                  documents={documents}
                  document={doc}
                  selectedId={docId}
                  selectedTopics={topics}
                  health={health}
                  busy={actionBusy}
                  onSelect={selectDocument}
                  onTopics={setTopics}
                  onStart={() => void startQuiz()}
                  onQuickStart={quickStart}
                  onAI={() => go("ai")}
                  onUpload={upload}
                  onConfirmTopics={confirmTopics}
                  onResume={resume}
                  onRefresh={() => {
                    refreshDocuments()
                      .then(() => (docId ? refreshDoc(docId) : undefined))
                      .catch((e) => setError(errorMessage(e)));
                  }}
                />
              )}
              {page === "practice" && (
                <PracticePage
                  quiz={quiz}
                  index={index}
                  busy={actionBusy}
                  jobStatus={jobStatus}
                  hasParent={parents.length > 0}
                  onSubmit={submit}
                  onVariant={variant}
                  onEvidence={showEvidence}
                  onIndex={(value) => {
                    if (busy) return;
                    setIndex(value);
                    setError("");
                    window.scrollTo({ top: 0, behavior: "smooth" });
                  }}
                  onReturn={returnParent}
                  onDocuments={() => go("documents")}
                  onReview={() => go("review")}
                />
              )}
              {page === "review" && (
                <ReviewPage
                  review={review}
                  loading={reviewLoading}
                  busy={actionBusy}
                  onRefresh={fetchReview}
                  onPractice={() => go("documents")}
                  onEvidence={showEvidence}
                  onVariant={variant}
                  onRevisit={revisit}
                />
              )}
              {page === "ai" && (
                <AIWorkbench
                  documents={documents}
                  selectedId={docId}
                  onSelect={selectDocument}
                  onPractice={quickStart}
                />
              )}
            </>
          )}
        </div>
      </main>
      {evidenceOpen && (
        <EvidenceDrawer
          evidence={evidence}
          loading={evidenceLoading}
          error={evidenceError}
          onClose={closeEvidence}
        />
      )}
    </div>
  );
}
