# Auditor gate — content-tier smoke test

This is an **intentional, safe smoke-test artifact** opened to validate Phase 3 of
the auditor gate (content-tier auto-merge). It changes only this markdown file, so
`auditor/tiers.py` classifies the PR as **content**.

Expected behaviour: the auditor reviews this PR with the content model, finds no
blockers, confirms it is mergeable + non-draft, and **auto-merges it** via
`gh pr merge --squash --delete-branch`.

It introduces no code and no runtime behaviour. Safe to delete in a later content
PR once the test has been observed.
