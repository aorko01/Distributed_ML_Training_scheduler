# The compute studio

The User UI adopts `personal_website`'s dimmed retro palette and Inter / JetBrains Mono typography: warm charcoal surfaces, copper actions, olive success states, and restrained borders. The dashboard and authentication pages feature an original SVG compute topology, with no network or image dependency.

## Styling

- `src/index.css`: shared palette, fonts, application shell, pages, controls, responsive layouts, and reduced motion support.
- `src/App.css`: authentication layouts.
- `src/capacity.css`: machine cards, capacity controls, and modal geometry.
- `src/workspace.css`: the existing editor geometry with the studio palette. Monaco and xterm themes live in their existing adapter/components.
- `src/components/PageHeading.tsx`, `Brand.tsx`, `ComputeArtwork.tsx`, and `AuthLayout.tsx`: reusable presentation.
- `public/fonts`: locally served Latin variable fonts with their SIL Open Font Licenses. System fallbacks remain available.

The service layer, API URLs, authentication storage, request bodies, polling, log streaming, runtime operations, and download behavior are preserved. Active runs remain on Overview, and completed/failed jobs remain in Job history.

## Local preview and verification

Run from `UI/User`:

```sh
npm ci
npm run dev -- --host 127.0.0.1
npm run build
npm test
npm run test:interactive
npm run lint
```

On Node 26, run component tests with `NODE_OPTIONS=--no-experimental-webstorage npm run test:interactive` so jsdom owns browser storage globals. This is a test-environment workaround; browser storage and authentication behavior are unchanged.

The UI was visually checked in Chrome on desktop and mobile with mocked API responses, including navigation, search, training submission payloads, profile updates, and authentication. These checks do not substitute for a live cluster integration run.
