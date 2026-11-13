# myservice-demo

The small project used in the README and the demo video.

```sh
uv venv -p 3.14t .venv
uv pip install -p .venv/bin/python pydantic numpy lxml questdb pytest
giltrip run -- .venv/bin/python -m pytest -q
```
