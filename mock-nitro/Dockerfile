FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn[standard] pyyaml
COPY mock_nitro.py .
COPY mock-config.yml .
EXPOSE 9080
CMD ["python", "mock_nitro.py"]
