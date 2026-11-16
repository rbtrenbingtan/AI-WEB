export type Confidence = "sure" | "unsure" | "guess";
export type Page = "documents" | "practice" | "review" | "ai";
export interface Health {
  mode: "demo" | "live";
  version: string;
  upload_enabled: boolean;
}
export interface Misconception {
  id: string;
  label: string;
  description: string;
  question_count: number;
  refute_sentence_ids: string[];
}
export interface Topic {
  id: string;
  title: string;
  description: string;
  status: string;
  question_count: number;
  reason?: string;
  misconceptions: Misconception[];
}
export interface DocumentSummary {
  id: string;
  name: string;
  page_count: number;
  status: string;
  is_demo: boolean;
  question_count: number;
  topic_count: number;
  source_note: string;
}
export interface Candidate {
  title: string;
  description: string;
}
export interface StudyDocument extends DocumentSummary {
  topics: Topic[];
  samples: { page_index: number; text: string }[];
  jobs: Job[];
  candidate_topics: Candidate[];
  warnings?: string[];
}
export interface Option {
  letter: string;
  text: string;
}
export interface Question {
  id: string;
  topic_id: string;
  topic_title: string;
  target_label?: string;
  stem: string;
  options: Option[];
  origin: "bank" | "live";
  source_note: string;
  kind: string;
  is_retest: boolean;
  result?: Result;
}
export interface Quiz {
  id: string;
  questions: Question[];
}
export interface Result {
  question_id: string;
  correct: boolean;
  selected: string;
  confidence: Confidence;
  answer_letter: string;
  explanation: string;
  options: (Option & { feedback: string; is_correct: boolean })[];
  misconception: null | {
    id: string;
    label: string;
    description: string;
    refute_sentence_ids: string[];
  };
  evidence_sentence_ids: string[];
  document_id: string;
  duplicate: boolean;
}
export interface Job {
  id: string;
  status: string;
  progress: number;
  error?: string;
  quiz_id?: string;
  question?: Question;
}
export interface Evidence {
  document_name: string;
  sentences: {
    id: string;
    text: string;
    page_index: number;
    char_start: number;
    char_end: number;
    context: { id: string; text: string; highlight: boolean }[];
  }[];
}
export interface ReviewMisconception {
  id: string;
  topic_id: string;
  topic_title: string;
  label: string;
  description: string;
  status: "pending" | "improving" | "corrected";
  wrong_count: number;
  sure_wrong_count: number;
  last_wrong_at: string;
  distinct_correct: number;
  document_id: string;
  question_id: string;
  refute_sentence_ids: string[];
}
export interface ReviewAttempt {
  question_id: string;
  quiz_id: string;
  stem: string;
  correct: boolean;
  confidence: Confidence;
  created_at: string;
  document_id: string;
}
export interface Review {
  total: number;
  correct: number;
  quadrants: Record<
    | "sure_correct"
    | "sure_wrong"
    | "unsure_correct"
    | "unsure_wrong"
    | "guess_correct"
    | "guess_wrong",
    number
  >;
  topics: { id: string; title: string; correct: number; total: number }[];
  misconceptions: ReviewMisconception[];
  attempts: ReviewAttempt[];
  guess_correct: Pick<
    ReviewAttempt,
    "question_id" | "quiz_id" | "stem" | "document_id"
  >[];
}
export interface AIOverview {
  is_demo: boolean;
  prompt_version: string;
  models: { role: string; name: string; configured: boolean }[];
  counts: {
    pages: number;
    sentences: number;
    chunks: number;
    questions: number;
  };
  chunks: {
    id: string;
    page_index: number;
    text: string;
    sentence_count: number;
  }[];
  misconceptions: { id: string; label: string; refutation: string }[];
  generation: { accepted: number; rejected: number; repairs: number };
  calls: {
    total: number;
    tokens: number;
    average_ms: number;
    records: {
      id: string;
      purpose: "candidate_topics" | "misconceptions" | "review_misconceptions" | "generate" | "blind_review" | "repair" | "embedding";
      model: string;
      input_tokens: number;
      output_tokens: number;
      status: "succeeded" | "failed" | "interrupted";
    }[];
  };
}
