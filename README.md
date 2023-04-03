# RunAI interactive context

[![PyPI - Version](https://img.shields.io/pypi/v/runai-interactive-context.svg)](https://pypi.org/project/runai-interactive-context)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/runai-interactive-context.svg)](https://pypi.org/project/runai-interactive-context)

This CLI improves the interactive mode of the `runai` CLI by:

- Deleting the job on exit to reduce the costs of idle interactive sessions.
- Allowing for longer delays when asking for a port forward (RunAI CLI only waits 60 seconds)
- Parsing and translating url of a jupyter notebook (with the forwarded port and token information).

-----

**Table of Contents**

- [Installation](#installation)
- [Usage](#usage)
- [License](#license)

## Installation

```console
pip install runai-interactive-context
```

## Usage

Start an interactive jupyter server:

```console
runai-interactive --mode jupyter <job_name> <image_name> -- jupyter server
```

Run a streamlit application

```console
runai-interactive --mode port --container-port 8501 <job_name> <image_name> -- streamlit app.py
```

## License

`runai-interactive-context` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
