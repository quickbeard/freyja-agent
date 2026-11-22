# Contract: the result of `check_agent_tasks`, changes in feature 008

`check_agent_tasks` is one of Freyja's own tools, read-only in the fixed table (feature 003,
FR-018). Its arguments, its classification, and when it is offered do not change. Producing
its result reads the service's database and contacts no agent (FR-013).

## What changes

The result lists one agent task per entry. An entry is one line, as in feature 003:

```text
- Agent '<agent>', task <task id>: <status>, started <time>, last checked <time>[, ended <time>]. Task message: <first 200 characters of the message>
```

From this feature on, an entry for a task whose status is `working` and that has kept
progress is followed by a line, indented by two spaces, and then by the kept text, every
line of which is indented by four spaces:

```text
  Progress the agent reported, in its own words (learned <time>):
    <first line of the progress text>
    <each later line, as the agent wrote it>
```

`<time>` is `progress.learned_at` of the thread's state, in the form the entry's other times
have. The text is whole, up to its 2,000 characters. Every entry of the result starts at the
beginning of a line with `- Agent`, and every line of an agent's text starts with four
spaces, so no line of it can be read as another entry, whatever it holds (FR-013).

Example, a thread with one task that is working and one that completed:

```text
- Agent 'stub', task 3f1c2b7e: working, started 2026-11-21T10:00:41.512000+00:00, last checked 2026-11-21T10:04:01.990000+00:00. Task message: steps 12 30: compile the weekly report
  Progress the agent reported, in its own words (learned 2026-11-21T10:03:51.871000+00:00):
    Step 7 of 12
- Agent 'stub', task 9b0e61c4: completed, started 2026-11-21T09:40:00+00:00, last checked 2026-11-21T09:41:12+00:00, ended 2026-11-21T09:41:12+00:00. Task message: summarize the notes
```

## What does not change

- An entry for a task with no kept progress, and for a task that has ended, was given up, or
  waits for input, whatever was kept for it: the one line of feature 003.
- "This thread has no agent tasks." and "No agent task <id> on this thread."
- **What the orchestrator is told on every model call**: one line per pending task with its
  agent, its id, its status, and when it started. It holds no progress text (FR-014).
- **An outcome recorded in the thread**: it appears as a call of this tool with the outcome
  as its result (feature 003, research R10). That result is the outcome's own text and has
  no progress line.

## Trust

The progress line is an agent's claim, worded as one. It reaches the orchestrator as a tool
result, where an agent's reply has always reached it, and it decides nothing: whether an
action pauses for a person is the catalog's and the gate's to say (FR-017).

The tool's description gains one clause, that the result also holds the progress an agent
last reported for a task it is working on, in the agent's own words.
