// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import App from "./App";
import { DocumentsPage } from "./DocumentsPage";
import { PracticePage } from "./PracticePage";
import { ReviewPage } from "./ReviewPage";
import { EvidencePanel } from "./EvidencePanel";
import { AIWorkbench } from "./AIWorkbench";
import { readSession, writeSession } from "./persistence";
import type { AIOverview, Question, Quiz, StudyDocument } from "./types";

const doc: StudyDocument = {
  id: "doc",
  name: "测试讲义",
  page_count: 8,
  status: "ready",
  is_demo: true,
  question_count: 12,
  topic_count: 1,
  source_note: "离线示例，尚未人工审定",
  topics: [
    {
      id: "topic",
      title: "栈",
      description: "栈的基本操作",
      status: "ready",
      question_count: 12,
      misconceptions: [],
    },
  ],
  samples: [],
  jobs: [],
  candidate_topics: [],
};
function question(id: string): Question {
  return {
    id,
    topic_id: "topic",
    topic_title: "栈",
    stem: `测试题 ${id}`,
    origin: "bank",
    source_note: "离线示例",
    kind: "concept",
    is_retest: false,
    options: ["A", "B", "C", "D"].map((letter) => ({
      letter,
      text: `选项 ${letter}`,
    })),
  };
}
function routes(quizzes: Record<string, Quiz>, fail?: () => boolean) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path === "/api/health")
      return Response.json({
        mode: "demo",
        version: "2.0",
        upload_enabled: false,
      });
    if (path === "/api/documents") return Response.json({ documents: [doc] });
    if (path === "/api/documents/doc") return Response.json(doc);
    if (path.startsWith("/api/quizzes/")) {
      if (fail?.()) throw new Error("offline");
      const q = quizzes[path.split("/").at(-1)!];
      return q
        ? Response.json(q)
        : Response.json({ detail: "不存在" }, { status: 404 });
    }
    throw new Error(`Unexpected test endpoint: ${path}`);
  });
}
beforeEach(() => {
  localStorage.clear();
  location.hash = "practice";
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  Element.prototype.scrollIntoView = vi.fn();
});

it("renders provider call purposes and all persisted statuses from the API contract", async () => {
  const overview: AIOverview = {
    is_demo: false, prompt_version: "zhizheng-v2.2", models: [],
    counts: { pages: 1, sentences: 1, chunks: 1, questions: 0 },
    chunks: [{ id: "chunk", page_index: 1, text: "栈只能从栈顶删除。", sentence_count: 1 }],
    misconceptions: [], generation: { accepted: 0, rejected: 0, repairs: 0 },
    calls: { total: 3, tokens: 12, average_ms: 1000, records: [
      { id: "embedding", purpose: "embedding", model: "embed-model", input_tokens: 12, output_tokens: 0, status: "succeeded" },
      { id: "review", purpose: "blind_review", model: "review-model", input_tokens: 0, output_tokens: 0, status: "failed" },
      { id: "topics", purpose: "candidate_topics", model: "topic-model", input_tokens: 0, output_tokens: 0, status: "interrupted" },
    ] },
  };
  vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json(overview));
  render(<AIWorkbench documents={[doc]} selectedId="doc" onSelect={vi.fn()} onPractice={vi.fn()} />);
  const success = (await screen.findByText("embed-model")).parentElement!;
  expect(within(success).getByText("向量化")).toBeTruthy();
  expect(within(success).getByText("成功").className).toBe("text-right");
  const failure = screen.getByText("review-model").parentElement!;
  expect(within(failure).getByText("独立复核")).toBeTruthy();
  expect(within(failure).getByText("失败")).toBeTruthy();
  const interrupted = screen.getByText("topic-model").parentElement!;
  expect(within(interrupted).getByText("知识点提取")).toBeTruthy();
  expect(within(interrupted).getByText("已中断")).toBeTruthy();
  expect(screen.queryByText("暂无真实模型调用记录")).toBeNull();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("restores the exact question and both parent rounds after a page reload", async () => {
  const quizzes = {
    round: {
      id: "round",
      questions: [question("one"), question("two"), question("three")],
    },
    first: { id: "first", questions: [question("first")] },
    second: { id: "second", questions: [question("second")] },
  };
  routes(quizzes);
  writeSession({
    current: { id: "second", index: 0 },
    parents: [
      { id: "round", index: 1 },
      { id: "first", index: 0 },
    ],
    pendingJob: null,
  });
  render(<App />);
  expect(await screen.findByText("测试题 second")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "返回上一轮练习" }));
  expect(await screen.findByText("测试题 first")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "返回上一轮练习" }));
  expect(await screen.findByText("测试题 two")).toBeTruthy();
  await waitFor(() =>
    expect(readSession().current).toEqual({ id: "round", index: 1 }),
  );
  expect(readSession().parents).toEqual([]);
});

it("preserves the saved round during an outage and reconnects without a reload", async () => {
  let offline = true;
  routes(
    { saved: { id: "saved", questions: [question("saved")] } },
    () => offline,
  );
  writeSession({
    current: { id: "saved", index: 0 },
    parents: [],
    pendingJob: null,
  });
  render(<App />);
  const retry = await screen.findByRole("button", { name: "重新连接" });
  expect(readSession().current?.id).toBe("saved");
  offline = false;
  fireEvent.click(retry);
  expect(await screen.findByText("测试题 saved")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "重新连接" })).toBeNull();
});

it("lets users confirm a manual topic when extraction returns no candidates", async () => {
  const confirm = vi.fn();
  render(
    <DocumentsPage
      documents={[doc]}
      document={{
        ...doc,
        is_demo: false,
        status: "awaiting_topics",
        topics: [],
        candidate_topics: [],
        samples: [{ page_index: 1, text: "栈是后进先出的线性表。" }],
      }}
      selectedId="doc"
      selectedTopics={[]}
      health={{ mode: "live", version: "2", upload_enabled: true }}
      busy=""
      onSelect={vi.fn()}
      onTopics={vi.fn()}
      onStart={vi.fn()}
      onUpload={vi.fn()}
      onConfirmTopics={confirm}
      onResume={vi.fn()}
      onRefresh={vi.fn()}
    />,
  );
  expect(screen.getByText(/没有可用候选/)).toBeTruthy();
  const input = await screen.findByRole("textbox", { name: "知识点 1 名称" });
  fireEvent.change(input, { target: { value: "栈的操作" } });
  fireEvent.click(screen.getByRole("button", { name: "确认并生成练习" }));
  expect(confirm).toHaveBeenCalledWith([
    { title: "栈的操作", description: "" },
  ]);
});

it("resumes polling a saved generation job after a temporary network error", async () => {
  const mock = routes({
    original: { id: "original", questions: [question("original")] },
  });
  const ordinary = mock.getMockImplementation()!;
  let jobReads = 0;
  mock.mockImplementation(async (input, init) => {
    if (String(input) === "/api/jobs/pending") {
      if (jobReads++ === 0) throw new Error("offline");
      return Response.json({
        id: "pending",
        status: "completed",
        progress: 100,
        quiz_id: "new-round",
        question: question("generated"),
      });
    }
    return ordinary(input, init);
  });
  writeSession({
    current: { id: "original", index: 0 },
    parents: [{ id: "original", index: 0 }],
    pendingJob: "pending",
  });
  render(<App />);
  const retry = await screen.findByRole("button", { name: "重新读取进度" });
  expect(readSession().pendingJob).toBe("pending");
  fireEvent.click(retry);
  expect(await screen.findByText("测试题 generated")).toBeTruthy();
  await waitFor(() => expect(readSession().pendingJob).toBeNull());
  expect(readSession().parents).toEqual([{ id: "original", index: 0 }]);
});

it("requires an explicit confidence choice before sending an answer", () => {
  const submit = vi.fn();
  render(
    <PracticePage
      quiz={{ id: "q", questions: [question("q")] }}
      index={0}
      busy=""
      jobStatus=""
      hasParent={false}
      onSubmit={submit}
      onVariant={vi.fn()}
      onEvidence={vi.fn()}
      onIndex={vi.fn()}
      onReturn={vi.fn()}
      onDocuments={vi.fn()}
      onReview={vi.fn()}
    />,
  );
  const button = screen.getByRole<HTMLButtonElement>("button", {
    name: "提交答案",
  });
  expect(button.disabled).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: /选项 A/ }));
  expect(button.disabled).toBe(true);
  fireEvent.click(screen.getByRole("radio", { name: /不太确定/ }));
  expect(button.disabled).toBe(false);
  fireEvent.click(button);
  expect(submit).toHaveBeenCalledWith("A", "unsure");
});

it("distinguishes an unavailable review from an empty learning record", () => {
  const refresh = vi.fn();
  render(
    <ReviewPage
      review={null}
      loading={false}
      busy=""
      onRefresh={refresh}
      onPractice={vi.fn()}
      onEvidence={vi.fn()}
      onVariant={vi.fn()}
      onRevisit={vi.fn()}
    />,
  );
  expect(screen.getByText("暂时无法读取复习记录")).toBeTruthy();
  expect(screen.queryByText("开始第一次练习")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect(refresh).toHaveBeenCalledOnce();
});

it("merges adjacent evidence contexts into one page with no duplicate sentence", () => {
  const contexts = [
    { id: "p1s1", text: "上文", highlight: false },
    { id: "p1s2", text: "直接依据", highlight: true },
    { id: "p1s3", text: "纠正依据", highlight: true },
  ];
  const { container } = render(
    <EvidencePanel
      loading={false}
      error=""
      refuteIds={["p1s3"]}
      evidence={{
        document_name: "讲义",
        sentences: [
          {
            id: "p1s2",
            text: "直接依据",
            page_index: 1,
            char_start: 3,
            char_end: 7,
            context: contexts,
          },
          {
            id: "p1s3",
            text: "纠正依据",
            page_index: 1,
            char_start: 8,
            char_end: 12,
            context: contexts.slice(1),
          },
        ],
      }}
    />,
  );
  expect(screen.getAllByText("第 1 页")).toHaveLength(1);
  expect(screen.getAllByText("直接依据")).toHaveLength(1);
  expect(container.querySelectorAll("mark")).toHaveLength(2);
  expect(container.querySelector(".refutation")?.textContent).toBe("纠正依据");
});

it("supports answer shortcut keys and Enter only after confidence is chosen", () => {
  const submit = vi.fn();
  render(
    <PracticePage
      quiz={{ id: "q", questions: [question("q")] }}
      index={0}
      busy=""
      jobStatus=""
      hasParent={false}
      onSubmit={submit}
      onVariant={vi.fn()}
      onEvidence={vi.fn()}
      onIndex={vi.fn()}
      onReturn={vi.fn()}
      onDocuments={vi.fn()}
      onReview={vi.fn()}
    />,
  );
  fireEvent.keyDown(window, { key: "b" });
  fireEvent.keyDown(window, { key: "Enter" });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("radio", { name: "确定" }));
  fireEvent.keyDown(window, { key: "Enter" });
  expect(submit).toHaveBeenCalledWith("B", "sure");
});
