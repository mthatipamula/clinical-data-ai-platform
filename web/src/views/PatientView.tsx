import { type FormEvent, type ReactNode, useEffect, useState } from "react";
import { type Answer, api, type CareGap, type Lineage, type PatientRecord, usd } from "../api";

const SOURCE_LABEL: Record<string, string> = {
  "ehr-northside": "Northside EHR", "payer-claims": "Summit claims", "hospital-adt": "Northside Hospital",
  "bh-clinic": "Riverbend program", "document-feed": "Clinical notes",
};

export function useLoad<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setError("");
    api<T>(path).then((d) => live && setData(d), (e: Error) => live && setError(e.message));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, tick, ...deps]);
  return { data, error, reload: () => setTick((t) => t + 1), setData };
}

export function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}{count !== undefined && <span className="count">{count}</span>}</h2>
      {children}
    </section>
  );
}

function Source({ eid, item }: { eid: string; item: { resource_id: string; source: string } }) {
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [open, setOpen] = useState(false);
  const toggle = () => {
    setOpen(!open);
    if (!lineage) api<Lineage>(`/api/patients/${eid}/lineage/${item.resource_id}`).then(setLineage, () => setOpen(false));
  };
  return (
    <span className="source">
      <button className="link" onClick={toggle} aria-expanded={open} title="Show where this record came from">
        {SOURCE_LABEL[item.source] ?? item.source}
      </button>
      {open && lineage && (
        <ol className="lineage">
          {lineage.steps.map((s) => (
            <li key={s.stage}>
              <strong>{s.stage}</strong>
              {Object.entries(s).filter(([k]) => k !== "stage").map(([k, v]) => (
                <span key={k}>{k.replace(/_/g, " ")}: <code>{v.length > 24 ? v.slice(0, 16) + "…" : v}</code></span>
              ))}
            </li>
          ))}
        </ol>
      )}
    </span>
  );
}

function Gaps({ gaps, role }: { gaps: CareGap[]; role: string }) {
  if (!gaps.length) return <p className="empty">Nothing needs attention right now.</p>;
  return (
    <ul className="gaps">
      {gaps.map((g) => (
        <li key={g.rule_id} className={`gap ${g.severity}`}>
          <div className="gap-title">{g.title}</div>
          <p>{role === "patient" ? g.patient_text : g.recommendation}</p>
          {g.rule_version !== undefined && (
            <small>Rule <code>{g.rule_id}</code> v{g.rule_version}{g.evidence?.length ? ` · based on ${g.evidence.length} record${g.evidence.length > 1 ? "s" : ""}` : ""}</small>
          )}
        </li>
      ))}
    </ul>
  );
}

function Ask({ eid, role }: { eid: string; role: string }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    api<Answer>(`/api/patients/${eid}/ask`, { question: q })
      .then(setAnswer, (err: Error) => setError(err.message))
      .finally(() => setBusy(false));
  };
  return (
    <Section title={role === "patient" ? "Ask about your record" : "Ask about this record"}>
      <form className="ask" onSubmit={submit}>
        <input value={q} onChange={(e) => setQ(e.target.value)} minLength={3} required
          placeholder={role === "patient" ? "What was my last A1c?" : "What is the latest HbA1c?"} aria-label="Question" />
        <button disabled={busy}>{busy ? "Searching…" : "Ask"}</button>
      </form>
      {error && <p className="error">{error}</p>}
      {answer && (
        <div className="answer">
          <p>{answer.answer}</p>
          {answer.citations.length > 0 && (
            <>
              <h3>Sources in the record</h3>
              <ol>{answer.citations.map((c) => <li key={c.id}>{c.text}</li>)}</ol>
              <small>Answered by <code>{answer.model}</code> with prompt <code>{answer.prompt}</code>. Answers quote the record; they are not medical advice.</small>
            </>
          )}
        </div>
      )}
    </Section>
  );
}

export function PatientView({ eid, role, onBack }: { eid: string; role: string; onBack?: () => void }) {
  const { data: r, error } = useLoad<PatientRecord>(`/api/patients/${eid}`);
  if (error) return <p className="error">{error}</p>;
  if (!r) return <p className="empty">Loading record…</p>;
  const d = r.demographics;
  const facts = r.note_facts.flatMap((n) =>
    Object.entries(n.facts).flatMap(([kind, list]) => list.map((f) => ({ kind, f, n }))));

  return (
    <div className="stack">
      {onBack && <button className="link back" onClick={onBack}>← All patients</button>}
      <div className="patient-head">
        <h1>{role === "patient" ? `Hello, ${d.name.split(" ")[0]}` : d.name}</h1>
        <p>{[d.age && `${d.age} years`, d.sex, d.city && `${d.city}, ${d.state}`, d.birth_date && `born ${d.birth_date}`].filter(Boolean).join(" · ")}</p>
        <p className="muted">
          Record assembled from {r.sources.filter((s) => SOURCE_LABEL[s]).length} sources you are permitted to see.
          {r.restricted_sources?.length ? " Includes records from a confidential program that only you, and providers you consent to, can see." : ""}
        </p>
      </div>

      <div className="two-col">
        <Section title={role === "patient" ? "For you to follow up" : "Care gaps and insights"} count={r.care_gaps.length}>
          <Gaps gaps={r.care_gaps} role={role} />
        </Section>
        <Ask eid={eid} role={role} />
      </div>

      <div className="two-col">
        <Section title="Conditions" count={r.conditions.length}>
          <table><tbody>
            {r.conditions.map((c) => (
              <tr key={c.resource_id}><td>{c.display}</td><td><code>{c.code}</code></td><td><Source eid={eid} item={c} /></td></tr>
            ))}
          </tbody></table>
          {!r.conditions.length && <p className="empty">None recorded.</p>}
        </Section>
        <Section title="Medications" count={r.medications.length}>
          <table><tbody>
            {r.medications.map((m) => (
              <tr key={m.resource_id}><td>{m.display}</td><td>{m.status}</td><td><Source eid={eid} item={m} /></td></tr>
            ))}
          </tbody></table>
          {!r.medications.length && <p className="empty">None recorded.</p>}
        </Section>
      </div>

      <Section title="Results and vitals" count={r.observations.length}>
        <table>
          <thead><tr><th>Date</th><th>Test</th><th>Value</th><th>Source</th></tr></thead>
          <tbody>
            {r.observations.map((o) => (
              <tr key={o.resource_id}><td>{o.date}</td><td>{o.display}</td><td className="num">{o.value}</td><td><Source eid={eid} item={o} /></td></tr>
            ))}
          </tbody>
        </table>
        {!r.observations.length && <p className="empty">None recorded.</p>}
      </Section>

      {facts.length > 0 && (
        <Section title="Found in clinical notes" count={facts.length}>
          <p className="muted">Extracted by <code>{r.note_facts[0].model}</code> using prompt <code>{r.note_facts[0].prompt}</code>. Each fact is kept only if its quote appears word for word in the note.</p>
          <table>
            <thead><tr><th>Kind</th><th>Fact</th><th>Quote from the note</th></tr></thead>
            <tbody>
              {facts.map(({ kind, f }, i) => (
                <tr key={i}>
                  <td>{kind}</td>
                  <td>{Object.entries(f).filter(([k]) => k !== "evidence").map(([, v]) => String(v)).join(" · ")}</td>
                  <td className="quote">“{String(f.evidence)}”</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      <div className="two-col">
        <Section title="Hospital and emergency visits" count={r.encounters.length}>
          <table><tbody>
            {r.encounters.map((e) => (
              <tr key={e.resource_id}><td>{e.start?.slice(0, 10)}</td><td>{e.class === "EMER" ? "Emergency" : "Inpatient"}</td><td>{e.reason}</td><td><Source eid={eid} item={e} /></td></tr>
            ))}
          </tbody></table>
          {!r.encounters.length && <p className="empty">None this year.</p>}
        </Section>
        <Section title="Claims" count={r.claims.length}>
          <table><tbody>
            {r.claims.map((c) => (
              <tr key={c.resource_id}><td>{c.service_date}</td><td>{c.procedures.join(", ")}</td><td className="num">{usd(c.total)}</td><td><Source eid={eid} item={c} /></td></tr>
            ))}
          </tbody></table>
          {!r.claims.length && <p className="empty">No claims visible.</p>}
        </Section>
      </div>

      {r.documents.some((doc) => doc.text) && (
        <Section title="Clinical notes" count={r.documents.length}>
          {r.documents.map((doc) => (
            <details key={doc.resource_id}>
              <summary>{doc.type} · {doc.date}</summary>
              <pre>{doc.text}</pre>
            </details>
          ))}
        </Section>
      )}
    </div>
  );
}
