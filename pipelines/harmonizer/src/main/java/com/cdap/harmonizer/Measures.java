package com.cdap.harmonizer;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * Quality measures over one person's harmonized record.
 *
 * <p>These three are modeled on widely used public measure concepts (diabetes control, blood
 * pressure control, breast cancer screening) but are simplified and are not certified
 * implementations of any measure steward's specification. They are written in plain Java so the
 * logic is reviewable today; ADR 0004 covers moving measure logic to CQL.
 *
 * <p>Only sources whose data rights include the "operations" purpose may feed a measure. Records
 * from consent-restricted programs are excluded before any logic runs.
 */
public final class Measures {
  public static final String VERSION = "2026.1";
  private static final ObjectMapper JSON = new ObjectMapper();
  private static final Set<String> MAMMOGRAPHY_CPT = Set.of("77065", "77066", "77067");

  private Measures() {}

  public record Result(String measureId, boolean numerator, boolean inverse, String detail, List<String> evidence) {}

  /**
   * @param wrapped harmonized resource wrappers for one EID, already filtered to permitted sources
   */
  public static List<Result> evaluate(List<JsonNode> wrapped, int year) {
    LocalDate start = LocalDate.of(year, 1, 1);
    LocalDate end = LocalDate.of(year, 12, 31);
    String sex = null;
    LocalDate dob = null;
    boolean diabetes = false, hypertension = false;
    List<String> dxEvidence = new ArrayList<>();

    JsonNode latestA1c = null, latestBp = null;
    String latestA1cId = null, latestBpId = null, mammoId = null;
    LocalDate latestA1cDate = null, latestBpDate = null;

    for (JsonNode w : wrapped) {
      JsonNode r = w.path("resource");
      String id = w.path("resource_id").asText();
      switch (w.path("resource_type").asText()) {
        case "Patient" -> {
          if (dob == null && r.hasNonNull("birthDate")) {
            dob = LocalDate.parse(r.path("birthDate").asText());
            sex = r.path("gender").asText(null);
          }
        }
        case "Condition" -> {
          String code = firstCode(r.path("code"));
          if (code.startsWith("E11")) {
            diabetes = true;
            dxEvidence.add(id);
          } else if (code.equals("I10")) {
            hypertension = true;
            dxEvidence.add(id);
          }
        }
        case "Claim" -> {
          for (JsonNode dx : r.path("diagnosis")) {
            String code = firstCode(dx.path("diagnosisCodeableConcept"));
            if (code.startsWith("E11")) {
              diabetes = true;
            } else if (code.equals("I10")) {
              hypertension = true;
            }
          }
          for (JsonNode item : r.path("item")) {
            if (MAMMOGRAPHY_CPT.contains(firstCode(item.path("productOrService"))) && item.hasNonNull("servicedDate")) {
              LocalDate d = LocalDate.parse(item.path("servicedDate").asText());
              // Look-back: from 1 October two years before the measurement year.
              if (!d.isBefore(LocalDate.of(year - 2, 10, 1)) && !d.isAfter(end)) {
                mammoId = id;
              }
            }
          }
        }
        case "Observation" -> {
          String code = firstCode(r.path("code"));
          LocalDate when = date(r.path("effectiveDateTime").asText(""));
          if (when == null || when.isBefore(start) || when.isAfter(end)) {
            continue;
          }
          if (code.equals("4548-4") && (latestA1cDate == null || when.isAfter(latestA1cDate))) {
            latestA1c = r;
            latestA1cDate = when;
            latestA1cId = id;
          } else if (code.equals("85354-9") && (latestBpDate == null || when.isAfter(latestBpDate))) {
            latestBp = r;
            latestBpDate = when;
            latestBpId = id;
          }
        }
        default -> { }
      }
    }

    List<Result> out = new ArrayList<>();
    if (dob == null) {
      return out;
    }
    int age = end.getYear() - dob.getYear() - (end.getDayOfYear() < dob.getDayOfYear() ? 1 : 0);

    if (diabetes && age >= 18 && age <= 75) {
      if (latestA1c == null) {
        out.add(new Result("DM-A1C-POOR-CONTROL", true, true, "no HbA1c result in " + year, dxEvidence));
      } else {
        double v = latestA1c.path("valueQuantity").path("value").asDouble();
        out.add(new Result("DM-A1C-POOR-CONTROL", v > 9.0, true,
            "most recent HbA1c " + v + "% on " + latestA1cDate, List.of(latestA1cId)));
      }
    }
    if (hypertension && age >= 18 && age <= 85) {
      if (latestBp == null) {
        out.add(new Result("HTN-BP-CONTROL", false, false, "no blood pressure reading in " + year, dxEvidence));
      } else {
        double sys = component(latestBp, "8480-6");
        double dia = component(latestBp, "8462-4");
        out.add(new Result("HTN-BP-CONTROL", sys < 140 && dia < 90, false,
            "most recent BP " + (int) sys + "/" + (int) dia + " on " + latestBpDate, List.of(latestBpId)));
      }
    }
    if ("female".equals(sex) && age >= 52 && age <= 74) {
      out.add(new Result("BCS-MAMMOGRAPHY", mammoId != null, false,
          mammoId != null ? "mammography claim found in look-back window" : "no mammography claim since October " + (year - 2),
          mammoId != null ? List.of(mammoId) : List.of()));
    }
    return out;
  }

  public static String toJson(String eid, int year, Result r) {
    ObjectNode n = JSON.createObjectNode();
    n.put("eid", eid);
    n.put("measure_id", r.measureId());
    n.put("measure_version", VERSION);
    n.put("period_start", year + "-01-01");
    n.put("period_end", year + "-12-31");
    n.put("denominator", true);
    n.put("numerator", r.numerator());
    n.put("inverse", r.inverse());
    n.put("detail", r.detail());
    r.evidence().forEach(n.putArray("evidence")::add);
    return n.toString();
  }

  private static String firstCode(JsonNode codeableConcept) {
    return codeableConcept.path("coding").path(0).path("code").asText("");
  }

  private static double component(JsonNode obs, String loinc) {
    for (JsonNode c : obs.path("component")) {
      if (loinc.equals(firstCode(c.path("code")))) {
        return c.path("valueQuantity").path("value").asDouble();
      }
    }
    return Double.NaN;
  }

  private static LocalDate date(String s) {
    return s.length() >= 10 ? LocalDate.parse(s.substring(0, 10)) : null;
  }
}
