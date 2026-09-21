# Backstage portal

Production shell is Backstage (architecture §9). This folder is the catalog feed
and an optional Compose profile.

Without Entra SSO and a built Backstage image, `docker compose --profile backstage up`
serves the catalog YAML on 127.0.0.1:7007. Point a real Backstage at
`GET /api/catalog` on the Phase 1 API for live Component entities per requirement.
