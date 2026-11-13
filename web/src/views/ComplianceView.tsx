import type { AuditLog, Evidence } from "../api";
import { AuditTable } from "./OpsView";
import { Section, useLoad } from "./PatientView";

export function ComplianceView() {
  const ev = useLoad<Evidence>("/api/compliance/evidence");
  const audit = useLoad<AuditLog>("/api/audit?limit=60", [ev.data]);
  return (
    <div className="stack">
      <div className="patient-head">
        <h1>Controls and evidence</h1>
        <p className="muted">Each control is checked against live platform state when this page loads. Expand one to see the evidence behind it. You cannot see patient data from this role.</p>
      </div>
      <Section title="Controls" count={ev.data?.controls.length}>
        {ev.error && <p className="error">{ev.error}</p>}
        {ev.data && (
          <>
            <p className="muted">Collected {ev.data.generated_at.replace("T", " ").replace("Z", " UTC")} · {ev.data.summary.pass ?? 0} passing, {ev.data.summary.fail ?? 0} failing</p>
            {ev.data.controls.map((c) => (
              <details key={c.id} className="control">
                <summary>
                  <span className={`chip ${c.status === "pass" ? "ok" : "high"}`}>{c.status}</span>
                  <code>{c.id}</code> {c.title}
                </summary>
                <pre>{JSON.stringify(c.evidence, null, 2)}</pre>
              </details>
            ))}
          </>
        )}
      </Section>
      <Section title="Audit trail">{audit.data && <AuditTable log={audit.data} />}</Section>
    </div>
  );
}
