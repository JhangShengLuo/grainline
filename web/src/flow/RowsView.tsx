import type { Table } from "../types";

const HIDE = new Set(["source", "items", "landing_hit_id"]);

/** 一張中繼表裡的幾列，表頭標出粒度。 */
export function RowsView({ table, columns, danger }: { table: Table; columns?: string[]; danger?: boolean }) {
  const cols = columns ?? Array.from(new Set(table.rows.flatMap((r) => Object.keys(r)))).filter((c) => !HIDE.has(c)).slice(0, 6);
  return (
    <div className={`rows ${danger ? "rows-danger" : ""}`}>
      <div className="rows-head">
        <code>{table.model}</code>
        <span className="grain">1 列 = {table.grain}</span>
        <span className="row-count">{table.rows.length} 列</span>
      </div>
      <table>
        <thead>
          <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
        </thead>
        <tbody>
          {table.rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{format(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function format(v: unknown): string {
  if (v === undefined || v === null) return "";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(2);
  const s = String(v);
  return s.length > 19 ? `${s.slice(0, 18)}…` : s;
}
