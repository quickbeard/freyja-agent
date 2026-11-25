# Research: Progress of Pending Agent Tasks

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

Decisions the plan rests on, each with its reason and what was set aside. Facts marked
*verified* were checked on 2026-11-21 against the code on `main` at 56f65a1 and the packages
in `uv.lock` (a2a-sdk 1.3.0, langgraph 1.2.18, deepagents 0.7.32, fastapi 0.162.0,
sse-starlette 3.6.0, psycopg 3.3.6) and `web/pnpm-lock.yaml` (Next.js 16.4.6, React 19.3.0,
swr 2.5.1). Facts marked *run* were seen that day in a run of the real hand-off code
(`AgentLink`, `send_message`) against a stand-in agent built with the SDK's server classes, in
memory. Facts about Next.js come from the documentation bundled with the installed version
(`web/node_modules/next/dist/docs/`), as `web/AGENTS.md` requires. The prototypes were thrown
away; each is described well enough here to be written again, and the tests of the
implementation repeat them. What could not be checked, and what is knowingly not covered, is
listed at the end.

The spec asks that User Story 2 be read again if feature 007's task page was built
differently from its specification. It was read against the code when this plan was
written, and again at the checklist review on 2026-11-22, after feature 007's last phases
(#127, #128): `web/components/tasks/agent-tasks.tsx` lists each agent task with its agent,
its status in words, and its message; `task-view.tsx` reads the task again every 12 seconds
while an agent task is awaited (`AGENT_SECONDS`, 15 when this plan was first written), reads
nothing while the page is hidden, and reads at once when it is shown again. That is what
the spec assumed, so the story stands as written, and SC-001's 30 seconds for the page still
hold: 10 seconds to a check, 12 to a reading.

No reviewer other than the author has read this plan.

## R1. What an agent's progress is, in the protocol

**Question**: where does "the text of the status message the agent attached" come from, and
does a read of a pending task's status return it?

**Findings**:

- *verified* An A2A task's `status` holds a `state` and an optional `message`. The hand-off
  already reads it in three places in `delegates/a2a_agents.py`: the `task` event of a send,
  each `status_update` event of a stream, and each `GetTask` of a hand-off to an agent that
  does not stream (`_poll`). The worker reads it once per check (`AgentLink.get_task` in
  `DelegationWorker._check`). Those are the only places the service learns a status, and
  they are the two the spec names (FR-002).
- *run* A stand-in agent that sends a working status with a new message while its task is
  held was handed a task with a call time limit of 2.5 seconds. After the hand-off had
  stopped waiting, `GetTask` returned the message the agent sent last, and returned each
  later one within the same second. An agent whose stream Freyja has left goes on updating
  the task its own server keeps. So the worker's check sees progress with the request it
  already makes.
- *run* A working status sent with no message replaces the earlier one: `GetTask` then
  returns a status that has no message. FR-003 covers it: the kept progress stays.
- *run* With an agent that does not stream, the same hand-off put one progress update in the
  stream, for the change from submitted to working, and none for the text that changed
  afterwards. That is the gap User Story 4 names.

**Decision**: one function decides what counts as progress, for the hand-off and for the
worker alike: `progress_text(status)` in `delegates/a2a_agents.py`, beside `state_name` and
`_text`. It returns the text to keep, or nothing:

1. The status's state must be `working` as the service names it (`state_name`), which covers
   the protocol's submitted and working. Any other state gives nothing: an ending, a
   question, and a request for authentication are outcomes (FR-002).
2. The text is the status message's text parts joined by a line break, which is `_text`, the
   join a progress update in a run's stream has today. Parts that are not text are left out,
   with no note in their place (FR-004).
3. A NUL character is removed and a lone surrogate is dropped, since a text column takes
   neither (`thread_index._cut` does the same for a person's message, and replaces where
   this drops; the spec says dropped).
4. The text is cut to its first 2,000 characters (`PROGRESS_LIMIT`), and a text that is then
   empty or only blank space gives nothing (FR-003, FR-004).

Nothing else is done to the text. It does not go through the address scrubber (FR-004,
clarified).

**Alternatives set aside**: `a2a_result_text`, which a reply is turned into text with: it
writes a note for each part it leaves out and turns data into JSON, which the spec leaves out
of progress. The status's own `timestamp`: it is the agent's clock (FR-005).

## R2. Where the kept progress is stored

**Decision**: two nullable columns on the table that exists, `freyja_agent_tasks`:
`progress text` and `progress_at timestamptz`. `AgentTaskRecord` gains `progress` and
`progress_at`, both `None` by default. Both stores carry them: `PostgresAgentTaskStore` and
`InMemoryAgentTaskStore`.

- *verified* `_TASK_COLUMNS` and `UPDATABLE` are made from the record's fields, so
  `record_task`, `get_task`, `list_tasks`, `claim_due`, and `update_task` read and write the
  new columns with no statement of their own.
- *verified* A deployment's table exists already. Feature 005 added `agent_ref` to it
  through `_ADDED_COLUMNS`: the column is looked for, and added only when missing, under a
  lock timeout of five seconds, and the service does not start when the table cannot be
  changed in that time. The two new columns are two more entries of that tuple, and the
  `CREATE TABLE` statement gains them for a new database. Adding a nullable column with no
  default rewrites nothing.
- *verified* Nothing that gives a task up or records its outcome names these columns:
  `GIVE_UP`, `give_up_pending`, `record_outcome`, `_give_up`, and `mark_delivered` set other
  columns only. So the kept progress stays when a task ends, is given up, or comes to wait
  for input, with no code for it (FR-007).
- A row written before this feature has both columns null, and is read as a task with no
  kept progress (FR-021).

It is in the database, so every instance reads the same and a restart loses nothing (FR-008).

**Alternatives set aside**: a table of its own for progress, which would add a join to every
reading of a thread's agent tasks for one text per task. The thread's checkpoint: progress
would then be written into the thread, which FR-016 forbids, and would need the run lease.

## R3. Keeping progress in a check, and the time that stays

**Decision**: the worker's check keeps progress in the statement it already runs. In
`DelegationWorker._check`, where a task found working has `last_checked_at` set today, the
same `update_task` also sets `progress` and `progress_at` when `progress_text` gave a text
and that text is not the one the row holds. `progress_at` is the `store.now()` the check has
already read for `last_checked_at`. When the text equals the kept one, or there is none, the
statement is today's and the two columns are not named, so the time stays (FR-003).

- *verified* `_check` reads the task's row (`store.get_task`) before it asks the agent, to
  leave alone a task that was given up meanwhile. That reading is what the new text is
  compared with. No request and no statement is added to a check (FR-015).
- *verified* The update is conditional on the row still being `working`
  (`when_state=("working",)`), as today, so a task that ended or was given up while the
  check was under way keeps what it had.
- *verified* A task whose row says `input_required` is checked too, and when the agent says
  it is working, nothing is written: the hand-off that carries the answer is following it
  and records what the answer leads to. Progress follows that rule: none is kept by the
  worker while the row waits for input, and the hand-off records the task as working again
  with the progress it saw (R4).

**The comparison is made in the service, not in the statement.** Between the reading and the
update, another writer could keep another text, and the time could then be a few seconds
off. The only other writer of a working task's progress is a hand-off that sends a message
on the task (R4). An answer to a question is not one: while the row waits for input the
worker writes nothing (above). A message on a task that is still working is: the model can
hand off with the id of a working task. One instance watches a task at a time (003 FR-019).
A wrong time of a few seconds in a case that needs two rare things at once does not justify
a store method and a statement of its own. A worker's text is always the one learned last
when it is written, since it was learned at the check itself; the spec's rule, that the text
learned last is kept, then holds.

**Alternatives set aside**: a store method with `WHERE progress IS DISTINCT FROM %(text)s`,
for the reason above. Setting `progress_at` at every check: clarified, the time says since
when the agent has reported nothing new.

## R4. Progress a hand-off saw, and when it learned it

**Decision**: `SendOutcome` gains `progress`, the last text `progress_text` gave during the
send, and `progress_seen`, the event loop's clock when that text was first seen. `_send`
notes it at the `task` event and at each `status_update`, and `_poll` at each read. A text
equal to the one held is not noted again, so `progress_seen` is when the hand-off first saw
it.

`_record` in `delegates/a2a_tools.py` writes it with the task, in the `record_task` or the
`update_task` it already makes: `progress` and `progress_at` are set when the outcome holds a
text and the row does not already hold the same one (FR-006). This holds for a task recorded
as pending and for one recorded as ended, and for a hand-off whose stream broke after the
agent acknowledged.

A hand-off that sends a message on a task the row already holds (an answer, or a message on
a task still working) updates that row. It writes its progress only when the row, which
`_record` reads before it writes (`known_task`), holds no progress or progress learned
earlier than the hand-off's. A check may have kept a later text while the hand-off was under
way, and the spec keeps the text learned last, not the one written last (Edge Cases). The
read and the write are two statements; a check that writes between them is the same rare
case R3 accepts.

**The time** is the store's clock, never the agent's and never this instance's wall clock
(FR-005): `_record` already reads `store.now()`, and `progress_at` is that time less the
seconds that passed on the event loop's clock since `progress_seen`. No statement is added
for it. A stream can carry many updates a second, and reading the store's clock at each
would be a query per update.

**Alternatives set aside**: writing the row's progress at every update of a stream.
*verified* A task has no row until its hand-off ends: `_record` writes it once, after the
send. The thread's state lists no task until then, so nothing would read it. Stamping with the
time the row is written: up to the call time limit later than the text was learned, 30
seconds by default.

## R5. A hand-off that asks for a task's status reports a changed text

**Decision**: `_poll` remembers the last text it saw beside the last state. It puts a
progress update in the stream when a read finds another state, as today, and also when it
finds the same state with a text that is neither blank nor the one it last saw (FR-019). The
texts are compared as `progress_text` makes them (R1), so the rule is the kept text's: two
texts that differ only after their first 2,000 characters are the same, and blank is none.
The update still carries `_text` of the status message, as today, not cut. A read with no
text changes neither what was last seen nor the stream. The text it starts from is the one of the status the send returned.

- *verified* The update's shape (`agent`, `task_id`, `state`, `text`) and where it appears in
  the stream do not change. `contracts/sse-events.md` says what changed.
- *verified* `_send` does not call `_poll` for an agent that streams, so that path is
  untouched (FR-020).
- Progress between two reads is still not seen: the hand-off reads about once a second
  (`POLL_SECONDS`), and this feature changes no interval.

## R6. The thread's state

**Decision**: `AgentTask` in `api/schemas.py` gains one key, `progress`, which is null or an
object with `text` and `learned_at`. `_read_state` in `api/threads.py` fills it from the two
columns when both are set. It is filled for every agent task, pending or ended (FR-009).

One key that is null or whole, and not two keys side by side, because a text with no time,
or a time with no text, is then a shape no client has to handle. Every key the shape had
stays, with its meaning (FR-021). The contract is
[contracts/openapi.yaml](contracts/openapi.yaml).

- *verified* `GET /threads/{thread_id}/state` is the only route that returns agent tasks.
  The index of threads (`GET /threads`, `GET /threads/{thread_id}`) keeps only whether a
  thread has a pending agent task, and is not changed. No route is added (FR-018).
- *verified* `tests/test_state.py` pins the keys of an agent task and two whole entries. It
  is one of the tests FR-022 expects to change.

## R7. The orchestrator's check

**Decision**: `task_line` in `agent/agent_tasks.py` adds one more line under a task that is
`working` and has kept progress, in fixed words that say whose text it is and when it was
learned. Every other task is listed as today. `pending_line`, which is what the orchestrator
is told on every model call, is not changed, so progress reaches the model only in the
result of the check (FR-013, FR-014). The exact result is
[contracts/check-agent-tasks.md](contracts/check-agent-tasks.md).

- *verified* The check reads `store.list_tasks` and nothing else: no link and no agent.
- *verified* The system prompt already tells the orchestrator to use `check_agent_tasks` to
  answer questions about the thread's agent tasks (`agent/prompts.py`). It is not changed.
  The tool's description gains a clause that says the result holds the progress an agent
  last reported, in the agent's own words. No test pins that description.
- *verified* An outcome of a pending task is recorded in the thread as a call of this tool
  with a result of its own (`api/outcomes.py`). That result is built from the outcome, not
  by `task_line`, and does not change.
- The whole kept text is listed, up to its 2,000 characters. A cut for the model would be a
  second limit with nothing in the spec behind it.
- **Set apart** (FR-013): the text follows the progress line with every one of its lines
  indented by four spaces. An entry of the result starts at the beginning of a line with
  `- Agent`, so no line of an agent's text, whatever it holds, can pass for another entry,
  for an entry's status, or for the service's own words.

**Trust**: the text reaches the model as a tool result, the place an agent's reply has
always reached it. Whether an action pauses for a person is decided by the catalog and the
approval gate, not by anything the model reads (constitution, principle I). A test hands
the model a progress text that reads like an instruction and checks that a gated action
still pauses (FR-017, SC-007).

## R8. The task's page

**Decision**:

- `web/lib/tasks.ts`: `AgentTask` gains `progress: { text: string; learned_at: string } |
  null`.
- `web/components/tasks/agent-tasks.tsx`: under an agent task whose status is `working` and
  whose `progress` is not null, one line that says the agent last reported this and how
  long ago, then the text. An agent task in any other status shows nothing more than today
  (FR-010, clarified).
- The text is a text node in an element with `whitespace-pre-wrap` and `break-words`, as
  the task's message beside it is, so line breaks are kept and nothing is rendered (FR-012).
  It sits in a box with a greatest height that scrolls inside itself, so 2,000 characters
  neither hide the rest of the page nor are cut. The box can be reached by keyboard
  (`tabIndex={0}`) and is named for a screen reader as the progress the agent reported.
- The element takes its direction from its own text and isolates it (`dir="auto"`), so a
  direction mark in an agent's text cannot reorder the words around it (FR-012).
- A change of the text is not announced: a live region read again every 12 seconds would
  interrupt a person using a screen reader each time an agent moves on. The text is there
  when they come to it.
- When a reading of the task fails, the view says so as feature 007 built it, above what it
  last read; the progress it shows is that reading's, and its age goes on from the browser's
  clock, so it never claims to be newer than it is (Edge Cases).
- **How long ago** is worked out by a function of its own, `ago` in `web/lib/ago.ts`, from
  `learned_at` and a time it is given: "just now" under a minute, then whole minutes, hours,
  and days. The exact time is in a `<time>` element's `dateTime` and its title, formatted as
  the list of tasks formats a time.
- **The time it is given** is the browser's clock, kept in the view's state. It is read in
  two places that exist: the callback of the timer the view already has, and an effect that
  runs when a reading of the task arrives. No timer and no reading is added (FR-011). While
  the page is hidden the timer's callback returns before it does anything, as today.
  *Since Phase 8 (T045, T046)* it is also read when the view mounts and when a hidden page is
  shown again (a `visibilitychange` listener), so that a page opened again, or shown while
  the service cannot be reached, words the age at once; neither is a timer or a reading.

*verified* (Next.js docs, `01-app/03-api-reference/04-functions/io.md`): with Cache
Components, a Client Component is prerendered on the server, and a read of `Date.now()`
while rendering would be put in the static shell unless the component first calls `io()`.
The clock here is never read while rendering: only in an effect and in a timer's callback,
which do not run in a prerender. Until the first effect has run there is no time to compare
with, and the page shows the text without its age. So `io()` is not needed, and no new
`<Suspense>` boundary.

*verified* SWR hands the view the same data when a reading equals the one before, and the
component is then not rendered again. An age shown from the data alone would stand still
while nothing changes, which is exactly when a person wants to see it grow. That is why the
time is the view's state and moves with its timer.

**A browser whose clock is wrong** shows a wrong age. An age that would be negative is shown
as "just now". The exact time in the title does not depend on the browser's clock being
right, only on its time zone.

**Alternatives set aside**: a relative time from `Intl.RelativeTimeFormat`, which words by
locale; the console is in English and the words are pinned by tests. The service returning
its own current time with the state: a new key for a few seconds of difference. A timer of
the component's own that ticks every second: FR-011.

## R9. Nothing of it is logged

**Decision**: no line is added to the log by this feature, and no existing line gains the
text (FR-018a, clarified). FR-018a allows a line that says a task's progress changed,
without the text. None is added: an agent that puts a running clock in its text would write
one at every check of every task, and the thread's state already says when the text was
learned.

- *verified* The lines the worker and the hand-off write today name the agent, the task, and
  the thread, with an error's text, a state, or the result of a message the agent did not
  carry out. That result can hold the message of the status that ended a task, which is an
  outcome; none holds the message of a working status.
- *run* With every logger of the process at debug level, the prototype's progress texts
  appeared in lines of two kinds only. The first is the stand-in agent's own server
  (`a2a.server.*`), which in a deployment is another process. The second is
  `sse_starlette.sse`, which logs each chunk of a stream it sends at debug level; the
  service streams a run with it, so an `update` event's text is in such a line today, at
  debug level, with or without this feature. The A2A client (`a2a.client.*`) and `httpx`
  logged none of the text.
- The proof (SC-011) therefore searches everything the service's own loggers
  (`freyja_agent.*`) wrote at debug level and above, and everything any logger wrote at
  info level and above, for a recognizable progress text. That is what the spec's
  assumption says: what a library logs at its own debug level is not changed by this
  feature.

## R10. The stand-in agent

**Decision**: `tests/a2a_stub.py` gains what FR-023 asks for, an agent that reports a
progress text and changes it:

- `StubAgent.working_text`, the text of the first working status, `"Working on it."` as
  today; set to `None`, the stand-in attaches no message to a working status.
- `StubAgent.report(text)`: every `hold` task sends a working status with that text, or with
  no message when it is `None`. The wait of a `hold` task listens for it beside its release
  and its cancel. This is the prototype of R1.
- A keyword for a run by hand, `steps <n> <seconds>`: after its first working status, the
  task stays working, reports "Step k of n" every `<seconds>`, the first `<seconds>` after
  that status, and completes right after the last. The quickstart uses it with the
  stand-in served on loopback, with and without `--no-streaming`.
- An answer that starts with `slow` sends its working status with `working_text`, where it
  sends none today, so that an answer's hand-off has progress to keep (FR-006). This is the
  one existing behavior that changes; the existing tests that send such an answer check
  only its result.

*verified* The stand-in's first working status already carries "Working on it.", so with
this feature every `hold` task in the existing suite has that as its kept progress. Tests
that pin an agent task's whole shape in the thread's state, or the whole result of the
check, change by that (FR-022).

## R11. Tests

- **Service, no network** (pytest, the stand-in agent, in-memory stores, the scripted
  model): a new file for the feature, `tests/test_a2a_progress.py`, with the scenarios of
  User Stories 1, 3, and 4 and the edge cases; additions to `tests/test_agent_task_store.py`
  for the record's two fields in the in-memory store; changes to `tests/test_state.py` and
  to the tests that pin a whole entry or a whole check result.
- **The count of requests** (SC-002): each stand-in's transport records every request it
  received (`StreamingASGITransport.requests`). The same scenario is run with an agent that
  reports progress and with one that reports none, and the two lists are equal.
- **Nothing written to the thread** (SC-003): the thread's messages, the index's row, and the
  pending proposal are compared before and after progress is learned, also while the run
  lease is held and while a proposal waits.
- **Several instances and a restart** (SC-008): a second runtime on the same stores, as
  `tests/test_a2a_pending.py` does (`second_instance`), and a runtime opened again on them.
- **Postgres** (opt-in, `tests/integration/test_a2a_tasks_postgres.py`): the two columns are
  added to a table created without them, a row written before them is read with no progress,
  and progress round-trips. CI does not run these.
- **The log** (SC-011): R9. **The address** (FR-004): a text that holds an address is kept as
  written; `tests/test_agents_address_nowhere.py` is not changed, since no route and no log
  line about agents is added and an agent's own words are outside its search.
- **Web units** (Vitest): `web/__tests__/lib/ago.test.ts`; additions to
  `components/tasks/agent-tasks.test.tsx` (shown only when working, as text, markup not
  rendered, line breaks, the longest text, no progress) and to `task-view.test.tsx` (the age
  moves with the timer, nothing while hidden).
- **Web end to end** (Playwright, Chromium and Firefox): `web/e2e/tasks-progress.spec.ts`,
  with `page.clock` as `tasks-run.spec.ts` uses it, and `agentTask` in `web/e2e/tasks-api.ts`
  given a `progress` of null by default.

## R12. What is changed outside this feature's own files

Every file this feature changes belongs to an earlier feature; it adds none on the service
but tests. The files and why are in [plan.md](plan.md), "Source Code". Two things to know:

- `web/components/tasks/`, `web/lib/tasks.ts`, and `web/e2e/tasks-api.ts` are feature 007's,
  whose session may still run its last step (`speckit-converge`). The console phase of this
  feature is built on the `main` of its day and says so in its pull request.
- No setting, no dependency, no CI file, and no file under `.specify/` changes.
  `pyproject.toml`, `web/vitest.config.mts`, `.gitleaks.toml`, and
  `web/playwright.config.ts` are not touched.

## Not verified

- That the two columns are added to a table with rows in it under a real lock, in a
  deployment's database. The mechanism is feature 005's and was read, not run again. The
  opt-in Postgres test of this feature runs it.
- How the progress box looks and scrolls in Firefox with a text of 2,000 characters. The
  end-to-end test of the console checks it.
- That a model answers "how far is it?" well from the check's result. The automated tests
  use a scripted model, which shows that the result reaches the model and what it holds, not
  what a model makes of it. The quickstart asks it of a real model.

## Knowingly not covered

- **An agent that reports the same text for hours and one that is stuck look alike**, apart
  from the growing age. That is the most this feature can say: the agent's own words and
  when they last changed.
- **A browser with a wrong clock** shows a wrong age (R8).
- **A library's debug log** may hold an update's text, as today (R9). So may the message of
  an error a library raises about a reply it could not read, which the service logs as it
  does today; nothing was learned from such a reply, so it is no kept progress.
- **The check's result has no overall size limit**, as today: each working task adds up to
  2,000 characters of progress. A thread with many working tasks gives the model a long
  result.
- **The task's page shows no agent update of a run it streams.** It shows that Freyja wrote
  a reply or ran its actions (`STEPS` in `task-view.tsx`), and this feature does not add to
  that. User Story 4 is about the run's stream, and is seen by a client that reads it.
- **Feature 003's contract** says an update's `text` has "non-text parts replaced by notes".
  The code gives the text parts only (`_text`). This feature compares and shows the text as
  the code produces it and corrects neither side.
- **Instances on an earlier version** sharing the database neither read nor write the two
  columns; a task they watch shows no progress until an instance on this version claims it
  (spec, Assumptions).
