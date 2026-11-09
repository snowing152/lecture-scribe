# docs

Project notes that are not user instructions. The user guide is the
top-level [`README.md`](../README.md). The binding rules for working on the
code are in [`CLAUDE.md`](../CLAUDE.md).

| file | what it holds |
|---|---|
| [`bugs.md`](bugs.md) | open bugs, known limitations, and everything fixed so far with its commit |
| [`decisions.md`](decisions.md) | each design decision with date, reason and what it ruled out, including reversed ones |
| [`reviews/`](reviews/) | one file per code review: range reviewed, findings, what held up |

When a review finds something, give it a `B-n` id in `bugs.md` and link the
review. When a bug is fixed, move it to *Fixed* with the commit. When a
decision is made or reversed, add a `D-n` entry here and update the
*Settled decisions* in `CLAUDE.md`.
