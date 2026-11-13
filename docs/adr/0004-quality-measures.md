# ADR 0004: Quality measure logic

Status: accepted, with a planned change

## Decision

Measures run in the Java pipeline, per person, after harmonization. Today the three measures are
plain Java (`Measures.java`) with unit tests. They are simplified models of public measure
concepts, not certified implementations.

## Why Java, and why not CQL yet

The standards tooling for this layer is Java: HAPI FHIR for parsing and building resources, the
reference CQL engine for measure logic, and Beam's most complete SDK. Running measures in the same
pipeline as harmonization avoids a second pass over the data.

CQL is the right long-term form: measure stewards publish it, and clinical informaticists can
review it. It needs a terminology service and value sets, which this slice does not have. Plain
Java let the pipeline, the data rights filter and the evidence trail be built and tested first.

## Planned change

Adopt the CQL engine behind the same `Measures.evaluate` contract, with value sets served from a
terminology service. The output schema (`measure_id`, `measure_version`, numerator, evidence)
does not change, so downstream products are unaffected.

## Guardrail that must survive the change

Measures see only sources whose rights include health care operations. The filter is applied
before evaluation, in `MeasureFn`, and stays there.
