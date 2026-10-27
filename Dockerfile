FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl fontconfig \
    && curl -fsSL -o /tmp/designcraft.deb \
        https://github.com/storytold/designcraft/releases/download/v0.13.0/designcraft-0.13.0-linux-x86_64.deb \
    && apt-get install -y /tmp/designcraft.deb \
    && rm -rf /var/lib/apt/lists/* /tmp/designcraft.deb \
    && designcraft-cli --help >/dev/null

WORKDIR /app
COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
COPY assets ./assets
COPY templates ./templates
RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
EXPOSE 7980
CMD designcraft-brochure serve --host 0.0.0.0 --port ${PORT:-7980} --backend headless --jobs /data/jobs --saved-templates /data/templates
