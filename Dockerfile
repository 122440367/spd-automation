FROM python:3.13-alpine

RUN apk add --no-cache tzdata \
    && mkdir -p /app /data/asniptest /var/lib/spd-automation

WORKDIR /app

COPY spd_automation.py docker_runner.py ./

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    SPD_CSV_DIR=/data/asniptest

ENTRYPOINT ["python3", "/app/docker_runner.py"]
CMD ["schedule"]
