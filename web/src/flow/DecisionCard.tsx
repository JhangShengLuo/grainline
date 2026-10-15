import { useState } from "react";

export function DecisionCard({ text, onClose }: { text: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/markdown" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "定案.md";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <section className="decision" aria-label="定案卡">
      <div className="decision-actions">
        <button onClick={() => navigator.clipboard.writeText(text).then(() => setCopied(true))}>{copied ? "已複製" : "複製"}</button>
        <button onClick={download}>下載 .md</button>
        <button onClick={onClose} aria-label="關閉">關閉</button>
      </div>
      <pre>{text}</pre>
    </section>
  );
}
