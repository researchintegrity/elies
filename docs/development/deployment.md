# Deployment

## Docker Compose

The primary deployment method is via Docker Compose.

```bash
docker-compose up -d
```

## Production Considerations

- **Security**: Ensure `SECRET_KEY` and database passwords are strong and not default.
- **HTTPS**: Use a reverse proxy (like Nginx or Traefik) to handle SSL termination.
- **Persistence**: Ensure Docker volumes are backed up regularly.
