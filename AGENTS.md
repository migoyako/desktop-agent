# Repository Agent Rules

## Internal Context Output

- Never send internal compaction, checkpoint, continuation, or handoff material to the user.
- Text inside `<analysis>`, `<summary>`, `<thinking>`, or `<final>`, and output requested as `CONTEXT CHECKPOINT SUMMARY`, is internal state only. Never make it the assistant's visible response.
- A context checkpoint is not task completion. After a continuation, inspect the current state and continue the interrupted implementation without restating the checkpoint.
- User-facing progress updates must be short and limited to concrete actions, findings, or blockers.
