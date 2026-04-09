FROM node:22-slim

# Install Chromium + all shared libs required for headless Chrome/Puppeteer
RUN apt-get update && apt-get install -y \
    chromium \
    ca-certificates \
    fonts-liberation \
    fonts-noto-color-emoji \
    libglib2.0-0 \
    libx11-6 \
    libxrandr2 \
    libxinerama1 \
    libxi6 \
    libxext6 \
    libxcursor1 \
    libxss1 \
    libxtst6 \
    libnss3 \
    libgconf-2-4 \
    libappindicator1 \
    libindicator7 \
    libgbm1 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcairo2 \
    libdbus-1-3 \
    libfontconfig1 \
    libfreetype6 \
    libxft2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libasound2 \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Skip Puppeteer's bundled Chrome download — use system Chromium instead
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium
ENV NODE_ENV=production

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm install --omit=dev

# Copy source
COPY . .

# Railway injects PORT at runtime — use it in index.js via process.env.PORT
EXPOSE 3001

CMD ["npm", "start"]
