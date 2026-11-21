"""Localhost HTTP endpoints the Orca plugin posts to, wired to the rules and the speaker."""

import json
import threading
from collections.abc import Callable, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from orca_talkback.rules import Action, FocusRules, Speak, Stop


class VoiceDaemon:
    def __init__(
        self,
        rules: FocusRules,
        speaker: Any,
        lookup_message: Callable[[str], str],
        to_sentences: Callable[[str], Iterable[str]],
        log: Callable[[str], None] = print,
        verbose: bool = False,
        front_app_check: Callable[[], bool] | None = None,
    ) -> None:
        self._rules = rules
        self._verbose = verbose
        self._front_app_check = front_app_check
        self._speaker = speaker
        self._lookup_message = lookup_message
        self._to_sentences = to_sentences
        self._log = log
        self._lock = threading.Lock()

    @property
    def is_playing(self) -> bool:
        return bool(self._speaker.is_playing)

    def handle_focus(self, payload: dict) -> None:
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            raise ValueError("context must be an object")
        with self._lock:
            if self._verbose:
                self._log(f"[event] focus workspace={context.get('workspaceId')} name={context.get('displayName')!r}")
            action = self._rules.on_focus(context.get("workspaceId"))
            if action is not None:
                self._log(f"[focus] {context.get('displayName')!r} -> {describe_action(action)}")
            self._apply(action)

    def handle_status(self, payload: dict) -> None:
        pane_key = payload.get("paneKey")
        state = payload.get("state")
        if not isinstance(pane_key, str) or not isinstance(state, str):
            raise ValueError("paneKey and state must be strings")
        with self._lock:
            if self._verbose:
                self._log(
                    f"[event] status pane={pane_key} state={state} "
                    f"workspace={payload.get('workspaceId')} focused={self._rules.focused_workspace_id}"
                )
            action = self._rules.on_status(
                payload.get("workspaceId"), pane_key, state, workspace_list_id=payload.get("workspaceListId"),
            )
            if action is not None:
                self._log(f"[status] {pane_key} {state} -> {describe_action(action)}")
            self._apply(action)

    def handle_stop(self, payload: dict) -> None:
        self._log("[stop] requested")
        self._speaker.stop("requested")

    def enforce_front_app(self) -> None:
        """Stop playback if Orca has left the front. Called periodically while speaking."""
        if self._front_app_check is not None and self._speaker.is_playing and not self._front_app_check():
            self._speaker.stop("Orca is not the app in front")

    def _apply(self, action: Action | None) -> None:
        if isinstance(action, Stop):
            self._speaker.stop(action.reason)
        elif isinstance(action, Speak):
            if self._front_app_check is not None and not self._front_app_check():
                self._log("[speak] skipped: Orca is not the app in front")
                return
            message = self._lookup_message(action.pane_key)
            if not message.strip():
                self._log(f"[speak] no message for {action.pane_key}")
                return
            self._log(f"[speak] reply from {action.pane_key}")
            self._speaker.speak(self._logged(self._to_sentences(message)))

    def _logged(self, sentences: Iterable[str]) -> Iterable[str]:
        for sentence in sentences:
            self._log(f"[say] {sentence}")
            yield sentence


def describe_action(action: Action) -> str:
    if isinstance(action, Stop):
        return f"stop ({action.reason})"
    return "speak"


MAX_BODY_BYTES = 1_000_000


def make_server(
    daemon: VoiceDaemon,
    host: str = "127.0.0.1",
    port: int = 47823,
    log: Callable[[str], None] = print,
) -> ThreadingHTTPServer:
    routes: dict[str, Callable[[dict], None]] = {
        "/focus": daemon.handle_focus,
        "/status": daemon.handle_status,
        "/stop": daemon.handle_stop,
    }

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self) -> None:
            if self.path == "/health":
                self._respond(200, {"ok": True, "playing": daemon.is_playing})
            else:
                self._respond(404, {"error": "not found"})

        def do_POST(self) -> None:
            route = routes.get(self.path)
            if route is None:
                self._respond(404, {"error": "not found"})
                return
            content_type = self.headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type != "application/json":
                self._respond(415, {"error": "content-type must be application/json"})
                return
            try:
                length = int(self.headers.get("content-length", "0"))
                if length < 0 or length > MAX_BODY_BYTES:
                    raise ValueError("invalid content-length")
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
                route(payload)
            except ValueError as error:  # includes json.JSONDecodeError
                self._respond(400, {"error": str(error)})
                return
            except Exception as error:
                log(f"[error] {self.path}: {error!r}")
                self._respond(500, {"error": "internal error"})
                return
            self._respond(200, {"ok": True})

        def _respond(self, status: int, body: dict) -> None:
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)
