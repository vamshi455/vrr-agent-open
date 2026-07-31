/**
 * The only place this app talks to the backend.
 *
 * Types mirror what `agent/tools.py` returns. They are hand-written rather than
 * generated because the tool payloads carry provenance keys (`sources`, `run_id`,
 * `pvt_methods`, `formulas`) that matter to the UI, and a generator would flatten them
 * into `any` — the fields most worth typing are exactly the ones proving where a number
 * came from.
 *
 * Nothing here computes. If a value needs deriving, it is derived in `core/` behind a
 * tool, so the browser and the LLM cannot disagree about a figure.
 */

const BASE = "/api";

/**
 * The bearer token, held in localStorage so a refresh does not sign you out.
 *
 * Honest limitation: localStorage is readable by any script on the page, so an XSS bug
 * leaks the token. The alternative — an httpOnly cookie — is immune to that but needs
 * CSRF protection, and for a workbench you run on your own machine the trade is fine.
 * It would NOT be fine on a shared deployment; that is the point to switch.
 */
const TOKEN_KEY = "vrr.token";

export const session = {
  get token(): string | null {
    return localStorage.getItem(TOKEN_KEY);
  },
  save(token: string) {
    localStorage.setItem(TOKEN_KEY, token);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
  },
};

/** Fires when the server rejects our token, so the shell can show the login form. */
export const onUnauthorized: { handler: (() => void) | null } = { handler: null };

function authHeaders(): Record<string, string> {
  const t = session.token;
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function get<T>(path: string, params?: Record<string, string | undefined>): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined) qs.set(k, v);
  const url = `${BASE}${path}${qs.toString() ? `?${qs}` : ""}`;
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw await fail(res, url);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw await fail(res, path);
  return res.json() as Promise<T>;
}

/** A 401 means the token is gone, expired or forged — drop it and ask for a new one. */
async function fail(res: Response, path: string): Promise<ApiError> {
  const err = new ApiError(res.status, await detail(res), path);
  if (res.status === 401) {
    session.clear();
    onUnauthorized.handler?.();
  }
  return err;
}

async function detail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    return res.statusText;
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public path: string) {
    super(message);
  }
}

// ---------------------------------------------------------------- types ----
export interface Pattern {
  pattern_id: string;
  pattern_name: string;
  asset?: string | null;
  vrr?: number | null;
  vrr_date?: string | null;
}

export interface TrendRow {
  vrr_date: string;
  vrr: number;
  inj_res_bbl: number;
  prod_res_bbl: number;
  any_extrapolated: boolean;
  n_completions: number;
}

export interface PatternContext {
  pattern_id: string;
  pattern_name: string;
  asset?: string | null;
  target_vrr: number;
  memory?: { typical_low?: number | null; typical_high?: number | null;
             response_factor?: number | null } | null;
}

export interface OverviewRow {
  id_pattern: string;
  pattern_name: string;
  asset?: string | null;
  vrr: number;
  target_vrr: number;
  drift: number;
  verdict?: string | null;
  n_completions: number;
  any_extrapolated: boolean;
  response_factor?: number | null;
  vrr_date: string;
}

export interface Overview {
  n_patterns: number;
  off_target: unknown[];
  patterns: OverviewRow[];
}

export interface Driver {
  term: string;
  label: string;
  contribution: number;
  share: number;
  d_res_bbl?: number;
}

export interface Decompose {
  ok: boolean;
  reason?: string;
  vrr_a: number;
  vrr_b: number;
  d_vrr: number;
  drivers: Driver[];
  side_contributions: { injection: number; production: number };
}

export interface AuditResult {
  ok: boolean;
  stored: { vrr: number; run_id?: string | null };
  recomputed: { vrr: number };
  difference: number;
  matches: boolean;
  n_raw_rows: number;
  pvt_methods: string[];
  low_confidence_inputs: boolean;
  provenance: { recomputed_from: string[]; code: string };
}

export interface Lineage {
  sources: Record<string, string>;
  /** FACTOR summarised across the period's completions — it is the first multiplicand
   *  in every formula, so the graph shows it as numbers, not just as a table name. */
  allocation?: { n: number; min: number | null; max: number | null;
                 weighted_mean: number | null };
  formulas: Record<string, string>;
  completions: Record<string, unknown>[];
  term_totals: Record<string, number>;
  recomputed_from_terms: { prod_res_bbl: number; inj_res_bbl: number; vrr: number };
}

/** The pattern schematic. Every x/y is computed by `core.pattern_layout` — this file
 *  carries the shape, it does not decide it. `is_schematic` is always true today and the
 *  view must say so: the database holds contribution factors, never coordinates. */
export interface LayoutNode {
  completion_id: string;
  completion_name: string;
  role: "injector" | "producer" | "idle";
  x: number; y: number;
  factor: number; share: number; size: number; res_bbl: number;
  shared: boolean; n_patterns: number;
  low_confidence: boolean; pvt_methods: string;
}

export interface PatternLayout {
  found: boolean;
  pattern_id?: string; pattern_name?: string; vrr_date?: string; vrr?: number;
  prod_res_bbl?: number; inj_res_bbl?: number;
  geometry?: string; geometry_label?: string; caption?: string; is_schematic?: boolean;
  n_injectors?: number; n_producers?: number; n_idle?: number;
  nodes?: LayoutNode[];
  links?: { to: string; factor: number; share_of_production: number }[];
  hub?: { x: number; y: number; radius: number };
  shared?: string[]; low_confidence?: string[];
}

export interface AnalysisCase {
  ok: boolean;
  reason?: string;
  narrative: string;
  draft?: { action_type: string; severity?: string } | null;
  audit?: { audit?: { verdict?: string; summary?: string } };
}

export interface QueueItem {
  action_id: string;
  id_pattern: string;
  pattern_name: string;
  vrr_date: string;
  stage: string;
  severity: string;
  confidence?: string;
  action_type: string;
  driver?: string | null;
  narrative?: string | null;
  stage_by?: string | null;
  run_id?: string | null;
  recommendation?: { injector_changes?: Record<string, unknown>[] } | string | null;
}

export interface Adjustment {
  action_id: string;
  pattern_name: string;
  vrr_date: string;
  change_type: string;
  d_surface_pct?: number | null;
  pre_vrr?: number | null;
  predicted_post_vrr?: number | null;
  actual_post_vrr?: number | null;
  approved_by?: string | null;
  ts: string;
}

/** The provenance block every answer carries — what the caption in the drawer renders. */
export interface ChatMeta {
  llm?: boolean;
  model?: string | null;
  gate?: string | null;
  retrieved?: number;
  grounded?: boolean;
  tools_called?: string[] | null;
  violations?: { kind: string; term?: string; detail: string }[] | null;
  first_attempt_violations?: { kind: string; term?: string; detail: string }[] | null;
  uncited_numbers?: number[] | null;
}

export interface ChatAnswer {
  intent: string;
  text: string;
  meta: ChatMeta;
  data?: unknown;
  persisted?: boolean;
  /** Tracing is meant to be on always; these say whether this turn actually landed
   *  in MLflow, and link straight to its span tree. */
  trace_id?: string | null;
  trace_url?: string | null;
  traced?: boolean;
}

export interface HistoryTurn {
  chat_id: string;
  question: string;
  answer: string;
  intent: string;
  asked_by?: string | null;
  created_at: string;
  meta?: ChatMeta | null;
  payload?: unknown;
}

export interface Health {
  auth: { required_for: string[]; scheme: string; token_ttl_minutes: number;
          ephemeral_secret: boolean };
  llm: { available: boolean; model: string | null; provider: string };
  tracing: { enabled: boolean; uri: string };
  postgres: { host: string; monthly_rows: number };
  knowledge: { docs: number; chunks: number };
  retrieval_min_score: number;
}

/** Every lane at once — the swim-lane board reads this in one call. */
export interface Board {
  lanes: Record<string, QueueItem[]>;
  counts: Record<string, number>;
  approver_for_stage: Record<string, string>;
  order: string[];
}

export interface Stages {
  stages: string[];
  roles: string[];
  approver_for_stage: Record<string, string>;
}

// ------------------------------------------------------------- endpoints ----
export interface Identity {
  username: string;
  role: string;
  full_name?: string | null;
}

export const api = {
  /** OAuth2 password grant. Form-encoded, not JSON — that is what the spec (and
   *  FastAPI's OAuth2PasswordRequestForm, and the /docs Authorize button) expects. */
  async login(username: string, password: string): Promise<Identity> {
    const res = await fetch(`${BASE}/auth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username, password }),
    });
    if (!res.ok) throw new ApiError(res.status, await detail(res), "/auth/token");
    const body = await res.json();
    session.save(body.access_token);
    return { username: body.username, role: body.role, full_name: body.full_name };
  },
  me: () => get<{ username: string; role: string; expires_at: number }>("/auth/me"),
  logout: () => session.clear(),

  health: () => get<Health>("/health"),
  stages: () => get<Stages>("/stages"),

  patterns: () => get<Pattern[]>("/patterns"),
  overview: (asset?: string) => get<Overview>("/overview", { asset }),
  dataQuality: () => get<{ ok: boolean; n_findings?: number; checks_run: string[];
                           findings?: Record<string, Record<string, unknown>[]> }>("/data-quality"),
  inputAudit: (pattern?: string) =>
    get<{ n?: number; by_verdict?: Record<string, number>;
          audits?: { vrr_date: string; verdict: string; summary: string }[] }>(
      "/input-audit", { pattern }),

  context: (id: string) => get<PatternContext>(`/patterns/${id}/context`),
  trend: (id: string) => get<{ rows: TrendRow[] }>(`/patterns/${id}/trend`),
  decompose: (id: string, from: string, to: string) =>
    get<Decompose>(`/patterns/${id}/decompose`, { from, to }),
  audit: (id: string, date: string) => get<AuditResult>(`/patterns/${id}/audit`, { date }),
  lineage: (id: string, date: string) => get<Lineage>(`/patterns/${id}/lineage`, { date }),
  analysis: (id: string, date: string) => get<AnalysisCase>(`/patterns/${id}/analysis`, { date }),
  layout: (id: string, date: string) => get<PatternLayout>(`/patterns/${id}/layout`, { date }),
  // No submitted_by / role / user in any write: the server takes the actor from the
  // token. A client-supplied identity on an audit trail is a signature anyone can forge.
  submit: (id: string, date: string) =>
    post<{ action_id: string; next_approver: string }>(`/patterns/${id}/submit`, { date }),

  queue: (stage: string) => get<QueueItem[]>("/queue", { stage }),
  board: () => get<Board>("/board"),
  adjustments: () => get<Adjustment[]>("/adjustments"),
  advance: (actionId: string) =>
    post<{ from: string; to: string; by: string; wrote_adjustment_history: boolean }>(
      `/queue/${actionId}/advance`, undefined),
  reject: (actionId: string) =>
    post<{ to: string }>(`/queue/${actionId}/reject`, undefined),

  chat: (body: { question: string; pattern?: string; date?: string; agentic?: boolean }) =>
    post<ChatAnswer>("/chat", body),
  history: (pattern: string, user?: string) =>
    get<HistoryTurn[]>("/chat/history", { pattern, user }),
  /** Hides the transcript for THIS user. Deletes nothing — the rows and the traces
   *  behind them are the audit record. */
  clearChat: (pattern: string) =>
    post<{ cleared_for: string; note: string }>(`/chat/clear?pattern=${pattern}`, undefined),
};
