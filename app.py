import json
import os
import re
import string
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from groq import Groq


load_dotenv()

app = Flask(__name__)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

AUDIT_LOG_PATH = Path("audit_log.jsonl")
CONTENT_RECORDS_PATH = Path("content_records.json")
GROQ_MODEL = "llama-3.3-70b-versatile"
AI_STYLE_PHRASES = [
    "certainly",
    "it is important to note",
    "furthermore",
    "in conclusion",
    "as an ai",
    "delve",
    "transformative",
]
LABEL_TEXT = {
    "likely_ai": "Provenance Guard found strong signals that this text may have been AI-generated. This label is based on automated analysis and may be appealed by the creator.",
    "likely_human": "Provenance Guard found strong signals that this text is likely human-written. This label reflects automated analysis and is not a guarantee of authorship.",
    "uncertain": "Provenance Guard could not confidently determine whether this text was human-written or AI-generated. Readers should treat the attribution as uncertain.",
}


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat()


def clamp_score(value):
    return max(0.0, min(1.0, float(value)))


def map_score_to_attribution(score):
    if score >= 0.75:
        return "likely_ai"
    if score < 0.40:
        return "likely_human"
    return "uncertain"


def generate_label(attribution):
    return LABEL_TEXT[attribution]


def combine_signal_scores(llm_score, heuristic_score):
    return clamp_score(((66 * llm_score) + (33 * heuristic_score)) / 99)


def split_sentences(text):
    sentences = re.split(r"[.!?]+", text)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def tokenize_words(text):
    return re.findall(r"[a-zA-Z']+", text.lower())


def calculate_variance(values):
    if len(values) < 2:
        return 0.0

    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def calculate_heuristic_signal(text):
    lower_text = text.lower()
    sentences = split_sentences(text)
    words = tokenize_words(text)
    word_count = len(words)

    sentence_lengths = [len(tokenize_words(sentence)) for sentence in sentences]
    mean_sentence_length = (
        sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0.0
    )
    sentence_length_variance = calculate_variance(sentence_lengths)
    sentence_length_stddev = sentence_length_variance**0.5
    coefficient_of_variation = (
        sentence_length_stddev / mean_sentence_length
        if mean_sentence_length
        else 1.0
    )
    uniformity_score = clamp_score(1 - (coefficient_of_variation / 0.9))

    unique_words = set(words)
    type_token_ratio = len(unique_words) / word_count if word_count else 0.0
    vocabulary_repetition_score = (
        0.5 if word_count < 20 else clamp_score((0.78 - type_token_ratio) / 0.38)
    )

    punctuation_count = sum(1 for char in text if char in string.punctuation)
    punctuation_density = punctuation_count / len(text) if text else 0.0
    punctuation_score = clamp_score(1 - abs(punctuation_density - 0.035) / 0.035)

    ai_phrase_hits = [
        phrase for phrase in AI_STYLE_PHRASES if phrase in lower_text
    ]
    ai_phrase_score = clamp_score(len(ai_phrase_hits) / 3)

    score = clamp_score(
        (0.25 * uniformity_score)
        + (0.25 * vocabulary_repetition_score)
        + (0.05 * punctuation_score)
        + (0.45 * ai_phrase_score)
    )

    if word_count < 20:
        score = (score + 0.5) / 2

    return {
        "score": round(score, 3),
        "metrics": {
            "word_count": word_count,
            "sentence_count": len(sentences),
            "sentence_length_variance": round(sentence_length_variance, 3),
            "type_token_ratio": round(type_token_ratio, 3),
            "punctuation_density": round(punctuation_density, 3),
            "ai_phrase_hits": ai_phrase_hits,
        },
    }


def call_groq_signal(text):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "your_key_here":
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = Groq(api_key=api_key)
    prompt = f"""
You are part of Provenance Guard, a cautious AI-attribution backend.

Analyze the submitted writing and return only valid JSON with:
- score: a number from 0.0 to 1.0, where 0.0 means strongly human-like and 1.0 means strongly AI-like
- reasoning: one short sentence explaining the score

Be conservative. Formal human writing can look AI-like, so avoid high AI scores unless the text has strong signals.
Use scores above 0.75 only when there are multiple strong AI-like signals, such as generic balanced structure, assistant-like transitions, or phrases like "it is important to note", "furthermore", "certainly", "in conclusion", "delve", or "transformative".
Use scores from 0.40 to 0.74 for mixed or uncertain evidence.
Use scores below 0.40 when the text has specific personal detail, uneven human rhythm, casual phrasing, or clear individual voice.

Text:
\"\"\"{text}\"\"\"
"""

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {
                "role": "system",
                "content": "Return only valid JSON. Do not include markdown.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=300,
        response_format={"type": "json_object"},
    )

    raw_content = response.choices[0].message.content
    parsed = json.loads(raw_content)
    score = clamp_score(parsed.get("score", 0.5))
    reasoning = str(parsed.get("reasoning", "No reasoning returned."))

    return {
        "score": score,
        "reasoning": reasoning,
    }


def write_audit_entry(entry):
    with AUDIT_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(entry) + "\n")


def read_audit_entries(limit=25):
    if not AUDIT_LOG_PATH.exists():
        return []

    with AUDIT_LOG_PATH.open("r", encoding="utf-8") as log_file:
        lines = log_file.readlines()

    entries = []
    for line in lines[-limit:]:
        if line.strip():
            entries.append(json.loads(line))
    return entries


def read_content_records():
    if not CONTENT_RECORDS_PATH.exists():
        return {}

    with CONTENT_RECORDS_PATH.open("r", encoding="utf-8") as records_file:
        return json.load(records_file)


def write_content_records(records):
    with CONTENT_RECORDS_PATH.open("w", encoding="utf-8") as records_file:
        json.dump(records, records_file, indent=2)


def save_content_record(content_id, record):
    records = read_content_records()
    records[content_id] = record
    write_content_records(records)


@app.errorhandler(429)
def rate_limit_error(error):
    return (
        jsonify(
            {
                "error": "Rate limit exceeded.",
                "message": str(error.description),
            }
        ),
        429,
    )


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    creator_id = data.get("creator_id")
    text = data.get("text")

    if not creator_id or not text:
        return (
            jsonify(
                {
                    "error": "Missing required fields.",
                    "required_fields": ["creator_id", "text"],
                }
            ),
            400,
        )

    content_id = str(uuid.uuid4())
    llm_result = call_groq_signal(text)
    llm_score = llm_result["score"]
    heuristic_result = calculate_heuristic_signal(text)
    heuristic_score = heuristic_result["score"]
    combined_score = round(combine_signal_scores(llm_score, heuristic_score), 3)
    attribution = map_score_to_attribution(combined_score)
    label = generate_label(attribution)

    response_body = {
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": combined_score,
        "label": label,
        "signals": {
            "llm_score": llm_score,
            "llm_reasoning": llm_result["reasoning"],
            "heuristic_score": heuristic_score,
            "heuristic_metrics": heuristic_result["metrics"],
        },
        "status": "classified",
    }

    save_content_record(content_id, response_body)

    audit_entry = {
        "event_type": "classification",
        "timestamp": utc_timestamp(),
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": combined_score,
        "llm_score": llm_score,
        "heuristic_score": heuristic_score,
        "heuristic_metrics": heuristic_result["metrics"],
        "label": label,
        "status": "classified",
    }
    write_audit_entry(audit_entry)

    return jsonify(response_body)


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not creator_reasoning:
        return (
            jsonify(
                {
                    "error": "Missing required fields.",
                    "required_fields": ["content_id", "creator_reasoning"],
                }
            ),
            400,
        )

    records = read_content_records()
    original_record = records.get(content_id)
    if not original_record:
        return jsonify({"error": "Content ID not found."}), 404

    original_record["status"] = "under_review"
    original_record["appeal_reasoning"] = creator_reasoning
    records[content_id] = original_record
    write_content_records(records)

    appeal_entry = {
        "event_type": "appeal",
        "timestamp": utc_timestamp(),
        "content_id": content_id,
        "creator_id": original_record["creator_id"],
        "original_attribution": original_record["attribution"],
        "original_confidence": original_record["confidence"],
        "llm_score": original_record["signals"]["llm_score"],
        "heuristic_score": original_record["signals"]["heuristic_score"],
        "creator_reasoning": creator_reasoning,
        "status": "under_review",
    }
    write_audit_entry(appeal_entry)

    return jsonify(
        {
            "content_id": content_id,
            "status": "under_review",
            "message": "Appeal received. The content is now under review.",
        }
    )


@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": read_audit_entries()})


@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"service": "Provenance Guard", "status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5001)
