# Own httpx client instead of the official SDK

The Higgsfield API is plain REST, and the official `higgsfield-client` SDK is pre-1.0 (0.2.0). We talk to the API through our own small httpx client so that we fully control idempotency keys, retries and backoff, price estimates and file uploads, and are not exposed to breaking changes in an unstable SDK.
