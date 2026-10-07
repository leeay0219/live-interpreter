# Contributing

Use short branches and pull requests for focused changes. Explain the behavior being changed and the relevant checks. Keep generated recordings, PDFs, session records and personal AWS settings out of commits.

`VERSION` identifies a source release. `CHANGELOG.md` records user-visible changes, migration notes and limitations. Routine intermediate commits, debugging transcripts and local deployment addresses do not belong in release notes.

Before a release:

1. Run unit tests, caption checks and the two isolated browser checks from the README.
2. Update `VERSION` and `CHANGELOG.md`.
3. Run `python3 tools/prepare_release.py`. For the first public history, add `--init-git --git-name <public-name> --git-email <GitHub-noreply-email>`.
4. Inspect the produced source directory and checksum manifest.
5. Run `python3 tools/prepare_release.py --verify-public-git <public-repository>` to check all reachable history and author metadata.
6. Publish the reviewed repository to the intended GitHub account.

The release builder initializes a fresh repository only when `--init-git` is provided. It does not copy development history or configure a remote. For later releases of an already public repository, use a branch and pull request to preserve that public history rather than replacing it with a new initial commit.

Do not put real customer transcripts or personal information in bug reports. Use a small synthetic example that demonstrates the behavior.
