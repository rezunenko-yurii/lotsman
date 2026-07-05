# Release checklist

Metadata is a contract: CHANGELOG, pyproject, README and the tag must tell the
same story. Run through this list for every release.

1. **Bump the version** in *both* places (they must match):
   - `pyproject.toml` → `version`
   - `lotsman/__init__.py` → `__version__`
2. **Update CHANGELOG.md** — new section on top, dated, factual.
3. **Update README** if commands, numbers, or test counts changed
   (grep for the old test count).
4. **Run the tests**: `python -m unittest discover -s tests` — all green.
5. **Run the benchmark quality gates**:
   `python benchmarks/bench_django.py [--django-dir <checkout>]` — exit 0.
6. **Check consistency** before tagging:
   ```bash
   grep -E 'version|Development Status' pyproject.toml
   git status --short        # must be clean after the release commit
   git grep -n "$OLD_VERSION" -- README.md CHANGELOG.md  # no stale mentions
   ```
7. **Commit, tag, push** — branch and tags together, so the remote never
   shows a tag whose tree is newer than `main`:
   ```bash
   git tag -a vX.Y.Z -m "lotsman X.Y.Z"
   git push origin main --tags
   ```
8. **Verify the remote**: `git ls-remote origin | head` — `HEAD` and the new
   tag must point at the same commit you just made.
