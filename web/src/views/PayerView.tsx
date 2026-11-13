import { useState } from "react";
import { type Cost, type MeasureRate, type PatientRow, usd } from "../api";
import { PatientView, Section, useLoad } from "./PatientView";
import { Measures, PatientTable } from "./ProviderView";

export function PayerView() {
  const [eid, setEid] = useState<string | null>(null);
  const measures = useLoad<MeasureRate[]>("/api/population/measures");
  const cost = useLoad<Cost>("/api/population/cost");
  const members = useLoad<PatientRow[]>("/api/patients");
  if (eid) return <PatientView eid={eid} role="payer" onBack={() => setEid(null)} />;
  const months = cost.data?.by_month.filter((m) => m.month >= "2026-01") ?? [];
  const peak = Math.max(1, ...months.map((m) => m.billed));
  return (
    <div className="stack">
      <div className="patient-head">
        <h1>Member population</h1>
        <p className="muted">Quality and cost for your plan's members. Access is logged under purpose of use: health care operations. Records from consent-restricted programs are never included.</p>
      </div>
      <div className="two-col">
        <Section title="Quality measures">
          {measures.data ? <Measures rates={measures.data} /> : <p className="empty">Loading…</p>}
        </Section>
        <Section title="Billed charges, 2026">
          {cost.data && (
            <>
              <p className="stat">{usd(cost.data.total_billed)} <small>across {cost.data.members} members</small></p>
              <div className="months" role="img" aria-label="Billed charges by month">
                {months.map((m) => (
                  <div key={m.month} title={`${m.month}: ${usd(m.billed)}`}>
                    <span style={{ height: `${(m.billed / peak) * 100}%` }} />
                    <small>{m.month.slice(5)}</small>
                  </div>
                ))}
              </div>
            </>
          )}
        </Section>
      </div>
      <Section title="Highest-cost members">
        <table className="rows">
          <thead><tr><th>Member</th><th className="num">Billed</th><th className="num">Emergency visits</th></tr></thead>
          <tbody>
            {cost.data?.top_members.map((m) => (
              <tr key={m.eid} onClick={() => setEid(m.eid)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setEid(m.eid)}>
                <td><strong>{m.name}</strong></td>
                <td className="num">{usd(m.billed)}</td>
                <td className="num">{m.ed_visits >= 3 ? <span className="chip high">{m.ed_visits}</span> : m.ed_visits || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
      <Section title="All members" count={members.data?.length}>
        {members.data && <PatientTable rows={members.data} onOpen={setEid} />}
      </Section>
    </div>
  );
}
