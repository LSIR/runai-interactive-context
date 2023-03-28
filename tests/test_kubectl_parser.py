from runai_interactive_context.cli import kubectl_output_extract_forwarded_port


def test_kubectl_output_extract_forwarded_port():
    assert (
        kubectl_output_extract_forwarded_port(
            b"Forwarding from 127.0.0.1:34805 -> 8888"
        )
        == 34805
    )
    assert kubectl_output_extract_forwarded_port(b"Else") is None
