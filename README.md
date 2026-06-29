# Provenance Guard

Provenance Guard is a Flask backend for CodePath AI201 Project 4. It analyzes submitted text for AI-attribution signals, returns a confidence-aware transparency label, supports creator appeals, rate limits submissions, and records structured audit logs.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env`:

```bash
GROQ_API_KEY=your_groq_key_here
```

Start the API:

```bash
python app.py
```

The local server runs on `http://127.0.0.1:5001`.

Main endpoints:

```text
POST /submit
POST /appeal
GET /log
```

You can test the app with the included CLI helper instead of writing raw `curl`:

```bash
python client.py submit "Certainly, it is important to note that AI has transformative effects."
python client.py log --limit 3
python client.py appeal --content-id PASTE_CONTENT_ID_HERE --reason "I wrote this myself and want a review."
```

## Architecture Overview

A submission enters `POST /submit` with `creator_id` and `text`. Flask validates the request, creates a `content_id`, sends the text to the Groq signal, runs local stylometric heuristics, combines both scores, maps the combined score to an attribution result, generates a transparency label, stores the current content record, writes an audit log entry, and returns JSON.

Appeals enter `POST /appeal` with `content_id` and `creator_reasoning`. The API finds the original decision, updates the content status to `under_review`, writes an appeal audit entry, and returns confirmation.

```text
POST /submit -> Groq signal -> heuristic signal -> weighted score
             -> attribution -> transparency label -> audit log -> response

POST /appeal -> find original decision -> status under_review
             -> appeal audit log -> response
```

## Detection Signals

Signal 1 is Groq LLM classification using `llama-3.3-70b-versatile`. It captures holistic style, tone, semantic flow, generic phrasing, and assistant-like structure. I chose it because it can notice writing qualities that simple metrics miss. Its blind spot is that formal or polished human writing may look AI-generated.

Signal 2 is local stylometric heuristics. It checks sentence-length uniformity, vocabulary diversity, punctuation density, and AI-style phrases such as `certainly`, `it is important to note`, `furthermore`, `in conclusion`, `as an AI`, `delve`, and `transformative`. I chose it because it is explainable and independent from the LLM. Its blind spot is that poetry, academic prose, very short text, and non-native English writing can trigger misleading scores.

## Confidence Scoring

Scores mean AI-likeness: `0.0` is strongly human-like and `1.0` is strongly AI-like. The system is conservative because a false positive against a human creator is the biggest harm.

```text
combined_score = ((66 * llm_score) + (33 * heuristic_score)) / 99
```

Thresholds:

```text
likely_ai:     >= 0.75
uncertain:     >= 0.40 and < 0.75
likely_human:  < 0.40
```

Testing showed meaningful score variation:

| Example | LLM | Heuristic | Combined | Attribution |
| --- | ---: | ---: | ---: | --- |
| AI-like text with "it is important to note", "furthermore", and "transformative" | 0.85 | 0.617 | 0.772 | `likely_ai` |
| Casual ramen review with personal voice | 0.20 | 0.105 | 0.168 | `likely_human` |
| Formal monetary policy paragraph | 0.82 | 0.188 | 0.609 | `uncertain` |

The formal paragraph is intentionally uncertain: the LLM found it AI-like, but the heuristic did not find phrase markers or strong structural evidence.

## Transparency Labels

| Variant | Exact label text |
| --- | --- |
| High-confidence AI | "Provenance Guard found strong signals that this text may have been AI-generated. This label is based on automated analysis and may be appealed by the creator." |
| High-confidence human | "Provenance Guard found strong signals that this text is likely human-written. This label reflects automated analysis and is not a guarantee of authorship." |
| Uncertain | "Provenance Guard could not confidently determine whether this text was human-written or AI-generated. Readers should treat the attribution as uncertain." |

## Appeals Workflow

Creators can appeal with `content_id` and `creator_reasoning`. The system keeps the original decision, updates the content status to `under_review`, and logs the appeal with the original score details.

Test response:

```json
{
  "content_id": "9656d173-e0c3-4aa4-b2a3-c0dd4df13bc0",
  "message": "Appeal received. The content is now under review.",
  "status": "under_review"
}
```

## Rate Limiting

`POST /submit` is limited to:

```text
10 per minute;100 per day
```

Reasoning: a normal writer is unlikely to submit more than 10 pieces in one minute or 100 in one day. This keeps the project usable while blocking simple scripts and reducing Groq API abuse.

Rate-limit test output from 12 rapid requests:

```text
200
200
200
200
200
200
200
200
200
200
429
429
```

## Audit Log

The audit log is structured JSONL in `audit_log.jsonl` and is exposed through `GET /log`. Runtime logs are ignored by Git, so this README includes sample evidence.

Sample entries:

```json
[
  {
    "event_type": "classification",
    "content_id": "bb305ea4-3606-43c5-aeff-d8884540815a",
    "creator_id": "m5-likely-ai",
    "attribution": "likely_ai",
    "confidence": 0.772,
    "llm_score": 0.85,
    "heuristic_score": 0.617,
    "status": "classified"
  },
  {
    "event_type": "classification",
    "content_id": "9656d173-e0c3-4aa4-b2a3-c0dd4df13bc0",
    "creator_id": "m5-uncertain",
    "attribution": "uncertain",
    "confidence": 0.609,
    "llm_score": 0.82,
    "heuristic_score": 0.188,
    "status": "classified"
  },
  {
    "event_type": "classification",
    "content_id": "68eeabc9-a376-498d-8df2-bb67763da6ac",
    "creator_id": "m5-human",
    "attribution": "likely_human",
    "confidence": 0.168,
    "llm_score": 0.20,
    "heuristic_score": 0.105,
    "status": "classified"
  },
  {
    "event_type": "appeal",
    "content_id": "9656d173-e0c3-4aa4-b2a3-c0dd4df13bc0",
    "creator_id": "m5-uncertain",
    "original_attribution": "uncertain",
    "original_confidence": 0.609,
    "llm_score": 0.82,
    "heuristic_score": 0.188,
    "creator_reasoning": "I wrote this myself from personal experience. I am a non-native English speaker and my writing style may appear more formal than typical.",
    "status": "under_review"
  }
]
```

## Known Limitations

Very short submissions are hard to score because the heuristic signal has little text to measure. Poetry with repetition may look AI-like because repeated phrases lower vocabulary diversity. Academic human writing may trigger the LLM because formal balanced paragraphs resemble generated text. Lightly edited AI output may avoid obvious phrase markers and score too human-like.

## Spec Reflection

The spec helped by forcing the confidence score meaning before implementation. Deciding that scores mean AI-likeness made the thresholds, labels, and audit log easier to build consistently.

One implementation detail diverged from the first simple plan: I added `content_records.json` in addition to `audit_log.jsonl`. The audit log is still the evidence trail, but appeals needed a current-status store so a content item could actually become `under_review`.

## AI Usage

I used AI assistance to turn the project requirements into a short `planning.md` with the architecture, conservative thresholds, signal choices, and label text. I revised the plan to use a 66/33 Groq-to-heuristic weighting and added AI-style phrase markers like `certainly`.

I also used AI assistance to generate and refine the Flask implementation. I reviewed and changed the scoring behavior after testing because the first Groq prompt was too conservative for obvious AI-style text; I kept the `0.75` threshold but calibrated the prompt and heuristic phrase weight.

## Walkthrough Video Notes

For the short portfolio walkthrough, show:

1. `planning.md`: point out the two-signal design and conservative thresholds.
2. `app.py`: show `POST /submit`, scoring, labels, `POST /appeal`, and rate limiting.
3. Terminal: run one `/submit`, one `/appeal`, and `GET /log`.
4. Explain the key decision: uncertain is a real result because protecting human creators from false positives matters.
