FROM node:22-slim

ENV NODE_ENV=production
# Node Memory limit slightly constrained to prevent memory leaks from taking down the container
ENV NODE_OPTIONS="--max_old_space_size=300"

WORKDIR /app

# Install dependencies (No Chromium needed anymore!)
COPY package*.json ./
RUN npm ci --only=production

# Copy source
COPY . .

CMD ["node", "index.js"]
