#!/usr/bin/env python3
"""Deterministic synthetic data generator. Standard library only.

Produces one fictional population as it would arrive from five source systems,
each in its native format and with the demographic drift that makes identity
resolution hard (nicknames, typos, moves, missing SSNs, a pair of twins).

Nothing here is derived from real people. Names, identifiers and addresses are
drawn from small fixed lists with a seeded RNG, so every run is identical.

    python3 tools/synth/generate.py --out data/synthetic
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 10, 31)
YEAR = 2026

FEMALE = ["Maria", "Elizabeth", "Katherine", "Margaret", "Jennifer", "Patricia", "Susan",
          "Deborah", "Angela", "Priya", "Mei", "Fatima", "Rebecca", "Victoria", "Christina"]
MALE = ["Robert", "William", "James", "Michael", "Richard", "Thomas", "Joseph", "Daniel",
        "Anthony", "Rajesh", "Wei", "Omar", "Edward", "Charles", "Samuel"]
LAST = ["Alvarez", "Brennan", "Castellano", "Dvorak", "Eriksen", "Fitzgerald", "Goldberg",
        "Hernandez", "Iwasaki", "Jankowski", "Kowalczyk", "Lindqvist", "Montgomery",
        "Nakamura", "Okonkwo", "Petrovic", "Quintero", "Ramirez", "Sorensen", "Thackeray",
        "Underwood", "Vasquez", "Whitfield", "Yamamoto", "Zielinski"]
NICK = {"Robert": "Bob", "William": "Bill", "James": "Jim", "Michael": "Mike",
        "Richard": "Rick", "Thomas": "Tom", "Joseph": "Joe", "Daniel": "Dan",
        "Anthony": "Tony", "Edward": "Ed", "Charles": "Chuck", "Samuel": "Sam",
        "Elizabeth": "Liz", "Katherine": "Kate", "Margaret": "Peggy", "Jennifer": "Jen",
        "Patricia": "Pat", "Susan": "Sue", "Deborah": "Deb", "Rebecca": "Becky",
        "Victoria": "Vicky", "Christina": "Chris"}
STREETS = ["Maple Ave", "Oak St", "Cedar Ln", "Birch Rd", "Elm Ct", "Juniper Way",
           "Hawthorne Blvd", "Sycamore Dr", "Willow Pl", "Chestnut St"]
CITIES = [("Hartford", "CT", "06103"), ("New Haven", "CT", "06510"),
          ("Stamford", "CT", "06901"), ("Providence", "RI", "02903"),
          ("Springfield", "MA", "01103"), ("Worcester", "MA", "01608")]
PROVIDERS = [("1730000011", "Okafor", "Adaeze"), ("1730000029", "Lindgren", "Paul"),
             ("1730000037", "Shah", "Meera")]


def iso(d: date) -> str:
    return d.isoformat()


def typo(rng: random.Random, s: str) -> str:
    """Transpose two adjacent interior letters."""
    if len(s) < 5:
        return s
    i = rng.randrange(1, len(s) - 2)
    return s[:i] + s[i + 1] + s[i] + s[i + 2:]


def make_population(rng: random.Random, n: int) -> list[dict]:
    people: list[dict] = []
    used: set[tuple[str, str]] = set()
    for i in range(n):
        sex = "female" if rng.random() < 0.55 else "male"
        while True:
            first = rng.choice(FEMALE if sex == "female" else MALE)
            last = rng.choice(LAST)
            if (first, last) not in used:
                used.add((first, last))
                break
        age = rng.randint(27, 79)
        dob = date(YEAR - age, rng.randint(1, 12), rng.randint(1, 28))
        city, state, zip_ = rng.choice(CITIES)
        p = {
            "idx": i, "first": first, "last": last, "sex": sex, "dob": dob,
            "line1": f"{rng.randint(10, 990)} {rng.choice(STREETS)}",
            "city": city, "state": state, "zip": zip_,
            "phone": f"860555{rng.randint(1000, 9999)}",
            "ssn4": f"{rng.randint(0, 9999):04d}",
            "mrn": f"NS{100000 + i:06d}",
            "member_id": f"SHP{700000000 + i * 37:09d}",
            "bh_mrn": f"RB{50000 + i:05d}",
        }
        p["diabetic"] = rng.random() < 0.35
        p["a1c_state"] = rng.choice(["controlled", "controlled", "poor", "missing"]) if p["diabetic"] else None
        p["hypertensive"] = rng.random() < 0.45
        p["bp_controlled"] = rng.random() < 0.6 if p["hypertensive"] else None
        p["mammo_eligible"] = sex == "female" and 52 <= age <= 74
        p["mammo_done"] = p["mammo_eligible"] and rng.random() < 0.6
        p["smoker"] = rng.random() < 0.2
        p["ed_visits"] = rng.choice([0, 0, 0, 0, 1, 1, 2, 4])
        p["in_payer"] = rng.random() < 0.85
        p["has_notes"] = rng.random() < 0.65
        people.append(p)

    # A pair of twins: same surname, birth date, address and phone. Only the
    # first name and SSN differ. The matcher must not merge them.
    a, b = people[-2], people[-1]
    for k in ("last", "dob", "line1", "city", "state", "zip", "phone", "sex"):
        b[k] = a[k]
    a["first"], b["first"] = ("Maria", "Marta") if a["sex"] == "female" else ("Daniel", "Darnell")
    b["ssn4"] = f"{(int(a['ssn4']) + 4321) % 10000:04d}"
    a["in_payer"] = b["in_payer"] = True

    # Behavioral health program: four people, two of whom have signed a
    # consent that lets their treating providers see those records.
    for j, i in enumerate((3, 9, 17, 24)):
        people[i]["bh"] = {"code": ["F10.20", "F32.9", "F11.20", "F41.1"][j],
                           "display": ["Alcohol dependence, uncomplicated", "Major depressive disorder, single episode",
                                       "Opioid dependence, uncomplicated", "Generalized anxiety disorder"][j],
                           "consent": j % 2 == 0}
    return people


# ---------------------------------------------------------------- FHIR (EHR)

def coding(system: str, code: str, display: str) -> dict:
    return {"coding": [{"system": system, "code": code, "display": display}], "text": display}


def patient_resource(p: dict, pid: str, system: str, with_ssn: bool = True, phone: str | None = None) -> dict:
    ids = [{"system": system, "value": pid}]
    if with_ssn:
        ids.append({"system": "urn:cdap:ssn-last4", "value": p["ssn4"]})
    return {
        "resourceType": "Patient", "id": pid, "identifier": ids,
        "name": [{"family": p["last"], "given": [p["first"]]}],
        "gender": p["sex"], "birthDate": iso(p["dob"]),
        "telecom": [{"system": "phone", "value": phone or p["phone"]}],
        "address": [{"line": [p["line1"]], "city": p["city"], "state": p["state"], "postalCode": p["zip"]}],
    }


def ehr_bundle(rng: random.Random, p: dict) -> dict:
    ref = {"reference": f"Patient/{p['mrn']}"}
    entries = [patient_resource(p, p["mrn"], "urn:cdap:mrn:northside")]

    def cond(code: str, display: str, onset: date) -> None:
        entries.append({
            "resourceType": "Condition", "id": f"{p['mrn']}-c-{code.replace('.', '')}", "subject": ref,
            "clinicalStatus": coding("http://terminology.hl7.org/CodeSystem/condition-clinical", "active", "Active"),
            "code": coding("http://hl7.org/fhir/sid/icd-10-cm", code, display),
            "onsetDateTime": iso(onset)})

    def obs(suffix: str, when: date, body: dict) -> None:
        entries.append({"resourceType": "Observation", "id": f"{p['mrn']}-o-{suffix}", "status": "final",
                        "subject": ref, "effectiveDateTime": iso(when), **body})

    if p["diabetic"]:
        cond("E11.9", "Type 2 diabetes mellitus without complications", date(YEAR - rng.randint(2, 9), 5, 14))
        a1c = coding("http://loinc.org", "4548-4", "Hemoglobin A1c/Hemoglobin.total in Blood")
        obs("a1c-prior", date(YEAR - 1, rng.randint(2, 11), 12),
            {"code": a1c, "valueQuantity": {"value": round(rng.uniform(6.8, 8.9), 1), "unit": "%"}})
        if p["a1c_state"] != "missing":
            lo, hi = (6.4, 8.6) if p["a1c_state"] == "controlled" else (9.2, 11.4)
            obs("a1c", date(YEAR, rng.randint(2, 9), rng.randint(1, 28)),
                {"code": a1c, "valueQuantity": {"value": round(rng.uniform(lo, hi), 1), "unit": "%"}})
        entries.append({"resourceType": "MedicationRequest", "id": f"{p['mrn']}-m-metformin", "status": "active",
                        "intent": "order", "subject": ref, "authoredOn": iso(date(YEAR, 1, 20)),
                        "medicationCodeableConcept": coding("http://www.nlm.nih.gov/research/umls/rxnorm", "861007",
                                                            "metformin hydrochloride 500 MG Oral Tablet")})
    if p["hypertensive"]:
        cond("I10", "Essential (primary) hypertension", date(YEAR - rng.randint(1, 12), 3, 9))
        sys_, dia = (rng.randint(112, 136), rng.randint(68, 86)) if p["bp_controlled"] else (rng.randint(144, 168), rng.randint(88, 102))
        obs("bp", date(YEAR, rng.randint(3, 10), rng.randint(1, 28)), {
            "code": coding("http://loinc.org", "85354-9", "Blood pressure panel"),
            "component": [
                {"code": coding("http://loinc.org", "8480-6", "Systolic blood pressure"),
                 "valueQuantity": {"value": sys_, "unit": "mm[Hg]"}},
                {"code": coding("http://loinc.org", "8462-4", "Diastolic blood pressure"),
                 "valueQuantity": {"value": dia, "unit": "mm[Hg]"}}]})
        entries.append({"resourceType": "MedicationRequest", "id": f"{p['mrn']}-m-lisinopril", "status": "active",
                        "intent": "order", "subject": ref, "authoredOn": iso(date(YEAR, 2, 3)),
                        "medicationCodeableConcept": coding("http://www.nlm.nih.gov/research/umls/rxnorm", "314076",
                                                            "lisinopril 10 MG Oral Tablet")})
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in entries]}


def bh_bundle(p: dict) -> dict:
    ref = {"reference": f"Patient/{p['bh_mrn']}"}
    bh = p["bh"]
    # The program holds a different phone number and no SSN for these patients.
    entries = [patient_resource(p, p["bh_mrn"], "urn:cdap:mrn:riverbend", with_ssn=False,
                                phone="401555" + p["phone"][-4:]),
               {"resourceType": "Condition", "id": f"{p['bh_mrn']}-c-1", "subject": ref,
                "clinicalStatus": coding("http://terminology.hl7.org/CodeSystem/condition-clinical", "active", "Active"),
                "code": coding("http://hl7.org/fhir/sid/icd-10-cm", bh["code"], bh["display"]),
                "onsetDateTime": iso(date(YEAR - 1, 9, 2))}]
    if bh["consent"]:
        entries.append({
            "resourceType": "Consent", "id": f"{p['bh_mrn']}-consent", "status": "active", "patient": ref,
            "scope": coding("http://terminology.hl7.org/CodeSystem/consentscope", "patient-privacy", "Privacy Consent"),
            "category": [coding("http://loinc.org", "59284-0", "Consent Document")],
            "dateTime": iso(date(YEAR, 1, 15)),
            "provision": {"type": "permit",
                          "purpose": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ActReason",
                                       "code": "TREAT", "display": "treatment"}]}})
    return {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": r} for r in entries]}


# ------------------------------------------------------------ X12 837P claims

def x12_837(p: dict, view: dict, claim_id: str, dos: date, dx: list[str], lines: list[tuple[str, float]],
            prov: tuple[str, str, str], ctrl: int) -> str:
    total = sum(c for _, c in lines)
    d8 = dos.strftime("%Y%m%d")
    seg = [
        f"ISA*00*          *00*          *ZZ*NORTHSIDE      *ZZ*SUMMITHP       *{dos.strftime('%y%m%d')}*1200*^*00501*{ctrl:09d}*0*P*:",
        f"GS*HC*NORTHSIDE*SUMMITHP*{d8}*1200*{ctrl}*X*005010X222A1",
        f"ST*837*{ctrl:04d}*005010X222A1",
        f"BHT*0019*00*{claim_id}*{d8}*1200*CH",
        "NM1*41*2*NORTHSIDE HEALTH*****46*NS0001",
        "NM1*40*2*SUMMIT HEALTH PLAN*****46*SHP001",
        "HL*1**20*1",
        "NM1*85*2*NORTHSIDE MEDICAL GROUP*****XX*1730000003",
        "HL*2*1*22*0",
        "SBR*P*18*******CI",
        f"NM1*IL*1*{view['last']}*{view['first']}****MI*{p['member_id']}",
        f"N3*{view['line1']}",
        f"N4*{view['city']}*{view['state']}*{view['zip']}",
        f"DMG*D8*{p['dob'].strftime('%Y%m%d')}*{'F' if p['sex'] == 'female' else 'M'}",
        f"CLM*{claim_id}*{total:.2f}***11:B:1*Y*A*Y*Y",
        "HI*" + "*".join(("ABK:" if i == 0 else "ABF:") + c.replace(".", "") for i, c in enumerate(dx)),
        f"NM1*82*1*{prov[1].upper()}*{prov[2].upper()}****XX*{prov[0]}",
    ]
    for n, (cpt, charge) in enumerate(lines, 1):
        seg += [f"LX*{n}", f"SV1*HC:{cpt}*{charge:.2f}*UN*1***1", f"DTP*472*D8*{d8}"]
    seg += [f"SE*{len(seg) - 1}*{ctrl:04d}", f"GE*1*{ctrl}", f"IEA*1*{ctrl:09d}"]
    return "~\n".join(seg) + "~\n"


def payer_view(rng: random.Random, p: dict) -> dict:
    """How the health plan spells this member: upper case, sometimes a nickname, sometimes a stale address."""
    first = NICK[p["first"]] if p["first"] in NICK and rng.random() < 0.3 else p["first"]
    v = {"first": first.upper(), "last": p["last"].upper(), "line1": p["line1"].upper(),
         "city": p["city"].upper(), "state": p["state"], "zip": p["zip"]}
    if rng.random() < 0.2 and p["idx"] < 38:  # moved since enrolment
        city, state, zip_ = rng.choice(CITIES)
        v.update(line1=f"{rng.randint(10, 990)} {rng.choice(STREETS)}".upper(), city=city.upper(), state=state, zip=zip_)
    return v


def claims_for(rng: random.Random, p: dict, counter: list[int]) -> list[tuple[str, str]]:
    view = payer_view(rng, p)
    out: list[tuple[str, str]] = []

    def add(dos: date, dx: list[str], lines: list[tuple[str, float]]) -> None:
        counter[0] += 1
        cid = f"CLM{counter[0]:06d}"
        out.append((cid, x12_837(p, view, cid, dos, dx, lines, rng.choice(PROVIDERS), counter[0])))

    dx = [c for c, on in (("E11.9", p["diabetic"]), ("I10", p["hypertensive"])) if on] or ["Z00.00"]
    for _ in range(rng.randint(2, 5)):
        dos = date(YEAR, 1, 5) + timedelta(days=rng.randint(0, 290))
        lines = [(rng.choice(["99213", "99214"]), rng.choice([128.0, 142.0, 187.0, 210.0]))]
        if p["diabetic"] and rng.random() < 0.5:
            lines.append(("83036", 34.0))  # HbA1c lab billed with the visit
        add(dos, dx, lines)
    if p["mammo_done"]:
        add(date(YEAR, 1, 10) - timedelta(days=rng.randint(0, 600)) + timedelta(days=200), ["Z12.31"], [("77067", 265.0)])
    for _ in range(p["ed_visits"]):
        add(date(YEAR, 1, 5) + timedelta(days=rng.randint(0, 290)), ["R07.9"] + dx[:1],
            [("99284", rng.choice([980.0, 1240.0, 1575.0]))])
    return out


# ---------------------------------------------------------------- HL7 v2 ADT

def adt_messages(rng: random.Random, p: dict, counter: list[int]) -> list[tuple[str, str]]:
    last = typo(rng, p["last"]) if rng.random() < 0.25 and p["idx"] < 38 else p["last"]
    out = []
    for _ in range(p["ed_visits"]):
        counter[0] += 1
        admit = date(YEAR, 1, 5) + timedelta(days=rng.randint(0, 290))
        ts = admit.strftime("%Y%m%d") + f"{rng.randint(0, 23):02d}{rng.randint(0, 59):02d}"
        out_ts = admit.strftime("%Y%m%d") + "2355"
        mid = f"ADT{counter[0]:06d}"
        sex = "F" if p["sex"] == "female" else "M"
        # The hospital registers patients under its own medical record number.
        msg = "\r".join([
            f"MSH|^~\\&|ADT|NORTHSIDE_HOSP|CDAP|CDAP|{ts}||ADT^A03|{mid}|P|2.5.1",
            f"EVN|A03|{ts}",
            f"PID|1||H{p['mrn'][2:]}^^^NORTHSIDE_HOSP^MR||{last.upper()}^{p['first'].upper()}||{p['dob'].strftime('%Y%m%d')}|{sex}|||"
            f"{p['line1']}^^{p['city']}^{p['state']}^{p['zip']}||{p['phone']}||||||{p['ssn4']}",
            f"PV1|1|E|ED^^^NORTHSIDE_HOSP||||{rng.choice(PROVIDERS)[0]}||||||||||||V{counter[0]:07d}|||||||||||||||||||||||||{ts}|{out_ts}",
            "DG1|1||R07.9^Chest pain, unspecified^I10",
        ]) + "\r"
        out.append((mid, msg))
    return out


# ------------------------------------------------------------ clinical notes

def note_for(rng: random.Random, p: dict) -> dict:
    visit = date(YEAR, rng.randint(4, 10), rng.randint(1, 28))
    age = YEAR - p["dob"].year
    pron = "She" if p["sex"] == "female" else "He"
    lines = [f"PROGRESS NOTE - Northside Medical Group", f"Date of service: {iso(visit)}",
             f"Patient: {p['first']} {p['last']}, {age}-year-old {p['sex']}.", "",
             "SUBJECTIVE:", f"{pron} presents for routine follow-up."]
    truth: dict = {"conditions": [], "medications": [], "labs": [], "social": []}
    if p["smoker"]:
        ppd = rng.choice(["half a pack", "1 pack", "1.5 packs"])
        lines.append(f"Patient reports smoking {ppd} per day for the past {rng.randint(5, 30)} years. Not ready to quit.")
        truth["social"].append({"type": "tobacco_use", "status": "current"})
    else:
        lines.append("Denies tobacco use.")
        truth["social"].append({"type": "tobacco_use", "status": "never_or_former"})
    lines += ["", "ASSESSMENT AND PLAN:"]
    if p["diabetic"]:
        truth["conditions"].append({"code": "E11.9", "name": "type 2 diabetes mellitus"})
        if p["a1c_state"] == "missing":
            # The only current A1c for these patients lives in free text.
            val = round(rng.uniform(9.3, 10.8), 1)
            when = visit - timedelta(days=rng.randint(5, 30))
            lines.append(f"1. Type 2 diabetes mellitus. Outside lab result reviewed: HbA1c {val}% on {iso(when)}. "
                         "Poorly controlled.")
            truth["labs"].append({"test": "hba1c", "value": val, "unit": "%", "date": iso(when)})
            lines.append("   Increase metformin to 1000 mg twice daily.")
            truth["medications"].append({"name": "metformin", "dose": "1000 mg", "frequency": "twice daily"})
        else:
            lines.append("1. Type 2 diabetes mellitus. Continue metformin 500 mg twice daily.")
            truth["medications"].append({"name": "metformin", "dose": "500 mg", "frequency": "twice daily"})
    if p["hypertensive"]:
        truth["conditions"].append({"code": "I10", "name": "hypertension"})
        lines.append(f"{2 if p['diabetic'] else 1}. Hypertension. Continue lisinopril 10 mg daily.")
        truth["medications"].append({"name": "lisinopril", "dose": "10 mg", "frequency": "daily"})
    if not (p["diabetic"] or p["hypertensive"]):
        lines.append("1. Health maintenance. No chronic conditions identified.")
    if p["mammo_eligible"] and not p["mammo_done"]:
        lines.append("Screening mammogram discussed; patient declined at this time.")
    prov = rng.choice(PROVIDERS)
    lines += ["", f"Electronically signed: {prov[2]} {prov[1]}, MD"]
    return {"document_id": f"DOC-{p['mrn']}-1", "doc_type": "progress_note", "authored": iso(visit),
            "author_npi": prov[0],
            "patient": {"mrn": p["mrn"], "mrn_system": "urn:cdap:mrn:northside", "first": p["first"],
                        "last": p["last"], "dob": iso(p["dob"]), "sex": p["sex"], "zip": p["zip"]},
            "text": "\n".join(lines), "_truth": truth}


# ----------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic")
    ap.add_argument("--people", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261031)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    people = make_population(rng, args.people)
    dirs = {k: out / k for k in ("ehr-northside", "payer-claims", "hospital-adt", "bh-clinic", "document-feed")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            f.unlink()

    truth: list[dict] = []          # source record -> person, for scoring identity resolution
    golden_notes: list[dict] = []   # note -> expected extraction, for scoring the LLM
    claim_n, adt_n = [0], [0]
    for p in people:
        (dirs["ehr-northside"] / f"{p['mrn']}.json").write_text(json.dumps(ehr_bundle(rng, p), indent=1))
        truth.append({"person": p["idx"], "source": "ehr-northside", "system": "urn:cdap:mrn:northside", "id": p["mrn"]})
        if p["in_payer"]:
            for cid, text in claims_for(rng, p, claim_n):
                (dirs["payer-claims"] / f"{cid}.x12").write_text(text)
            truth.append({"person": p["idx"], "source": "payer-claims", "system": "urn:cdap:member:summit", "id": p["member_id"]})
        msgs = adt_messages(rng, p, adt_n)
        for mid, text in msgs:
            (dirs["hospital-adt"] / f"{mid}.hl7").write_text(text)
        if msgs:
            truth.append({"person": p["idx"], "source": "hospital-adt", "system": "urn:cdap:mrn:northside-hosp", "id": "H" + p["mrn"][2:]})
        if "bh" in p:
            (dirs["bh-clinic"] / f"{p['bh_mrn']}.json").write_text(json.dumps(bh_bundle(p), indent=1))
            truth.append({"person": p["idx"], "source": "bh-clinic", "system": "urn:cdap:mrn:riverbend", "id": p["bh_mrn"]})
        if p["has_notes"]:
            note = note_for(rng, p)
            golden_notes.append({"document_id": note["document_id"], "text": note["text"], "expected": note.pop("_truth")})
            (dirs["document-feed"] / f"{note['document_id']}.json").write_text(json.dumps(note, indent=1))

    (out / "identity_truth.json").write_text(json.dumps(truth, indent=1))
    (out / "extraction_golden.json").write_text(json.dumps(golden_notes, indent=1))
    counts = {k: len(list(d.iterdir())) for k, d in dirs.items()}
    print(json.dumps({"people": len(people), "files": counts}))


if __name__ == "__main__":
    main()
