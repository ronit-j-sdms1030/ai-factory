# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm AS build

ARG COSIGN_VERSION=v2.5.3
ARG TARGETARCH
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && curl --fail --location --retry 3 \
      "https://github.com/sigstore/cosign/releases/download/${COSIGN_VERSION}/cosign-linux-${TARGETARCH}" \
      --output /usr/local/bin/cosign \
    && chmod 0755 /usr/local/bin/cosign \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --requirement requirements.lock

FROM python:3.12.11-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /app app \
    && mkdir -p /data /signing \
    && chown app:app /data /signing

COPY --from=build /opt/venv /opt/venv
COPY --from=build /usr/local/bin/cosign /usr/local/bin/cosign
COPY --chown=app:app attestation.py gate_engine.py intake_skill.py requirement.py run_ledger.py workflow_templates.py skill_registry.py stack_profiles.py department_routing.py substitutes.py ./
COPY --chown=app:app phase1 ./phase1
COPY --chown=app:app phase2 ./phase2
COPY --chown=app:app phase3 ./phase3
COPY --chown=app:app phase4 ./phase4
COPY --chown=app:app phase5 ./phase5
COPY --chown=app:app backstage ./backstage
COPY --chown=app:app skills ./skills

USER app
EXPOSE 8787
VOLUME ["/data"]

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=2)"]

CMD ["python", "-c", "from phase1.server import serve; serve(host='0.0.0.0', port=8787, root='/data')"]
