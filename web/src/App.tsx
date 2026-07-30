/**
 * The workbench shell: sidebar filters, four views, and the docked chat drawer.
 *
 * Layout mirrors the analyst's workflow rather than the data model — Portfolio ("where
 * do I look?") → Report ("what happened here?") → Lineage ("do I believe the number?")
 * → Approval ("who signs it off?"), with the chat pinned beside all four so you can
 * interrogate a chart without leaving it.
 *
 * Sidebar selections (pattern · period · acting-as role) are held here and passed down,
 * because every view and the chat all read the same context — the alternative, each
 * view owning its own pattern picker, is how a chart and an answer end up describing
 * different months.
 */
import { useEffect, useMemo, useState } from "react";
import { api, onUnauthorized, session, type Health, type Identity, type Pattern,
         type TrendRow } from "./api";
import { ChatDrawer } from "./components/ChatDrawer";
import { Login } from "./components/Login";
import { Banner, Spinner } from "./components/ui";
import { ApprovalView } from "./views/ApprovalView";
import { LineageView } from "./views/LineageView";
import { PortfolioView } from "./views/PortfolioView";
import { ReportView } from "./views/ReportView";

const VIEWS = [
  { id: "portfolio", label: "🗺️ Portfolio" },
  { id: "report", label: "📈 Report" },
  { id: "lineage", label: "🔎 Lineage & audit" },
  { id: "approval", label: "✅ Approval queue" },
] as const;
type ViewId = (typeof VIEWS)[number]["id"];

export default function App() {
  const [view, setView] = useState<ViewId>("portfolio");
  const [patterns, setPatterns] = useState<Pattern[] | null>(null);
  const [patternId, setPatternId] = useState<string>("");
  const [trend, setTrend] = useState<TrendRow[]>([]);
  const [period, setPeriod] = useState<string>("");
  // Identity, not preference. `me` is null until you sign in; the role inside it is a
  // signed claim from the server, which is why there is no longer a role dropdown.
  const [me, setMe] = useState<Identity | null>(null);
  const [loginOpen, setLoginOpen] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.patterns()
      .then((p) => {
        setPatterns(p);
        if (p.length) setPatternId(p[0].pattern_id);
      })
      .catch((e) => setError(String(e.message ?? e)));
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Period defaults to the latest month whenever the pattern changes — an analyst opens
  // a pattern to see where it is NOW, not where it was three years ago.
  useEffect(() => {
    if (!patternId) return;
    api.trend(patternId).then(({ rows }) => {
      setTrend(rows);
      setPeriod(rows.length ? rows[rows.length - 1].vrr_date : "");
    });
  }, [patternId]);

  // Resume a session across refreshes, and drop it the moment the server says the token
  // is no longer good (expired, or signed with a key this process does not have).
  useEffect(() => {
    if (session.token) {
      api.me().then((who) => setMe({ username: who.username, role: who.role }))
              .catch(() => session.clear());
    }
    onUnauthorized.handler = () => { setMe(null); setLoginOpen(true); };
    return () => { onUnauthorized.handler = null; };
  }, []);

  // The chat's quick question needs to say "high" or "low" (the intent router keys on
  // those words), so the shell computes it once from the selected period.
  const selected = trend.find((r) => r.vrr_date === period);
  const vsTarget: "high" | "low" = (selected?.vrr ?? 1) >= 1 ? "high" : "low";

  const sorted = useMemo(
    () => [...(patterns ?? [])].sort((a, b) =>
      (a.pattern_name ?? "").localeCompare(b.pattern_name ?? "")), [patterns]);

  if (error) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <Banner tone="bad" title="Cannot reach the API">
          {error}. Start it with <code className="font-mono">make api</code>, and if the
          database is empty run <code className="font-mono">make seed</code>.
        </Banner>
      </div>
    );
  }
  if (!patterns) return <Spinner label="loading patterns…" />;
  if (!patterns.length) {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <Banner tone="warn" title="No VRR data yet">
          Run <code className="font-mono">make seed</code> to generate the synthetic field.
        </Banner>
      </div>
    );
  }

  const shared = { patternId, period, trend };

  return (
    // h-screen + overflow-hidden pins the shell to the viewport, so each of the three
    // columns scrolls INSIDE itself. With min-h-screen the page grew as the transcript
    // grew, which pushed the chat input off the bottom — the box you type in must not
    // move because the conversation got longer.
    <div className="flex h-screen overflow-hidden bg-slate-50 text-slate-900">
      {/* ---------------------------------------------------------- sidebar */}
      <aside className="w-64 shrink-0 overflow-y-auto border-r border-slate-200 bg-white p-4">
        <h1 className="text-base font-semibold">🛢️ VRR — Open</h1>
        <p className="mt-0.5 text-xs text-slate-500">Reasoning &amp; lineage workbench</p>

        <label className="mt-5 block text-xs font-medium text-slate-600">Pattern</label>
        <select
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          value={patternId}
          onChange={(e) => setPatternId(e.target.value)}
        >
          {sorted.map((p) => (
            <option key={p.pattern_id} value={p.pattern_id}>
              {p.pattern_name} ({p.pattern_id.slice(0, 8)})
            </option>
          ))}
        </select>

        <label className="mt-4 block text-xs font-medium text-slate-600">Period under review</label>
        <select
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
        >
          {[...trend].reverse().map((r) => (
            <option key={r.vrr_date} value={r.vrr_date}>
              {new Date(r.vrr_date + "T00:00:00").toLocaleDateString(undefined,
                { month: "short", year: "numeric" })}
            </option>
          ))}
        </select>

        <div className="mt-4 rounded border border-slate-200 bg-slate-50 p-2">
          {me ? (
            <>
              <p className="text-xs font-medium text-slate-700">{me.username}</p>
              <p className="mt-0.5 text-[11px] text-slate-500">
                role <strong>{me.role}</strong> — from your token, not a setting
              </p>
              <button
                onClick={() => { api.logout(); setMe(null); }}
                className="mt-1.5 text-[11px] text-slate-500 underline"
              >
                sign out
              </button>
            </>
          ) : (
            <>
              <p className="text-xs text-slate-600">Not signed in</p>
              <p className="mt-0.5 text-[11px] text-slate-500">
                Reading is open; asking the agent or approving needs an account.
              </p>
              <button
                onClick={() => setLoginOpen(true)}
                className="mt-1.5 rounded bg-slate-900 px-2 py-1 text-[11px] text-white"
              >
                Sign in
              </button>
            </>
          )}
        </div>

        <nav className="mt-6 space-y-1">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => setView(v.id)}
              className={`block w-full rounded px-2 py-1.5 text-left text-sm ${
                view === v.id ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
              }`}
            >
              {v.label}
            </button>
          ))}
        </nav>

        <div className="mt-6 space-y-2 border-t border-slate-100 pt-4 text-[11px] leading-relaxed text-slate-500">
          <p>
            LLM narrator:{" "}
            {health?.llm.available
              ? <span className="text-signal">🟢 {health.llm.model}</span>
              : <span>⚪ not running</span>}
            <br />
            {health?.llm.available
              ? "Phrasing is LLM-generated and gated; numbers stay tool-computed."
              : "Answers are fully computed — no LLM needed."}
          </p>
          <p>Postgres: <code className="font-mono">{health?.postgres.host ?? "—"}</code></p>
          <p>
            {health?.tracing.enabled
              ? <a className="underline" href={health.tracing.uri} target="_blank" rel="noreferrer">
                  MLflow traces
                </a>
              : "⚪ MLflow tracing off"}
            {" "}— every question is a span tree
          </p>
          <p>
            Knowledge index: {health?.knowledge.docs ?? 0} doc(s),{" "}
            {health?.knowledge.chunks ?? 0} chunks
            {health && !health.knowledge.chunks && " — run make knowledge"}
          </p>
        </div>
      </aside>

      {/* ------------------------------------------------------------- main */}
      <main className="min-w-0 flex-1 overflow-y-auto p-6">
        {view === "portfolio" && <PortfolioView onPick={setPatternId} />}
        {view === "report" && <ReportView {...shared} />}
        {view === "lineage" && <LineageView {...shared} />}
        {view === "approval" && <ApprovalView role={me?.role ?? ""} />}
      </main>

      {/* ------------------------------------------------------------ chat */}
      <ChatDrawer
        patternId={patternId}
        patternName={sorted.find((p) => p.pattern_id === patternId)?.pattern_name ?? ""}
        period={period}
        user={me?.username ?? ""}
        signedIn={!!me}
        onNeedSignIn={() => setLoginOpen(true)}
        vsTarget={vsTarget}
        llmUp={!!health?.llm.available}
        open={drawerOpen}
        onToggle={() => setDrawerOpen((o) => !o)}
      />

      {loginOpen && (
        <Login
          onSignedIn={(who) => { setMe(who); setLoginOpen(false); }}
          onCancel={() => setLoginOpen(false)}
        />
      )}
    </div>
  );
}
