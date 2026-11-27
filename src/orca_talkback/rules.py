"""Pure focus rules: decide whether an Orca event should speak, stop, or do nothing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Speak:
    pane_key: str


@dataclass(frozen=True)
class Stop:
    reason: str


Action = Speak | Stop

# A pane moving from one of these into "working" may mean you just sent it a prompt.
PROMPTED_FROM_STATES = ("done", "waiting")

# Prompts that wake an agent without you: Orca's messages between agents, the brief Orca gives a
# dispatched worker, and Claude Code's background task notifications.
AGENT_MESSAGE_PREFIXES = ("──── From:", "<task-notification", "You are working inside Orca")


# A reply that finished while you were on another workspace plays when you go there, if this recent.
UNHEARD_REPLY_SECONDS = 600


def is_agent_message(prompt: str) -> bool:
    """True when a prompt came from another agent or a background task rather than from you."""
    return prompt.lstrip().startswith(AGENT_MESSAGE_PREFIXES)


class FocusRules:
    """Decides which agent you are looking at, and so which replies to speak.

    An agent is on screen when its workspace is the focused one, or when it is
    the chat you just sent a prompt to: Orca's focus event follows the sidebar,
    not chats, so prompting a chat is the reliable sign you are viewing it.
    Picking another workspace means you moved on and clears that chat.

    Agents also wake themselves (a worker agent reports back, a background task
    finishes), which looks the same as a prompt. When the Claude Code hook has
    said who sent the prompt, `user_prompted` is True or False and only your
    prompts count; when it is None, any wake-up counts, as before.

    A reply that finishes while you are on another workspace is kept as unheard,
    and plays once when you go to its workspace, unless the agent has started
    working again or it finished more than ten minutes ago.

    Orca repeats status events throughout a turn, so only a pane's transition
    into a state produces an action. A pane first seen already `done` is
    ignored, so starting the daemon never replays an old reply.
    """

    def __init__(self) -> None:
        self.focused_workspace_id: str | None = None
        self._pane_states: dict[str, str] = {}
        self._prompted_pane: str | None = None
        # Replies that finished while you were elsewhere: the pane's workspace ids and when it finished.
        self._unheard: dict[str, tuple[tuple[str | None, str | None], float]] = {}

    def on_focus(self, workspace_id: str | None, now: float = 0.0) -> Action | None:
        if workspace_id == self.focused_workspace_id:
            return None
        self.focused_workspace_id = workspace_id
        self._prompted_pane = None
        self._unheard = {pane: entry for pane, entry in self._unheard.items() if now - entry[1] <= UNHEARD_REPLY_SECONDS}
        waiting = [(finished_at, pane) for pane, (ids, finished_at) in self._unheard.items()
                   if workspace_id is not None and workspace_id in ids]
        if waiting:
            _, pane_key = max(waiting)
            del self._unheard[pane_key]
            return Speak(pane_key)
        return Stop("focus changed")

    def mark_prompted(self, pane_key: str) -> Action | None:
        """Your prompt was reported after the agent had already started working: count it now."""
        if self._pane_states.get(pane_key) != "working":
            return None
        self._prompted_pane = pane_key
        return Stop("you sent a new prompt")

    def on_status(
        self,
        workspace_id: str | None,
        pane_key: str,
        state: str,
        workspace_list_id: str | None = None,
        user_prompted: bool | None = None,
        now: float = 0.0,
    ) -> Action | None:
        previous_state = self._pane_states.get(pane_key)
        self._pane_states[pane_key] = state
        if state == "working":
            self._unheard.pop(pane_key, None)
        if previous_state == state:
            return None
        if state == "working" and previous_state in PROMPTED_FROM_STATES and user_prompted is not False:
            self._prompted_pane = pane_key
            return Stop("you sent a new prompt")
        # Orca names a focused worktree by its list id and a focused folder workspace by its id.
        in_focus = self.focused_workspace_id is not None and self.focused_workspace_id in (workspace_id, workspace_list_id)
        if not in_focus and pane_key != self._prompted_pane:
            if state == "done" and previous_state is not None:
                self._unheard[pane_key] = ((workspace_id, workspace_list_id), now)
            return None
        if state == "working":
            return Stop("agent started working")
        if state == "done" and previous_state is not None:
            return Speak(pane_key)
        return None
