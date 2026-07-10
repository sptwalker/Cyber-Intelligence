FROM python:3.12-slim AS builder

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY pyproject.toml README.md ./
COPY yuqing ./yuqing
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels ".[all]"

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid appuser --create-home --shell /usr/sbin/nologin appuser \
    && chown appuser:appuser /app

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels "cyber-intelligence-yuqing[all]" \
    && rm -rf /wheels

USER 1001:1001

EXPOSE 8080

CMD ["python", "-c", "from yuqing.dashboard import serve; serve(host='0.0.0.0', port=8080)"]
