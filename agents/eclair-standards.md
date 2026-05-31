# Éclair Coding Standards

These standards are non-negotiable. They apply to every code change Éclair makes — implementation tasks, bug fixes, scripts, PR feedback. No exceptions.

---

## 1. Core Principles (Karpathy)

### Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### Simplicity First
- Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

### Surgical Changes
- Touch only what you must. Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken. Match existing style.
- If you notice unrelated dead code, mention it — don't delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Every changed line should trace directly to the task requirement.

### Goal-Driven Execution
- Transform tasks into verifiable goals with success criteria.
- For multi-step tasks, state a brief plan with verification checks per step.
- Strong success criteria let you loop independently. Weak criteria require clarification — ask for it.

---

## 2. Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) format.

### Subject Line
```
<type>(<scope>): <imperative summary>
```

- **type**: `feat`, `fix`, `refactor`, `test`, `chore`, `docs`
- **scope**: feature area or component in lowercase (e.g. `auth`, `events`, `delivery`, `infra`). Not the package name.
- **summary**: imperative mood ("Add X", "Fix Y", "Remove Z"), max ~72 chars. Describe *what changed*, not the ticket.

Examples:
- `fix(auth): Return dataInStore from handler when no fields changed`
- `feat: Add MetadataKeySpec and consolidate key factory API`
- `refactor(auth): Remove unused quality metrics from schema`
- `test(auth): Add local integration tests`

### Body (required for non-trivial changes)

Wrap at 72 chars. Explain:
1. **What was wrong / what existed before** — the problem or prior state
2. **What this change does and why** — the fix/approach and reasoning
3. **Side effects or related cleanup** — if any

Don't repeat the diff. Explain the *why* that isn't obvious from the code.

### Footer
```
Ticket: <ticket-url>    (if applicable)
PR: <pr-url>
```

### Anti-patterns
- ❌ No type/scope, lists implementation details in subject
- ❌ Too vague, no type/scope
- ❌ Ticket ID in subject, "cleanup" says nothing
- ✅ `refactor(auth): Remove deprecated quality metrics from translator` — type, scope, clear intent

### PR Title
The PR title MUST also follow the `type(scope): summary` format.

### AI Footer
Every comment posted on PRs or tickets must end with:
```
— Tiramisu 🐶🍮 (AI assistant)
```

### PR Description Integrity
**NEVER modify the PR description when addressing reviewer feedback.** The description is the original change record — reviewers have already read it. Respond to feedback by:
1. Replying to the comment (via `gh pr review <n> --comment`)
2. Pushing a new revision with the fix
Never silently edit the description to incorporate feedback.

---

## 3. Unit Testing

**Every PR with business logic changes MUST include unit tests in the same PR.** No separate "add tests later" PRs.

- Cover all lines and all branches by default.
- Go beyond the obvious — think about edge cases, failure modes, boundary conditions.
- Tests are proportional to the change. A 1-line fix gets a targeted test. A new module gets comprehensive coverage.

---

## 4. Java Standards

### Code Quality & Safety
- **Null safety**: Use `Optional<T>` instead of returning null. Use `Optional.empty()` instead of `return null`. Skip null checks when `@Nonnull` or `@ParametersAreNonnullByDefault` is present.
- **Exceptions**: Avoid checked exceptions. Use unchecked wrapper pattern (RuntimeException subclass with message + cause). Flag catch blocks that only log and rethrow without adding value.
- **Logging**: Always include context (request ID, correlation ID, business identifiers). Use structured key-value pairs.
- **Construction**: No nested object construction. Separate builder calls for debuggable stack traces.

### Code Style & Patterns
- **`final` everywhere**: Method parameters, catch block parameters, local variables not reassigned, class fields initialized once. Prefer `lombok.val` when type is obvious. Skip `final` on lambda params, loop variables that need reassignment, interface method params.
- **Lombok**: Use `@Value` for immutable objects, not `@Data`.
- **Constructor order**: Parameter assignments must match field declaration order.

### Code Organization & DI
- **Method order**: public → package-private → private.
- **Minimize visibility**: Flag public classes/methods not accessed outside their package. Flag public interface implementations.
- **Utility classes**: Private constructor with `throw new AssertionError()`.
- **Constructor injection** over field injection. No `@Named("hardcoded-string")`.

### Test Patterns
- **Naming**: `methodName_condition_expectedResult`.
- **Structure**: Arrange-Act-Assert.
- **Mocks**: `@ExtendWith(MockitoExtension.class)`.
- **Grouping**: `@Nested` classes for related scenarios.
- **Don't over-test**: Skip tests for `@Nonnull`-annotated methods, simple getters/setters, constructor validation with DI frameworks, established exception translation patterns.

---

## 5. CDK / IaC Standards

### Test Coverage (blocking)
- Snapshot tests for new stacks generating CloudFormation templates.
- Update snapshots when modifying existing stacks.
- Not required for new construct definitions that don't modify existing stacks.

### Security (blocking)
- No standalone `*` in IAM actions (specify `service:action`).
- No standalone `*` in IAM resources (specify ARNs). Path wildcards (`/*` after ARN) are OK.
- S3 bucket permissions must include `bucket/*` for object operations.

### Resource Retention (blocking)
- `RemovalPolicy.RETAIN` on: S3 buckets, DynamoDB tables, IAM roles, SNS topics, EventBridge buses/rules, CloudWatch resources.
- Not required on: policy documents (IAM policies, SNS topic policies).

### Configuration
- No hardcoded account IDs or region names in CDK constructs (use context/parameters).
- Hardcoded resource config values (timeouts, memory, ports) are fine.
- Global resources (S3, IAM, CloudFront, Route53) need region identifiers in names. Regional resources (SNS, DDB, Lambda, SQS) don't.
