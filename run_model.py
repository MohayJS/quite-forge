from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import subprocess
import sys

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "Phi-4-mini-instruct-q4_k_m.gguf"
PROMPT = BASE / "p.txt"
OUTPUT = BASE / "r.txt"
WORK_PROMPT = BASE / ".prompt_for_model.txt"

parser = argparse.ArgumentParser(description="Run Phi-4-mini locally with conversation history.")
parser.add_argument("--reset", action="store_true", help="clear history before running")
parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY, help="use another history JSON file")
parser.add_argument("locations", nargs="*", metavar="HISTORY_DIR RESPONSE_DIR PROMPT_DIR")
args = parser.parse_args()
if len(args.locations) not in (0, 3):
    raise SystemExit("Provide either no locations or exactly: HISTORY_DIR RESPONSE_DIR PROMPT_DIR")
locations = args.locations or ["-", "-", "-"]
def locate(value, filename):
    folder = BASE if value == "-" else Path(value)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename
history_file = locate(locations[0], "c.json") if args.history == DEFAULT_HISTORY else (args.history if args.history.is_absolute() else BASE / args.history)
output_file = locate(locations[1], "r.txt")
prompt_file = locate(locations[2], "p.txt")

if not MODEL.exists():
    raise SystemExit(f"Model not found: {MODEL}")
if not prompt_file.exists():
    prompt_file.write_text("", encoding="utf-8")

if args.reset or not history_file.exists():
    history = []
else:
    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise SystemExit(f"Invalid history file: {history_file}")

user_prompt = prompt_file.read_text(encoding="utf-8").strip()
if not user_prompt:
    raise SystemExit("p.txt is empty.")

history_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
transcript = (history_text + "\n" if history_text else "") + f"USER: {user_prompt}\nASSISTANT:"
WORK_PROMPT.write_text(transcript, encoding="utf-8")

command = [
    "llama-cli", "-m", str(MODEL), "-f", str(WORK_PROMPT),
    "-n", "512", "-c", "4096", "--no-display-prompt",
    "--single-turn", "--simple-io", "--no-show-timings", "--log-disable",
]

try:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
except FileNotFoundError:
    raise SystemExit("llama-cli was not found. Reopen PowerShell, then run this script again.")
if result.returncode != 0:
    print(result.stderr, file=sys.stderr)
    raise SystemExit(result.returncode)

text = result.stdout
if "ASSISTANT:" in text:
    text = text.rsplit("ASSISTANT:", 1)[1]
if "Exiting..." in text:
    text = text.split("Exiting...", 1)[0]
answer = text.strip()

now = datetime.now(timezone.utc).isoformat()
history.extend([
    {"role": "user", "content": user_prompt, "time": now},
    {"role": "assistant", "content": answer, "time": datetime.now(timezone.utc).isoformat()},
])
history_file.parent.mkdir(parents=True, exist_ok=True)
history_file.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
output_file.write_text(answer + "\n", encoding="utf-8")
print(f"Response written to: {OUTPUT}")
print(f"Conversation history saved to: {history_file}")
