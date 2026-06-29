# Provenance Guard Plan

## Summary

Provenance Guard is a Flask backend that accepts text, runs two AI-attribution signals, combines them into an AI-likeness score, returns a reader transparency label, logs the decision, and lets creators appeal. The design is conservative: false positives against human writers are worse than false negatives, so uncertain cases stay uncertain.

Scores mean AI-likeness: `0.0` is strongly human-like, `1.0` is strongly AI-like. Thresholds: `likely_ai >= 0.75`, `uncertain >= 0.40 and < 0.75`, `likely_human < 0.40`.

## Detection and Scoring

Signal 1 is Groq LLM classification using `llama-3.3-70b-versatile`. It captures holistic style, tone, generic phrasing, and semantic flow. Blind spot: formal or polished human writing may look AI-generated.

Signal 2 is Python stylometric heuristics. It checks sentence-length uniformity, vocabulary diversity, punctuation density, and AI-style phrases such as `certainly`, `it is important to note`, `furthermore`, `in conclusion`, `as an AI`, `delve`, and `transformative`. Blind spot: poems, short text, non-native English writing, and academic prose can trigger misleading scores.

Both signals return `0.0` to `1.0`. Groq is weighted 66 and heuristics 33:

```text
combined_score = ((66 * llm_score) + (33 * heuristic_score)) / 99
```

## API and Labels

`POST /submit` accepts:

```json
{ "creator_id": "test-user-1", "text": "submitted text" }
```

It returns `content_id`, `creator_id`, `attribution`, `confidence`, `label`, `signals`, and `status: classified`.

`POST /appeal` accepts:

```json
{ "content_id": "uuid", "creator_reasoning": "I wrote this myself..." }
```

It updates the content to `under_review`, logs the appeal, and returns confirmation. `GET /log` returns recent structured audit entries.

Exact label text:

| Variant | Text |
| --- | --- |
| High-confidence AI | "Provenance Guard found strong signals that this text may have been AI-generated. This label is based on automated analysis and may be appealed by the creator." |
| High-confidence human | "Provenance Guard found strong signals that this text is likely human-written. This label reflects automated analysis and is not a guarantee of authorship." |
| Uncertain | "Provenance Guard could not confidently determine whether this text was human-written or AI-generated. Readers should treat the attribution as uncertain." |

## Appeals, Logging, and Rate Limits

An appeal can be submitted by any creator with a valid `content_id`. The creator provides reasoning; the system keeps the original classification, changes status to `under_review`, and logs an appeal event. A reviewer would need content ID, creator ID, original attribution, confidence, signal scores, label, reasoning, and status.

Audit logs use structured JSONL in `audit_log.jsonl`. Classification entries include timestamp, content ID, creator ID, attribution, confidence, LLM score, heuristic score, and status. Appeal entries include timestamp, content ID, creator reasoning, and `status: under_review`.

Rate limit `POST /submit` with `10 per minute;100 per day` using Flask-Limiter memory storage. This fits normal creator use while blocking rapid scripts and limiting Groq API abuse.

## Architecture

```text
POST /submit {creator_id, text}
  -> Flask API
  -> Groq LLM signal -> llm_score
  -> heuristic signal -> heuristic_score
  -> weighted confidence score
  -> threshold attribution
  -> transparency label
  -> audit log
  -> JSON response

POST /appeal {content_id, creator_reasoning}
  -> Flask API
  -> find original decision
  -> status = under_review
  -> audit log
  -> JSON response
```

## Edge Cases

Very short text may not provide enough evidence. Poetry with repetition may look AI-like. Academic human writing may trigger formal AI-style phrases. Lightly edited AI output may avoid obvious markers. Non-native English writing may appear unusually formal, so appeals are required.

## AI Tool Plan

M3: Provide Detection and Scoring plus Architecture. Ask for Flask skeleton, `POST /submit`, Groq signal, simple audit log, and `GET /log`. Verify one submission and one log entry.

M4: Provide Detection and Scoring plus thresholds. Ask for heuristic signal, phrase checks, weighted scoring, and attribution mapping. Verify four inputs: AI-like, human-like, formal borderline, and edited AI.

M5: Provide API and Labels plus Appeals, Logging, and Rate Limits. Ask for exact label function, `POST /appeal`, full audit entries, and Flask-Limiter. Verify all three labels, one appeal, three log entries, and `429` rate-limit responses.
