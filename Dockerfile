FROM python:3.12-slim

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /src

RUN useradd --uid 1000 --create-home app
USER 1000
WORKDIR /work
ENTRYPOINT ["config-to-vault"]
