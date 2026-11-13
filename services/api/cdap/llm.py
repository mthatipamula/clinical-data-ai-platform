"""LLM access with governance built into the only call path.

There is exactly one way to call a model: ``Gateway.run(prompt_id, variables)``. It

* loads the prompt from the versioned registry (``prompts/*.yaml``) and hashes it,
* checks the configured model against the allowlist for that prompt's task,
* calls the provider,
* records which prompt version and model produced the output (hashes only, never content).

Providers: ``offline`` (deterministic, no network; the default), ``vertex-claude`` and
``vertex-gemini`` (Vertex AI; selected with CDAP_LLM_PROVIDER).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import yaml

from .store import ROOT, Store, now, sha

API_DIR = Path(__file__).resolve().parents[1]


class GovernanceError(Exception):
    pass


class ModelRefusal(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    task: str
    owner: str
    approved_by: str
    system: str
    user: str

    @property
    def sha(self) -> str:
        return sha(json.dumps([self.id, self.version, self.system, self.user]))

    def render(self, variables: dict[str, str]) -> str:
        text = self.user
        for k, v in variables.items():
            text = text.replace("{" + k + "}", v)
        return text


def load_prompts(directory: Path | None = None) -> dict[str, Prompt]:
    out = {}
    for f in sorted((directory or API_DIR / "prompts").glob("*.yaml")):
        d = yaml.safe_load(f.read_text())
        out[d["id"]] = Prompt(**{k: d[k] for k in ("id", "version", "task", "owner", "approved_by", "system", "user")})
    return out


def load_allowlist() -> list[dict]:
    return json.loads((ROOT / "config" / "models.json").read_text())["models"]


class Provider(Protocol):
    name: str
    model: str

    def complete(self, task: str, system: str, user: str) -> str: ...


# --------------------------------------------------------------------- offline

def _between(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", text, re.S)
    return m.group(1) if m else ""


class OfflineProvider:
    """Deterministic stand-in for a model. Pattern rules, no network.

    It exists so the whole platform runs and tests without credentials. Its extraction rules were
    written against the synthetic note templates, so its eval score says the plumbing works; it
    says nothing about how a real model performs on real notes.
    """
    name = "offline"
    model = "deterministic-rules-v1"

    def complete(self, task: str, system: str, user: str) -> str:
        return json.dumps(getattr(self, f"_{task}")(user))

    def _extract_clinical_facts(self, user: str) -> dict:
        note = _between(user, "note")
        out: dict = {"conditions": [], "medications": [], "labs": [], "social": [], "screenings": []}
        for line in (ln.strip() for ln in note.splitlines()):
            if re.search(r"type 2 diabetes", line, re.I):
                out["conditions"].append({"code": "E11.9", "name": "type 2 diabetes mellitus", "evidence": line})
            if re.search(r"\bhypertension\b", line, re.I):
                out["conditions"].append({"code": "I10", "name": "hypertension", "evidence": line})
            for m in re.finditer(r"HbA1c (\d+(?:\.\d+)?)% on (\d{4}-\d{2}-\d{2})", line):
                out["labs"].append({"test": "hba1c", "value": float(m.group(1)), "unit": "%",
                                    "date": m.group(2), "evidence": m.group(0)})
            for m in re.finditer(r"(?:Continue|Increase|Start) (\w+) (?:to )?(\d+ mg) (twice daily|daily)", line):
                out["medications"].append({"name": m.group(1).lower(), "dose": m.group(2),
                                           "frequency": m.group(3), "evidence": m.group(0)})
            if re.search(r"reports smoking", line, re.I):
                out["social"].append({"type": "tobacco_use", "status": "current", "evidence": line})
            elif re.search(r"denies tobacco", line, re.I):
                out["social"].append({"type": "tobacco_use", "status": "never_or_former", "evidence": line})
            if re.search(r"mammogram discussed; patient declined", line, re.I):
                out["screenings"].append({"type": "mammography", "status": "declined", "evidence": line})
        return out

    def _answer_patient_question(self, user: str) -> dict:
        passages = re.findall(r"\[(P\d+)\] (.*)", _between(user, "context"))
        if not passages:
            return {"answer": "I could not find anything in the record that answers this.", "citations": []}
        top = passages[:3]
        return {"answer": "From the record: " + " ".join(text.rstrip(".") + "." for _, text in top),
                "citations": [pid for pid, _ in top]}

    def _triage_incident(self, user: str) -> dict:
        alert = json.loads(_between(user, "alert") or "{}")
        runbooks = json.loads(_between(user, "runbooks") or "[]")
        match = next((r for r in runbooks if r.get("alert_type") == alert.get("type")), None)
        return {"runbook_id": match["id"] if match else None,
                "severity": alert.get("severity", "medium"),
                "summary": f"{alert.get('type', 'unknown alert')} on {alert.get('resource', 'unknown resource')}",
                "hypothesis": match["hypothesis"] if match else "No runbook matches this alert; a human should triage."}


# ---------------------------------------------------------------------- Vertex

class VertexClaudeProvider:
    """Claude on Vertex AI. Authenticates with Application Default Credentials."""
    name = "vertex-claude"

    def __init__(self, model: str, project: str, region: str):
        from anthropic import AnthropicVertex  # optional dependency: cdap-api[vertex]
        self.model = model
        self._client = AnthropicVertex(project_id=project, region=region)

    def complete(self, task: str, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self.model, max_tokens=16000, system=system,
            messages=[{"role": "user", "content": user}])
        if resp.stop_reason == "refusal":
            raise ModelRefusal(f"model declined the request for task {task}")
        return "".join(b.text for b in resp.content if b.type == "text")


class VertexGeminiProvider:
    """Gemini on Vertex AI. Authenticates with Application Default Credentials."""
    name = "vertex-gemini"

    def __init__(self, model: str, project: str, region: str):
        from google import genai  # optional dependency: cdap-api[vertex]
        from google.genai import types
        self.model = model
        self._types = types
        self._client = genai.Client(vertexai=True, project=project, location=region)

    def complete(self, task: str, system: str, user: str) -> str:
        resp = self._client.models.generate_content(
            model=self.model, contents=user,
            config=self._types.GenerateContentConfig(system_instruction=system,
                                                     response_mime_type="application/json"))
        return resp.text or ""


def provider_from_env() -> Provider:
    kind = os.environ.get("CDAP_LLM_PROVIDER", "offline")
    if kind == "offline":
        return OfflineProvider()
    project = os.environ["GCP_PROJECT"]
    region = os.environ.get("VERTEX_REGION", "global")
    if kind == "vertex-claude":
        return VertexClaudeProvider(os.environ.get("CDAP_LLM_MODEL", "claude-opus-5-5"), project, region)
    if kind == "vertex-gemini":
        return VertexGeminiProvider(os.environ["CDAP_LLM_MODEL"], project, region)
    raise GovernanceError(f"unknown LLM provider: {kind}")


# --------------------------------------------------------------------- gateway

def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in a code fence. Accept that; reject anything else."""
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    obj = json.loads(m.group(1) if m else text)
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


class Gateway:
    def __init__(self, store: Store, provider: Provider | None = None, require_approval: bool | None = None):
        self.store = store
        self.provider = provider or provider_from_env()
        self.prompts = load_prompts()
        self.allowlist = load_allowlist()
        # Outside production an unapproved model may run so it can be evaluated. In production
        # a model must carry a named approval.
        self.require_approval = (os.environ.get("CDAP_ENV") == "prod") if require_approval is None else require_approval

    def _check(self, task: str) -> None:
        for m in self.allowlist:
            if m["provider"] == self.provider.name and m["model"] == self.provider.model:
                if task not in m["approved_tasks"]:
                    break
                if self.require_approval and m["approved_by"] in (None, "PENDING"):
                    raise GovernanceError(f"{self.provider.model} is listed but not yet approved")
                return
        raise GovernanceError(f"{self.provider.name}/{self.provider.model} is not on the allowlist for task {task}")

    def run(self, prompt_id: str, variables: dict[str, str]) -> tuple[dict, Prompt]:
        prompt = self.prompts[prompt_id]
        self._check(prompt.task)
        user = prompt.render(variables)
        start = time.monotonic()
        raw = self.provider.complete(prompt.task, prompt.system, user)
        ms = int((time.monotonic() - start) * 1000)
        self.store.x(
            "INSERT INTO llm_calls (ts, task, prompt_id, prompt_version, prompt_sha, provider, model, input_sha,"
            " output_sha, latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (now(), prompt.task, prompt.id, prompt.version, prompt.sha, self.provider.name, self.provider.model,
             sha(user), sha(raw), ms))
        return parse_json(raw), prompt
