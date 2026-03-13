# Frontend Overview

This document explains how the frontend is structured today, what business flows it implements, and where to make changes safely.

## Scope

The frontend lives in `frontend/` and is a small single-page React application for:

- landing and marketing (`/`)
- authentication (`/login`, `/register`)
- video library management (`/dashboard`)
- transcript inspection (`/videos/:id`)
- semantic search across uploaded videos

The frontend is intentionally thin. Most business logic still lives in the backend; the UI mainly handles navigation, form state, API calls, optimistic updates, and task polling.

## Stack

- React 18
- TypeScript with strict mode enabled
- Vite for dev/build
- React Router for client-side routing
- Axios for HTTP
- Tailwind CSS for styling

Key config files:

- `frontend/package.json`
- `frontend/vite.config.ts`
- `frontend/tsconfig.app.json`
- `frontend/tailwind.config.cjs`
- `frontend/postcss.config.cjs`

## Application Shape

```text
index.html
  -> src/main.tsx
    -> BrowserRouter
      -> AuthProvider
        -> App
          -> Layout
            -> AppRouter
              -> Page component
```

This is a flat app shell with one global context and no nested route layouts.

## Folder Map

```text
frontend/
  src/
    api/
      axiosClient.ts         # shared Axios instance + auth header injection
    components/
      Layout.tsx             # app shell: nav, content width, footer
      SemanticSearchCard.tsx # search form + results list
      VideoItem.tsx          # video list row
      VideoProcessingTracker.tsx
      VideoUploadCard.tsx
      ui/                    # reusable presentational primitives
    context/
      AuthContext.tsx        # only global client state
    pages/
      HomePage.tsx
      LoginPage.tsx
      RegisterPage.tsx
      DashboardPage.tsx
      VideoDetailPage.tsx
    routes/
      AppRouter.tsx          # flat route table
    App.tsx                  # wraps router in Layout
    main.tsx                 # bootstrap
    index.css                # Tailwind base styles
```

## Architectural Boundaries

### 1. Bootstrap and global providers

`src/main.tsx` mounts the app with:

- `BrowserRouter`
- `AuthProvider`
- global Tailwind stylesheet

There are no other providers for data fetching, forms, theming, or state.

### 2. Routing

Routing is fully declared in `src/routes/AppRouter.tsx`:

- `/` -> `HomePage`
- `/login` -> `LoginPage`
- `/register` -> `RegisterPage`
- `/dashboard` -> `DashboardPage`
- `/videos/:id` -> `VideoDetailPage`

The route tree is flat. There are no protected routes, route loaders, route-level error boundaries, or nested layouts.

### 3. State management

The app uses local component state by default.

Global state is limited to `AuthContext`, which stores:

- `user`
- `token`
- `login()`
- `logout()`

Auth state is hydrated from `localStorage` on mount and written back on login/logout. Everything else is page-local state.

### 4. Data access

All HTTP calls use `src/api/axiosClient.ts`.

Current behavior:

- fixed base URL: `http://localhost:8000`
- default JSON content type
- request interceptor adds `Authorization: Bearer <token>` when `access_token` exists in `localStorage`

There is no response interceptor, retry logic, token refresh, or typed API abstraction layer. Pages and feature components call Axios directly.

### 5. Reuse strategy

Reuse is split in two layers:

- `components/ui/*`: reusable visual primitives such as `Card`, `Button`, `Input`, `Badge`, `Skeleton`, `Spinner`
- `components/*`: small feature-level building blocks used mostly by the dashboard

Pages still own orchestration. They fetch data, hold loading/error state, and wire child components together.

## Route and Feature Map

### Home page

`HomePage.tsx` is a static marketing page. It does not fetch data.

Use it when changing:

- product messaging
- top-level CTA paths
- visual language for public pages

### Login and register

`LoginPage.tsx` and `RegisterPage.tsx` implement the full client auth flow:

1. user submits credentials
2. page posts to `/auth/login` or `/auth/register`
3. response returns `access_token` and `user`
4. page calls `login()` from `AuthContext`
5. user is redirected to the next page

Client-side validation is minimal:

- login: required browser validation only
- register: password confirmation match only

There is no shared auth form hook or schema-based validation layer.

### Dashboard

`DashboardPage.tsx` is the main authenticated working surface and the most important page in the app.

It combines three flows:

1. video listing
   - fetches `/videos?owner_id=<user.id>`
   - renders empty, loading, or list states
2. upload and processing
   - `VideoUploadCard` uploads a file to `/videos/upload`
   - dashboard adds an optimistic `processing` row immediately
   - `VideoProcessingTracker` polls `/videos/tasks/{taskId}` every 3 seconds
   - status is updated to `ready` or `failed`
3. semantic search
   - `SemanticSearchCard` calls `/videos/search`
   - results link into the video detail page

This page is the closest thing to a feature composition root.

### Video detail

`VideoDetailPage.tsx` loads:

- `/videos/:id`
- `/videos/:id/transcript`

It shows metadata plus a read-only transcript list. There are no seek controls, playback controls, editing tools, or transcript search inside the page.

## Important End-to-End Flows

### Auth flow

```text
Login/Register form submit
  -> POST /auth/*
  -> receive access_token + user
  -> AuthContext.login()
  -> localStorage write
  -> nav updates
  -> redirect
```

### Upload flow

```text
Select file + title
  -> POST /videos/upload (multipart/form-data)
  -> receive task_id + video_id
  -> optimistic video row added to dashboard
  -> poll /videos/tasks/{task_id}
  -> mark video ready/failed
```

### Search flow

```text
Enter natural-language query
  -> GET /videos/search?q=...&owner_id=...&k=10
  -> render ranked results
  -> click result
  -> open /videos/:id
```

## Styling System

The project uses Tailwind directly in JSX and keeps design tokens in `tailwind.config.cjs`.

Shared tokens currently include:

- `brand.*` color ramp for primary actions and accents
- `app.bg`, `app.surface`, `app.border`, `app.muted` for app chrome
- custom radii and shadows for cards and surfaces

Global base styles are intentionally light and live in `src/index.css`.

### Styling conventions already present

- public pages use large marketing gradients and softer decorative backgrounds
- app surfaces use white cards on `bg-app-bg`
- most interactive surfaces use `rounded-xl` or `rounded-2xl`
- primitives prefer semantic variants over one-off styling when the same pattern repeats

### Styling inconsistency to be aware of

Most pages use the shared UI primitives, but `VideoDetailPage.tsx` uses one-off card and spinner markup instead of the shared `Card` and `Spinner` components. Keep that in mind before expanding the UI kit or restyling the app shell.

## Conventions Inferred From the Code

- keep files small and colocated by role, not by domain package
- keep business orchestration in page components
- prefer explicit local state over additional abstractions
- use `axiosClient` directly instead of wrapper services
- use reusable UI primitives when a pattern appears on multiple pages
- favor optimistic UI only when backend state can be polled cheaply

## Risks and Maintenance Hotspots

### Route protection is only cosmetic

The nav changes based on auth state, but routes themselves are not protected. `/dashboard` and `/videos/:id` are still routable directly from the browser.

### Dashboard assumes a user exists

`DashboardPage` fetches videos only when `user?.id` exists. If the page is opened without auth state, the main loading state does not resolve cleanly into an explicit unauthenticated state. Fixing route protection and dashboard fallback behavior should be treated as the same piece of work.

### Backend URL is hard-coded

The frontend always calls `http://localhost:8000`. There is no `VITE_*` environment-based configuration, so changing environments currently requires source edits or proxying.

### Auth is only partially integrated

The client always sends a bearer token when it has one, but the backend documentation still describes development routes as publicly accessible. Do not assume frontend auth state equals backend authorization.

### Video detail page breaks the normal shell pattern

`App.tsx` already wraps all routes in `Layout`, but `VideoDetailPage.tsx` wraps itself in `Layout` again. Any shell-level changes should account for this inconsistency first.

### No frontend test suite

There are no frontend `.test.*` or `.spec.*` files in the repository. Changes to routing, auth, upload behavior, and search UX currently rely on manual verification.

## Where To Change Things

- add or change a route: `src/routes/AppRouter.tsx`
- change nav, shell width, footer, logged-in links: `src/components/Layout.tsx`
- change auth persistence or login/logout semantics: `src/context/AuthContext.tsx`
- change base API behavior: `src/api/axiosClient.ts`
- change dashboard behavior: `src/pages/DashboardPage.tsx` plus dashboard child components
- change shared app styling: `src/components/ui/*`, `src/index.css`, `tailwind.config.cjs`

## When to Introduce More Structure

The current structure is acceptable because the app is still small. Add new layers only when repeated code makes them worthwhile.

Likely next abstractions when the app grows:

- protected-route wrapper
- typed API module per backend resource
- shared async-state helpers or data-fetching library
- reusable auth form pieces
- frontend tests for auth, dashboard, and upload polling
