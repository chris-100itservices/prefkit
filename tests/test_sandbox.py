"""Tests for the sandbox. Docker is replaced by a fake, so these run without Docker."""

import subprocess

import pytest

from prefkit import sandbox


class FakeDocker:
    """Records docker calls and answers as a real Docker would, for the cases we set."""

    def __init__(self, run_result=None, run_raises=None, info_code=0):
        self.calls = []
        self.run_result = run_result
        self.run_raises = run_raises
        self.info_code = info_code

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(args, self.info_code, "", "")
        if args[:2] == ["docker", "kill"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if self.run_raises is not None:
            raise self.run_raises
        return self.run_result


@pytest.fixture(autouse=True)
def fresh_docker_check(monkeypatch):
    """Each test starts as if Docker had not been checked yet in this process."""
    monkeypatch.setattr(sandbox, "_docker_checked", False)


@pytest.fixture
def docker_present(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker")


def install(monkeypatch, fake):
    monkeypatch.setattr(sandbox.subprocess, "run", fake)


def test_command_has_network_and_resource_limits():
    cmd = sandbox.docker_command("box", timeout_s=5, memory="512m")
    assert cmd[cmd.index("--network") + 1] == "none"
    assert cmd[cmd.index("--memory") + 1] == "512m"
    assert cmd[cmd.index("--memory-swap") + 1] == "512m"
    assert cmd[cmd.index("--pids-limit") + 1] == "64"
    assert "--read-only" in cmd
    assert "--rm" in cmd
    assert "--privileged" not in cmd
    assert cmd[cmd.index("--user") + 1] == "65534:65534"


def test_command_stops_the_program_after_timeout():
    cmd = sandbox.docker_command("box", timeout_s=7, memory="512m")
    assert cmd[cmd.index("timeout") + 1] == "7"
    assert cmd[-3:] == ["python", "-I", "-"]


def test_missing_docker_refuses_to_run(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    fake = FakeDocker()
    install(monkeypatch, fake)
    with pytest.raises(sandbox.SandboxUnavailable, match="not installed"):
        sandbox.run_in_sandbox("print(1)")
    assert fake.calls == []  # nothing was run


def test_stopped_docker_refuses_to_run(docker_present, monkeypatch):
    fake = FakeDocker(info_code=1)
    install(monkeypatch, fake)
    with pytest.raises(sandbox.SandboxUnavailable, match="not running"):
        sandbox.run_in_sandbox("print(1)")


def test_successful_program_is_ok(docker_present, monkeypatch):
    fake = FakeDocker(run_result=subprocess.CompletedProcess([], 0, "1\n", ""))
    install(monkeypatch, fake)
    result = sandbox.run_in_sandbox("print(1)")
    assert result.ok
    assert result.stdout == "1\n"
    assert sandbox.passes("print(1)") is True


def test_failing_program_is_not_ok(docker_present, monkeypatch):
    fake = FakeDocker(run_result=subprocess.CompletedProcess([], 1, "", "AssertionError"))
    install(monkeypatch, fake)
    result = sandbox.run_in_sandbox("assert False")
    assert not result.ok
    assert result.exit_code == 1


def test_exit_code_124_counts_as_timeout(docker_present, monkeypatch):
    fake = FakeDocker(run_result=subprocess.CompletedProcess([], 124, "", ""))
    install(monkeypatch, fake)
    result = sandbox.run_in_sandbox("while True: pass")
    assert result.timed_out
    assert not result.ok


def test_backstop_timeout_kills_the_container(docker_present, monkeypatch):
    fake = FakeDocker(run_raises=subprocess.TimeoutExpired(cmd="docker", timeout=35))
    install(monkeypatch, fake)
    result = sandbox.run_in_sandbox("while True: pass")
    assert result.timed_out
    kills = [args for args, _ in fake.calls if args[:2] == ["docker", "kill"]]
    assert len(kills) == 1
    assert kills[0][2].startswith("prefkit-sandbox-")


def test_code_is_sent_on_stdin_not_in_the_command(docker_present, monkeypatch):
    fake = FakeDocker(run_result=subprocess.CompletedProcess([], 0, "", ""))
    install(monkeypatch, fake)
    sandbox.run_in_sandbox("print('hello')")
    args, kwargs = next((a, k) for a, k in fake.calls if a[:2] == ["docker", "run"])
    assert kwargs["input"] == "print('hello')"
    assert "print('hello')" not in args


def test_docker_is_checked_once_per_process(docker_present, monkeypatch):
    fake = FakeDocker(run_result=subprocess.CompletedProcess([], 0, "", ""))
    install(monkeypatch, fake)
    sandbox.run_in_sandbox("print(1)")
    sandbox.run_in_sandbox("print(2)")
    infos = [args for args, _ in fake.calls if args[:2] == ["docker", "info"]]
    assert len(infos) == 1


def test_docker_failing_during_a_run_is_an_error_not_a_failing_program(docker_present, monkeypatch):
    # Exit code 125 means Docker itself failed. Counting it as "tests rejected the program"
    # would make the tests look stronger than they are.
    failed = subprocess.CompletedProcess([], 125, "", "Cannot connect to the Docker daemon")
    fake = FakeDocker(run_result=failed)
    install(monkeypatch, fake)
    with pytest.raises(sandbox.SandboxUnavailable, match="stopped working"):
        sandbox.run_in_sandbox("print(1)")
    assert sandbox._docker_checked is False  # checked again on the next run
