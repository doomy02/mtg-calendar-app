FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CALENDAR_HOST=0.0.0.0 CALENDAR_PORT=8001 CALENDAR_DB=/data/calendar.sqlite3
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt tzdata \
    && mkdir /data && chown 10001:10001 /data
COPY app.py serve.py style.css availability.js places.js ./
USER 10001:10001
EXPOSE 8001
CMD ["python", "serve.py"]
