/**
 * Approval queue — "who signs this off?"
 *
 * Advisory only: the agent writes `draft`, and every forward step is a human act. The
 * role check below hides buttons you may not press, but that is UX — the SERVER refuses
 * a transition your role does not own (403 from routes_approvals.py). The Streamlit
 * version only hid the button, which anyone could bypass with a POST.
 *
 * Reaching `executed` writes vrr_agent.adjustment_history, which is what the ρ learning
 * loop reads back.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type Adjustment, type QueueItem, type Stages } from "../api";
import { Badge, Banner, Card, DataTable, ErrorNote, Spinner, fmt } from "../components/ui";

interface Props { role: string; user: string }

export function ApprovalView({ role, user }: Props) {
  const [stages, setStages] = useState<Stages | null>(null);
  const [stage, setStage] = useState("draft");
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [history, setHistory] = useState<Adjustment[]>([]);
  const [note, setNote] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const refresh = useCallback(() => {
    setItems(null);
    api.queue(stage).then(setItems).catch(setErr);
    api.adjustments().then(setHistory).catch(() => {});
  }, [stage]);

  useEffect(() => { api.stages().then(setStages).catch(setErr); }, []);
  useEffect(refresh, [refresh]);

  if (err) return <ErrorNote error={err} />;
  if (!stages) return <Spinner />;

  const needed = stages.approver_for_stage[stage];
  const canAction = role === needed;

  async function act(item: QueueItem, kind: "advance" | "reject") {
    try {
      const res = kind === "advance"
        ? await api.advance(item.action_id, role, user)
        : await api.reject(item.action_id, role, user);
      setNote({
        tone: "good",
        text: kind === "advance"
          ? `${item.action_id} → ${res.to}` +
            ("wrote_adjustment_history" in res && res.wrote_adjustment_history
              ? " · wrote adjustment_history (the ρ loop reads this)" : "")
          : `${item.action_id} rejected`,
      });
      refresh();
    } catch (e) {
      setNote({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-lg font-semibold">Approval queue</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-slate-500">
          Advisory only — the agent writes <code className="font-mono">draft</code>; every
          forward step is a human act (core/approval.py). Executing writes{" "}
          <code className="font-mono">vrr_agent.adjustment_history</code>, which is what the
          ρ (response factor) learning loop reads back.
        </p>
      </header>

      <div className="flex items-center gap-3">
        <select
          className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm"
          value={stage}
          onChange={(e) => { setStage(e.target.value); setNote(null); }}
        >
          {stages.stages.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <p className="text-xs text-slate-500">
          {needed
            ? <>advances on <strong>{needed}</strong> sign-off — you are acting as{" "}
                <strong>{role}</strong>{canAction ? "" : " (switch role in the sidebar)"}</>
            : "terminal stage — no further transitions"}
        </p>
      </div>

      {note && <Banner tone={note.tone === "good" ? "good" : "bad"} title={note.text} />}

      {!items ? <Spinner /> : items.length === 0 ? (
        <p className="text-sm text-slate-500">No items in <code className="font-mono">{stage}</code>.</p>
      ) : (
        <div className="space-y-3">
          {items.map((d) => {
            const rec = typeof d.recommendation === "string"
              ? JSON.parse(d.recommendation) : d.recommendation;
            return (
              <Card
                key={d.action_id}
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    {d.pattern_name} · {fmt.month(String(d.vrr_date))} · {d.action_type}
                    <Badge tone={d.severity === "high" ? "red" : "slate"}>{d.severity}</Badge>
                    {d.confidence && <Badge>confidence {d.confidence}</Badge>}
                  </span>
                }
                sub={
                  <>action_id <code className="font-mono">{d.action_id}</code> · driver{" "}
                  <code className="font-mono">{d.driver ?? "—"}</code> · raised by{" "}
                  {d.stage_by ?? "—"} · run {d.run_id ?? "—"}</>
                }
              >
                {d.narrative && (
                  <pre className="whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs leading-relaxed text-slate-800">
                    {d.narrative}
                  </pre>
                )}
                {rec?.injector_changes?.length ? (
                  <div className="mt-3">
                    <DataTable rows={rec.injector_changes} max={220} />
                  </div>
                ) : null}

                {canAction && stages.approver_for_stage[stage] && (
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => act(d, "advance")}
                      className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white"
                    >
                      ✅ Approve
                    </button>
                    <button
                      onClick={() => act(d, "reject")}
                      className="rounded border border-slate-300 px-3 py-1.5 text-sm"
                    >
                      ❌ Reject
                    </button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      <Card
        title="Executed adjustments"
        sub="The ρ-learning input: predicted vs actual post-VRR per executed change."
      >
        <DataTable rows={history as unknown as Record<string, unknown>[]} />
      </Card>
    </div>
  );
}
