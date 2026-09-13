FROM oven/bun:1

WORKDIR /app
COPY package.json ./
RUN bun install --production

COPY tsconfig.json ./
COPY src ./src

EXPOSE 8080
CMD ["bun", "run", "src/index.ts"]
