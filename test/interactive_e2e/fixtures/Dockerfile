FROM tailscale/tailscale:v1.102.3@sha256:8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680 AS tailscale
FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
WORKDIR /fixture
COPY test/interactive_e2e/requirements.txt requirements.txt
COPY Headscale_Management/requirements.txt management-requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r management-requirements.txt
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
COPY test/interactive_e2e /fixture
COPY Gateway/interactive_gateway /fixture/interactive_gateway
CMD ["python", "fixtures/controller.py"]
