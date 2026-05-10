import subprocess
import platform
from typing import Tuple

def run_cmd(cmd: list[str], shell: bool = False, timeout: int = 60) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, shell=shell, timeout=timeout)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)

def is_windows() -> bool:
    return platform.system().lower() == "windows"

def powershell(ps: str, timeout: int = 60) -> Tuple[int, str, str]:
    return run_cmd(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        timeout=timeout,
    )
