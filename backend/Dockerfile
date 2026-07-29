FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY utily.py .

EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/health')" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8001", "--workers", "3", "--threads", "2", "--timeout", "60", "utily:app"]
