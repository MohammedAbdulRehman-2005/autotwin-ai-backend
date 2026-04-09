# ── Stage 1: Base with Chromium system dependencies ──────────────────────────
FROM node:22-slim

# Install Chromium and all its shared library dependencies via apt
RUN apt-get update && apt-get install -y \
    chromium \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Tell Puppeteer/whatsapp-web.js to skip downloading its own Chrome
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Copy package files and install Node deps
COPY package*.json ./
RUN npm install --omit=dev

# Copy application source
COPY . .

EXPOSE 8080

CMD ["node", "index.js"]
