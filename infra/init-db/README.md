# infra/init-db/

Owner: shared infra. Phase: Tier 0.

Empty on purpose. `infra/docker-compose.yml` mounts `../docs/schema.sql`
straight into Postgres's `/docker-entrypoint-initdb.d/` so the schema
loads on first boot without being copied/duplicated here. If a second
init step is ever needed (e.g. seeding `rate_card`), drop a
`002_seed.sql` in this directory — Postgres runs everything in
`docker-entrypoint-initdb.d/` in filename order, once, only when the
`pgdata` volume is empty.
