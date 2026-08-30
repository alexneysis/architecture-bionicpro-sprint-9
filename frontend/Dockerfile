# Build stage
FROM node:16-alpine

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .
RUN chown -R node:node /app
USER node

# Expose port
EXPOSE 3000

# Start nginx
CMD ["npm", "start"]