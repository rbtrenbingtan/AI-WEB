import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  FileText,
  Loader2,
  Plus,
  RefreshCw,
  UploadCloud,
  X,
} from "lucide-react";
import type {
  Candidate,
  DocumentSummary,
  Health,
  StudyDocument,
} from "./types";

const labels: Record<string, string> = {
  ready: "可练习",
  parsing: "解析中",
  indexing: "建立索引",
  generating: "生成题库",
  queued: "已排队",
  running: "处理中",
  failed: "处理失败",
  interrupted: "已中断",
  awaiting_topics: "待确认知识点",
  insufficient: "依据不足",
};
export function statusLabel(status: string) {
  return labels[status] ?? status;
}
interface Props {
  documents: DocumentSummary[];
  document: StudyDocument | null;
  selectedId: string;
  selectedTopics: string[];
  health: Health | null;
  busy: string;
  onSelect: (id: string) => void;
  onTopics: (ids: string[]) => void;
  onStart: () => void;
  onUpload: (file: File) => void;
  onConfirmTopics: (topics: Candidate[]) => void;
  onResume: () => void;
  onRefresh: () => void;
  onQuickStart?: () => void;
  onAI?: () => void;
}

export function DocumentsPage(p: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [expanded, setExpanded] = useState<string[]>([]);
  const doc = p.document;
  const candidateState =
    doc &&
    !doc.is_demo &&
    !doc.topics.length &&
    doc.samples.length > 0 &&
    ["awaiting_topics", "failed"].includes(doc.status);
  return (
    <>
      <header className="page-heading">
        <h1>学习资料</h1>
        <button className="text-button" onClick={p.onRefresh}>
          <RefreshCw size={18} />
          刷新
        </button>
      </header>
      <section className="quick-start">
        <div>
          <h2>答错时，直接回到讲义原文</h2>
          <p>选择答案、记录信心，用新的情境检验同一个误区。</p>
        </div>
        <button
          className="button primary"
          disabled={!!p.busy}
          onClick={p.onQuickStart ?? p.onStart}
        >
          {p.busy === "start" && <Loader2 size={18} className="spin" />}
          用示例讲义练 3 题<ArrowRight size={18} />
        </button>
      </section>
      <div className="section-heading">
        <h2>我的资料</h2>
        {p.onAI && (
          <button className="text-button" onClick={p.onAI}>
            查看 AI 工作流
            <ArrowRight size={18} />
          </button>
        )}
      </div>
      <div className="library-grid">
        {p.documents.map((document) => (
          <button
            key={document.id}
            className={`document-card ${document.id === p.selectedId ? "selected" : ""}`}
            onClick={() => p.onSelect(document.id)}
            aria-pressed={document.id === p.selectedId}
          >
            <span className="document-card-top">
              <FileText size={28} />
              <span className="badge">{statusLabel(document.status)}</span>
            </span>
            <h3>{document.name}</h3>
            <span className="document-meta">
              {document.page_count} 页 · {document.topic_count} 个知识点 ·{" "}
              {document.question_count} 道题
            </span>
          </button>
        ))}
        {p.health?.upload_enabled && (
          <div
            className="upload-card"
            onDragOver={(event) => event.preventDefault()}
            onDrop={(event) => {
              event.preventDefault();
              if (!p.busy && event.dataTransfer.files[0])
                p.onUpload(event.dataTransfer.files[0]);
            }}
          >
            <input
              type="file"
              ref={input}
              accept=".pdf,application/pdf"
              aria-label="选择文本型 PDF"
              hidden
              onChange={(event) => {
                if (event.target.files?.[0]) p.onUpload(event.target.files[0]);
                event.target.value = "";
              }}
            />
            <UploadCloud size={28} />
            <h3>导入课程讲义</h3>
            <p>文本 PDF · 10 MB / 30 页</p>
            <button
              className="button secondary"
              disabled={!!p.busy}
              onClick={() => input.current?.click()}
            >
              {p.busy === "upload" ? (
                <Loader2 size={18} className="spin" />
              ) : (
                <Plus size={18} />
              )}
              上传 PDF
            </button>
          </div>
        )}
      </div>
      {!doc && (
        <div className="card empty-state">
          {p.selectedId ? (
            <>
              <Loader2 className="spin" />
              加载资料…
            </>
          ) : (
            <>
              <BookOpen />
              <h3>暂无资料</h3>
            </>
          )}
        </div>
      )}
      {doc && (
        <section className="card document-detail">
          <header className="section-heading">
            <h2>选择知识点</h2>
            <span className="secondary-text">{doc.topics.length} 个知识点</span>
          </header>
          {doc.jobs
            .filter((job) =>
              ["queued", "running", "failed", "interrupted"].includes(
                job.status,
              ),
            )
            .map((job) => (
              <div className="job-panel" key={job.id}>
                <strong>{statusLabel(job.status)}</strong>
                {job.error && <p>{job.error}</p>}
                {["queued", "running"].includes(job.status) && (
                  <progress max="100" value={job.progress} />
                )}
              </div>
            ))}
          {["interrupted", "failed"].includes(doc.status) && !doc.is_demo && (
            <button
              className="button secondary"
              disabled={!!p.busy}
              onClick={p.onResume}
            >
              <RefreshCw size={18} />
              继续处理
            </button>
          )}
          {doc.warnings?.map((warning) => (
            <p className="text-wrong" key={warning}>
              {warning}
            </p>
          ))}
          {candidateState && (
            <CandidateEditor
              candidates={doc.candidate_topics}
              busy={p.busy === "topics"}
              onConfirm={p.onConfirmTopics}
            />
          )}
          <div className="topic-list">
            {doc.topics.map((topic) => (
              <div className="topic-row" key={topic.id}>
                <div className="topic-main">
                  <label className="topic-choice">
                    <input
                      type="checkbox"
                      checked={p.selectedTopics.includes(topic.id)}
                      disabled={
                        topic.question_count < 3 || topic.status !== "ready"
                      }
                      onChange={() =>
                        p.onTopics(
                          p.selectedTopics.includes(topic.id)
                            ? p.selectedTopics.filter((id) => id !== topic.id)
                            : [...p.selectedTopics, topic.id],
                        )
                      }
                    />
                    <span>{topic.title}</span>
                  </label>
                  <span className="secondary-text">
                    {topic.question_count} 道题
                  </span>
                  <button
                    className="text-button"
                    aria-expanded={expanded.includes(topic.id)}
                    onClick={() =>
                      setExpanded((previous) =>
                        previous.includes(topic.id)
                          ? previous.filter((id) => id !== topic.id)
                          : [...previous, topic.id],
                      )
                    }
                  >
                    {topic.misconceptions.length} 个误区
                    <ChevronDown size={18} />
                  </button>
                </div>
                {topic.reason && <p className="text-wrong">{topic.reason}</p>}
                {expanded.includes(topic.id) && (
                  <div className="coverage-list">
                    {topic.misconceptions.map((item) => (
                      <div key={item.id}>
                        <span>{item.label}</span>
                        <span className="badge">{item.question_count} 题</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
          <div className="start-row">
            <span>已选 {p.selectedTopics.length} 个知识点 · 3 题</span>
            <button
              className="button primary"
              disabled={!!p.busy || !p.selectedTopics.length}
              onClick={p.onStart}
            >
              {p.busy === "start" && <Loader2 size={18} className="spin" />}
              开始本轮练习
              <ArrowRight size={18} />
            </button>
          </div>
          {doc.samples.length > 0 && (
            <details className="samples">
              <summary>查看讲义文本</summary>
              <div className="sample-grid">
                {doc.samples.map((sample) => (
                  <article key={sample.page_index}>
                    <h3>第 {sample.page_index} 页</h3>
                    <p>{sample.text}</p>
                  </article>
                ))}
              </div>
            </details>
          )}
        </section>
      )}
    </>
  );
}

function CandidateEditor({
  candidates,
  busy,
  onConfirm,
}: {
  candidates: Candidate[];
  busy: boolean;
  onConfirm: (items: Candidate[]) => void;
}) {
  const [items, setItems] = useState<(Candidate & { checked: boolean })[]>([]);
  useEffect(
    () =>
      setItems(
        candidates.length
          ? candidates.map((candidate) => ({ ...candidate, checked: true }))
          : [{ title: "", description: "", checked: true }],
      ),
    [candidates],
  );
  const selected = items.filter((item) => item.checked);
  const duplicate =
    new Set(selected.map((item) => item.title.trim().toLocaleLowerCase()))
      .size !== selected.length;
  const missing = selected.some((item) => !item.title.trim());
  return (
    <section className="candidate-panel">
      <h3>确认知识点</h3>
      <p>
        {candidates.length
          ? "勾选并修改需要练习的知识点。"
          : "没有可用候选，请填写知识点名称。"}
      </p>
      {items.map((item, index) => (
        <div className="candidate-row" key={index}>
          <input
            type="checkbox"
            aria-label={`使用知识点 ${index + 1}`}
            checked={item.checked}
            onChange={(event) =>
              setItems((previous) =>
                previous.map((value, i) =>
                  index === i
                    ? { ...value, checked: event.target.checked }
                    : value,
                ),
              )
            }
          />
          <input
            aria-label={`知识点 ${index + 1} 名称`}
            value={item.title}
            maxLength={80}
            placeholder="知识点名称"
            onChange={(event) =>
              setItems((previous) =>
                previous.map((value, i) =>
                  index === i ? { ...value, title: event.target.value } : value,
                ),
              )
            }
          />
          <input
            aria-label={`知识点 ${index + 1} 描述`}
            value={item.description}
            maxLength={300}
            placeholder="描述（可选）"
            onChange={(event) =>
              setItems((previous) =>
                previous.map((value, i) =>
                  index === i
                    ? { ...value, description: event.target.value }
                    : value,
                ),
              )
            }
          />
          <button
            className="icon-button"
            aria-label={`移除知识点 ${index + 1}`}
            onClick={() =>
              setItems((previous) => previous.filter((_, i) => index !== i))
            }
          >
            <X size={18} />
          </button>
        </div>
      ))}
      {duplicate && (
        <p className="text-wrong" role="status">
          知识点名称不能重复。
        </p>
      )}
      <div className="button-row">
        <button
          className="text-button"
          disabled={items.length >= 6}
          onClick={() =>
            setItems((previous) => [
              ...previous,
              { title: "", description: "", checked: true },
            ])
          }
        >
          <Plus size={18} />
          新增知识点
        </button>
        <button
          className="button primary"
          disabled={busy || missing || duplicate || !selected.length}
          onClick={() =>
            onConfirm(
              selected.map((item) => ({
                title: item.title.trim(),
                description: item.description.trim(),
              })),
            )
          }
        >
          {busy ? <Loader2 size={18} className="spin" /> : <Check size={18} />}
          确认并生成练习
        </button>
      </div>
    </section>
  );
}
