import json
import os
import re
import subprocess
import ast
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from prompt import SYSTEM_PROMPT

load_dotenv()

app = FastAPI(title="Agent Backend")
WORKSPACE_ROOT = Path.cwd().resolve()
PLACEHOLDER_PATTERNS = [
    "rest of the html",
    "placeholder",
    "lorem ipsum",
    "content here",
    "your content",
    "todo",
]


class PromptRequest(BaseModel):
    instruction: str


def _extract_text_preview(html: str, max_len: int = 500) -> str:
    without_scripts = re.sub(r"<script[\\s\\S]*?</script>", " ", html, flags=re.IGNORECASE)
    without_styles = re.sub(r"<style[\\s\\S]*?</style>", " ", without_scripts, flags=re.IGNORECASE)
    plain_text = re.sub(r"<[^>]+>", " ", without_styles)
    compact = " ".join(plain_text.split())
    return compact[:max_len] if compact else "(no readable text found)"


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>([\\s\\S]*?)</title>", html, flags=re.IGNORECASE)
    return " ".join(match.group(1).split()) if match else "(no title found)"


def _extract_favicon_url(html: str, base_url: str) -> str:
    link_match = re.search(
        r"<link[^>]*rel=[\"'][^\"']*icon[^\"']*[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>",
        html,
        flags=re.IGNORECASE,
    )
    if link_match:
        return urljoin(base_url, link_match.group(1).strip())

    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


def fetch_website(args: str, include_favicon_default: bool = True) -> str:
    if not args:
        return "Error: fetchWebsite expects a URL string or JSON like {\"url\":\"https://...\",\"include_favicon\":true}."

    url_value = ""
    include_favicon = include_favicon_default

    raw = args.strip()
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            url_value = str(payload.get("url", "")).strip()
            if "include_favicon" in payload:
                include_favicon = bool(payload.get("include_favicon"))
        except json.JSONDecodeError:
            return "Error: fetchWebsite JSON args are invalid."
    else:
        url_value = raw

    if not url_value:
        return "Error: fetchWebsite requires a URL."

    if not url_value.lower().startswith(("http://", "https://")):
        url_value = f"https://{url_value}"

    try:
        response = requests.get(url_value, timeout=(10, 25))
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Error: Failed to fetch website: {exc}"

    html = response.text or ""
    title = _extract_title(html)
    preview = _extract_text_preview(html)

    lines = [
        f"URL: {response.url}",
        f"Title: {title}",
        f"Preview: {preview}",
    ]

    if include_favicon:
        favicon_url = _extract_favicon_url(html, response.url)
        lines.append("Favicon:")
        lines.append(favicon_url)

    return "\n".join(lines)

def get_provider_config() -> Dict[str, str]:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip() or os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", "").strip() or os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
    api_url = os.getenv("OPENROUTER_API_URL", "").strip() or os.getenv(
        "GROQ_API_URL",
        "https://openrouter.ai/api/v1/chat/completions" if os.getenv("OPENROUTER_API_KEY") else "https://api.groq.com/openai/v1/chat/completions",
    ).strip()

    extra_headers: Dict[str, str] = {}
    if "openrouter.ai" in api_url:
        referer = os.getenv("OPENROUTER_SITE_URL", "").strip()
        title = os.getenv("OPENROUTER_APP_NAME", "lite-cursor").strip()
        if referer:
            extra_headers["HTTP-Referer"] = referer
        if title:
            extra_headers["X-Title"] = title

    return {
        "api_key": api_key,
        "model": model,
        "api_url": api_url,
        **extra_headers,
    }

def resolve_workspace_path(path_value: str) -> Path:
    target = (WORKSPACE_ROOT / path_value).resolve()
    if os.path.commonpath([str(WORKSPACE_ROOT), str(target)]) != str(WORKSPACE_ROOT):
        raise ValueError("Path must stay inside the workspace.")
    return target

def list_files(_: str = "") -> str:
    entries: List[str] = []
    for root, dirs, files in os.walk(WORKSPACE_ROOT):
        dirs[:] = [name for name in dirs if name not in {".git", ".venv", "__pycache__"}]
        current_root = Path(root)
        for filename in files:
            entries.append(str((current_root / filename).relative_to(WORKSPACE_ROOT)))
        if len(entries) >= 200:
            break
    return "\n".join(entries[:200]) if entries else "(empty workspace)"

def read_file(path_value: str) -> str:
    if not path_value:
        return "Error: tool_args must be a file path."
    target = resolve_workspace_path(path_value)
    if not target.exists() or target.is_dir():
        return f"Error: File not found: {path_value}"
    return target.read_text(encoding="utf-8")

def write_file(args: str) -> str:
    if not args:
        return "Error: tool_args must be a JSON string with path and content."
    payload = None
    try:
        payload = json.loads(args)
    except json.JSONDecodeError:
        try:
            payload = ast.literal_eval(args)
        except Exception:
            return "Error: writeFile expects tool_args as JSON with path and content."
    if not isinstance(payload, dict):
        return "Error: writeFile expects tool_args as JSON with path and content."

    path_value = str(payload.get("path", "")).strip()
    content = str(payload.get("content", ""))
    if not path_value:
        return "Error: writeFile requires a path."
    if is_placeholder_content(content):
        return f"Error: writeFile rejected placeholder content for {path_value}. Write complete real code."

    target = resolve_workspace_path(path_value)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Wrote {path_value} ({len(content)} chars)."

def execute_command(cmd: str) -> str:
    if not cmd:
        return "Error: tool_args must be a command string."
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(WORKSPACE_ROOT),
        )
    except Exception as exc:
        return f"Command Failed. Error: {exc}"

    output = "\n".join(part for part in [(result.stdout or "").strip(), (result.stderr or "").strip()] if part)
    if result.returncode != 0:
        return f"Command Failed. {output or f'Exit code {result.returncode}.'}"
    return output or "Command executed successfully."

TOOL_MAP = {
    "listFiles": list_files,
    "readFile": read_file,
    "writeFile": write_file,
    "executeCommand": execute_command,
}


def is_placeholder_content(content: str) -> bool:
    lowered = content.lower()
    if len(content.strip()) < 80:
        return True
    return any(pattern in lowered for pattern in PLACEHOLDER_PATTERNS)


def required_project_files(instruction: str) -> List[str]:
    lowered = instruction.lower()
    if "scaler_clone" in lowered:
        return [
            "scaler_clone/index.html",
            "scaler_clone/styles.css",
            "scaler_clone/script.js",
        ]
    if "youtube_clone" in lowered:
        return [
            "youtube_clone/index.html",
            "youtube_clone/styles.css",
            "youtube_clone/script.js",
        ]
    return []


def validate_project_files(instruction: str) -> str:
    required_files = required_project_files(instruction)
    if not required_files:
        return ""

    missing_files: List[str] = []
    placeholder_files: List[str] = []

    for relative_path in required_files:
        target = resolve_workspace_path(relative_path)
        if not target.exists() or target.is_dir():
            missing_files.append(relative_path)
            continue
        content = target.read_text(encoding="utf-8")
        if is_placeholder_content(content):
            placeholder_files.append(relative_path)

    if missing_files:
        return "Missing required files: " + ", ".join(missing_files)
    if placeholder_files:
        return "Placeholder-like content detected in: " + ", ".join(placeholder_files)
    return ""

def extract_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:-3].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:-3].strip()
    try:
        return json.loads(cleaned)
    except Exception:
        first = cleaned.find("{")
        last = cleaned.rfind("}")
        if first != -1 and last != -1 and last > first:
            return json.loads(cleaned[first : last + 1])
        raise

def call_model(messages: List[dict]) -> str:
    config = get_provider_config()
    if not config["api_key"] or not config["model"] or not config["api_url"]:
        raise RuntimeError("Missing provider config. Set OPENROUTER_API_KEY/OPENROUTER_MODEL or GROQ_API_KEY/GROQ_MODEL.")
    payload = {
        "model": config["model"],
        "messages": messages,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "6000")),
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {config['api_key']}",
        "Content-Type": "application/json",
    }
    if config.get("HTTP-Referer"):
        headers["HTTP-Referer"] = config["HTTP-Referer"]
    if config.get("X-Title"):
        headers["X-Title"] = config["X-Title"]

    response = requests.post(config["api_url"], headers=headers, json=payload, timeout=(20, 90))
    if response.status_code != 200:
        raise RuntimeError(f"Provider error {response.status_code}: {response.text[:1000]}")

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Unexpected provider response: {json.dumps(data)[:1000]}")

    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    finish_reason = choices[0].get("finish_reason")
    if finish_reason == "stop":
        raise RuntimeError("Provider returned empty content.")
    raise RuntimeError(f"Provider returned empty content: {json.dumps(data)[:1000]}")

def run_agent_events(instruction: str) -> Iterable[dict]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": instruction},
    ]

    max_iterations = int(os.getenv("LLM_MAX_ITERATIONS", "16"))
    empty_content_retries = 0
    invalid_json_retries = 0
    yield {"type": "status", "content": f"Using model: {get_provider_config()['model']}"}

    for iteration in range(1, max_iterations + 1):
        yield {"type": "status", "content": f"Iteration {iteration}/{max_iterations}"}
        try:
            raw_content = call_model(messages)
        except Exception as exc:
            error_text = str(exc)
            if "Provider returned empty content" in error_text and empty_content_retries < 2:
                empty_content_retries += 1
                observation = "Continue with the next JSON step only. Do not return empty content."
                yield {"type": "warning", "content": observation}
                messages.append({"role": "developer", "content": json.dumps({"step": "OBSERVE", "content": observation})})
                continue
            yield {"type": "error", "content": error_text}
            return


        try:
            parsed = extract_json(raw_content)
        except Exception:
            invalid_json_retries += 1
            observation = (
                "Return one valid JSON step only. "
                "If writing a large file, still return one complete valid JSON object with escaped content."
            )
            yield {"type": "warning", "content": observation, "raw": raw_content[:800]}
            if invalid_json_retries >= 3:
                yield {"type": "error", "content": "Model returned invalid JSON three times in a row."}
                return
            messages.append({"role": "developer", "content": json.dumps({"step": "OBSERVE", "content": observation})})
            continue

        invalid_json_retries = 0

        step = str(parsed.get("step", "")).upper().strip()
        content = str(parsed.get("content", "")).strip()
        tool_name = parsed.get("tool_name")
        tool_args = parsed.get("tool_args")

        if step in {"START", "THINK"}:
            yield {"type": "step", "step": step, "content": content}
            messages.append({"role": "assistant", "content": json.dumps(parsed)})
            messages.append(
                {
                    "role": "developer",
                    "content": json.dumps({"step": "OBSERVE", "content": "Continue to the next step."}),
                }
            )
            continue
        if step == "TOOL":
            yield {"type": "tool", "tool_name": tool_name, "tool_args": tool_args}
            observation = f"Error: Tool '{tool_name}' is not available."
            if str(tool_name) == "fetchWebsite":
                observation = fetch_website("" if tool_args is None else str(tool_args), include_favicon_default=True)
            else:
                tool = TOOL_MAP.get(str(tool_name))
                if tool:
                    observation = tool("" if tool_args is None else str(tool_args))
            yield {"type": "observe", "content": observation}
            messages.append({"role": "assistant", "content": json.dumps(parsed)})
            messages.append({"role": "developer", "content": json.dumps({"step": "OBSERVE", "content": observation})})
            validation_error = validate_project_files(instruction)
            if not validation_error:
                yield {"type": "final", "content": "All required project files were generated successfully."}
                return
            continue
        if step == "OUTPUT":
            validation_error = validate_project_files(instruction)
            if validation_error:
                yield {"type": "warning", "content": validation_error}
                messages.append(
                    {
                        "role": "developer",
                        "content": json.dumps({"step": "OBSERVE", "content": validation_error}),
                    }
                )
                continue
            yield {"type": "final", "content": content}
            return

        yield {"type": "warning", "content": f"Invalid step: {step}", "raw": raw_content[:800]}
        return

    yield {"type": "error", "content": "Agent loop timed out."}

@app.post("/run-agent")
def run_agent(req: PromptRequest):
    final_message = None
    last_error = None
    for event in run_agent_events(req.instruction):
        if event["type"] == "final":
            final_message = event["content"]
            break
        if event["type"] == "error":
            last_error = event["content"]

    if final_message:
        return {"status": "success", "message": final_message}
    return {"status": "error", "message": last_error or "Agent stopped without output."}

@app.post("/run-agent-stream")
def run_agent_stream(req: PromptRequest):
    def generate():
        for event in run_agent_events(req.instruction):
            yield json.dumps(event, ensure_ascii=True) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
