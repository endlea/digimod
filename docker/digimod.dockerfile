# syntax=docker/dockerfile:1.7
#
# The build service in a container: the page, mod storage, builds and their checks.
#
# The emulator is not in here. It talks to CoreAudio and CoreMIDI and is built as a macOS library, so it runs
# only natively (`make setup && make run`). In the container the page says so and hides its buttons.

ARG PYTHON_VERSION=3.12

# ---- the firmware tool ---------------------------------------------------------------------------------------
FROM debian:bookworm-slim AS tool
RUN apt-get update -qq \
    && apt-get install -yqq --no-install-recommends gcc libc6-dev make \
    && rm -rf /var/lib/apt/lists/*
COPY third_party/elektron-firmware-tool/ /src/
RUN make -C /src CFLAGS="-O2" && strip /src/elektron-firmware-tool

# ---- the service ---------------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm

# The server is plain stdlib, so there is nothing to pip install.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DIGIMOD_TOOL=/usr/local/bin/elektron-firmware-tool

COPY --from=tool /src/elektron-firmware-tool /usr/local/bin/

WORKDIR /app
COPY server/ server/
COPY web/ web/
COPY mods/ mods/
COPY tests/ tests/
# The emulator's address profiles: the page uses them to say which firmwares the emulator knows.
COPY third_party/dt2/core/machines/digitakt2/profiles.inc.c third_party/dt2/core/machines/digitakt2/

# firmware/ (official OS files) and data/ (builds, caches) are bind-mounted from the host.
RUN useradd --uid 1000 --create-home digimod \
    && mkdir -p firmware data \
    && chown digimod:digimod firmware data
USER digimod

EXPOSE 8700
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8700/api/mods', timeout=2)"

CMD ["python", "server/app.py", "--host", "0.0.0.0", "--port", "8700"]
