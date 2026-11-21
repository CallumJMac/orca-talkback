"""Pure focus rules: decide whether an Orca event should speak, stop, or do nothing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Speak:
    pane_key: str


@dataclass(frozen=True)
class Stop:
    reason: str


Action = Speak | Stop


class FocusRules:
    """Tracks the focused Orca workspace and each pane's last known state.

    Orca repeats status events throughout a turn, so only a pane's transition
    into a state produces an action. A pane first seen already `done` is
    ignored, so starting the daemon never replays an old reply.
    """

    def __init__(self) -> None:
        self.focused_workspace_id: str | None = None
        self._pane_states: dict[str, str] = {}

    def on_focus(self, workspace_id: str | None) -> Action | None:
        if workspace_id == self.focused_workspace_id:
            return None
        self.focused_workspace_id = workspace_id
        return Stop("focus changed")

    def on_status(self, workspace_id: str | None, pane_key: str, state: str) -> Action | None:
        previous_state = self._pane_states.get(pane_key)
        self._pane_states[pane_key] = state
        if previous_state == state:
            return None
        if workspace_id is None or workspace_id != self.focused_workspace_id:
            return None
        if state == "working":
            return Stop("agent started working")
        if state == "done" and previous_state is not None:
            return Speak(pane_key)
        return None
