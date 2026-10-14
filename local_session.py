from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import subprocess
import sys
import threading
import time

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "Phi-4-mini-instruct-q4_k_m.gguf"
PROMPT = BASE / "prompt.txt"
OUTPUT = BASE / "response.txt"
HISTORY = BASE / "conversation.json"

parser = argparse.ArgumentParser(description="Persistent local Phi-4-mini session.")
parser.add_argument("--timeout", type=int, default=3600, help="idle timeout in seconds (default: 3600)")
parser.add_argument("--reset", action="store_true", help="start without previous history")
args = parser.parse_args()

if not MODEL.exists():
    raise SystemExit(f"Model not found: {MODEL}")

history = []
if not args.reset and HISTORY.exists():
    try:
        history = json.loads(HISTORY.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"Invalid history file: {HISTORY}")

transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
first_prompt = PROMPT.read_text(encoding="utf-8").strip()
if not first_prompt:
    raise SystemExit("prompt.txt is empty.")
if transcript:
    first_prompt = transcript + "\nUSER: " + first_prompt

# No server is started: stdin/stdout are connected only to this Python process.
command = [
    "llama-cli", "-m", str(MODEL), "-p", first_prompt,
    "-n", "512", "-c", "4096", "--simple-io", "--no-show-timings",
    "--log-disable", "-r", "USER:",
]

process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, encoding="utf-8",
                           bufsize=1)
last_activity = time.monotonic()

def read_output():
    return process.stdout.read()

try:
    print(f"Persistent local session started. Idle timeout: {args.timeout // 60} minutes.")
    print("Edit prompt.txt, then press Enter here for each new prompt. Ctrl+C terminates it.")
    while process.poll() is None:
        if time.monotonic() - last_activity >= args.timeout:
            print("Idle timeout reached; terminating local model.")
            break
        try:
            user_prompt = input("\nPrompt (or /exit): ").strip()
        except EOFError:
            break
        last_activity = time.monotonic()
        if user_prompt == "/exit":
            break
        if not user_prompt:
            user_prompt = PROMPT.read_text(encoding="utf-8").strip()
        process.stdin.write(user_prompt + "\n")
        process.stdin.flush()
        answer = read_output().strip()
        if "USER:" in answer:
            answer = answer.rsplit("USER:", 1)[0].strip()
        OUTPUT.write_text(answer + "\n", encoding="utf-8")
        now = datetime.now(timezone.utc).isoformat()
        history.extend([
            {"role": "user", "content": user_prompt, "time": now},
            {"role": "assistant", "content": answer, "time": datetime.now(timezone.utc).isoformat()},
        ])
        HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(answer)
finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    print("Local session terminated.")
