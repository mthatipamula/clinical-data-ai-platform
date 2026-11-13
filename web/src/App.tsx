import { useEffect, useState } from "react";
import { api, type DemoUser, type Me, setUser } from "./api";
import { ComplianceView } from "./views/ComplianceView";
import { OpsView } from "./views/OpsView";
import { PatientView } from "./views/PatientView";
import { PayerView } from "./views/PayerView";
import { ProviderView } from "./views/ProviderView";

const ROLE_LABEL: Record<string, string> = {
  patient: "Patient", provider: "Provider", payer: "Health plan", operator: "Platform operations", auditor: "Compliance",
};

export function App() {
  const [users, setUsers] = useState<DemoUser[]>([]);
  const [userId, setUserId] = useState(() => localStorage.getItem("cdap-user") ?? "dr-okafor");
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<DemoUser[]>("/api/demo-users").then(setUsers, (e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    setUser(userId);
    setMe(null);
    localStorage.setItem("cdap-user", userId);
    api<Me>("/api/me").then(setMe, (e: Error) => setError(e.message));
  }, [userId]);

  return (
    <>
      <header className="topbar">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          Clinical Data and AI Platform
          <span className="tag">synthetic data</span>
        </div>
        <label className="switcher">
          <span>Viewing as</span>
          <select value={userId} onChange={(e) => setUserId(e.target.value)}>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.name} — {ROLE_LABEL[u.role] ?? u.role}</option>
            ))}
          </select>
        </label>
      </header>
      <main>
        {error && <p className="error">Could not reach the API: {error}</p>}
        {me && (
          <div key={me.id}>
            {me.role === "patient" && (me.own_eid
              ? <PatientView eid={me.own_eid} role="patient" />
              : <p className="empty">No record is linked to this sign-in yet.</p>)}
            {me.role === "provider" && <ProviderView />}
            {me.role === "payer" && <PayerView />}
            {me.role === "operator" && <OpsView me={me} />}
            {me.role === "auditor" && <ComplianceView />}
          </div>
        )}
      </main>
    </>
  );
}
