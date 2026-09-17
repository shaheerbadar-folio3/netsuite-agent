FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
COPY agent ./agent
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 agent && mkdir -p /app/data && chown agent:agent /app/data
USER agent
EXPOSE 8765
CMD ["uvicorn", "agent.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8765", "--no-access-log"]
