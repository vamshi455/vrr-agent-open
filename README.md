# vrr_agent_open

**Open-source, fully-local VRR Reasoning & Lineage agent — LangGraph + PostgreSQL/pgvector + Unity Catalog OSS + MLflow. No cloud, no cost.**

![Python](https://img.shields.io/badge/python-3.10+-blue)
![LangGraph](https://img.shields.io/badge/agent-LangGraph%20StateGraph-1C3C3C)
![PostgreSQL](https://img.shields.io/badge/data-PostgreSQL%20%2B%20pgvector-336791)
![Unity Catalog](https://img.shields.io/badge/governance-Unity%20Catalog%20OSS-red)
![FastAPI](https://img.shields.io/badge/api-FastAPI-009688)
![React](https://img.shields.io/badge/ui-React%20%2B%20Vite%20%2B%20TS-61DAFB)
![MLflow](https://img.shields.io/badge/tracing%20%2B%20eval-MLflow%20OSS-0194E2)
![Tests](https://img.shields.io/badge/tests-154%20passing-brightgreen)
![License](https://img.shields.io/badge/license-Apache%202.0-green)

An open-source port of a Databricks VRR agent. Same trust model — **the LLM never
computes**; every number comes from a deterministic tool with provenance, a
faithfulness gate rejects narration the math doesn't support, recommendations are
physics-computed and safety-clamped, humans approve, and executed outcomes feed a
learned per-pattern response factor ρ — rebuilt on a free local stack.

> **Why this exists:** it shows the *architecture* (deterministic core + agentic
> reasoning + governance + closed-loop learning) is portable off any single vendor.
> The deterministic heart (`core/`) is copied **verbatim** from the Databricks
> version — provider-agnostic Python with its unit tests.

---

## Contents

| | |
|---|---|
| [1. The one idea](#1-the-one-idea-the-llm-never-computes) | where the model is allowed to act |
| [2. System map](#2-system-map) | every component and how they connect |
| [3. Data model](#3-data-model--three-schemas-raw--curated--agent) | three schemas, raw → curated → agent |
| [4. The physics](#4-the-physics--how-a-vrr-number-is-built) | PVT ladder → reservoir volumes → VRR |
| [5. The agent](#5-the-agent--a-real-langgraph-stategraph) | LangGraph StateGraph, node by node |
| [6. Analyst pipeline](#6-the-analyst-pipeline--five-steps-in-this-order) | verify → attribute → classify → propose → draft |
| [7. Faithfulness gate](#7-the-faithfulness-gate--what-actually-blocks-a-wrong-answer) | the three violations it catches |
| [8. RAG](#8-rag--ingest-chunking-retrieval-and-knowing-when-to-abstain) | ingest, measured chunking, abstention |
| [9. Closed loop](#9-the-closed-loop--approval-execution-and-learned-ρ) | approval chain and ρ learning |
| [10. Evaluation](#10-evaluation--prompts-traces-scorers-judges) | prompts, traces, scorers, judges |
| [11. Observability](#11-observability--the-trace-span-tree) | the MLflow span tree |
| [12. Governance](#12-governance--unity-catalog-as-catalog-of-record) | Unity Catalog, honestly |
| [12b. The workbench](#12b-the-workbench--react-over-fastapi) | React over FastAPI, and why the split |
| [12c. API security](#12c-api-security--oauth2-password-grant--jwt-bearer) | OAuth2 + JWT, and what it does not cover |
| [13. Run it](#13-run-it) | every make target |
| [14. Repo layout](#14-repository-layout) | where everything lives |
| [15. Status](#15-status--what-is-real-and-what-is-not) | what is real, what is not |

---

## 1. The one idea: the LLM never computes

Every design decision below follows from one boundary. The model may **choose tools**
and **phrase results**. It may not produce a figure, pick a magnitude, or decide that
an input is trustworthy.

```mermaid
flowchart LR
    subgraph MODEL["🤖 What the LLM may do"]
        direction TB
        M1["pick which tool to call"]
        M2["phrase a computed result"]
        M3["answer general theory, labelled as such"]
    end
    subgraph NEVER["🚫 What it may never do"]
        direction TB
        N1["produce a number"]
        N2["choose a change magnitude"]
        N3["decide inputs are trustworthy"]
        N4["approve or execute anything"]
    end
    subgraph DET["⚙️ Deterministic, provenance-carrying"]
        direction TB
        D1["core/physics.py — PVT + volumes"]
        D2["core/decompose.py — exact LMDI split"]
        D3["core/anomaly.py — detection rules"]
        D4["core/recommend.py — rho + safety clamps"]
        D5["core/audit.py — input verdict"]
        D6["core/approval.py — human chain"]
    end
    MODEL --> GATE{{"core/faithfulness.py GATE"}}
    DET --> GATE
    GATE -->|"verified"| OUT["answer reaches the analyst"]
    GATE -->|"rejected"| FALLBACK["computed attribution shown instead"]
    NEVER -.->|"structurally impossible"| DET

    style MODEL fill:#e8f0fe,stroke:#4285f4
    style NEVER fill:#fce8e6,stroke:#ea4335
    style DET fill:#e6f4ea,stroke:#34a853
    style GATE fill:#fef7e0,stroke:#fbbc04
```

A useful way to read the rest of this document: **every arrow that carries a number
starts in the green box.**

---

## 2. System map

```mermaid
flowchart TB
    subgraph UI["🖥️ React workbench (web/) — make app"]
        T1["🗺️ Portfolio — every pattern vs target"]
        T2["📈 Report — trend + ΔVRR attribution"]
        T3["🔎 Lineage — raw to curated + RECOMPUTE"]
        T4["✅ Approval queue — draft to analyst to RM to site"]
        DRAWER["💬 Chat drawer, docked right of every view<br/>transcript in vrr_agent.chat_history"]
    end

    subgraph APIL["🌐 FastAPI (api/) — 22 endpoints"]
        RP["routes_patterns — reads, pass-through to tools"]
        RA["routes_approvals — role checks ENFORCED here"]
        RC["routes_chat — one gated answer per request"]
    end

    subgraph AGENT["🧠 agent/ — the reasoning layer"]
        CHAT["chat.py — intent router"]
        ANALYST["analyst.py — 5-step deterministic pipeline"]
        GRAPH["graph.py — LangGraph StateGraph"]
        TOOLS["tools.py — 15 deterministic tools"]
        LLMC["llm.py + providers.py<br/>ollama · openai · anthropic"]
    end

    subgraph CORE["⚙️ core/ — pure, no I/O, unit-tested off-DB"]
        direction LR
        C1["physics · decompose"]
        C2["anomaly · audit"]
        C3["recommend · approval"]
        C4["faithfulness · knowledge · ids"]
    end

    subgraph PG["🐘 PostgreSQL + pgvector"]
        direction LR
        RAW["vrr_raw<br/>7 tables"]
        CUR["vrr_curated<br/>3 tables"]
        AG["vrr_agent<br/>8 tables incl.<br/>reservoir_knowledge"]
    end

    subgraph OPS["🔭 Ops + governance"]
        direction LR
        ML["MLflow OSS<br/>traces · eval · prompt registry"]
        UC["Unity Catalog OSS<br/>catalog-of-record"]
        OLL["Ollama<br/>qwen2.5:7b + nomic-embed-text"]
    end

    UI --> APIL
    DRAWER --> APIL
    APIL --> CHAT
    APIL --> TOOLS
    CHAT -->|"default"| ANALYST
    CHAT -->|"agentic=true"| GRAPH
    ANALYST --> TOOLS
    GRAPH --> TOOLS
    GRAPH --> LLMC
    ANALYST --> LLMC
    TOOLS --> CORE
    TOOLS --> PG
    LLMC --> OLL
    AGENT -.->|"every span"| ML
    PG -.->|"names + RBAC"| UC

    style CORE fill:#e6f4ea,stroke:#34a853
    style PG fill:#e8eaed,stroke:#5f6368
    style AGENT fill:#e8f0fe,stroke:#4285f4
    style OPS fill:#f3e8fd,stroke:#a142f4
    style APIL fill:#e0f2f1,stroke:#009688
```

### Stack, and why each piece

| Concern | Tool | Why this one |
|---|---|---|
| Data + compute | **PostgreSQL** | VRR is pure SQL — no Spark needed at this scale |
| Knowledge index | **pgvector** | same DB: one store, one backup, one connection |
| Agent loop | **LangGraph** `StateGraph` | reducers make evidence append-only; the gate is an *edge* |
| Narrator | **Ollama** (`qwen2.5:7b`) | local + free; OpenAI/Anthropic pluggable, billable, off by default |
| Embeddings | **nomic-embed-text** | 768-dim, local, matches the `vector(768)` column |
| Governance | **Unity Catalog OSS** | catalog-of-record (RBAC + lineage) — [not a query engine](#12-governance--unity-catalog-as-catalog-of-record) |
| Tracing / eval / registry | **MLflow OSS** | span trees, scorers, prompt versioning |
| UI | **React + Vite + TypeScript + Tailwind** | 4 views + a docked chat drawer, talking to FastAPI |
| API | **FastAPI** | the same tools over HTTP — and where the approval role checks live |

---

## 3. Data model — three schemas, raw → curated → agent

```mermaid
flowchart LR
    subgraph RAW["vrr_raw — as the field reports it"]
        PV["production_volumes_daily<br/>keyed by completion only"]
        PAT["pattern"]
        COMP["completion"]
        PCF["pattern_contribution_factor<br/>time-windowed allocation"]
        PP["pattern_pressure — time-windowed"]
        PVT["completion_pvt_characteristics<br/>by completion, test_date, pressure"]
        PT["pattern_target"]
    end
    subgraph CURATED["vrr_curated — computed by core.physics"]
        CC["completion_contrib — 272,880 rows<br/>oil/water/free-gas/injection reservoir bbl"]
        PVRR["pattern_vrr — daily + monthly<br/>volume-weighted average FVFs"]
        CUM["pattern_vrr_cumulative<br/>running sum, not the average of ratios"]
    end
    subgraph AGENTS["vrr_agent — what the agent knows and decides"]
        MEM["pattern_memory — learned rho + typical band"]
        IA["input_audit — DATA_ARTIFACT vs REAL_SIGNAL"]
        AQ["action_queue — drafts awaiting approval"]
        AH["adjustment_history — executed changes + outcomes"]
        SL["safety_limits — max percent change per injector"]
        RK["reservoir_knowledge — chunks + vector(768)"]
        KR["knowledge_registry — human approval gate"]
        CH["chat_history — shared transcript"]
    end

    PV --> CC
    PCF --> CC
    PVT --> CC
    PP --> CC
    COMP --> CC
    CC --> PVRR
    PVRR --> CUM
    PVRR --> IA
    PVRR --> AQ
    PAT --> PVRR
    PT --> AQ
    MEM --> AQ
    SL --> AQ
    AQ --> AH
    AH -.->|"EMA update of rho"| MEM
    KR -->|"approved only"| RK

    style RAW fill:#f1f3f4,stroke:#5f6368
    style CURATED fill:#e6f4ea,stroke:#34a853
    style AGENTS fill:#e8f0fe,stroke:#4285f4
```

Aligned to the production VRR data model: volumes keyed by **completion only**,
time-windowed allocation and pressure, PVT by `(completion, test_date, pressure)`,
derived `Amount_Type`, a `HAVING` gate, and cumulative VRR as a **running sum of
reservoir volumes** — never the average of monthly ratios. Details and the local
deviations: [docs/vrr_data_model.md](docs/vrr_data_model.md).

---

## 4. The physics — how a VRR number is built

```mermaid
flowchart TB
    P["pattern pressure at the period"] --> LADDER
    PVTPTS["PVT points for the completion"] --> LADDER
    LADDER["1️⃣ pvt_lookup — the PVT ladder<br/>exact → interpolate → extrapolate → closest"]
    LADDER --> METHOD{{"method recorded on every row"}}
    METHOD -->|"exact / interpolated"| OK["trustworthy input"]
    METHOD -->|"extrapolated / closest"| SUSPECT["⚠️ suspect input — core.audit vetoes advice"]

    LADDER --> FVF["Bo · Bw · Bg · Rs · Bw_inj · Bg_inj"]
    VOL["daily volumes<br/>OIL · WATER · GAS · WATER_INJ · GAS_INJ"] --> CONTRIB
    FACTOR["allocation FACTOR — completion to pattern"] --> CONTRIB
    FVF --> CONTRIB

    CONTRIB["2️⃣ completion_contribution<br/>oil_res = FACTOR · OIL · Bo<br/>water_res = FACTOR · WATER · Bw<br/>free_gas_res = FACTOR · (GAS·1000 − Rs·OIL) · Bg<br/>water_inj_res = FACTOR · WATER_INJ · Bw_inj<br/>gas_inj_res = FACTOR · GAS_INJ·1000 · Bg_inj"]

    CONTRIB --> AGG["3️⃣ aggregate to pattern × period"]
    AGG --> VRR["VRR = sum of injection reservoir bbl<br/>divided by<br/>sum of production reservoir bbl"]
    VRR --> BAND{{"vs target 1.00, band 0.90 to 1.10"}}

    style LADDER fill:#e6f4ea,stroke:#34a853
    style CONTRIB fill:#e6f4ea,stroke:#34a853
    style SUSPECT fill:#fce8e6,stroke:#ea4335
```

**Why the PVT method is carried all the way through:** a VRR built on an *extrapolated*
lookup is a number with unquantified error. `core/audit.py` turns that into a verdict —
`DATA_ARTIFACT` (fix the inputs, no valve change) vs `REAL_SIGNAL` (diagnose and
recommend) — and the guardrail *never recommend on suspect inputs* is enforced in code,
not in a prompt.

---

## 5. The agent — a real LangGraph `StateGraph`

`agent/graph.py`. Compiled once per process; `build().get_graph().draw_mermaid()`
regenerates this diagram from the code.

```mermaid
flowchart TD
    START(["START"]) --> PLAN
    PLAN["plan — 🤖 the ONLY node that may speak<br/>picks from 15 tool specs, or answers"]
    PLAN -->|"tool_calls present"| TOOLS
    PLAN -->|"no tool_calls, it answered"| GATE
    PLAN -->|"steps == max_steps"| BUDGET
    TOOLS["tools — ⚙️ executes over Postgres<br/>harvests every number into facts"]
    TOOLS --> PLAN
    GATE["gate — 🛡️ core.faithfulness<br/>drivers · directions · numbers"]
    GATE -->|"rejected, first attempt"| REPAIR
    GATE -->|"passed, or already repaired"| FIN(["END"])
    REPAIR["repair — 🤖 one rewrite, violation fed back<br/>TOOLS WITHHELD"]
    REPAIR --> GATE
    BUDGET["budget — step budget exhausted"] --> FIN

    style PLAN fill:#e8f0fe,stroke:#4285f4
    style REPAIR fill:#e8f0fe,stroke:#4285f4
    style TOOLS fill:#e6f4ea,stroke:#34a853
    style GATE fill:#fef7e0,stroke:#fbbc04
```

### The state schema is the contract

```python
class State(TypedDict, total=False):
    messages:       Annotated[list[dict],  operator.add]   # append-only
    trace:          Annotated[list[dict],  operator.add]   # append-only  {tool, args, result}
    facts:          Annotated[list[float], operator.add]   # append-only  numbers the answer may cite
    last_decompose: dict | None      # newest VRR_DECOMPOSE — what the gate checks against
    answer: str;  gate: dict;  steps: int;  max_steps: int;  repaired: bool
```

The reducers are the point: a node returns only what it **adds**, so no step can
silently drop evidence the gate is about to check. Five properties fall out:

| Property | Mechanism |
|---|---|
| Evidence cannot be dropped | `operator.add` reducers on `messages`/`trace`/`facts` |
| The gate cannot be bypassed | every path `plan → END` routes through the `gate` node |
| Repaired text is gated too | `repair → gate`, never `repair → END` |
| Runs resume | compiled with `InMemorySaver`; `run(..., thread_id=…)` continues |
| Runaway loops stop | `max_steps` model turns + a `recursion_limit` backstop |

### The 15 deterministic tools

| Group | Tools |
|---|---|
| Discover | `LIST_PATTERNS` · `VRR_OVERVIEW` · `PATTERN_CONTEXT` · `LIST_COMPLETIONS` |
| Measure | `VRR_GET` · `VRR_TREND` · `VRR_DECOMPOSE` |
| Verify | `VRR_AUDIT` (recompute from raw) · `INPUT_AUDIT` · `DATA_QUALITY` · `VRR_LINEAGE` |
| Decide | `DETECT_ANOMALIES` · `RECOMMEND_CHANGE` · `FIND_PRECEDENT` |
| Recall | `SEARCH_KNOWLEDGE` (pgvector, RETRIEVER span) |

A tool error is returned as `{"error": …}` data, never raised — a broken tool must not
crash the loop; it must be something the model can see and route around.

### Two modes, gated identically

```mermaid
flowchart LR
    Q["analyst question"] --> ROUTER["chat.py — intent router"]
    ROUTER --> I{{"intent"}}
    I -->|"explain · recommend · audit · lineage<br/>completions · portfolio · data_quality"| DEFAULT
    I -->|"knowledge"| RAG["RAG path — see section 8"]
    I -->|"general"| GEN["model knowledge, labelled 'not your data'"]
    DEFAULT["DEFAULT ~8s — analyst.analyze runs the 5 steps;<br/>the model only REWRITES the result"]
    AGENTIC["AGENTIC ~1-2min — the model picks tools itself"]
    DEFAULT --> GATE2["faithfulness gate"]
    AGENTIC --> GATE2
    ROUTER -.->|"toggle in the drawer"| AGENTIC
    GATE2 --> ANS["answer + provenance caption"]

    style DEFAULT fill:#e6f4ea,stroke:#34a853
    style AGENTIC fill:#e8f0fe,stroke:#4285f4
    style GATE2 fill:#fef7e0,stroke:#fbbc04
```

On a local 7B the agentic loop gets caught fabricating figures more often (it likes to
compute daily averages). When it does, the computed answer is shown with the violation
displayed — **the designed outcome, not a failure.**

---

## 6. The analyst pipeline — five steps, in this order

`agent/analyst.py`. The order is the argument: you cannot attribute a number you have
not verified, and you must not recommend on inputs you do not trust.

```mermaid
flowchart TB
    S1["1️⃣ VERIFY — VRR_AUDIT via core.physics<br/>recompute the month from raw daily rows<br/>diff against stored · report the PVT method"]
    S1 --> S2["2️⃣ ATTRIBUTE — VRR_DECOMPOSE via core.decompose<br/>exact log-mean (LMDI) split<br/>contributions sum to ΔVRR, to machine precision"]
    S2 --> S3["3️⃣ CLASSIFY — DETECT_ANOMALIES via core.anomaly"]
    S3 --> R{{"which rule fired?"}}
    R -->|"out_of_band — outside the learned band"| S4
    R -->|"sustained_drift — 3+ same-sign moves, cumulative 0.10"| S4
    R -->|"extrapolated_pvt — an INPUT problem"| VETO["🚫 no valve change<br/>draft = investigate inputs<br/>owner = data steward"]
    S4["4️⃣ PROPOSE — RECOMMEND_CHANGE via core.recommend"]
    S4 --> S5["5️⃣ DRAFT — assemble the case file<br/>into action_queue at stage 'draft'"]
    VETO --> S5

    style VETO fill:#fce8e6,stroke:#ea4335
    style S1 fill:#e6f4ea,stroke:#34a853
    style S2 fill:#e6f4ea,stroke:#34a853
```

### How a recommendation gets its magnitude

```mermaid
flowchart LR
    A["target VRR minus current VRR"] --> B["1. physics<br/>injection reservoir bbl needed"]
    B --> C["2. precedent calibration<br/>divide by rho, the learned per-pattern gain"]
    C --> D["3. allocate across injectors<br/>by current contribution"]
    D --> E["4. SAFETY CLAMP — each injector limited to<br/>safety_limits.max_inj_rate_change_pct"]
    E --> F["5. expected post-VRR<br/>current + rho · applied / production"]
    F --> G{{"was anything clamped?"}}
    G -->|"yes"| H["note: clamped by safety limits —<br/>expected VRR will not fully reach target"]

    style E fill:#fce8e6,stroke:#ea4335
    style C fill:#e6f4ea,stroke:#34a853
```

The model never picks the number **and never sees a path where it could** — step 4 is a
`min`/`max` in Python against a row in `vrr_agent.safety_limits`.

---

## 7. The faithfulness gate — what actually blocks a wrong answer

`core/faithfulness.py`. Pure, no second model, no I/O — it checks narration against the
decomposition that produced it.

```mermaid
flowchart TB
    NARR["LLM narration"] --> C1
    DECOMP["core.decompose result<br/>term · label · contribution · share"] --> C1
    FACTS["facts — every number a tool returned<br/>plus x100 and rounded variants"] --> C3

    C1{{"1. terms named in the text —<br/>does the decomposition contain them?"}}
    C1 -->|"no"| V1["❌ unsupported_driver"]
    C1 -->|"yes"| C2
    C2{{"2. clause-level direction check —<br/>does the text move the term<br/>the way the numbers say?"}}
    C2 -->|"no"| V2["❌ wrong_direction"]
    C2 -->|"yes"| C3
    C3{{"3. is every decimal in the answer<br/>present in facts?"}}
    C3 -->|"no"| V3["❌ uncited_number"]
    C3 -->|"yes"| PASS["✅ verdict ok"]

    V1 --> REPAIR
    V2 --> REPAIR
    V3 --> REPAIR
    REPAIR["repair once — violation fed back, tools withheld"] --> RECHECK{{"passes now?"}}
    RECHECK -->|"yes"| PASS
    RECHECK -->|"no"| REPLACE["🛡️ REPLACE with the computed attribution<br/>terse and right beats fluent and wrong"]

    style PASS fill:#e6f4ea,stroke:#34a853
    style REPLACE fill:#fef7e0,stroke:#fbbc04
    style V1 fill:#fce8e6,stroke:#ea4335
    style V2 fill:#fce8e6,stroke:#ea4335
    style V3 fill:#fce8e6,stroke:#ea4335
```

Two details that took real work:

- **Clause-level, not sentence-level.** *"water injection fell, pushing VRR up"* carries
  two opposite direction words; only the first is a claim *about the term*.
- **Listing is not claiming.** "gas injection contributed 0.0%" is fine; calling a 0.0%
  term *the driver* is not. `DRIVER_CLAIMS` phrases ("driven by", "main", "responsible
  for") turn a mention into a claim, and only terms above a 10% share may carry one.

Real output from a live run:

```
⚠️ The narration was rejected by the faithfulness gate.
Computed attribution:
- water production: +0.0294 VRR (59.6% of the move)
- oil production:   +0.0130 VRR (26.3% of the move)
- water injection:  -0.0069 VRR (14.1% of the move)
[gate: uncited_numbers: [3.36] | retried: True]
```

The model cited **3.36** — a number no tool returned. Caught, retried, replaced.

---

## 8. RAG — ingest, chunking, retrieval, and knowing when to abstain

```mermaid
flowchart TB
    U["👤 drop a file in ./knowledge_uploads/<br/>.pdf .txt .md .html .docx .csv"] --> REG
    REG["1️⃣ register_new — sha1 into knowledge_registry"] --> REV
    REV{{"2️⃣ HUMAN review — status = approved<br/>deliberately NOT automated"}}
    REV -->|"approved"| LOAD
    REV -->|"rejected"| STOP["never embedded"]
    LOAD["3️⃣ document_loaders.py to List of Documents<br/>file_name · page (1-based) · file_type"]
    LOAD --> SPLIT["4️⃣ text_splitters.py<br/>recursive 400/60 — the MEASURED default"]
    SPLIT --> PII["5️⃣ core.knowledge.redact_pii<br/>email · phone · SSN · card · creds<br/>PII never reaches the DB"]
    PII --> EMB["6️⃣ embed — nomic-embed-text, 768-dim, local"]
    EMB --> STORE["vrr_agent.reservoir_knowledge<br/>text + embedding vector(768)"]

    Q["question"] --> SEARCH["7️⃣ search — cosine nearest via pgvector"]
    STORE --> SEARCH
    SEARCH --> FLOOR{{"8️⃣ score at or above 0.62?<br/>the similarity FLOOR"}}
    FLOOR -->|"yes"| CTX["formatted context<br/>[file.pdf p.4] (similarity 0.82)"]
    FLOOR -->|"nothing clears it"| IDK["🛑 I don't know —<br/>the model is NEVER CALLED"]
    CTX --> ANS["grounded answer + citations"]

    style REV fill:#fef7e0,stroke:#fbbc04
    style PII fill:#fce8e6,stroke:#ea4335
    style IDK fill:#fce8e6,stroke:#ea4335
    style SPLIT fill:#e6f4ea,stroke:#34a853
```

### Chunking is judged by retrieval, never by eye

Each chunk is embedded in **isolation** — context lost at a boundary is unrecoverable at
query time. So the test is retrieval, not appearance (`make chunks`):

| Strategy | chunks | ends on a sentence | recall@2 | MRR |
|---|---|---|---|---|
| fixed (200 chars) | 4 | 25% | 0.33 | 0.56 |
| **recursive (400/60)** ← default | 3 | 100% | **1.00** | **1.00** |
| semantic (cosine 0.75) | 9 | 100% | 0.67 | 0.78 |

Fixed splitting cuts `"…the response factor rho … It starts │ at 0.85…"`; the question
*"what does rho start at?"* then ranks the chunk holding `0.85` **third** — outside the
top-k that reaches the prompt.

**Semantic chunking is not automatically better.** On short, dense procedure text it
over-splits (83-char chunks carry too little context to rank). That finding is exactly
why the measurement exists.

Diagnosing a failing probe:

| Symptom | Cause | Fix |
|---|---|---|
| recall high, MRR low | chunks too big, answer buried | smaller chunks / more overlap |
| fails at one chunk size | a boundary cuts the rule | recursive, or raise overlap |
| **fails at every size** | vocabulary mismatch | query expansion / hybrid search |
| off-topic scores like answerable | wrong embedder for the domain | `make floor` shows a gap ≤ 0 |

### The floor is measured, not guessed

```mermaid
flowchart LR
    subgraph MEASURED["make floor — against the live index"]
        A["ANSWERABLE questions — min top-1 = 0.671"]
        B["OFF-TOPIC questions — max top-1 = 0.564"]
    end
    A --> GAP["gap +0.107"]
    B --> GAP
    GAP --> F["VRR_RETRIEVAL_MIN_SCORE = 0.62"]
    F --> NOTE["⚠️ nomic-embed-text scores UNRELATED text<br/>at 0.40 to 0.56 — an intuitive 0.35 admits<br/>everything and the agent never abstains"]

    style NOTE fill:#fce8e6,stroke:#ea4335
    style F fill:#e6f4ea,stroke:#34a853
```

A **negative** gap means no threshold separates the sets — that is a retrieval problem
(chunking, embedder), not a tuning problem. `rulebook_unanswerable` in the eval set
guards the abstain path: without a negative case, a retriever that always returns its k
nearest rows scores identically to one that knows when it has nothing.

---

## 9. The closed loop — approval, execution, and learned ρ

```mermaid
stateDiagram-v2
    [*] --> draft: anomaly fires, action_queue row created
    draft --> analyst: analyst approves
    analyst --> rm: RM approves
    rm --> site: site engineer approves
    site --> executed: site marks executed
    executed --> [*]

    draft --> rejected: any approver rejects
    analyst --> rejected
    rm --> rejected
    rejected --> [*]

    note right of site
        ONLY the site role may execute.
        Enforced in core/approval.py,
        not in the UI.
    end note
    note right of executed
        writes adjustment_history:
        pattern, date, driver,
        recommended surface rate,
        predicted change in VRR
    end note
```

Then the loop closes:

```mermaid
flowchart LR
    EX["executed change + predicted ΔVRR"] --> WAIT["next month's build — make build"]
    WAIT --> ACT["actual post-VRR observed"]
    ACT --> EMA["core.recommend.update_response_factor<br/>rho moves toward observed, alpha = 0.3"]
    EMA --> MEM["vrr_agent.pattern_memory — learned rho per pattern"]
    MEM -.->|"calibrates the NEXT recommendation"| NEXT["step 2 of section 6"]

    style EMA fill:#e6f4ea,stroke:#34a853
```

> **Honest status:** the write-back of `actual_post_vrr` and the EMA update are the top
> open task — `core.recommend.update_response_factor` exists and is unit-tested, but the
> pipeline job that feeds it observed outcomes is not wired yet.

---

## 10. Evaluation — prompts, traces, scorers, judges

```mermaid
flowchart TB
    subgraph AUTHOR["authored + versioned"]
        PR["MLflow Prompt Registry — make prompts<br/>vrr_domain_primer · vrr_narrator<br/>vrr_knowledge_rag · vrr_general"]
        QS["data/evaluation/vrr_questions.py — 11 cases<br/>expected_intent · expected_tools · forbidden_tools<br/>expected_verdict · must_mention · must_not_mention"]
    end
    QS --> RUN["make traces — run the agent, log spans + expectations"]
    PR --> RUN
    RUN --> TR["MLflow traces tagged eval_case"]
    TR --> SC["make eval"]

    subgraph DET2["6 deterministic scorers — trace only, no model"]
        D1["gate_passed"]
        D2["numbers_grounded"]
        D3["audit_before_advice"]
        D4["no_advice_on_artifact"]
        D5["tools_used"]
        D6["latency_ms"]
    end
    subgraph JUD["3 LLM judges — make_judge"]
        J1["provenance_cited"]
        J2["decision_complete"]
        J3["grounded_in_documents"]
    end
    SC --> DET2
    SC --> JUD
    DET2 --> REPORT["run metrics"]
    JUD -.->|"⚠️ UNMEASURED — see below"| REPORT

    style DET2 fill:#e6f4ea,stroke:#34a853
    style JUD fill:#fce8e6,stroke:#ea4335
```

### The scorers, and what each one catches

| Scorer | Fails when | Latest run |
|---|---|---|
| `gate_passed` | any span records a faithfulness rejection | 1.00 |
| `numbers_grounded` | a decimal in the answer appears in no tool span | 0.98 |
| `audit_before_advice` | a recommendation is issued before an input audit | 1.00 |
| `no_advice_on_artifact` | a change is proposed on a `DATA_ARTIFACT` verdict | 1.00 |
| `tools_used` | *(diagnostic)* zero means the model spoke alone | mean 4.34 |
| `latency_ms` | *(diagnostic)* quality gains that cost 10× stay visible | mean 1.12 s |

### The case set — 11 cases, including the negative ones

| Case | Guards |
|---|---|
| `explain_out_of_band` | the core verify → attribute → propose path |
| `audit_clean_number` | an audit question must not turn into advice |
| `suspect_inputs_no_advice` | **no recommendation on a `DATA_ARTIFACT` verdict** |
| `healthy_pattern_no_action` | a healthy pattern gets no change proposed |
| `lineage_derivation` · `completions_listing` · `portfolio_triage` · `data_quality_check` | routing + tool selection |
| `rulebook_step_limit` | RAG grounding: the answer stays inside the retrieved excerpts |
| `rulebook_unanswerable` | **the agent must ABSTAIN** |
| `general_concept` | theory answered, and labelled "not your data" |

### ⚠️ The judges are not usable yet — stated plainly

`make_judge(base_url=…)` was pointed at `{ollama}/v1`, but MLflow **POSTs to that URL
verbatim** instead of appending `/chat/completions`. Every judge died on a silent `404`,
so `make eval` reported 6 scorers, exited 0, and looked green while `get_scorers()`
claimed 9. The endpoint is fixed and they now execute — but they score ~0.02 with
rationales reading *"Not enough information provided"*, meaning the trace content is not
reaching the judge. Treat `provenance_cited` / `decision_complete` /
`grounded_in_documents` as **unmeasured**.

> **Repo rule:** where a judge and a deterministic scorer disagree, **the deterministic
> one is right.** `numbers_grounded` scores 0.98 over the same traces the judges score
> 0.02.

> **Evaluation rule:** always `make traces` immediately before `make eval`, and only via
> the Makefile — `make eval` passes `--eval-only`, which filters `tags.eval_case != ''`.
> Running the script bare scores the last 50 traces of *any* origin, so the `*/mean`
> denominators shift and two runs stop being comparable.

---

## 11. Observability — the trace span tree

Every question is a span tree in MLflow. Span **types** matter: a `RETRIEVER` span
carrying `mlflow.entities.Document`s is what makes retrieval scorable at all — the same
content in a `TOOL` span is invisible to the retrieval scorers.

```
chat.respond ······················ AGENT
├── agent.tool_loop ··············· AGENT      (agentic mode only)
│   ├── node.plan ················· LLM        ← the model's turn
│   ├── node.tools ················ CHAIN
│   │   ├── tool_call VRR_AUDIT ··· TOOL       ← recompute from raw
│   │   └── tool_call VRR_DECOMPOSE TOOL       ← LMDI attribution
│   ├── node.plan ················· LLM
│   ├── node.gate ················· CHAIN      ← faithfulness verdict
│   └── node.repair ··············· LLM        (only when the gate rejects)
├── llm.chat ······················ LLM        (default mode: rewrite only)
├── search_knowledge ·············· RETRIEVER  ← Documents, not a dict
└── faithfulness_gate ············· CHAIN
```

Tool spans are recorded **structured and untruncated** — a truncated payload silently
breaks every grounding check that reads the trace, which is a bug this repo has already
had once.

---

## 12. Governance — Unity Catalog as catalog-of-record

Unity Catalog OSS is a **catalog**, not a query engine. It governs registered assets
(RBAC + lineage + credential vending) but does **not** intercept live PostgreSQL queries
in OSS — Lakehouse Federation is Databricks-only.

```mermaid
flowchart LR
    AG["agent"] -->|"1. resolve name + permission"| UC["Unity Catalog OSS<br/>catalog-of-record"]
    UC -->|"2. authorized name"| AG
    AG -->|"3. execute"| PG["PostgreSQL"]
    UC -.->|"registered assets: schemas · tables · lineage"| PG

    style UC fill:#f3e8fd,stroke:#a142f4
```

So the enforcement boundary is the agent, not the database. Full reasoning and the
alternative all-Delta design: [docs/design.md](docs/design.md).

---

## 12b. The workbench — React over FastAPI

Streamlit was retired in favour of a real client/server split. The reason is not
cosmetic: in the Streamlit version the approval role check was *hiding a button*, which
is UX, not a control. Now the client asks and the **server decides**.

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser (React)
    participant A as FastAPI
    participant T as agent/tools.py
    participant C as core/
    participant P as PostgreSQL

    B->>A: GET /api/patterns/{id}/audit?date=…
    A->>T: VRR_AUDIT(pattern, date)
    T->>P: read raw daily rows
    T->>C: core.physics recompute
    C-->>T: vrr + pvt_methods + provenance
    T-->>A: tool payload (verbatim)
    A-->>B: same payload, provenance intact

    Note over B,A: the browser and the LLM call the SAME tool,<br/>so a chart and an answer cannot disagree

    B->>A: POST /api/queue/{id}/advance<br/>Authorization: Bearer …
    A->>A: role comes from the verified token,<br/>checked against the stage
    A-->>B: 403 if it is not that stage's role
```

**The endpoints** (full OpenAPI at `:8000/docs`):

| Group | Endpoints |
|---|---|
| Reads | `/patterns` · `/overview` · `/data-quality` · `/input-audit` · `/patterns/{id}/context` `/trend` `/decompose` `/audit` `/lineage` `/completions` `/analysis` |
| Writes | `/patterns/{id}/submit` · `/queue/{id}/advance` · `/queue/{id}/reject` |
| Chat | `POST /chat` · `GET /chat/history` |
| System | `/health` · `/stages` · `/queue` · `/adjustments` |

**Three rules this layer holds:**

1. **No endpoint computes.** Reads are pass-throughs to `agent/tools.py`, provenance keys
   and all. A test asserts the payload comes back *verbatim* — the moment the API
   reshapes a tool result, the number on screen stops being the number the tool produced.
2. **Guardrails are server-side.** Role checks, terminal-stage refusals and the
   adjustment-history write all live in `routes_approvals.py`, and the role they check
   is the one in the caller's verified token — see [§12c](#12c-authentication--oauth2-password-grant--jwt-bearer).
3. **`adjustment_history` is written before the stage moves.** An executed item with no
   history row would silently never be learned from by the ρ loop.

**Why plain JSON and not token streaming:** `core.faithfulness` can only verify a
*finished* answer. Streaming tokens would mean streaming text the gate has not approved
and may replace. Streaming *progress events* (which tool is running) is the sane future
addition; streaming the narration is not.

The React side is deliberately thin — `web/src/api.ts` is the only place it speaks HTTP,
and no view does arithmetic. It renders what the tools computed, plus the provenance
caption under every answer:

```
Computed from your tables · qwen2.5:7b phrasing · ✅ gate passed after one repair
▸ Evidence & provenance
```

---

## 12c. Authentication — OAuth2 password grant + JWT bearer

The approval chain is a chain of *people*: a draft moves analyst → RM → site, and only
the site engineer may execute. That only means something if the server — not the client
— decides who you are. So **identity is a signed token claim**, established at login and
verified on every protected call.

### Signing in

```mermaid
sequenceDiagram
    autonumber
    participant U as Analyst
    participant W as Workbench (React)
    participant A as FastAPI
    participant DB as vrr_agent.app_user

    U->>W: username + password
    W->>A: POST /api/auth/token (form-encoded)
    A->>DB: look up the account
    DB-->>A: password hash + role + active flag
    A->>A: verify hash · check the account is active
    alt credentials good
        A-->>W: signed token { subject, role, expiry }
        W->>W: keep it for the session
        W->>A: GET /api/auth/me
        A-->>W: who you are — the sidebar shows this
    else credentials bad
        A-->>W: 401 (same message either way)
    end
```

The failure message is identical whether the account does not exist or the password is
wrong: a login that distinguishes them tells a stranger which usernames are real.

### Making a request

```mermaid
flowchart TB
    REQ["request from the workbench"] --> KIND{"read or write?"}
    KIND -->|"read: portfolio, trend,<br/>attribution, lineage, audit"| SERVE["served — no account needed"]
    KIND -->|"write, or ask the agent"| TOK{"valid token?"}
    TOK -->|"missing, expired,<br/>or not ours"| R401["401 — sign in"]
    TOK -->|"valid"| ROLE{"does the claimed role<br/>own this step?"}
    ROLE -->|"no"| R403["403 — refused, and the<br/>request body cannot argue"]
    ROLE -->|"yes"| DO["perform it, recording the<br/>token's subject as the actor"]

    style SERVE fill:#e6f4ea,stroke:#34a853
    style DO fill:#e6f4ea,stroke:#34a853
    style R401 fill:#fce8e6,stroke:#ea4335
    style R403 fill:#fce8e6,stroke:#ea4335
```

Two things fall out of that shape:

- **The role is never read from the request.** It is a claim inside the token, checked
  against the stage the item currently sits at. Hiding a button in the UI is convenience;
  the refusal is the control.
- **The actor on the audit trail is the token's subject.** `action_queue.stage_by` and
  `adjustment_history.approved_by` record who the server authenticated — the ρ learning
  loop and any later review read a name that was proven, not typed.

### What needs an account

| | Needs a token | Why |
|---|---|---|
| Portfolio, trend, attribution, lineage, audit, health | no | reading is how you evaluate the tool; a fresh clone should just work |
| Ask the agent (`/chat`) | **yes** | it spends real compute |
| Draft a change, advance, reject | **yes** | these move a valve change toward execution |

### Roles

```mermaid
stateDiagram-v2
    [*] --> draft: agent raises it
    draft --> analyst: analyst signs off
    analyst --> rm: RM signs off
    rm --> site: site signs off
    site --> executed: site executes
    executed --> [*]

    note right of site
        Each arrow needs an account
        holding THAT role. Signing in
        as one role cannot perform
        another role's step.
    end note
```

### Setting it up

```bash
make users                          # creates the account table and seeds one demo
                                    # account per role, with hashed passwords
make users p=<your-password>        # …choosing the password instead of the default
```

Set a signing key in `.env` before real use — without one the API generates a throwaway
key per process, so sessions end at every restart and it says so loudly at startup:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # → VRR_JWT_SECRET in .env
```

`.env` is gitignored. No credential or key is committed, and none has a default baked
into the source — a well-known signing key in a public repo would look like security
while providing none.

### Before exposing this beyond localhost

The defaults suit a workbench you run on your own machine. Moving it anywhere shared is
a deliberate step with a checklist, not a copy of this configuration:

1. **Change the seeded accounts** — `make users p=…`, or create real ones and remove the
   demos. Treat the shipped defaults as placeholders, not accounts.
2. **Set `VRR_JWT_SECRET`** to a value generated as above, hold it like a password, and
   rotate it if it is ever shared. Rotating invalidates existing sessions by design.
3. **Terminate TLS in front of the API.** Bearer tokens over plain HTTP are readable in
   transit; nothing in the application layer compensates for that.
4. **Shorten `VRR_JWT_TTL_MINUTES`** from the 12-hour default to match how long a session
   should reasonably live, since sessions end by expiry rather than by revocation.
5. **Decide where the token lives.** The browser build keeps it in web storage, which is
   the usual trade for a single-page app; a deployment with stricter requirements should
   move it to an httpOnly cookie and add CSRF protection.
6. **Point at your identity provider** if you have one. The verification step is one
   function — `current_user` in `api/auth.py` — and swapping local signing for an IdP's
   published keys changes nothing downstream of it.

`tests/test_auth.py` covers this layer as a set of adversarial cases — tampered tokens,
expired tokens, missing claims, and a caller trying to act above its role — so a
regression in any of them fails the suite rather than reaching a review.

---

## 13. Run it

```bash
# ---- fast path: logic only, nothing running ---------------------------------
pip install -e ".[dev]"     # installable package + pytest/ruff
pytest -q                   # 129 tests, no Postgres, no Ollama, ~5 s

# ---- the stack --------------------------------------------------------------
docker compose up -d        # postgres+pgvector · unitycatalog · mlflow
make seed                   # synthetic VRR data; core.physics computes curated
make build                  # rebuild vrr_curated from vrr_raw alone
make queue                  # anomaly → action_queue drafts awaiting approval
make users                  # seed the demo accounts (analyst/rm/site) — writes need a token
make app                    # build the React UI and serve it from FastAPI on :8000

# ---- developing the UI ------------------------------------------------------
make api                    # FastAPI with --reload; OpenAPI docs at :8000/docs
make web                    # Vite dev server on :5173, proxying /api to :8000

# ---- knowledge / RAG --------------------------------------------------------
make knowledge              # register → (human approves) → load → chunk → embed
make loaders                # what a folder/URL parses into, before embedding it
make chunks                 # score chunking strategies by retrieval (recall@k, MRR)
make floor                  # measure the abstention threshold for YOUR corpus

# ---- the model --------------------------------------------------------------
make llm-check              # can each provider complete AND tool-call?
make agent                  # one question from the CLI, through the graph

# ---- evaluation (always in this order) --------------------------------------
make prompts                # version the 4 prompts in the MLflow registry
make traces                 # run the 11 eval cases, logging spans + expectations
make eval                   # score them (deterministic + judges if a model is up)
```

Every command commented step-by-step: [docs/running.md](docs/running.md).
Config lives in `.env` — copy [.env.example](.env.example), which documents every knob.

---

## 14. Repository layout

```
vrr_agent_open/
├── docker-compose.yml          # postgres+pgvector · unitycatalog · mlflow
├── Makefile                    # every target above; auto-selects .venv/bin/python
├── .env.example                # every setting, commented (keys live in .env only)
├── src/vrr_agent_open/
│   ├── config.py               # DSN · UC · MLflow · provider · embeddings · RAG floor
│   ├── core/                   # ⚙️ PURE logic — no I/O, unit-tested off-DB
│   │   ├── physics.py          #    PVT ladder + reservoir volumes
│   │   ├── decompose.py        #    exact LMDI ΔVRR attribution
│   │   ├── anomaly.py          #    out_of_band · sustained_drift · extrapolated_pvt
│   │   ├── recommend.py        #    rho-calibrated, safety-clamped change + EMA update
│   │   ├── audit.py            #    DATA_ARTIFACT vs REAL_SIGNAL verdict
│   │   ├── faithfulness.py     #    the gate: drivers · directions · numbers
│   │   ├── approval.py         #    draft → analyst → rm → site → executed
│   │   ├── knowledge.py        #    chunking + PII redaction
│   │   └── ids.py              #    stable short ids for provenance
│   ├── agent/
│   │   ├── graph.py            # 🧠 LangGraph StateGraph (plan/tools/gate/repair/budget)
│   │   ├── tools.py            #    15 deterministic tools over psycopg
│   │   ├── analyst.py          #    the 5-step pipeline
│   │   ├── chat.py             #    intent router + RAG + the abstain path
│   │   ├── llm.py              #    one call shape for every backend
│   │   ├── providers.py        #    ollama · openai · anthropic translation
│   │   ├── history.py          #    shared chat transcript in Postgres
│   │   └── tracing.py          #    MLflow spans (TOOL · LLM · CHAIN · RETRIEVER)
│   ├── pipeline/
│   │   ├── schema.sql          #    the three-schema DDL (+ pgvector)
│   │   ├── seed.py             #    deterministic synthetic field generator
│   │   ├── build.py            #    vrr_raw → vrr_curated via core.physics
│   │   ├── input_audit.py      #    the audit gate as a batch job
│   │   ├── anomaly_to_queue.py #    anomalies → action_queue drafts
│   │   ├── knowledge_ingest.py #    register → approve → load → chunk → embed → search
│   │   ├── document_loaders.py #    pdf/txt/md/html/docx/csv/folder/URL → Documents
│   │   └── text_splitters.py   #    fixed vs recursive vs semantic + retrieval_check
│   ├── evaluation/             # 6 deterministic scorers + 3 judges
│   ├── prompts/templates.py    # the 4 versioned prompts
│   ├── api/                    # 🌐 FastAPI — the workbench backend
│   │   ├── main.py             #    app, CORS, health, serves web/dist in prod
│   │   ├── routes_patterns.py  #    reads: overview · trend · decompose · audit · lineage
│   │   ├── routes_approvals.py #    the chain — ROLE CHECKS ENFORCED SERVER-SIDE
│   │   └── routes_chat.py      #    one gated answer per request + the transcript
│   └── governance/uc_register.py
├── web/                        # ⚛️ React + Vite + TypeScript + Tailwind
│   ├── src/api.ts              #    the typed client — the only place it calls HTTP
│   ├── src/App.tsx             #    shell: sidebar filters + view routing
│   ├── src/views/              #    Portfolio · Report · Lineage · Approval
│   └── src/components/         #    ChatDrawer + shared primitives
├── data/evaluation/            # the 11 authored cases + their expectations
├── scripts/                    # traces · eval · prompts · judges · floor · llm-check
├── tests/                      # 129 tests, all off-DB
└── docs/                       # design · running · agent-flow · knowledge-flow ·
                                # evaluation · evaluation-walkthrough · vrr_data_model
```

---

## 15. Status — what is real, and what is not

| Area | State |
|---|---|
| Deterministic core (`core/`) | ✅ ported verbatim, 129 tests, no stack needed |
| Postgres schema + seed + build | ✅ verified end to end (272,880 contrib → 1,440 monthly rows) |
| LangGraph agent + 15 tools | ✅ a real `StateGraph`; every path tested with the model stubbed |
| Faithfulness gate | ✅ catches wrong drivers, wrong directions, uncited numbers |
| React workbench + FastAPI | ✅ 4 views, docked chat, login, **roles from signed JWT claims** (403/401-verified live) |
| RAG (load → chunk → embed → search) | ✅ 4 docs / 35 chunks ingested; chunking + floor both **measured** |
| Abstention ("I don't know") | ✅ floor 0.62, model not called, guarded by an eval case |
| Providers (Ollama / OpenAI / Anthropic) | 🔶 local verified; **hosted unverified — no API key on the dev machine** |
| Evaluation harness | ✅ 6 deterministic scorers over 11 cases |
| The 3 LLM judges | 🔶 **execute but unusable** — trace content is not reaching them |
| ρ write-back loop | 🔶 `update_response_factor` exists + tested; the outcome job is not wired |
| Unity Catalog registration | 🔶 skeleton; column population from `information_schema` is a TODO |
| Docker compose path | 🔶 verified against a local Postgres 18, not yet the compose stack |

Contributions welcome — Apache-2.0.
