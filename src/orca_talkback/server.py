"""Localhost HTTP endpoints the Orca plugin posts to, wired to the rules and the speaker."""

import json
import threading
import time
from collections.abc import Callable, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from orca_talkback.rules import Action, FocusRules, Speak, Stop, is_agent_message

# How long after the hook reports your prompt the agent may start working and still count as prompted.
PROMPT_MATCH_SECONDS = 15
# A reply that finished while Orca was hidden plays when you come back, if you come back this soon.
HELD_REPLY_SECONDS = 600


class VoiceDaemon:
    def __init__(
        self,
        rules: FocusRules,
        speaker: Any,
        lookup_message: Callable[[str], str],
        to_sentences: Callable[[str], Iterable[str]],
        log: Callable[[str], None] = print,
        verbose: bool = False,
        orca_visible_check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rules = rules
        self._clock = clock
        # The latest prompt the Claude Code hook reported for each session: when, and whether it was yours.
        self._prompts: dict[str, tuple[float, bool]] = {}
        # Each session's pane, and when each pane last woke up without a confirmed prompt from you,
        # so a report that arrives after the agent started working can still count.
        self._session_panes: dict[str, str] = {}
        self._unconfirmed_wakeups: dict[str, float] = {}
        # The reply that finished while Orca was hidden: its pane and when it finished.
        self._held_reply: tuple[str, float] | None = None
        self._verbose = verbose
        self._orca_visible_check = orca_visible_check
        # Voice mode: which workspaces the Talkback plugin muted, and each agent session's workspace ids.
        self._muted_workspace_ids: set[str] = set()
        self._off_everywhere = False
        self._session_workspace_ids: dict[str, set[str]] = {}
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
            action = self._rules.on_focus(context.get("workspaceId"), now=self._clock())
            if action is not None:
                self._log(f"[focus] {context.get('displayName')!r} -> {describe_action(action)}")
            self._apply(action)

    def handle_status(self, payload: dict) -> None:
        pane_key = payload.get("paneKey")
        state = payload.get("state")
        if not isinstance(pane_key, str) or not isinstance(state, str):
            raise ValueError("paneKey and state must be strings")
        with self._lock:
            session_id = payload.get("sessionId")
            if isinstance(session_id, str):
                self._session_workspace_ids[session_id] = {
                    workspace for workspace in (payload.get("workspaceId"), payload.get("workspaceListId"))
                    if isinstance(workspace, str)
                }
            if self._verbose:
                self._log(
                    f"[event] status pane={pane_key} state={state} session={payload.get('sessionId')} "
                    f"workspace={payload.get('workspaceId')} focused={self._rules.focused_workspace_id}"
                )
            if isinstance(session_id, str):
                self._session_panes[session_id] = pane_key
            user_prompted = self._user_prompted(session_id)
            if state == "working" and user_prompted is False:
                self._unconfirmed_wakeups.setdefault(pane_key, self._clock())
            elif state != "working":
                self._unconfirmed_wakeups.pop(pane_key, None)
            action = self._rules.on_status(
                payload.get("workspaceId"), pane_key, state, workspace_list_id=payload.get("workspaceListId"),
                user_prompted=user_prompted, now=self._clock(),
            )
            if action is not None:
                self._log(f"[status] {pane_key} {state} -> {describe_action(action)}")
            self._apply(action)

    def handle_prompt(self, payload: dict) -> None:
        """The Claude Code hook reports each prompt, so agent wake-ups are not mistaken for yours."""
        session_id = payload.get("session")
        prompt = payload.get("prompt", "")
        if not isinstance(session_id, str) or not isinstance(prompt, str):
            raise ValueError("session and prompt must be strings")
        from_you = not is_agent_message(prompt)
        with self._lock:
            self._prompts[session_id] = (self._clock(), from_you)
            if self._verbose:
                self._log(f"[prompt] session={session_id} from {'you' if from_you else 'another agent'}")
            pane_key = self._session_panes.get(session_id)
            woke_at = self._unconfirmed_wakeups.get(pane_key) if pane_key else None
            if from_you and woke_at is not None and self._clock() - woke_at <= PROMPT_MATCH_SECONDS:
                self._unconfirmed_wakeups.pop(pane_key, None)
                action = self._rules.mark_prompted(pane_key)
                if action is not None:
                    self._log(f"[prompt] {pane_key} -> {describe_action(action)} (reported after it started working)")
                self._apply(action)

    def _user_prompted(self, session_id: object) -> bool | None:
        """Whether your prompt just woke this session; None when the hook never reported on it."""
        if not isinstance(session_id, str) or session_id not in self._prompts:
            return None
        reported_at, from_you = self._prompts[session_id]
        return from_you and self._clock() - reported_at <= PROMPT_MATCH_SECONDS

    def handle_mutes(self, payload: dict) -> None:
        muted = payload.get("muted", [])
        off = payload.get("off", False)
        if not isinstance(muted, list) or not all(isinstance(item, str) for item in muted) or not isinstance(off, bool):
            raise ValueError("muted must be a list of strings and off a boolean")
        with self._lock:
            self._muted_workspace_ids = set(muted)
            self._off_everywhere = off

    def voice_mode(self, session_id: str) -> bool:
        """Whether an agent session should write a spoken line: Talkback on and its workspace not muted."""
        with self._lock:
            if self._off_everywhere:
                return False
            return not (self._session_workspace_ids.get(session_id, set()) & self._muted_workspace_ids)

    def handle_stop(self, payload: dict) -> None:
        self._log("[stop] requested")
        with self._lock:
            self._held_reply = None
        self._speaker.stop("requested")

    def enforce_orca_visible(self) -> None:
        """Called twice a second: stop playback once Orca is hidden, and play a held reply once it is back."""
        if self._orca_visible_check is None:
            return
        with self._lock:
            held_reply = self._held_reply
        if held_reply is None and not self._speaker.is_playing:
            return
        visible = self._orca_visible_check()
        if self._speaker.is_playing and not visible:
            self._speaker.stop("no Orca window is visible")
        if held_reply is None or not visible:
            return
        with self._lock:
            if self._held_reply != held_reply:
                return  # replaced or dropped while the check ran
            self._held_reply = None
            pane_key, finished_at = held_reply
            if self._clock() - finished_at > HELD_REPLY_SECONDS:
                self._log(f"[speak] dropped the held reply from {pane_key}: you were away too long")
                return
            self._log("[speak] Orca is visible again, playing the held reply")
            self._speak(pane_key)

    def _apply(self, action: Action | None) -> None:
        if action is not None:
            # You moved on (another workspace, a new prompt, the agent working again), or a newer
            # reply is about to play, so a held reply is stale.
            self._held_reply = None
        if isinstance(action, Stop):
            self._speaker.stop(action.reason)
        elif isinstance(action, Speak):
            if self._orca_visible_check is not None and not self._orca_visible_check():
                self._held_reply = (action.pane_key, self._clock())
                self._log("[speak] holding the reply until Orca is visible")
                return
            self._speak(action.pane_key)

    def _speak(self, pane_key: str) -> None:
        message = self._lookup_message(pane_key)
        if not message.strip():
            self._log(f"[speak] no message for {pane_key}")
            return
        self._log(f"[speak] reply from {pane_key}")
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
        "/mutes": daemon.handle_mutes,
        "/prompt": daemon.handle_prompt,
    }

    class Handler(BaseHTTPRequestHandler):
        timeout = 5

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/health":
                self._respond(200, {"ok": True, "playing": daemon.is_playing})
            elif url.path == "/voice-mode":
                session_id = parse_qs(url.query).get("session", [""])[0]
                self._respond(200, {"voice": daemon.voice_mode(session_id)})
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
