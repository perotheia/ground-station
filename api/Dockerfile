# gs-api — the ground-station operator backend (FastAPI + uvicorn).
# Reuses ../fleet/fleet.py (the Mender client) mounted at /gs/fleet; the package
# itself ships here. Runs on the Mender network so it reaches the GW + gs-minio.
FROM python:3.12-slim

WORKDIR /gs/api
COPY pyproject.toml ./
COPY gs_api ./gs_api
RUN pip install --no-cache-dir .

# fleet.py is mounted at /gs/fleet by compose (clients.py loads it from there);
# the package resolves it relative to this dir's parent (parents[2] == /gs).
EXPOSE 8080
CMD ["uvicorn", "gs_api.app:app", "--host", "0.0.0.0", "--port", "8080"]
