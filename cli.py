import json
import sys
import webbrowser
from pathlib import Path
import requests

API_URL = "http://localhost:8000/run-agent-stream"


class RunState:
    def __init__(self) -> None:
        self.step_number = 0
        self.generated_html: list[Path] = []


def next_step_label(state: RunState, name: str) -> str:
    state.step_number += 1
    return f"[STEP {state.step_number}][{name}]"


def remember_generated_html(state: RunState, event: dict) -> None:
    if event.get("tool_name") != "writeFile":
        return

    tool_args = event.get("tool_args")
    if not isinstance(tool_args, str):
        return

    try:
        payload = json.loads(tool_args)
    except json.JSONDecodeError:
        return

    path_value = str(payload.get("path", "")).strip()
    if path_value.lower().endswith(".html"):
        state.generated_html.append(Path(path_value))


def maybe_open_generated_html(state: RunState) -> None:
    if not state.generated_html:
        return

    html_path = state.generated_html[-1].resolve()
    if not html_path.exists():
        return

    webbrowser.open(html_path.as_uri())
    print(f"[OPEN] Opened {html_path} in your browser.")


def print_event(event: dict, state: RunState) -> None:
    event_type = event.get("type")
    if event_type == "status":
        print(f"[STATUS] {event.get('content', '')}")
    elif event_type == "step":
        print(f"{next_step_label(state, event.get('step', 'STEP'))} {event.get('content', '')}")
    elif event_type == "tool":
        remember_generated_html(state, event)
        print(f"{next_step_label(state, 'TOOL')} {event.get('tool_name')} -> {event.get('tool_args')}")
    elif event_type == "observe":
        print(f"{next_step_label(state, 'OBSERVE')} {event.get('content', '')}")
    elif event_type == "warning":
        print(f"[WARN] {event.get('content', '')}")
        if event.get("raw"):
            print(f"[WARN RAW] {event['raw']}")
    elif event_type == "final":
        print(f"\n{next_step_label(state, 'OUTPUT')}\n{event.get('content', '')}")
        maybe_open_generated_html(state)
    elif event_type == "error":
        print(f"\n[ERROR]\n{event.get('content', '')}")


def run_once(instruction: str) -> None:
    state = RunState()
    try:
        with requests.post(
            API_URL,
            json={"instruction": instruction},
            stream=True,
            timeout=(30, 600),
        ) as response:
            if response.status_code != 200:
                print(f"[ERROR] Server responded with {response.status_code}")
                print(response.text)
                return

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                print_event(json.loads(line), state)
    except requests.exceptions.RequestException as exc:
        print(f"[ERROR] Failed to connect to backend: {exc}")


def main():
    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:]).strip()
    else:
        instruction = input("Enter instruction: ").strip()

    if instruction:
        run_once(instruction)


if __name__ == "__main__":
    main()
