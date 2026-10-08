"""
One place to run analysis tools in Docker containers (issue #67).

Every tool container:
- gets a unique name and ELIES labels, so it can be killed on timeout (killing
  the ``docker`` CLI alone leaves the container running) and found again if a
  worker restarts mid-run;
- has a wall-clock timeout;
- mounts its inputs read-only (only output directories are writable);
- runs without network access, with no-new-privileges and a PID limit by
  default, plus optional memory/CPU limits and user (see settings);
- has both stdout and stderr drained concurrently, so a chatty tool cannot
  dead-lock the worker on a full pipe.
"""
import logging
import re
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

from app.config import settings
from app.config.settings import convert_container_path_to_host
from app.exceptions import DockerUnavailableError

logger = logging.getLogger(__name__)

# Identifies containers started by this worker (the hostname survives restarts
# of the same worker container, so leftovers can be cleaned up on start)
WORKER_LABEL = socket.gethostname()
# Docker sets a container's hostname to its short id
_CONTAINER_HOSTNAME = re.compile(r"[0-9a-f]{12}")
MANAGED_LABEL = "elies.managed=true"

_DAEMON_UNAVAILABLE_MARKERS = (
    "cannot connect to the docker daemon",
    "failed to connect to the docker api",
    "is the docker daemon running",
    "error during connect",
)
_KILL_TIMEOUT = 30


@dataclass(frozen=True)
class Mount:
    """A bind mount. ``source`` is a path as seen by this process."""
    source: Union[str, Path]
    target: str
    read_only: bool = True

    def as_volume_arg(self) -> str:
        host = str(convert_container_path_to_host(Path(self.source)))
        return f"{host}:{self.target}" + (":ro" if self.read_only else "")


@dataclass
class ContainerRun:
    """Outcome of a tool container run."""
    name: str
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    duration: float
    command: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0

    def describe_failure(self, limit: int = 2000) -> str:
        if self.timed_out:
            return f"timed out after {self.duration:.0f}s"
        if self.returncode == 137:  # SIGKILL: the kernel's OOM killer when a memory limit is set
            return "killed (exit code 137), most likely out of memory: raise DOCKER_TOOL_MEMORY"
        detail = (self.stderr or self.stdout).strip()[-limit:]
        return f"exit code {self.returncode}" + (f": {detail}" if detail else "")


def build_command(
    image: str,
    args: Sequence[str],
    mounts: Sequence[Mount],
    name: str,
    env: Optional[Dict[str, str]] = None,
    gpu: bool = False,
) -> List[str]:
    """The full ``docker run`` command line for a tool container."""
    cmd = [
        settings.DOCKER_BINARY, "run", "--rm",
        "--name", name,
        "--label", MANAGED_LABEL,
        "--label", f"elies.worker={WORKER_LABEL}",
        "--security-opt", "no-new-privileges",
    ]
    if settings.DOCKER_TOOL_NETWORK:
        cmd += ["--network", settings.DOCKER_TOOL_NETWORK]
    if settings.DOCKER_TOOL_PIDS_LIMIT:
        cmd += ["--pids-limit", str(settings.DOCKER_TOOL_PIDS_LIMIT)]
    if settings.DOCKER_TOOL_MEMORY:
        cmd += ["--memory", settings.DOCKER_TOOL_MEMORY]
    if settings.DOCKER_TOOL_CPUS:
        cmd += ["--cpus", settings.DOCKER_TOOL_CPUS]
    if settings.DOCKER_TOOL_USER:
        cmd += ["--user", settings.DOCKER_TOOL_USER]
    if gpu:
        cmd += ["--runtime=nvidia", "--gpus", "all"]
    for mount in mounts:
        cmd += ["-v", mount.as_volume_arg()]
    for key, value in (env or {}).items():
        cmd += ["-e", f"{key}={value}"]
    cmd.append(image)
    cmd += [str(arg) for arg in args]
    return cmd


def _drain(stream, sink: List[str], on_line: Optional[Callable[[str], None]]) -> None:
    for line in iter(stream.readline, ""):
        sink.append(line)
        if on_line:
            try:
                on_line(line.rstrip("\n"))
            except Exception:
                logger.exception("Error in container output callback")
    stream.close()


def kill_container(name: str) -> None:
    try:
        subprocess.run([settings.DOCKER_BINARY, "kill", name], capture_output=True, timeout=_KILL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.error("Could not kill container %s: %s", name, e)


def _daemon_unavailable(stderr: str) -> bool:
    lowered = stderr.lower()
    return any(marker in lowered for marker in _DAEMON_UNAVAILABLE_MARKERS)


def run_tool_container(
    image: str,
    args: Sequence[str] = (),
    mounts: Sequence[Mount] = (),
    *,
    timeout: int,
    env: Optional[Dict[str, str]] = None,
    gpu: bool = False,
    purpose: str = "tool",
    on_stdout_line: Optional[Callable[[str], None]] = None,
) -> ContainerRun:
    """
    Run a tool container to completion or until ``timeout`` seconds pass.

    Raises:
        DockerUnavailableError: the Docker CLI or daemon is unreachable
            (transient: callers may retry).
    """
    name = f"elies-{purpose}-{uuid.uuid4().hex[:12]}"
    cmd = build_command(image, args, mounts, name, env=env, gpu=gpu)
    logger.info("Starting container %s: %s", name, " ".join(cmd))

    started = time.monotonic()
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                                   encoding="utf-8", errors="replace")
    except OSError as e:
        raise DockerUnavailableError(f"Cannot run docker: {e}") from e

    stdout_lines: List[str] = []
    stderr_lines: List[str] = []
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout_lines, on_stdout_line), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr_lines, None), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    finished = False
    try:
        process.wait(timeout=timeout)
        finished = True
    except subprocess.TimeoutExpired:
        timed_out = True
        logger.error("Container %s exceeded %ss; killing it", name, timeout)
    finally:
        # Also reached when the Celery soft time limit interrupts the wait
        if not finished:
            kill_container(name)
            process.kill()
            process.wait()
        for reader in readers:
            reader.join(timeout=10)

    result = ContainerRun(
        name=name,
        returncode=process.returncode,
        stdout="".join(stdout_lines),
        stderr="".join(stderr_lines),
        timed_out=timed_out,
        duration=time.monotonic() - started,
        command=cmd,
    )
    if result.returncode not in (0, None) and not timed_out and _daemon_unavailable(result.stderr):
        raise DockerUnavailableError(f"Docker daemon unavailable: {result.stderr.strip()[:500]}")
    if not result.ok:
        logger.warning("Container %s failed: %s", name, result.describe_failure(500))
    return result


def _docker_lines(args: List[str]) -> Optional[List[str]]:
    try:
        listed = subprocess.run([settings.DOCKER_BINARY, *args], capture_output=True, text=True,
                                timeout=_KILL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.warning("Could not list containers: %s", e)
        return None
    return [line.strip() for line in listed.stdout.splitlines() if line.strip()]


def kill_orphaned_containers() -> int:
    """
    Kill tool containers whose worker is gone: those started by an earlier
    run of this worker (same hostname: a restarted container) and those
    whose worker container no longer runs (in Docker the hostname is the
    worker container's short id, which changes when compose recreates it).
    Containers of live workers, and of workers running directly on a host,
    are left alone. Returns how many were killed.
    """
    tools = _docker_lines(["ps", "--filter", f"label={MANAGED_LABEL}",
                           "--format", '{{.ID}} {{.Label "elies.worker"}}'])
    running = _docker_lines(["ps", "-q", "--no-trunc"])
    if tools is None or running is None:
        return 0

    killed = 0
    for line in tools:
        container_id, _, owner = line.partition(" ")
        owner_gone = bool(_CONTAINER_HOSTNAME.fullmatch(owner)) and not any(
            running_id.startswith(owner) for running_id in running)
        if owner == WORKER_LABEL or owner_gone:
            kill_container(container_id)
            killed += 1
    if killed:
        logger.warning("Killed %d orphaned tool container(s)", killed)
    return killed
