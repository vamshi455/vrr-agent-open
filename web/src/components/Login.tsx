/**
 * Sign-in. Shown when a write needs a token and there isn't one.
 *
 * It is NOT a wall in front of the whole app: reads stay public, so you can look at the
 * portfolio, the attribution and the lineage without an account. You sign in to *act* —
 * ask the agent, draft a change, move it along the approval chain.
 *
 * The role you get is the role your account has. That is the whole point of this screen:
 * the previous build let you pick "site" from a dropdown and execute a valve change.
 */
import { useState } from "react";
import { api, type Identity } from "../api";
import { Banner } from "./ui";

const DEMO = [
  ["analyst.demo", "drafts, first sign-off"],
  ["rm.demo", "second sign-off"],
  ["site.demo", "the only role that may execute"],
];

export function Login({ onSignedIn, onCancel, reason }: {
  onSignedIn: (who: Identity) => void;
  onCancel?: () => void;
  reason?: string;
}) {
  const [username, setUsername] = useState("analyst.demo");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await api.login(username, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <form onSubmit={submit}
            className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-6 shadow-lg">
        <h2 className="text-base font-semibold">🛢️ Sign in</h2>
        <p className="mt-1 text-xs leading-relaxed text-slate-500">
          {reason ?? "Reading is open. Asking the agent and moving an approval need an account — your role comes from it."}
        </p>

        {error && <div className="mt-3"><Banner tone="bad" title={error} /></div>}

        <label className="mt-4 block text-xs font-medium text-slate-600">Username</label>
        <input
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
          autoComplete="username"
        />

        <label className="mt-3 block text-xs font-medium text-slate-600">Password</label>
        <input
          type="password"
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />

        <div className="mt-4 flex gap-2">
          <button
            type="submit"
            disabled={busy || !password}
            className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
          >
            {busy ? "signing in…" : "Sign in"}
          </button>
          {onCancel && (
            <button type="button" onClick={onCancel}
                    className="rounded border border-slate-300 px-3 py-1.5 text-sm">
              Keep reading
            </button>
          )}
        </div>

        <div className="mt-5 border-t border-slate-100 pt-3">
          <p className="text-[11px] font-medium text-slate-600">Demo accounts</p>
          <ul className="mt-1 space-y-0.5 text-[11px] text-slate-500">
            {DEMO.map(([name, what]) => (
              <li key={name}>
                <button type="button" onClick={() => setUsername(name)}
                        className="font-mono underline">{name}</button> — {what}
              </li>
            ))}
          </ul>
          <p className="mt-2 text-[11px] text-slate-400">
            Seed them with <code className="font-mono">make users</code>; the default
            password is <code className="font-mono">vrr-demo</code>.
          </p>
        </div>
      </form>
    </div>
  );
}
