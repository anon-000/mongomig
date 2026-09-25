# Security policy

MongoMig runs with write access to production databases, so security reports are taken
seriously.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on
[github.com/anon-000/mongomig](https://github.com/anon-000/mongomig/security/advisories/new).

Include the affected version, a description, and steps to reproduce. You'll get an
acknowledgement within a few days and a fix or mitigation plan as soon as possible.

## Supported versions

Only the latest release receives security fixes while the project is 0.x.

## Scope notes

- MongoMig never logs or prints connection-string passwords; a leak is a security bug.
- Migration files and `env.py` are Python code that MongoMig executes by design; only run
  migrations you trust.
