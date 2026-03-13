# Frontend Maintenance Guide

This guide covers how to run, extend, debug, and safely evolve the frontend without adding unnecessary architectural churn.

## Scope

Use this document for day-2 work:

- local development
- build and container workflow
- adding routes or features
- debugging frontend/backend integration
- understanding current gaps that affect maintenance

Read `frontend_overview.md` first if you need the structural map.

## Local Development Workflow

### Standard scripts

Defined in `frontend/package.json`:

- `npm run dev`
- `npm run build`
- `npm run preview`

Expected behavior:

- Vite dev server runs on `5173`
- build runs TypeScript project build first, then Vite production build

### Docker-based workflow

`docker-compose.yml` defines a `frontend` service that:

- builds from `frontend/Dockerfile`
- exposes port `5173`
- mounts `./frontend` into `/app`
- keeps `/app/node_modules` as a container-only volume

`vite.config.ts` enables:

- `host: '0.0.0.0'`
- `port: 5173`
- polling file watch for Docker-mounted development environments

Use Docker when local Node tooling is not installed or when you want parity with the rest of the stack.

## Runtime Dependencies

The frontend depends on the backend being reachable at `http://localhost:8000`.

Current implications:

- local browser + local backend works
- Docker frontend calling a host backend may need extra networking care
- staging/production URLs cannot be configured without code changes

There are currently no frontend `.env` files and no `import.meta.env` usage.

## How to Make Common Changes

### Add a new page

1. Create a page component in `src/pages/`.
2. Add a route in `src/routes/AppRouter.tsx`.
3. Add navigation in `src/components/Layout.tsx` only if it should be globally reachable.
4. Prefer composing existing `ui` primitives before creating new visual patterns.

Use a new page when the feature needs its own URL and navigation state. Use a child component when the feature is only part of the dashboard.

### Add a new backend call

Follow the existing pattern unless you are intentionally refactoring:

1. call `axiosClient`
2. keep request orchestration in the page or feature component
3. keep `loading`, `error`, and result state local
4. surface backend `detail` messages when present

Do not introduce a separate service layer for a single endpoint. Create one only when multiple components start sharing the same API contract or state transitions.

### Add a new dashboard widget

The dashboard already acts as a composition root. Prefer this split:

- `DashboardPage.tsx`: fetch parent data, wire callbacks, own cross-widget state
- `components/*`: implement a focused widget
- `components/ui/*`: extract only the purely presentational pieces that are reusable elsewhere

If the widget needs polling or optimistic updates, keep the state transition logic in the dashboard unless multiple pages need it.

### Add real route protection

Treat this as a cross-cutting change, not just a router tweak.

Recommended approach:

1. create a protected-route wrapper
2. redirect unauthenticated users before page content renders
3. decide whether auth hydration needs an explicit `isReady` state
4. update dashboard and detail pages to handle unauthenticated entry cleanly

Do not rely on hiding nav links as the only protection mechanism.

### Improve forms and validation

Current forms use local state and manual checks. If forms expand beyond the current auth and upload cases, add a form abstraction only when it reduces duplication across multiple screens.

The best first upgrade would be shared auth form logic, not a global form framework everywhere.

## Debugging Guide

### Symptom: dashboard does not behave correctly when opened directly

Check:

- whether `localStorage` contains `access_token` and `user`
- whether `AuthContext` hydrated successfully
- whether the route should have been protected before render

The dashboard currently assumes auth context exists for normal operation.

### Symptom: API calls work in one environment and fail in another

Check:

- whether the backend is actually on `http://localhost:8000`
- whether the browser can reach that origin from the current runtime
- whether Docker or remote hosting changed the expected hostname

The first place to inspect is `src/api/axiosClient.ts`.

### Symptom: upload succeeds but video never becomes ready

Check the full chain:

1. `POST /videos/upload` returned `task_id` and `video_id`
2. dashboard inserted the optimistic row
3. `VideoProcessingTracker` is polling `/videos/tasks/{taskId}`
4. backend worker is running
5. Celery task status is changing beyond `PENDING` or `STARTED`

The frontend only polls. It does not reconcile with a later full refetch after completion, so any backend-side status or title corrections will not appear automatically.

### Symptom: search returns nothing

Check:

- user is authenticated and has an `ownerId`
- query is non-empty
- processed videos actually exist for that owner
- backend search index exists and has completed processing

The frontend search card is thin; empty results often mean backend state is not ready yet.

### Symptom: layout appears duplicated on the video detail route

Inspect `App.tsx` and `VideoDetailPage.tsx` together. The app shell is already mounted globally, and the detail page currently mounts another `Layout` locally.

## Important Current Gaps

These are not all blockers, but they matter when planning future work.

### 1. No frontend tests

There is currently no automated verification for:

- route rendering
- auth redirects
- upload polling
- search result rendering
- transcript detail loading

If the team adds tests, prioritize:

1. auth flow
2. dashboard load/upload/search
3. detail page fetch states

### 2. No environment-based frontend config

This is the main operational limitation. A small `VITE_API_BASE_URL` addition would remove the need to hard-code backend hosts.

### 3. Async patterns are duplicated

Each page or feature component manages its own:

- loading flag
- error extraction
- Axios call
- success branch

This is still manageable, but it is the first area likely to become noisy as features expand.

### 4. Auth and authorization are not aligned

The client stores and sends JWTs, but the backend docs still describe development routes as public. Before adding role-based UI or permissions, align the backend enforcement model first.

### 5. Some UI patterns bypass the shared primitives

The design system is small but present. Expand it deliberately; otherwise the app will drift into two styling systems:

- `components/ui/*` primitives
- page-local one-off markup

## Recommended Rules for Future Changes

- keep pages responsible for orchestration until at least two places need the same behavior
- extract primitives for repeatable visual patterns, not one-off layouts
- prefer explicit route guarding over conditional rendering inside pages
- add environment-based config before supporting more than one runtime target
- keep backend contracts close to usage sites unless multiple views share them
- document only durable patterns, not temporary JSX details

## Suggested Verification Checklist

Use this checklist after any meaningful frontend change.

### Auth

- login persists user and token
- logout clears both and updates nav
- direct navigation to authenticated pages behaves intentionally

### Dashboard

- video list loads for the current user
- upload creates an optimistic row
- processing state resolves to ready or failed
- search results link to the correct detail page

### Detail

- metadata and transcript load together
- loading, empty, and error states still render correctly
- app shell renders only once

### Visual consistency

- new UI uses existing tokens from `tailwind.config.cjs`
- buttons, cards, and inputs still match the rest of the app
- mobile layout still works for public pages and dashboard cards

## Recommended Next Improvements

If the team wants the highest return on small changes, do these in order:

1. add `VITE_API_BASE_URL`
2. add protected routing and unauthenticated fallbacks
3. remove duplicate shell usage in `VideoDetailPage.tsx`
4. introduce a minimal frontend test setup
5. refactor repeated async/error handling only after the app grows further
