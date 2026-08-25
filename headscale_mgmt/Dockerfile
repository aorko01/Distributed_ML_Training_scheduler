FROM python:3.11-slim

# Install the headscale CLI so the service can manage pre-auth keys locally.
# Override with --build-arg HEADSCALE_VERSION=<x.y.z> if needed.
ARG HEADSCALE_VERSION=0.23.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/headscale \
       "https://github.com/juanfont/headscale/releases/download/v${HEADSCALE_VERSION}/headscale_${HEADSCALE_VERSION}_linux_amd64" \
    && chmod +x /usr/local/bin/headscale \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8100

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8100"]
