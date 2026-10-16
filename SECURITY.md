<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Security Policy

This is a personal, single-maintainer Home Assistant custom integration.
It is maintained in spare time, but security reports are taken
seriously and always get a private channel.

## Supported Versions

Security fixes land on the default branch (`main`) and ship in the next
tagged release. Only the most recent published release is supported;
older tags do not receive back-ported fixes. If you are running an older
release, upgrade before reporting.

| Version                  | Supported          |
| ------------------------ | ------------------ |
| Latest published release | :white_check_mark: |
| Older releases and tags  | :x:                |
| Unreleased `main`        | Best effort        |

## Reporting a Vulnerability

Please report security vulnerabilities **privately**.

### Preferred: GitHub Private Vulnerability Reporting

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Fill in the details described below.

This opens a draft security advisory that only the maintainer can see.

### Alternative: Email

If you cannot use GitHub's private reporting, email the maintainer at
<tykeal@bardicgrove.org>. There is no PGP key for this project; if you
need encryption, say so in a first contact message and something can be
arranged.

### Do Not Open a Public Issue or Pull Request

Please do **not** report a vulnerability through a public GitHub issue,
discussion, or pull request. Doing so discloses the problem to everyone
running the integration before a fix exists.

## What to Include

A good report usually has:

- The affected version, tag, or commit SHA.
- Steps to reproduce, ideally a minimal proof of concept.
- What an attacker gains: the practical impact.
- Any workaround or mitigation you already found.
- How you would like to be credited, if at all.

## What to Expect

This project has one maintainer and no security team, so the commitment
here is deliberately modest and honest rather than ambitious:

- **Acknowledgement**: on a best-effort basis, normally within 14 days.
- **Assessment**: confirmation of the issue and its severity, with
  questions back to you if the report needs more detail.
- **Fix**: developed privately in a draft advisory where that is
  practical, then released in a new tagged version.
- **Disclosure**: coordinated with you. The advisory is published once a
  fix is available, or sooner if the issue is already public. If you want
  to disclose on your own timeline, say so and a schedule can be agreed.
- **Credit**: reporters are named in the advisory unless they ask not to
  be.

If you have had no reply after 14 days, a follow-up email is welcome.
Silence means the message was missed, not ignored.

## Scope

In scope: the code, configuration, and documentation in this repository.

This integration authenticates to the Hostaway API with a Client ID and
Client Secret that Home Assistant stores in the config entry. Anything
that exposes those credentials, or other account data, is squarely in
scope. Concretely:

- Leaking the Client ID, Client Secret, or an access token through logs,
  exception messages, or error text. The integration routes API logging
  and error strings through `custom_components/hostaway/api/redaction.py`,
  which masks secret-like keys, bearer tokens, and door codes; a bypass of
  that redaction is a vulnerability.
- Leaking credentials or account-identifying data through the diagnostics
  download. The diagnostics payload is deliberately limited to rate-limit
  and coordinator state and names the account by a random label rather
  than its Client ID; anything that puts a credential or account
  identifier back into that payload is a vulnerability.
- Insecure storage or transmission of credentials, improper TLS
  handling, or injection flaws in requests built by this integration.

Out of scope, because they are not this project's to fix:

- Vulnerabilities in Home Assistant core or in other integrations.
  Report those to the Home Assistant project at
  <https://github.com/home-assistant/core/security/policy>.
- Vulnerabilities in the Hostaway service or its API. Report those to
  Hostaway directly.
- Vulnerabilities in third-party dependencies. Report those to the
  dependency's maintainers; this project will pick up the fixed version
  once released.
- Issues that require an attacker to already have administrative access
  to the Home Assistant instance, since such an attacker can read the
  config entry anyway.
