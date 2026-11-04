# Footy Pick ’Em — standing agent rules

These rules apply throughout this repository.

- GitHub `main` is the canonical production code baseline.
- Stage 2 work occurs on `beta/stage2-backfill` unless explicitly instructed otherwise.
- Local code inspection, editing, and local tests are permitted when requested.
- Never merge, push, deploy, redeploy, restart a service, change Railway configuration/variables/volumes, modify n8n, or perform any production write without explicit Steve authorization.
- Never execute a production database migration, backfill, SQL write, import, restore, cleanup, or schema change without explicit authorization.
- Production `/data/pickem.db` is protected.
- Read-only production access must also be explicitly scoped; read-only access is not blanket permission.
- Stop on unexpected production state rather than improvising.
- Never expose, print, commit, or copy secrets.
- Never put production database files, backups, credentials, tokens, or private evidence into GitHub.
- Preserve existing production IDs and historical values unless an explicitly reviewed migration says otherwise.
- Do not “repair” production data merely to make a migration or test pass.
- External side effects such as Gmail sends, X calls, OpenAI production calls, provider fetches, or task POSTs require explicit authorization unless part of an already-approved normal production workflow.
- Keep schema changes explicit and migration-driven; application startup must not silently migrate production.
- Before any production action, state exactly what will change and wait for explicit authorization.
