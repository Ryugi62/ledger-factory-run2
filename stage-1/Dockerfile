FROM node:22-alpine
ENV NODE_ENV=production UV_THREADPOOL_SIZE=4 PORT=8080
WORKDIR /app
COPY src/ ./src/
USER node
EXPOSE 8080
CMD ["node", "src/server.js"]
