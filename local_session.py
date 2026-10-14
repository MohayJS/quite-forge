from pathlib import Path
from datetime import datetime, timezone
import argparse, json, queue, subprocess, sys, threading, time

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "Phi-4-mini-instruct-q4_k_m.gguf"
PROMPT = BASE / "prompt.txt"
OUTPUT = BASE / "response.txt"
HISTORY = BASE / "conversation.json"
SAVED = BASE / "conversations"

parser = argparse.ArgumentParser()
parser.add_argument("--timeout", type=int, default=3600)
args = parser.parse_args()
if not MODEL.exists(): raise SystemExit(f"Model not found: {MODEL}")
PROMPT.write_text("", encoding="utf-8")

def load_history(path=HISTORY):
    if not path.exists(): return []
    try: return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError: return []

history = load_history()
process = None
output_queue = queue.Queue()

def start_model():
    global process, output_queue
    if process and process.poll() is None: process.terminate()
    output_queue = queue.Queue()
    process = subprocess.Popen([
        "llama-cli", "-m", str(MODEL), "-n", "512", "-c", "4096",
        "--simple-io", "--no-show-timings", "--log-disable", "-r", ">"
    ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
       text=True, encoding="utf-8", bufsize=1)
    def reader():
        while True:
            char = process.stdout.read(1)
            if not char: break
            output_queue.put(char)
        output_queue.put(None)
    threading.Thread(target=reader, daemon=True).start()
    # Discard llama-cli's startup banner and its initial input prompt.
    read_response()
    if process.poll() is not None:
        error = process.stderr.read().strip()
        raise SystemExit(f"llama-cli exited during startup: {error or 'unknown error'}")
    # Rebuild the model's live context from the saved conversation. The
    # model must reread this after every process restart; JSON alone is not
    # automatically visible to llama-cli.
    if history:
        transcript = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        process.stdin.write(
            "The following is the existing conversation. Use it as context, "
            "do not answer it, and wait for the next user message.\n\n" +
            transcript + "\n\n"
        )
        process.stdin.flush()
        read_response()

def stop_model():
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.kill()

def read_response():
    text = ""
    while True:
        char = output_queue.get()
        if char is None: break
        text += char
        if text == ">" or text.endswith("\n>") or text.endswith("\r>"):
            text = text[:-1]
            break
    if "ASSISTANT:" in text: text = text.rsplit("ASSISTANT:", 1)[1]
    if "Loading model..." in text: text = text.split("Loading model...", 1)[-1]
    if "available commands:" in text: text = text.split("available commands:", 1)[-1]
    return text.strip()

def save_history():
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def send_prompt(user_prompt):
    if process.poll() is not None:
        raise SystemExit("The local model process has already stopped. Restart run_session.cmd.")
    # llama-cli already owns the active chat context and KV cache. Sending
    # the transcript again would duplicate/confuse its chat template.
    process.stdin.write(user_prompt + "\n")
    process.stdin.flush()
    print("Generating response...", flush=True)
    answer = read_response()
    # The first interactive read can still contain llama-cli's help block
    # while it finishes switching from startup to conversation mode.
    while "/exit or Ctrl+C" in answer or "/clear              clear the chat history" in answer:
        answer = read_response()
    OUTPUT.write_text(answer + "\n", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    history.extend([{"role":"user", "content":user_prompt, "time":now},
                    {"role":"assistant", "content":answer, "time":datetime.now(timezone.utc).isoformat()}])
    save_history()
    print("Response generated and saved to response.txt.", flush=True)

start_model()
last_activity = time.monotonic()
print("Persistent local session started. Model is loading...", flush=True)
print("Edit prompt.txt, then press Enter in this terminal.", flush=True)
try:
    while True:
        if time.monotonic() - last_activity >= args.timeout:
            print("Idle timeout reached; terminating local model.")
            break
        command = input("\n[Press Enter after editing prompt.txt | /exit | /clear | /save name | /load name] ").strip()
        last_activity = time.monotonic()
        if command == "/exit": break
        if command == "/clear":
            history.clear(); save_history(); start_model()
            print("History cleared and model context restarted."); continue
        if command.startswith("/save "):
            name = command[6:].strip()
            if not name: print("Usage: /save <name>"); continue
            SAVED.mkdir(exist_ok=True); (SAVED / f"{name}.json").write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Conversation saved as {name}."); continue
        if command.startswith("/load "):
            name = command[6:].strip(); path = SAVED / f"{name}.json"
            if not name or not path.exists(): print("Usage: /load <name> (saved conversation not found)"); continue
            history = load_history(path); save_history(); start_model()
            print(f"Conversation {name} loaded and model context restarted."); continue
        if command:
            print("Prompts must be written in prompt.txt. Press only Enter here, or use a slash command."); continue
        user_prompt = PROMPT.read_text(encoding="utf-8").strip()
        if not user_prompt:
            print("prompt.txt is empty; write a prompt there first."); continue
        send_prompt(user_prompt)
        PROMPT.write_text("", encoding="utf-8")
finally:
    stop_model()
    print("Local session terminated.")
