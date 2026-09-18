# Deterministic shell/workdir fixture; use only in the separate runtime gate.
FROM alpine:3.22.1@sha256:4bcff63911fcb4448bd4fdacec207030997caf25e9bea4045fa6c8c44de311d1
RUN adduser -D -u 1000 fixture && mkdir /workspace && chown 1000:1000 /workspace
USER 1000:1000
WORKDIR /workspace
RUN printf 'workload-fixture-v1\n' > /workspace/identity.txt
ENTRYPOINT ["/bin/sh"]
