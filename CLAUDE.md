# CLAUDE.md

## Core Rule

Small, clear, safe steps—grounded in real docs. TDD: red→green→refactor.

## Principles

- Clarity over cleverness; simplicity over complexity.
- Minimal, reversible diffs; remove deps unless justified.
- TDD by default.

## Safety & Policy

- HITL for any side effect: Plan → Approve → Apply.
- Never auto-execute model output; use URL/command allowlists (prompt-injection, insecure output).
- Keep secrets in env/secret manager; never in code, logs, or prompts.

## Tools (MCP)

- Use MCP tools; transports: stdio and Streamable HTTP/SSE; bridge remote via mcp-remote/proxy.
- Each tool exposes a clear JSON schema; unsafe ops require HITL.

## Knowledge & Libraries

- Fetch and cite official docs before tests/code (e.g., context7 for library docs).
- Call resolve-library-id, then get-library-docs to verify APIs.
- If uncertain, pause and request clarification.
- Project-specific info: see @README.md, @docs/ directory.

## Workflow

1. **Plan**: Short numbered plan with impacted files, risks, rollback; request approval for side effects.
2. **Read**: Read all relevant code, tests, and configs before changing anything.
3. **Verify**: Confirm APIs/assumptions against official docs; re-check interfaces and syntax.
4. **Implement**: Keep scope tight and diffs small; write small, single-purpose modules.
5. **Test & Docs**: Write a failing test, implement minimum to pass, refactor safely; add ≥1 test, update docs.
6. **Verify Your Work**: Always run lint/typecheck on completion. Give yourself a way to verify the result (tests, browser, CLI output).
7. **Reflect**: Capture root cause and adjacent risks; note follow-ups to prevent regressions.

## Code Style & Limits

- Files ≤ 300 LOC; cohesive, single-purpose modules.
- Configuration centralized; no magic numbers (tests use the same config).
- Simplicity: Implement exactly what's requested—nothing extra.
- Comments: File header (where/what/why); explain non-obvious logic; record rationale/assumptions/trade-offs.
- Language: Comments and CLI output in Japanese.
- UI/UX tweaks are maintainer-owned; don't revert the latest design unless instructed.

## Testing

- Unit tests first; E2E only for critical paths; deterministic, isolated (AAA), and fast.
- Keep the suite green on every change.

## Reliability

- Retries: exponential backoff with jitter and caps; set per-call timeouts.
- Do not retry non-idempotent actions without idempotency keys or approval; use circuit breakers for flaky upstreams.

## Code & PRs

- Prefer small atomic PRs (ideal <~50 LOC; soft cap ~200) for faster, higher-quality reviews.

## Quick Checklist

Plan → Approve → Read → Verify → Test-first → Implement → Verify-work → Refactor → Docs → Reflect
