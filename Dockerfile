# home-lab-pxe — PXE/iPXE control plane and admin UI.
#
# Build:  docker build -t home-lab-pxe .
# Run:    docker compose up --build
#
# This image is never pushed by CI smoke jobs.

FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ARG VERSION=0.1.1

LABEL org.opencontainers.image.title="home-lab-pxe" \
      org.opencontainers.image.description="Docker PXE/iPXE server with web console, cloud-init, and Cloudbase-Init" \
      org.opencontainers.image.version="${VERSION}"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    PXE_HTTP_PORT=8080 \
    PXE_HTTPS_PORT=8443 \
    PXE_TFTP_ROOT=/var/lib/pxe/tftp \
    PXE_IMAGE_ROOT=/var/lib/pxe/images \
    PXE_DATA_DIR=/var/lib/pxe/data \
    PXE_SSL_DIR=/var/lib/pxe/ssl \
    PXE_DATABASE_URL=sqlite:////var/lib/pxe/data/pxe.db

# Pinned official wimboot (BIOS + 64-bit UEFI). Build fails if the checksum does not match.
ENV WIMBOOT_URL=https://github.com/ipxe/wimboot/releases/download/v2.8.0/wimboot \
    WIMBOOT_SHA256=74d4bf3d09386ccbbe907d9db59030f8cd8c88f7b4ccb799d386f31def11b3fe

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
        samba \
        smbclient \
        dbus \
        nfs-ganesha \
        nfs-ganesha-vfs \
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
    && (apt-get install -y --no-install-recommends 7zip || apt-get install -y --no-install-recommends p7zip-full) \
    && (command -v 7z >/dev/null || ln -sf "$(command -v 7zz || command -v 7za)" /usr/local/bin/7z) \
    && (apt-get install -y --no-install-recommends libssl3t64 || apt-get install -y --no-install-recommends libssl3) \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && useradd --uid 10002 --gid 10001 --system --no-create-home --shell /usr/sbin/nologin pxemedia \
    && mkdir -p /var/lib/pxe/tftp /var/lib/pxe/images /var/lib/pxe/images/smb /var/lib/pxe/images/nfs /var/lib/pxe/data /var/lib/pxe/ssl \
        /usr/share/home-lab-pxe /var/log/samba /var/log/ganesha /run/samba /run/rpcbind /run/dbus /var/run/ganesha /var/lib/nfs/ganesha

# Best-effort official iPXE binaries. Always leave non-empty TFTP files so a
# read-only rootfs (CI hardened smoke) can start even when the downloads 404.
RUN set -e; \
    mkdir -p /var/lib/pxe/tftp; \
    curl -fsSL -o /var/lib/pxe/tftp/undionly.kpxe https://boot.ipxe.org/undionly.kpxe || true; \
    curl -fsSL -o /var/lib/pxe/tftp/ipxe.efi https://boot.ipxe.org/ipxe.efi || true; \
    curl -fsSL -o /var/lib/pxe/tftp/snponly.efi https://boot.ipxe.org/snponly.efi || true; \
    for f in undionly.kpxe ipxe.efi snponly.efi; do \
      if [ ! -s "/var/lib/pxe/tftp/$f" ]; then \
        printf 'ipxe-stub\n' > "/var/lib/pxe/tftp/$f"; \
      fi; \
    done; \
    curl -fsSL -o /usr/share/home-lab-pxe/wimboot "$WIMBOOT_URL"; \
    echo "$WIMBOOT_SHA256  /usr/share/home-lab-pxe/wimboot" | sha256sum -c -; \
    cp /usr/share/home-lab-pxe/wimboot /var/lib/pxe/tftp/wimboot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') is None else 1)"

COPY VERSION ./VERSION
COPY config/smb.conf /etc/samba/smb.conf
COPY config/ganesha.conf /etc/ganesha/ganesha.conf
COPY src ./src
COPY scripts ./scripts
RUN sed -i 's/\r$//' ./scripts/entrypoint.sh ./scripts/run-web.sh ./scripts/pxe_smoke.py \
    && chmod +x ./scripts/entrypoint.sh ./scripts/run-web.sh \
    && chown -R app:app /app /var/lib/pxe \
    && chmod 644 /etc/samba/smb.conf /etc/ganesha/ganesha.conf

EXPOSE 8080 8443 67/udp 69/udp 2049/tcp 2049/udp 20048/tcp 20048/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"]

ENTRYPOINT ["./scripts/entrypoint.sh"]
