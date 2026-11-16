import { useEffect, useRef } from "react";
import { BookOpen, Loader2, X } from "lucide-react";
import type { Evidence } from "./types";

interface Props {
  evidence: Evidence | null;
  loading: boolean;
  error: string;
  refuteIds?: string[];
}

export function EvidencePanel({
  evidence,
  loading,
  error,
  refuteIds = [],
}: Props) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    panel.current?.querySelector("mark")?.scrollIntoView({ block: "nearest" });
  }, [evidence]);
  if (loading)
    return (
      <div className="loading-state">
        <Loader2 className="spin" />
        加载原文…
      </div>
    );
  if (error)
    return (
      <div className="alert error" role="alert">
        {error}
      </div>
    );
  if (!evidence) return null;
  // Merge overlapping contexts so each sentence appears once per page.
  const pages = new Map<
    number,
    Map<string, Evidence["sentences"][number]["context"][number]>
  >();
  for (const sentence of evidence.sentences) {
    if (!pages.has(sentence.page_index))
      pages.set(sentence.page_index, new Map());
    for (const item of sentence.context)
      pages.get(sentence.page_index)!.set(item.id, item);
  }
  return (
    <div className="source-content" ref={panel}>
      <p className="source-document">
        <BookOpen size={18} />
        {evidence.document_name}
      </p>
      {[...pages]
        .sort(([a], [b]) => a - b)
        .map(([page, sentences]) => (
          <article className="source-page" key={page}>
            <h3>第 {page} 页</h3>
            <div className="source-text">
              {[...sentences.values()]
                .sort((a, b) =>
                  a.id.localeCompare(b.id, undefined, { numeric: true }),
                )
                .map((sentence) => (
                  <p
                    key={sentence.id}
                    className={
                      refuteIds.includes(sentence.id) ? "refutation" : undefined
                    }
                  >
                    {sentence.highlight ? (
                      <mark>{sentence.text}</mark>
                    ) : (
                      sentence.text
                    )}
                  </p>
                ))}
            </div>
          </article>
        ))}
    </div>
  );
}

export function EvidenceDrawer(props: Props & { onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    close.current?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") props.onClose();
      if (event.key === "Tab") {
        event.preventDefault();
        close.current?.focus();
      }
    };
    window.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = overflow;
      window.removeEventListener("keydown", key);
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [props.onClose]);
  return (
    <div className="drawer-layer">
      <button
        className="drawer-scrim"
        onClick={props.onClose}
        aria-label="关闭原文抽屉"
      />
      <section
        className="evidence-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-title"
      >
        <header className="section-heading">
          <h2 id="source-title">讲义原文</h2>
          <button
            className="icon-button"
            ref={close}
            onClick={props.onClose}
            aria-label="关闭原文"
          >
            <X />
          </button>
        </header>
        <EvidencePanel {...props} />
      </section>
    </div>
  );
}
