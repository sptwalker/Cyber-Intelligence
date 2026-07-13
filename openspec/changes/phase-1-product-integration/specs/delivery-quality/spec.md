## ADDED Requirements

### Requirement: Workbench states are explicit
Every phase-one page SHALL provide loading, empty, error, unauthorized, and retry states appropriate to its request.

#### Scenario: API request failure
- **WHEN** a page request fails
- **THEN** the page shows a readable error and retry control without replacing the error with demo data

### Requirement: Mutations prevent duplicate submission
The workbench SHALL disable a mutation control while its request is in flight and SHALL refresh the corresponding server data after success.

#### Scenario: Repeated click during mutation
- **WHEN** a user clicks the same active mutation control multiple times before completion
- **THEN** only one request is submitted

### Requirement: Untrusted content is rendered safely
The system SHALL escape user text and report content and SHALL restrict source links to allowed HTTP or HTTPS protocols.

#### Scenario: Malicious source content
- **WHEN** stored text contains HTML or a JavaScript URL
- **THEN** the workbench displays inert text and does not execute it

### Requirement: CI blocks regressions
The CI verification stage SHALL run unit tests, end-to-end selfcheck, architecture regression, scheduler selftest, compilation, and workbench package checks.

#### Scenario: Core selfcheck failure
- **WHEN** any required verification command exits non-zero
- **THEN** CI blocks image build or deployment

### Requirement: Workbench is released through a reversible rollout
The system SHALL keep `/v2` as a gray release until real-user acceptance is complete and SHALL retain a previously working image for rollback.

#### Scenario: Gray release failure
- **WHEN** blocking errors are found during workbench validation
- **THEN** operators can continue using legacy routes and roll back without losing SQLite data
