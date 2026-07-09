# Contributing to Zulip (backend)

This is the Zulip server — a large, monolithic Django application. Backend code lives in
`zerver/`. This file is the orientation a senior contributor gives a new hire: how the
codebase is organized and the conventions every backend change must follow. It does **not**
tell you what to build — that's in your task.

## Build / test commands

Backend tests run with Zulip's own runner (not bare pytest):

```bash
./tools/test-backend                                 # full backend suite
./tools/test-backend zerver.tests.test_realm         # one module (dotted path)
./tools/test-backend zerver.tests.test_realm.RealmAPITest.test_foo   # one test
```

Schema changes require a migration: `./manage.py makemigrations zerver`. Lint: `./tools/lint`.

## How the backend is layered

A user request flows through distinct layers, and most features touch several of them:

- **`zerver/models/`** — Django ORM models. Realm-level configuration (org-wide settings)
  lives on the `Realm` model. Many settings are enum-backed with a dedicated property type.
- **`zerver/actions/`** — the *only* place that mutates state. Views never write to the DB
  directly; they call an `actions/` function. Crucially, actions are also where state-change
  **events** are emitted to connected clients. A setting change that doesn't go through the
  right action won't propagate.
- **`zerver/views/`** — HTTP endpoints. They validate request parameters (using
  `REQ`/typed validators), then delegate to an action. Realm settings are updated via
  `PATCH /json/realm` (`zerver/views/realm.py`).
- **`zerver/lib/`** — shared business logic and the **event/state system**:
  - `lib/events.py` assembles the initial state payload (`do_events_register`) that a client
    receives on load. Any new realm setting must be added here or clients never see it.
  - `lib/event_schema.py` / `lib/event_types.py` validate and type the events sent over the
    wire. New event fields must be registered in both, or `test_events.py` will fail.
- **`zerver/migrations/`** — sequentially numbered Django migrations. New schema lands here.
- **`zerver/openapi/zulip.yaml`** — the REST API spec; API-visible settings are documented here.
- **`zerver/tests/`** — backend tests. Zulip requires tests for feature PRs.

## The invariant that catches people: "a feature touches everything"

Zulip's defining trait is that a single org-level setting is **cross-cutting**. To add or
change one correctly you typically must, consistently across layers:

1. Add/modify the field on the model (+ a migration).
2. Make the mutation flow through an `actions/` function that also sends a realm-update event.
3. Register the field in the event system — `lib/events.py` (initial state),
   `lib/event_schema.py` and `lib/event_types.py` (event validation/typing).
4. Accept and validate it in the `views/` endpoint, returning a clear error on bad input.
5. Enforce the setting wherever the behavior it governs is implemented (often in `actions/`
   or `lib/`).
6. Update tests across every layer touched, and the OpenAPI spec if it's API-visible.

The classic failure mode is editing the model and the view but **forgetting the event-system
registration** — the setting saves, but doesn't sync to clients, and `test_events.py` fails.
When adding a setting, find an existing setting of the same shape and trace how it flows through
all of the above; mirror that path exactly.

## Conventions

- Migrations are sequentially numbered; add yours after the highest existing number.
- Enum-backed settings follow an established pattern — look at a comparable existing setting
  rather than inventing structure.
- Tests use existing factory/helper patterns; match the nearest similar test.
