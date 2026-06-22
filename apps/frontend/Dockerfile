# Stage 1: Build static assets
FROM node:20-alpine AS builder

WORKDIR /app

# Copy dependency definition files
COPY package*.json ./

# Install dependencies reproducing the lockfile
RUN npm ci

# Copy source code and build project
COPY . .
RUN npm run build

# Stage 2: Serve using Nginx
FROM nginx:alpine

# Copy built files to Nginx public folder
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy Nginx template to automatic envsubst templates directory
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Expose port (Cloud Run defaults to 8080 but dynamic binding will be used)
EXPOSE 8080

# Nginx alpine image starts Nginx by default, substituting template variables
CMD ["nginx", "-g", "daemon off;"]
