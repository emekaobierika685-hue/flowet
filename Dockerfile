FROM python:3.10-slim

# Install Node.js (required for fp.js) and system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs \
    npm \
    curl \
    ca-certificates \
    build-essential \
    libjpeg-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway uses a dynamic PORT, but we default to 5000 just in case
ENV PORT=5000
ENV MODE=api

# Expose the port (Railway maps this automatically)
EXPOSE 5000

CMD ["python", "-u", "account_creator.py"]
