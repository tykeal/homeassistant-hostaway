# Specification Quality Checklist: Custom Variable (Custom Field) Support

**Purpose**: Validate specification completeness and quality before
proceeding to planning
**Created**: 2026-09-21
**Feature**: [spec.md](../spec.md)

## Content Quality

- [ ] No internal implementation details (not met: the spec intentionally
  includes Home Assistant integration contracts that are the feature boundary)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No clarification markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [ ] Success criteria are technology-agnostic (not met: the user-facing
  outcomes intentionally name Home Assistant, Hostaway services, Hostaway
  fields, and Hostaway rate limits where those are the measurable outcomes)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [ ] No implementation details leak into specification (not met: coordinator,
  entity, attribute, and service response contracts are intentionally specified
  because they define observable Home Assistant behaviour for this feature)

## Notes

- Clarifications are resolved:
  - **FR-006**: definitions use a dedicated coordinator with a configurable
    `custom_field_definitions_scan_interval` option that defaults to 15
    minutes and enforces a one-minute minimum, with no user-invokable refresh
    service or refresh-on-miss requirement.
  - **FR-011**: each listing custom-variable value gets its own dynamic
    diagnostic sensor keyed from `custom_` plus slugified `varName`, with
    numeric-id disambiguation for collisions and stable late-collision
    behaviour, while the seven existing diagnostic listing sensors keep their
    current attribute surface.
  - **FR-015/FR-024**: reservation custom fields and read-service responses use
    collision-safe `custom_fields` mappings rather than display-name keys.
- External API endpoint and payload details appear in the spec only where they
  are the subject of safety verification requirements (FR-035 and FR-051
  through FR-055); they are drawn from issue #195 and live source evidence and
  define integration obligations rather than internal implementation design.
- SC-003 no longer depends on a minimum count of populated custom variables.
  The empty-diff no-op protocol uses the whole object as the control group,
  followed by a sentinel write and exact restore check.
- Three checklist items remain intentionally unchecked: implementation-detail
  and technology-agnostic checks are not fully met because this feature's
  measurable outcomes must name Home Assistant visibility, Hostaway service
  behaviour, and Hostaway API rate limits.
