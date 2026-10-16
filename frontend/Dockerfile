FROM node:22-alpine

WORKDIR /app

# Copy package files and install dependencies
COPY package*.json ./
RUN npm install

# Copy source code
COPY . .

EXPOSE 4321

# Binds Astro dev server to 0.0.0.0 so it's accessible externally
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
