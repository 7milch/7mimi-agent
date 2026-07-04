---
name: next-task
description: Trigger this skill when the user asks "what should we do next", "resume work", "check current status", or asks for the next task to implement.
argument-hint: "[optional context or focus area]"
user-invocable: true
allowed-tools: Read, Grep, Glob, Bash, WebSearch, Agent(doc-updater, implementer, tester, reviewer), AskUserQuestion
disallowed-tools: Write, Edit, Replace
model: opus
---
# Next Task Orchestration Workflow (next-task)

## When to trigger (発火条件)
- Use this skill when the user asks "what should we do next", "resume work", "check current status", or asks for the "next task" from the roadmap or issues.
- Example triggers: "What's next?", "Resume from yesterday's work", "Find the next task and start".

When this skill is invoked, you will act as the main orchestrator to determine the next step and lead its development. Execute the following steps sequentially. Do not proceed to the next step until the current step is fully completed.

## Context Window Awareness
This is a multi-phase skill. If context reaches or exceeds 70% during any phase:
- Append this notice to the current response: "**Context is approaching the limit (≥70%).** Progress has been saved to the GitHub Issue. Please open a fresh Claude Code session and run `/next-task` to continue."
- Ensure all current progress is saved to the Issue via `gh issue comment` before stopping.

## Step 1: Situation Analysis
- Use `Read`, `Grep`, `Glob` to check `docs/planning/` (roadmap, adr, open-questions).
- Use `Bash` to check open GitHub issues (`gh issue list`) and git status/history (`git status`, `git log`).
- Review actual code if necessary to confirm the current progress.

## Step 2: Propose Next Steps
- Based on your analysis, present the current situation and propose a few structured candidates for the next task to the user.
- **Do NOT generate everything silently.** You must present the options and wait for the user to select one.
- **Do not proceed until the user explicitly selects and approves a specific task.**

## Step 3: Create or Select Issue and Checkout Branch
- If an issue for the task doesn't exist, use `gh issue create`. Otherwise, use the existing issue number.
- Use `Bash` to checkout a branch named `issue-<number>` (e.g., `git checkout -b issue-42`). If the branch already exists, just check it out.

## Step 4: Implementation (Implementer)
- Invoke `Agent(implementer)` and provide it with the specifications to request the implementation.

## Step 5: Testing (Tester) & Review (Reviewer) Loop
- Invoke `Agent(tester)` to write and execute tests for the newly implemented code.
- **Tester Gate**: The tester will return a verdict of `[TEST-EXECUTION]: SUCCESS`, `FAIL`, or `SPEC-ISSUE`. If `FAIL`, you MUST invoke `Agent(implementer)` to fix the code, then call `Agent(tester)` again.
- Once the Tester returns `SUCCESS`, invoke `Agent(reviewer)` to review the codebase for quality, architecture, and security.
- **Reviewer Gate**: The reviewer will return a verdict of `[CODE-REVIEW]: APPROVE`, `CONCERNS`, `REJECT`, or `SPEC-ISSUE`. If it is NOT `APPROVE`, you MUST pass the reviewer's feedback to `Agent(implementer)` for fixes, then restart the cycle (Tester -> Reviewer).
- **IMPORTANT**: You must repeat this cycle until the Reviewer explicitly outputs `[CODE-REVIEW]: APPROVE`. Do not skip this loop.
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
- **Recommended Next Steps**: Suggest exactly what the user can do next (e.g., "Run `/next-task` to pick up the next item from the roadmap", "Check the staging environment", etc.)
