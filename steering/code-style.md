# Code Style Preferences

Personal coding conventions across Java, Python, Rust, and TypeScript. These represent how I want AI agents and tools to write code on my behalf.

---

## Java

### Null Safety
- Return `Optional<T>` instead of null from methods
- Use `Optional.empty()` or `Optional.of()` — never write `return null`
- Don't add redundant null checks when contracts already guarantee non-null

### Exception Handling
- Use unchecked (RuntimeException) wrappers instead of checked exceptions
- Catch blocks must add value — either add context or provide fallback. Never just log-and-rethrow.

### Logging
- Include context in every log statement (request ID, correlation ID, or business identifiers)
- Use structured logging with key-value pairs

### Final Keyword
- Mark method parameters `final`
- Mark local variables `final` when not reassigned
- Mark class fields `final` when initialized once
- Use `lombok.val` when type is obvious from context

### Lombok
- Use `@Value` for immutable objects — never `@Data`

### Design Patterns
- Separate nested builder/object construction into individual variables for debugging
- Order constructor parameter assignments to match field declaration order
- Use constructor injection — not field injection
- Use custom qualifier annotations instead of `@Named("string")`

### Code Organization
- Order methods: public → package-private → private
- Use narrowest visibility possible
- Utility classes get private constructor

### Code Style
- CamelCase acronyms: `Xml`, `Dao`, `HttpRequest` — not `XML`, `DAO`, `HTTPRequest`
- No unused imports
- Multi-param methods: each parameter on its own line

### Performance
- No `String.format()` on hot paths — use concatenation or parameterized logging
- Cache expensive computations (regex patterns, date formatters) as static finals
- Pre-size collections when size is known: `new ArrayList<>(expectedSize)`
- Avoid `Optional` in tight loops — use null checks in performance-critical iteration

### Testing
- Name: `methodName_condition_expectedResult`
- Annotate mock test classes with `@ExtendWith(MockitoExtension.class)`
- Group related scenarios with `@Nested`
- All test dependencies private
- Structure: Arrange-Act-Assert
- Parameterized providers: explicit `Stream.of(Arguments.of(...))`
- Don't test framework behavior, simple getters/setters, or DI wiring

---

## Python

### Naming
- `module_name`, `package_name`, `ClassName`, `method_name`, `GLOBAL_CONSTANT_NAME`
- Boolean variables: `is_`, `has_`, `can_`, `should_` prefixes
- Trailing underscore (`class_`, `type_`) when a name would collide with a keyword

#### Underscores for scope signaling *(PEP-8)*
- **Single leading underscore** (`_internal_var`): module/class-level "internal" marker. Blocks export via `from module import *`. The standard privacy convention.
- **Double leading underscore** (`__private_var`): triggers Python name mangling inside classes. Use only when designing for subclassing and you need collision-proof names. Rare.
- **Never invent dunder names** (`__your_name__`): reserved for the language.

### Type Annotations
- Always annotate public function signatures (args + return)
- Use `X | None` (3.10+) over `Optional[X]`
- Use `collections.abc.Sequence`/`Mapping` over `list`/`dict` in signatures

### Imports
- One import per line (except `typing` and `collections.abc`)
- Order: future → stdlib → third-party → local
- Full package paths — no relative imports
- Never `from module import *`

### Functions
- Max ~40 lines — split if longer
- No mutable default args (`def f(x=[])` is a bug)
- Prefer exceptions over returning None for errors
- Prefer early returns over deep nesting
- Limit arguments to 5 max — use dataclass/dict for more

### Docstrings
- `"""One-line summary."""` for simple functions
- Args/Returns/Raises sections for public APIs (Google style)
- Describe WHAT and WHY, not HOW

### Comprehensions
- Single `for` + single `if` max — no nested comprehensions
- If it needs a comment to explain, use a loop

### Error Handling
- Never bare `except:` — always catch specific exceptions
- Minimize code in `try` blocks
- Custom exceptions end in `Error`
- Fail fast: validate inputs at function entry

### Formatting
- 80 char line limit, 4-space indent
- Trailing commas in multi-line collections
- Use `ruff` for linting/formatting

### Testing
- `foo.py` → `test_foo.py`
- Names: `test_<function>_<scenario>_<expected>`
- One assertion per test when possible
- Use `pytest.fixture` over setUp/tearDown
- Mock at the boundary, not deep internals
- No logic in tests — no loops, no conditionals

### General
- Prefer composition over inheritance
- Constants at module top, never magic numbers inline
- Prefer `pathlib.Path` over `os.path`
- Use `dataclasses` or `@attrs` for data containers, not raw dicts
- Prefer `enum.Enum` over string constants for fixed sets

---

## Rust

### Naming
- `snake_case` for functions, methods, variables, modules
- `CamelCase` for types, traits, enums
- `SCREAMING_SNAKE_CASE` for constants/statics
- `as_*` (borrowed→borrowed), `to_*` (borrowed→owned), `into_*` (owned→owned)

### Error Handling
- Use `Result<T, E>` — never panic in library code
- `thiserror` for library error types, `anyhow` for application code
- `?` operator for propagation — no `.unwrap()` in production code
- Add context: `.context("failed to read config")` with anyhow

### Ownership & Borrowing
- Prefer `&str` over `String` in function params
- Prefer `&[T]` over `&Vec<T>`
- Use `Cow<'_, str>` when you might or might not need to allocate
- Clone explicitly — never hide allocations
- Use `Arc` only when shared ownership is truly needed

### Structs & Enums
- `#[derive(Debug, Clone, PartialEq)]` on data types
- Builder pattern for 3+ optional fields
- `#[non_exhaustive]` on public enums/structs for forward compatibility
- Newtype pattern for domain types: `struct UserId(u64)`

### Async
- Use `tokio` runtime
- Prefer `async fn` over returning `impl Future`
- Cancel-safety: document whether functions are cancel-safe
- Avoid holding locks across `.await` points
- Prefer channels (`mpsc`, `oneshot`) over shared mutable state

### Formatting
- `rustfmt` with default style
- `clippy` with `#![warn(clippy::all, clippy::pedantic)]`

### Modules
- One public type per file for complex types
- `pub(crate)` over `pub` when possible
- Keep module depth ≤ 3 levels

### Testing
- `#[cfg(test)] mod tests` in same file for unit tests
- Integration tests in `tests/` directory
- Property testing with `proptest` for complex invariants

### Dependencies
- Pin exact versions in `Cargo.toml`
- Audit with `cargo-audit` before adding new deps
- Minimize dependency tree

---

## TypeScript

### General
- Strict mode always (`"strict": true`)
- Prefer `readonly` on properties that shouldn't change
- Use discriminated unions over class hierarchies for state machines
- Prefer `interface` over `type` for object shapes
- No `any` — use `unknown` and narrow with type guards

### CDK/IaC Specific
- Constructs: props interface → construct class → exported outputs
- Tag all resources with Stage, Service, Owner
- Use `RemovalPolicy.RETAIN` for stateful resources (DDB, S3)

### Testing
- Jest with `ts-jest`
- Snapshot tests as safety net (not the only test)
- Mock at the client level, not individual operations

---

## Universal Preferences

- Constructor injection over field injection (all languages)
- Immutable by default (all languages)
- Iterative approach: start simple, refine progressively
- Working code over lengthy explanations
- ARM64 architecture for containerized deployments (cost savings)
- Prefer composition over inheritance (all languages)

### Comments explain WHY, not WHAT *(PEP-8)*

Assume readers can read code. Comments should add context the code can't:

```python
# GOOD — adds non-obvious intent
if retry_count == 4:  # 4 is the carrier's hard rate-limit threshold
    back_off()

# BAD — restates what the code already says
x, y = y, x  # swap x and y
```

If you find yourself describing *what* code does, the right fix is usually to make the code clearer, not to comment it.

### Forbidden single-letter names *(PEP-8)*

Never use these as variable names — they're visually ambiguous in most fonts:

- `l` (lowercase L) — looks like `1` or `I`
- `O` (uppercase O) — looks like `0`
- `I` (uppercase I) — looks like `1` or `l`

Use `i`, `j`, `k` for loop counters, `idx` for an index, or a descriptive name.
