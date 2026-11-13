package com.cdap.harmonizer;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.cdap.harmonizer.Converters.Converted;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.List;
import org.hl7.fhir.r4.model.Claim;
import org.hl7.fhir.r4.model.Encounter;
import org.junit.jupiter.api.Test;

class ConvertersTest {
  private static final ObjectMapper JSON = new ObjectMapper();

  static final String X12 = String.join("~\n",
      "ISA*00*          *00*          *ZZ*A*ZZ*B*260315*1200*^*00501*000000001*0*P*:",
      "ST*837*0001*005010X222A1", "BHT*0019*00*CLM000001*20260315*1200*CH",
      "NM1*85*2*NORTHSIDE MEDICAL GROUP*****XX*1730000003", "N3*1 PROVIDER PLAZA", "N4*HARTFORD*CT*06103",
      "NM1*IL*1*YAMAMOTO*BECKY****MI*SHP700000000", "N3*223 BIRCH RD", "N4*HARTFORD*CT*06103",
      "DMG*D8*19750217*F", "CLM*CLM000001*221.00***11:B:1*Y*A*Y*Y", "HI*ABK:E119*ABF:I10",
      "NM1*82*1*OKAFOR*ADAEZE****XX*1730000011", "LX*1", "SV1*HC:99214*187.00*UN*1***1", "DTP*472*D8*20260315",
      "LX*2", "SV1*HC:83036*34.00*UN*1***1", "DTP*472*D8*20260315", "SE*19*0001") + "~";

  @Test
  void x12ClaimBecomesFhirClaim() {
    Converted c = Converters.convert("x12_837", X12);
    assertEquals("SHP700000000", c.demographics().idValue());
    assertEquals("1975-02-17", c.demographics().birthDate());
    // The subscriber's address, not the billing provider's.
    assertEquals("223 BIRCH RD", c.demographics().addressLine1());
    assertNull(c.demographics().ssnLast4());

    Claim claim = (Claim) c.resources().get(1);
    assertEquals("E11.9", claim.getDiagnosis().get(0).getDiagnosisCodeableConcept().getCodingFirstRep().getCode());
    assertEquals("I10", claim.getDiagnosis().get(1).getDiagnosisCodeableConcept().getCodingFirstRep().getCode());
    assertEquals(2, claim.getItem().size());
    assertEquals("83036", claim.getItem().get(1).getProductOrService().getCodingFirstRep().getCode());
    assertEquals("2026-03-15", claim.getItem().get(0).getServicedDateType().getValueAsString());
    assertEquals("221.00", claim.getTotal().getValue().toPlainString());
  }

  @Test
  void hl7AdtBecomesEncounter() {
    String adt = String.join("\r",
        "MSH|^~\\&|ADT|NORTHSIDE_HOSP|CDAP|CDAP|202606191305||ADT^A03|ADT000001|P|2.5.1",
        "PID|1||H100001^^^NORTHSIDE_HOSP^MR||ZIELINSKI^JAMES||19751112|M|||255 Oak St^^Springfield^MA^01103||8605551687||||||6937",
        "PV1|1|E|ED^^^NORTHSIDE_HOSP||||1730000011||||||||||||V0000001|||||||||||||||||||||||||202606191305|202606192355",
        "DG1|1||R07.9^Chest pain, unspecified^I10");
    Converted c = Converters.convert("hl7v2", adt);
    assertEquals("H100001", c.demographics().idValue());
    assertEquals("6937", c.demographics().ssnLast4());
    assertEquals("01103", c.demographics().postalCode());
    Encounter e = (Encounter) c.resources().get(1);
    assertEquals("V0000001", e.getIdElement().getIdPart());
    assertEquals("EMER", e.getClass_().getCode());
    assertEquals("2026-06-19T13:05:00Z", e.getPeriod().getStartElement().getValueAsString());
    assertEquals("R07.9", e.getReasonCodeFirstRep().getCodingFirstRep().getCode());
  }

  @Test
  void malformedPayloadsAreRejectedNotGuessed() {
    assertThrows(Converters.ConversionException.class, () -> Converters.convert("x12_837", "ISA*00~ST*837~"));
    assertThrows(Converters.ConversionException.class, () -> Converters.convert("hl7v2", "MSH|^~\\&|X"));
    assertThrows(Converters.ConversionException.class,
        () -> Converters.convert("fhir", "{\"resourceType\":\"Bundle\",\"entry\":[{\"resource\":{\"resourceType\":\"Patient\",\"bogus\":1}}]}"));
  }

  @Test
  void wrapPointsEveryReferenceAtTheEnterpriseId() throws Exception {
    JsonNode env = JSON.readTree("{\"source_system\":\"payer-claims\",\"tenant\":\"t\",\"ingest_id\":\"ing_1\",\"sha256\":\"abc\"}");
    Converted c = Converters.convert("x12_837", X12);
    JsonNode w = JSON.readTree(HarmonizeFn.wrap(env, "eid-42", c.resources().get(1)));
    assertEquals("eid-42", w.path("eid").asText());
    assertEquals("payer-claims.CLM000001", w.path("resource_id").asText());
    assertEquals("Patient/eid-42", w.path("resource").path("patient").path("reference").asText());
    assertEquals("urn:cdap:source:payer-claims#ing_1", w.path("resource").path("meta").path("source").asText());
  }

  // ---------------------------------------------------------------- measures

  private static JsonNode res(String type, String id, String json) throws Exception {
    return JSON.readTree("{\"resource_type\":\"" + type + "\",\"resource_id\":\"" + id + "\",\"resource\":" + json + "}");
  }

  private static String a1c(String date, double v) {
    return "{\"code\":{\"coding\":[{\"code\":\"4548-4\"}]},\"effectiveDateTime\":\"" + date + "\",\"valueQuantity\":{\"value\":" + v + "}}";
  }

  private static List<JsonNode> diabetic() throws Exception {
    List<JsonNode> l = new ArrayList<>();
    l.add(res("Patient", "p", "{\"birthDate\":\"1960-05-01\",\"gender\":\"female\"}"));
    l.add(res("Condition", "c", "{\"code\":{\"coding\":[{\"code\":\"E11.9\"}]}}"));
    return l;
  }

  @Test
  void diabetesPoorControlUsesMostRecentResultInYear() throws Exception {
    List<JsonNode> l = diabetic();
    l.add(res("Observation", "o1", a1c("2026-02-01", 10.2)));
    l.add(res("Observation", "o2", a1c("2026-08-01", 7.4)));
    Measures.Result r = Measures.evaluate(l, 2026).stream()
        .filter(x -> x.measureId().equals("DM-A1C-POOR-CONTROL")).findFirst().orElseThrow();
    assertFalse(r.numerator());
    assertEquals(List.of("o2"), r.evidence());
  }

  @Test
  void diabetesWithNoResultThisYearCountsAsPoorControl() throws Exception {
    List<JsonNode> l = diabetic();
    l.add(res("Observation", "old", a1c("2025-06-01", 7.0)));
    Measures.Result r = Measures.evaluate(l, 2026).get(0);
    assertTrue(r.numerator());
    assertTrue(r.inverse());
  }

  @Test
  void mammographyLookBackAndEligibility() throws Exception {
    List<JsonNode> l = diabetic();
    l.add(res("Claim", "clm", "{\"item\":[{\"productOrService\":{\"coding\":[{\"code\":\"77067\"}]},\"servicedDate\":\"2025-03-01\"}]}"));
    assertTrue(Measures.evaluate(l, 2026).stream().anyMatch(x -> x.measureId().equals("BCS-MAMMOGRAPHY") && x.numerator()));

    List<JsonNode> tooOld = diabetic();
    tooOld.add(res("Claim", "clm", "{\"item\":[{\"productOrService\":{\"coding\":[{\"code\":\"77067\"}]},\"servicedDate\":\"2024-03-01\"}]}"));
    assertTrue(Measures.evaluate(tooOld, 2026).stream().anyMatch(x -> x.measureId().equals("BCS-MAMMOGRAPHY") && !x.numerator()));

    List<JsonNode> male = new ArrayList<>();
    male.add(res("Patient", "p", "{\"birthDate\":\"1960-05-01\",\"gender\":\"male\"}"));
    assertTrue(Measures.evaluate(male, 2026).isEmpty());
  }
}
