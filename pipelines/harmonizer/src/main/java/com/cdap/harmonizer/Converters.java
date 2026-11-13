package com.cdap.harmonizer;

import ca.uhn.fhir.context.FhirContext;
import ca.uhn.fhir.parser.IParser;
import ca.uhn.fhir.parser.StrictErrorHandler;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.hl7.fhir.r4.model.Address;
import org.hl7.fhir.r4.model.Bundle;
import org.hl7.fhir.r4.model.Claim;
import org.hl7.fhir.r4.model.CodeableConcept;
import org.hl7.fhir.r4.model.Coding;
import org.hl7.fhir.r4.model.ContactPoint;
import org.hl7.fhir.r4.model.DateTimeType;
import org.hl7.fhir.r4.model.DateType;
import org.hl7.fhir.r4.model.DocumentReference;
import org.hl7.fhir.r4.model.Encounter;
import org.hl7.fhir.r4.model.Enumerations;
import org.hl7.fhir.r4.model.Identifier;
import org.hl7.fhir.r4.model.Money;
import org.hl7.fhir.r4.model.Patient;
import org.hl7.fhir.r4.model.Period;
import org.hl7.fhir.r4.model.Reference;
import org.hl7.fhir.r4.model.Resource;

/**
 * Source format to FHIR R4. Each converter takes one raw payload about one patient and returns
 * that patient's demographics (for identity resolution) plus the resources the payload yields.
 *
 * <p>Resources reference the patient by a placeholder; the caller swaps in the enterprise ID
 * once the master patient index has answered.
 */
public final class Converters {
  public static final String PATIENT_PLACEHOLDER = "Patient/__SUBJECT__";
  static final String ICD10 = "http://hl7.org/fhir/sid/icd-10-cm";
  static final String CPT = "http://www.ama-assn.org/go/cpt";
  static final String NPI = "http://hl7.org/fhir/sid/us-npi";
  static final String SSN4 = "urn:cdap:ssn-last4";

  private static final FhirContext CTX = FhirContext.forR4();
  private static final ObjectMapper JSON = new ObjectMapper();

  private Converters() {}

  /** One source system's description of a person, in the shape the identity service accepts. */
  public record Demographics(
      String idSystem, String idValue, String first, String last, String birthDate, String sex,
      String ssnLast4, String phone, String addressLine1, String postalCode) {}

  public record Converted(Demographics demographics, List<Resource> resources) {}

  public static class ConversionException extends RuntimeException {
    public ConversionException(String message) {
      super(message);
    }
  }

  public static IParser parser() {
    return CTX.newJsonParser().setParserErrorHandler(new StrictErrorHandler());
  }

  public static Converted convert(String kind, String payload) {
    try {
      return switch (kind) {
        case "fhir" -> fhir(payload);
        case "x12_837" -> x12(payload);
        case "hl7v2" -> hl7(payload);
        case "document" -> document(payload);
        default -> throw new ConversionException("unsupported kind: " + kind);
      };
    } catch (ConversionException e) {
      throw e;
    } catch (RuntimeException | java.io.IOException e) {
      throw new ConversionException(kind + ": " + e.getMessage());
    }
  }

  // ------------------------------------------------------------------ FHIR

  static Converted fhir(String payload) {
    Bundle bundle = parser().parseResource(Bundle.class, payload);
    List<Resource> out = new ArrayList<>();
    Patient patient = null;
    for (Bundle.BundleEntryComponent e : bundle.getEntry()) {
      Resource r = e.getResource();
      if (r instanceof Patient p && patient == null) {
        patient = p;
      }
      out.add(r);
    }
    if (patient == null || patient.getIdentifier().isEmpty()) {
      throw new ConversionException("fhir: bundle has no identified Patient");
    }
    String ssn = null;
    for (Identifier id : patient.getIdentifier()) {
      if (SSN4.equals(id.getSystem())) {
        ssn = id.getValue();
      }
    }
    Identifier primary = patient.getIdentifierFirstRep();
    Address addr = patient.getAddressFirstRep();
    Demographics d = new Demographics(
        primary.getSystem(), primary.getValue(),
        patient.getNameFirstRep().getGivenAsSingleString(), patient.getNameFirstRep().getFamily(),
        patient.getBirthDateElement().getValueAsString(),
        patient.hasGender() ? patient.getGender().toCode() : null, ssn,
        patient.getTelecomFirstRep().getValue(),
        addr.hasLine() ? addr.getLine().get(0).getValue() : null, addr.getPostalCode());
    return new Converted(d, out);
  }

  // ------------------------------------------------------------- X12 837P

  static Converted x12(String payload) {
    String member = null, first = null, last = null, line1 = null, zip = null, city = null, state = null;
    String dob = null, sex = null, created = null, npi = null;
    Claim claim = new Claim();
    claim.setStatus(Claim.ClaimStatus.ACTIVE).setUse(Claim.Use.CLAIM);
    claim.setType(concept("http://terminology.hl7.org/CodeSystem/claim-type", "professional", "Professional"));
    claim.setPriority(concept("http://terminology.hl7.org/CodeSystem/processpriority", "normal", "Normal"));
    claim.setPatient(new Reference(PATIENT_PLACEHOLDER));
    Claim.ItemComponent item = null;

    for (String raw : payload.split("~")) {
      String[] el = raw.strip().split("\\*", -1);
      switch (el[0]) {
        case "BHT" -> created = isoDate(at(el, 4));
        case "NM1" -> {
          if ("IL".equals(at(el, 1))) {
            last = at(el, 3);
            first = at(el, 4);
            member = at(el, 9);
          } else if ("82".equals(at(el, 1))) {
            npi = at(el, 9);
          }
        }
        case "N3" -> {
          if (member != null && line1 == null) {
            line1 = at(el, 1);
          }
        }
        case "N4" -> {
          if (member != null && zip == null) {
            city = at(el, 1);
            state = at(el, 2);
            zip = at(el, 3);
          }
        }
        case "DMG" -> {
          dob = isoDate(at(el, 2));
          sex = "F".equals(at(el, 3)) ? "female" : "M".equals(at(el, 3)) ? "male" : "unknown";
        }
        case "CLM" -> {
          claim.setId(at(el, 1));
          claim.addIdentifier().setSystem("urn:cdap:claim:summit").setValue(at(el, 1));
          claim.setTotal(usd(at(el, 2)));
        }
        case "HI" -> {
          for (int i = 1; i < el.length; i++) {
            String[] parts = el[i].split(":");
            if (parts.length == 2) {
              claim.addDiagnosis().setSequence(i)
                  .setDiagnosis(concept(ICD10, dotIcd(parts[1]), null));
            }
          }
        }
        case "SV1" -> {
          String[] proc = at(el, 1).split(":");
          item = claim.addItem().setSequence(claim.getItem().size() + 1);
          item.setProductOrService(concept(CPT, proc.length > 1 ? proc[1] : proc[0], null));
          item.setNet(usd(at(el, 2)));
        }
        case "DTP" -> {
          if ("472".equals(at(el, 1)) && item != null) {
            item.setServiced(new DateType(isoDate(at(el, 3))));
          }
        }
        default -> { }
      }
    }
    if (member == null || claim.getId() == null || dob == null) {
      throw new ConversionException("x12_837: missing subscriber, claim or birth date");
    }
    if (created != null) {
      claim.setCreatedElement(new DateTimeType(created));
    }
    if (npi != null) {
      claim.setProvider(new Reference().setIdentifier(new Identifier().setSystem(NPI).setValue(npi)));
    }
    claim.addInsurance().setSequence(1).setFocal(true).setCoverage(new Reference().setDisplay("Summit Health Plan"));

    Patient p = new Patient();
    p.setId(member);
    p.addIdentifier().setSystem("urn:cdap:member:summit").setValue(member);
    p.addName().setFamily(last).addGiven(first);
    p.setGender(Enumerations.AdministrativeGender.fromCode(sex));
    p.setBirthDateElement(new DateType(dob));
    p.addAddress().addLine(line1).setCity(city).setState(state).setPostalCode(zip);

    Demographics d = new Demographics("urn:cdap:member:summit", member, first, last, dob, sex, null, null, line1, zip);
    return new Converted(d, List.of(p, claim));
  }

  // --------------------------------------------------------- HL7 v2 ADT

  static Converted hl7(String payload) {
    String[] pid = null, pv1 = null, dg1 = null, msh = null;
    for (String seg : payload.split("[\\r\\n]+")) {
      String[] f = seg.split("\\|", -1);
      switch (f[0]) {
        case "MSH" -> msh = f;
        case "PID" -> pid = f;
        case "PV1" -> pv1 = f;
        case "DG1" -> dg1 = f;
        default -> { }
      }
    }
    if (msh == null || pid == null || pv1 == null) {
      throw new ConversionException("hl7v2: MSH, PID and PV1 are required");
    }
    String mrn = at(pid, 3).split("\\^")[0];
    String[] name = at(pid, 5).split("\\^");
    String[] addr = at(pid, 11).split("\\^", -1);
    String dob = isoDate(at(pid, 7));
    String sex = "F".equals(at(pid, 8)) ? "female" : "M".equals(at(pid, 8)) ? "male" : "unknown";
    String ssn = at(pid, 19).isEmpty() ? null : at(pid, 19);
    String line1 = at(addr, 0);
    String zip = at(addr, 4);
    String last = name[0];
    String first = name.length > 1 ? name[1] : "";
    String system = "urn:cdap:mrn:northside-hosp";

    Patient p = new Patient();
    p.setId(mrn);
    p.addIdentifier().setSystem(system).setValue(mrn);
    p.addName().setFamily(last).addGiven(first);
    p.setGender(Enumerations.AdministrativeGender.fromCode(sex));
    p.setBirthDateElement(new DateType(dob));
    p.addAddress().addLine(line1).setCity(at(addr, 2)).setState(at(addr, 3)).setPostalCode(zip);
    if (!at(pid, 13).isEmpty()) {
      p.addTelecom().setSystem(ContactPoint.ContactPointSystem.PHONE).setValue(at(pid, 13));
    }

    Encounter enc = new Encounter();
    enc.setId(at(pv1, 19).isEmpty() ? at(msh, 9) : at(pv1, 19));
    enc.addIdentifier().setSystem("urn:cdap:visit:northside-hosp").setValue(enc.getId());
    enc.setStatus(at(pv1, 45).isEmpty() ? Encounter.EncounterStatus.INPROGRESS : Encounter.EncounterStatus.FINISHED);
    boolean emergency = "E".equals(at(pv1, 2));
    enc.setClass_(new Coding("http://terminology.hl7.org/CodeSystem/v3-ActCode",
        emergency ? "EMER" : "IMP", emergency ? "emergency" : "inpatient encounter"));
    enc.setSubject(new Reference(PATIENT_PLACEHOLDER));
    Period period = new Period();
    period.setStartElement(new DateTimeType(isoDateTime(at(pv1, 44))));
    if (!at(pv1, 45).isEmpty()) {
      period.setEndElement(new DateTimeType(isoDateTime(at(pv1, 45))));
    }
    enc.setPeriod(period);
    if (!at(pv1, 7).isEmpty()) {
      enc.addParticipant().setIndividual(
          new Reference().setIdentifier(new Identifier().setSystem(NPI).setValue(at(pv1, 7))));
    }
    if (dg1 != null) {
      String[] dx = at(dg1, 3).split("\\^");
      enc.addReasonCode(concept(ICD10, dx[0], dx.length > 1 ? dx[1] : null));
    }

    Demographics d = new Demographics(system, mrn, first, last, dob, sex, ssn,
        at(pid, 13).isEmpty() ? null : at(pid, 13), line1, zip);
    return new Converted(d, List.of(p, enc));
  }

  // ------------------------------------------------------------ documents

  static Converted document(String payload) throws java.io.IOException {
    JsonNode doc = JSON.readTree(payload);
    JsonNode pt = doc.path("patient");
    String mrn = pt.path("mrn").asText();
    String system = pt.path("mrn_system").asText();
    if (mrn.isEmpty() || system.isEmpty() || doc.path("text").asText().isEmpty()) {
      throw new ConversionException("document: patient identifier and text are required");
    }
    DocumentReference ref = new DocumentReference();
    ref.setId(doc.path("document_id").asText());
    ref.setStatus(Enumerations.DocumentReferenceStatus.CURRENT);
    ref.setType(concept("http://loinc.org", "11506-3", "Progress note"));
    ref.setSubject(new Reference(PATIENT_PLACEHOLDER));
    ref.setDateElement(new org.hl7.fhir.r4.model.InstantType(doc.path("authored").asText() + "T00:00:00Z"));
    if (doc.hasNonNull("author_npi")) {
      ref.addAuthor(new Reference().setIdentifier(
          new Identifier().setSystem(NPI).setValue(doc.path("author_npi").asText())));
    }
    ref.addContent().getAttachment().setContentType("text/plain; charset=utf-8")
        .setTitle(doc.path("doc_type").asText())
        .setData(doc.path("text").asText().getBytes(StandardCharsets.UTF_8));

    // Notes carry too little demographic detail to stand alone; the identifier does the linking.
    Demographics d = new Demographics(system, mrn, pt.path("first").asText(), pt.path("last").asText(),
        pt.path("dob").asText(), pt.path("sex").asText(null), null, null, null, pt.path("zip").asText(null));
    return new Converted(d, List.of(ref));
  }

  // -------------------------------------------------------------- helpers

  private static String at(String[] a, int i) {
    return i < a.length ? a[i] : "";
  }

  static CodeableConcept concept(String system, String code, String display) {
    Coding c = new Coding().setSystem(system).setCode(code);
    if (display != null) {
      c.setDisplay(display);
    }
    return new CodeableConcept().addCoding(c);
  }

  private static Money usd(String amount) {
    return new Money().setValue(new BigDecimal(amount)).setCurrency("USD");
  }

  /** X12 drops the decimal point from ICD-10-CM codes; FHIR keeps it. */
  static String dotIcd(String code) {
    return code.length() > 3 ? code.substring(0, 3) + "." + code.substring(3) : code;
  }

  static String isoDate(String yyyymmdd) {
    if (yyyymmdd.length() < 8) {
      throw new ConversionException("bad date: " + yyyymmdd);
    }
    return yyyymmdd.substring(0, 4) + "-" + yyyymmdd.substring(4, 6) + "-" + yyyymmdd.substring(6, 8);
  }

  static String isoDateTime(String ts) {
    if (ts.length() < 12) {
      return isoDate(ts) + "T00:00:00Z";
    }
    return isoDate(ts) + "T" + ts.substring(8, 10) + ":" + ts.substring(10, 12) + ":00Z";
  }
}
