FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py core.py sample_data.py ./
COPY templates/ templates/

ENV SECRET_KEY=change-me-in-prod PORT=5000
EXPOSE 5000
CMD ["python", "app.py"]
