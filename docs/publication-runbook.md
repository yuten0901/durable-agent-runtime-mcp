# Publication runbook

## Release scope

`release-manifest.txt` is the only authoritative publication file list. The verification script
rejects missing, duplicate, unsorted or unsafe paths and rejects any Git-visible source file that is
not in the manifest. It copies only those files into an isolated directory, creates a temporary Git
commit, makes a local clone and runs every gate from that clone. The source repository's index,
branch and commits are not changed.

Run after installing the development dependencies:

```bash
python tools/verify_release.py
```

If the verification interpreter has the test tools but not the local wheel build tools, select a
Python interpreter that already contains `setuptools>=68` and `wheel>=0.41`:

```bash
python tools/verify_release.py --builder-python /path/to/builder-python
```

No package index or remote Git access is used. The wheel command uses `--no-index`, `--no-deps` and
`--no-build-isolation`. The script then installs that wheel into a fresh venv and runs the packaged
demo from outside the clone.

## Gates

1. manifest path safety, ordering, uniqueness, existence and exact Git-visible source coverage;
2. MIT license markers;
3. credential-like value, email, phone number and local home-path scan;
4. Upwork推奨比率に合わせたportfolio PNGのsignature、1000×750 dimensions、complete chunksとtext/EXIF metadata非混入;
5. explicit evidence-boundary language and absence of overclaim phrases;
6. temporary Git commit and exact manifest-only local clone;
7. pytest, Ruff, strict Mypy, compileall and machine-readable workflow/MCP evaluation;
8. offline wheel build, fresh-venv install and packaged demo assertions;
9. clean tracked worktree after all generated outputs.

## Recommended real commit

Because this repository has no prior commit and the code, tests, evidence and boundary documents form
one accepted unit, use one atomic initial commit rather than publishing a temporarily incomplete
history.

Recommended message:

```text
feat: add checkpointed agent runtime and MCP evidence
```

Before committing, inspect `release-manifest.txt`, then stage exactly its paths:

```bash
git add --pathspec-from-file=release-manifest.txt
git diff --cached --name-only
git commit -m "feat: add checkpointed agent runtime and MCP evidence"
```

Do not use `git add .` for the initial publication. Run the verifier again after the commit. Remote
creation, visibility selection, tag creation and push require separate approval.

## Files kept local

The following review records remain useful locally but are deliberately not public:

- `reports/independent-review-2026-11-06.md`
- `reports/independent-review-2026-11-06-round2.md`
- `reports/independent-review-2026-11-06-round3.md`
- `reports/publication-review-2026-11-06.md`

Also excluded are `.venv/`, test/type/lint caches, `*.egg-info/`, SQLite databases, generated
workspace JSON, temporary verification evaluation files, wheels and build directories.

## Remote rollback

For a normal defective publication, preserve history and revert the published commit:

```bash
git revert <published-commit-sha>
git push origin main
```

If a release tag already exists, do not silently move it. Publish a corrected patch commit and a new
tag, or delete the remote tag only with explicit approval and a recorded reason. If the entire
repository must be withdrawn, first change remote visibility or archive it, then communicate the
withdrawal; those are hosting-provider operations and are not automated here.

If a credential or private client datum is ever published, a normal revert is insufficient: revoke
the credential first, restrict repository visibility, and use the hosting provider's audited history
purge procedure. The current manifest passes the local secret/PII gate, but that gate is not a
substitute for human review.
