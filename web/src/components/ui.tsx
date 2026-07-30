/**
 * Small shared primitives. Deliberately few: this is a workbench for one engineer
 * reading numbers, not a design system.
 *
 * The one piece of real logic here is `provenanceLine` — where an answer came from, in
 * analyst English. It is the single most-read line in the app, so it lives in one place
 * with its own test rather than being re-derived per view.
 */
import type { ChatMeta } from "../api";
import type { ReactNode } from "react";

export function Card({ title, sub, children, className = "" }: {
  title?: ReactNode; sub?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || sub) && (
        <header className="border-b border-slate-100 px-4 py-3">
          {title && <h2 className="text-sm font-semibold text-slate-800">{title}</h2>}
          {sub && <p className="mt-0.5 text-xs leading-relaxed text-slate-500">{sub}</p>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Metric({ label, value, foot, tone = "plain" }: {
  label: string; value: ReactNode; foot?: ReactNode;
  tone?: "plain" | "good" | "warn" | "bad";
}) {
  const toneClass = {
    plain: "text-slate-900", good: "text-signal", warn: "text-suspect", bad: "text-offtarget",
  }[tone];
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${toneClass}`}>{value}</div>
      {foot && <div className="mt-0.5 text-xs text-slate-500">{foot}</div>}
    </div>
  );
}

export function Banner({ tone, title, children }: {
  tone: "good" | "warn" | "bad" | "info"; title: ReactNode; children?: ReactNode;
}) {
  const styles = {
    good: "border-green-200 bg-green-50 text-green-900",
    warn: "border-amber-200 bg-amber-50 text-amber-900",
    bad: "border-red-200 bg-red-50 text-red-900",
    info: "border-slate-200 bg-slate-50 text-slate-700",
  }[tone];
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm ${styles}`}>
      <div className="font-medium">{title}</div>
      {children && <div className="mt-1 text-xs leading-relaxed opacity-90">{children}</div>}
    </div>
  );
}

export function Badge({ children, tone = "slate" }: {
  children: ReactNode; tone?: "slate" | "green" | "amber" | "red";
}) {
  const styles = {
    slate: "bg-slate-100 text-slate-700", green: "bg-green-100 text-green-800",
    amber: "bg-amber-100 text-amber-800", red: "bg-red-100 text-red-800",
  }[tone];
  return (
    <span className={`inline-flex rounded px-1.5 py-0.5 text-[11px] font-medium ${styles}`}>
      {children}
    </span>
  );
}

export function Spinner({ label = "loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-8 text-sm text-slate-500">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-300 border-t-slate-600" />
      {label}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <Banner tone="bad" title="Request failed">
      {msg}. Is the API up (<code className="font-mono">make api</code>) and seeded
      (<code className="font-mono">make seed</code>)?
    </Banner>
  );
}

/** A scrollable data table. Keys of the first row become the columns. */
export function DataTable({ rows, max = 400 }: {
  rows: Record<string, unknown>[]; max?: number;
}) {
  if (!rows.length) return <p className="text-sm text-slate-500">No rows.</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="overflow-auto rounded border border-slate-200" style={{ maxHeight: max }}>
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-50 text-left text-slate-600">
          <tr>
            {cols.map((c) => <th key={c} className="whitespace-nowrap px-2 py-1.5 font-medium">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate-100 hover:bg-slate-50">
              {cols.map((c) => (
                <td key={c} className="whitespace-nowrap px-2 py-1 tabular-nums text-slate-700">
                  {fmtCell(r[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function fmtCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(4);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ------------------------------------------------------------ provenance ----
const INTENT_LABEL: Record<string, string> = {
  explain: "Computed from your tables",
  recommend: "Computed from your tables",
  audit: "Input audit",
  lineage: "Lineage",
  completions: "Completion list",
  portfolio: "Portfolio scan",
  data_quality: "Data-quality check",
  knowledge: "From your documents",
  general: "General knowledge — not your data",
};

/**
 * One caption saying who produced an answer and whether the gate cleared it.
 *
 * Ported from the Streamlit drawer, where the raw version (`intent: general · LLM ·
 * gate n/a (no field figures claimed)`) read like a debug log. The information is the
 * trust argument, so it stays visible — but in English.
 */
export function provenanceLine(intent: string | undefined, meta: ChatMeta = {}): string {
  const source = INTENT_LABEL[intent ?? ""] ?? (intent ?? "answer").replace(/_/g, " ");
  const gate = meta.gate ?? "";
  const model = meta.model ?? "LLM";
  let phrasing: string;
  // Two very different reasons an answer has no LLM in it, and conflating them reads as
  // a broken model when nothing is wrong:
  //   gate "skipped (no local LLM running)"  → Ollama really is down
  //   no gate at all                         → this INTENT never uses a model. Lineage,
  //     portfolio, completions and data-quality answers are assembled from tool output
  //     by design; there is no prose for a model to write.
  if (!meta.llm && gate.startsWith("skipped"))
    phrasing = "⚪ no model running — computed answer";
  else if (!meta.llm) phrasing = "✅ deterministic answer — no model needed";
  else if (gate.startsWith("REJECTED"))
    phrasing = `⚠️ ${model} phrasing rejected — computed wording shown`;
  else if (gate.includes("abstained")) phrasing = "abstained — nothing above the retrieval floor";
  else if (gate.includes("repair")) phrasing = `${model} phrasing · ✅ gate passed after one repair`;
  else if (gate.startsWith("n/a")) phrasing = `${model} · no field figures to verify`;
  else phrasing = `${model} phrasing · ✅ gate passed`;
  const tools = meta.tools_called?.length ? ` · tools: ${meta.tools_called.join(", ")}` : "";
  return `${source} · ${phrasing}${tools}`;
}

/** A gate violation as a sentence, not a dict repr. */
export function violationLine(v: { kind?: string; term?: string; detail?: string }): string {
  return v.detail ?? `${v.kind ?? "violation"} on ${v.term ?? "?"}`;
}

export const fmt = {
  vrr: (v: number | null | undefined) => (v == null ? "—" : v.toFixed(3)),
  bbl: (v: number | null | undefined) => (v == null ? "—" : Math.round(v).toLocaleString()),
  signed: (v: number, dp = 4) => `${v >= 0 ? "+" : ""}${v.toFixed(dp)}`,
  pct: (v: number) => `${(v * 100).toFixed(1)}%`,
  month: (iso: string) =>
    new Date(iso + (iso.length === 10 ? "T00:00:00" : "")).toLocaleDateString(undefined, {
      month: "short", year: "numeric",
    }),
};
