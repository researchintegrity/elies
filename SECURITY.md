# Security policy

## Reporting a vulnerability

Please report security problems **privately**, not in public issues:

- use GitHub's private vulnerability reporting: the repository's
  **Security** tab, then **Report a vulnerability**
  (https://github.com/researchintegrity/elies/security/advisories/new);
- include what is affected, how to reproduce it and its impact.

We will acknowledge the report within a week, keep you informed while it is
fixed and credit you in the advisory unless you prefer otherwise.

## Supported versions

Security fixes are made on the `main` branch and released from there. Run the
latest release, and keep your `.env` in line with `.env.example`.

## Deployment checklist

ELIES stores unpublished research material, so a deployment should at least:

- set a random `JWT_SECRET` (32+ characters) and keep it private;
- keep MongoDB, Redis, Milvus, MinIO, CBIR and provenance bound to
  `127.0.0.1` (`BIND_ADDRESS`, the default) and set `REDIS_PASSWORD`;
- serve the API over HTTPS through a reverse proxy, with `ALLOWED_ORIGINS`
  limited to the frontend's URL;
- create administrators with `python -m app.cli create-admin` (there is no
  default account) and review `GET /admin/audit-log`;
- treat the worker containers as privileged: they control the host's Docker
  daemon through `/var/run/docker.sock`;
- back up MongoDB and the workspace regularly.

See [docs/development/deployment.md](docs/development/deployment.md) for
details.
