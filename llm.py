import os, re, json, time, logging, requests
from dotenv import load_dotenv
load_dotenv()
log = logging.getLogger("llm")

_BASE_URL = "https://integrate.api.nvidia.com/v1"
_PLACEHOLDERS = {"your-nvidia-nim-key", "your-key", "", None}

# Models known to be reachable on a standard NVIDIA NIM account. Tried in order
# when the configured model is unavailable for the current account/key.
_FALLBACK_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "mistralai/mistral-large-4-1t-a52b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
]
_working_model = None


def _get_key():
    key = os.getenv("NVIDIA_API_KEY")
    if key in _PLACEHOLDERS:
        raise RuntimeError(
            "NVIDIA_API_KEY is not configured. "
            "Get a free key at https://build.nvidia.com (sign in, click 'Get API Key' on any model page), "
            "then set NVIDIA_API_KEY=nvapi-... in your .env file."
        )
    return key


def _candidate_models():
    global _working_model
    if _working_model:
        return [_working_model]
    primary = os.getenv("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")
    out = [primary]
    for m in _FALLBACK_MODELS:
        if m not in out:
            out.append(m)
    return out


def _extract_content(data):
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    content = msg.get("content")
    if content is None:
        content = msg.get("reasoning_content")
    return (content or "").strip()


def _is_model_unavailable(status, text):
    if status == 404:
        return True
    t = (text or "").lower()
    return "not found" in t or "not_found" in t or "model not found" in t


def complete(system, user, max_tokens=3000):
    global _working_model
    key = _get_key()
    url = f"{_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    last_err = None
    for model in _candidate_models():
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "top_p": 0.7,
            "stream": False,
        }
        for i in range(2):
            try:
                r = requests.post(url, headers=headers, json=payload, timeout=180)
                if r.status_code in (401, 403):
                    raise RuntimeError(
                        f"NVIDIA NIM auth error ({r.status_code}): your NVIDIA_API_KEY is invalid or expired. "
                        f"Get a new key at https://build.nvidia.com. Response: {r.text[:300]}"
                    )
                if _is_model_unavailable(r.status_code, r.text):
                    log.warning("Model %s unavailable (%d): %s", model, r.status_code, r.text[:200])
                    last_err = RuntimeError(f"NVIDIA NIM model unavailable ({r.status_code}): {r.text[:300]}")
                    break
                if r.status_code == 422:
                    raise RuntimeError(f"NVIDIA NIM bad request (422): {r.text[:400]}")
                if r.status_code in (429, 500, 502, 503, 504):
                    log.warning("NVIDIA NIM transient error (%d), retry %d: %s", r.status_code, i + 1, r.text[:200])
                    time.sleep(2 ** i)
                    continue
                r.raise_for_status()
                data = r.json()
                content = _extract_content(data)
                if not content:
                    # Fall back to trying another model if this one returned empty content.
                    last_err = RuntimeError(f"NVIDIA NIM returned empty content for model {model}")
                    break
                _working_model = model
                return content
            except RuntimeError:
                raise
            except requests.RequestException as e:
                last_err = e
                log.warning("LLM network error (attempt %d/2) for %s: %s", i + 1, model, e)
                time.sleep(2 ** i)
        # end retry loop for this model
    raise RuntimeError(f"LLM unavailable after trying all models. Last error: {last_err}")


def complete_json(system, user, max_tokens=3000):
    t = complete(system + "\nReturn ONLY valid JSON, no prose or code fences.", user, max_tokens)
    m = re.search(r"[\[{].*[\]}]", t, re.S)
    if not m:
        raise ValueError(f"LLM did not return valid JSON. Response was: {t[:300]}")
    return json.loads(m.group(0))