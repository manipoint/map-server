# Authentication and Session Management

## Goals

The Flutter user should remain signed in without repeated login prompts while sessions remain revocable after logout, password reset, account disablement, token theft, or administrator action.

This is achieved with short-lived access tokens and rotating long-lived refresh sessions—not a permanent bearer token.

## Credential model

| Credential | Client storage | Server storage | Purpose |
| --- | --- | --- | --- |
| Access token | Memory | Normally stateless claims plus session lookup/version | Authenticate REST and WebSocket handshakes. |
| Refresh token | iOS Keychain/Android Keystore | Cryptographic hash only | Rotate access credentials without interactive login. |
| WebSocket ticket | Memory, optional | Short-lived one-time record/hash | Authenticate clients that cannot set a handshake header. |

Native Flutter should prefer an `Authorization` handshake header. A future browser client should use an `HttpOnly`, `Secure`, `SameSite` cookie through a backend-for-frontend pattern.

## Login and rotation

```mermaid
sequenceDiagram
    title Login and refresh rotation
    participant Flutter
    participant FastAPI
    participant AuthService
    participant PostgreSQL

    Flutter->>FastAPI: POST /auth/login
    FastAPI->>AuthService: Verify credentials
    AuthService->>PostgreSQL: Create device session
    PostgreSQL-->>AuthService: Session ID
    AuthService-->>FastAPI: Access and refresh tokens
    FastAPI-->>Flutter: Login response
    Flutter->>FastAPI: POST /auth/refresh
    FastAPI->>AuthService: Validate refresh token
    AuthService->>PostgreSQL: Rotate token hash
    PostgreSQL-->>AuthService: Updated session
    AuthService-->>FastAPI: New token pair
    FastAPI-->>Flutter: Refresh response
```

Rotation invalidates the previous refresh token. Reuse of an already-rotated token is treated as possible theft and revokes the affected token family or device session.

## Session record

Required fields:

```text
id
user_id
refresh_token_hash
token_family_id
device_id
device_name
created_at
last_used_at
expires_at
rotated_at
revoked_at
revoke_reason
replaced_by_session_id
ip_address
user_agent
```

The application may silently extend an active session according to configured policy. A session is still revoked on explicit logout and security events. "Stay signed in" is a user-experience guarantee, not permission to create a credential that can never expire.

## FastAPI endpoints

```text
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
POST /api/v1/auth/logout-all
GET  /api/v1/auth/sessions
DELETE /api/v1/auth/sessions/{session_id}
```

Registration may be deferred if an external identity provider is selected. Endpoint bodies and rate limits are versioned independently from WebSocket events.

## Access-token claims

Minimum claims:

```text
sub          user ID
sid          session ID
iat          issued time
exp          expiration time
iss          configured issuer
aud          configured audience
jti          unique token ID
```

Authorization roles/scopes may be added later. Sensitive profile information does not belong in token claims.

## WebSocket authorization

1. Validate token signature, issuer, audience, and expiry.
2. Verify the referenced user and session are active.
3. Bind the connection to `user_id` and `session_id`.
4. Revalidate long-running connections periodically.
5. Close every connection mapped to a session when it is revoked.
6. Never accept a refresh token as WebSocket authorization.

## Logout behavior

Device logout:

1. Revoke the current server session transactionally.
2. Close mapped WebSocket connections.
3. Delete access/refresh credentials from Flutter secure storage.
4. Return success even when the session was already revoked.

Logout-all revokes every active user session and forces all devices to reauthenticate.

## Security events

Require revocation or reauthentication after:

- password reset or recovery;
- email/phone ownership change;
- account disablement;
- refresh-token reuse;
- suspicious device or location policy trigger;
- privilege change;
- confirmed credential exposure.

## Passwords and abuse controls

- Use an established password-hashing algorithm and library with configurable work factor.
- Rate-limit login, registration, refresh, and recovery separately.
- Return non-enumerating login/recovery errors.
- Record security audit events without tokens or passwords.
- Add MFA/passkeys later without changing the session table contract.

## Failure behavior

| Condition | Result |
| --- | --- |
| Access token expired | REST returns 401; Flutter refreshes then retries once. |
| Refresh token valid | Rotate and return a new pair. |
| Refresh token reused | Revoke session family and require login. |
| Session revoked | Reject REST/WebSocket and clear local credentials. |
| Auth database unavailable | Fail closed; do not create an unauthenticated session. |
| Logout repeated | Return idempotent success. |

## Data and tracing

Tokens, password material, cookies, authorization headers, and recovery codes are excluded from logs and LangSmith. Security events use opaque identifiers and minimal device metadata with an explicit retention policy.
