package com.cdap.harmonizer;

import com.cdap.harmonizer.Converters.Converted;
import com.cdap.harmonizer.Converters.Demographics;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Iterator;
import java.util.Map;
import org.apache.beam.sdk.transforms.DoFn;
import org.apache.beam.sdk.values.TupleTag;
import org.hl7.fhir.r4.model.Resource;

/**
 * Raw envelope in, harmonized FHIR resources out.
 *
 * <p>Every output line is a FHIR R4 resource wrapped with its provenance: the enterprise ID it
 * belongs to, the source system and tenant, and the ingest ID and hash of the raw payload it was
 * derived from. That wrapper is what makes lineage a lookup instead of an investigation.
 *
 * <p>A payload that cannot be converted, or whose patient cannot be resolved, goes to the
 * dead-letter output with the reason. Nothing is dropped silently.
 */
public class HarmonizeFn extends DoFn<String, String> {
  public static final String PIPELINE_VERSION = "harmonizer-0.1.0";
  public static final TupleTag<String> RESOURCES = new TupleTag<>() {};
  public static final TupleTag<String> DEAD_LETTER = new TupleTag<>() {};
  private static final ObjectMapper JSON = new ObjectMapper();

  private final String identityUrl;
  private transient HttpClient http;

  public HarmonizeFn(String identityUrl) {
    this.identityUrl = identityUrl;
  }

  @Setup
  public void setup() {
    http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
  }

  @ProcessElement
  public void process(@Element String line, MultiOutputReceiver out) {
    JsonNode env;
    try {
      env = JSON.readTree(line);
    } catch (Exception e) {
      out.get(DEAD_LETTER).output(deadLetter(null, "unparseable envelope", null));
      return;
    }
    try {
      String source = env.path("source_system").asText();
      Converted c = Converters.convert(env.path("kind").asText(), env.path("payload").asText());
      String eid = resolve(source, c.demographics());
      for (Resource r : c.resources()) {
        out.get(RESOURCES).output(wrap(env, eid, r));
      }
    } catch (Exception e) {
      // The reason may be logged and stored; the payload stays in the raw zone only.
      out.get(DEAD_LETTER).output(deadLetter(env, e.getClass().getSimpleName(), e.getMessage()));
    }
  }

  private String resolve(String source, Demographics d) throws Exception {
    ObjectNode body = JSON.createObjectNode();
    body.put("source_system", source);
    body.putArray("identifiers").addObject().put("system", d.idSystem()).put("value", d.idValue());
    body.put("first_name", d.first()).put("last_name", d.last()).put("birth_date", d.birthDate());
    putIfPresent(body, "sex", d.sex());
    putIfPresent(body, "ssn_last4", d.ssnLast4());
    putIfPresent(body, "phone", d.phone());
    putIfPresent(body, "address_line1", d.addressLine1());
    putIfPresent(body, "postal_code", d.postalCode());
    HttpRequest req = HttpRequest.newBuilder(URI.create(identityUrl + "/v1/identity/resolve"))
        .timeout(Duration.ofSeconds(10)).header("Content-Type", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(JSON.writeValueAsString(body))).build();

    Exception last = null;
    for (int attempt = 0; attempt < 4; attempt++) {
      try {
        HttpResponse<String> resp = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (resp.statusCode() == 200) {
          return JSON.readTree(resp.body()).path("eid").asText();
        }
        if (resp.statusCode() < 500 && resp.statusCode() != 429) {
          throw new IllegalStateException("identity service rejected record: " + resp.statusCode());
        }
        last = new IllegalStateException("identity service " + resp.statusCode());
      } catch (java.io.IOException e) {
        last = e;
      }
      Thread.sleep(200L << attempt);
    }
    throw new IllegalStateException("identity service unavailable: " + last.getMessage());
  }

  private static void putIfPresent(ObjectNode n, String k, String v) {
    if (v != null && !v.isEmpty()) {
      n.put(k, v);
    }
  }

  /** Encode with HAPI, then namespace the ID, point at the EID and stamp provenance. */
  static String wrap(JsonNode env, String eid, Resource r) throws Exception {
    String source = env.path("source_system").asText();
    ObjectNode res = (ObjectNode) JSON.readTree(Converters.parser().encodeResourceToString(r));
    String localId = r.getIdElement().getIdPart();
    String id = (source + "." + localId).replaceAll("[^A-Za-z0-9.\\-]", "-");
    res.put("id", id);
    rewriteSubject(res, "Patient/" + eid);
    if ("Patient".equals(res.path("resourceType").asText())) {
      ((ArrayNode) res.withArray("identifier")).addObject().put("system", "urn:cdap:eid").put("value", eid);
    }
    ObjectNode meta = res.withObject("/meta");
    meta.put("source", "urn:cdap:source:" + source + "#" + env.path("ingest_id").asText());

    ObjectNode w = JSON.createObjectNode();
    w.put("eid", eid);
    w.put("source_system", source);
    w.put("tenant", env.path("tenant").asText());
    w.put("ingest_id", env.path("ingest_id").asText());
    w.put("raw_sha256", env.path("sha256").asText());
    w.put("resource_type", res.path("resourceType").asText());
    w.put("resource_id", id);
    w.put("pipeline_version", PIPELINE_VERSION);
    w.set("resource", res);
    return JSON.writeValueAsString(w);
  }

  private static void rewriteSubject(JsonNode node, String ref) {
    if (node.isObject()) {
      ObjectNode obj = (ObjectNode) node;
      JsonNode r = obj.get("reference");
      if (r != null && r.isTextual() && r.asText().startsWith("Patient/")) {
        obj.put("reference", ref);
      }
      for (Iterator<Map.Entry<String, JsonNode>> it = obj.fields(); it.hasNext(); ) {
        rewriteSubject(it.next().getValue(), ref);
      }
    } else if (node.isArray()) {
      node.forEach(n -> rewriteSubject(n, ref));
    }
  }

  private static String deadLetter(JsonNode env, String error, String detail) {
    ObjectNode n = JSON.createObjectNode();
    if (env != null) {
      n.put("ingest_id", env.path("ingest_id").asText());
      n.put("source_system", env.path("source_system").asText());
      n.put("kind", env.path("kind").asText());
    }
    n.put("error", error);
    n.put("detail", detail);
    n.put("pipeline_version", PIPELINE_VERSION);
    return n.toString();
  }
}
