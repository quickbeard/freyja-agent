# Feature Specification: Web Console Shell and LLM Profiles

**Feature Branch**: `004-web-llm-profiles`

**Created**: 2026-11-17

**Status**: Draft

**Input**: User description: "Web console shell and LLM profiles. The web app gets a left sidebar with two entries, LLM and Agents; the Agents entry is only a placeholder in this feature and is built in the next one. The LLM section lets an operator configure which model Freyja's orchestrator runs on without editing environment variables or restarting the service. It shows the configured LLM profiles as a list of cards and has an Add button. A profile has a name, a provider (an OpenAI-compatible endpoint, Anthropic, or OpenRouter), a model id, an optional base URL, an API key, and for OpenAI-compatible endpoints a switch that says whether the model is called through the Responses API or through chat completions. Many profiles can be saved and exactly one is active: the active profile is the model the orchestrator uses. The operator can add, edit, delete, and activate a profile, and can test a profile, which sends a short conversation of more than one turn to the endpoint and reports whether it worked, so a failure that only shows on a second turn is caught before the profile is used. Activating a profile takes effect on the running service without a restart: runs already in progress finish on the model they started with, and the next run uses the newly active one. The model configured through environment variables today (FREYJA_MODEL with the provider's key and base URL) appears as a read-only profile marked as coming from the environment, and it is the active one until a saved profile is activated, so an existing deployment keeps working unchanged. API keys typed into the web app are stored encrypted, are never returned by the service or written to logs, and a card shows only the last few characters of its key. The active profile cannot be deleted. Profiles are saved in the service's database and survive a restart. The web app is a client of Freyja's API only: every change goes through the API, and the browser reaches the API on the same origin as the web app. The API stays unauthenticated and bound to this machine, as today."

## Clarifications

### Session 2026-11-17

- Q: When the service starts and no usable model is configured, for example on a first installation with no key or when the active profile's stored key can no longer be read, should it start anyway? → A: Yes. The service starts and the console works; every run ends with an error saying that no usable model is active, until the operator activates a usable profile. Freyja never switches to another model by itself, not even to the environment's.
- Q: When several service instances share one database, should a model change made through one instance reach the others, in this feature? → A: Yes. Every instance notices an activation or an edit of the active profile, and a run that starts on any instance ten seconds later or more uses the new values. The instance that handled the change applies it at once.
- Q: Should the console let an operator activate a profile that has not passed a test? → A: Yes, with a warning. Each card shows its profile's last test result: passed, failed, or not tested since its last change. Activation is always possible, and its confirmation warns and offers to test first when the profile has not passed. The last result is therefore kept with the profile.
- Q: Should Freyja record which profile each run used, so an operator can tell afterwards which model produced a reply? → A: In the run's stream and in the service log. The first event of each run's stream names the profile the run uses, and the service logs one line per run naming it. Nothing is added to the thread itself.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open the console and see which model Freyja runs on (Priority: P1)

An operator opens the web app. A sidebar on the left lists the console's sections, LLM and Agents, and stays in place while they move between them. The LLM section shows every configured LLM profile as a card, and one card is marked active: that is the model the orchestrator uses. On a deployment that has only ever been configured through environment variables, there is exactly one card. It carries the model from the environment, is marked as coming from the environment, is active, and cannot be changed from the console. The Agents entry opens a page that says agents are not managed from the console yet.

**Why this priority**: This is the console's frame and the smallest slice that is useful by itself: an operator can see, without reading configuration files, which model the service is running on. Every other story adds to this page.

**Independent Test**: Start the service configured through environment variables only, with no saved profile, and open the web app. The sidebar shows LLM and Agents. The LLM section shows one card with the environment's provider and model, marked as coming from the environment and as active, with no key shown beyond its last characters and no way to edit or delete it. A run started through the API behaves exactly as it did before this feature.

**Acceptance Scenarios**:

1. **Given** the web app is open on any page, **When** the operator looks at the sidebar, **Then** it lists LLM and Agents, marks the section they are in, and choosing an entry opens that section without losing the sidebar.
2. **Given** a service configured through environment variables with no saved profile, **When** the operator opens the LLM section, **Then** it shows one card with the environment's provider, model id, and base URL if one is set, marked as coming from the environment and as active, with no control to edit or delete it.
3. **Given** the LLM section, **When** a card is shown, **Then** its key appears only as its last four characters, and nowhere on the page or in what the page received is the rest of the key.
4. **Given** the web app's address alone, **When** the operator opens it, **Then** they land in the LLM section.
5. **Given** the Agents entry, **When** the operator opens it, **Then** the page says agents are not managed from the console yet and offers no control.
6. **Given** the service cannot be reached, **When** the operator opens the LLM section, **Then** the page says the service cannot be reached and offers to try again, and it shows no profile as if it were current.
7. **Given** a service configured through environment variables with a usable model and no saved profile, **When** runs are started through the API, **Then** they use the environment's model and behave as they did before this feature.

---

### User Story 2 - Add a profile and switch Freyja to it without a restart (Priority: P2)

The operator presses Add, fills in a name, a provider, a model id, an optional base URL, and an API key, and saves. The profile appears as a new card. They choose Set active on it and confirm. From then on, runs use that model: the service was not restarted and no environment variable was touched. A run that was streaming when they switched finishes on the model it started with. After a restart the same profile is still there and still active.

**Why this priority**: This is the reason for the feature. Changing the model today means editing a file and restarting the service, which interrupts every run in progress.

**Independent Test**: With the service running on the environment's model, add a profile for a second model through the console and activate it. Start a run and confirm from the model endpoint's side that the second model received it. Restart the service and start another run: it still goes to the second model. Activate the environment card again and confirm the next run goes back to the first model.

**Acceptance Scenarios**:

1. **Given** the LLM section, **When** the operator presses Add, enters a name, a provider, a model id, and an API key, and saves, **Then** a new card appears with those values and the key shown as its last four characters, and the active profile is unchanged.
2. **Given** a saved profile that is not active, **When** the operator chooses Set active and confirms, **Then** its card is marked active, the card that was active no longer is, and exactly one card is active.
3. **Given** a profile was just activated, **When** a run starts on the same service, **Then** it uses the newly active profile's model, and the service was not restarted.
4. **Given** a run is streaming, **When** another profile is activated, **Then** that run completes on the model it started with, including the work of its subagent, and the next run on the same thread uses the newly active model with the thread's history intact.
5. **Given** a run paused on a proposal, **When** another profile is activated and the proposal is then decided, **Then** the continuation uses the newly active model.
6. **Given** a saved profile is active, **When** the service restarts, **Then** the profile is still listed, still active, and runs use it, whatever the environment's model is.
7. **Given** a saved profile is active, **When** the operator activates the environment card, **Then** runs use the environment's model again and no saved profile is active.
8. **Given** the form, **When** the provider is an OpenAI-compatible endpoint, **Then** it offers the switch between the Responses API and chat completions, on by default; for the other providers the switch is not offered and has no effect.
9. **Given** the form, **When** a required value is missing, the name is already used, or the base URL is not a web address, **Then** nothing is saved and the form says which value is wrong and why.
10. **Given** several service instances share one database, **When** a profile is activated through one of them, **Then** runs that start on any instance ten seconds later or more use the newly active profile.
11. **Given** a run starts, is continued after a pause, or is started in the background, **When** its stream opens, **Then** the first event names the profile the run uses and says whether it comes from the environment, and the service log has one line for the run naming the same profile; neither contains a key.

---

### User Story 3 - Test a profile before relying on it (Priority: P3)

Before saving a profile, or at any time afterwards, the operator presses Test. The service holds a short conversation with the endpoint the way a run would: a first message, then a second request that carries the first exchange together with a tool call and its result. The console reports that the profile works, or says which step failed and why, and the profile's card keeps that result. An endpoint that answers a first message and refuses everything after it is reported as failing, before a single run depends on it.

**Why this priority**: A model endpoint can accept a first message and reject a conversation, which only shows once a run is under way and has already failed. The test turns that into something the operator learns in seconds. It is not needed to switch models, so it comes after the switch itself.

**Independent Test**: Point a profile at an endpoint that answers a first message and refuses a request carrying earlier turns, and press Test: the console reports a failure on the second step with the endpoint's reason, and the card shows the failure. Choose Set active on it: the confirmation warns that its last test failed. Point it at a working endpoint and test again: the console reports success and the warning is gone. In every case the active profile, each profile's values, and every thread are unchanged.

**Acceptance Scenarios**:

1. **Given** a profile whose endpoint handles a conversation, **When** the operator presses Test, **Then** the console reports within sixty seconds that the profile works.
2. **Given** a profile whose endpoint answers a first message and refuses a request that carries an earlier reply, a tool call, and its result, **When** the operator presses Test, **Then** the console reports that the second step failed and shows the endpoint's reason.
3. **Given** a profile whose endpoint cannot be reached or rejects the key, **When** the operator presses Test, **Then** the console says so, naming the first step.
4. **Given** the Add or Edit form with values not yet saved, **When** the operator presses Test, **Then** those values are tested and nothing is saved.
5. **Given** the Edit form with the key left empty, **When** the operator presses Test, **Then** the stored key is used.
6. **Given** the environment card, **When** the operator presses Test, **Then** the environment's model is tested the same way.
7. **Given** any test, **When** it ends, **Then** the active profile, every profile's values, and every thread are as they were, and the reason shown contains no part of a key. The one thing a test of a listed profile changes is that profile's last test result.
8. **Given** an endpoint that does not answer, **When** the test's time limit passes, **Then** the test ends and reports that the endpoint did not answer in time.
9. **Given** a listed profile that was tested, **When** its card is shown, also after the page is loaded again or the service restarts, **Then** the card says whether the last test passed or failed and when, and for a failure which step failed.
10. **Given** a saved profile with a test result, **When** its provider, model id, base URL, key, or choice of API is changed, **Then** its card says it has not been tested since its last change; changing only its name keeps the result.
11. **Given** a profile whose last test did not pass, or that has not been tested since its last change, **When** the operator chooses Set active, **Then** the confirmation says so and offers to test first, and the operator can still activate it.

---

### User Story 4 - Change and remove profiles (Priority: P4)

The operator opens a saved profile, changes its model id or its base URL, and saves. They replace its key by typing a new one, or keep it by leaving the key empty. They delete a profile they no longer need, after confirming. The console refuses to delete the active profile and says to activate another one first. The environment card can be neither edited nor deleted.

**Why this priority**: Profiles need upkeep: a key is rotated, a model is renamed, an endpoint moves. Without this the only correction is to add a second profile, which is workable, so this comes after adding, switching, and testing.

**Independent Test**: Edit a saved profile's model id and save: the card shows the new value. Edit the active profile's model id and start a run: the run uses the new model id without a restart. Save an edit with the key left empty and test the profile: it still works. Delete an inactive profile: its card is gone and stays gone after a restart. Try to delete the active profile: it is refused with a reason.

**Acceptance Scenarios**:

1. **Given** a saved profile, **When** the operator edits any of its values and saves, **Then** the card shows the new values.
2. **Given** the Edit form, **When** the operator leaves the key empty and saves, **Then** the stored key is kept; **When** they type a new key and save, **Then** the new key replaces it and the card shows the new key's last four characters.
3. **Given** the active profile, **When** the operator edits it and saves, **Then** the next run uses the edited values without a restart, and a run in progress finishes on the values it started with.
4. **Given** a saved profile that is not active, **When** the operator deletes it and confirms, **Then** its card is gone, it cannot be activated, and it is still gone after a restart.
5. **Given** the active profile, **When** the operator tries to delete it, **Then** it is refused and the console says to activate another profile first.
6. **Given** the environment card, **When** the operator looks for a way to edit or delete it, **Then** there is none, and a request to do so sent straight to the service is refused.
7. **Given** two operators have the same profile open, **When** both save, **Then** the later save is the one kept, and each sees the current values when they next load the section.

---

### User Story 5 - API keys stay secret (Priority: P5)

An operator types an API key once. From then on nobody reads it back: not in the console, not from the service, not in a log, and not from the database's files. The card shows its last four characters so that two keys can be told apart. If the secret that protects stored keys is missing, the service says so when a profile is saved and saves nothing; if that secret is later changed, profiles whose keys can no longer be read are shown as unusable until a key is typed again.

**Why this priority**: The rule protects every other story, and each of them is written to respect it. It is listed last because it adds no capability of its own; it is still required before anything that stores a key is released.

**Independent Test**: Save a profile with a key made of a recognizable string. Search everything the service returned to the console, the service's log, every thread, and the stored profile for that string: it appears nowhere except as its last four characters. Start the service without the secret that protects stored keys and try to save a profile: it is refused with a reason naming the missing setting, and the environment's model keeps working.

**Acceptance Scenarios**:

1. **Given** a saved profile, **When** anything about it is returned, listed, logged, or streamed, **Then** it contains the last four characters of the key at most.
2. **Given** a saved profile, **When** its stored form is read directly from the database, **Then** the key cannot be read from it without the secret that protects stored keys.
3. **Given** an endpoint that repeats the key in an error, **When** a test or a run fails with that error, **Then** the text shown, logged, or kept in a thread has the key removed.
4. **Given** the secret that protects stored keys is not set, **When** the operator saves a profile, **Then** nothing is saved, the console names the missing setting, and the environment's model, its card, and its runs are unaffected.
5. **Given** stored keys were protected with a secret that has since changed, **When** the service starts, **Then** it starts, each profile whose key cannot be read is shown as unusable, and typing its key again makes it usable.
6. **Given** a key shorter than eight characters, **When** its card is shown, **Then** no character of it is shown.

---

### Edge Cases

- No saved profile exists and the environment names no usable model, for example on a first installation with no key. The service starts, the environment card is shown as not usable, and a run ends with an error saying that no usable model is active, until the operator adds and activates a profile.
- The active profile becomes unusable, because its key can no longer be read. The service starts and stays reachable so that the console can fix it; runs end with the same error, and the service does not fall back to another model by itself.
- A profile is activated whose endpoint is down. Activation does not contact the endpoint, so it succeeds; runs then fail with the model error they would get today. The test is how the operator finds out beforehand.
- A profile cannot be turned into a model at all, for example an unknown provider value sent straight to the service. Saving it is refused with the reason, and nothing changes.
- The operator's page is stale: they activate, edit, or delete a profile that another operator has just deleted. The request is refused as not found and the list shows the current profiles.
- A profile is deleted at the same moment as it is activated. One of the two wins: either the profile is active and still exists, or it is gone and the previous profile is still active. There is never no active profile and never two.
- The service stops in the middle of an activation. After the restart either the earlier profile or the new one is active, never both and never neither.
- A profile is activated while a background run is delivering an agent's outcome. That run finishes on the model it started with; the next background run uses the newly active one.
- The environment's model is changed and the service restarted while a saved profile is active. The environment card shows the new values, and the saved profile stays active.
- The switch between the Responses API and chat completions is sent for a provider that has none. It is ignored, as the matching environment setting is today.
- Values at the limits: a name, a model id, a base URL, or a key longer than allowed is refused with the limit named; a base URL that is not a web address is refused.
- Two profiles carry the same provider, model id, and key under different names. Both are kept; only names must differ.
- A test is running when its profile is edited or deleted. The test finishes with the values it started with and its result is reported to whoever started it, but it is not recorded: the result belongs to values the profile no longer has.
- The operator presses Set active on the profile that is already active. Nothing changes and no run is affected.
- More profiles than the console's limit are requested. Adding one more is refused with the limit named.
- The console is opened on a narrow screen. The sidebar can be collapsed and opened again, and every control stays reachable.

## Requirements *(mandatory)*

### Functional Requirements

**Console shell**

- **FR-001**: The web app MUST show a sidebar on the left of every page that lists the console's sections, LLM and Agents, marks the section in use, and opens a section when its entry is chosen. On a narrow screen the sidebar MUST be collapsible, with every section still reachable.
- **FR-002**: Opening the web app's address alone MUST lead to the LLM section. The Agents section MUST say that agents are not managed from the console yet and MUST offer no control.
- **FR-003**: The web app MUST read and change everything through the service's API, on the same origin the web app is served from. It MUST hold no model credential and call no model endpoint itself. When the service cannot be reached or refuses a request, the page MUST say so with the service's reason, offer to try again, and never present earlier data as current.

**Profiles**

- **FR-004**: The service MUST keep LLM profiles. A profile has a name, a provider, a model id, an optional base URL, an API key, and, for an OpenAI-compatible endpoint, a choice between the Responses API and chat completions that defaults to the Responses API. The provider is one of three: an OpenAI-compatible endpoint, Anthropic, or OpenRouter.
- **FR-005**: An operator MUST be able to add a profile, see every profile, edit a saved profile, and delete a saved profile, from the LLM section. Saved profiles MUST survive a restart of the service.
- **FR-006**: A name MUST be unique among profiles, compared without regard to letter case, and MUST be 1 to 64 characters. A model id MUST be 1 to 200 characters, a base URL at most 2,000 characters and a web address when given, and a key 1 to 4,000 characters. At most 50 profiles MAY be saved. A request that breaks any of these MUST be refused, name the value and the rule, and save nothing.
- **FR-007**: The LLM section MUST show the profiles as cards, each with the profile's name, provider, model id, base URL if any, the choice of API where it applies, the last four characters of its key, whether it is active, whether it comes from the environment, whether it is usable, and its last test result: passed or failed and when, with the step that failed, or not tested since its last change. It MUST have an Add button that opens the form for a new profile.
- **FR-008**: The model the environment configures MUST appear as one profile marked as coming from the environment. It MUST NOT be editable or deletable, from the console or by a request sent straight to the service. It MUST show the values the service was started with.
- **FR-009**: When a saved profile is edited, a key left empty MUST keep the stored key, and a key that is given MUST replace it.
- **FR-010**: Deleting MUST ask for confirmation in the console. The active profile MUST NOT be deletable; the refusal MUST say to activate another profile first.

**The active profile**

- **FR-011**: Exactly one profile MUST be active at any time, and it is the model the orchestrator and its subagent use. With no saved profile activated, the environment's profile is the active one, so a deployment configured only through the environment behaves as before this feature.
- **FR-012**: An operator MUST be able to activate any usable profile, including the environment's, after confirming in the console. Activation MUST either complete or leave the earlier profile active; it MUST NOT contact the model endpoint, and it MUST NOT depend on a test. When the profile's last test did not pass, or it has not been tested since its last change, the confirmation MUST say so and offer to test first.
- **FR-013**: Activating a profile, and editing the active one, MUST take effect without a restart: a run that starts afterwards on the same service instance uses the new values. A run in progress MUST finish on the model it started with, including its subagent's work. A paused run that is continued afterwards, and a background run that starts afterwards, use the newly active profile. A thread's history MUST be unaffected by a change of model.
- **FR-014**: When several service instances share one database, a run that starts on any of them ten seconds or more after an activation or an edit of the active profile MUST use the new values. The instance that handled the change MUST apply it at once (FR-013).
- **FR-015**: Which profile is active MUST survive a restart. A saved profile that is active stays active whatever the environment configures.
- **FR-016**: The service MUST start when the active profile, or the environment's model, is not usable. A run MUST then end with an error that says no usable model is active, and the service MUST NOT switch to another profile by itself, the environment's included. The LLM section MUST say that no usable model is active while that is the case.

**Testing a profile**

- **FR-017**: An operator MUST be able to test any profile and the values in the Add or Edit form before they are saved. In the Edit form an empty key means the stored key.
- **FR-018**: A test MUST hold a conversation of at least two requests with the profile's endpoint, built the way a run builds one: a first message, then a request carrying that exchange together with a tool call and its result. It passes only if every request is answered. It MUST report which step failed and the endpoint's reason.
- **FR-019**: A test MUST end within sixty seconds, reporting an endpoint that did not answer in time. It MUST NOT change the active profile, any profile's values, or a thread.
- **FR-019a**: The result of the last test of a listed profile MUST be kept with it: whether it passed, when, and for a failure the step and a reason of at most 500 characters. A saved profile's result MUST survive a restart and MUST be cleared when its provider, model id, base URL, key, or choice of API changes; a change of name keeps it. The environment profile's result is kept until the service restarts. A test of unsaved form values is not kept, and neither is a result for a profile that was changed or deleted while the test ran.

**Keys**

- **FR-020**: A key MUST be stored so that it cannot be read from the stored profile without a secret the operator gives the service through its configuration. The service MUST never return a key, and MUST never write one to a log, a thread, or a stream. The most it shows of a key is its last four characters, and nothing of a key shorter than eight characters.
- **FR-021**: Text about a profile that is shown, logged, or kept, including a reason from a model endpoint, MUST have the profile's key removed from it.
- **FR-022**: When the secret that protects stored keys is not set, saving a profile MUST be refused with a reason that names the missing setting, and the environment's profile MUST keep working. When stored keys cannot be read with the current secret, the service MUST start, mark each such profile unusable, and let the operator make it usable by entering its key again.

**Compatibility and record**

- **FR-023**: With no saved profile and a usable model in the environment, every behavior of the service MUST be what it was before this feature, and every existing automated test MUST pass unchanged. The differences are FR-016, without a usable model, and the profile named in a run's first event and in the log (FR-024a), which adds to what a run reports and takes nothing away.
- **FR-024**: The service MUST log, without any key, each profile added, edited, deleted, and activated, naming the profile.
- **FR-024a**: The first event of every run's stream, including a continuation after a pause and a background run, MUST name the profile the run uses and say whether it comes from the environment. The service MUST log one line per run naming that profile. Neither contains a key, and nothing about the profile is added to the thread.
- **FR-025**: The web app's behavior and the service's behavior MUST each be covered by automated tests that need no model provider and no network, with a stand-in for the model endpoint.

### Key Entities

- **LLM profile**: One way to reach a model. It has a name, a provider, a model id, an optional base URL, a key, the choice of API for an OpenAI-compatible endpoint, when it was added and last changed, where it comes from (saved by an operator, or the environment), whether it is usable, and the result of its last test. A saved profile belongs to the service's database; the environment's profile is read from the service's configuration and is never stored.
- **Active profile**: The one profile the orchestrator and its subagent use. A single choice shared by every service instance on the same database, kept across restarts.
- **Profile test**: One check of a profile or of unsaved form values. It has the steps it ran, whether each was answered, and the reason for a failure. The last one for a listed profile is kept with that profile until the profile's way of reaching its model changes; a check of unsaved form values is not kept.
- **Console section**: A named area of the web app reached from the sidebar. This feature has two: LLM, and Agents as a placeholder.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator who has a model's details at hand can add a profile, activate it, and have the next run use it in under two minutes, without editing a file and without restarting anything.
- **SC-002**: After an activation, every run that starts on the same service instance uses the newly active model, and every run that starts on another instance ten seconds later or more does too.
- **SC-003**: No run in progress fails, stops, or changes model because a profile was activated or edited while it ran.
- **SC-004**: A recognizable key saved in a profile appears nowhere in what the service returns, logs, streams, or keeps in a thread, and cannot be found in the stored profile, beyond its last four characters.
- **SC-005**: A profile whose endpoint accepts a first message and refuses a conversation is reported as failing by the test every time, and the test ends within sixty seconds whatever the endpoint does.
- **SC-006**: A deployment configured only through the environment, with a usable model, behaves exactly as before: every automated test that existed before this feature passes unchanged.
- **SC-007**: Saved profiles and the choice of active profile are the same after a restart as before it, every time.
- **SC-008**: From any page of the console, the LLM section is one action away, and with fifty saved profiles its cards are shown within two seconds on the machine the service runs on.
- **SC-009**: At every moment, including during concurrent activations and deletions and across a restart in the middle of one, exactly one profile is active.
- **SC-010**: For every run, an operator can tell which profile it used from the run's stream while it runs, and from the service log afterwards.
- **SC-011**: An operator about to activate a profile that has not passed a test since its last change is told so before they confirm, every time.

## Assumptions

**Scope**

- Out of scope, per the description: the Agents section beyond its placeholder, which is the next feature; authentication for the console or the API; reaching the console or the API from another machine; and a model per role, such as one for the orchestrator and another for its subagent. The orchestrator and its subagent use the one active profile.
- Also out of scope: the models of delegate agents, which stay their teams' concern; choosing a model per thread or per run; keeping with a thread which profile produced each reply, which a run reports in its stream and the log instead (FR-024a); usage or cost reporting; reordering or grouping cards; and importing or exporting profiles.
- The providers are the three the service has clients for today. Adding a provider is a later change.
- The console is in English and is used with a current desktop browser; the narrow-screen rule in FR-001 keeps it usable on a small window, and a dedicated mobile layout is out of scope.

**Behavior choices**

- The service starts without a usable model (FR-016). Today it stops at startup when the configured model cannot be built. Starting anyway is what lets an operator fix the model from the console, and it is the only way a first installation with nothing in the environment can be configured there. A run without a usable model fails with a clear error instead. This is the largest change to existing behavior in this feature. Confirmed in clarification on 2026-11-17.
- An unusable active profile is never replaced automatically (FR-016). Falling back to another model without the operator asking, the environment's included, would send conversations to an endpoint they did not choose. Confirmed in clarification on 2026-11-17.
- A paused run continues on the newly active model (FR-013). Nothing holds a paused run in memory, so after a restart it could only continue on whatever is active then; the rule is the same without a restart.
- Activation does not require a passing test (FR-012). An endpoint can be down for a moment, and the operator may know better. The confirmation warns when the profile has not passed a test since its last change and offers to run one. Confirmed in clarification on 2026-11-17.
- A key is required for every saved profile. An OpenAI-compatible server that checks no key accepts any text, as the environment's configuration already documents.
- Only the last test result of a profile is kept (FR-019a); there is no history of tests. A result says what was true when the test ran, not that the endpoint works now, which is why the card shows when it ran.
- A test sends a few short requests to the endpoint and may be billed by its provider as any request is.
- Concurrent edits of one profile are settled by the later save (User Story 4). Profiles are few and operators fewer; locking is not worth its cost here.
- Ten seconds is the bound for other instances to pick up a change (FR-014); the plan sets how. The instance that handled the change applies it at once. Confirmed in clarification on 2026-11-17.
- Trust, stated in one place. The operator is trusted: anyone who can reach the console can change the model every thread uses. A model endpoint is not trusted with the service's logs or threads, which is why its error text is cleaned of keys (FR-021). The base URL an operator enters is trusted as they entered it and may name another machine, since model endpoints usually are elsewhere.

**Environment and dependencies**

- The constitution's principle IV says all runtime configuration is read from environment variables. Profiles are configuration an operator changes while the service runs, kept in the database, and the secret that protects stored keys is a new environment setting. The constitution is amended to allow operator-managed configuration before this feature's plan is written; the plan's Constitution Check depends on it.
- The service keeps binding to the local interface with no authentication. With this feature, anyone who can reach the port can change the model and read every profile except its key. That is acceptable for local use, is documented, and must be revisited before the service is bound more widely.
- The web app is a client of the service's API only, and the browser reaches the API on the web app's own origin, so the API needs no change to accept requests from another origin.
- Several service instances may share one database, as features 001 to 003 already allow; the active profile is one choice for all of them.
- The existing thread routes, approval gate, run lease, step limit, catalog, and delegation worker are reused unchanged. This feature adds the profiles, the active choice, the test, and the routes the console needs, and changes how the service obtains its model from one fixed value at startup to the active profile.
- The setting that chooses between the Responses API and chat completions for the environment's model (feature 001's research R3) keeps its meaning and is what the environment's profile shows.
