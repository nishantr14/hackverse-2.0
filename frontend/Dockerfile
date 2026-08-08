# Engineering Spend Intelligence — frontend
# Owner: Livana (frontend lane) / shared infra. Phase: Tier 0.
FROM node:20-slim

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
