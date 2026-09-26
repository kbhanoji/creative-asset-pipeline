FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY cap ./cap
COPY agents ./agents
RUN pip install --no-cache-dir ".[gcp,router]"
# Only the selected customer's config goes into the image
ARG CUSTOMER_DIR
COPY ${CUSTOMER_DIR} ./${CUSTOMER_DIR}
COPY services/lineage_router/main.py ./main.py
ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
