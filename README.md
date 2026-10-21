# QuietForge

QuietForge is a private, offline-first AI assistant for Windows. It runs Qwen3:8B locally through `llama.cpp` on the CPU, reads prompts from a text file, and writes responses to another text file. It does not start a model server or expose an API port.

## Requirements

- Windows 10 or newer
- Python 3.10+
- `llama.cpp` with the `llama-cli` command available on `PATH`
- At least 8 GB of available RAM; 16 GB is recommended for Qwen3:8B

Install `llama.cpp` with WinGet:

```powershell
winget install --id ggml.llamacpp --accept-source-agreements --accept-package-agreements
```

## Download the model

This project uses the Q4_K_M quantization of Qwen3:8B. Download it into a `models` folder (about 5 GB):

```powershell
New-Item -ItemType Directory -Force .\models | Out-Null
curl.exe -L --fail -o .\models\Qwen3-8B-Q4_K_M.gguf `
  https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf
```

The weights are downloaded once. Inference runs locally through `llama-cli`; QuietForge does not launch Ollama, `llama-server`, or any model API server.

## Run

After cloning and downloading the model:

```powershell
.\run_session.cmd
```

Thinking mode is disabled by default to keep replies faster. Enable it for a one-shot response or a persistent session with `--thinking`:

```powershell
py .\run_model.py --thinking
.\run_session.cmd --thinking
```

This option applies to the whole run/session. For one-shot mode it can be combined with `--reset` or other runner options.

Each model response can generate up to 2,048 tokens. This is a ceiling, not a target; generation stops sooner when the model finishes. The context window is 4,096 tokens total, shared by the prompt, conversation, and generated response. The one-shot runner sends only the latest two compact tool results back to the model and trims older saved messages. The persistent session clears llama-cli's in-memory chat after each terminal command and gives it a compact task log, preventing repeated output from filling its context. Full command output remains in `c.json`. During one runner invocation, the agent can make up to 12 terminal calls, one per model turn. The CTF prompt reminds it to pipe Base64 input to `base64 -d` (for example, `printf '%s' 'DATA' | base64 -d`) instead of passing the data as a filename.

Both runners use about 80% of the machine's logical processors by default (`7` threads on an 8-thread CPU). Adjust the thread count with `--threads N`, for example:

```powershell
py .\run_model.py --threads 6
.\run_session.cmd --threads 6
```

CPU load varies with prompt length, memory bandwidth, and thermal limits, so the thread setting targets utilization but does not guarantee an exact percentage.

Put challenge files in `workspace/`. Model terminal commands start in this folder in WSL, Git Bash, CMD, and PowerShell, so use `ls` or `cat app.py` there; do not prefix paths with `workspace/`. The runner adds a live list of workspace files to each prompt. Relative paths (such as `./flag.txt`) resolve against the challenge workspace. The folder is local and ignored by Git. Then write your prompt into `p.txt` and save it. QuietForge detects it automatically, clears it when accepted, and writes the answer to `r.txt`. A prompt saved while a response is generating remains queued in `p.txt` until the current response finishes.

The CTF system prompt is stored in `system_prompt.txt` and is included in both one-shot and persistent runs. Edit that file to change the model's challenge-solving guidance; the default asks it to verify terminal results and avoid reporting guessed flags as solved.

Conversation history is stored locally in `c.json`. Terminal commands:

```text
/undo
/clear
/save name
/load name
/exit
```

Use `/undo` to remove the latest complete user/assistant exchange from saved history and restart the model with the remaining conversation. This is useful when an unwanted response was saved. In one-shot mode, run `py .\run_model.py --undo` to remove the latest exchange without generating a response. The next normal run uses the remaining history.

The model can request terminal commands using `[[run]]...[[/run]]`; Ubuntu in WSL runs by default. It can explicitly request `[[run:gitbash]]...[[/run]]`, `[[run:cmd]]...[[/run]]`, or `[[run:powershell]]...[[/run]]` for those shells. Commands run from `workspace/` (translated to its WSL path for Ubuntu). The model should inspect files before editing, use one noninteractive command such as Python `-c` or `sed -i`, and reread or diff the result. It should not use interactive editors such as nano or vim. QuietForge waits for each command to finish, then sends the exit code and output back to the model. Each command has a 15-second timeout, output is limited to 12,000 characters, and each runner invocation allows up to 12 terminal calls.

The default idle timeout is 3,600 seconds:

```powershell
.\run_session.cmd --timeout 1800
```

## Custom file locations

Arguments use this order: `PROMPT_DIR RESPONSE_DIR HISTORY_DIR`. Missing directories and files are created automatically. Use `-` for the project directory; omitted trailing locations also default to `-`.

```powershell
.\run_session.cmd C:\Users\mohay C:\ C:\Users\mohay\OneDrive\Desktop
```

The same arguments work with one-shot mode:

```powershell
py .\run_model.py C:\Users\mohay C:\ C:\Users\mohay\OneDrive\Desktop
```

Use `--reset` with `run_model.py` to clear saved history before generating a response. The new prompt and response are then saved as the start of a fresh conversation. Use `--undo` to remove the latest exchange from the history file and exit.

## Privacy

After the model is downloaded, inference is local and offline. QuietForge uses standard input/output with a local child process and does not listen on a network port. Prompts, responses, and conversations are intentionally ignored by Git.

## License

The project code is released under the MIT License. The model has its own license and terms; review its model card before redistribution.
