import enum
import json
import subprocess
from contextlib import contextmanager
import time
from typing import Generator, NamedTuple, Optional

import typer
from rich.console import Console

err_console = Console(stderr=True)


class RunAIInteractiveMode(str, enum.Enum):
    # Simple type, simply runs a shell
    SHELL = "shell"
    # Forwards a port
    PORT = "port"


class RunAIJobStatus(enum.Enum):
    PENDING = enum.auto()
    CONTAINERCREATING = enum.auto()
    RUNNING = enum.auto()
    NOT_READY = enum.auto()
    DOES_NOT_EXISTS = enum.auto()

    @classmethod
    def from_str(cls, value: str) -> "RunAIJobStatus":
        return getattr(cls, value.upper(), RunAIJobStatus.NOT_READY)


class RunAIJobDetails(NamedTuple):
    name: str
    pod_name: str
    status: RunAIJobStatus


def log_error(msg: str):
    err_console.print(f"ERROR: {msg}")


def check_command(*command: str) -> bool:
    """Check whether the command executed successfully

    Args:
        command (list[str]): The command to check

    Returns:
        bool: True if the command executed successfully,
            False on non-zero return code.
    """
    try:
        process = subprocess.run(command, capture_output=True)
    except FileNotFoundError:
        return False
    return process.returncode == 0


def get_runai_job_status(job_name: str) -> RunAIJobDetails:
    process = subprocess.run(
        ["runai", "describe", "job", job_name, "--output", "json"], capture_output=True
    )
    if process.returncode != 0:
        return RunAIJobDetails(job_name, job_name, RunAIJobStatus.DOES_NOT_EXISTS)

    payload = json.loads(process.stdout)
    return RunAIJobDetails(
        payload["name"],
        payload["chiefName"],
        RunAIJobStatus.from_str(payload["status"]),
    )


def wait_until_job_started(job_name: str) -> RunAIJobDetails:
    notified_container_creating = False
    while (job := get_runai_job_status(job_name)).status != RunAIJobStatus.RUNNING:
        if job.status == RunAIJobStatus.DOES_NOT_EXISTS:
            log_error(f"Job {job_name} does not exists.")
            raise typer.Exit(code=1)
        if (
            job.status == RunAIJobStatus.CONTAINERCREATING
            and not notified_container_creating
        ):
            print("Creating container...")
            notified_container_creating = True
        time.sleep(5)
    return job


@contextmanager
def runai_submit_interactive_job(
    job_name: str, image: str, command: list[str]
) -> Generator[RunAIJobDetails, None, None]:
    process = subprocess.run(
        ["runai", "submit", job_name, "-i", image, "--interactive"] + command
    )
    if process.returncode != 0:
        log_error("Could not submit job to RunAI")
        raise typer.Exit(code=1)

    try:
        print("Waiting for the job to start...")
        yield wait_until_job_started(job_name)
    finally:
        subprocess.run(["runai", "delete", "job", job_name])


def kubectl_output_extract_forwarded_port(stdout_line: bytes) -> Optional[int]:
    if not stdout_line.startswith(b"Forwarding"):
        return None

    # Keep after ":" in 127.0.0.1:12345 -> 8888
    _, ports_map = stdout_line.split(b":")
    # Take the source port
    src_port, _ = ports_map.split(b" -> ")
    return int(src_port)


@contextmanager
def kubectl_pod_forward_port(
    pod_name: str, container_port: int
) -> Generator[int, None, None]:
    with subprocess.Popen(
        ["kubectl", "port-forward", f"pods/{pod_name}", f":{container_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            port = kubectl_output_extract_forwarded_port(line)
            if port is not None:
                try:
                    yield port
                finally:
                    proc.terminate()


def _wait_until_interupted():
    while True:
        time.sleep(10)


def _handle_shell_context(job: RunAIJobDetails):
    print(f"Interactive session started, you can connect with `runai bash {job.name}`")
    _wait_until_interupted()


def _handle_port_context(job: RunAIJobDetails, container_port: int):
    with kubectl_pod_forward_port(job.pod_name, container_port) as local_port:
        print(f"The application is running at http://localhost:{local_port}")
        _wait_until_interupted()


def interactive_context(
    job_name: str,
    image: str,
    args: Optional[list[str]] = typer.Argument(
        None, help="Additional arguments passed to `runai submit`"
    ),
    mode: RunAIInteractiveMode = RunAIInteractiveMode.SHELL,
    container_port: Optional[int] = typer.Option(
        None, help="The container port to forward to localhost"
    ),
):
    args = args or []
    # Checking the runai is available
    if not check_command("runai", "--help"):
        log_error("Could not find the runai CLI")
        raise typer.Exit(code=1)

    # Check if container port is defined
    if mode == RunAIInteractiveMode.PORT and container_port is None:
        log_error("container_port should be defined if mode=port")
        raise typer.Exit(code=1)

    with runai_submit_interactive_job(job_name, image, args) as job:
        if mode == RunAIInteractiveMode.SHELL:
            _handle_shell_context(job)
        elif mode == RunAIInteractiveMode.PORT:
            assert container_port is not None
            _handle_port_context(job, container_port)
        print("Job started")
        time.sleep(1000)

    print(f"{image=}, {args=}")


def main():
    typer.run(interactive_context)
