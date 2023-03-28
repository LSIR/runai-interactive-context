import subprocess
from contextlib import contextmanager
import time
from typing import Optional

import typer
from rich.console import Console

err_console = Console(stderr=True)


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
        process = subprocess.run(command)
    except FileNotFoundError:
        return False
    return process.returncode == 0


@contextmanager
def runai_submit_interactive_job(job_name: str, image: str, command: list[str]):
    process = subprocess.run(
        ["runai", "submit", job_name, "-i", image, "--interactive"] + command
    )
    if process.returncode != 0:
        log_error("Could not submit job to RunAI")
        raise typer.Exit(code=1)

    try:
        yield
    finally:
        subprocess.run(["runai", "delete", "job", job_name])


def interactive_context(
    job_name: str, image: str, command: Optional[list[str]] = typer.Argument(None)
):
    command = command or []
    # Checking the runai is available
    if not check_command("runai", "--help"):
        err_console.print("ERROR: Could not find the runai CLI")
        raise typer.Exit(code=1)

    with runai_submit_interactive_job(job_name, image, command):
        print("Job started")
        time.sleep(10)

    print(f"{image=}, {command=}")


def main():
    typer.run(interactive_context)


"""

LSIR_EXPLORATION_IMAGE="nginx"


runai-interactive-trap() {
    runai delete job "$1"
    trap - SIGHUP
}

await-runai-job-running() {
    job_name="$1"

    echo "Waiting for $job_name to start"
    until test "$(runai list | grep "$job_name" | awk '{print $2}')" == 'Running'
    do
        sleep 5
    done
}

runai-notebook-server() {
    job_name="lsir-interactive-notebook"
    # Add a trap is the signal is interrupted or killed
    trap 'runai-interactive-trap "$job_name"' SIGHUP
    # Start the job
    runai submit "$job_name" -i "$LSIR_EXPLORATION_IMAGE" --interactive
    # Wait for the pod to start
    await-runai-job-running "$job_name"
    # Forward a port locally
    kubectl port-forward "pods/$job_name-0-0" :8888
    # Delete the job when exiting
    runai-interactive-trap "$job_name"
}

"""
