FROM node:20-slim

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy application code
COPY . .

# Expose ports for Vite (5173) and Express API (3001)
EXPOSE 5173
EXPOSE 3001

# Start both using the start script (concurrently)
CMD ["npm", "start", "--", "--host"]
