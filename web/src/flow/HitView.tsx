import { useState } from "react";

import type { Step } from "../types";

/** 一筆 hit 的 URL：每個參數一顆 chip；這題需要、但這筆 hit 沒帶的參數用紅色虛線標出來。 */
export function HitView({ step, required }: { step: Step; required: string[] }) {
  const [open, setOpen] = useState(false);
  const present = new Set(step.params.map((p) => p.key));
  const absent = required.filter((r) => !present.has(r));
  return (
    <div className="hit">
      <div className="hit-path">/collect?</div>
      <div className="chips">
        {step.params.map((p) =>
          p.key === "items" ? (
            <button key={p.key} className="chip chip-items" onClick={() => setOpen(!open)} aria-expanded={open}>
              <b>items</b>=[{(JSON.parse(p.value) as unknown[]).length} 個商品]
            </button>
          ) : (
            <span key={p.key} className={`chip ${p.key.startsWith("utm_") ? "chip-utm" : ""}`}>
              <b>{p.key}</b>={p.value}
            </span>
          ),
        )}
        {absent.map((key) => (
          <span key={key} className="chip chip-missing" data-missing={key} title="這題需要，但這筆 hit 沒有">
            <b>{key}</b>=?
          </span>
        ))}
      </div>
      {open && <pre className="items-json">{JSON.stringify(JSON.parse(step.params.find((p) => p.key === "items")!.value), null, 1)}</pre>}
    </div>
  );
}

/** 這題對某個事件要求、而規格可能沒有的參數 */
export function requiredFor(ev: string, requires: { event: string; param: string }[]): string[] {
  return requires.filter((r) => r.event === ev).map((r) => r.param);
}
