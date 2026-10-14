from pathlib import Path
from datetime import datetime, timezone
import argparse, json, queue, subprocess, sys, threading, time

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "Phi-4-mini-instruct-q4_k_m.gguf"
PROMPT = BASE / "prompt.txt"
OUTPUT = BASE / "response.txt"
HISTORY = BASE / "conversation.json"
SAVED = BASE / "conversations"
CONTEXT = BASE / ".conversation_context.txt"

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
    command = ["llama-cli", "-m", str(MODEL), "-n", "512", "-c", "4096",
               "--simple-io", "--no-show-timings", "--log-disable", "-r", ">"]
    if history:
        transcript = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in history)
        CONTEXT.write_text(
            "Use the following saved conversation as prior context. Answer the next user message normally.\n\n" + transcript,
            encoding="utf-8")
        command += ["--system-prompt-file", str(CONTEXT)]
    else:
        CONTEXT.write_text("", encoding="utf-8")
    process = subprocess.Popen(command,
       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
PROMPT.write_text("", encoding="utf-8")
last_activity = time.monotonic()
busy = threading.Event()
commands = queue.Queue()

def read_commands():
    while True:
        try: commands.put(input("\n[Type /exit, /clear, /save name, or /load name] ").strip())
        except EOFError: commands.put("/exit"); return

def generate(user_prompt):
    busy.set()
    try: send_prompt(user_prompt)
    finally: busy.clear()

threading.Thread(target=read_commands, daemon=True).start()
print("Persistent local session started. Model is loading...", flush=True)
print("Write a prompt into prompt.txt and save it; the session will detect it automatically.", flush=True)
try:
    while True:
        if time.monotonic() - last_activity >= args.timeout:
            print("Idle timeout reached; terminating local model."); break
        try:
            command = commands.get_nowait()
            last_activity = time.monotonic()
            if command == "/exit": break
            if command == "/clear":
                if busy.is_set(): print("Wait for the current response before using /clear."); continue
                history.clear(); save_history(); start_model(); print("History cleared and model context restarted."); continue
            if command.startswith("/save "):
                name = command[6:].strip()
                if not name: print("Usage: /save <name>"); continue
                SAVED.mkdir(exist_ok=True); (SAVED / f"{name}.json").write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"Conversation saved as {name}."); continue
            if command.startswith("/load "):
                if busy.is_set(): print("Wait for the current response before using /load."); continue
                name = command[6:].strip(); path = SAVED / f"{name}.json"
                if not name or not path.exists(): print("Usage: /load <name> (saved conversation not found)"); continue
                history = load_history(path); save_history(); start_model(); print(f"Conversation {name} loaded and model context restarted."); continue
        except queue.Empty: pass
        if not busy.is_set():
            user_prompt = PROMPT.read_text(encoding="utf-8").strip()
            if user_prompt:
                PROMPT.write_text("", encoding="utf-8")
                last_activity = time.monotonic()
                threading.Thread(target=generate, args=(user_prompt,), daemon=True).start()
        time.sleep(0.2)
finally:
    stop_model()
    print("Local session terminated.")
