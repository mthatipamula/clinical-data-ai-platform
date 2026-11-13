// Thin client for the platform API. The demo user header stands in for real sign-in during
// local development only; the server refuses it in production.

let currentUser = "";

export function setUser(id: string) {
  currentUser = id;
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Demo-User": currentUser, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j.detail, () => res.statusText);
    throw new Error(typeof detail === "string" ? detail : `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export interface DemoUser { id: string; name: string; role: string }
export interface Me extends DemoUser {
  tenant: string | null; can_approve: boolean; purpose_of_use: string | null; own_eid: string | null;
}
export interface Demographics {
  eid: string; name: string; birth_date?: string; age?: number; sex?: string; city?: string; state?: string;
}
export interface PatientRow extends Demographics { open_gaps: number; high_severity_gaps: number }
export interface CareGap {
  rule_id: string; title: string; severity: "high" | "medium" | "low";
  recommendation?: string; patient_text?: string; rule_version?: number; evidence?: string[];
}
export interface Sourced { resource_id: string; source: string }
export interface PatientRecord {
  demographics: Demographics;
  conditions: (Sourced & { code: string; display: string; onset?: string })[];
  observations: (Sourced & { display: string; value: string; date: string })[];
  medications: (Sourced & { display: string; authored?: string; status?: string })[];
  encounters: (Sourced & { class: string; start?: string; end?: string; reason: string })[];
  claims: (Sourced & { service_date: string; total: number; diagnoses: string[]; procedures: string[] })[];
  documents: (Sourced & { date: string; type: string; text?: string })[];
  note_facts: { document_id: string; model: string; prompt: string; facts: Record<string, Record<string, unknown>[]> }[];
  measures: { measure_id: string; name: string; met: boolean; detail: string; version: string }[];
  care_gaps: CareGap[];
  sources: string[];
  restricted_sources?: string[];
}
export interface Lineage { resource_id: string; resource_type: string; steps: Record<string, string>[] }
export interface Answer { answer: string; citations: { id: string; text: string; resource_id: string }[]; model?: string; prompt?: string }
export interface MeasureRate {
  measure_id: string; name: string; inverse: boolean; denominator: number; numerator: number; rate: number | null;
}
export interface Cost {
  total_billed: number; members: number; by_month: { month: string; billed: number }[];
  top_members: { eid: string; name: string; billed: number; ed_visits: number }[];
}
export interface Action {
  id: string; tool: string; args: Record<string, unknown>; why: string | null; risk: string; status: string;
  requested_by: string; decided_by: string | null; result: Record<string, unknown> | null;
}
export interface Incident {
  id: string; created_at: string; status: string;
  alert: { type: string; resource: string; severity: string; description: string };
  triage: { summary: string; hypothesis: string; runbook_id: string | null; model: string; prompt: string; requested_by: string } | null;
  actions: Action[];
}
export interface OpsState { incidents: Incident[]; metrics: Record<string, number>; platform: Record<string, unknown> }
export interface AuditEntry {
  seq: number; ts: string; actor: string; actor_type: string; action: string; resource: string;
  purpose: string | null; outcome: string; hash: string;
}
export interface AuditLog { chain: { valid: boolean; entries: number; broken_at_seq?: number }; entries: AuditEntry[] }
export interface Evidence {
  generated_at: string; summary: Record<string, number>;
  controls: { id: string; title: string; status: "pass" | "fail"; evidence: unknown }[];
}

export const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
