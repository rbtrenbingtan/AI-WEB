export interface QuizLocation {
  id: string;
  index: number;
}
export interface StudySession {
  current: QuizLocation | null;
  parents: QuizLocation[];
  pendingJob: string | null;
}
const KEY = "zhizheng.session.v2";
const empty = (): StudySession => ({
  current: null,
  parents: [],
  pendingJob: null,
});
const valid = (x: unknown): x is QuizLocation =>
  typeof x === "object" &&
  x !== null &&
  typeof (x as QuizLocation).id === "string" &&
  !!(x as QuizLocation).id &&
  Number.isInteger((x as QuizLocation).index) &&
  (x as QuizLocation).index >= 0;
export function readSession(): StudySession {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) {
      const value = JSON.parse(raw) as StudySession;
      return {
        current: valid(value.current) ? value.current : null,
        parents: Array.isArray(value.parents)
          ? value.parents.filter(valid)
          : [],
        pendingJob:
          typeof value.pendingJob === "string" ? value.pendingJob : null,
      };
    }
    const legacy = localStorage.getItem("zhizheng.quiz");
    return { ...empty(), current: legacy ? { id: legacy, index: 0 } : null };
  } catch {
    return empty();
  }
}
// Store only navigation pointers. Answers and feedback always come from the server.
export function writeSession(value: StudySession) {
  try {
    localStorage.setItem(KEY, JSON.stringify(value));
    localStorage.removeItem("zhizheng.quiz");
  } catch {
    /* Browser storage can be disabled; the current page still works. */
  }
}
