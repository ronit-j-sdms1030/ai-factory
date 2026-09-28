# Canonical frontend

Static UI for the governed SDLC (seven gates). Phase 1–5 compatibility lives in `../new/phase1`.

```bash
PHASE1_API_URL=http://127.0.0.1:8787 npm run dev
```

Demo login (`PHASE1_DEV_MODE=1`, password `password123`):

| Email | Role | Gate |
|---|---|---|
| requester@client.example | Requester (also UAT) | originator, 6 |
| po@client.example | Product Owner | 1 |
| bo@client.example | Business Owner | 2 |
| ctl@client.example | Client Tech Lead | 2 |
| architect@client.example | Architect | 3 |
| ux@client.example | UI/UX | 3 |
| ba@client.example | Business Analyst | 3 |
| techlead@client.example | Tech Lead | 4 |
| dev-lead@client.example | Stream Lead (Development) | 4 |
| ai-lead@client.example | Stream Lead (AI) | 4 |
| qa-lead@client.example | Stream Lead (QA) | 4 |
| senior@client.example | Senior Engineer | 5 |
| release@client.example | Release Manager | 7 |

MD / CEO / VP labels from the old org chart are gone. Settings → Agent configuration picks an OpenRouter model per agent.
