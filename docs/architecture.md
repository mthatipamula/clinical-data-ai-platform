# Architecture

Diagrams are Mermaid, kept next to the code, so engineers and AI agents read and change the same
source. "Built" means it runs in this repository today. "Target" means it is designed and, where
noted, provisioned by Terraform, but the adapter that uses it is not written yet. The README lists
every gap in one place.

## 1. Who uses it

```mermaid
flowchart LR
  subgraph Sources
    EHR[EHR<br/>FHIR R4]
    PAYER[Health plan<br/>X12 837 claims]
    ADT[Hospital<br/>HL7 v2 ADT]
    BH[Behavioral health program<br/>consent-restricted]
    DOCS[Clinical notes<br/>unstructured text]
  end
  P[Clinical Data and AI Platform]
  EHR --> P
  PAYER --> P
  ADT --> P
  BH --> P
  DOCS --> P
  P --> PT[Patients<br/>their own record, in plain language]
  P --> PR[Providers<br/>panel, care gaps, record Q&A]
  P --> PY[Payers<br/>quality measures, cost]
  P --> OPS[Platform operations<br/>AI-assisted triage]
  P --> AUD[Compliance<br/>controls and evidence]
```

## 2. How data moves

```mermaid
flowchart TD
  S[Source systems] -->|POST raw payload| GW

  subgraph Go[Go service: services/gateway]
    GW[Ingest gateway<br/>validate kind, deduplicate by content hash]
    MPI[Identity resolution<br/>blocking, weighted match, review queue]
  end

  GW -->|unchanged payload + envelope| RAW[(Raw zone<br/>local: NDJSON · target: GCS, CMEK)]
  GW -.->|pointer only, no PHI| PS[[Pub/Sub raw-ingest<br/>target]]

  subgraph Java[Java pipeline: pipelines/harmonizer on Apache Beam]
    H[Harmonize<br/>HL7 v2, X12 837, FHIR, notes to FHIR R4]
    M[Quality measures<br/>per person, operations-permitted sources only]
  end

  RAW --> H
  H <-->|demographics in, enterprise ID out| MPI
  H -->|FHIR + provenance wrapper| HZ[(Harmonized zone<br/>local: NDJSON · target: BigQuery)]
  H -->|failures with reason| DL[(Dead letter)]
  HZ --> M --> MR[(Measure results)]

  subgraph Py[Python service: services/api]
    LLM[LLM gateway<br/>prompt registry, model allowlist, call log]
    X[Document extraction<br/>facts kept only if quoted verbatim]
    R[Rules engine<br/>versioned care-gap rules]
    DP[Data products<br/>record, panel, population, cost, lineage]
    AC[Access policy<br/>relationship, purpose of use, consent]
    QA[Record Q&A<br/>retrieval + cited answers]
  end

  HZ --> X --> R
  MR --> R
  X <--> LLM
  QA <--> LLM
  HZ --> DP
  R --> DP
  DP --> AC --> API[HTTP API] --> WEB[React web app]
  AC --> QA
  AC -->|every allow and deny| AUDIT[(Hash-chained audit log)]
  LLM -->|prompt version, model, hashes| AUDIT
```

Three properties hold at every arrow:

- **Replayable.** The raw payload is stored unchanged before anything interprets it. When
  conversion logic changes, the harmonized zone can be rebuilt from raw.
- **Traceable.** Every harmonized record carries the ingest ID and hash of the payload it came
  from and the pipeline version that produced it. The web app shows this chain on any record.
- **Idempotent.** Ingest IDs derive from source and content; identity resolution returns the
  same enterprise ID for the same record; loads replace by key. Retrying any stage is safe.

## 3. AI-assisted operations

```mermaid
sequenceDiagram
  participant A as Alert
  participant E as On-call engineer
  participant G as Ops agent
  participant L as LLM gateway
  participant P as Policy (code)
  participant H as Approver (a different person)
  participant X as Platform

  A->>E: backlog growing
  E->>G: triage this incident
  G->>L: alert + runbook catalog
  L-->>G: runbook choice + hypothesis
  G->>P: runbook steps
  P->>X: read-only diagnostics run now
  P-->>G: step outside the agent's allowlist: refused
  P-->>E: changes are queued for approval
  E--xP: approve (refused: requester cannot approve)
  H->>P: approve
  P->>X: change executes
  Note over P,X: every step above is written to the hash-chained audit log
```

The model chooses and explains. Code decides what runs. See
[ADR 0006](adr/0006-ai-operations-agent.md).

## 4. Deployment on Google Cloud (target, provisioned by `infra/terraform`)

```mermaid
flowchart TB
  subgraph PERIM[VPC Service Controls perimeter]
    subgraph VPC[Private VPC, no public IPs, Cloud NAT egress]
      subgraph GKE[GKE Autopilot, private, Binary Authorization]
        GWp[gateway<br/>sa-gateway]
        APIp[api + web<br/>sa-api]
        AGp[ops agent<br/>sa-ops-agent, read-only roles]
      end
      DF[Dataflow harmonizer<br/>sa-harmonizer]
    end
    GCS[(Cloud Storage<br/>raw, harmonized)]
    PSUB[[Pub/Sub<br/>raw-ingest + dead letter]]
    SP[(Spanner<br/>identity index)]
    BQ[(BigQuery<br/>harmonized, products, audit)]
    VX[Vertex AI<br/>Gemini, Claude]
    KMS[Cloud KMS<br/>one key per store]
  end
  IAP[Identity-Aware Proxy] --> APIp
  SRC[Source systems<br/>mTLS] --> GWp
  GWp --> GCS
  GWp --> PSUB
  GWp --> SP
  PSUB --> DF
  DF --> GCS
  DF --> BQ
  APIp --> BQ
  APIp --> VX
  AGp --> VX
  DEP[Release pipeline<br/>sa-deployer] -->|only identity that changes workloads| GKE
  KMS -.-> GCS
  KMS -.-> PSUB
  KMS -.-> SP
  KMS -.-> BQ
```

One project per environment. Each workload runs as its own service account through Workload
Identity, with no key files. The ops agent's account can read metrics and logs and call Vertex AI;
it holds no role that can change anything.

## 5. Decisions

| # | Decision | Record |
|---|---|---|
| 1 | Data rights are enforced at read time from one source registry | [ADR 0001](adr/0001-data-rights-and-isolation.md) |
| 2 | Identity resolution prefers a missed link over a false merge | [ADR 0002](adr/0002-identity-resolution.md) |
| 3 | Raw is landed unchanged; harmonized FHIR is stored as JSON with a provenance wrapper | [ADR 0003](adr/0003-raw-landing-and-fhir-storage.md) |
| 4 | Quality measures are plain Java now, CQL later | [ADR 0004](adr/0004-quality-measures.md) |
| 5 | One governed path to every model; outputs must be grounded | [ADR 0005](adr/0005-llm-governance.md) |
| 6 | The ops agent proposes; code and people dispose | [ADR 0006](adr/0006-ai-operations-agent.md) |
