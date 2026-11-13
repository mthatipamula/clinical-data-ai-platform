import { useState } from "react";
import { type Action, api, type AuditLog, type Incident, type Me, type OpsState } from "../api";
import { Section, useLoad } from "./PatientView";

const ALERTS = [
  { label: "Ingest backlog growing", type: "pubsub_backlog_growing", resource: "subscription/raw-ingest-harmonizer", severity: "high" },
  { label: "Harmonizer error rate", type: "dataflow_error_rate", resource: "dataflow/harmonizer", severity: "high" },
  { label: "BigQuery spend anomaly", type: "bq_spend_anomaly", resource: "bigquery/harmonized", severity: "medium" },
  { label: "Alert with no runbook", type: "certificate_expiring", resource: "gateway/tls", severity: "low" },
];

const STATUS: Record<string, string> = {
  executed: "Ran", pending_approval: "Awaiting approval", rejected: "Rejected", denied_not_permitted: "Refused: not permitted",
};

export function AuditTable({ log }: { log: AuditLog }) {
  return (
    <>
      <p className={log.chain.valid ? "ok-line" : "error"}>
        {log.chain.valid
          ? `Hash chain verified across ${log.chain.entries} entries.`
          : `Hash chain is broken at entry ${log.chain.broken_at_seq}. The log has been altered.`}
      </p>
      <div className="scroll">
        <table>
          <thead><tr><th>#</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Resource</th><th>Purpose</th><th>Outcome</th></tr></thead>
          <tbody>
            {log.entries.map((e) => (
              <tr key={e.seq}>
                <td className="num">{e.seq}</td><td>{e.ts.replace("T", " ").replace("Z", "")}</td>
                <td>{e.actor} {e.actor_type !== "user" && <span className="chip">{e.actor_type}</span>}</td>
                <td><code>{e.action}</code></td><td><code>{e.resource.length > 30 ? e.resource.slice(0, 28) + "…" : e.resource}</code></td>
                <td>{e.purpose ?? "—"}</td>
                <td><span className={`chip ${e.outcome === "deny" || e.outcome === "denied" ? "high" : ""}`}>{e.outcome}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ActionRow({ a, me, onDone }: { a: Action; me: Me; onDone: (i: Incident) => void }) {
  const [error, setError] = useState("");
  const decide = (approve: boolean) =>
    api<Incident>(`/api/ops/actions/${a.id}/decision`, { approve }).then(onDone, (e: Error) => setError(e.message));
  return (
    <tr>
      <td><code>{a.tool}</code><br /><small>{a.why}</small></td>
      <td><span className={`chip ${a.risk === "low" ? "" : a.risk}`}>{a.risk}</span></td>
      <td>
        <span className={`chip status-${a.status}`}>{STATUS[a.status] ?? a.status}</span>
        {a.decided_by && a.decided_by !== "policy" && <small> by {a.decided_by}</small>}
        {a.status === "pending_approval" && (
          <div className="decide">
            <button onClick={() => decide(true)}>Approve</button>
            <button className="secondary" onClick={() => decide(false)}>Reject</button>
            {!me.can_approve && <small>Needs someone with approval rights.</small>}
          </div>
        )}
        {error && <p className="error">{error}</p>}
      </td>
      <td><small>{a.result ? JSON.stringify(a.result) : "—"}</small></td>
    </tr>
  );
}

export function OpsView({ me }: { me: Me }) {
  const ops = useLoad<OpsState>("/api/ops/incidents");
  const audit = useLoad<AuditLog>("/api/audit?limit=40", [ops.data]);
  const [error, setError] = useState("");
  const raise = (a: (typeof ALERTS)[number]) =>
    api<Incident>("/api/ops/incidents", { type: a.type, resource: a.resource, severity: a.severity, description: a.label })
      .then((inc) => api<Incident>(`/api/ops/incidents/${inc.id}/triage`, {}))
      .then(ops.reload, (e: Error) => setError(e.message));
  const m = ops.data?.metrics;
  return (
    <div className="stack">
      <div className="patient-head">
        <h1>Operations console</h1>
        <p className="muted">An AI agent triages alerts and gathers diagnostics on its own. Anything that changes the platform waits for a person with approval rights, who cannot be the person that started the triage. Platform state here is simulated.</p>
      </div>
      <Section title="Simulate an alert">
        <div className="buttons">
          {ALERTS.map((a) => <button key={a.type} className="secondary" onClick={() => raise(a)}>{a.label}</button>)}
        </div>
        {error && <p className="error">{error}</p>}
        {m && (
          <dl className="metrics">
            <div><dt>Incidents</dt><dd>{m.incidents}</dd></div>
            <div><dt>Ran automatically</dt><dd>{m.auto_executed}</dd></div>
            <div><dt>Approved by a person</dt><dd>{m.human_approved}</dd></div>
            <div><dt>Awaiting approval</dt><dd>{m.awaiting_approval}</dd></div>
            <div><dt>Refused by least privilege</dt><dd>{m.denied_by_least_privilege}</dd></div>
          </dl>
        )}
      </Section>
      {ops.data?.incidents.map((inc) => (
        <Section key={inc.id} title={inc.alert.description || inc.alert.type}>
          <p>
            <span className={`chip status-${inc.status}`}>{inc.status.replace("_", " ")}</span>{" "}
            <code>{inc.id}</code> · {inc.alert.resource} · requested by {inc.triage?.requested_by}
          </p>
          {inc.triage && (
            <p className="hypothesis">
              <strong>Agent's assessment.</strong> {inc.triage.hypothesis}{" "}
              <small>({inc.triage.runbook_id ?? "no runbook"} · <code>{inc.triage.model}</code> · <code>{inc.triage.prompt}</code>)</small>
            </p>
          )}
          {inc.actions.length > 0 && (
            <table>
              <thead><tr><th>Step</th><th>Risk</th><th>Status</th><th>Result</th></tr></thead>
              <tbody>{inc.actions.map((a) => <ActionRow key={a.id} a={a} me={me} onDone={ops.reload} />)}</tbody>
            </table>
          )}
        </Section>
      ))}
      <Section title="Audit trail">{audit.data && <AuditTable log={audit.data} />}</Section>
    </div>
  );
}
