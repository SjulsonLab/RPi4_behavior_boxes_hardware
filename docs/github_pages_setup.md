# GitHub Pages Setup

The Sphinx documentation for this repository is built from
[docs/source](/Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware/docs/source)
and deployed by the GitHub Actions workflow in
[docs-pages.yml](/Users/lukesjulson/codex/RPi4_refactor/targets/RPi4_behavior_boxes_hardware/.github/workflows/docs-pages.yml).

## How it works

- The generated HTML under `docs/_build/html` is a build artifact, not a source file.
- Pushes to `main` that touch `docs/` or the workflow file trigger a Sphinx build.
- The workflow uploads the generated HTML and deploys it to GitHub Pages.

## One-time repository setting

In the GitHub repository settings:

1. Open `Settings`.
2. Open `Pages`.
3. Under `Build and deployment`, set `Source` to `GitHub Actions`.

Once that is enabled, future documentation pushes should publish automatically.
