import { useEffect, useState } from "react";
import {
  ArrowRight,
  Braces,
  Check,
  Cpu,
  Database,
  FileText,
  GitBranch,
  Loader2,
  RefreshCw,
  Search,
  ShieldCheck,
  Target,
} from "lucide-react";
import { api } from "./api";
import type { AIOverview, DocumentSummary } from "./types";

const stages = [
  {
    title: "解析与分句",
    icon: FileText,
    technique: "句级锚定",
    description: "保留页码、原文偏移和稳定句子编号，让每一条依据都能回到讲义。",
  },
  {
    title: "证据检索",
    icon: Search,
    technique: "混合 RAG",
    description: "向量 Top-4 与关键词命中取并集，定向补练强制包含误区纠正句。",
  },
  {
    title: "误区建模",
    icon: Target,
    technique: "结构化生成",
    description: "生成稳定误区实体，再独立检查纠正句是否支持这个误区的判断。",
  },
  {
    title: "约束出题",
    icon: Braces,
    technique: "Schema 约束",
    description:
      "题目绑定目标误区与证据编号，代码检查四选项、唯一答案和近似重复。",
  },
  {
    title: "复核与修复",
    icon: ShieldCheck,
    technique: "生成—评估—修正",
    description: "复核调用独立作答和匹配误区；失败最多修复一次，随后重新检查。",
  },
  {
    title: "学习闭环",
    icon: GitBranch,
    technique: "误区状态追踪",
    description: "作答信心、定向补练和后续复测围绕同一个误区累计学习记录。",
  },
];
const purposeLabels: Record<AIOverview["calls"]["records"][number]["purpose"], string> = {
  candidate_topics: "知识点提取",
  misconceptions: "误区生成",
  review_misconceptions: "误区复核",
  generate: "题目生成",
  blind_review: "独立复核",
  repair: "修复",
  embedding: "向量化",
};
const statusLabels = { succeeded: "成功", failed: "失败", interrupted: "已中断" };

export function AIWorkbench({
  documents,
  selectedId,
  onSelect,
  onPractice,
}: {
  documents: DocumentSummary[];
  selectedId: string;
  onSelect: (id: string) => void;
  onPractice: () => void;
}) {
  const [overview, setOverview] = useState<AIOverview | null>(null);
  const [active, setActive] = useState(0);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setOverview(null);
    setError("");
    if (!selectedId) return;
    const controller = new AbortController();
    api<AIOverview>(`/documents/${encodeURIComponent(selectedId)}/ai`, {
      signal: controller.signal,
    })
      .then(setOverview)
      .catch((failure: Error) => {
        if (!controller.signal.aborted) setError(failure.message);
      });
    return () => controller.abort();
  }, [selectedId, revision]);

  return (
    <>
      <header className="page-heading">
        <div>
          <h1>AI 工作台</h1>
          <p>从课程原文到可验证练习，展开查看每一步。</p>
        </div>
        <button
          className="text-button"
          onClick={() => setRevision((value) => value + 1)}
        >
          <RefreshCw size={18} />
          刷新数据
        </button>
      </header>
      <div className="workbench-toolbar">
        <label>
          当前讲义
          <select
            value={selectedId}
            onChange={(event) => onSelect(event.target.value)}
          >
            {documents.map((document) => (
              <option key={document.id} value={document.id}>
                {document.name}
              </option>
            ))}
          </select>
        </label>
        {overview && (
          <span className="badge blue-badge">
            {overview.is_demo ? "示例流程" : "实时资料"}
          </span>
        )}
      </div>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {!overview && !error && (
        <div className="loading-state">
          <Loader2 className="spin" />
          加载工作流…
        </div>
      )}
      {overview && (
        <>
          <section className="card workflow-card">
            <div className="section-heading">
              <h2>大模型工作流</h2>
              <span className="secondary-text">{overview.prompt_version}</span>
            </div>
            <div className="workflow-track">
              {stages.map((stage, index) => (
                <button
                  key={stage.title}
                  className={active === index ? "active" : ""}
                  aria-pressed={active === index}
                  onClick={() => setActive(index)}
                >
                  <stage.icon size={24} />
                  <span>{stage.title}</span>
                  <small>{stage.technique}</small>
                </button>
              ))}
            </div>
            <div className="stage-detail">
              <div className="stage-explanation">
                <span className="badge mark-badge">步骤 {active + 1}</span>
                <h2>{stages[active].title}</h2>
                <p>{stages[active].description}</p>

              </div>
              <div className="stage-output">
                {active === 0 && (
                  <>
                    <h3>已入库的课程结构</h3>
                    <div className="mini-stats">
                      <span>
                        <strong>{overview.counts.pages}</strong>页
                      </span>
                      <span>
                        <strong>{overview.counts.sentences}</strong>句
                      </span>
                      <span>
                        <strong>{overview.counts.chunks}</strong>片段
                      </span>
                    </div>
                    <blockquote className="source-quote">
                      {overview.chunks[0]?.text}
                    </blockquote>
                  </>
                )}
                {active === 1 && (
                  <>
                    <h3>{overview.is_demo ? "示例证据片段" : "可检索片段"}</h3>
                    <div className="chunk-list">
                      {overview.chunks.slice(0, 3).map((chunk) => (
                        <details key={chunk.id}>
                          <summary>
                            <span>第 {chunk.page_index} 页</span>
                            <span>{chunk.sentence_count} 句</span>
                          </summary>
                          <p className="source-quote">{chunk.text}</p>
                        </details>
                      ))}
                    </div>
                    <div className="formula-flow">
                      <span>向量 Top-4</span>
                      <span>＋</span>
                      <span>关键词补充</span>
                      <ArrowRight size={18} />
                      <strong>证据包</strong>
                    </div>
                  </>
                )}
                {active === 2 && (
                  <>
                    <h3>误区与纠正依据</h3>
                    {overview.misconceptions.slice(0, 3).map((item) => (
                      <div className="entity-row" key={item.id}>
                        <strong>{item.label}</strong>
                        <blockquote>{item.refutation}</blockquote>
                      </div>
                    ))}
                  </>
                )}
                {active === 3 && (
                  <>
                    <h3>结构化题目约定</h3>
                    <pre className="schema-preview">
                      {
                        '{\n  "stem": "情境题干",\n  "options": ["四个选项"],\n  "target_misconception_id": "稳定误区",\n  "evidence_sentence_ids": ["原文编号"]\n}'
                      }
                    </pre>
                    <p>
                      题库已保存 <strong>{overview.counts.questions}</strong>{" "}
                      道题。
                    </p>
                  </>
                )}
                {active === 4 && (
                  <>
                    <h3>入库门槛</h3>
                    <ul className="gate-list">
                      <li>
                        <Check size={18} />
                        结构与证据归属检查
                      </li>
                      <li>
                        <Check size={18} />
                        随机选项后的独立盲答
                      </li>
                      <li>
                        <Check size={18} />
                        干扰项与误区映射检查
                      </li>
                      <li>
                        <Check size={18} />
                        失败修复后重新复核
                      </li>
                    </ul>
                    <div className="mini-stats">
                      <span>
                        <strong>{overview.generation.accepted}</strong>生成通过
                      </span>
                      <span>
                        <strong>{overview.generation.rejected}</strong>拒绝记录
                      </span>
                      <span>
                        <strong>{overview.generation.repairs}</strong>修复尝试
                      </span>
                    </div>
                  </>
                )}
                {active === 5 && (
                  <>
                    <h3>围绕同一个误区检验理解</h3>
                    <div className="learning-chain">
                      <span>作答与信心</span>
                      <ArrowRight size={18} />
                      <span>原文纠正</span>
                      <ArrowRight size={18} />
                      <span>同误区补练</span>
                      <ArrowRight size={18} />
                      <span>后续复测</span>
                    </div>
                    <button className="button primary" onClick={onPractice}>
                      体验证据练习
                      <ArrowRight size={18} />
                    </button>
                  </>
                )}
              </div>
            </div>
          </section>
          <details className="model-settings"><summary>模型配置</summary>
          <div className="model-grid">
            {overview.models.map((model) => (
              <section className="card model-card" key={model.role}>
                <Cpu size={24} />
                <div>
                  <h3>{model.role}</h3>
                  <p>{model.configured ? model.name : "未配置"}</p>
                </div>
                <span
                  className={`badge ${model.configured ? "blue-badge" : ""}`}
                >
                  {model.configured ? "已配置" : "待接入"}
                </span>
              </section>
            ))}
          </div>
          </details>
          <section className="card model-observability">
            <div className="section-heading">
              <h2>模型调用记录</h2>
              <span className="badge">来自当前讲义</span>
            </div>
            <div className="stat-grid">
              <div className="stat-card">
                <span>调用次数</span>
                <strong>{overview.calls.total}</strong>
              </div>
              <div className="stat-card">
                <span>总 Token</span>
                <strong>{overview.calls.tokens.toLocaleString()}</strong>
              </div>
              <div className="stat-card">
                <span>平均耗时</span>
                <strong>
                  {overview.calls.total
                    ? `${(overview.calls.average_ms / 1000).toFixed(1)} 秒`
                    : "—"}
                </strong>
              </div>
            </div>
            {overview.calls.records.length === 0 ? (
              <div className="empty-inline">
                <Database size={20} />
                <span>暂无真实模型调用记录</span>
              </div>
            ) : (
              <div className="call-table">
                <div className="call-row call-head">
                  <span>用途</span>
                  <span>模型</span>
                  <span>Token</span>
                  <span>状态</span>
                </div>
                {overview.calls.records.map((call) => (
                  <div className="call-row" key={call.id}>
                    <span>{purposeLabels[call.purpose]}</span>
                    <span>{call.model}</span>
                    <span>{call.input_tokens + call.output_tokens}</span>
                    <span
                      className={
                        call.status === "succeeded" ? "text-right" : "text-wrong"
                      }
                    >
                      {statusLabels[call.status]}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}
