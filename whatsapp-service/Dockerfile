# Playwright focal image ships with all browser automation system dependencies
# pre-installed (Chromium, fonts, shared libs), avoiding apt-get failures on slim images.
FROM mcr.microsoft.com/playwright:v1.40.0-focal

# Install Node.js 22.x (the focal image bundles Node 18 by default)
RUN apt-get update && apt-get install -y curl ca-certificates --no-install-recommends \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Use the Chromium that Playwright already installed; skip Puppeteer's own download
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/ms-playwright/chromium-1091/chrome-linux/chrome
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
