---
name: case-notes
description: Keep running working notes for the current thread in the workspace. Use when a task spans several steps or tool calls and the user may return to it later.
---

# Case notes

Keep working notes for the current conversation under `cases/<thread_id>/notes.md` in the
workspace. The thread id is the one the user is talking on.

1. At the start of a multi-step task, create the file if it does not exist with a one-line
   summary of the request.
2. After each tool call that changes your understanding, append a short dated line: what you
   did, what you learned, what is next.
3. Before proposing an action that needs approval, record the proposal and its arguments.
4. Never store secrets in the notes.

The notes are for the next person or agent picking up the thread, so write them to be read
cold.
