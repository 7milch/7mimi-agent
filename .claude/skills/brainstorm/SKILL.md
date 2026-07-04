---
name: brainstorm
description: "Guided software concept ideation — from a loose idea to a structured concept document. Uses professional problem discovery, concept generation, scope definition techniques, and multi-agent validation."
argument-hint: "[feature idea, problem statement, or 'open']"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, WebSearch, AskUserQuestion, Agent(tech-lead, product-manager)
disallowed-tools: Bash
model: sonnet
---
# Software Ideation Workflow (brainstorm)

## When to trigger (発火条件)
- Use this skill when the user wants to "explore a new idea", "define requirements from scratch", or "brainstorm a new project/feature" before actual implementation begins.
- Example triggers: "Let's brainstorm a new app", "I have a vague idea for a feature, let's flesh it out."

When this skill is invoked, act as a professional System Architect and Orchestrator. Guide the user through the following phases. 
**Do NOT generate everything silently.** You must pause and interact with the user step-by-step. Use `AskUserQuestion` at key decision points.

## Context Window Awareness
This is a multi-phase skill. If context reaches or exceeds 70% during any phase:
- Append this notice: "**Context is approaching the limit (≥70%).** Progress has been saved. Please open a fresh Claude Code session to continue."

---

### Phase 1: Problem Discovery
Ask conversational questions to understand the core problem:
- **Target Audience**: Who is the primary user? What is their current pain point?
- **Goals**: What does success look like for this feature/project?
- **Constraints**: What are the strict limitations? (Timeline, budget, existing architecture).
*Synthesize the answers into a brief Problem Statement and confirm it with the user.*

### Phase 2: Solution Concept Generation
Generate **3 distinct approaches** to solve the problem (e.g., A lightweight/fast approach, a robust/scalable approach, a creative/unconventional approach).
For each, provide:
- **Concept Name & Pitch**
- **Pros & Cons**
- **Biggest Risk**
Use `AskUserQuestion` to present these 3 concepts and ask the user which one they prefer or if they want to combine them. **Wait for their choice.**

### Phase 3: Core Workflow Design
For the chosen concept, outline the core user experience:
- **Happy Path**: What is the primary step-by-step flow when everything goes right?
- **Edge Cases**: What are 2-3 critical failure states or edge cases?
*Ask the user if this workflow aligns with their vision.*

### Phase 4: Design Pillars & PM Review
Collaboratively define:
- **3 Design Pillars**: Core principles that guide decision making.
- **3 Anti-goals (Out of Scope)**: Explicitly state what we will NOT build right now to prevent scope creep.
*Confirm these pillars with the user.*

**PM Review Gate**: After agreeing on the pillars, invoke `Agent(product-manager)` with the Problem Statement, Workflow, Pillars, and Anti-goals. Ask for a `[PM-SCOPE]` verdict.
- If `APPROVE`: Proceed to Phase 5.
- If `CONCERNS` or `REJECT`: Present the PM's feedback to the user and revise the scope or pillars together until approved.

### Phase 5: Technical Feasibility & Tech Lead Review
Ground the concept in reality:
- **Technical Stack**: Which technologies or APIs should be used? (Use `WebSearch` if you need to research).
- **MVP Definition**: What is the absolute minimum viable product that proves the value?
*Confirm the technical direction and MVP scope.*

**Tech Lead Review Gate**: After defining the stack and MVP, invoke `Agent(tech-lead)` with the technical approach and MVP. Ask for a `[TL-FEASIBILITY]` verdict.
- If `APPROVE`: Proceed to Phase 6.
- If `CONCERNS` or `REJECT`: Present the Tech Lead's feedback to the user and revise the technical stack or MVP together until approved.

### Phase 6: Document Generation & Next Steps
- Compile all approved points (Problem Statement, Chosen Concept, Workflow, Pillars/Anti-goals, MVP) into a structured markdown document.
- Present the content to the user. Use the `Write` tool to save it as `docs/planning/concept-[name].md` only AFTER getting explicit approval.
- **Output a summary**:
  - **Verdict: COMPLETE**
  - **Recommended Next Step**: "Run `/new-spec` to begin creating GitHub issues and starting the implementation loop based on this concept."
