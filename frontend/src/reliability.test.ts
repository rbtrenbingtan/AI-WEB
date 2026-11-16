// @vitest-environment jsdom
import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { readSession, writeSession } from "./persistence";
import { api, ApiError } from "./api";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());
describe("learning navigation survives failures", () => {
  it("restores nested parent rounds and a running job", () => {
    const value = {
      current: { id: "variant-2", index: 0 },
      parents: [
        { id: "round", index: 2 },
        { id: "variant-1", index: 0 },
      ],
      pendingJob: "job-2",
    };
    writeSession(value);
    expect(readSession()).toEqual(value);
    expect(localStorage.getItem("zhizheng.session.v2")).not.toContain(
      "answer_letter",
    );
  });
  it("retains a legacy round until migration has been saved", () => {
    localStorage.setItem("zhizheng.quiz", "original");
    expect(readSession().current?.id).toBe("original");
    writeSession(readSession());
    expect(localStorage.getItem("zhizheng.quiz")).toBeNull();
    expect(readSession().current?.id).toBe("original");
  });
  it("ignores corrupted storage and invalid indexes", () => {
    localStorage.setItem("zhizheng.session.v2", "{");
    expect(readSession().current).toBeNull();
    localStorage.setItem(
      "zhizheng.session.v2",
      JSON.stringify({
        current: { id: "x", index: -1 },
        parents: [
          { id: "ok", index: 1 },
          { id: "bad", index: "1" },
        ],
      }),
    );
    expect(readSession().parents).toEqual([{ id: "ok", index: 1 }]);
    expect(readSession().current).toBeNull();
  });
  it("does not erase navigation on transient network failure", async () => {
    writeSession({
      current: { id: "saved", index: 2 },
      parents: [],
      pendingJob: null,
    });
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network"));
    await expect(api("/quizzes/saved")).rejects.toMatchObject({ status: 0 });
    expect(readSession().current).toEqual({ id: "saved", index: 2 });
  });
  it("distinguishes an inaccessible round from a connection failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "练习不存在" }), { status: 404 }),
    );
    await expect(api("/quizzes/missing")).rejects.toMatchObject({
      status: 404,
    });
  });
  it("keeps same-origin credentials and JSON body on submission", async () => {
    const request = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(
        new Response(JSON.stringify({ correct: true }), { status: 200 }),
      );
    await api("/questions/q/attempts", {
      method: "POST",
      body: JSON.stringify({ selected: "A", confidence: "sure" }),
    });
    expect(request.mock.calls[0][1]?.credentials).toBe("same-origin");
    expect(
      new Headers(request.mock.calls[0][1]?.headers).get("content-type"),
    ).toBe("application/json");
  });
});
