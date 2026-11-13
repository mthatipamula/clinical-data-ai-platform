"""Batch jobs and release gates.

    python -m cdap.cli load var/harmonized     load harmonizer output into the data product store
    python -m cdap.cli extract                 run LLM extraction over clinical notes
    python -m cdap.cli rules                   evaluate care-gap rules
    python -m cdap.cli eval [--min 0.9]        score extraction on the golden set; non-zero exit below the bar
    python -m cdap.cli identity-eval           score identity resolution against ground truth
    python -m cdap.cli evidence out.json       write the audit evidence bundle
"""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations

from . import clinical, evidence
from .access import Policy
from .llm import Gateway
from .store import ROOT, Store


def identity_eval(store: Store) -> dict:
    """Pairwise precision and recall of record linkage against the generator's ground truth."""
    truth = json.loads((ROOT / "data" / "synthetic" / "identity_truth.json").read_text())
    eid_of: dict[tuple[str, str], str] = {}
    for r in store.q("SELECT eid, body FROM resources WHERE resource_type='Patient'"):
        for ident in json.loads(r["body"]).get("identifier", []):
            eid_of[(ident.get("system"), ident.get("value"))] = r["eid"]
    recs = [(t["person"], eid_of.get((t["system"], t["id"]))) for t in truth]
    missing = sum(1 for _, e in recs if e is None)
    tp = fp = fn = 0
    for (pa, ea), (pb, eb) in combinations([r for r in recs if r[1]], 2):
        same_truth, same_pred = pa == pb, ea == eb
        tp += same_truth and same_pred
        fp += (not same_truth) and same_pred
        fn += same_truth and not same_pred
    return {"source_records": len(recs), "unresolved": missing, "people_truth": len({p for p, _ in recs}),
            "people_resolved": len({e for _, e in recs if e}),
            "pairwise_precision": round(tp / (tp + fp), 4) if tp + fp else 1.0,
            "pairwise_recall": round(tp / (tp + fn), 4) if tp + fn else 1.0,
            "false_merges": fp, "missed_links": fn}


def main() -> None:
    ap = argparse.ArgumentParser(prog="cdap")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("load").add_argument("directory")
    sub.add_parser("extract")
    sub.add_parser("rules")
    sub.add_parser("eval").add_argument("--min", type=float, default=0.9)
    sub.add_parser("identity-eval")
    sub.add_parser("evidence").add_argument("path")
    args = ap.parse_args()

    store = Store()
    if args.cmd == "load":
        out = store.load_harmonized(args.directory)
    elif args.cmd == "extract":
        out = clinical.extract_documents(store, Gateway(store))
    elif args.cmd == "rules":
        permitted = {s for s, cfg in Policy(store).sources.items() if "operations" in cfg["permitted_purposes"]}
        out = clinical.run_rules(store, permitted)
    elif args.cmd == "eval":
        out = clinical.evaluate_extraction(Gateway(store))
        print(json.dumps(out, indent=1))
        if out["min_score"] < args.min:
            sys.exit(f"extraction eval below the bar: {out['min_score']} < {args.min}")
        return
    elif args.cmd == "identity-eval":
        out = identity_eval(store)
        print(json.dumps(out, indent=1))
        if out["false_merges"]:
            sys.exit("identity resolution merged records of different people")
        return
    else:
        out = evidence.write(store, Gateway(store), args.path)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
