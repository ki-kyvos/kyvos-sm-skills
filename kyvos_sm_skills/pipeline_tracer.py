"""Single-file pipeline tracer — captures every step, API call, and LLM exchange.

Writes a human-readable trace of the full discovery/deployment pipeline to one
file so failures can be diagnosed without re-running the pipeline.

Usage:
    tracer = PipelineTracer(trace_path).activate()
    # ... pipeline code calls tracer.step / .api_call / .llm_exchange / .note
    tracer.close()

Any code can access the current tracer via ``PipelineTracer.current()`` and
call methods on it without needing the trace path explicitly.
"""

from __future__ import annotations

import json
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_current: ContextVar["PipelineTracer | None"] = ContextVar(
    "kyvos_pipeline_tracer", default=None
)


def get_tracer() -> "PipelineTracer | None":
    """Return the currently active tracer, or None."""
    return _current.get()


class PipelineTracer:
    """Append-only tracer that writes every pipeline event to a single file."""

    def __init__(self, trace_path: str) -> None:
        self.trace_path = Path(trace_path)
        self._lock = threading.Lock()
        self._counter = 0
        self._closed = False
        self._token = None
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._raw(
            "=" * 78 + "\n"
            "  Kyvos Discovery Pipeline Trace\n"
            f"  Started:  {datetime.now(timezone.utc).isoformat()}\n"
            f"  Trace:    {self.trace_path}\n"
            + "=" * 78 + "\n"
        )

    # ── Registration ──────────────────────────────────────────────────────

    def activate(self) -> "PipelineTracer":
        """Set this tracer as the current one for the calling context."""
        self._token = _current.set(self)
        return self

    def deactivate(self) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None

    @classmethod
    def current(cls) -> "PipelineTracer | None":
        return _current.get()

    # ── Writing ───────────────────────────────────────────────────────────

    def _raw(self, text: str) -> None:
        with self._lock:
            with open(self.trace_path, "a", encoding="utf-8") as f:
                f.write(text)

    def _section(self, title: str, body: str = "") -> None:
        self._counter += 1
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        header = (
            f"\n{'─' * 78}\n"
            f"  [{self._counter:03d}] {title}\n"
            f"  {ts} UTC\n"
            f"{'─' * 78}\n"
        )
        text = header + body
        if body and not body.endswith("\n"):
            text += "\n"
        self._raw(text)

    # ── Public API ─────────────────────────────────────────────────────────

    def step(self, name: str, detail: str = "") -> None:
        """Log a pipeline step marker (e.g. 'Schema Inspection', 'SM Design')."""
        self._section(f"STEP: {name}", detail)

    def api_call(
        self,
        method: str,
        url: str,
        status: int,
        *,
        request_body: Any = None,
        response_body: str = "",
    ) -> None:
        """Log a complete Kyvos API call (request + response)."""
        parts = [f"Method:  {method}", f"URL:     {url}"]
        if request_body:
            parts.append(
                f"Request body:\n{json.dumps(request_body, indent=2, default=str)}"
            )
        parts.append(f"Status:  {status}")
        parts.append(f"Response:\n{response_body or '(empty)'}")
        # Truncate the section title for readability
        short_url = url.split("/kyvos")[-1] if "/kyvos" in url else url
        self._section(f"API {method} {short_url[:70]}", "\n".join(parts))

    def llm_exchange(
        self,
        label: str,
        *,
        system_prompt: str = "",
        user_message: str = "",
        response: str = "",
        parsed: Any = None,
    ) -> None:
        """Log an LLM exchange (prompt → response → parsed result)."""
        parts: list[str] = []
        if system_prompt:
            parts.append(f"--- System prompt ---\n{system_prompt}")
        if user_message:
            parts.append(f"--- User message ---\n{user_message}")
        if response:
            parts.append(f"--- Raw response ---\n{response}")
        if parsed is not None:
            parts.append(
                f"--- Parsed ---\n{json.dumps(parsed, indent=2, default=str)}"
            )
        self._section(f"LLM: {label}", "\n\n".join(parts))

    def json_dump(self, label: str, obj: Any) -> None:
        """Log a JSON object."""
        self._section(label, json.dumps(obj, indent=2, default=str))

    def note(self, label: str, content: str) -> None:
        """Log arbitrary notes (repairs, validation results, etc.)."""
        self._section(label, content)

    def error(self, label: str, error: str, traceback_str: str = "") -> None:
        """Log an error."""
        body = f"ERROR: {error}"
        if traceback_str:
            body += f"\n\n{traceback_str}"
        self._section(f"ERROR: {label}", body)

    def close(self, status: str = "completed") -> None:
        """Write end-of-trace marker."""
        if not self._closed:
            self._closed = True
            self._raw(
                f"\n{'=' * 78}\n"
                f"  Pipeline {status} — {datetime.now(timezone.utc).isoformat()}\n"
                f"{'=' * 78}\n"
            )
