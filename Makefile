.PHONY: help pipeline api web test test-go test-java test-python test-web eval evidence tf-validate clean

help:
	@echo "make pipeline     generate synthetic data and run it through every stage"
	@echo "make api          start the API on :8000"
	@echo "make web          start the web app on :5173"
	@echo "make test         run all test suites"
	@echo "make eval         LLM extraction release gate (golden set)"
	@echo "make evidence     write the audit evidence bundle to evidence.json"
	@echo "make tf-validate  format-check and validate Terraform"

pipeline:
	./scripts/run-pipeline.sh

api:
	cd services/api && uv run uvicorn cdap.api:app --port 8000

web:
	cd web && npm install --silent && npm run dev

test: test-go test-java test-python test-web

test-go:
	cd services/gateway && go vet ./... && go test ./...

test-java:
	cd pipelines/harmonizer && ./gradlew -q test

test-python:
	cd services/api && uv run -q python -m pytest -q

test-web:
	cd web && npm install --silent && npm run build

eval:
	cd services/api && uv run -q python -m cdap.cli eval --min 0.9

evidence:
	cd services/api && uv run -q python -m cdap.cli evidence ../../evidence.json

tf-validate:
	cd infra/terraform && terraform fmt -check -recursive && terraform init -backend=false -input=false > /dev/null && terraform validate

clean:
	rm -rf var bin data/synthetic evidence.json web/dist pipelines/harmonizer/build
