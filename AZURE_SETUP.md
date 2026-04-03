# Azure AD Setup — Microsoft Graph Email (5 minutes)

This gives the FG Agent System permission to send approval emails from
`kphipps@firstgenesis.com` using the Microsoft Graph API.
No app passwords, no MFA bypass — just a secure app registration.

**Who does this:** IT admin or anyone with Azure AD admin rights for firstgenesis.com

---

## Step 1 — Register the App

1. Go to **[portal.azure.com](https://portal.azure.com)**
2. Search for **"Azure Active Directory"** → open it
3. Left menu → **"App registrations"** → **"+ New registration"**
4. Fill in:
   - **Name:** `FG-Agent-System`
   - **Supported account types:** `Accounts in this organizational directory only`
   - **Redirect URI:** leave blank
5. Click **"Register"**

---

## Step 2 — Copy Your IDs

On the app's overview page, copy these two values into your `.env` file:

| Value | Where | .env variable |
|---|---|---|
| Application (client) ID | Overview page | `MS_CLIENT_ID` |
| Directory (tenant) ID | Overview page | `MS_TENANT_ID` |

---

## Step 3 — Create a Client Secret

1. Left menu → **"Certificates & secrets"**
2. **"+ New client secret"**
3. Description: `FG-Agent-System-Secret`
4. Expiry: **24 months** (set a calendar reminder to rotate)
5. Click **"Add"**
6. **Copy the secret VALUE immediately** (it's only shown once)
7. Paste it into `.env` as `MS_CLIENT_SECRET`

---

## Step 4 — Grant Mail.Send Permission

1. Left menu → **"API permissions"**
2. **"+ Add a permission"**
3. Choose **"Microsoft Graph"**
4. Choose **"Application permissions"** (not delegated)
5. Search for `Mail.Send` → check the box → **"Add permissions"**
6. Click **"Grant admin consent for First Genesis"** → confirm **"Yes"**

The `Mail.Send` status should show a green checkmark: **"Granted for First Genesis"**

---

## Step 5 — Update Your .env

```env
EMAIL_PROVIDER=graph
MS_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
MS_CLIENT_SECRET=your~secret~value~here
MS_SENDER_EMAIL=kphipps@firstgenesis.com
```

---

## Step 6 — Test It

```bash
# Verify credentials resolve a token correctly
python3 -c "
import os, urllib.request, urllib.parse, json

tenant = os.environ['MS_TENANT_ID']
url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
data = urllib.parse.urlencode({
    'grant_type':    'client_credentials',
    'client_id':     os.environ['MS_CLIENT_ID'],
    'client_secret': os.environ['MS_CLIENT_SECRET'],
    'scope':         'https://graph.microsoft.com/.default',
}).encode()
req = urllib.request.Request(url, data=data, method='POST')
req.add_header('Content-Type', 'application/x-www-form-urlencoded')
with urllib.request.urlopen(req) as r:
    resp = json.loads(r.read())
print('Token acquired:', resp['token_type'], '— expires in', resp['expires_in'], 'seconds')
"

# Then run a real approval email test
EMAIL_PROVIDER=graph FG_ENV=lab python claude_code_agent_ecosystem.py run_pm_agent
```

A successful run will log:
```
Graph API token acquired
Graph email sent for pm_AURA MVP_... → tjohnson@firstgenesis.com
```

---

## Security Notes

- The `Mail.Send` permission allows the app to send as **any user** in your tenant.
  Scope it to Kiera's mailbox only by enabling a **Mail.Send application access policy**:
  ```powershell
  # Run in Exchange Online PowerShell (optional but recommended)
  New-ApplicationAccessPolicy `
    -AppId "<MS_CLIENT_ID>" `
    -PolicyScopeGroupId "kphipps@firstgenesis.com" `
    -AccessRight RestrictAccess `
    -Description "Restrict FG-Agent-System to Kiera mailbox only"
  ```
- Rotate `MS_CLIENT_SECRET` every 24 months (set a reminder when you create it)
- Never commit `.env` to git — it is in `.gitignore`
