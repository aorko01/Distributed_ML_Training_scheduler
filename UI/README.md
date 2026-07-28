# (Researcher)Ongoing Mock DistributeML User Dashboards (Copy)

This is a code bundle for (Researcher)Ongoing Mock DistributeML User Dashboards (Copy). The original project is available at https://www.figma.com/design/yqpdEd9w7HoDHMHvyohaDk/-Researcher-Ongoing-Mock-DistributeML-User-Dashboards--Copy-.

## Running the code

Run `npm i` to install the dependencies.

Run `npm run dev` to start the development server.

## Scheduler API

The dashboard fetches live scheduler data from `http://localhost:8000` by default. Start the Scheduler API before launching the UI.

Set `VITE_API_BASE_URL` only when the API is hosted elsewhere. The UI uses:

- `GET /dashboard`
- `POST /jobs/submit_job`
