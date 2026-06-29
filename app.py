import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from groq import Groq


load_dotenv()

app = Flask(__name__)

AUDIT_LOG_PATH = Path("audit_log.jsonl")
GROQ_MODEL = "llama-3.3-70b-versatile"


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


@app.route("/submit", methods=["POST"])
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
    attribution = map_score_to_attribution(llm_score)

    response_body = {
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": llm_score,
        "label": "Placeholder label: final transparency labels will be added in Milestone 5.",
        "signals": {
            "llm_score": llm_score,
            "llm_reasoning": llm_result["reasoning"],
        },
        "status": "classified",
    }

    audit_entry = {
        "event_type": "classification",
        "timestamp": utc_timestamp(),
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": llm_score,
        "llm_score": llm_score,
        "status": "classified",
    }
    write_audit_entry(audit_entry)

    return jsonify(response_body)


@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": read_audit_entries()})


@app.route("/", methods=["GET"])
def health_check():
    return jsonify({"service": "Provenance Guard", "status": "ok"})


if __name__ == "__main__":
    app.run(debug=True)
