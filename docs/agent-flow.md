# Analyst workflow — from a suspicious number to an approved valve change

How the four app tabs, the agent, and the deterministic core fit together. Companion
to [design.md](design.md) (why) and [running.md](running.md) (commands).

## The loop

```
      ┌──────────── Streamlit workbench (make app) ─────────────┐
      │ 📈 Report      trend + band + ΔVRR attribution          │
      │ 🔎 Lineage     raw → PVT → contrib → monthly + RECOMPUTE │
      │ 💬 Chat        ask the agent (tools compute, LLM phrases)│
      │ ✅ Approval    draft → analyst → RM → site → executed    │
      └────────────────────────┬────────────────────────────────┘
                               │ every number
      ┌────────────────────────▼────────────────────────────────┐
      │ agent/tools.py — deterministic tools over PostgreSQL     │
      │ core/physics · decompose · anomaly · recommend · approval│
      └──────────────────────────────────────────────────────────┘
```

The LLM sits *outside* that box. It may pick tools and rephrase the computed answer;
it may never produce a figure. `core/faithfulness.py` enforces that before an answer
reaches the analyst.

## What the agent does when you ask "why is VRR high?"

`agent/analyst.py::analyze` runs five steps in this order — the order is the point:

| # | Step | Code | Question it answers |
|---|------|------|---------------------|
| 1 | **Verify** | `VRR_AUDIT` → `core.physics` | Is the number even right? Recomputes the month from raw and diffs it against the stored value; reports the PVT method. |
| 2 | **Attribute** | `VRR_DECOMPOSE` → `core.decompose` | Which term moved it? Exact log-mean (LMDI) split; Σ contributions = ΔVRR. |
| 3 | **Classify** | `DETECT_ANOMALIES` → `core.anomaly` | Out of band? Sustained drift? Suspect inputs? |
| 4 | **Propose** | `RECOMMEND_CHANGE` → `core.recommend` | How much valve change, ρ-calibrated and clamped by `vrr_agent.safety_limits`. |
| 5 | **Draft** | `core.anomaly.build_draft` | A queue row citing anomaly + recommendation + precedent. |

Step 1 gates step 4: if the PVT lookups were extrapolated, the draft becomes
*investigate inputs* and no valve change is proposed (design §6 — never act on suspect
inputs).

## Connecting the local LLM

```bash
brew install ollama                # or https://ollama.com/download
ollama serve &                     # http://localhost:11434
ollama pull qwen2.5:7b             # narrator + tool-caller (reliable tool schema)
ollama pull nomic-embed-text       # 768-dim embeddings for knowledge search
```

The app auto-detects it (sidebar flips to 🟢 with the model name). `VRR_LLM_MODEL`
overrides the choice; `agent.llm.pick_model()` falls back to whatever chat model is
actually pulled, so any local model works. `VRR_LLM_BASE_URL` points at any other
OpenAI-compatible endpoint.

Two modes, toggled in the chat tab:

| Mode | Who calls the tools | Speed (local 7B) | Use when |
|---|---|---|---|
| **default** | the deterministic pipeline (`analyst.analyze`) runs verify → attribute → classify → propose; the model only rewrites the result | ~8 s | normal analysis |
| **agentic** | the model itself picks tools/tables in a loop (`graph.run`) | ~1–2 min | open-ended questions that cross tools |

Both are gated identically. In practice the agentic loop on a 7B model gets caught
fabricating figures more often (it likes to compute daily averages) — when that happens
the computed answer is shown with the violation displayed, which is the designed
outcome, not a failure.

## General VRR questions

Conceptual questions ("what is VRR", "what happens if you over-inject") are routed to a
`general` intent: the model answers from its own knowledge, grounded in the project's
VRR primer (`graph.DOMAIN`) plus any ingested documents (pgvector), and the answer is
labelled *general knowledge — not computed from your Postgres tables*. Nothing about
this field's numbers may come from that path.

## The chat is answerable without an LLM

`agent/chat.py` routes a question to an intent (`explain` · `audit` · `lineage` ·
`recommend` · `submit` · `knowledge` · `list`), resolves the pattern/date from the text
or the sidebar selection, and builds the answer from tool output. Then:

* **No local LLM running** → the computed narrative is shown as-is. Nothing is lost but
  the prose.
* **Ollama running** → the LLM rewrites that narrative, and the result must pass
  `check_faithfulness` (no unsupported driver, no wrong direction) *and* `check_numbers`
  (every decimal in the text must match a tool-produced figure). On failure the computed
  text is shown instead, with the violation displayed.

## Lineage — what is actually stored

`vrr_curated.completion_contrib` is the lineage layer: one row per
(pattern, completion, **day**) carrying the root inputs (`factor, oil, water, gas,
water_inj, gas_inj`), the pattern pressure used, the **PVT method label**
(`exact | interpolated | extrapolated | closest | none`), all five derived reservoir
terms, and the `run_id` of the build that produced it. So for any VRR on screen you can
walk down to the exact daily rows and the exact FVF lookup behind it — that is what the
🔎 Lineage tab and `VRR_LINEAGE` render, and what `VRR_AUDIT` independently recomputes.

Unity Catalog OSS (`make register`) records the same chain at *table* level (raw →
curated → agent) for governance; the row-level derivation lives in the contrib table.

## Approval

`core/approval.py` owns the state machine: `draft → analyst → rm → site → executed`,
plus `rejected` from any live stage. The agent may only write `draft` — every forward
transition is a human act in the app, role-gated in the sidebar. Executing writes
`vrr_agent.adjustment_history` (predicted vs actual ΔVRR), which is what the learned
response factor ρ (`core.recommend.update_response_factor`) reads back.

```bash
make queue    # anomaly → drafts for every pattern's latest period
make app      # review, chat, approve
```
