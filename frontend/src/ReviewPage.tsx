import {
  ArrowRight,
  BookOpen,
  Check,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import type { Review } from "./types";
interface Props {
  review: Review | null;
  loading: boolean;
  busy: string;
  onRefresh: () => void;
  onPractice: () => void;
  onEvidence: (documentId: string, ids: string[]) => void;
  onVariant: (questionId: string) => void;
  onRevisit: (quizId: string, questionId: string) => void;
}
const states = {
  pending: "待纠正",
  improving: "纠正中",
  corrected: "近期已纠正",
};
const confidenceLabels = { sure: "确定", unsure: "不太确定", guess: "猜的" };

export function ReviewPage(p: Props) {
  if (!p.review && p.loading)
    return (
      <div className="loading-state">
        <Loader2 className="spin" />
        加载复习记录…
      </div>
    );
  if (!p.review)
    return (
      <div className="card empty-state">
        <h2>暂时无法读取复习记录</h2>
        <button className="button primary" onClick={p.onRefresh}>
          重新读取
          <RefreshCw size={18} />
        </button>
      </div>
    );
  const r = p.review;
  return (
    <>
      <header className="page-heading">
        <h1>我的复习</h1>
        <button
          className="text-button"
          disabled={p.loading}
          onClick={p.onRefresh}
        >
          <RefreshCw size={18} className={p.loading ? "spin" : ""} />
          刷新记录
        </button>
      </header>
      {r.total === 0 && (
        <section className="card empty-state">
          <h2>还没有练习记录</h2>
          <button className="button primary" onClick={p.onPractice}>
            开始第一次练习
            <ArrowRight size={18} />
          </button>
        </section>
      )}
      {r.total > 0 && (
        <>
          <div className="stat-grid">
            <div className="stat-card">
              <span>累计作答</span>
              <strong>{r.total}</strong>
            </div>
            <div className="stat-card">
              <span>答对</span>
              <strong>{r.correct}</strong>
            </div>
            <div className="stat-card">
              <span>待纠正误区</span>
              <strong className="text-wrong">
                {
                  r.misconceptions.filter((item) => item.status !== "corrected")
                    .length
                }
              </strong>
            </div>
          </div>
          <div className="review-layout">
            <section className="card misconception-review">
              <h2>误区记录</h2>
              {r.misconceptions.length === 0 && (
                <p className="empty-inline">暂无答错误区</p>
              )}
              {r.misconceptions.map((item) => (
                <article className="review-misconception" key={item.id}>
                  <div className="misconception-heading">
                    <span
                      className={`badge ${item.status === "pending" ? "wrong-badge" : "blue-badge"}`}
                    >
                      {states[item.status]}
                    </span>
                    <span className="secondary-text">{item.topic_title}</span>
                  </div>
                  <h3>{item.label}</h3>
                  <p>
                    答错 {item.wrong_count} 次 · 其中确定却答错{" "}
                    {item.sure_wrong_count} 次 · 后续答对{" "}
                    {item.distinct_correct} 题
                  </p>
                  <div className="button-row">
                    <button
                      className="text-button"
                      onClick={() =>
                        p.onEvidence(item.document_id, item.refute_sentence_ids)
                      }
                    >
                      <BookOpen size={18} />
                      看原文
                    </button>
                    <button
                      className="button secondary"
                      disabled={!!p.busy}
                      onClick={() => p.onVariant(item.question_id)}
                    >
                      {p.busy === `variant:${item.question_id}` ? (
                        <Loader2 size={18} className="spin" />
                      ) : (
                        <RefreshCw size={18} />
                      )}
                      再练一道
                    </button>
                  </div>
                </article>
              ))}
            </section>
            <aside className="card confidence-card">
              <h2>信心与对错</h2>
              <table className="confidence-matrix">
                <thead>
                  <tr>
                    <th scope="col" />
                    <th scope="col">答对</th>
                    <th scope="col">答错</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row">确定</th>
                    <td>{r.quadrants.sure_correct}</td>
                    <td className="wrong-cell">{r.quadrants.sure_wrong}</td>
                  </tr>
                  <tr>
                    <th scope="row">没把握</th>
                    <td>
                      {r.quadrants.unsure_correct + r.quadrants.guess_correct}
                    </td>
                    <td className="wrong-cell">
                      {r.quadrants.unsure_wrong + r.quadrants.guess_wrong}
                    </td>
                  </tr>
                </tbody>
              </table>
            </aside>
          </div>
          <section className="card attempt-history">
            <h2>最近练习</h2>
            <div className="history-table">
              <div className="history-head">
                <span>题目</span>
                <span>信心</span>
                <span>结果</span>
                <span>时间</span>
              </div>
              {r.attempts.slice(0, 20).map((attempt) => (
                <button
                  className="history-row"
                  key={attempt.question_id}
                  disabled={!!p.busy}
                  onClick={() =>
                    p.onRevisit(attempt.quiz_id, attempt.question_id)
                  }
                >
                  <span>{attempt.stem}</span>
                  <span>{confidenceLabels[attempt.confidence]}</span>
                  <span
                    className={attempt.correct ? "text-right" : "text-wrong"}
                  >
                    {attempt.correct ? <Check size={16} /> : <X size={16} />}{" "}
                    {attempt.correct
                      ? attempt.confidence === "guess"
                        ? "猜对"
                        : "答对"
                      : "答错"}
                  </span>
                  <time>
                    {new Date(attempt.created_at).toLocaleString("zh-CN", {
                      month: "2-digit",
                      day: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                      hour12: false,
                    })}
                  </time>
                </button>
              ))}
            </div>
          </section>
        </>
      )}
    </>
  );
}
