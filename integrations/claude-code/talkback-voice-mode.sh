#!/bin/sh
# Claude Code UserPromptSubmit hook for orca-talkback.
# When Talkback is on for this session, ask the agent to end its reply with a hidden spoken line,
# so the daemon can speak it at once instead of summarising the reply with another model.
# When Talkback is muted, off, or not running, it prints nothing and the agent writes as usual.
url="${TALKBACK_URL:-http://127.0.0.1:47823}"
session_id=$(jq -r '.session_id // empty' 2>/dev/null) || exit 0
[ -n "$session_id" ] || exit 0
voice=$(curl -s --max-time 0.5 "$url/voice-mode?session=$session_id" 2>/dev/null | jq -r '.voice // false' 2>/dev/null)
[ "$voice" = "true" ] || exit 0
instruction='Talkback voice mode is on for this session: the user will hear a spoken version of your reply. End every reply with one final line in exactly this form, which is hidden when the reply is rendered: <!-- spoken: YOUR SPOKEN VERSION --> In it, write one to three short conversational sentences, as you would say them out loud to a colleague: what you did or found, and any question for the user. Make the first sentence short, under ten words, so it can be spoken straight away. No markdown, code, file paths, URLs or dashes. Write the rest of the reply as usual.'
jq -n --arg context "$instruction" '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $context}}'
