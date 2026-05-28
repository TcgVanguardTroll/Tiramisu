# Engineering Principles

Distilled from: Effective Java (Bloch), Clean Code (Martin), A Philosophy of Software Design (Ousterhout), Designing Data-Intensive Applications (Kleppmann), Release It! (Nygard), Java Concurrency in Practice (Goetz).

## Code Design

### Complexity Management (Ousterhout)
- **Deep modules over shallow**: A module should hide significant complexity behind a simple interface. A class with 5 methods that each do one complex thing is better than 20 trivial methods that force callers to orchestrate.
- **Define errors out of existence**: Design APIs so invalid states are unrepresentable. Prefer enums over strings, typed IDs over raw strings, builders that enforce required fields.
- **Pull complexity downward**: If something is going to be complex, put it in the implementation — not in the interface. Callers should never need to understand internals.
- **Strategic vs tactical programming**: Don't just make it work — make it right. A 10% investment in design saves 100% in future maintenance. But don't over-design either — YAGNI still applies.

### Function Design (Clean Code + Effective Java)
- **Functions do one thing**: If you can extract a meaningful sub-function, the original was doing too much.
- **Max 3 parameters**: Beyond 3, use a parameter object or builder. Never boolean parameters that change behavior — use two methods instead.
- **Command-query separation**: A method either changes state OR returns a value, not both. Exception: atomic operations (compareAndSet, putIfAbsent).
- **Fail fast**: Validate inputs at the top of the method. Don't let invalid data propagate deep into logic.
- **Return early**: Prefer guard clauses over nested if-else. The happy path should be the least-indented code.

### Naming (Clean Code)
- **Names reveal intent**: `elapsedTimeInMs` not `t`. `isEligibleForProcessing` not `check`.
- **Avoid encodings**: No Hungarian notation, no `I` prefix on interfaces, no `m_` on fields.
- **Verb phrases for methods**: `createShipment`, `validateInput`, `findById`.
- **Noun phrases for classes**: `ShipmentValidator`, `MetadataRegistry`, `TextGenerator`.
- **Don't be cute**: `abort()` not `whack()`. `deleteItems()` not `holyHandGrenade()`.

### Abstraction (Effective Java + Ousterhout)
- **Prefer composition over inheritance**: Inheritance breaks encapsulation. Use delegation.
- **Design for extension or prohibit it**: Classes should be `final` unless explicitly designed for subclassing.
- **Minimize accessibility**: Everything private until proven otherwise. Package-private > protected > public.
- **Immutability by default**: Use `@Value`, `final` fields, unmodifiable collections. Mutable state is the root of most bugs.

## Java Specifics (Effective Java)

### Object Creation
- **Static factories over constructors**: `Metadata.of(...)` reads better than `new Metadata(...)` and can cache, return subtypes, or have descriptive names.
- **Builders for 4+ parameters**: Never telescoping constructors. Builder enforces required fields at compile time.
- **Avoid creating unnecessary objects**: Reuse immutable objects. Cache expensive computations. But never sacrifice clarity for micro-optimization.

### Enums and Types
- **Enums over int constants**: Type-safe, extensible, can have behavior.
- **Use EnumMap/EnumSet**: Array-backed, no hashing overhead.
- **Bounded wildcards**: `<? extends T>` for producers, `<? super T>` for consumers (PECS).

### Error Handling
- **Unchecked exceptions for programming errors**: NullPointerException, IllegalArgumentException.
- **Checked exceptions only for recoverable conditions**: And even then, prefer unchecked with clear documentation.
- **Never catch Exception or Throwable**: Catch the specific exception you can handle.
- **Include failure-capture information**: Exception messages should contain the values that caused the failure.

### Concurrency (Java Concurrency in Practice)
- **Shared mutable state is the enemy**: Eliminate it via immutability, confinement, or synchronization — in that preference order.
- **Prefer concurrent collections**: `ConcurrentHashMap` over `synchronizedMap`. `CopyOnWriteArrayList` for read-heavy lists.
- **Document thread safety**: Every class should state whether it's immutable, thread-safe, conditionally thread-safe, or not thread-safe.
- **Never call alien methods while holding a lock**: Alien = any method that can be overridden or that you don't control.

## Distributed Systems (DDIA + Release It!)

### Data Consistency
- **Exactly-once is a lie**: Design for at-least-once with idempotency. Use idempotency keys for all write operations.
- **Eventual consistency is the default**: DynamoDB, SQS, EventBridge — all eventually consistent. Code must handle stale reads.
- **Compensating transactions over distributed locks**: If step 3 of 5 fails, undo steps 1-2 rather than holding locks across services.

### Resilience Patterns
- **Timeouts on everything**: Every network call needs a connect timeout AND a read timeout. No exceptions.
- **Retry with exponential backoff + jitter**: Never retry immediately. Never retry without jitter (thundering herd).
- **Bulkheads**: Isolate failures. One slow downstream shouldn't exhaust all threads. Use separate thread pools per dependency.
- **Graceful degradation**: When a dependency is down, serve stale data or reduced functionality — don't fail entirely.
- **Circuit breakers only at service boundaries**: Not inside a single service's internal calls.

### Queue-Based Systems
- **Visibility timeout > processing time**: If processing takes 30s, visibility timeout should be 60s+.
- **Dead letter queues for everything**: Never silently drop messages.
- **Idempotent consumers**: Messages will be delivered more than once. Handle it.
- **Backpressure over unbounded queues**: If the consumer can't keep up, slow the producer — don't let the queue grow forever.

### DynamoDB Patterns
- **Single-table design for related entities**: Reduces round-trips. Use composite sort keys.
- **Avoid hot partitions**: Distribute writes across partition keys. Never use a monotonically increasing key.
- **Condition expressions for optimistic locking**: `attribute_not_exists(pk)` for creates, version numbers for updates.
- **TTL for ephemeral data**: Correlation tables, caches, session data — always set TTL.

## Testing Principles

### What to Test
- **Test behavior, not implementation**: Tests should break when behavior changes, not when internals are refactored.
- **One assertion per test**: Makes failures obvious. Multiple assertions hide which invariant broke.
- **Test the contract, not the code**: If the method promises to throw on null input, test that — don't test internal validation logic.

### What NOT to Test
- **Don't test the framework**: DI injection works. SDK works. Don't verify them.
- **Don't test trivial code**: Getters, setters, constructors with no logic, toString.
- **Don't test private methods**: If you need to test a private method, it should be extracted to its own class.

### Test Design
- **Arrange-Act-Assert**: Three sections, clearly separated. No logic in tests.
- **Test names describe the scenario**: `getMetadata_collectionNotFound_throwsNotFoundException`
- **Prefer real objects over mocks**: Mocks test interaction, not behavior. Use fakes/stubs for external dependencies only.
- **Tests are documentation**: A new team member should understand the system's behavior by reading tests alone.

## When NOT to Apply These

- **Prototypes and spikes**: Skip all of this. Make it work, throw it away.
- **Scripts and one-off tools**: 80% of these rules don't apply to a 50-line script.
- **Performance-critical hot paths**: Clarity sometimes yields to performance. Document why.
- **Existing codebase conventions**: Match the codebase, even if it violates these principles. Consistency > correctness in style.

## Agent Behavior Rules (Karpathy Principles)

These apply when the agent is WRITING code, not just reviewing it:

- **Think Before Coding**: State assumptions explicitly before implementing. If a request is ambiguous, present interpretations and ask — never guess. Surface inconsistencies and tradeoffs before writing a single line.
- **Simplicity First**: Write the minimum code that solves the stated problem. No unrequested abstractions, no speculative features, no "flexibility" that wasn't asked for. If the user asked for X, deliver X — not X + Y + Z "just in case."
- **Surgical Changes**: Touch ONLY code directly related to the request. No reformatting adjacent lines, no "while I'm here" cleanup, no renaming unrelated variables. Every changed line must trace to what was asked.
- **Goal-Driven Execution**: Convert vague instructions into verifiable success criteria before starting. "Fix the bug" → "write a test that reproduces it, then make it pass." "Add a feature" → "define the acceptance criteria, implement, verify."
