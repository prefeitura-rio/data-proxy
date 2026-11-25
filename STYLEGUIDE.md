# Style Guide

These guidelines explain how to make engineering decisions in this repository. They describe intent and tradeoffs, rather than prescribe every code shape.

## How to use this guide

Read the surrounding code and understand its constraints before changing it. Follow useful existing patterns, and explain a deliberate departure when another approach serves the code better.

Use judgment when principles compete. Prefer the choice that makes behavior, ownership and failure easier to understand. Explain the tradeoff instead of adding a rule for each exception.

Security, data integrity and compatibility protect users. Treat those constraints differently from preferences about naming or layout. The [development guide](docs/dev/index.md) describes project procedures and checks.

Tool configuration owns formatting and static-analysis settings. A diagnostic is feedback, not permission to distort code. Propose a targeted configuration correction when a check conflicts with clear, correct code.

## Principles

- Understand the problem before choosing a mechanism. Fix the root cause rather than its visible symptom.
- Prefer deletion, native features and installed dependencies before adding abstractions or custom infrastructure.
- Keep changes cohesive and reviewable. Separate unrelated work and preserve behavior outside the intended change.
- Give each fact and responsibility a clear owner. Share authoritative definitions instead of maintaining competing copies.
- Keep decisions separate from effects where that improves reasoning and testing. Make mutable state, resource lifetimes and concurrency ownership explicit.
- Build abstractions around actual contracts and reuse. Avoid scaffolding for hypothetical callers or future implementations.

### Naming and comments

Choose names that describe an operation or domain concept. Use consistent terms, preserve distinctions that matter, and avoid filler that adds no meaning.

Choose name length for scope and context, not a fixed character count. A short receiver can be clear. A value that crosses several stages needs enough context to remain recognizable.

A constructor can use a name such as `makeState` or `makePlan`. A conversion can use `decodeClaims`. Judge the meaning rather than impose a prefix or suffix template.

Keep a helper when its name, contract or boundary improves understanding. A single caller alone neither requires inlining nor justifies extraction.

Comments explain intent, contracts and constraints beyond declarations and control flow. Keep useful inline explanations. Avoid narrating individual expressions or repeating declarations.

Respect language conventions and names that external interfaces require. Keep module interfaces deliberate and implementation details local to their owner.

### Safety and compatibility

- Keep credentials outside source, images, logs and exported artifacts. Require deliberate secret configuration and use identities with the permissions they need.
- Validate data where it crosses a trust boundary. Choose unknown-field handling deliberately for security and compatibility rather than making every schema open or closed.
- Distinguish identity from authorization. Enforce access policy at the authoritative boundary rather than trusting client claims as permission state.
- Parameterize SQL values and use dedicated quoting for identifiers. Keep elevated execution narrow and control its name-resolution context.
- Preserve isolation between users, projects and representations in shared caches. Plan changes to formats that old and new processes can read during deployment.
- Preserve applied migration history and published contracts. Introduce explicit migrations for incompatible data, interface or release changes.

### Errors and resources

Distinguish expected failures from programming errors. Give callers a useful recovery contract, preserve error identity, and add context where it helps locate the failed operation.

Keep native diagnostics when they already explain the failure. Avoid wrappers that only repeat an error, and avoid catches that conceal defects or turn failure into apparent success.

Keep resource ownership visible. Close or release resources at the boundary that owns them, and propagate cancellation and deadlines through I/O operations.

## Logs

Use logs to explain operational events and failures. Include stable identifiers that let an operator follow an operation without exposing sensitive values.

Choose severity for the event, not for emphasis. Distinguish normal lifecycle events, skipped work, recoverable problems and terminal failures.

Use the logging facilities of the component so context and output stay consistent. Preserve machine-output contracts when another tool consumes the output.

Keep external tool output intact when its format belongs to that tool. Parse it only when the integration has a defined need and contract.

See [metrics and logging](docs/operations/metrics.md) for deployed log formats and operational context.

## Tests

Test observable behavior at the smallest boundary that provides useful evidence. Test owned decisions and integration contracts rather than reproducing a dependency's implementation.

Cover meaningful normal, failure and boundary cases. Include defaults when they affect behavior or safety. Use descriptive cases and tables when they make the scenarios easier to compare.

Choose assertions that prove the contract. Avoid coupling tests to incidental layout, private storage or an entire registry when only one behavior matters.

Keep tests independent and deterministic. Control clocks, randomness and external boundaries. Use a real engine when its semantics are the behavior under test.

Wait for a condition that proves the current action completed. Avoid signals left by earlier actions and arbitrary delays. Use a deliberate observation window when absence is the behavior under test.

Fix flaky behavior at its cause and preserve a reproducing case. Never conceal a failed test by adding a retry.

The [test guide](docs/dev/tests.md) owns commands, fixtures, suite organization and service requirements.

## Python

Use types and explicit parameters to make data flow and alternatives understandable. Validate external data at boundaries, and use lightweight models for trusted internal data.

Keep domain logic near its owner. Choose functions or objects for the responsibilities they clarify, not for a prescribed flow pattern or line count.

Protect the event loop from blocking operations. Keep concurrent resource ownership and time conversions explicit.

Follow Python conventions for imports, privacy and documentation. The project configuration defines formatter, analyzer and test settings.

## Lua

Keep request decisions understandable without nginx state or network access where possible. Concentrate effects at explicit boundaries and inject them where isolation helps testing.

Make shared mutable state deliberate. Keep coupled helpers local, and share values or utilities when they represent a real common concept.

Use OpenResty facilities and installed libraries when they improve the implementation. Use a parser for structured syntax rather than a collection of fragile string operations.

Describe important module interfaces and table shapes so LuaLS can check their use. Use objects for meaningful state or lifecycle responsibilities rather than making every module a class.

Avoid blocking I/O in the request path. Preserve authorization and cache-isolation contracts during refactoring.

The [development guide](docs/dev/index.md) owns dependency and type-library setup. The [proxy architecture](docs/architecture/serving.md) describes request boundaries.

## Nushell

Use typed command contracts and structured pipelines to make transformations clear. Make ordering explicit for external side effects.

Use native file and parsing commands before adding wrappers. Handle missing values and deferred fallbacks deliberately, and preserve external command exit status.

Keep command descriptions concise and examples useful to callers. Execute deterministic examples in isolation. Never execute publishing, credential or database operations merely to test documentation.

Keep imports within the files that each deployment actually ships. Explain domain-specific SQL and orchestration contracts at their implementation boundary.

## Nix

Keep environments reproducible through pinned inputs and explicit dependencies. Make build inputs visible to Nix so the sandbox can track them.

Keep component ownership, configuration and resource allocation clear. Prefer native module, task and process features over another orchestration layer.

Use bindings when their names clarify intent. Prefer direct commands over generated wrappers when a wrapper adds no contract or behavior.

Keep development and test initialization consistent. Make intentional interactive differences explicit, and keep checks free from source-file mutations.

Document useful module and function contracts with native [Nix documentation conventions](https://github.com/NixOS/rfcs/blob/master/rfcs/0145-doc-strings.md). Avoid headings and type descriptions that merely restate a declaration.

The [environment guide](docs/dev/index.md) owns profile layout, task names, lifecycle dependencies and cache behavior.

## SQL

Use database constraints, transactions and appropriate types to protect data integrity. Prefer set-based operations that express the work directly.

Keep SQL composition auditable. Parameterize values, quote identifiers through dedicated APIs, and avoid duplicating quoting logic or building fragments that hide inputs.

Keep elevated functions narrow and give them a controlled search path. Describe mutation and volatility accurately so callers and the optimizer can rely on the contract.

Separate reusable domain logic from query-specific composition. Preserve applied migrations rather than changing history to match a new implementation.

The [SQL template guide](docs/sql.md) describes rendering and input contracts. [Database documentation](docs/database.md) and [authorization documentation](docs/security/authorization.md) describe storage and access boundaries.

## Go

Keep transport, domain and Terraform adaptation responsibilities separate. Make interfaces describe actual boundaries rather than speculative implementations.

Pass contexts through I/O calls and preserve useful error identity when adding operation context. Keep request-specific state outside long-lived objects.

Use idiomatic types, names and documentation. Keep names recognizable in their scope, and avoid changing an unrelated interface to satisfy a local naming diagnostic.

Use controlled HTTP and clock boundaries in tests. The [provider guide](docs/terraform.md) owns build, acceptance-test and release procedures.

## TypeScript

Validate runtime input before treating it as trusted typed data. Derive types from validation schemas where that prevents drift, and keep unsafe assertions narrow and explicit.

Keep credentials and authorization on the server. Separate data loading and effects from pure transformations with independent tests.

Use typed failure contracts when callers can recover, and preserve diagnostics for failures that prevent an operation. Follow framework entry-point conventions and keep module exports deliberate.

Let compiler and formatter configuration own mechanical syntax choices. Use comments for contracts and reasoning rather than to narrate expressions.

## Svelte

Use the framework's reactivity model. Derive values from their sources rather than synchronizing duplicate state, and clean up effects that interact with external systems.

Choose keys that preserve meaningful item identity. Extract components for cohesive responsibilities and useful reuse, not to satisfy a fixed script or template length.

Make forms accessible and resilient. Use shared interaction patterns for pending state, errors and destructive actions so the user can understand what happened.

Use shared visual primitives and semantic theme tokens. Keep presentation, routing and live-data behavior consistent with the surrounding console.

## Notebooks

Keep credentials outside notebook content and artifacts. Use scoped grants rather than expanding the notebook's access to deployment state.

Validate the origin and sender of messages and restrict the destination origin. Treat embedded and parent applications as distinct trust boundaries.

Respect the notebook framework's data-flow constraints. The [notebook guide](docs/notebooks.md) describes the current bridge and execution model.

## Documentation

Write for the reader's task. Use clear, focused prose, consistent terminology and realistic examples that expose no sensitive data.

Verify claims, commands and links against actual behavior. Give each fact an authoritative home, and link or include it elsewhere instead of maintaining copies.

Keep the path from a reader's question to the answer short. Separate explanation, procedures and reference material when they serve different needs.

Choose diagrams that answer a useful question. Keep labels readable, relationships clear and color meaningful without making color the only signal.

The [documentation workflow](docs/dev/index.md#write-the-docs) owns mdBook navigation, includes, diagram generation and publication checks. Prose-tool configuration owns mechanical editorial checks.

## Git

Keep commits cohesive and explain intent, consequences and migration needs. Review the actual diff rather than relying on file names or generated summaries.

Use the configured Conventional Commit conventions because release automation consumes that metadata. Let the release tools own generated versions and changelogs.

Follow the [repository workflow](https://github.com/prefeitura-rio/data-proxy/blob/main/AGENTS.md#workflow) for review and authorization. The [release guide](docs/dev/ci.md) owns tag formats, component scope and publication procedures.
