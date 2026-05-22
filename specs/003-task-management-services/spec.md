# Feature Specification: Hostaway Task Management Services

**Feature Branch**: `003-task-management-services` **Created**: 2025-07-14
**Status**: Draft **Input**: User description: "Add 4 Home Assistant services
for full CRUD management of Hostaway tasks via the Hostaway Tasks API"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create Task from Automation (Priority: P1)

As a property manager, I want to create Hostaway tasks from Home Assistant
automations so that maintenance workflows are triggered automatically based on
device states or events (e.g., "change batteries when device threshold is low").

**Why this priority**: This is the primary use case driving the feature.
Automation-driven task creation delivers the core value of bridging Home
Assistant device monitoring with Hostaway's task management.

**Independent Test**: Can be fully tested by calling the create_task service
with a title and verifying a task is created in Hostaway; delivers immediate
value for automation workflows.

**Acceptance Scenarios**:

1. **Given** the integration is configured and connected, **When** a user calls
   the create_task service with only a title, **Then** a new task is created in
   Hostaway and the task data is returned.
2. **Given** the integration is configured, **When** a user calls create_task
   with a title, listing_name, description, and a due date, **Then** the task is
   created with all specified fields and the listing is resolved to its numeric
   ID.
3. **Given** a listing_name that does not match any cached listing, **When**
   create_task is called, **Then** a validation error is raised with a clear
   message.
4. **Given** multiple config entries exist, **When** create_task is called
   without config_entry_id, **Then** a validation error is raised indicating the
   user must specify which account to use.
5. **Given** a single config entry exists, **When** create_task is called
   without config_entry_id, **Then** the single account is auto-detected and
   used.

---

### User Story 2 - Update Existing Task (Priority: P2)

As a property manager, I want to update task details (status, description,
assignee, priority) from automations or the Home Assistant UI so that task
workflows progress automatically (e.g., mark a task as completed when a sensor
confirms the work is done).

**Why this priority**: Updating tasks enables closed-loop automation — tasks
created automatically can also be progressed automatically based on sensor
feedback.

**Independent Test**: Can be fully tested by calling update_task with a known
task_id and verifying the updated fields are reflected in the returned task
data.

**Acceptance Scenarios**:

1. **Given** an existing task, **When** update_task is called with task_id and a
   new status, **Then** the task status is updated and the full updated task
   data is returned.
2. **Given** an existing task, **When** update_task is called with multiple
   optional fields (title, description, priority, assignee), **Then** all
   specified fields are updated.
3. **Given** a task_id that does not exist, **When** update_task is called,
   **Then** a validation error is raised indicating the task was not found.

---

### User Story 3 - List and Filter Tasks (Priority: P3)

As a property manager, I want to retrieve and filter Hostaway tasks from Home
Assistant so that I can query task status in automations or scripts (e.g., check
if there are pending tasks for a listing before guest checkout).

**Why this priority**: Listing tasks enables conditional automation logic and
reporting, building on the create/update capabilities.

**Independent Test**: Can be fully tested by calling get_tasks with various
filter combinations and verifying the returned task list matches expected
results.

**Acceptance Scenarios**:

1. **Given** tasks exist in Hostaway, **When** get_tasks is called with no
   filters, **Then** all tasks are returned.
2. **Given** tasks exist for multiple listings, **When** get_tasks is called
   with a listing_name filter, **Then** only tasks for the resolved listing are
   returned.
3. **Given** tasks with various statuses, **When** get_tasks is called with a
   status filter, **Then** only tasks matching that status are returned.
4. **Given** a date range, **When** get_tasks is called with
   can_start_from_start and can_start_from_end, **Then** only tasks within that
   date window are returned.

---

### User Story 4 - Delete Task (Priority: P4)

As a property manager, I want to delete tasks from Home Assistant so that I can
clean up cancelled or erroneous tasks created by automations.

**Why this priority**: Deletion is a supporting operation for lifecycle
management; less frequently needed than create/update/list but necessary for
completeness.

**Independent Test**: Can be fully tested by calling delete_task with a known
task_id and verifying the task is removed (subsequent get returns not found).

**Acceptance Scenarios**:

1. **Given** an existing task, **When** delete_task is called with the task_id,
   **Then** the task is deleted and no data is returned.
2. **Given** a task_id that does not exist, **When** delete_task is called,
   **Then** a validation error is raised indicating the task was not found.

---

### Edge Cases

- What happens when the Hostaway API is unreachable or returns a server error?
  The system raises an appropriate error to the caller.
- What happens when both listing_id and listing_name are provided to the same
  service call? The system should use listing_id directly and ignore
  listing_name (or raise a validation error if they conflict).
- What happens when listing_name matches multiple listings? The resolution
  should be exact-match only; if ambiguous, raise a validation error.
- What happens when date fields are provided in an invalid format? The system
  passes ISO strings through to the API; the API will reject invalid formats and
  the error is surfaced to the user.
- What happens when categories_map contains invalid category IDs? The API will
  reject them and the error is surfaced to the user.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a `hostaway.create_task` service that creates
  a task in Hostaway with at minimum a title.
- **FR-002**: System MUST provide a `hostaway.update_task` service that updates
  an existing task identified by task_id.
- **FR-003**: System MUST provide a `hostaway.delete_task` service that removes
  a task identified by task_id.
- **FR-004**: System MUST provide a `hostaway.get_tasks` service that retrieves
  tasks with optional filters (listing, reservation, status, date ranges).
- **FR-005**: All services (except delete) MUST return the task data in the
  service response.
- **FR-006**: The delete service MUST return no data (None) in the service
  response.
- **FR-007**: System MUST accept listing_name as an alternative to listing_id
  and resolve it to a numeric ID using the cached listings from the coordinator.
- **FR-008**: If listings data is not loaded when listing_name resolution is
  attempted, the system MUST raise a validation error.
- **FR-009**: System MUST auto-detect the config entry when only one account is
  configured; MUST require config_entry_id when multiple accounts exist.
- **FR-010**: System MUST convert parameter names from snake_case to camelCase
  when communicating with the Hostaway API.
- **FR-011**: System MUST surface "not found" API errors as validation errors to
  the caller.
- **FR-012**: System MUST surface other API errors as general errors to the
  caller.
- **FR-013**: Date fields (can_start_from, should_end_by, and date range
  filters) MUST be accepted as ISO format strings and passed through to the API.
- **FR-014**: The categories_map field MUST be accepted as a list of integer
  category IDs.
- **FR-015**: Task status values MUST be limited to: pending, confirmed,
  inProgress, completed, cancelled.
- **FR-016**: This feature MUST NOT introduce sensors, polling, or coordinator
  changes for tasks — services only.

### Key Entities

- **Task**: A work item managed in Hostaway, characterized by: title,
  description, status (pending/confirmed/inProgress/completed/cancelled),
  priority, assignee, associated listing, associated reservation, scheduling
  dates (can_start_from, should_end_by), categories, and resolution_note.
- **Listing**: A property managed in Hostaway, identified by numeric ID or
  resolved from a human-readable name. Listings data is provided by the existing
  coordinator cache.
- **Config Entry**: Represents a configured Hostaway account in Home Assistant.
  One or more may exist; services must identify which to use.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can create a Hostaway task from an automation in under 5
  seconds end-to-end (service call to confirmation).
- **SC-002**: All four CRUD operations (create, update, delete, list) complete
  successfully when called with valid parameters.
- **SC-003**: 100% of error scenarios (invalid task_id, missing listing, API
  failures) produce clear, actionable error messages to the user.
- **SC-004**: Listing name resolution returns the correct listing ID for all
  cached listings without additional API calls.
- **SC-005**: Users with a single configured account can use all services
  without specifying config_entry_id.

## Assumptions

- The existing Hostaway integration is configured and authenticated with valid
  API credentials.
- The listings coordinator is already implemented and provides a cache of
  listing data including internal names and numeric IDs.
- The Hostaway Tasks API endpoints (GET/POST /v1/tasks, GET/PUT/DELETE
  /v1/tasks/{taskId}) are stable and available.
- Task data is represented as raw dictionaries with snake_cased keys — no
  dedicated model class is introduced.
- The SupportsResponse mechanism for returning service call data is already
  available in the integration's Home Assistant version.
- Listing name resolution uses exact string matching against the internal
  listing name field.
- Date validation is delegated to the Hostaway API — the integration passes ISO
  strings through without additional parsing.
