## ADDED Requirements

### Requirement: Phase-one navigation exposes only real workflows
The workbench SHALL expose overview, collection, review, analysis, incidents, backlog, reports, and watch configuration as phase-one workflows. Structured knowledge editing, report approval, and remote secret editing SHALL NOT perform simulated persistence.

#### Scenario: Phase-one menu load
- **WHEN** an authenticated analyst opens `/v2`
- **THEN** every enabled navigation item is backed by a real API and persistent or deterministic domain behavior

### Requirement: Analyst can operate the collection workflow
The analyst SHALL be able to inspect platform health, inspect execution state, start a collection run, request a cooperative stop, and identify the actual collection execution environment.

#### Scenario: Collection run starts
- **WHEN** an authorized analyst starts collection while no run is active
- **THEN** the system starts one background run and reports its current stage

#### Scenario: Duplicate collection request
- **WHEN** an analyst starts collection while a run is already active
- **THEN** the system does not start a second run and reports the existing run

### Requirement: Analyst can complete review work
The analyst SHALL be able to filter the review queue and save single or batch verdicts, with a maximum batch size of 100 documents.

#### Scenario: Batch review
- **WHEN** an authorized analyst submits valid verdicts for no more than 100 documents
- **THEN** the system returns a result for every document and the saved verdicts survive service restart

### Requirement: Analyst can inspect and transition incidents
The workbench SHALL show incident details and only actions allowed by the server-side incident state machine.

#### Scenario: Valid incident transition
- **WHEN** the analyst chooses an action included in `allowed_actions`
- **THEN** the incident changes state and records actor, timestamp, and note

#### Scenario: Invalid incident transition
- **WHEN** the analyst requests an action not allowed in the current state
- **THEN** the incident remains unchanged and the API returns `INVALID_TRANSITION`

### Requirement: Analyst can inspect deterministic analysis and reports
The analyst SHALL be able to view ABSA, topics, BHI, backlog, reports, and source documents derived from the shared database.

#### Scenario: Source traceability
- **WHEN** the analyst opens a report citation
- **THEN** the system returns the referenced source document or an explicit not-found response

### Requirement: Analyst can maintain monitoring configuration
The analyst SHALL be able to read and update validated watch configuration, keywords, and seed suggestions without exposing stored secrets.

#### Scenario: Invalid watch configuration
- **WHEN** submitted watch YAML fails validation
- **THEN** the server rejects it and leaves the previous configuration unchanged
