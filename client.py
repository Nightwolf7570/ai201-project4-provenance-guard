import argparse
import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_BASE_URL = "http://127.0.0.1:5000"


def print_json(data):
    print(json.dumps(data, indent=2))


def request_json(base_url, path, method="GET", payload=None):
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read()), response.status
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        try:
            return json.loads(body), error.code
        except json.JSONDecodeError:
            return {"error": body}, error.code
    except urllib.error.URLError as error:
        return {
            "error": "Could not connect to the API.",
            "detail": str(error.reason),
            "hint": "Start the server with: source .venv/bin/activate && python app.py",
        }, 0


def read_text(args):
    if args.file:
        with open(args.file, "r", encoding="utf-8") as text_file:
            return text_file.read()
    return args.text


def submit(args):
    text = read_text(args)
    if not text:
        print("Provide text with --text or --file.", file=sys.stderr)
        return 2

    body, status = request_json(
        args.base_url,
        "/submit",
        method="POST",
        payload={"creator_id": args.creator_id, "text": text},
    )
    print_json(body)
    return 0 if 200 <= status < 300 else 1


def appeal(args):
    body, status = request_json(
        args.base_url,
        "/appeal",
        method="POST",
        payload={
            "content_id": args.content_id,
            "creator_reasoning": args.reason,
        },
    )
    print_json(body)
    return 0 if 200 <= status < 300 else 1


def log(args):
    body, status = request_json(args.base_url, "/log")
    entries = body.get("entries", [])
    if args.limit is not None:
        body["entries"] = entries[-args.limit:]
    print_json(body)
    return 0 if 200 <= status < 300 else 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="Small CLI client for the Provenance Guard API."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("PROVENANCE_GUARD_URL", DEFAULT_BASE_URL),
        help=f"API base URL. Defaults to {DEFAULT_BASE_URL}.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    submit_parser = subparsers.add_parser("submit", help="Submit text for analysis.")
    submit_parser.add_argument("--creator-id", required=True)
    text_source = submit_parser.add_mutually_exclusive_group(required=True)
    text_source.add_argument("--text")
    text_source.add_argument("--file")
    submit_parser.set_defaults(func=submit)

    appeal_parser = subparsers.add_parser("appeal", help="Appeal a classification.")
    appeal_parser.add_argument("--content-id", required=True)
    appeal_parser.add_argument("--reason", required=True)
    appeal_parser.set_defaults(func=appeal)

    log_parser = subparsers.add_parser("log", help="Show audit log entries.")
    log_parser.add_argument("--limit", type=int)
    log_parser.set_defaults(func=log)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
