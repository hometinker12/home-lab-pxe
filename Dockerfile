# home-lab-pxe — PXE/iPXE control plane and admin UI.
#
# Build:  docker build -t home-lab-pxe .
# Run:    docker compose up --build
#
# This image is never pushed by CI smoke jobs.

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG VERSION=0.1.0

LABEL org.opencontainers.image.title="home-lab-pxe" \
      org.opencontainers.image.description="Docker PXE/iPXE server with web console, cloud-init, and Cloudbase-Init" \
      org.opencontainers.image.version="${VERSION}"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PXE_HTTP_PORT=8080 \
    PXE_TFTP_ROOT=/var/lib/pxe/tftp \
    PXE_IMAGE_ROOT=/var/lib/pxe/images \
    PXE_DATABASE_URL=sqlite:////var/lib/pxe/data/pxe.db

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dnsmasq \
        gosu \
        gzip \
        libpcre2-8-0 \
        libsqlite3-0 \
        openssl \
        perl-base \
        tftp-hpa \
        bsdutils \
        libblkid1 \
        liblastlog2-2 \
        libmount1 \
        libsmartcols1 \
        libuuid1 \
        login \
        mount \
        util-linux \
    && (apt-get install -y --no-install-recommends libssl3t64 || apt-get install -y --no-install-recommends libssl3) \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /var/lib/pxe/tftp /var/lib/pxe/images /var/lib/pxe/data

# Best-effort official iPXE binaries; stubs are created at runtime if these fail.
RUN curl -fsSL -o /var/lib/pxe/tftp/undionly.kpxe https://boot.ipxe.org/undionly.kpxe \
    && curl -fsSL -o /var/lib/pxe/tftp/ipxe.efi https://boot.ipxe.org/ipxe.efi \
    && curl -fsSL -o /var/lib/pxe/tftp/snponly.efi https://boot.ipxe.org/snponly.efi \
    || true

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') is None else 1)"

COPY VERSION ./VERSION
COPY src ./src
COPY scripts ./scripts
RUN sed -i 's/\r$//' ./scripts/entrypoint.sh ./scripts/pxe_smoke.py \
    && chmod +x ./scripts/entrypoint.sh \
    && chown -R app:app /app /var/lib/pxe

EXPOSE 8080 67/udp 69/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"]

ENTRYPOINT ["./scripts/entrypoint.sh"]
