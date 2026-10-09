# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

FROM base AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY opspilot ./opspilot
RUN pip install --prefix=/install .

FROM base AS runtime
RUN useradd --create-home --uid 10001 opspilot
WORKDIR /app
COPY --from=build /install /usr/local
COPY kb ./kb
COPY agent_templates ./agent_templates
COPY eval ./eval
ENV OPSPILOT_DATA_DIR=/data OPSPILOT_KB_DIR=/app/kb OPSPILOT_TEMPLATE_DIR=/app/agent_templates
RUN mkdir /data && chown opspilot /data
VOLUME /data
USER opspilot
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).status==200 else 1)"
CMD ["uvicorn", "opspilot.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
