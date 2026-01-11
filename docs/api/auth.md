# Authentication

The API uses OAuth2 with Password Flow and Bearer JWT tokens.

## Headers

All protected endpoints require the `Authorization` header:

```http
Authorization: Bearer <your_access_token>
```

## Token Expiry

Tokens are valid for a configurable duration (default: 30 minutes). Clients should handle 401 Unauthorized responses by redirecting to login.
