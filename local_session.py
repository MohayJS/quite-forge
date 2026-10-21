from pathlib import Path
from datetime import datetime, timezone
import argparse, json, queue, subprocess, sys, threading, time
from shell_tools import MAX_TOOL_CALLS, TOOL_INSTRUCTIONS, clean_truncation_marker, find_tool_request, load_system_prompt, run_shell, tool_result

BASE = Path(__file__).resolve().parent
MODEL = BASE / "models" / "Phi-4-mini-instruct-q4_k_m.gguf"
PROMPT = BASE / "p.txt"
OUTPUT = BASE / "r.txt"
HISTORY = BASE / "c.json"
parser = argparse.ArgumentParser()
parser.add_argument("--timeout", type=int, default=3600)
parser.add_argument("locations", nargs="*", metavar="PROMPT_DIR RESPONSE_DIR HISTORY_DIR")
args = parser.parse_args()
if len(args.locations) > 3:
    raise SystemExit("Provide at most: PROMPT_DIR RESPONSE_DIR HISTORY_DIR")
locations = (args.locations + ["-", "-", "-"])[:3]
def locate(value, filename):
    folder = BASE if value == "-" else Path(value)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / filename
PROMPT = locate(locations[0], "p.txt")
OUTPUT = locate(locations[1], "r.txt")
HISTORY = locate(locations[2], "c.json")
SAVED = HISTORY.parent / "conversations"
CONTEXT = HISTORY.parent / ".conversation_context.txt"
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
               "--simple-io", "--no-show-timings", "--log-disable",
               "--device", "none", "--gpu-layers", "0", "-r", ">"]
    transcript = "\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in history)
    context_text = load_system_prompt() + "\n\n" + TOOL_INSTRUCTIONS
    if transcript:
        context_text += "\n\nUse the following saved conversation as prior context. Answer the next user message normally.\n\n" + transcript
    CONTEXT.write_text(context_text, encoding="utf-8")
    command += ["--system-prompt-file", str(CONTEXT)]
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
    tool_trace = []
    for tool_count in range(MAX_TOOL_CALLS + 1):
        request = find_tool_request(answer)
        if not request:
            break
        if tool_count == MAX_TOOL_CALLS:
            answer = "I reached the limit of five terminal commands for this response."
            break
        shell, shell_command = request
        if not shell_command:
            exit_code, output = 2, "The terminal command was empty."
        else:
            print(f"Running {shell} command and waiting for completion...", flush=True)
            exit_code, output = run_shell(shell, shell_command)
        result_text = tool_result(shell, shell_command, exit_code, output)
        tool_trace.append(result_text)
        process.stdin.write(result_text + "\nContinue using the result above. Request another command with the tool format if needed; otherwise give the final answer.\n")
        process.stdin.flush()
        answer = read_response()
        while "/exit or Ctrl+C" in answer or "/clear              clear the chat history" in answer:
            answer = read_response()
    if tool_trace:
        answer = "\n\n".join(tool_trace + [answer])
    answer = clean_truncation_marker(answer).strip()
    OUTPUT.write_text(answer + "\n", encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()
    history.extend([{"role":"user", "content":user_prompt, "time":now},
                    {"role":"assistant", "content":answer, "time":datetime.now(timezone.utc).isoformat()}])
    save_history()
    print("Response generated and saved to r.txt.", flush=True)

start_model()
PROMPT.write_text("", encoding="utf-8")
last_activity = time.monotonic()
busy = threading.Event()
commands = queue.Queue()

def read_commands():
    while True:
        try: commands.put(input("\n[Type /undo, /clear, /save name, /load name, or /exit] ").strip())
        except EOFError: commands.put("/exit"); return

def generate(user_prompt):
    busy.set()
    try: send_prompt(user_prompt)
    finally: busy.clear()

threading.Thread(target=read_commands, daemon=True).start()
print("Persistent local session started. Model is loading...", flush=True)
print("Write a prompt into p.txt and save it; the session will detect it automatically.", flush=True)
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
            if command == "/undo":
                if busy.is_set(): print("Wait for the current response before using /undo."); continue
                if len(history) >= 2 and history[-2].get("role") == "user" and history[-1].get("role") == "assistant":
                    del history[-2:]; save_history(); start_model(); print("Latest exchange removed and model context restarted.")
                else: print("No complete latest user/assistant exchange to remove.")
                continue
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
