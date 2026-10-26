"""Shared Claude API helper: streaming call with server-side refusal fallback, retries and cost log."""
import json, os, time
from pathlib import Path
import anthropic

MODEL = os.environ["CLAUDE_MODEL"]          # model id is supplied by the caller
PRICE_IN, PRICE_OUT, PRICE_CACHE_READ, PRICE_CACHE_WRITE = 4.0, 20.0, 0.20, 5.0   # USD per MTok
USAGE_LOG = Path(__file__).resolve().parent.parent / "usage.jsonl"

_client = anthropic.Anthropic(api_key=open(os.environ["KEY_FILE"]).read().strip(), max_retries=4)


class Refused(Exception):
    pass


def call(system, content, *, tag, effort="medium", max_tokens=64000, attempts=4):
    """Return the text of one Claude response. `system` may be a str or a list of blocks; `content` a str or block list."""
    for attempt in range(1, attempts + 1):
        try:
            with _client.beta.messages.stream(
                model=MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={"effort": effort},
                betas=["server-side-fallback-2026-07-01"],
                extra_body={"fallbacks": "default"},
            ) as stream:
                msg = stream.get_final_message()
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            wait = 15 * attempt
            print(f"[{tag}] transient error {type(e).__name__}, retry in {wait}s", flush=True)
            time.sleep(wait)
            continue
        u = msg.usage
        cost = (u.input_tokens * PRICE_IN + u.output_tokens * PRICE_OUT
                + (u.cache_read_input_tokens or 0) * PRICE_CACHE_READ
                + (u.cache_creation_input_tokens or 0) * PRICE_CACHE_WRITE) / 1e6
        with open(USAGE_LOG, "a") as f:
            f.write(json.dumps({"tag": tag, "model": msg.model, "stop": msg.stop_reason, "in": u.input_tokens,
                                "out": u.output_tokens, "cache_read": u.cache_read_input_tokens,
                                "cache_write": u.cache_creation_input_tokens, "usd": round(cost, 4)}) + "\n")
        if msg.stop_reason == "refusal":
            raise Refused(f"[{tag}] refused: {getattr(msg, 'stop_details', None)}")
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason == "max_tokens":
            print(f"[{tag}] hit max_tokens, attempt {attempt}", flush=True)
            if attempt < attempts:
                continue
        return text
    raise RuntimeError(f"[{tag}] failed after {attempts} attempts")


def total_cost():
    if not USAGE_LOG.exists():
        return 0.0
    return sum(json.loads(l)["usd"] for l in open(USAGE_LOG))
