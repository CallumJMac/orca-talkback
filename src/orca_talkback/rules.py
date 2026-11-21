"""Pure focus rules: decide whether an Orca event should speak, stop, or do nothing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Speak:
    pane_key: str


@dataclass(frozen=True)
class Stop:
    reason: str


Action = Speak | Stop

# A pane moving from one of these into "working" means you just sent it a prompt.
PROMPTED_FROM_STATES = ("done", "waiting")


class FocusRules:
    """Decides which agent you are looking at, and so which replies to speak.

    An agent is on screen when its workspace is the focused one, or when it is
    the chat you just sent a prompt to: Orca's focus event follows the sidebar,
    not chats, so prompting a chat is the reliable sign you are viewing it.
    Picking another workspace means you moved on and clears that chat.

    Orca repeats status events throughout a turn, so only a pane's transition
    into a state produces an action. A pane first seen already `done` is
    ignored, so starting the daemon never replays an old reply.
    """

    def __init__(self) -> None:
        self.focused_workspace_id: str | None = None
        self._pane_states: dict[str, str] = {}
        self._prompted_pane: str | None = None

    def on_focus(self, workspace_id: str | None) -> Action | None:
        if workspace_id == self.focused_workspace_id:
            return None
        self.focused_workspace_id = workspace_id
        self._prompted_pane = None
        return Stop("focus changed")

    def on_status(
        self,
        workspace_id: str | None,
        pane_key: str,
        state: str,
        workspace_list_id: str | None = None,
    ) -> Action | None:
        previous_state = self._pane_states.get(pane_key)
        self._pane_states[pane_key] = state
        if previous_state == state:
            return None
        if state == "working" and previous_state in PROMPTED_FROM_STATES:
            self._prompted_pane = pane_key
            return Stop("you sent a new prompt")
        # Orca names a focused worktree by its list id and a focused folder workspace by its id.
        in_focus = self.focused_workspace_id is not None and self.focused_workspace_id in (workspace_id, workspace_list_id)
        if not in_focus and pane_key != self._prompted_pane:
            return None
        if state == "working":
            return Stop("agent started working")
        if state == "done" and previous_state is not None:
            return Speak(pane_key)
        return None
