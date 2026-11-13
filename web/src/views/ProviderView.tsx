import { useState } from "react";
import type { MeasureRate, PatientRow } from "../api";
import { PatientView, Section, useLoad } from "./PatientView";

export function Measures({ rates }: { rates: MeasureRate[] }) {
  return (
    <ul className="measures">
      {rates.map((m) => {
        const pct = Math.round((m.rate ?? 0) * 100);
        return (
          <li key={m.measure_id}>
            <div className="measure-head">
              <span>{m.name}</span>
              <strong>{pct}%</strong>
            </div>
            <div className="bar" role="img" aria-label={`${pct} percent`}>
              <span className={m.inverse ? "inverse" : ""} style={{ width: `${pct}%` }} />
            </div>
            <small>{m.numerator} of {m.denominator} eligible patients{m.inverse ? " (a lower rate is better)" : ""}</small>
          </li>
        );
      })}
    </ul>
  );
}

export function PatientTable({ rows, onOpen }: { rows: PatientRow[]; onOpen: (eid: string) => void }) {
  return (
    <table className="rows">
      <thead><tr><th>Patient</th><th>Age</th><th>City</th><th className="num">Open gaps</th><th className="num">High priority</th></tr></thead>
      <tbody>
        {rows.map((p) => (
          <tr key={p.eid} onClick={() => onOpen(p.eid)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onOpen(p.eid)}>
            <td><strong>{p.name}</strong></td>
            <td>{p.age}</td>
            <td>{p.city}{p.state ? `, ${p.state}` : ""}</td>
            <td className="num">{p.open_gaps || "—"}</td>
            <td className="num">{p.high_severity_gaps ? <span className="chip high">{p.high_severity_gaps}</span> : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ProviderView() {
  const [eid, setEid] = useState<string | null>(null);
  const patients = useLoad<PatientRow[]>("/api/patients");
  const measures = useLoad<MeasureRate[]>("/api/population/measures");
  if (eid) return <PatientView eid={eid} role="provider" onBack={() => setEid(null)} />;
  return (
    <div className="stack">
      <div className="patient-head">
        <h1>Your patients</h1>
        <p className="muted">Patients your organization treats, ordered by what most needs attention. Access is logged under purpose of use: treatment.</p>
      </div>
      <Section title="Quality across your panel">
        {measures.data ? <Measures rates={measures.data} /> : <p className="empty">Loading…</p>}
      </Section>
      <Section title="Patients" count={patients.data?.length}>
        {patients.error && <p className="error">{patients.error}</p>}
        {patients.data && <PatientTable rows={patients.data} onOpen={setEid} />}
      </Section>
    </div>
  );
}
