# Authentication

The API uses the OAuth2 password flow with bearer JWTs.

1. `POST /auth/register` (JSON) or `POST /auth/login` (form fields `username`,
   `password`; the username may also be the email address) returns
   `access_token`.
2. Send it on every request: `Authorization: Bearer <access_token>`.
   Media URLs used in `<img>` tags may pass it as `?token=` instead.

## Token lifetime and revocation

- Tokens are valid for `JWT_EXPIRATION_HOURS` (24 hours by default). Clients
  should send the user back to the login page on `401`.
- Tokens are signed with `JWT_SECRET` (HS256) and carry a per-user version:
  changing the password, deleting the account, or an administrator changing
  roles, resetting the password or deactivating the account revokes every
  token issued before.
- Deactivated accounts cannot log in or use existing tokens.

## Passwords and limits

- At least `PASSWORD_MIN_LENGTH` (12) characters and at most 72 bytes; stored
  as bcrypt hashes.
- Failed logins (per account and per IP) and registrations (per IP) are rate
  limited with `429` and a `Retry-After` header.

## Administrators

Administrators have the `admin` role. The first one is created on the
server with `python -m app.cli create-admin`; see the
[deployment guide](../development/deployment.md#5-create-the-first-administrator).
