# KCSC AI — Remote Testing Guide

Base URL for all examples: `https://erpnext-16.kcsc.com.jo`

---

## Step 1 — Install the app on the remote server

SSH into your server, then:

```bash
cd /path/to/frappe-bench

# Get the app (if not already there)
bench get-app kcsc_ai /path/to/kcsc_ai  # or from git

# Install on your site
bench --site erpnext-16.kcsc.com.jo install-app kcsc_ai

# Generate and set the encryption key (ONE TIME — keep it safe)
bench --site erpnext-16.kcsc.com.jo set-config kcsc_ai_encryption_key \
  "$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# Run migrate
bench --site erpnext-16.kcsc.com.jo migrate
```

---

## Step 2 — Verify installation

```bash
curl -s https://erpnext-16.kcsc.com.jo/api/method/kcsc_ai.kcsc_ai.api.auth.generate_login_qr \
  -d "user=Administrator" | python3 -m json.tool
```

Expected response:
```json
{
  "message": {
    "qr_data": "{\"t\":\"login\",\"k\":\"...\",\"s\":\"...\"}",
    "expires_in": 60,
    "qr_type": "login"
  }
}
```

---

## Step 3 — Full login flow (curl)

### 3a. Generate a login QR

```bash
BASE=https://erpnext-16.kcsc.com.jo

QR_RESPONSE=$(curl -s -X GET "$BASE/api/method/kcsc_ai.kcsc_ai.api.auth.generate_login_qr" \
  -d "user=Administrator")

echo $QR_RESPONSE | python3 -m json.tool

# Extract the QR token (the "k" field inside qr_data JSON)
QR_TOKEN=$(echo $QR_RESPONSE | python3 -c "
import sys, json
r = json.load(sys.stdin)
qr_data = json.loads(r['message']['qr_data'])
print(qr_data['k'])
")
echo "QR Token: $QR_TOKEN"
```

### 3b. Login with QR token

```bash
LOGIN_RESPONSE=$(curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.auth.qr_login" \
  -H "Content-Type: application/json" \
  -d "{
    \"qr_token\": \"$QR_TOKEN\",
    \"device_id\": \"test-device-001\",
    \"device_name\": \"Test Device\",
    \"platform\": \"Web\"
  }")

echo $LOGIN_RESPONSE | python3 -m json.tool

ACCESS_TOKEN=$(echo $LOGIN_RESPONSE | python3 -c "
import sys, json
print(json.load(sys.stdin)['message']['access_token'])
")
echo "Access Token: $ACCESS_TOKEN"
```

### 3c. Refresh token

```bash
REFRESH_TOKEN=$(echo $LOGIN_RESPONSE | python3 -c "
import sys, json
print(json.load(sys.stdin)['message']['refresh_token'])
")

curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.auth.refresh" \
  -d "refresh_token=$REFRESH_TOKEN&device_id=test-device-001" | python3 -m json.tool
```

---

## Step 4 — Device registration

```bash
# Generate a static pairing QR
STATIC_QR=$(curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.auth.generate_static_qr" \
  -d "user=Administrator")
PAIRING_TOKEN=$(echo $STATIC_QR | python3 -c "
import sys, json
r = json.load(sys.stdin)
qr_data = json.loads(r['message']['qr_data'])
print(qr_data['k'])
")

# Register device using pairing token
curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.device.register_device" \
  -H "Content-Type: application/json" \
  -d "{
    \"device_id\": \"mobile-flutter-001\",
    \"device_name\": \"Flutter Test App\",
    \"platform\": \"Android\",
    \"pairing_token\": \"$PAIRING_TOKEN\"
  }" | python3 -m json.tool

# List devices (requires Bearer token)
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.device.list_devices" \
  -H "Authorization: Bearer $ACCESS_TOKEN" | python3 -m json.tool
```

---

## Step 5 — Workflow Action Queue

```bash
AUTH_HEADER="Authorization: Bearer $ACCESS_TOKEN"
DEVICE_HEADER="X-Device-ID: test-device-001"

# Create a workflow action request
ACTION_RESPONSE=$(curl -s -X POST \
  "$BASE/api/method/kcsc_ai.kcsc_ai.api.workflow.create_action" \
  -H "$AUTH_HEADER" \
  -H "$DEVICE_HEADER" \
  -H "Content-Type: application/json" \
  -d "{
    \"action_type\": \"Workflow\",
    \"reference_doctype\": \"Purchase Order\",
    \"reference_name\": \"PO-2026-00001\",
    \"workflow_action\": \"Approve\",
    \"idempotency_key\": \"test-po-approve-001\"
  }")

echo $ACTION_RESPONSE | python3 -m json.tool
QUEUE_ID=$(echo $ACTION_RESPONSE | python3 -c "
import sys, json
print(json.load(sys.stdin)['message']['action_queue_id'])
")
echo "Queue ID: $QUEUE_ID"

# Check pending actions
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.workflow.get_pending" \
  -H "$AUTH_HEADER" | python3 -m json.tool

# Generate action confirmation QR
ACTION_QR=$(curl -s -X POST \
  "$BASE/api/method/kcsc_ai.kcsc_ai.api.auth.generate_action_qr" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{\"action_queue_id\": \"$QUEUE_ID\"}")
echo $ACTION_QR | python3 -m json.tool
CONFIRM_TOKEN=$(echo $ACTION_QR | python3 -c "
import sys, json
r = json.load(sys.stdin)
qr_data = json.loads(r['message']['qr_data'])
print(qr_data['k'])
")

# Confirm the action with QR token
curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.workflow.confirm_action" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{
    \"action_queue_id\": \"$QUEUE_ID\",
    \"confirmation_token\": \"$CONFIRM_TOKEN\",
    \"confirmation_method\": \"QR\"
  }" | python3 -m json.tool

# Poll status
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.workflow.get_action_status?action_queue_id=$QUEUE_ID" \
  -H "$AUTH_HEADER" | python3 -m json.tool
```

---

## Step 6 — AI Request

```bash
# Query (answered immediately, no queue)
curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.ai.request" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{
    \"action_type\": \"query\",
    \"doctype\": \"Purchase Order\",
    \"name\": \"PO-2026-00001\",
    \"query\": \"What is the status of this order?\"
  }" | python3 -m json.tool

# Workflow action via AI (goes to queue)
curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.ai.request" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{
    \"action_type\": \"workflow\",
    \"doctype\": \"Purchase Order\",
    \"name\": \"PO-2026-00001\",
    \"action\": \"Approve\"
  }" | python3 -m json.tool
```

---

## Step 7 — Tenant Management (System Manager only)

```bash
# List all tenants
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.tenant.list_tenants" \
  -H "$AUTH_HEADER" | python3 -m json.tool

# Get tenant details + live usage
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.tenant.get_tenant?tenant_name=erpnext-16.kcsc.com.jo" \
  -H "$AUTH_HEADER" | python3 -m json.tool

# Usage stats
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.tenant.usage_stats?tenant_name=erpnext-16.kcsc.com.jo" \
  -H "$AUTH_HEADER" | python3 -m json.tool
```

---

## Step 8 — Action Replay

```bash
# Replay a failed action
curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.replay.replay_action" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{\"action_queue_id\": \"$QUEUE_ID\"}" | python3 -m json.tool

# Full history for a document
curl -s "$BASE/api/method/kcsc_ai.kcsc_ai.api.replay.replay_history\
?reference_doctype=Purchase+Order&reference_name=PO-2026-00001" \
  -H "$AUTH_HEADER" | python3 -m json.tool
```

---

## Step 9 — Logout

```bash
curl -s -X POST "$BASE/api/method/kcsc_ai.kcsc_ai.api.auth.logout" \
  -H "$AUTH_HEADER" | python3 -m json.tool
```

---

## Common headers reference

| Header | Value | Required on |
|--------|-------|-------------|
| `Authorization` | `Bearer <access_token>` | All protected endpoints |
| `X-Device-ID` | `<device_id>` | Workflow actions (for risk scoring) |
| `X-Tenant-ID` | `<tenant_name>` | Multi-tenant override (optional) |
| `Content-Type` | `application/json` | POST with JSON body |

---

## Error codes

| HTTP | Frappe exception | Meaning |
|------|-----------------|---------|
| 403 | AuthenticationError | Token invalid/expired/revoked |
| 403 | PermissionError | User lacks ERPNext permission |
| 417 | ValidationError | Bad request data |
| 404 | DoesNotExistError | Document not found |
| 417 | DuplicateEntryError | Idempotency key collision |

---

## FAC (Frappe Assistant Core) integration

If FAC is installed on `https://erpnext-16.kcsc.com.jo`:

1. Set `ai_mode = Local` on your KCSC AI Tenant record (default)
2. AI requests will route through FAC automatically
3. To use a remote FAC on another server:
   - Set `ai_mode = Remote`
   - Set `ai_endpoint = https://your-fac-server/api/method/...`
   - Set `ai_api_key`

All AI actions still go through the Action Queue regardless of mode.
