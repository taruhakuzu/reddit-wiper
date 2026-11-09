# Security policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Use GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Security" tab, then "Report a vulnerability") on this repository.

Include what you found, how to reproduce it, and the impact. Never include real credentials,
tokens or anyone's personal data in a report. You should get a reply within a week.

## Scope

Things we especially care about:

- Credentials, passwords, 2FA codes, tokens or session keys appearing in logs, output or files.
- The tool sending data anywhere other than reddit.com.
- Ways to delete content without `--execute` and the username confirmation.

## Supported versions

Only the latest release receives fixes.

## For users

- Your credentials file and backups live in your user config and data folders (see
  `reddit-wiper status`). Keep them private.
- If you think your client secret leaked, delete the app at https://www.reddit.com/prefs/apps
  and create a new one. If your password leaked, change it on Reddit.
