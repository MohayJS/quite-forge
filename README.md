# Local Phi-4-mini runner

1. Edit `prompt.txt`.
2. Run `run_model.cmd` or `python run_model.py`.
3. Read the answer in `response.txt`.
4. The conversation is saved in `conversation.json` and included next time.

Reset the conversation:

```powershell
run_model.cmd --reset
```

Use a separate conversation:

```powershell
python .\run_model.py --history conversations\project.json
```

This uses `llama-cli` directly. It does not start a server and works offline after the model has been downloaded.

For a persistent local process that keeps the model loaded:

```powershell
run_session.cmd
run_session.cmd --reset
run_session.cmd --timeout 3600
```

This session uses only local stdin/stdout; it does not listen on a network port. Press `Ctrl+C` or type `/exit` to terminate it.
