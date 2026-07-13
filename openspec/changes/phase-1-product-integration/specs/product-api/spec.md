## ADDED Requirements

### Requirement: Versioned API response contract
Every phase-one product endpoint SHALL use the `/api/v1` namespace and SHALL return the common success or error envelope defined in the design.

#### Scenario: Successful request
- **WHEN** an authenticated request is processed successfully
- **THEN** the response contains `success=true`, a `data` value, and metadata including `generated_at` and `data_quality`

#### Scenario: Invalid request
- **WHEN** request parameters or state transitions are invalid
- **THEN** the response contains `success=false` and a stable error code without an internal stack trace

### Requirement: Product read APIs reuse domain logic
Product read APIs MUST call the existing Store, analytics, insights, alerts, report, and collection logic and MUST NOT implement a second browser-specific business calculation.

#### Scenario: Overview metric consistency
- **WHEN** the overview API and the existing analytics functions read the same database snapshot
- **THEN** BHI, sentiment, risk, and volume values use the same domain results

### Requirement: Protected mutations
Every phase-one mutation endpoint SHALL enforce the existing session, origin, forwarded-host, and CSRF controls.

#### Scenario: Authenticated same-origin mutation
- **WHEN** a valid OAuth session performs a same-origin mutation
- **THEN** the server processes the mutation and writes an auditable SQLite record

#### Scenario: Cross-origin mutation
- **WHEN** a mutation comes from an invalid origin or missing session
- **THEN** the server returns HTTP 403 and performs no write

### Requirement: Data quality is explicit
Product APIs SHALL distinguish missing or degraded observations from measured zero values.

#### Scenario: Failed platform collection
- **WHEN** a monitored platform has a failed or stale collection state
- **THEN** the API returns a degraded data quality status and explanatory note instead of presenting the absence as zero risk
