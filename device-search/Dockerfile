FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/ /app/

ENV PORT=8080 HOME=/tmp PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
