FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (gcc untuk compiling pip packages jika perlu)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the bot (Telegram + MCP server)
CMD ["python", "-u", "bot_telegram.py"]