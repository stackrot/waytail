# Contributing

One person maintains this repo, and the process below applies to every
change, including the maintainer's own and anything an AI agent writes.

## How a change lands

1. Start with an issue. Bugs, features and chores all get one. Search the
   open issues first, and if nothing fits, open a new one from a template.
2. Branch from the latest default branch and name the branch
   `<issue>-<short-slug>`, for example `42-fix-login-redirect`.
3. Keep the branch to that one issue. If you find something else along the
   way, open a separate issue for it with the `found-in-passing` label and
   leave it out of your PR, unless your change can't work without it.
4. Open a pull request that closes the issue (`Closes #42` in the
   description) and fill in the template.
5. Get the checks green. Every PR runs the repo's CI, a policy check for the
   linked issue and AI disclosure, and an automated Claude code review. Fix
   each review comment or reply to say why not, then resolve the thread.
   PRs from forks don't get the automated review, so the maintainer reviews
   them by hand.
6. Once every required check passes and every thread is resolved, the PR is
   squash-merged and its branch deleted.

Nobody pushes straight to the default branch, the maintainer included. The
branch ruleset blocks it.

## Commit messages and PR titles

Match the style in `git log`. The PR title becomes the commit subject on the
default branch, and the PR description becomes the commit body, so write both
for someone reading the history later.

## AI-assisted changes

AI agents can contribute, but they have to say so. Any change an AI agent
wrote, in whole or in part, must state:

- the model's full name and version, with its exact ID where it has one
- the effort or reasoning level it ran at
- the harness that ran it, with its version

Put them in the PR description (the template has a section for them) and as
trailers on every commit the agent wrote:

```
AI-Model: Claude Opus 5.5 (claude-opus-5-5)
AI-Effort: high
AI-Harness: Claude Code 2.1.332 in T3 Code
```

If more than one model or session worked on the change, list each of them.
Write `unknown` for a value the harness doesn't show rather than guessing. If
no AI was involved, replace the three lines with `AI assistance: none`. The
policy check fails a PR that has neither.

Whoever opens the PR is responsible for it, whoever or whatever wrote the
code.

## Development

You need Python 3.11 or newer. The panel tests also need PyGObject, GTK 4
and gtk4-layer-shell, which the Install section of `README.md` covers.

```bash
python -m pip install ruff
ruff check .
python -m unittest discover -v
shellcheck scripts/install.sh
python -m pip wheel --no-deps --wheel-dir dist .
```

Panel tests are skipped if GTK or a display isn't available. Set
`WAYTAIL_REQUIRE_GTK_TESTS=1` to require them. With `python-coverage`,
`xorg-server-xvfb` and `xorg-xauth` installed, this runs the full suite in a
virtual display and checks the 90% coverage floor, as CI does:

```bash
GDK_BACKEND=x11 WAYTAIL_REQUIRE_GTK_TESTS=1 xvfb-run -a dbus-run-session -- python -m coverage run -m unittest discover -v
python -m coverage report
```

CI runs the lint, unit tests and wheel build on Python 3.11 and 3.15, and
the full suite with GTK on Arch Linux.

## Security issues

Don't report a vulnerability in a public issue. Use "Report a vulnerability"
on the repo's Security tab, or follow `SECURITY.md` if there is one.

## Licence

Contributions are made under the licence in `LICENSE`.
