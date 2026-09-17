FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
LABEL org.opencontainers.image.version="1.0.0" io.dml.protocol="terminal-stream-v1"
WORKDIR /service
COPY interactive_access ./interactive_access
USER 10001:10001
HEALTHCHECK --interval=10s --timeout=4s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9002/health/ready',timeout=3)"]
CMD ["python", "-m", "interactive_access.main"]
