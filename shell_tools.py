"""Small synchronous shell tool used by the local model runners."""
from pathlib import Path
import re
import shlex
import socket
import shutil
import subprocess

BASE = Path(__file__).resolve().parent
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
MAX_TOOL_CALLS = 12
TOOL_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 12000

DEFAULT_SYSTEM_PROMPT = """Solve authorized CTF tasks carefully. Verify transformations and preserve exact bytes and case. Use terminal output as evidence; never invent results or call a guessed flag verified. Stay within the supplied challenge scope.

Use Ubuntu in WSL by default. Request commands only as one complete `[[run]]command[[/run]]` block, then wait for the actual result before choosing the next step. Keep commands short and valid. For Base64 input, pipe it to stdin, e.g. `printf '%s' 'DATA' | base64 -d`; `base64 -d 'DATA'` treats DATA as a filename. If a command fails, use the error output to correct it.

Inference stays local in `llama-cli`; never start or expose a server, API, or listener. Keep answers concise, finish sentences and code blocks, and clearly say when evidence is incomplete."""
SYSTEM_PROMPT_FILE = BASE / "system_prompt.txt"


def load_system_prompt():
    if not SYSTEM_PROMPT_FILE.exists():
        SYSTEM_PROMPT_FILE.write_text(DEFAULT_SYSTEM_PROMPT + "\n", encoding="utf-8")
    return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()


def clean_truncation_marker(text):
    return text.replace(" ... (truncated)", "").replace("(truncated)", "")

TOOL_INSTRUCTIONS = """When a terminal command is needed, output exactly one complete command block and no surrounding text: [[run]]command[[/run]]. WSL is the default; use [[run:gitbash]]command[[/run]], [[run:cmd]]command[[/run]], or [[run:powershell]]command[[/run]] to select another shell. Wait for the real exit code and output before proceeding. Make at most one command request per response. For Base64, pipe data into stdin: `printf '%s' 'DATA' | base64 -d` (never pass DATA as a filename). For a text challenge, request `nc host port` alone first; the connection stays open for later commands."""

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


def run_shell(shell, command, cwd=BASE):
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
