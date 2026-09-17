FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
WORKDIR /service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home service && mkdir -p /state /bootstrap && chown service:service /state /bootstrap
COPY interactive_gateway ./interactive_gateway
USER 10001:10001
CMD ["uvicorn", "interactive_gateway.main:app", "--host", "0.0.0.0", "--port", "8030", "--no-access-log", "--timeout-graceful-shutdown", "20", "--ws-max-size", "65536", "--ws-max-queue", "4", "--ws-per-message-deflate", "false"]
