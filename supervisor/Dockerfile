FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 50051
HEALTHCHECK --interval=5s --timeout=5s --start-period=60s --retries=5 \
  CMD python healthcheck.py || exit 1
CMD ["python", "main.py"]
