FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY cap ./cap
COPY agents ./agents
RUN pip install --no-cache-dir ".[gcp,agent]"
# Only the selected customer's config goes into the image
ARG CUSTOMER_DIR
COPY ${CUSTOMER_DIR} ./${CUSTOMER_DIR}
COPY services/prompt_agent/main.py ./main.py
ENV PORT=8080 GOOGLE_GENAI_USE_VERTEXAI=TRUE
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT}
