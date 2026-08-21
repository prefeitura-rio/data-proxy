# Style Guide

Conventions for working in this repository. Follow these before inventing new patterns.

## Engineering Principles

- Search and read the codebase before writing anything. Do not assume a convention; verify it against source.
- Deletion beats addition. Prefer removing code, fields, or config over adding a new layer around a problem.
- Baby steps. Keep diffs small and reviewable; do not bundle unrelated changes.
- Never patch symptoms. Find and fix the root cause, even if it takes longer.
- Prefer idiomatic tooling for each ecosystem (Python, Helm, SQL) over custom scripting.

## Python

- Discriminated unions over `isinstance`/`hasattr` duck typing. Model alternatives as tagged Pydantic models (`Literal` discriminator field) and `match`/`case` on the tag.
- Exhaustive `match`/`case`: enumerate every case explicitly. Do not fall back to a wildcard `case _` when the case set is closed and known.
- No leading-underscore function names as a style choice. Only prefix a parameter with `_` when it is genuinely unused by the calling convention and never passed by keyword anywhere in the codebase. If a parameter is called by keyword at any real call site (e.g. `job_config=`, `approximate=`), keep its name and, if a linter flags it as unused, suppress via `ignore_names` instead of renaming.
- Prefer `asyncer.asyncify` to wrap blocking calls over hand-rolled thread/executor code.
- Avoid dynamic `**kwargs` passthroughs and bare `lambda`s where a named function or an explicit parameter list documents intent better.
- Group related functions by module/domain, not by kind (don't split a single feature's helpers across unrelated files).
- Within a file scoped entirely to one domain, prefer a short, unqualified name (`table`) over a prefixed one (`bq_table`) -- but check every function in the file for name collisions first.
- Avoid domain-specific field/column names in generic, reusable schemas (e.g. an access-control table should use `unit_type`/`unit_id`, not a hardcoded customer's terminology like `cras`).
- A JWT carries identity, not authorization state. Never encode "what this user can access" as a token claim; look it up from data at request time.
- No outbound HTTP calls from request-time authorization logic (e.g. RLS checks). Authorization must resolve from data already in the database.
- Generalize hardcoded identifiers (customer names, specific org/table names) into configuration the first time you touch code that has them.
- Reserved SQL keywords used as identifiers (e.g. a role literally named `user`) must be double-quoted in raw SQL or shell heredocs. `psycopg.sql.Identifier()` already handles this automatically -- prefer it over hand-quoting.

## Configuration & Schema Design

- One universal mechanism beats several special-cased ones. If two features solve the same problem for different customers/tables, unify them into one generic, data-driven mechanism instead of keeping both.
- A single, explicit source of truth for each fact. If two config locations can express the same thing (e.g. "which schemas exist"), delete one -- do not synchronize two by hand.
- Static Helm `.Files.Get` SQL files cannot be parameterized. If a Helm value needs to reach the SQL, convert the file to a `{{ define }}` template.

## Concurrency & State (sync pipeline specifics)

- Redis/streams are the source of truth for pipeline state, not in-memory counters.
- Every background loop has exactly one termination trigger. Do not add a second, redundant shutdown path.
- State keys are explicit and typed, not stringly-composed ad hoc.
- Prefer `XTRIM` with an explicit policy over `DEL` when trimming a stream -- destructive resets lose in-flight consumer state.
- Set `diagnose=False` for loggers in production paths; do not leak local variable values into logs by default.
- Prefer `logger.exception` inside `except` blocks over `logger.error` plus manual traceback formatting.

## Testing & Verification

Before considering any change complete, run and confirm clean output from:

```bash
uv run pytest --cov=dp --cov-report=term-missing   # 100% coverage, all green
uv run ruff check
uv run ruff format --check
uv run basedpyright src/ tests/
uv run complexipy src/ tests/
uv run vulture src/ tests/
uv run sqlfluff lint src/dp/sql/
helm lint helm/ -f helm/ci/test-values.yaml
helm lint helm/ -f helm/ci/test-values-ha.yaml
helm unittest helm/
```

- Docstrings are brief: one line stating what the function does, not how.
- When a doc or comment claims something about runtime behavior (scaling, retries, diagrams), verify it against the actual source/Helm templates before writing it down. Do not describe behavior from memory or assumption.
- When explaining _why_ a technical choice was made, ground the explanation in verified facts (changelogs, source, issue trackers), not assumption.

## Documentation

- Simplified Technical English (ASD-STE100): short sentences, one idea per sentence, consistent terminology, active voice.
- One clause per sentence. Split any sentence joined by "and", "but", "so", "which", or a colon into two sentences.
- Avoid vague qualifiers and hedges: "too", "plus", "back to", "either way", "instead of", "coarse", "regardless". State the fact plainly instead.
- Each doc file answers exactly one question. Do not split one topic across two files, and do not create a stub file whose scope overlaps another file's.
- Use generic, domain-neutral examples (e.g. "school", "student") instead of repo-specific customer names when illustrating a concept.
- For conceptual/onboarding explanations, prefer a narrative walkthrough (a hypothetical user, a numbered sequence of steps) over a bare command checklist. Save checklists for purely mechanical how-tos.
- Keep list formatting consistent within a document: `**Bold term**: description.` (colon, not period, after the bold term).
- Verify registry paths, image names, and org/repo references against actual CI workflow config or `git remote -v` -- never assume them.
- Prefer capturing a real, executed example (command + actual output) over a fabricated one when documenting an API or CLI.

## Git

- Follow Conventional Commits, matching this repository's history: `type: imperative summary` (`feat`, `fix`, `refactor`, `docs`, `chore`, `test`), lowercase, no trailing period.
- Never commit or push without explicit user approval. Stage and summarize the change, then wait.
- Keep commits scoped to one logical change matching the diff, not the whole working session.

## Planning

- For any change with real blast radius (new dependency, cross-file rename, schema/model restructuring, infra template changes), propose a plan and get approval before implementing.
- Pure documentation clarifications, typo fixes, and single-file wording corrections do not need a plan -- just make them.
