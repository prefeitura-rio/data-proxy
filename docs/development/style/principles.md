# Principles

Applies to: the whole repository.

These rules apply to every file. The pages for each area add more.

## Working rules

- **GEN-01** Read the code before you write code. [must, review]
- **GEN-02** Follow an existing pattern before you add a new one. [must, review]
- **GEN-03** Prefer deletion to a new abstraction or a new configuration layer. [should, review]
- **GEN-04** Make small, reviewable changes. Don't bundle unrelated work. [should, review]
- **GEN-05** Fix the root cause, not the symptom. [must, review]
- **GEN-06** Keep one source of truth for each fact. [must, review]
- **GEN-07** Use the idiomatic tool for each ecosystem. [should, review]

## State

- **GEN-08** Let DBOS and PostgreSQL own the orchestration state. Redis is a cache only. [must, review]
- **GEN-09** Keep DBOS workflows deterministic, and put I/O in steps. [must, review]
- **GEN-10** Don't re-implement DBOS checkpoints. [must, review]

See [Architecture](../../architecture/index.md) for the layers. See [Database Schema](../../database.md) for roles, row policies and privileged functions.

<!-- nav:start -->
---

[Previous: Style rules](index.md) | [Next: Logs](logs.md) | [Index](index.md) | [Home](../../index.md)
<!-- nav:end -->
