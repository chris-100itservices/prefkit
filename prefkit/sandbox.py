"""Run untrusted Python code inside a Docker container with no network access.

Why a container: the container has its own file system and its own list of
processes. The code cannot see your files, because nothing from your computer
is mounted into it.

Why it fails closed: if Docker is missing or not running, this module raises
SandboxUnavailable. It never falls back to running the code on your computer.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass

IMAGE = "python:3.12-slim"
DEFAULT_TIMEOUT_S = 5
DEFAULT_MEMORY = "512m"
TIMEOUT_EXIT_CODE = 124  # what the `timeout` command returns when it stops a program
DOCKER_CHECK_TIMEOUT_S = 30
STARTUP_ALLOWANCE_S = 30  # extra time for Docker to start the container


class SandboxUnavailable(RuntimeError):
    """Docker is missing or not running, so the code must not be run."""


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0


def docker_command(name: str, timeout_s: int, memory: str) -> list[str]:
    """The docker command line. Each flag is one limit, explained in the comments."""
    return [
        "docker", "run",
        "--rm",                          # delete the container when the run ends
        "-i",                            # keep stdin open, so the code can be sent in
        "--name", name,                  # a name, so a stuck run can be killed by name
        "--network", "none",             # no network: the code cannot send or fetch anything
        "--memory", memory,              # memory cap
        "--memory-swap", memory,         # same value, so swap cannot raise the cap
        "--cpus", "1",                   # one CPU core at most
        "--pids-limit", "64",            # caps the number of processes, stopping fork bombs
        "--read-only",                   # the container's file system cannot be changed
        "--tmpfs", "/tmp:rw,size=64m,noexec",  # a small scratch space that cannot run programs
        "--cap-drop", "ALL",             # drop all Linux privileges
        "--security-opt", "no-new-privileges",  # a program cannot gain privileges later
        "--user", "65534:65534",         # run as user "nobody", not as root
        IMAGE,
        "timeout", str(timeout_s),       # stop the program after timeout_s seconds
        "python", "-I", "-",             # -I: ignore environment variables; "-": read code from stdin
    ]


# Set once Docker has answered, so later runs in the same process skip the check. Checking
# before every program would add a second Docker call to each of thousands of runs.
_docker_checked = False

# What `docker run` returns when Docker itself failed, before the program could start.
DOCKER_FAILED_EXIT_CODE = 125


def ensure_docker() -> None:
    """Raise SandboxUnavailable unless the docker command exists and the daemon answers."""
    global _docker_checked
    if _docker_checked:
        return
    if shutil.which("docker") is None:
        raise SandboxUnavailable("Docker is not installed. Install Docker Desktop, then try again.")
    probe = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, timeout=DOCKER_CHECK_TIMEOUT_S
    )
    if probe.returncode != 0:
        raise SandboxUnavailable("Docker is installed but not running. Start Docker Desktop, then try again.")
    _docker_checked = True


def docker_running(timeout_s: int = 5) -> bool:
    """True when Docker answers now. Unlike ensure_docker, it asks every time it is called,
    so the web page can show when Docker has been stopped or started."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return False
    return probe.returncode == 0


def run_in_sandbox(
    code: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    memory: str = DEFAULT_MEMORY,
) -> RunResult:
    """Run one Python program in the sandbox and report what happened.

    If Docker stops during a run, `docker run` fails with exit code 125. That is raised as
    SandboxUnavailable instead of being reported as a failing program, because a failing
    program means "the tests rejected it", and that would be a false result.
    """
    global _docker_checked
    ensure_docker()
    name = f"prefkit-sandbox-{uuid.uuid4().hex}"
    try:
        done = subprocess.run(
            docker_command(name, timeout_s, memory),
            input=code,
            capture_output=True,
            text=True,
            timeout=timeout_s + STARTUP_ALLOWANCE_S,  # backstop if the inner timeout fails
        )
    except subprocess.TimeoutExpired:
        # The backstop fired: stop the container itself, since killing the client does not.
        subprocess.run(["docker", "kill", name], capture_output=True)
        return RunResult(exit_code=None, stdout="", stderr="", timed_out=True)

    if done.returncode == DOCKER_FAILED_EXIT_CODE:
        _docker_checked = False  # check again next time, in case Docker has been restarted
        raise SandboxUnavailable(
            "Docker stopped working during the run. Start Docker Desktop, then try again. "
            f"Docker said: {done.stderr.strip()[:300]}"
        )

    return RunResult(
        exit_code=done.returncode,
        stdout=done.stdout,
        stderr=done.stderr,
        timed_out=done.returncode == TIMEOUT_EXIT_CODE,
    )


def passes(code: str, timeout_s: int = DEFAULT_TIMEOUT_S, memory: str = DEFAULT_MEMORY) -> bool:
    """True when the program exits with code 0 inside the limits. Used for unit tests."""
    return run_in_sandbox(code, timeout_s=timeout_s, memory=memory).ok
