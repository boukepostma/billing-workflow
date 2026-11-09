# ADR-0003: Credentials

Status: accepted (confirmed by owner 2026-11-09)

## Decision
All secrets are **workspace secrets** created in Mistral Studio (console.mistral.ai/secrets) and bound to the managed deployment as environment variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `EBOEKHOUDEN_API_TOKEN`. The worker reads them from its environment; `config/billing.yaml` only holds the env var *names*. Secret values never appear in chat, git, or workflow history (activities resolve env vars locally; the config model passed through workflow state contains only names).

The mailbox polling uses the outlook connector with a workspace-level credential (`run_as="deployment"`), because scheduled runs have no user identity.

## Alternatives
- Custom HTTP connector with a workspace credential — viable but heavier; not needed.
- User-scope connector credentials — impossible for unattended scheduled runs.
