# orca-talkback: design

> The original design document, kept for the reasoning behind the rules and trade-offs. Names are updated; the [README](../README.md) is the user-facing reference.

**Date:** 2026-11-21
**Status:** Implemented in 0.1. Kept as the record of the design decisions; the README is the user-facing reference.

## Goal

Talk to Claude Code sessions running in Orca and hear them talk back, like voice mode in the Claude apps.

- **Input:** unchanged. The user dictates into the visible Orca pane with Wispr Flow.
- **Output:** a new `orca-talkback` daemon speaks a short conversational version of each finished reply through Kokoro.
- **Barge-in:** the user can interrupt by speaking; WebRTC AEC3 stops the daemon's own audio from triggering an interrupt.

## Acceptance criteria

1. When an agent in the workspace currently focused in Orca finishes a turn, speech of its reply starts within about 3 seconds.
2. Replies from agents in other workspaces are never spoken, including when the user later switches to that workspace.
3. Speaking over the playback stops it within about 300 ms; the daemon's own audio alone never stops it.
4. Playback stops when the user switches workspace or when an agent in the focused workspace starts working again.
5. A newer reply replaces the current one; there is never a queue of replies.
6. If the daemon is not running, Orca and Claude Code behave exactly as before.

## Non-goals

- Hands-free input (local speech-to-text driving the agent). Possible later as a separate mode.
- Speaking progress narration during a turn. Only the final reply is spoken.
- Per-pane focus. Orca reports focus per workspace; that is the unit used.
- Auto-start (launchd). The daemon is started by hand for v1.

## Architecture

```
 Wispr Flow ──types──► Orca Claude Code pane          (unchanged)
                              │ finishes / starts working
                              ▼
 Orca plugin "talkback" (trusted Node worker, forwards events only)
   workspace.changed ───────► POST /focus  {workspaceId}
   agent.status.changed ────► POST /status {workspaceId, paneKey, state, sessionId}
                              ▼
 orca-talkback daemon (Python, 127.0.0.1 only)
   rules ─► agent's hidden spoken line ─► sentence chunks ─► Kokoro ─► speakers
                                                            │ reference signal
   mic ─► WebRTC AEC3 ─► Silero VAD ─► user speaking ─► stop playback
```

The plugin forwards events and holds no logic. All decisions are made in the daemon so they can be unit tested in Python.

## Components

All paths are relative to the repository root.

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| `src/orca_talkback/server.py` | Localhost HTTP server: `POST /focus`, `POST /status`, `POST /stop`, `GET /health`. Validates payloads and passes them to `rules`. | `rules`, `speaker` |
| `src/orca_talkback/rules.py` | Pure state machine. Holds the focused workspace id; turns events into actions (`speak(text)`, `stop()`, nothing). No I/O. | none |
| `src/orca_talkback/replies.py` | Finds the pane in `orca agent list --json` (matched on `paneKey`), then reads its full last assistant message with `orca agent read` (the list truncates messages to 200 characters). | `orca` CLI |
| `src/orca_talkback/visibility.py` | Reads macOS's on-screen window list (CoreGraphics, front to back) and reports whether at least 10% of some Orca window is uncovered, so replies play while Orca is visible on any screen and stop when it is covered, minimised or hidden. No permissions needed. | none |
| `src/orca_talkback/spoken.py` | Finds the agent's hidden spoken line, or cleans up the reply when there is none; splits text into sentences. | none |
| `src/orca_talkback/speaker.py` | Synthesises sentences with Kokoro in a background thread and plays them gaplessly; runs barge-in detection during playback. `stop()` fades out within 50 ms and cancels pending synthesis. | `audio`, `kokoro-onnx` |
| `src/orca_talkback/audio.py` | AEC3 processor factory, reference alignment and the barge-in detector, ported from `voice_loop_mac.py` with its timing invariants. The original lives in [Trelis Research voice-loop](https://github.com/TrelisResearch/voice-loop). | `livekit`, `silero-vad` |
| [orca-talkback-plugin](https://github.com/CallumJMac/orca-talkback-plugin) (separate repo) | Orca plugin manifest and worker. Subscribes to `workspace.changed` and `agent.status.changed` and POSTs the payloads unchanged to the daemon. | Orca plugin host API |

Entry point: `uv run orca-talkback` (console script; `python -m orca_talkback` also works).

## Rules

The state is the focused workspace id and the last known state of each pane. Orca repeats `working` events throughout a turn, so only a pane's transition into a state counts.

| Event | Condition | Action |
|-------|-----------|--------|
| `/focus` | workspace id differs from current | store it; `stop()` |
| `/status` state `done` | pane was not already `done`, `workspaceId` equals the focused workspace | look up the message; if not empty, `speak(message)` (replaces any current playback) |
| `/status` state `done` | other workspace | nothing |
| `/status` state `working` | pane was not already `working`, `workspaceId` equals the focused workspace | `stop()` |
| `/status` other states (`blocked`, `waiting`) | any | nothing |
| `/stop` | any | `stop()` |
| barge-in detected | playback active | `stop()` |

Replies that finished while their workspace was not focused are not stored and are never spoken later.

## Spoken text

- **Spoken text:** the agent ends its reply with a hidden `[//]: # (spoken: ...)` line when Talkback is on (a Claude Code `UserPromptSubmit` hook asks for it); the daemon speaks that line, or a cleaned-up version of the reply when there is none.
- **Fallback:** with no spoken line, the reply is stripped of code blocks, tables, links and markdown and cut to 600 characters at a sentence boundary.
- **Voice:** `bf_emma` at speed 1.1 by default, overridable with `--voice` and `--speed`.

## Barge-in and echo

- **Mic timing:** the mic stream (16 kHz mono) is open only while playback is active.
- **Echo cancellation:** each playback block is also fed to AEC3 as the reverse stream, so the cancellation reference matches exactly what the speakers emit. This reuses Trelis voice-loop's AEC3 setup, including its buffer sizes, which are known to keep AEC timing stable.
- **Threshold:** the speech threshold and the 160 ms window are CLI flags (`--barge-in-ms`, `--vad-threshold`) so they can be tuned without code changes.
- **Known limitation:** Wispr Flow records from its own mic without AEC. If the user starts dictating at the same moment as the barge-in, Wispr may capture up to about 300 ms of the tail of the speech. A fast fade keeps this rare. It is accepted for v1.

## Error handling

- **Daemon not running:** the plugin's POST fails; the worker catches it, logs once per minute at most, and carries on.
- **Kokoro or audio device error:** log the error, drop that reply, keep the daemon running.
- **Mic permission denied:** log a clear message at startup and run without barge-in. Playback still works.
- **Malformed request:** respond with 400 and ignore it.
- **Logging:** the daemon logs each decision (event, action, spoken text) to stdout for debugging.

## Spike (done 2026-11-21)

Result: a trusted worker can `fetch` `http://127.0.0.1:47823`. `workspaceId` on `agent.status.changed` matches `context.workspaceId` on `workspace.changed`. The event carries `paneKey` and `sessionId` but no message text, so the daemon reads it from the CLI. Original spike brief:

Confirm that a trusted Orca plugin worker can POST to `http://127.0.0.1:<port>`. The plugin guide says plugins have "no network", which may apply only to panels.

- **If the POST works:** proceed as designed.
- **If it fails:** stop and return to the user before changing approach. The fallback considered was "speak the session you last dictated into", using a `UserPromptSubmit` hook.

The spike also confirms the `agent.status.changed` payload. It checks whether `lastAssistantMessage` is present or must be read through `agents.list`, and checks that `workspaceId` matches the id from `workspace.changed`.

## Rollout

1. Build and test the daemon with `curl` requests to `/focus` and `/status`.
2. Install the plugin in Orca as a development plugin; verify the end-to-end flow.

## Testing

- **Unit (`pytest`):** every row of the rules table, the text fallback cleaner, and the sentence splitter on streamed chunks.
- **Integration:** with the daemon running, `curl` a `/status done` for the focused workspace and check that it plays; send one for another workspace and check that it is silent.
- **Manual barge-in:**
  1. Let a long reply play with speakers at normal volume and stay silent; it must finish.
  2. Speak over it; it must stop within about 300 ms.
- **Manual focus:** switch workspace mid-reply; the audio stops. Submit a new prompt with Wispr; the audio stops.
- **Plugin:** `orca plugin validate` and `orca plugin test` in the orca-talkback-plugin repo.
