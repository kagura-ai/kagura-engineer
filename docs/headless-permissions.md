# Headless permissions

A headless `claude -p` has **no human to answer Claude Code's permission
prompts**. A tool call that would normally raise an "allow?" dialog simply hangs
and is reported as blocked, and the run red-halts. Every capability a run needs
must therefore be granted *up front*, in two separate places.

## 1. The repository allowlist

Commit a `.claude/settings.json` listing the commands and tools a run actually
uses. `Edit` / `Write` are **separate permissions from the Bash patterns**: with
only a Bash allowlist, `start` passes — it merely runs commands — and
`implement` red-halts, because it has to write code.

A working baseline:

```json
{
  "permissions": {
    "allow": [
      "Edit", "Write", "NotebookEdit",
      "Bash(git status *)", "Bash(git diff *)", "Bash(git log *)",
      "Bash(git add *)", "Bash(git commit *)", "Bash(git checkout *)",
      "Bash(git push *)", "Bash(git fetch *)",
      "Bash(gh auth status)", "Bash(gh issue view *)",
      "Bash(gh pr create *)", "Bash(gh pr view *)", "Bash(gh api *)",
      "Bash(pytest *)", "Bash(uv run *)"
    ]
  }
}
```

`kagura-engineer setup --fix headless-permissions` merges missing entries into
an existing file after an explicit prompt.

## 2. Workspace trust

The allowlist is honoured only for directories the **human** has trusted in
Claude Code (`hasTrustDialogAccepted` in `~/.claude.json`).

Trust for the repository and trust for the `.kagura-runs/<repo>/` worktree area
are **separate**. Each run gets a fresh `run-<issue>` worktree path, so trust
must cover the worktree *parent*, not one specific run directory.

Open `claude` once in the repository **and** once under `.kagura-runs/<repo>/`,
accepting the trust dialog each time. This step is deliberately human-only: an
agent must not be able to widen its own permissions.

## Verifying both at once

```bash
kagura-engineer doctor --exec-probe
```

The probe checks both, in the context `run` actually executes in. It creates an
ephemeral git worktree under `.kagura-runs/<repo>/` — exercising the committed
allowlist and the worktree-area trust together — launches the resolved headless
brain there, asks it to run one approval-requiring command from the baseline
(`gh auth status`; read-only git is approval-free and would prove nothing) and
to write one uniquely-named temp file, verifies that write **on disk** rather
than trusting the model's self-report, then removes the worktree.

It names exactly which capability is blocked, before a real run burns a dispatch
discovering it. The probe spends tokens and takes roughly 30 seconds or more,
which is why it is opt-in.

With `brain_backend: codex` the probe is skipped: Codex uses its own sandbox and
approval model rather than Claude Code permissions.
