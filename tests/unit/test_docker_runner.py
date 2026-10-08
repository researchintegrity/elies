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
        if "--format" in sys.argv:  # managed tool containers: "<id> <worker label>"
            sys.stdout.write(os.environ.get("FAKE_DOCKER_TOOLS", ""))
        else:  # running container ids
            sys.stdout.write(os.environ.get("FAKE_DOCKER_RUNNING", ""))
    elif command == "run":
        if mode == "chatty":
            sys.stderr.write("w" * 300000)
            sys.stderr.flush()
            print("[STATUS] loading model", flush=True)
            print("[STATUS] done", flush=True)
        elif mode == "binary":
            sys.stdout.buffer.write(b"caf\\xe9 \\xff\\xfe progress\\n")
            sys.stdout.flush()
            sys.stderr.write("e" * 300000)
            print("ok", flush=True)
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


def test_killed_tool_is_reported_as_out_of_memory():
    run = docker_runner.ContainerRun(name="t", returncode=137, stdout="", stderr="Creating settings file",
                                     timed_out=False, duration=6.0)
    assert "out of memory" in run.describe_failure() and "DOCKER_TOOL_MEMORY" in run.describe_failure()


def test_orphans_of_gone_workers_are_killed(fake_docker, monkeypatch):
    live_worker, gone_worker = "b" * 12, "a" * 12
    monkeypatch.setenv("FAKE_DOCKER_TOOLS", "\n".join([
        f"t-own {docker_runner.WORKER_LABEL}",   # started by an earlier run of this worker
        f"t-gone {gone_worker}",                 # its worker container was recreated
        f"t-live {live_worker}",                 # another worker, still running
        "t-host laptop",                         # a worker running directly on a host
    ]) + "\n")
    monkeypatch.setenv("FAKE_DOCKER_RUNNING", live_worker + "c" * 52 + "\n")

    assert kill_orphaned_containers() == 2
    log = fake_docker.read_text()
    assert "kill t-own" in log and "kill t-gone" in log
    assert "kill t-live" not in log and "kill t-host" not in log


def test_non_utf8_tool_output_does_not_stall_the_run(fake_docker, monkeypatch):
    monkeypatch.setenv("FAKE_DOCKER_MODE", "binary")
    started = time.monotonic()
    run = run_tool_container("tool:latest", [], [], timeout=20, purpose="test")
    assert run.ok and not run.timed_out
    assert time.monotonic() - started < 10
    assert "\ufffd" in run.stdout and "ok" in run.stdout
