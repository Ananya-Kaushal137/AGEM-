# One image for all four demo agents; docker-compose picks the agent and port.
# Build context is the repo root, so the wrapper can be copied in unchanged.
FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" pydantic

COPY agent_wrappers/python_wrapper.py .
COPY demo_agents/*.py .
