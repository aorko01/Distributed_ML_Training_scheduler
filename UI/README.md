# (Researcher)Ongoing Mock DistributeML User Dashboards (Copy)

This is a code bundle for (Researcher)Ongoing Mock DistributeML User Dashboards (Copy). The original project is available at https://www.figma.com/design/yqpdEd9w7HoDHMHvyohaDk/-Researcher-Ongoing-Mock-DistributeML-User-Dashboards--Copy-.

## Running the code

Run `npm i` to install the dependencies.

Run `npm run dev` to start the development server.

## Data mode

The dashboard runs with populated dummy data by default.

If a backend becomes available later, set `VITE_API_BASE_URL` to the API root and the app will use:

- `GET /dashboard`
- `POST /jobs`

If the variable is not set, the app stays in mock mode and remains fully usable.
