#!/usr/bin/env bash
# End-to-end local run: generate synthetic data, ingest it through the Go gateway, harmonize it
# with the Java pipeline, then load it and run extraction and rules in Python.
set -euo pipefail
cd "$(dirname "$0")/.."

GATEWAY_PORT="${GATEWAY_PORT:-8081}"
export GATEWAY_ADDR=":${GATEWAY_PORT}"

echo "==> build"
mkdir -p bin var
(cd services/gateway && go build -o ../../bin/gateway ./cmd/gateway)
(cd pipelines/harmonizer && ./gradlew -q installDist)
(cd services/api && uv sync -q)

echo "==> reset local state"
rm -rf var/raw var/mpi var/harmonized var/cdap.db

echo "==> synthetic data"
python3 tools/synth/generate.py --out data/synthetic

echo "==> ingest gateway"
./bin/gateway > var/gateway.log 2>&1 &
GATEWAY_PID=$!
trap 'kill "$GATEWAY_PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  curl -sf "http://localhost:${GATEWAY_PORT}/healthz" > /dev/null && break
  sleep 0.1
done
python3 tools/synth/post.py --data data/synthetic --gateway "http://localhost:${GATEWAY_PORT}"

echo "==> harmonize (Apache Beam, direct runner)"
pipelines/harmonizer/build/install/harmonizer/bin/harmonizer \
  --input='var/raw/*/*.ndjson' --output=var/harmonized \
  --identityUrl="http://localhost:${GATEWAY_PORT}" --sourcesFile=config/sources.json 2>&1 \
  | grep -v "Failed to match temporary files" || true
echo "dead letters: $(cat var/harmonized/deadletter-*.ndjson | wc -l | tr -d ' ')"
echo "identity: $(curl -s "http://localhost:${GATEWAY_PORT}/v1/identity/stats")"

cdap() { (cd services/api && uv run -q python -m cdap.cli "$@"); }
echo "==> load data products";            cdap load ../../var/harmonized
echo "==> identity resolution vs truth";  cdap identity-eval
echo "==> extract facts from notes";      cdap extract
echo "==> care-gap rules";                cdap rules
echo
echo "Done. Start the app with:  make api   (one terminal)   and   make web   (another)"
