FROM python:3.13-slim

# System deps for Playwright Chromium + build tools for native packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 libgbm1 \
    libpango-1.0-0 libcairo2 libasound2 libxshmfence1 \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

# Copy project
COPY . .

# Data volume for persistent SQLite DB and logs
VOLUME ["/app/data"]

# Use /app/data/jobs.db if DB_PATH not overridden
ENV DB_PATH=/app/data/jobs.db
ENV LOG_LEVEL=INFO

ENTRYPOINT ["python", "main.py"]
CMD ["run"]
