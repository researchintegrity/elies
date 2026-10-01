"""The shared Docker tool runner (#67), exercised with a fake docker executable."""
import stat
import sys
import textwrap
import time

import pytest

from app.config import settings
from app.exceptions import DockerUnavailableError
from app.utils import docker_runner
from app.utils.docker_runner import Mount, build_command, kill_orphaned_containers, run_tool_container

FAKE_DOCKER = textwrap.dedent(f"""\
    #!{sys.executable}
    import os, sys, time
    with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
        log.write(" ".join(sys.argv[1:]) + "\\n")
    command = sys.argv[1]
    mode = os.environ.get("FAKE_DOCKER_MODE", "ok")
    if command == "ps":
        print("abc123")
        print("def456")
    elif command == "run":
        if mode == "chatty":
            sys.stderr.write("w" * 300000)
            sys.stderr.flush()
            print("[STATUS] loading model", flush=True)
            print("[STATUS] done", flush=True)
        elif mode == "hang":
            time.sleep(30)
        elif mode == "nodaemon":
            sys.stderr.write("failed to connect to the docker API at unix:///var/run/docker.sock")
            sys.exit(1)
        elif mode == "fail":
            sys.stderr.write("tool crashed")
            sys.exit(2)
        else:
            print("ok")
""")


@pytest.fixture
def fake_docker(tmp_path, monkeypatch):
    script = tmp_path / "docker"
    script.write_text(FAKE_DOCKER)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "docker.log"
    log.touch()
    monkeypatch.setattr(settings, "DOCKER_BINARY", str(script))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(log))
    return log


def test_command_is_sandboxed_and_inputs_read_only(monkeypatch):
    monkeypatch.setattr(settings, "DOCKER_TOOL_MEMORY", "4g")
    monkeypatch.setattr(settings, "DOCKER_TOOL_CPUS", "2")
    cmd = build_command(
        "tool:latest", ["--in", "/input/a.png"],
        [Mount("/data/in", "/input"), Mount("/data/out", "/output", read_only=False)],
        name="elies-test-1", env={"MODE": "fast"},
    )
    joined = " ".join(cmd)
    assert cmd[:3] == [settings.DOCKER_BINARY, "run", "--rm"]
    assert "--name elies-test-1" in joined
    assert "--network none" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--pids-limit 1024" in joined
    assert "--memory 4g" in joined and "--cpus 2" in joined
    assert "-v /data/in:/input:ro" in joined
    assert "-v /data/out:/output " in joined + " "
    assert "-e MODE=fast" in joined
    assert cmd[-3:] == ["tool:latest", "--in", "/input/a.png"]


def test_chatty_container_does_not_deadlock_and_streams_stdout(fake_docker, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "chatty")
    lines = []
    result = run_tool_container("tool", timeout=20, on_stdout_line=lines.append)
    assert result.ok
    assert len(result.stderr) == 300000
    assert lines == ["[STATUS] loading model", "[STATUS] done"]


def test_timeout_kills_the_named_container(fake_docker, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "hang")
    started = time.monotonic()
    result = run_tool_container("tool", timeout=1, purpose="slow")
    assert time.monotonic() - started < 15
    assert result.timed_out and not result.ok
    assert f"kill {result.name}" in fake_docker.read_text()
    assert result.name.startswith("elies-slow-")


def test_unreachable_daemon_is_a_transient_error(fake_docker, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "nodaemon")
    with pytest.raises(DockerUnavailableError):
        run_tool_container("tool", timeout=10)


def test_missing_docker_cli_is_a_transient_error(monkeypatch):
    monkeypatch.setattr(settings, "DOCKER_BINARY", "/nonexistent/docker")
    with pytest.raises(DockerUnavailableError):
        run_tool_container("tool", timeout=10)


def test_tool_failure_is_reported(fake_docker, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "fail")
    result = run_tool_container("tool", timeout=10)
    assert not result.ok and result.returncode == 2
    assert "tool crashed" in result.describe_failure()


def test_orphans_from_this_worker_are_killed(fake_docker):
    assert kill_orphaned_containers() == 2
    log = fake_docker.read_text()
    assert f"label=elies.worker={docker_runner.WORKER_LABEL}" in log
    assert "kill abc123" in log and "kill def456" in log
