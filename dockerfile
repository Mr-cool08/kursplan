# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# Install dependencies separately so Docker can reuse this layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV FLASK_APP=app.py \
	PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "-m", "flask", "run", "--host", "0.0.0.0", "--port", "8000"]