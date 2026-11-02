import os, re, json, time, logging, requests
from dotenv import load_dotenv
load_dotenv()
log = logging.getLogger("llm")

_BASE_URL = "https://integrate.api.nvidia.com/v1"
_PLACEHOLDERS = {"your-nvidia-nim-key", "your-key", "", None}

def _get_key():
    key = os.getenv("NVIDIA_API_KEY")
    if key in _PLACEHOLDERS:
        raise RuntimeError(
            "NVIDIA_API_KEY is not configured. "
            "Get a free key at https://build.nvidia.com (sign in, click 'Get API Key' on any model page), "
            "then set NVIDIA_API_KEY=nvapi-... in your .env file."
        )
    return key

def complete(system, user, max_tokens=3000):
    key = _get_key()
    model = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-405b-instruct")
    url = f"{_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
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
    last_err = None
    for i in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=120)
            if r.status_code in (401, 403):
                raise RuntimeError(
                    f"NVIDIA NIM auth error ({r.status_code}): your NVIDIA_API_KEY is invalid or expired. "
                    f"Get a new key at https://build.nvidia.com. Response: {r.text[:300]}"
                )
            if r.status_code == 422:
                raise RuntimeError(f"NVIDIA NIM bad request (422): {r.text[:400]}")
            if r.status_code in (429, 500, 502, 503, 504):
                log.warning("NVIDIA NIM transient error (%d), retry %d: %s", r.status_code, i + 1, r.text[:200])
                time.sleep(2 ** i)
                continue
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
        except RuntimeError:
            raise
        except requests.RequestException as e:
            last_err = e
            log.warning("LLM network error (attempt %d/3): %s", i + 1, e)
            time.sleep(2 ** i)
    raise RuntimeError(f"LLM unavailable after 3 retries. Last error: {last_err}")

def complete_json(system, user, max_tokens=3000):
    t = complete(system + "\nReturn ONLY valid JSON, no prose or code fences.", user, max_tokens)
    m = re.search(r"[\[{].*[\]}]", t, re.S)
    if not m:
        raise ValueError(f"LLM did not return valid JSON. Response was: {t[:300]}")
    return json.loads(m.group(0))
