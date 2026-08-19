# 通用镜像:Fly.io / Railway / Koyeb / HF Spaces / 自建服务器均可用
FROM python:3.12-slim
WORKDIR /app
# 依赖单独成层(版本约束与 pyproject.toml [project] dependencies 保持同步):
# 代码改动不再触发 ortools 等大件重装,多架构 QEMU 构建能省好几分钟。
RUN pip install --no-cache-dir "ortools>=9.8" "fastapi>=0.110" \
    "uvicorn[standard]>=0.29" "pydantic>=2.6"
COPY pyproject.toml README.md LICENSE ./
COPY app ./app
COPY static ./static
RUN pip install --no-cache-dir --no-deps .
EXPOSE 8000
ENV PORT=8000
CMD uvicorn app.scheduling.api:app --host 0.0.0.0 --port ${PORT}
