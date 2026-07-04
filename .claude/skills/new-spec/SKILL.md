---
name: new-spec
description: Trigger this skill when the user asks to create a new feature, implement a new requirement, brainstorm specifications, or start developing a new function.
argument-hint: "[feature idea or 'open']"
user-invocable: true
allowed-tools: Read, WebSearch, Agent(doc-updater, implementer, tester, reviewer, tech-lead, product-manager), AskUserQuestion
disallowed-tools: Write, Edit, Replace
model: opus
---
# New Feature Development Workflow (new-spec)

## When to trigger (発火条件)
- Use this skill when the user asks to "create a new feature", "implement a new requirement", "brainstorm specifications", or "start developing a new function".
- Example triggers: "Let's brainstorm a new feature", "I want to add a new API", "Start a new spec".

When this skill is invoked, you will act as the main orchestrator. Work together with the user and execute the following steps sequentially. Do not proceed to the next step until the current step is fully completed.

## Context Window Awareness
This is a multi-phase skill. If context reaches or exceeds 70% during any phase (especially during the testing/review loops):
- Append this notice to the current response: "**Context is approaching the limit (≥70%).** Progress has been saved to the GitHub Issue. Please open a fresh Claude Code session and run `/next-task` or `/resume-work` to continue."
- Ensure all current progress is saved to the Issue via `gh issue comment` before stopping.

## Step 1: Specification Brainstorming
- First, act as the brainstorming partner (as the main agent) and dive deep into the user's requirements.
- **Do NOT generate everything silently.** You must interact with the user step-by-step.
- Use professional brainstorming principles: Withhold judgment, encourage unusual ideas, and build on ideas using "yes, and..." rather than "but...".
- Clarify the implementation approach, architectural changes, API designs, etc.
- **Do not proceed to the next step until the implementation plan is drafted and explicitly agreed upon by the user.**

## Step 2: Create GitHub Issue and Git Branch
- Once the plan is drafted, use `Bash` to run `gh issue create` with an appropriate title and body.
- Extract the newly created issue number, and use `Bash` to checkout a new branch named `issue-<number>` (e.g., `git checkout -b issue-42`).

## Step 3: Design Review Gates (PM & Tech Lead)
- **PM Gate**: Invoke `Agent(product-manager)` with the drafted specification to evaluate the scope and user value. Require a `[PM-SCOPE]: APPROVE` verdict.
- **Tech Lead Gate**: Invoke `Agent(tech-lead)` with the technical approach to evaluate feasibility and architecture. Require a `[TL-FEASIBILITY]: APPROVE` verdict.
- If either gives `CONCERNS` or `REJECT`, revise the specification with the user and request another review before proceeding.

## Step 4: Implementation (Implementer)
- Invoke `Agent(implementer)` and provide it with the approved specifications to request the implementation.

## Step 5: Testing & Review Loop
- Once implementation is complete, invoke `Agent(tester)` to write and execute tests.
- **Tester Gate**: The tester will return a verdict of `[TEST-EXECUTION]: SUCCESS`, `FAIL`, or `SPEC-ISSUE`. If `FAIL`, you MUST invoke `Agent(implementer)` to fix the code, then call `Agent(tester)` again.
- Once the Tester returns `SUCCESS`, invoke `Agent(reviewer)` to review the entire codebase for code quality, architectural validity, and security.
- **Reviewer Gate**: The reviewer will return a verdict of `[CODE-REVIEW]: APPROVE`, `CONCERNS`, `REJECT`, or `SPEC-ISSUE`. If it is NOT `APPROVE`, you MUST pass the reviewer's feedback to `Agent(implementer)` for fixes, then restart the cycle (Tester -> Reviewer).
- **IMPORTANT**: You must repeat this cycle until the Reviewer explicitly outputs `[CODE-REVIEW]: APPROVE`. Do not skip this validation.
- **Spec Escalation**: If any agent returns `SPEC-ISSUE`, or reports that a failure is due to a flaw, contradiction, or impossibility in the original specification, STOP the loop immediately. Present the issue to the user, revise the specification together, and then restart the implementation from Step 4.

## Step 6: Update Documentation and ADR
- After the review is fully passed, determine the exact documentation and ADR updates based on what was actually implemented.
- Invoke `Agent(doc-updater)` to apply these changes to `docs/` and `docs/planning/adr.md` (strictly following the ADR formatting rules).

## Step 7: Merge Check, Issue Comment & Close
- Ensure all changes are committed on the branch.
- Verify if the branch is ready to be merged (e.g. check `git status`, or run a merge check).
- Use `Bash` to run `gh issue comment <number> --body "..."` summarizing the work done, updated docs, and completion status.
- Use `Bash` to run `gh issue close <number>`.

## Output and Next Steps
When all steps are finished, output a summary in the following format:
- **Summary**: A brief summary of what was implemented and the closed issue number.
- **Verdict: COMPLETE**
- **Recommended Next Steps**: Suggest exactly what the user can do next (e.g., "Run `/next-task` to pick up the next item from the roadmap", "Deploy the changes to staging", etc.)
