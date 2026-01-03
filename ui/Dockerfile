FROM node:20-bookworm-slim

# Install build dependencies for better-sqlite3
RUN apt-get update && apt-get install -y \
    python3 \
    make \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy application code
COPY . .

# Build the Svelte app
RUN npm run build

# Expose backend port
EXPOSE 3001

# Start the backend server
CMD ["node", "server.js"]
