# 通用镜像:Fly.io / Railway / Koyeb / HF Spaces / 自建服务器均可用
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY static ./static
RUN pip install --no-cache-dir .
EXPOSE 8000
ENV PORT=8000
CMD uvicorn app.scheduling.api:app --host 0.0.0.0 --port ${PORT}
