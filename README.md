# raphael-sync

Desktop file monitoring, offline sync, conflict resolution

## API

- Prefix: `/v1/sync`
- Port: `8098`
- Health: `GET /health`

## Events

_Published and consumed events documented in `openapi.yaml` and raphael-contracts._

## Development

```bash
uv sync
uv run uvicorn raphael_sync.app:app --reload --port 8098
```

Part of the [Raphael Platform](https://github.com/hummingbird-labs) by HummingBird Labs.
