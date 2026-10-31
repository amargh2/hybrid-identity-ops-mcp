# Hybrid Identity Operations MCP Server (`hybrid-identity-ops-mcp`)

An enterprise-governed Model Context Protocol (MCP) server designed for Hybrid Active Directory, Microsoft Entra ID (AD Connect), and Okta migration workflows. 

It provides safe, staged, conversational execution of on-premises attribute cutovers (`userPrincipalName`, `mail`, and case-sensitive `proxyAddresses`), automated rollback snapshots, and directory synchronization triggers.

---

## 🛡️ Enterprise Governance & Safety Gates

This server is designed for high-stakes hybrid environments where Active Directory is the on-premises Source of Authority:

1. **Two-Stage Execution**:
   - `stage_user_cutover`: **Read-Only**. Validates current attributes, checks for username collisions, and prepares the exact change diff for human review.
   - `execute_user_cutover`: Requires explicit confirmation (`confirm=True`).
2. **Automated Rollback Snapshots**:
   - Before any attribute is modified in AD, the tool serializes the user's prior state into a timestamped JSON file in `rollback_history/`.
   - `rollback_user_cutover`: Instantly restores the user's previous UPN, primary email, and proxy list in one command if needed.
3. **Case-Sensitive Exchange Safeguard**:
   - Automatically formats `SMTP:` (uppercase) for the new primary address and `smtp:` (lowercase) for all secondary aliases.
   - Preserves the legacy address as an alias so incoming customer and vendor emails never bounce.
4. **Audit Logging**:
   - All actions, changes, and snapshots are recorded with timestamps.

---

## 🚀 Available Tools

| Tool | PowerShell Equivalent | Purpose |
| :--- | :--- | :--- |
| **`stage_user_cutover`** | `Get-ADUser` + collision logic | **Stage / Dry-Run**: Validates current attributes and generates the proposed cutover plan for review. |
| **`execute_user_cutover`** | `Set-ADUser -Replace @{proxyAddresses=...}` | **Execute**: Takes a rollback snapshot, updates UPN, mail, and proxy list in Active Directory. |
| **`rollback_user_cutover`** | `Set-ADUser` (Restores snapshot) | **Emergency Reversal**: Restores the user to their pre-cutover state from the snapshot. |
| **`trigger_entra_connect_sync`** | `Start-ADSyncSyncCycle -PolicyType Delta` | Forces an immediate Entra Connect synchronization without waiting 30 minutes. |

---

## 📋 Prerequisites

- **Host Environment**: Windows Admin VM / Management Jumpbox with:
  - Windows PowerShell 5.1 or PowerShell 7 (`pwsh`)
  - Remote Server Administration Tools (RSAT) / `ActiveDirectory` PowerShell module
  - Line-of-sight to Active Directory Domain Controllers
- [Python 3.12+](https://www.python.org/)
- [`uv`](https://docs.astral.sh/uv/)

---

## ⚙️ Setup & Configuration

### 1. Install `uv` (in PowerShell):
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Clone Repository & Setup Environment:
```powershell
git clone git@github.com:amargh2/hybrid-identity-ops-mcp.git
cd hybrid-identity-ops-mcp

Copy-Item .env.example .env
```

Edit `.env`:
```ini
# Optional: Hostname of Entra Connect server if running remotely via WinRM
AD_SYNC_SERVER=adconnect.corp.internal
```

---

## 🤖 Running with AI Clients

### In VS Code / Antigravity (`.vscode/mcp.json`):
```json
{
  "servers": {
    "hybrid-identity-ops-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "${workspaceFolder}",
        "hybrid-identity-ops-mcp.py"
      ]
    }
  }
}
```

### In Claude Code (CLI):
```powershell
claude mcp add hybrid-identity-ops-mcp uv run --directory . hybrid-identity-ops-mcp.py
```

---

## 💬 Example Conversational Workflow

### 1. Stage & Review:
> **Admin**: *"We need to cut over Bob Smith (`bsmith`) to `newcorp.com`. Stage the change and check for collisions."*
>
> **Agent**: 
> ```json
> {
>   "status": "staged_pending_approval",
>   "user": { "displayName": "Bob Smith", "sAMAccountName": "bsmith" },
>   "collisionWarning": false,
>   "changes": {
>     "UserPrincipalName": { "before": "bsmith@oldcorp.com", "after": "bsmith@newcorp.com" },
>     "mail": { "before": "bsmith@oldcorp.com", "after": "bsmith@newcorp.com" },
>     "proxyAddresses": {
>       "before": ["SMTP:bsmith@oldcorp.com"],
>       "after": ["SMTP:bsmith@newcorp.com", "smtp:bsmith@oldcorp.com"]
>     }
>   }
> }
> ```

### 2. Approve & Execute:
> **Admin**: *"Looks clean. Execute the cutover."*
>
> **Agent**: Calls `execute_user_cutover('bsmith', 'newcorp.com', confirm=True)`
> - Snapshot saved to `rollback_history/bsmith_20261031_143000.rollback.json`
> - `Set-ADUser` applied in on-prem Active Directory.

### 3. Sync:
> **Admin**: *"Kick off Entra Connect sync."*
>
> **Agent**: Calls `trigger_entra_connect_sync(policy_type='Delta')`
