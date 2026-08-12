---
name: eunjin-engineering-workflow
description: "Use for Eunjin's coding, debugging, review, and security-lab tasks."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows, linux]
metadata:
  hermes:
    tags: [workflow, debugging, verification, backend, kernel, ctf, orchestration]
---

# Eunjin Engineering Workflow

A lightweight routing and completion discipline derived from the user's recurring backend, Windows kernel, CTF, debugging, and agent-harness work. Apply repository instructions and the user's current request before this skill.

## 1. Classify the requested operation

Determine which mode the user actually requested:

- **Explain/review/how-to:** inspect real files and evidence, then provide findings or proposed code in chat. Do not edit unless the user explicitly requests application or project rules permit it.
- **Build/fix/apply/run/verify:** make the change, exercise it, and keep working until the requested artifact and behavior are verified.
- **Plan only:** produce a plan without implementation.

Do not turn a narrow question into a broad redesign.

## 2. Establish and preserve the project contract

Treat rules the user explicitly designates as **project-wide rules** as persistent invariants, not instructions they must repeat in every request. Do not infer persistence merely because an instruction is repeated, important, or appears broadly applicable; ordinary instructions remain task-local unless the user explicitly promotes them or an authoritative repository instruction defines them as project-wide. Before asking the user to restate a designated rule, recover it from repository instructions, authoritative project artifacts, memory, or prior sessions.

At the start of each task:

1. identify the project and load its explicitly designated persistent constraints;
2. inspect repository `AGENTS.md`, `.hermes.md`, README, and assignment materials;
3. re-read the authoritative API/schema/specification files relevant to the task;
4. inspect the relevant implementation and call sites;
5. inspect manifests, lockfiles, resolved dependency versions, build configuration, and current git diff;
6. inspect actual logs, runtime output, screenshots, or VM/container state when applicable.

A remembered rule determines what to check; the current repository artifact determines its present contents. Continue applying a known project rule until the user or an authoritative repository change supersedes it. Do not repeatedly ask whether an established rule still applies when its current status can be verified from the repository.

Write down the minimum success conditions implied by the user's request. When a project contract conflicts with a generic best practice, follow the project contract unless it is unsafe or the user asks to change it.

## 3. Root-cause discipline

For bugs, build failures, dependency mismatches, or unexplained behavior, load and follow `systematic-debugging`.

- Reproduce first when feasible.
- Trace the failing path from observed symptom to the responsible configuration or code.
- Inspect declared and resolved dependency versions; never recommend a version change from an error message alone.
- Test one concrete hypothesis at a time.
- Do not stack speculative fixes.

## 4. Scope discipline

Prefer the smallest complete change.

Do not add unrelated abstractions, generic hardening, edge-case support, style cleanups, process lookups, dereferences, or memory access unless required. Preserve existing future-facing interfaces when the user says they will be used later.

Before editing, identify:

- files that must change;
- files that must not change;
- behavior that must remain compatible;
- whether the user wants code applied or only shown.

After editing, inspect the diff for drive-by changes.

## 5. Domain-specific routing

### Backend and APIs

- Treat the repository's OpenAPI/schema as the evaluation contract for methods, status codes, schemas, nullability, defaults, CORS, and error formats, subject to confirmed project-specific exceptions in memory or repository instructions.
- Verify framework-generated rejection paths, 404/405 behavior, and malformed-input paths rather than checking only happy-path handlers.
- For the user's backend study repositories, honor the no-direct-edit and test-review exclusions when those project rules apply.

### Windows kernel and systems coursework

Load the matching Windows kernel skill when relevant.

- Respect the exact Windows build, target process, assignment offsets, and requested IOCTL semantics.
- Do not generalize a coursework implementation into a production-grade driver unless requested.
- Keep transport-only IOCTLs transport-only.
- Distinguish PFN, physical page base, virtual address, and formatting conversions precisely.
- Verify builds and VM behavior separately; a host build does not prove a driver loaded or an IOCTL worked.

### CTF and security labs

Load `pwnable-ctf-exploitation` for authorized pwnable work.

- Operate only on user-provided or explicitly authorized targets.
- Separate static plausibility from actual exploitability.
- Reproduce the supplied server/container environment and solve using only the intended challenge files when that is the evaluation question.
- Verify reliability with repeated end-to-end runs when practical.
- Clean up temporary containers/processes and distinguish intentional SIGTERM cleanup from crashes.

### Agent harnesses and orchestration

For multi-agent systems, load `autonomous-ai-agents` or the matching Codex/Claude skill.

- Keep the main/orchestrator agent focused on decomposition, initial worker instructions, feedback from reports, lifecycle management, synthesis, and cleanup.
- Do not make the orchestrator duplicate each worker's tool-by-tool solving.
- Use isolated worktrees or workspaces for concurrent writers.
- Define machine-readable worker reports and explicit completion criteria.
- Verify worker cleanup and end-to-end smoke behavior, not only class structure.

## 6. Verification ladder

Use the narrowest useful check first, then broaden:

1. syntax/type/config validation;
2. targeted unit or reproduction test;
3. relevant subsystem test;
4. full build/test when feasible;
5. runtime, API, container, VM, browser, or exploit verification matching the real request;
6. final diff and artifact inspection.

A task is complete only when the requested artifact exists and the relevant behavior has been exercised. If the canonical suite has unrelated failures, report both the failing suite and a passing targeted verification; do not relabel the whole suite as passing.

## 7. Reporting

Keep the final response concise and evidence-based:

- what changed or what was found;
- exact scope and files;
- commands/checks actually run;
- real pass/fail output;
- remaining blocker or unverified boundary;
- any user action required next.

Never fabricate output or silently replace a failed canonical check with a weaker one.
