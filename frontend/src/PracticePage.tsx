import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { api } from "./api";
import { EvidencePanel } from "./EvidencePanel";
import type { Confidence, Evidence, Quiz } from "./types";

interface Props {
  quiz: Quiz | null;
  index: number;
  busy: string;
  jobStatus: string;
  hasParent: boolean;
  onSubmit: (selected: string, confidence: Confidence) => void;
  onVariant: (id: string) => void;
  onEvidence: (documentId: string, ids: string[]) => void;
  onIndex: (index: number) => void;
  onReturn: () => void;
  onDocuments: () => void;
  onReview: () => void;
}
const confidenceOptions = [
  { id: "sure", label: "确定" },
  { id: "unsure", label: "不太确定" },
  { id: "guess", label: "猜的" },
] as const;

export function PracticePage(p: Props) {
  const [selected, setSelected] = useState("");
  const [confidence, setConfidence] = useState<Confidence | null>(null);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [sourceError, setSourceError] = useState("");
  const q = p.quiz?.questions[p.index];
  const result = q?.result;
  const isVariant = p.quiz?.questions.length === 1;
  useEffect(() => {
    setSelected(q?.result?.selected ?? "");
    setConfidence(q?.result?.confidence ?? null);
  }, [q?.id, q?.result]);
  useEffect(() => {
    setEvidence(null);
    setSourceError("");
    if (!result) return;
    const controller = new AbortController();
    api<Evidence>(
      `/documents/${encodeURIComponent(result.document_id)}/sentences?ids=${encodeURIComponent(result.evidence_sentence_ids.join(","))}`,
      { signal: controller.signal },
    )
      .then(setEvidence)
      .catch((error: Error) => {
        if (!controller.signal.aborted) setSourceError(error.message);
      });
    return () => controller.abort();
  }, [result]);
  useEffect(() => {
    if (!q || result || p.busy) return;
    const key = (event: KeyboardEvent) => {
      if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        (event.target instanceof HTMLInputElement &&
          event.target.type !== "radio")
      )
        return;
      const index = "abcd".indexOf(event.key.toLowerCase());
      if (index >= 0 && event.key.length === 1) {
        event.preventDefault();
        setSelected(q.options[index].letter);
      }
      if (/^[1-4]$/.test(event.key)) {
        event.preventDefault();
        setSelected(q.options[Number(event.key) - 1].letter);
      }
      if (event.key === "Enter" && selected && confidence) {
        event.preventDefault();
        p.onSubmit(selected, confidence);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [q, result, p.busy, p.onSubmit, selected, confidence]);
  if (!p.quiz || !q)
    return (
      <>
        <header className="page-heading">
          <h1>证据练习</h1>
        </header>
        <section className="card empty-state">
          <BookOpen size={32} />
          <h2>选择讲义，开始三题练习</h2>
          <button className="button primary" onClick={p.onDocuments}>
            选择学习资料
            <ArrowRight size={18} />
          </button>
        </section>
      </>
    );
  const allDone = p.quiz.questions.every((item) => item.result);
  const correct = p.quiz.questions.filter(
    (item) => item.result?.correct,
  ).length;
  const mistakes = new Set(
    p.quiz.questions.flatMap((item) =>
      item.result?.misconception ? [item.result.misconception.id] : [],
    ),
  ).size;
  const correction = evidence?.sentences
    .filter((sentence) =>
      result?.misconception?.refute_sentence_ids.includes(sentence.id),
    )
    .map((sentence) => sentence.text)
    .join("");
  return (
    <>
      <header className="page-heading">
        <h1>{isVariant ? "误区补练" : "证据练习"}</h1>
        <button
          className="text-button"
          disabled={!!p.busy}
          onClick={isVariant ? p.onReturn : p.onDocuments}
        >
          <ArrowLeft size={18} />
          {isVariant
            ? p.hasParent
              ? "返回上一轮练习"
              : "返回我的复习"
            : "选择其他知识点"}
        </button>
      </header>
      <div className="quiz-progress" aria-label="本轮题目">
        {p.quiz.questions.map((item, index) => (
          <button
            key={item.id}
            disabled={!!p.busy}
            onClick={() => p.onIndex(index)}
            aria-label={`第 ${index + 1} 题${item.result ? (item.result.correct ? "，答对" : "，答错") : ""}`}
            aria-current={index === p.index ? "step" : undefined}
            className={`${index === p.index ? "current" : ""} ${item.result ? (item.result.correct ? "right" : "wrong") : ""}`}
          >
            <span>第 {index + 1} 题</span>
            {item.result &&
              (item.result.correct ? <Check size={18} /> : <X size={18} />)}
          </button>
        ))}
      </div>
      <div className={`practice-layout ${result ? "with-evidence" : ""}`}>
        <section className="card question-card">
          <div className="question-tags">
            <span className="badge">{q.topic_title}</span>
            {q.is_retest && (
              <span className="badge">
                <RefreshCw size={14} />
                延迟复测
              </span>
            )}
            {isVariant && (
              <span className="badge">
                补练{q.target_label ? ` · ${q.target_label}` : ""}
              </span>
            )}
          </div>
          <h2 className="question-stem">{q.stem}</h2>
          <fieldset className="answer-options" disabled={!!result || !!p.busy}>
            <legend className="sr-only">选择一个答案</legend>
            {q.options.map((option) => {
              const answer = result?.answer_letter === option.letter;
              const wrong =
                !!result &&
                !result.correct &&
                result.selected === option.letter;
              return (
                <label
                  key={option.letter}
                  className={`answer-option ${selected === option.letter ? "selected" : ""} ${answer ? "correct-option" : ""} ${wrong ? "wrong-option" : ""}`}
                >
                  <input
                    type="radio"
                    name={`answer-${q.id}`}
                    checked={selected === option.letter}
                    onChange={() => setSelected(option.letter)}
                  />
                  <span className="option-letter">
                    {answer ? (
                      <Check size={20} />
                    ) : wrong ? (
                      <X size={20} />
                    ) : (
                      option.letter
                    )}
                  </span>
                  <span className="option-copy">
                    <span>{option.text}</span>
                    {result && (
                      <span className="option-explanation">
                        {
                          result.options.find(
                            (item) => item.letter === option.letter,
                          )!.feedback
                        }
                      </span>
                    )}
                  </span>
                  {answer && <span className="option-label">正确</span>}
                  {wrong && <span className="option-label">你的选择</span>}
                </label>
              );
            })}
          </fieldset>
          {!result && (
            <>
              <h3 className="confidence-title">你有多确定？</h3>
              <fieldset className="confidence-options" disabled={!!p.busy}>
                <legend className="sr-only">标注你的信心</legend>
                {confidenceOptions.map((option) => (
                  <label
                    key={option.id}
                    className={confidence === option.id ? "selected" : ""}
                  >
                    <input
                      type="radio"
                      name={`confidence-${q.id}`}
                      checked={confidence === option.id}
                      onChange={() => setConfidence(option.id)}
                    />
                    {option.label}
                  </label>
                ))}
              </fieldset>
              <div className="submit-row">
                <button
                  className="button primary"
                  disabled={!selected || !confidence || !!p.busy}
                  onClick={() => confidence && p.onSubmit(selected, confidence)}
                >
                  {p.busy === "submit" && (
                    <Loader2 size={18} className="spin" />
                  )}
                  提交答案
                  <ArrowRight size={18} />
                </button>
              </div>
            </>
          )}
          {result && (
            <div className="feedback-area">
              {result.misconception && (
                <div className="misconception-callout">
                  <h3>本题误区：{result.misconception.label}</h3>
                  {correction && <blockquote>{correction}</blockquote>}
                </div>
              )}
              {result.correct && (
                <p className="success-message">
                  <Check size={20} />
                  {isVariant
                    ? "这道补练答对了"
                    : result.confidence === "guess"
                      ? "猜对，记得再回看原文"
                      : "回答正确"}
                </p>
              )}
              <button
                className="button secondary mobile-source"
                onClick={() =>
                  p.onEvidence(result.document_id, result.evidence_sentence_ids)
                }
              >
                <BookOpen size={18} />
                查看讲义原文
              </button>
              <div className="feedback-actions">
                {!result.correct && (
                  <button
                    className="button primary"
                    disabled={!!p.busy}
                    onClick={() => p.onVariant(q.id)}
                  >
                    {p.busy.startsWith("variant") ? (
                      <Loader2 size={18} className="spin" />
                    ) : (
                      <RefreshCw size={18} />
                    )}
                    针对这个误区再练一道
                  </button>
                )}
                {p.index < p.quiz.questions.length - 1 && (
                  <button
                    className="button secondary"
                    disabled={!!p.busy}
                    onClick={() => p.onIndex(p.index + 1)}
                  >
                    下一题
                    <ArrowRight size={18} />
                  </button>
                )}
                {isVariant && (
                  <button
                    className="button secondary"
                    disabled={!!p.busy}
                    onClick={p.onReturn}
                  >
                    {p.hasParent ? "返回本轮" : "返回复习"}
                  </button>
                )}
              </div>
            </div>
          )}
        </section>
        {result && (
          <aside className="card inline-evidence">
            <header className="section-heading">
              <h2>讲义原文</h2>
              <span className="badge mark-badge">证据高亮</span>
            </header>
            <EvidencePanel
              evidence={evidence}
              loading={!evidence && !sourceError}
              error={sourceError}
              refuteIds={result.misconception?.refute_sentence_ids}
            />
          </aside>
        )}
      </div>
      {allDone && (
        <section className="card round-summary">
          <h3>
            {p.quiz.questions.length} 题答对 {correct} 题，关联 {mistakes}{" "}
            个误区
          </h3>
          <div className="button-row">
            <button className="button primary" onClick={p.onReview}>
              去复习页
            </button>
            {!isVariant && (
              <button className="button secondary" onClick={p.onDocuments}>
                再来一轮
              </button>
            )}
          </div>
        </section>
      )}
    </>
  );
}
