# Fleet Maintenance

This is my implementation of the Fleet Maintenance take-home challenge: a REST API for managing a
fleet of vehicles and their maintenance history, plus a frontend that uses it.

- [Original Challenge](CHALLENGE.md)
- [System Design Notes](docs/SYSTEM_DESIGN.md)

## Implementation overview

- **Backend**: Django + Django REST Framework, SQLite.
- **Frontend**: Next.js 16 + React 19, with Material UI, axios and React Query.
- **Authentication**: JWT (the optional bonus) protects every application endpoint.
- **Catalogs**: vehicle makes and models are normalized into `VehicleMake` and `VehicleModel`
  resources. Maintenance types are their own resource too.
- **Advanced endpoints**: office summary, vehicle search, vehicle detail with full history,
  maintenance history, office assignment, mechanic workload, maintenance due, duplicate check and
  an educational active-fleet transfer operation.
- **Query performance**: joins and prefetches on every read path, `EXISTS` and aggregate queries
  for search and reports, and tests that pin query counts.
- **API docs**: OpenAPI schema and Swagger UI through `drf-spectacular`.
- **Checks**: backend and frontend tests, lint, type check, migration and schema checks, all run
  in GitHub Actions.

## Project structure

- `backend/`: Django project (`server`) and the `fleet` app (models, serializers, views,
  selectors, seed command and tests).
- `frontend/`: Next.js 16 + React 19 client for the API.
- `docs/`: design notes ([SYSTEM_DESIGN.md](docs/SYSTEM_DESIGN.md)).
- `CHALLENGE.md`: the original challenge specification.

## Running the backend

Requires Python 3.12. The database is SQLite, so no other services are needed.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_fleet          # optional demo data, safe to rerun
python manage.py createsuperuser     # user for the JWT token
python manage.py runserver
```

API docs (Swagger): http://localhost:8000/api/v1/docs/ — OpenAPI schema: http://localhost:8000/api/v1/schema/

Use `localhost` in the browser. `ALLOWED_HOSTS` is empty in development, so a `0.0.0.0` host is rejected.

## Authentication

Every application endpoint requires a Bearer JWT. Only the docs, the schema and the token
endpoints are public. The access token lasts 1 hour and the refresh token lasts 1 day.

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "<your-password>"}'
# -> {"refresh": "...", "access": "..."}

curl -s http://localhost:8000/api/v1/vehicles/ -H 'Authorization: Bearer <access>'
```

In Swagger, click **Authorize** and paste the access token. When the access token expires, send
`{"refresh": "<refresh>"}` to `POST /api/v1/auth/token/refresh/` to get a new one.

## Running the frontend

Requires Node.js 20.19+ or 22.13+. Start the backend first.

```bash
cd frontend
npm install
# NEXT_PUBLIC_API_BASE_URL defaults to http://localhost:8000/api
npm run dev
```

Visit `http://localhost:3000` and sign in with the user from `createsuperuser`.

## Tests and quality checks

Backend:

```bash
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
python manage.py spectacular --validate --fail-on-warn --file /dev/null
```

Frontend:

```bash
cd frontend
npm run lint
npx --no-install tsc --noEmit
npm test
npm run build
```

GitHub Actions runs the same backend and frontend checks on pull requests to `main` and on pushes
to `main`.

## API

All application endpoints are under `/api/v1/` and return JSON. Lists are paginated with `?page=`
(10 per page) unless noted.

| Feature | Endpoint |
|---|---|
| CRUD | `offices/`, `vehicle-makes/`, `vehicle-models/`, `vehicles/`, `mechanics/`, `maintenance-types/`, `maintenance-records/` (`GET`/`POST` on the collection, `GET`/`PUT`/`PATCH`/`DELETE` on `{id}/`) |
| Office summary | `GET offices/summary/` (plain list) |
| Vehicle search | `GET vehicles/?office=&active=&make=&model=&maintained_from=&maintained_to=&mechanic_certification=` (`office`, `make` and `model` are ids) |
| Vehicle details | `GET vehicles/{id}/` (office + full history + mechanic) |
| Maintenance history | `GET vehicles/{id}/maintenance-records/` (newest first) |
| Assign vehicle | `PUT vehicles/{id}/office/` with `{"office_id": 2}` |
| Mechanic workload | `GET mechanics/workload/` (plain list, busiest first) |
| Vehicles needing maintenance | `GET vehicles/maintenance-due/` |
| Duplicate vehicle check | `GET vehicles/duplicate-check/?vin=&license_plate=` |
| Educational active-fleet transfer | `POST operations/transfer-active-vehicles/` with `{"source_office_id": 1, "target_office_id": 2}`; moves every active vehicle between offices atomically and returns the moved count |

Relations are nested objects on read and `*_id` fields on write (`office_id`, `model_id`,
`make_id`, `vehicle_id`, `mechanic_id`, `type_id`).

Errors: `400` with field messages, `401` without a token, `404` for unknown ids, and `409` when you
delete an office, vehicle make, vehicle model, mechanic or maintenance type that other records
still reference.

## Vehicle make/model contract

- `VehicleMake` is canonical reference data. Make names are unique.
- `VehicleModel` belongs to a `VehicleMake`. Model names are unique within a make, so two makes
  can each have a model with the same name.
- A vehicle stores only its model. Its make is always `model.make`, so a vehicle cannot have a
  make that does not match its model.
- Vehicle writes send `model_id` only. Make or model names are not accepted, and neither is
  `make_id`.
- Vehicle reads return both as objects:
  `"make": {"id": 2, "name": "Ford"}, "model": {"id": 2, "name": "Transit"}`.
- Vehicle search takes ids for `make` and `model` (`?make=2`, `?model=2`, `?make=2&model=2`). An
  unknown id is a `400`, and so is a model that does not belong to the given make
  (`?make=1&model=2` returns `{"model": ["Model 2 does not belong to make 1."]}`), instead of a
  silent empty result.

## Seed data

`seed_fleet` creates a small, deterministic dataset where each record covers one scenario:
3 offices, 3 vehicle makes with 4 models (Ram ProMaster, Ford Transit, Ford F-150, Chevrolet
Express), 5 vehicles, 3 mechanics (one inactive), 4 maintenance types and 3 maintenance records.

It includes a vehicle that was never maintained, one with recent maintenance from two mechanics,
one whose last maintenance was more than 365 days ago, and one active and one inactive vehicle
sharing a license plate. Dates are relative to today. Reruns update the seeded fixtures in place
and preserve unrelated data, including manually added maintenance records.

The seed is small and scenario-focused instead of large random data (no Faker), so it is easy to
check by hand.

## Assumptions and tradeoffs

### Assumptions

- Maintenance type is its own resource with CRUD, so type names stay consistent. Vehicle makes
  and models are catalogs for the same reason. City, year and other scalar fields stay plain
  values.
- Certification numbers are unique mechanic identifiers. The database enforces it and the API
  returns a `400` for a duplicate.
- Maintenance cost must be `>= 0`; zero is allowed.
- Office summary: "last 12 months" goes from the same calendar date one year ago to today,
  inclusive (Feb 29 maps to Feb 28). All maintenance on a vehicle counts toward its *current*
  office, including inactive vehicles and work done before a move, because assignment history is
  not stored.
- Mechanic workload: "current year" means Jan 1 to today.
- Reports and maintenance-due ignore records dated after today. Exactly 365 days ago is not due
  yet, and never-maintained vehicles come first.
- Search: `office`, `make` and `model` are ids, and an unknown id returns 400.
  `mechanic_certification` is an exact match, so an unknown certification returns no vehicles.
  The date range and certification must match the same maintenance record. Empty parameters are
  ignored.
- Duplicate check: a VIN conflicts with any vehicle, a plate only with active vehicles. HTTP inputs
  are trimmed by DRF, then compared case-sensitively against stored values.
- Deleting a vehicle deletes its maintenance history. Set `active=false` to retire a vehicle
  instead.
- Dates use the server time zone (UTC).

### Tradeoffs

- CRUD stays in plain DRF viewsets, and report and filter queries live in `fleet/selectors.py`.
  Services remain exceptional and focused: `fleet/services.py` is used only for complex business
  writes that need orchestration, atomicity, or meaningful domain rules. The educational
  `POST operations/transfer-active-vehicles/` operation is the example: its APIView validates the
  HTTP request, while the service atomically moves all active vehicles between two distinct offices.
- Query behavior is treated as correctness: read paths join or prefetch their relations, search
  and reports run as single queries, and tests pin the query counts. See
  [System Design Notes](docs/SYSTEM_DESIGN.md) for details.
- Vehicle details return the full history in a bounded number of queries (two), but the response
  size is not bounded. The history endpoint is paginated for clients that want pages.
- SQLite keeps the take-home easy to run. PostgreSQL would be the production choice.
- The frontend keeps JWTs in `localStorage`. This is simple, but an XSS bug could leak them. In
  production I would use httpOnly cookies set by the server.
- Office assignment history is not stored. Assigning a vehicle only records the new office.
- The active-plate rule is a partial unique constraint in the database, plus a serializer check
  for a clear 400 message. Two concurrent requests can both pass validation, and then the database
  rejects the second one. That error is not translated into a custom API response.

## Frontend

The frontend covers:

- login with JWT, with one shared token refresh and retry on `401`;
- vehicle search with filters and pagination kept in the URL, so searches can be shared and
  restored with back/forward;
- vehicle create, edit and delete (the delete dialog warns that history is deleted too);
- dependent make/model selects backed by the catalogs;
- vehicle detail with the complete maintenance history;
- adding a maintenance record from the vehicle page;
- the maintenance-due list;
- loading, empty and error states, with the API's `400` messages shown next to each field.

Offices, makes, models, mechanics and maintenance types are read-only in the UI and managed
through the API (Swagger). React Query holds server data, the URL holds search and page, and
components hold form drafts.

## Production concerns intentionally omitted

- PostgreSQL and deployment.
- Secrets and settings from the environment (`SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`).
- Restrictive CORS (it currently allows all origins).
- Refresh token rotation and blacklisting.
- Rate limiting.
- Monitoring.
- Turning concurrent unique-constraint errors into `400` responses.
- Stronger input normalization where it makes sense.
- Office assignment history.
