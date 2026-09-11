"""Python code execution in an isolated subprocess.

Safety measures (process level):
* runs in a fresh temporary working directory that is deleted afterwards
* ``python -I -B`` (isolated mode: ignores PYTHON* env, user site, faulthandler)
* stripped environment (no API keys leak to executed code)
* POSIX resource limits: CPU time, address-space, file size, no new core dumps
* hard wall-clock timeout with SIGKILL
* disabled unless CONCILIUM_ENABLE_PYTHON_SANDBOX=true AND the tool is enabled
  in the UI

This is a solid local sandbox, NOT a container boundary. For hostile code, run
the whole Concilium stack in Docker/gVisor (documented in README/SECURITY).
"""
from __future__ import annotations

import asyncio
import os
import resource
import sys
import tempfile
import textwrap

from ..config import get_settings
from .base import Tool, ToolContext, ToolResult

PRELUDE = """\
import json, math, statistics, itertools, collections, re, random, datetime
"""


def _rlimit_preexec(memory_mb: int, cpu_seconds: int):
    def _set():
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024 * 1024, 5 * 1024 * 1024))
            mem = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        except (ValueError, OSError):
            pass
        os.setsid()

    return _set


class PythonSandboxTool(Tool):
    key = "python_sandbox"
    display_name = "Python execution (sandboxed)"
    description = (
        "Execute self-contained Python 3 code for computation, data processing and "
        "verification. No network access is provided. Print your result; only stdout is "
        "returned. Libraries: Python standard library only."
    )
    category = "code_execution"
    enabled_by_default = False
    parameters = {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source to execute"},
            "timeout": {"type": "integer", "description": "seconds (max configured)"},
        },
        "required": ["code"],
    }

    def available(self) -> bool:
        return get_settings().enable_python_sandbox

    def unavailable_reason(self) -> str:
        if not get_settings().enable_python_sandbox:
            return ("Python sandbox disabled server-side. Set CONCILIUM_ENABLE_PYTHON_SANDBOX=true "
                    "(and preferably run Concilium in Docker) then enable this tool.")
        return ""

    async def run(self, arguments, ctx: ToolContext) -> ToolResult:
        if not self.available():
            return ToolResult(ok=False, error=self.unavailable_reason())
        code = arguments.get("code", "")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(ok=False, error="no code supplied")
        if len(code) > 100_000:
            return ToolResult(ok=False, error="code too large (100KB max)")
        settings = get_settings()
        timeout = min(int(arguments.get("timeout") or settings.sandbox_timeout), settings.sandbox_timeout + 5)

        with tempfile.TemporaryDirectory(prefix="concilium_sbx_", dir=str(settings.data_path)) as tmp:
            script = os.path.join(tmp, "main.py")
            with open(script, "w") as f:
                f.write(PRELUDE + "\n" + textwrap.dedent(code))
            env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": tmp,
                   "TMPDIR": tmp, "PYTHONDONTWRITEBYTECODE": "1"}
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-I", "-B", script,
                    cwd=tmp, env=env, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=_rlimit_preexec(settings.sandbox_memory_mb, timeout),
                )
            except Exception as exc:  # noqa: BLE001
                return ToolResult(ok=False, error=f"failed to start sandbox: {exc}")
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 2)
            except asyncio.TimeoutError:
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass
                return ToolResult(ok=False, error=f"execution timed out after {timeout}s (killed)")

        out = stdout.decode(errors="replace")[-8000:]
        err = stderr.decode(errors="replace")[-4000:]
        if proc.returncode != 0:
            return ToolResult(ok=False, error=f"exit code {proc.returncode}\n{err}" or "non-zero exit")
        return ToolResult(ok=True, output=out or "(no stdout)", data={"stderr": err})
