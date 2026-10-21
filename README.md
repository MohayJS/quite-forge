# QuietForge

QuietForge is a private, offline-first AI assistant for Windows. It runs Phi-4-mini locally through `llama.cpp` on the CPU, reads prompts from a text file, and writes responses to another text file. It does not start a web server or expose an API port.

## Requirements

- Windows 10 or newer
- Python 3.10+
- `llama.cpp` with the `llama-cli` command available on `PATH`
- Approximately 3–5 GB of available RAM

Install `llama.cpp` with WinGet:

```powershell
winget install --id ggml.llamacpp --accept-source-agreements --accept-package-agreements
```

## Download the model

This project uses `Phi-4-mini-instruct-q4_k_m.gguf`. Download it into a `models` folder:

```powershell
New-Item -ItemType Directory -Force .\models | Out-Null
curl.exe -L --fail -o .\models\Phi-4-mini-instruct-q4_k_m.gguf `
  https://huggingface.co/Mungert/Phi-4-mini-instruct-GGUF/resolve/main/Phi-4-mini-instruct-q4_k_m.gguf
```

The model is not included because it is several gigabytes in size.

## Run

After cloning and downloading the model:

```powershell
.\run_session.cmd
```

Write your prompt into `p.txt` and save the file. QuietForge detects it automatically, clears it when accepted, and writes the answer to `r.txt`. A prompt saved while a response is generating remains queued in `p.txt` until the current response finishes.

Conversation history is stored locally in `c.json`. Terminal commands:

```text
/undo
/clear
/save name
/load name
/exit
```

Use `/undo` to remove the latest complete user/assistant exchange from saved history and restart the model with the remaining conversation. This is useful when an unwanted response was saved. In one-shot mode, run `py .\run_model.py --undo` to remove the latest exchange without generating a response. The next normal run uses the remaining history.

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
