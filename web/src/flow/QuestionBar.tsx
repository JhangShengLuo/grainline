import type { Question } from "../types";

export function QuestionBar({ questions, selected, onSelect }: {
  questions: Question[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="questions" role="tablist" aria-label="行銷問題">
      {questions.map((q) => (
        <button
          key={q.id}
          role="tab"
          aria-selected={q.id === selected}
          className={`question ${q.id === selected ? "selected" : ""}`}
          onClick={() => onSelect(q.id)}
        >
          <span className="question-title">{q.title}</span>
          <span className="question-ask">{q.ask}</span>
          <span className="phrasings">
            {q.phrasings.map((p) => (
              <span key={p} className="phrasing">「{p}」</span>
            ))}
          </span>
        </button>
      ))}
    </div>
  );
}
