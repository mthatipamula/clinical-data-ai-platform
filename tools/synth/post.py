#!/usr/bin/env python3
"""Send every synthetic file to the ingest gateway, as the source systems would.

    python3 tools/synth/post.py --data data/synthetic --gateway http://localhost:8081
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from collections import Counter
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/synthetic")
    ap.add_argument("--gateway", default="http://localhost:8081")
    args = ap.parse_args()

    tally: Counter[str] = Counter()
    for source_dir in sorted(p for p in Path(args.data).iterdir() if p.is_dir()):
        for f in sorted(source_dir.iterdir()):
            req = urllib.request.Request(f"{args.gateway}/v1/ingest/{source_dir.name}", data=f.read_bytes(), method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                receipt = json.load(resp)
            tally[f"{source_dir.name}:{'duplicate' if receipt['duplicate'] else 'accepted'}"] += 1
    print(json.dumps(dict(sorted(tally.items())), indent=1))


if __name__ == "__main__":
    main()
