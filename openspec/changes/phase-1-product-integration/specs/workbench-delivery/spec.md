## ADDED Requirements

### Requirement: Workbench assets are packaged and served
The system SHALL package the phase-one workbench HTML, CSS, and JavaScript inside the `yuqing` Python distribution and SHALL serve the entry page at `/v2` and static assets below `/v2/assets/`.

#### Scenario: Local workbench load
- **WHEN** an authenticated user requests `/v2`
- **THEN** the server returns the packaged workbench entry page with HTTP 200

#### Scenario: Packaged asset load
- **WHEN** the workbench requests an asset below `/v2/assets/`
- **THEN** the server returns only a file contained in the workbench asset directory with the correct content type

#### Scenario: Directory traversal attempt
- **WHEN** a request attempts to escape the workbench asset directory
- **THEN** the server rejects the request and does not disclose another local file

### Requirement: Legacy dashboards remain available
The system SHALL keep `/`, `/dash`, and `/exec` operational while `/v2` is being validated.

#### Scenario: Workbench rollback
- **WHEN** the new workbench is unavailable or disabled
- **THEN** an authenticated user can still access the existing legacy dashboard routes

### Requirement: Build artifacts contain the workbench
The wheel and Docker image SHALL contain every workbench asset required by `/v2`.

#### Scenario: Wheel content verification
- **WHEN** CI builds the Python wheel
- **THEN** an automated check confirms that the workbench entry page and assets exist in the wheel
