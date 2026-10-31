FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/Epiphany-Leave/jm-bot" \
      org.opencontainers.image.description="Standalone OneBot JM to PDF bot"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY jm_bot ./jm_bot
CMD ["python", "-m", "jm_bot"]
