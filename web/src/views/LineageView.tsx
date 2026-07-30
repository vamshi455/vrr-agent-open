/**
 * Lineage & audit — "do I believe this number?"
 *
 * The audit strip is the point: the recomputed VRR is rebuilt from raw daily rows
 * through core.physics IN THIS REQUEST, not read back from the curated table. A match
 * to ~1e-16 is evidence; a restatement of the stored value would be decoration.
 *
 * Below it, the derivation chain and the per-completion rows show HOW: root inputs →
 * the pattern pressure used → the PVT method that produced the FVFs → every derived
 * term. That is the same chain Unity Catalog registers as table-level lineage.
 */
import { useEffect, useState } from "react";
import { api, type AuditResult, type Lineage } from "../api";
import { Badge, Card, DataTable, ErrorNote, Metric, Spinner, fmt } from "../components/ui";

interface Props { patternId: string; period: string }

export function LineageView({ patternId, period }: Props) {
  const [audit, setAudit] = useState<AuditResult | null>(null);
  const [lin, setLin] = useState<Lineage | null>(null);
  const [err, setErr] = useState<unknown>(null);

  useEffect(() => {
    if (!patternId || !period) return;
    setAudit(null); setLin(null);
    api.audit(patternId, period).then(setAudit).catch(setErr);
    api.lineage(patternId, period).then(setLin).catch(setErr);
  }, [patternId, period]);

  if (err) return <ErrorNote error={err} />;
  if (!lin || !audit) return <Spinner label="recomputing from raw rows…" />;

  const chain = [
    lin.sources.raw_volumes, lin.sources.pressure, lin.sources.pvt,
    lin.sources.lineage_layer, lin.sources.aggregate,
  ].filter(Boolean);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold">
          How the {fmt.month(period)} VRR was computed
        </h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-500">
          Recomputed independently in this request from{" "}
          <code className="font-mono">{audit.provenance?.recomputed_from?.join("`, `")}</code>{" "}
          via <code className="font-mono">{audit.provenance?.code}</code> — not read back
          from the curated table.
        </p>
      </header>

      {audit.ok && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <Metric label="Stored VRR" value={audit.stored.vrr.toFixed(6)}
                    foot={`run_id ${audit.stored.run_id ?? "—"}`} />
            <Metric label="Recomputed from raw" value={audit.recomputed.vrr.toFixed(6)}
                    foot={`${audit.n_raw_rows} daily rows`} />
            <Metric label="Difference" value={audit.difference.toExponential(2)}
                    tone={audit.matches ? "good" : "bad"}
                    foot={audit.matches ? "✅ verified" : "⚠️ mismatch"} />
          </div>
          <p className="text-xs text-slate-500">
            PVT lookup methods in this period:{" "}
            {audit.pvt_methods.map((m) => (
              <span key={m} className="mr-1">
                <Badge tone={m === "exact" || m === "interpolated" ? "green" : "amber"}>{m}</Badge>
              </span>
            ))}
            {audit.low_confidence_inputs && (
              <span className="text-suspect"> ⚠️ low-confidence — no valve change may be
                proposed on this period</span>
            )}
          </p>
        </>
      )}

      <Card title="Derivation chain">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {chain.map((s, i) => (
            <span key={s} className="flex items-center gap-2">
              <code className="rounded bg-slate-100 px-2 py-1 font-mono">{s}</code>
              {i < chain.length - 1 && <span className="text-slate-400">→</span>}
            </span>
          ))}
        </div>
      </Card>

      <Card title="Formulas" sub="core.physics — the same code the recompute above ran.">
        <table className="w-full text-xs">
          <tbody>
            {Object.entries(lin.formulas).map(([term, f]) => (
              <tr key={term} className="border-b border-slate-100 last:border-0">
                <td className="w-40 py-1.5 pr-3 font-medium text-slate-700">{term}</td>
                <td className="py-1.5 font-mono text-slate-600">{f}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card
        title="Per-completion contributions"
        sub="One row per completion for this month: root inputs, the pattern pressure used, the PVT method that produced the FVFs, and every derived term."
      >
        <DataTable rows={lin.completions} max={480} />
      </Card>

      <Card title="Roll-up" sub="What those rows sum to — and the VRR they imply.">
        <DataTable rows={[{
          ...lin.term_totals,
          prod_res_bbl: lin.recomputed_from_terms.prod_res_bbl,
          inj_res_bbl: lin.recomputed_from_terms.inj_res_bbl,
          vrr: lin.recomputed_from_terms.vrr,
        }]} />
        <p className="mt-2 text-xs text-slate-500">
          Unity Catalog OSS registers these tables as the catalog-of-record, so this chain
          is also visible as table-level lineage (<code className="font-mono">make register</code>).
        </p>
      </Card>
    </div>
  );
}
