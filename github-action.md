# GitHub Action — Automated KiCad Design Review

Add automated design review to any KiCad project. Every push and PR that touches KiCad files gets a **commit status check** (green/red with findings summary). On PRs, a structured review comment is posted covering power tree, protocol compliance, voltage derating, SPICE results, EMC risk analysis, thermal analysis, component health, and PCB stats.

No account needed — just add the workflow file.

## Basic setup

```yaml
# .github/workflows/kicad-review.yml
name: KiCad Design Review
on:
  push:
    paths: ['**/*.kicad_sch', '**/*.kicad_pcb']
  pull_request:
    paths: ['**/*.kicad_sch', '**/*.kicad_pcb']

permissions:
  contents: read
  pull-requests: write
  statuses: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: sudo apt-get install -y ngspice poppler-utils
      - uses: MaximGorbunov/kicad-happy-dsh@v1
        id: analysis
      - uses: thollander/actions-comment-pull-request@v3
        if: github.event_name == 'pull_request'
        with:
          file-path: ${{ steps.analysis.outputs.report-path }}
          comment-tag: kicad-happy-review
          mode: upsert
```

The PR comment updates on re-pushes. A [full report](skills/kicad/references/report-generation.md) is available on the Actions run page. SPICE-enhanced mode activates automatically when ngspice is installed. EMC and thermal analysis run automatically after schematic/PCB analysis.

## Diff-based PR reviews

Enable `diff-base: true` to show only what changed between the PR and the base branch — component additions/removals, signal parameter shifts, new/resolved EMC findings, and SPICE status transitions. The PR comment includes a "Changes from Base" section.

```yaml
      - uses: MaximGorbunov/kicad-happy-dsh@v1
        id: analysis
        with:
          diff-base: true
```

## AI-powered review (optional, DSH headless)

For AI-driven reviews inside DeepSeek Harness, run a one-shot DSH agent on a
**self-hosted runner with DSH installed** (desktop app bundle; the CLI boots
from it). The agent loads the `kicad-happy` hub skill and the domain skills,
reads the deterministic analysis output, and writes the review:

```yaml
      - name: DSH AI review
        if: github.event_name == 'pull_request'
        run: |
          dsh headless "The kicad-happy deterministic analysis has already been
          run. Read the markdown report at ${REPORT}. Do NOT re-run analysis
          scripts. Review the findings, verify the top 3-5 IC pinouts against
          datasheets where a local cache exists, check WARNING findings for
          accuracy, and write a concise actionable summary (under 2000 chars)
          to $GITHUB_WORKSPACE/dsh-review.md."
        env:
          REPORT: ${{ steps.analysis.outputs.report-path }}
      - uses: thollander/actions-comment-pull-request@v3
        if: github.event_name == 'pull_request'
        with:
          file-path: dsh-review.md
          comment-tag: kicad-happy-dsh-review
          mode: upsert
```

Notes:

- `dsh headless "<task>"` boots a fresh agent against the current workspace
  (the checkout), so the `.dsh/skills` link gives it the full skill suite
  without any install step.
- The runner's DSH profile/credentials configure the model and provider
  (e.g. the same profile patch the desktop uses). Review cost follows your
  model provider — local models (e.g. llama.cpp via the profile's
  `llamapcpp` provider) make CI reviews free.
- Verify the exact `dsh headless` flags against the runner's DSH version
  (`dsh --help`); the flag set can evolve between releases.

## More examples

See [`action/examples/`](action/examples/) for fork-safe workflows, distributor API keys for datasheet download, the full [`dsh-headless.yml`](action/examples/dsh-headless.yml) two-tier workflow, and advanced configuration.
