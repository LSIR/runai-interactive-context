import dataclasses
import enum
import json
import re
import signal
import subprocess
import time
from contextlib import AbstractContextManager, contextmanager, ExitStack
from typing import Callable, Generator, NamedTuple, Optional
from urllib.parse import parse_qs, urlparse

import retry
import typer
from rich.console import Console

err_console = Console(stderr=True)


class DelayedKeyboardInterrupt:
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.interrupt_data = None
        self.original_handler = None

    def record_interrupt(self, sig, frame):
        self.interrupt_data = sig, frame

    def __enter__(self):
        self.interrupt_data = None
        self.original_handler = signal.signal(signal.SIGINT, self.record_interrupt)

    def __exit__(self, type, value, traceback):
        if self.original_handler is not None:
            signal.signal(signal.SIGINT, self.original_handler)
        if self.interrupt_data:
            if self.original_handler is None or self.original_handler == signal.SIG_IGN:
                pass
            elif self.original_handler == signal.SIG_DFL:
                raise KeyboardInterrupt()
            else:
                self.original_handler(*self.interrupt_data)  # type: ignore


class DelayedKeyboardInterruptExitStack(ExitStack):
    def __exit__(self, *args, **kwargs) -> bool:
        print("Cleaning up...")
        with DelayedKeyboardInterrupt():
            return super().__exit__(*args, **kwargs)


def preexec_ignore_sigint():
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def handle_sighup(signum, frame):
    log_error("Hangup received, terminating job.")
    raise typer.Exit(code=1)


class RunAIInteractiveMode(str, enum.Enum):
    # Simple type, simply runs a shell
    SHELL = "shell"
    # Forwards a port
    PORT = "port"
    # Jupyter server
    JUPYTER = "jupyter"


class RunAIJobStatus(enum.Enum):
    PENDING = enum.auto()
    CONTAINERCREATING = enum.auto()
    RUNNING = enum.auto()
    NOT_READY = enum.auto()
    IMAGEPULLBACKOFF = enum.auto()
    DOES_NOT_EXISTS = enum.auto()

    @classmethod
    def from_str(cls, value: str) -> "RunAIJobStatus":
        return getattr(cls, value.upper(), RunAIJobStatus.NOT_READY)


class RunAIJobDetails(NamedTuple):
    name: str
    pod_name: str
    status: RunAIJobStatus


class JupyterConnectionDetails(NamedTuple):
    container_port: int
    token: str


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
        if job.status == RunAIJobStatus.IMAGEPULLBACKOFF:
            log_error("Couldn't pull the image, are you sure it exists?")
            raise typer.Exit(code=1)
        if (
            job.status == RunAIJobStatus.CONTAINERCREATING
            and not notified_container_creating
        ):
            print("Creating container...")
            notified_container_creating = True
        time.sleep(5)
    return job


@dataclasses.dataclass
class RunAIInteractiveJob(AbstractContextManager[RunAIJobDetails]):
    job_name: str
    image: str
    command: list[str]

    def submit(self) -> RunAIJobDetails:
        job_cmd = [
            "runai",
            "submit",
            self.job_name,
            "-i",
            self.image,
            "--interactive",
        ] + self.command
        print(f"Submitting job: {' '.join(job_cmd)}")
        process = subprocess.run(job_cmd)
        if process.returncode != 0:
            log_error("Could not submit job to RunAI")
            raise typer.Exit(code=1)
        print("Waiting for the job to start...")
        return wait_until_job_started(self.job_name)

    def __enter__(self) -> RunAIJobDetails:
        return self.submit()

    def __exit__(self, *args, **kwargs):
        subprocess.run(
            ["runai", "delete", "job", self.job_name], preexec_fn=preexec_ignore_sigint
        )


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


def _wait_until_interrupted():
    while True:
        time.sleep(10)


def _handle_shell_context(job: RunAIJobDetails):
    print(f"Interactive session started, you can connect with `runai bash {job.name}`")
    _wait_until_interrupted()


def _handle_port_context(
    exit_stack: ExitStack,
    job: RunAIJobDetails,
    container_port: int,
    build_url: Callable[[int], str],
):
    local_port = exit_stack.enter_context(
        kubectl_pod_forward_port(job.pod_name, container_port)
    )
    url = build_url(local_port)
    print(f"The application is running at {url}")
    _wait_until_interrupted()


URL_RE = re.compile(rb"http\S+")


def find_jupyter_details_in_logs(line: bytes) -> Optional[JupyterConnectionDetails]:
    urls: list[bytes] = URL_RE.findall(line)
    for url in urls:
        url_obj = urlparse(url.decode())
        token = parse_qs(url_obj.query).get("token")
        if token:
            port = url_obj.port
            if port is None:
                port = 80 if url_obj.scheme == "http" else 443
            return JupyterConnectionDetails(port, token[0])


@retry.retry((subprocess.CalledProcessError, ValueError), delay=10, tries=20)
def extract_jupyter_details_from_job(job_name: str) -> JupyterConnectionDetails:
    proc = subprocess.run(["runai", "logs", job_name], capture_output=True)
    proc.check_returncode()
    jupyter_details = find_jupyter_details_in_logs(proc.stdout)
    if not jupyter_details:
        raise ValueError("No jupyter details found")

    return jupyter_details


def _handle_jupyter_context(exit_stack: ExitStack, job: RunAIJobDetails):
    jupyter_details = extract_jupyter_details_from_job(job.name)
    _handle_port_context(
        exit_stack,
        job,
        jupyter_details.container_port,
        lambda p: f"http://localhost:{p}/?token={jupyter_details.token}",
    )


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

    # Setting up signals
    signal.signal(signal.SIGHUP, handle_sighup)

    with DelayedKeyboardInterruptExitStack() as stack:
        job_def = RunAIInteractiveJob(job_name, image, args)
        # Add the cleanup code to the exit stack
        stack.push(job_def)
        # Submit the job on RunAI
        job = job_def.submit()

        if mode == RunAIInteractiveMode.SHELL:
            _handle_shell_context(job)
        elif mode == RunAIInteractiveMode.PORT:
            assert container_port is not None
            _handle_port_context(
                stack, job, container_port, lambda p: f"http://localhost:{p}/"
            )
        elif mode == RunAIInteractiveMode.JUPYTER:
            _handle_jupyter_context(stack, job)


def main():
    typer.run(interactive_context)
