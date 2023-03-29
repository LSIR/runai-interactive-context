from runai_interactive_context.cli import (
    JupyterConnectionDetails,
    find_jupyter_details_in_logs,
)


def test_extract_jupyter_details_from_job():
    assert (
        find_jupyter_details_in_logs(
            b"    To access the server, open this file in a browser:"
        )
        is None
    )
    assert find_jupyter_details_in_logs(
        b"[I 2023-03-29 08:57:24.938 ServerApp] "
        b"http://localhost:8970/?token=0ae67ae0f222ac82b321b33cb94b6f843725376b16b36975"
    ) == JupyterConnectionDetails(
        8970, "0ae67ae0f222ac82b321b33cb94b6f843725376b16b36975"
    )
