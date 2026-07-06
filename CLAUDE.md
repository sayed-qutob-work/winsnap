# WinSnap — Project Notes for Claude

## Development process: spec workflow (mandatory)

This project uses a spec-driven workflow for all non-trivial feature/fix work.
Do NOT create ad hoc `audit.md`, `plan.md`, or similar planning files.

- Workflow definition: `.claude/system-prompts/spec-workflow-starter.md` — the main
  thread acts as coordinator exactly as described there.
- Sub-agents: `.claude/agents/kfc/` (spec-requirements, spec-design, spec-tasks,
  spec-judge, spec-impl, spec-test) plus `.claude/agents/spec-review.md`
  (implementation review — runs after spec-impl, before a task's completion is
  accepted in tasks.md).
- Spec documents live under `.claude/specs/{feature-name}/`
  (requirements.md → design.md → tasks.md), each requiring explicit user approval
  before the next phase.
- Task execution: dispatch to spec-impl agents (parallel or auto mode), then
  spec-review each task. The main thread coordinates; it does not implement
  tasks directly.

## Project facts

- Python tool that migrates Windows user settings between machines:
  `export.py` (source machine) → `.winsnap` zip → `restore.py` (target machine).
- Settings capture/apply logic lives in `modules/` — one module per category,
  each exposing `export(snapshot_dir) -> dict` and `restore(data, snapshot_dir)`.
- `gui.py` is a PyQt6 frontend; backend must stay usable without it.
- Tests: `pytest` + `hypothesis` in `tests/` (mock-heavy; they don't touch the
  real registry). Run with `python -m pytest tests/ -q`.
