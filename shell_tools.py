"""Small synchronous shell tool used by the local model runners."""
from pathlib import Path
import re
import shlex
import socket
import shutil
import subprocess

BASE = Path(__file__).resolve().parent
WORKSPACE = BASE / "workspace"
WORKSPACE.mkdir(exist_ok=True)
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
MAX_TOOL_CALLS = 12
TOOL_TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 12000

DEFAULT_SYSTEM_PROMPT = """Solve authorized CTF tasks carefully. Verify transformations and preserve exact bytes and case. Use terminal output as evidence; never invent results or call a guessed flag verified. Stay within the supplied challenge scope.

Terminal commands start inside `workspace`; use `ls` and `cat app.py`, not `ls workspace` or `workspace/app.py`. If the user names a file but gives no contents, inspect it from the workspace before asking; IDE tabs are not directly visible. Check that files required by a program exist before running it; if a referenced file is absent, report that instead of retrying the program. Never launch a program that reads `input()` without supplying stdin in the same command (for example, pipe a generated line into it); do not run it interactively. Use Ubuntu in WSL by default. Request one complete `[[run]]command[[/run]]` block and wait for the actual result. Inspect files before editing. Never use interactive editors such as nano, vim, or an interactive REPL. Edit with one noninteractive command, then reread or diff to verify. For Base64, pipe input to stdin: `printf '%s' 'DATA' | base64 -d`; passing DATA as an argument treats it as a filename. Correct failed commands using actual error output and do not repeat identical failed commands.

Inference stays local in `llama-cli`; never start or expose a server, API, or listener. Keep answers concise, finish sentences and code blocks, and clearly say when evidence is incomplete."""
SYSTEM_PROMPT_FILE = BASE / "system_prompt.txt"


def load_system_prompt():
    if not SYSTEM_PROMPT_FILE.exists():
        SYSTEM_PROMPT_FILE.write_text(DEFAULT_SYSTEM_PROMPT + "\n", encoding="utf-8")
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()


def clean_truncation_marker(text):
    return text.replace(" ... (truncated)", "").replace("(truncated)", "")

TOOL_INSTRUCTIONS = """Commands start inside `workspace`; use `ls` and `cat app.py`, not `ls workspace` or `workspace/app.py`. If a named file is not included in the prompt, inspect it there before asking for contents; IDE tabs are not directly visible. Check required files exist before running a program; if one is missing, report it. If a program reads `input()`, supply stdin in the same command; never launch it interactively. Do not repeat successful file reads or identical failed commands. When a terminal command is needed, output exactly one complete block and no surrounding text: [[run]]command[[/run]]. WSL is the default; use [[run:gitbash]]command[[/run]], [[run:cmd]]command[[/run]], or [[run:powershell]]command[[/run]] to select another shell. Wait for the real exit code and output before proceeding. Make at most one command request per response. Never run interactive editors or REPLs. Edit with one noninteractive command, then reread or diff to verify. For Base64, pipe data to stdin: `printf '%s' 'DATA' | base64 -d`. For a text challenge, request `nc host port` alone first; the connection stays open for later commands. After each command result, continue from the supplied task summary and do not repeat completed steps."""


def workspace_inventory(limit=80, max_chars=1200):
    """Return a compact live listing of files available to the model."""
    files = sorted(
        path.relative_to(WORKSPACE).as_posix()
        for path in WORKSPACE.rglob("*")
        if path.is_file() and path.name != ".gitkeep"
    )
    shown = files[:limit]
    listing = "\n".join(f"- {name}" for name in shown) if shown else "(no files yet)"
    if len(files) > limit:
        listing += f"\n- ... and {len(files) - limit} more files"
    return listing[:max_chars]


def compact_history(history, max_messages=4, max_chars=700):
    """Keep recent dialogue context small; the complete history remains on disk."""
    compact = []
    for message in history[-max_messages:]:
        content = str(message.get("content", ""))
        if len(content) > max_chars:
            half = max_chars // 2
            content = content[:half] + "\n[older details omitted from model context]\n" + content[-half:]
        compact.append({"role": message.get("role", "user"), "content": content})
    return compact

TOOL_PATTERN = re.compile(
    r"(?:\[\[run(?::(gitbash|cmd|powershell|wsl))?\]\]|\[run\])\s*(.*?)"
    r"(?:\[\[/run\]\]|\|\|/run\]\]|<\|/run\|>|\[\\{0,2}/run\]|```)",
    re.I | re.S,
)
NC_PATTERN = re.compile(r"^nc\s+(?:-\w+\s+)*(?P<host>[A-Za-z0-9.-]+)\s+(?P<port>\d+)\s*$", re.I)
_ACTIVE_SOCKET = None


def challenge_session_active():
    return _ACTIVE_SOCKET is not None


def _read_challenge_response(sock):
    data = bytearray()
    sock.settimeout(15)
    while len(data) < MAX_OUTPUT_CHARS:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        data.extend(chunk)
        if b"Enter the Linux command to reverse it:" in data or b"Congratulations! You've recovered" in data:
            break
    return data.decode("utf-8", errors="replace").strip()


def find_tool_request(text):
    match = TOOL_PATTERN.search(text)
    if match:
        return (match.group(1) or "wsl").lower(), match.group(2).strip()
    # Phi sometimes returns a terminal command as a fenced example instead of
    # using the requested marker. Accept it only for nc setup or an active nc
    # session, where the command's intent is unambiguous.
    match = re.search(r"```(?:bash|sh)?\s*\n?([^`\r\n]+)\n?```", text, re.I)
    if match:
        command = match.group(1).strip()
        return "wsl", command
    match = re.search(r"```(?:run|terminal)\s+([^`\r\n]+)```", text, re.I)
    if match:
        return "wsl", match.group(1).strip()
    # Some local model responses imitate the tool transcript in plain text.
    # Treat the listed command as a request so it is actually run and its real
    # output replaces the model's fabricated trace in the following turn.
    trace_matches = list(re.finditer(r"\[Terminal tool:\s*(gitbash|cmd|powershell|wsl)\]\s*\n\$\s*([^\r\n]+)", text, re.I))
    if trace_matches:
        trace = trace_matches[-1]
        return trace.group(1).lower(), trace.group(2).strip()
    return None


def run_shell(shell, command, cwd=WORKSPACE):
    global _ACTIVE_SOCKET
    nc_match = NC_PATTERN.fullmatch(command.strip())
    if nc_match:
        if _ACTIVE_SOCKET:
            _ACTIVE_SOCKET.close()
        try:
            _ACTIVE_SOCKET = socket.create_connection(
                (nc_match.group("host"), int(nc_match.group("port"))), timeout=10,
            )
            return 0, _read_challenge_response(_ACTIVE_SOCKET)
        except OSError as exc:
            _ACTIVE_SOCKET = None
            return 1, f"Could not connect to {nc_match.group('host')}:{nc_match.group('port')}: {exc}"

    if _ACTIVE_SOCKET:
        try:
            submitted_command = command
            if re.search(r"\bbase64\b", command, re.I):
                submitted_command = "base64 -d"
            _ACTIVE_SOCKET.sendall(submitted_command.encode("utf-8") + b"\n")
            output = _read_challenge_response(_ACTIVE_SOCKET)
            if submitted_command != command:
                output = f"[Challenge adapter submitted `{submitted_command}` for the Base64 reversal.]\n{output}"
            if "Congratulations! You've recovered" in output:
                _ACTIVE_SOCKET.close()
                _ACTIVE_SOCKET = None
            return 0, output or "(no output)"
        except OSError as exc:
            _ACTIVE_SOCKET.close()
            _ACTIVE_SOCKET = None
            return 1, f"Challenge connection failed: {exc}"

    if shell == "gitbash":
        executable = GIT_BASH if GIT_BASH.exists() else Path(shutil.which("bash.exe") or "")
        if not executable or not executable.exists():
            return 127, "Git Bash was not found. Install Git for Windows or check its installation path."
        command = re.sub(r"\bbase64-decode\b", "base64 -d", command, flags=re.I)
        decode_arg = re.fullmatch(
            r"base64\s+(?:-d|--decode)\s+(?:'([A-Za-z0-9+/=]+)'|\"([A-Za-z0-9+/=]+)\"|([A-Za-z0-9+/=]+))",
            command.strip(), re.I,
        )
        if decode_arg:
            encoded = next(group for group in decode_arg.groups() if group is not None)
            command = f"printf '%s' '{encoded}' | base64 -d"
        rev_inline = re.fullmatch(r"rev([^\s].*)", command.strip(), re.I)
        if rev_inline:
            value = rev_inline.group(1).strip().strip("'\"")
            command = f"printf '%s' '{value}' | rev"
        args = [str(executable), "--noprofile", "--norc", "-c", command]
    elif shell == "wsl":
        wsl = shutil.which("wsl.exe") or shutil.which("wsl")
        if not wsl:
            return 127, "WSL was not found. Install WSL and an Ubuntu distribution."
        try:
            path_result = subprocess.run(
                [wsl, "--exec", "wslpath", "-a", str(cwd)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return 124, "Timed out while resolving the project directory in WSL."
        except OSError as exc:
            return 126, f"Could not start WSL: {exc}"
        if path_result.returncode != 0:
            detail = (path_result.stdout + path_result.stderr).strip()
            return path_result.returncode, f"Could not resolve the project directory in WSL: {detail}"
        linux_cwd = path_result.stdout.strip()
        if not linux_cwd:
            return 1, "WSL did not return a Linux path for the project directory."
        wsl_command = f"cd -- {shlex.quote(linux_cwd)} && {command}"
        args = [wsl, "--exec", "bash", "-lc", wsl_command]
    elif shell == "cmd":
        args = ["cmd.exe", "/d", "/s", "/c", command]
    elif shell == "powershell":
        args = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        return 2, f"Unsupported shell: {shell}"

    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=TOOL_TIMEOUT_SECONDS,
        )
        output = (result.stdout + result.stderr).strip()
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n[output truncated]"
        return result.returncode, output or "(no output)"
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output = output[-MAX_OUTPUT_CHARS:]
        return 124, f"Command timed out after {TOOL_TIMEOUT_SECONDS} seconds.\n{output}"
    except OSError as exc:
        return 126, f"Could not start {shell}: {exc}"


def tool_result(shell, command, code, output):
    return f"[Terminal tool: {shell}]\n$ {command}\nExit code: {code}\n{output}"
