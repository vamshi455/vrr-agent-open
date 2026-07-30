/**
 * The analyst chat, docked to the right of every view.
 *
 * Docked rather than a separate tab so you can interrogate a chart without leaving it.
 * The transcript lives in vrr_agent.chat_history — it survives a refresh and is SHARED,
 * so opening a pattern shows what anyone already asked about it.
 *
 * Every answer carries a provenance caption (who phrased it, whether the gate cleared
 * it) with the evidence one click away. That caption is the trust argument this whole
 * agent makes, so it stays visible; the raw payload does not.
 */
import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ChatMeta, type HistoryTurn } from "../api";
import { Badge, provenanceLine, violationLine } from "./ui";

interface Props {
  patternId: string; patternName: string; period: string; user: string;
  /** "high" | "low" vs target — the quick question is worded from it, and the intent
      router keys on those words. Asking "why is X off target" routes to PORTFOLIO. */
  vsTarget: "high" | "low";
  llmUp: boolean; open: boolean; onToggle: () => void;
}

interface Turn {
  question: string; answer: string; intent: string; meta: ChatMeta;
  askedBy?: string | null; at?: string; payload?: unknown; pending?: boolean;
}

export function ChatDrawer({ patternId, patternName, period, user, vsTarget, llmUp,
                             open, onToggle }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [agentic, setAgentic] = useState(false);
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!patternId) return;
    api.history(patternId)
      .then((rows: HistoryTurn[]) =>
        setTurns(rows.map((r) => ({
          question: r.question, answer: r.answer, intent: r.intent,
          meta: r.meta ?? {}, askedBy: r.asked_by, at: r.created_at, payload: r.payload,
        }))))
      .catch(() => setTurns([]));
  }, [patternId]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns.length, busy]);

  async function ask(question: string) {
    if (!question.trim() || busy) return;
    setInput("");
    setBusy(true);
    setTurns((t) => [...t, { question, answer: "", intent: "", meta: {}, pending: true }]);
    try {
      const res = await api.chat({
        question, pattern: patternId, date: period, agentic, asked_by: user,
      });
      setTurns((t) => [...t.slice(0, -1), {
        question, answer: res.text, intent: res.intent, meta: res.meta ?? {},
        askedBy: user, at: new Date().toISOString(), payload: res.data,
      }]);
    } catch (e) {
      setTurns((t) => [...t.slice(0, -1), {
        question, answer: `Request failed: ${e instanceof Error ? e.message : String(e)}`,
        intent: "error", meta: {}, askedBy: user,
      }]);
    } finally {
      setBusy(false);
    }
  }

  // Wording matters: the intent router keys on "high"/"low" for the explain path — "off
  // target" is a PORTFOLIO phrase and routes the question to a portfolio scan instead.
  const quick = [
    ["Why this VRR?", `Why is ${patternName}'s VRR ${vsTarget} in ${monthName(period)}?`],
    ["Is it correct?", `Is the ${monthName(period)} number actually correct?`],
    ["How computed?", `How is ${patternName}'s VRR calculated?`],
    ["Documents", "What do the documents say about changing injection rates?"],
  ];

  if (!open) {
    return (
      <button
        onClick={onToggle}
        title="Ask the agent about whatever you are looking at"
        className="h-full w-12 shrink-0 border-l border-slate-200 bg-white text-xl hover:bg-slate-50"
      >
        💬
      </button>
    );
  }

  return (
    <aside className="flex w-[26rem] shrink-0 flex-col border-l border-slate-200 bg-white">
      <header className="flex items-start justify-between border-b border-slate-100 p-3">
        <div>
          <h2 className="text-sm font-semibold">💬 Ask about {patternName}</h2>
          <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">
            {monthName(period)} · numbers come from deterministic tools over Postgres; the
            LLM only rephrases, behind the faithfulness gate.
          </p>
        </div>
        <button onClick={onToggle} className="rounded px-2 text-slate-400 hover:bg-slate-100"
                aria-label="Close chat">✕</button>
      </header>

      <div className="border-b border-slate-100 p-3">
        <label className="flex items-center gap-2 text-xs text-slate-600">
          <input type="checkbox" checked={agentic} disabled={!llmUp}
                 onChange={(e) => setAgentic(e.target.checked)} />
          Model queries the tables itself
          <span className="text-slate-400">{agentic ? "(~1–2 min)" : "(~8 s)"}</span>
        </label>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {quick.map(([label, prompt]) => (
            <button
              key={label}
              onClick={() => ask(prompt)}
              disabled={busy}
              className="rounded border border-slate-200 px-2 py-1 text-[11px] hover:bg-slate-50 disabled:opacity-40"
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        {turns.length === 0 && (
          <p className="text-xs text-slate-400">Nothing asked about this pattern yet.</p>
        )}
        {turns.map((t, i) => <TurnBlock key={i} turn={t} />)}
        <div ref={endRef} />
      </div>

      <form
        className="border-t border-slate-100 p-3"
        onSubmit={(e) => { e.preventDefault(); ask(input); }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Ask about ${patternName}…`}
          disabled={busy}
          className="w-full rounded border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50"
        />
      </form>
    </aside>
  );
}

function TurnBlock({ turn }: { turn: Turn }) {
  const [showEvidence, setShowEvidence] = useState(false);
  // A rejected answer stores `violations`; a REPAIRED one stores
  // `first_attempt_violations` — both are the gate's audit trail and both are shown.
  const caught = turn.meta.violations ?? turn.meta.first_attempt_violations ?? [];
  const rejected = !!turn.meta.violations;

  return (
    <div className="space-y-1.5">
      <div className="rounded-lg bg-slate-100 px-3 py-2 text-sm">{turn.question}</div>
      {turn.askedBy && (
        <p className="px-1 text-[10px] text-slate-400">
          {turn.askedBy}{turn.at && ` · ${new Date(turn.at).toLocaleString(undefined,
            { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}`}
        </p>
      )}

      {turn.pending ? (
        <p className="px-1 text-xs text-slate-400">thinking…</p>
      ) : (
        <>
          <div className="prose-agent rounded-lg border border-slate-200 px-3 py-2 text-sm leading-relaxed">
            {/* Answers are markdown — bold figures, source footers, and the portfolio
                scan returns a table. Rendering them as plain text showed raw ** and |. */}
            <Markdown remarkPlugins={[remarkGfm]}>{turn.answer}</Markdown>
          </div>
          <p className="px-1 text-[10px] text-slate-500">
            {provenanceLine(turn.intent, turn.meta)}
          </p>
          {(caught.length > 0 || turn.payload) && (
            <div className="px-1">
              <button
                onClick={() => setShowEvidence((s) => !s)}
                className="text-[10px] text-slate-500 underline"
              >
                {showEvidence ? "▾" : "▸"} Evidence &amp; provenance
              </button>
              {showEvidence && (
                <div className="mt-1 space-y-2">
                  {caught.length > 0 && (
                    <div className="rounded border border-amber-200 bg-amber-50 p-2">
                      <p className="text-[11px] font-medium text-amber-900">
                        {rejected
                          ? "Gate rejected the model's phrasing — computed wording shown instead:"
                          : "Gate caught this on the first attempt and had the model rewrite it:"}
                      </p>
                      <ul className="mt-1 list-disc pl-4 text-[11px] text-amber-900">
                        {caught.map((v, i) => <li key={i}>{violationLine(v)}</li>)}
                      </ul>
                      {turn.meta.uncited_numbers?.length ? (
                        <p className="mt-1 text-[11px] text-amber-900">
                          Numbers with no tool output behind them:{" "}
                          {turn.meta.uncited_numbers.join(", ")}
                        </p>
                      ) : null}
                    </div>
                  )}
                  {turn.meta.retrieved !== undefined && (
                    <p className="text-[10px] text-slate-500">
                      <Badge>{turn.meta.retrieved} chunk(s) retrieved</Badge>
                    </p>
                  )}
                  {turn.payload ? (
                    <pre className="max-h-56 overflow-auto rounded bg-slate-50 p-2 text-[10px] text-slate-600">
                      {JSON.stringify(turn.payload, null, 2)}
                    </pre>
                  ) : null}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function monthName(iso: string): string {
  if (!iso) return "";
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined,
    { month: "long", year: "numeric" });
}
