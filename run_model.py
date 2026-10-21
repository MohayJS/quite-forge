from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import math
import os
import subprocess
import sys
from shell_tools import MAX_TOOL_CALLS, TOOL_INSTRUCTIONS, challenge_session_active, clean_truncation_marker, compact_history, find_tool_request, load_system_prompt, run_shell, tool_result, workspace_inventory

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "Qwen3-8B-Q4_K_M.gguf"
DEFAULT_HISTORY = BASE / "c.json"
WORK_PROMPT = BASE / ".prompt_for_model.txt"

parser = argparse.ArgumentParser(description="Run Qwen3:8B locally with conversation history.")
parser.add_argument("--reset", action="store_true", help="clear history before running")
parser.add_argument("--undo", action="store_true", help="remove the latest user/assistant exchange from history")
parser.add_argument("--thinking", action="store_true", help="enable Qwen3 thinking for this response")
parser.add_argument("--threads", type=int, default=max(1, math.ceil((os.cpu_count() or 1) * 0.8)), help="CPU threads to use (default: about 80%% of logical processors)")
parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="use another history JSON file")
parser.add_argument("locations", nargs="*", metavar="PROMPT_DIR RESPONSE_DIR HISTORY_DIR")
args = parser.parse_args()
if args.threads < 1:
    parser.error("--threads must be at least 1")
if len(args.locations) > 3:
    raise SystemExit("Provide at most: PROMPT_DIR RESPONSE_DIR HISTORY_DIR")
locations = (args.locations + ["-", "-", "-"])[:3]
def locate(value, filename):
    folder = BASE if value == "-" else Path(value)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename
prompt_file = locate(locations[0], "p.txt")
output_file = locate(locations[1], "r.txt")
history_file = locate(locations[2], "c.json") if args.history == DEFAULT_HISTORY else (args.history if args.history.is_absolute() else BASE / args.history)

if not MODEL.exists():
    raise SystemExit(f"Model not found: {MODEL}")
if not prompt_file.exists():
    prompt_file.write_text("", encoding="utf-8")

if args.reset:
    history = []
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text("[]\n", encoding="utf-8")
elif not history_file.exists():
    history = []
else:
    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"Invalid history file: {history_file}")

if args.undo:
    if len(history) >= 2 and history[-2].get("role") == "user" and history[-1].get("role") == "assistant":
        del history[-2:]
        history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Latest exchange removed from: {history_file}")
    else:
        print("No complete latest user/assistant exchange to remove.")
    raise SystemExit(0)

user_prompt = prompt_file.read_text(encoding="utf-8").strip()
if not user_prompt:
    raise SystemExit("p.txt is empty.")

system_turns = f"<|im_start|>system\n{system_prompt}\n\n{TOOL_INSTRUCTIONS}\n\nCurrent workspace files:\n{workspace_inventory()}<|im_end|>\n"
history_turns = "".join(
    f"<|im_start|>{message['role']}\n{message['content']}<|im_end|>\n"
    for message in compact_history(history)
)
thinking_mode = "/think" if args.thinking else "/no_think"
current_user_turn = f"<|im_start|>user\n{user_prompt}\n{thinking_mode}<|im_end|>\n<|im_start|>assistant\n"
tool_context = []
tool_trace = []


def context_excerpt(text, limit):
    if len(text) <= limit:
        return text
    head = int(limit * 0.65)
    return text[:head] + "\n[output shortened in model context; full output is saved in c.json]\n" + text[-(limit - head):]


def build_model_context():
    recent = tool_context[-2:]
    context = system_turns + history_turns + current_user_turn
    for assistant_text, tool_text, followup in recent:
        context += (
            f"{context_excerpt(assistant_text, 450)}<|im_end|>\n<|im_start|>user\n"
            f"{context_excerpt(tool_text, 1100)}\n{followup}\n{thinking_mode}"
            "<|im_end|>\n<|im_start|>assistant\n"
        )
    # Keep a strict prompt budget so input plus generated tokens fits the 4096-token context.
    if len(context) > 7600:
        context = system_turns + current_user_turn
        if recent:
            assistant_text, tool_text, followup = recent[-1]
            context += (
                f"{context_excerpt(assistant_text, 300)}<|im_end|>\n<|im_start|>user\n"
                f"{context_excerpt(tool_text, 800)}\n{followup}\n{thinking_mode}"
                "<|im_end|>\n<|im_start|>assistant\n"
            )
    return context

for tool_count in range(MAX_TOOL_CALLS + 1):
    turns = build_model_context()
    WORK_PROMPT.write_text(turns, encoding="utf-8")
    command = [
        "llama-cli", "-m", str(MODEL), "-f", str(WORK_PROMPT),
        "-n", "2048", "-c", "4096", "--no-display-prompt",
        "--threads", str(args.threads),
        "--reasoning", "on" if args.thinking else "off",
        "--single-turn", "--simple-io", "--no-show-timings", "--log-disable",
        "--device", "none", "--gpu-layers", "0",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise SystemExit("llama-cli was not found. Reopen PowerShell, then run this script again.")
    if result.returncode != 0:
        if "exceeds the available context size" in result.stderr.lower():
            raise SystemExit("The prompt still exceeds llama.cpp's context window. Shorten the prompt or remove older saved history with --undo/--reset.")
        print(result.stderr, file=sys.stderr)
        raise SystemExit(result.returncode)

    # llama.cpp echoes either the ASSISTANT marker or a truncated prompt tail.
    text = result.stdout
    assistant_marker = "<|im_start|>assistant\n"
    boundary = text.rfind(assistant_marker)
    if boundary >= 0:
        text = text[boundary + len(assistant_marker):]
    else:
        boundary_marker = " ... (truncated)\n"
        boundary = text.rfind(boundary_marker)
        if boundary < 0:
            raise SystemExit("Could not locate llama-cli's echoed prompt; history was not updated.")
        text = text[boundary + len(boundary_marker):]
    if "Exiting..." in text:
        text = text.split("Exiting...", 1)[0]
    answer = text.strip()
    if answer.endswith("<|im_end|>"):
        answer = answer[:-len("<|im_end|>")].rstrip()
    request = find_tool_request(answer)
    if not request:
        if challenge_session_active():
            continuation = "The challenge connection is still active. Read the latest server hint and submit the next Linux command using exactly [[run]]command[[/run]]. If the server confirmed completion, report its exact confirmation."
            tool_context.append((answer, "Challenge session remains active.", continuation))
            continue
        break
    if tool_count == MAX_TOOL_CALLS:
        answer = "I reached the limit of twelve terminal commands for this response. " + answer
        break
    shell, shell_command = request
    if not shell_command:
        tool_output = "The terminal command was empty."
        exit_code = 2
    else:
        exit_code, tool_output = run_shell(shell, shell_command)
    result_text = tool_result(shell, shell_command, exit_code, tool_output)
    tool_trace.append(result_text)
    if exit_code != 0:
        followup = "The command failed. Treat its output as an error, not as the intended result. Do not guess or claim the operation succeeded. If the task still depends on it, correct the command and request another terminal call; otherwise explain the failure."
    else:
        followup = "Use the completed command result above to continue. If another command is needed, request it using the required tool format; otherwise answer normally."
    tool_context.append((answer, result_text, followup))
else:
    answer = "The terminal command limit was reached."

if not answer:
    raise SystemExit("llama-cli did not return a clean assistant response; history was not updated.")
if tool_trace:
    answer = "\n\n".join(tool_trace + [answer])
answer = clean_truncation_marker(answer).strip()

now = datetime.now(timezone.utc).isoformat()
history.extend([
    {"role": "user", "content": user_prompt, "time": now},
    {"role": "assistant", "content": answer, "time": datetime.now(timezone.utc).isoformat()},
])
history_file.parent.mkdir(parents=True, exist_ok=True)
history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
output_file.write_text(answer + "\n", encoding="utf-8")
print(f"Response written to: {output_file}")
print(f"Conversation history saved to: {history_file}")
