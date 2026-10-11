# Contributing

Use short branches and pull requests for focused changes. Explain the behavior being changed and the relevant checks. Keep generated recordings, PDFs, session records and personal AWS settings out of commits.

## Checks

```bash
tools/bootstrap.sh --dev
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
.venv/bin/python tests/test_commit.py
.venv/bin/python tests/test_studio_browser.py
.venv/bin/python tests/test_writeup_browser.py
.venv/bin/python tests/test_studio_controls_browser.py
.venv/bin/python tests/test_login_browser.py
```

Browser checks use system Chrome and an isolated test server. They make no real AWS calls. Interface rules are in [DESIGN.md](DESIGN.md).

## Releases

`VERSION` identifies a source release. `CHANGELOG.md` records user-visible changes, migration notes and limitations. Routine intermediate commits, debugging transcripts and local deployment addresses do not belong in release notes.

Before a release:

1. Run the checks above.
2. Update `VERSION` and `CHANGELOG.md`.
3. Run `python3 tools/prepare_release.py`. For the first public history, add `--init-git --git-name <public-name> --git-email <GitHub-noreply-email>`.
4. Inspect the produced source directory and checksum manifest. `SHA256SUMS` belongs with the ZIP, for example as a GitHub Releases asset, and is not committed to the repository.
5. Run `python3 tools/prepare_release.py --verify-public-git <public-repository>` to check all reachable history and author metadata.
6. Publish the reviewed repository to the intended GitHub account.

The release contains only source needed to run, generalized configuration examples, documentation and tests. It excludes local Git history, personal settings, event materials, user records and bundled fonts. Results are written to `out/releases/`.

The builder scans every file for credentials, account identifiers and similar values. To also block your organization's email domain or internal hostnames, add one `label<TAB>regex` per line to `tools/release-checks.local`. The file is ignored by Git and is not published.

The release builder initializes a fresh repository only when `--init-git` is provided. It does not copy development history or configure a remote. Use the GitHub noreply address from your GitHub email settings; the builder never takes a personal or company email from global Git settings. For later releases of an already public repository, use a branch and pull request to preserve that public history rather than replacing it with a new initial commit.

Do not put real customer transcripts or personal information in bug reports. Use a small synthetic example that demonstrates the behavior.
