# Isolated, minimal Python image. Runs untrusted code with no network access.
FROM python:3.11-slim

RUN useradd --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /sandbox

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY runner.py .

USER sandbox

ENTRYPOINT ["python", "runner.py"]
