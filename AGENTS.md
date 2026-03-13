# AGENTS.md

## Writing Rule
- When using an abbreviation or acronym, define it the first time it appears in the response, for example `sound pressure level (SPL)`.

## Gitignore Rule
- Always add `.DS_Store` to `.gitignore` for repositories in this workspace if it is not already present.

## Project Memory File
- This repository uses `Codex.md` in the repo root as persistent project context.
- At session start in this repo, read `Codex.md` automatically if it exists before deeper exploration.

## `/init` Command Behavior
- If the user enters `/init` (or asks to initialize/refresh project context), create or update root `Codex.md`.
- Write `Codex.md` to the repository root: `./Codex.md`.
- Include only concrete, current project facts:
  - Repository purpose and current scope.
  - Important directories and entry points.
  - Key run/test commands.
  - Current hardware mapping and backend behavior decisions.
  - Active branch/merge status and known follow-up work.
- Keep it concise, skimmable, and accurate.

## Maintenance Rule
- When significant architecture changes are made, update `Codex.md` in the same working session.
- Keep the event queue contract section current when callback payload shape changes (for example string events vs structured events).
