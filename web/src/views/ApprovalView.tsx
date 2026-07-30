/**
 * Approval board — where every proposed change is, at a glance.
 *
 * Swim lanes rather than a filtered list, because the question this view answers is
 * "where is work stuck?", and a dropdown showing one stage at a time cannot answer it.
 * Lane colour matches the stage colour used everywhere else, so a card tells you where
 * it sits before you read it.
 *
 * Advisory only: the agent writes `draft` and every forward step is a human act. The
 * buttons hide when the stage is not yours — but that is convenience. The SERVER refuses
 * a transition your token's role does not own, so hiding is not what makes it safe.
 */
import { useCallback, useEffect, useState } from "react";
import { api, type Adjustment, type Board, type QueueItem } from "../api";
import { Badge, Banner, Card, DataTable, ErrorNote, Spinner, fmt } from "../components/ui";

const LANE_STYLE: Record<string, { dot: string; head: string; ring: string }> = {
  draft: { dot: "bg-stage-draft", head: "text-slate-600", ring: "ring-slate-200" },
  analyst: { dot: "bg-stage-analyst", head: "text-brand-600", ring: "ring-brand-100" },
  rm: { dot: "bg-stage-rm", head: "text-[#5b53a6]", ring: "ring-indigo-100" },
  site: { dot: "bg-stage-site", head: "text-suspect", ring: "ring-amber-100" },
  executed: { dot: "bg-stage-executed", head: "text-signal", ring: "ring-green-100" },
  rejected: { dot: "bg-stage-rejected", head: "text-offtarget", ring: "ring-red-100" },
};

const LANE_HINT: Record<string, string> = {
  draft: "raised by the agent — awaiting analyst review",
  analyst: "analyst signed off — awaiting RM",
  rm: "RM signed off — awaiting site engineer",
  site: "site signed off — ready to execute",
  executed: "written to adjustment_history; the ρ loop reads these",
  rejected: "closed without action",
};

export function ApprovalView({ role }: { role: string }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [history, setHistory] = useState<Adjustment[]>([]);
  const [open, setOpen] = useState<QueueItem | null>(null);
  const [note, setNote] = useState<{ tone: "good" | "bad"; text: string } | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const refresh = useCallback(() => {
    api.board().then(setBoard).catch(setErr);
    api.adjustments().then(setHistory).catch(() => {});
  }, []);

  useEffect(refresh, [refresh]);

  if (err) return <ErrorNote error={err} />;
  if (!board) return <Spinner label="loading the board…" />;

  async function act(item: QueueItem, kind: "advance" | "reject") {
    try {
      const res = kind === "advance"
        ? await api.advance(item.action_id)
        : await api.reject(item.action_id);
      setNote({
        tone: "good",
        text: kind === "advance"
          ? `${item.action_id} → ${res.to}` +
            ("wrote_adjustment_history" in res && res.wrote_adjustment_history
              ? " · written to adjustment_history" : "")
          : `${item.action_id} rejected`,
      });
      setOpen(null);
      refresh();
    } catch (e) {
      setNote({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    }
  }

  const total = Object.values(board.counts).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-title font-semibold text-slate-900">Approval board</h1>
        <p className="mt-1 max-w-3xl text-label leading-relaxed text-slate-500">
          {total} item(s) across the chain. The agent may only write{" "}
          <span className="font-medium text-slate-700">draft</span>; every arrow after it
          is a person. Executing writes{" "}
          <code className="font-mono">vrr_agent.adjustment_history</code>, which is what
          the ρ learning loop reads back.
        </p>
      </header>

      {note && <Banner tone={note.tone === "good" ? "good" : "bad"} title={note.text} />}

      {/* --------------------------------------------------------- swim lanes */}
      <div className="flex gap-3 overflow-x-auto pb-2">
        {board.order.map((stage) => {
          const items = board.lanes[stage] ?? [];
          const style = LANE_STYLE[stage] ?? LANE_STYLE.draft;
          const needed = board.approver_for_stage[stage];
          const mine = needed && role === needed;
          return (
            <section key={stage} className="flex w-64 shrink-0 flex-col">
              <div className="mb-1.5 flex items-baseline gap-2">
                <span className={`h-2 w-2 rounded-full ${style.dot}`} />
                <h2 className={`text-label font-semibold uppercase tracking-wide ${style.head}`}>
                  {stage}
                </h2>
                <span className="text-label tabular-nums text-slate-400">{items.length}</span>
                {mine && (
                  <span className="ml-auto rounded bg-brand-50 px-1.5 py-0.5 text-micro font-medium text-brand-600">
                    yours
                  </span>
                )}
              </div>
              <p className="mb-2 h-8 text-micro leading-snug text-slate-400">
                {LANE_HINT[stage]}
              </p>

              <div className="max-h-[calc(100vh-19rem)] flex-1 space-y-2 overflow-y-auto rounded-lg bg-slate-100/70 p-2">
                {items.length === 0 && (
                  <p className="px-1 py-5 text-center text-micro text-slate-400">empty</p>
                )}
                {items.map((d) => (
                  <button
                    key={d.action_id}
                    onClick={() => setOpen(d)}
                    className={`w-full rounded-md bg-white p-2.5 text-left shadow-card ring-1 transition hover:shadow-panel ${style.ring}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-label font-semibold text-slate-800">
                        {d.pattern_name}
                      </span>
                      <Badge tone={d.severity === "high" ? "red" : "slate"}>
                        {d.severity}
                      </Badge>
                    </div>
                    <p className="mt-0.5 text-micro text-slate-500">
                      {fmt.month(String(d.vrr_date))} · {d.action_type.replace(/_/g, " ")}
                    </p>
                    {d.driver && (
                      <p className="mt-1 truncate font-mono text-micro text-slate-400">
                        {d.driver}
                      </p>
                    )}
                  </button>
                ))}
              </div>
            </section>
          );
        })}
      </div>

      {/* ------------------------------------------------------- detail panel */}
      {open && (
        <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30"
             onClick={() => setOpen(null)}>
          <div className="h-full w-[34rem] overflow-y-auto bg-white p-5 shadow-panel"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between">
              <div>
                <h2 className="text-sub font-semibold text-slate-900">
                  {open.pattern_name} · {fmt.month(String(open.vrr_date))}
                </h2>
                <p className="mt-0.5 text-micro text-slate-500">
                  <code className="font-mono">{open.action_id}</code> · stage{" "}
                  <strong>{open.stage}</strong> · raised by {open.stage_by ?? "agent"}
                </p>
              </div>
              <button onClick={() => setOpen(null)}
                      className="rounded px-2 text-slate-400 hover:bg-slate-100">✕</button>
            </div>

            {open.narrative && (
              <pre className="mt-3 whitespace-pre-wrap rounded-md bg-slate-50 p-3 text-micro leading-relaxed text-slate-700">
                {open.narrative}
              </pre>
            )}

            {(() => {
              const rec = typeof open.recommendation === "string"
                ? JSON.parse(open.recommendation) : open.recommendation;
              return rec?.injector_changes?.length ? (
                <div className="mt-3">
                  <p className="mb-1 text-label font-medium text-slate-700">
                    Proposed injector changes
                  </p>
                  <DataTable rows={rec.injector_changes} max={240} />
                </div>
              ) : null;
            })()}

            {board.approver_for_stage[open.stage] === role ? (
              <div className="mt-4 flex gap-2">
                <button
                  onClick={() => act(open, "advance")}
                  className="rounded-md bg-signal px-3 py-1.5 text-body font-medium text-white hover:brightness-110"
                >
                  Approve → {nextOf(board.order, open.stage)}
                </button>
                <button
                  onClick={() => act(open, "reject")}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-body text-slate-700 hover:bg-slate-50"
                >
                  Reject
                </button>
              </div>
            ) : (
              <p className="mt-4 rounded-md bg-slate-50 p-3 text-micro text-slate-500">
                Stage <strong>{open.stage}</strong> advances on{" "}
                <strong>{board.approver_for_stage[open.stage] ?? "—"}</strong> sign-off.
                {role
                  ? <> You are signed in as <strong>{role}</strong>.</>
                  : <> You are not signed in.</>}
              </p>
            )}
          </div>
        </div>
      )}

      <Card title="Executed adjustments"
            sub="The ρ-learning input: predicted vs actual post-VRR per executed change.">
        <DataTable rows={history as unknown as Record<string, unknown>[]} />
      </Card>
    </div>
  );
}

function nextOf(order: string[], stage: string): string {
  const i = order.indexOf(stage);
  return i >= 0 && i + 1 < order.length ? order[i + 1] : "—";
}
