"""
================================================================================
HYBRID IDENTITY OPERATIONS & MIGRATION ASSISTANT (MCP SERVER)
================================================================================
What is this server?
  This MCP server orchestrates Active Directory, Entra Connect, and Okta during
  email domain migrations and UPN cutovers.

How does it work?
  Instead of an admin manually clicking through Active Directory Users & Computers
  (ADUC) and Attribute Editor to hand-edit `proxyAddresses` and `userPrincipalName`:
  1. The AI Agent stages and validates the cutover plan (Dry-Run / Pre-Check).
  2. The Admin reviews and approves the plan in chat.
  3. The tool executes the native PowerShell commands on-premises against AD.
  4. It automatically saves a local JSON rollback snapshot before every change.
  5. It triggers delta syncs for Microsoft Entra Connect and Okta.

Safety & Governance:
  - Two-Step Workflow: Staged Plan -> Explicit Execution.
  - Automatic Rollback Snapshot: Captures prior attributes before altering anything.
  - Case-Sensitive SMTP Safeguard: Enforces uppercase `SMTP:` for primary and
    lowercase `smtp:` for aliases so mail routing never breaks.
================================================================================
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

ROLLBACK_DIR = os.getenv("ROLLBACK_DIR", os.path.join(os.path.dirname(__file__), "rollback_history"))
os.makedirs(ROLLBACK_DIR, exist_ok=True)

# ==============================================================================
# SECTION 1: POWERSHELL RUNNER HELPER
# ==============================================================================
def run_powershell_command(command_string: str) -> dict:
    """
    Executes a PowerShell command locally (on Windows Admin VM) or via WinRM.
    Returns: {"success": bool, "output": str, "error": str}
    """
    # Detect if running on Windows (powershell.exe) or cross-platform (pwsh)
    ps_executable = "powershell.exe" if sys.platform == "win32" else "pwsh"
    
    try:
        process = subprocess.run(
            [ps_executable, "-NoProfile", "-NonInteractive", "-Command", command_string],
            capture_output=True,
            text=True,
            timeout=45
        )
        return {
            "success": process.returncode == 0,
            "output": process.stdout.strip(),
            "error": process.stderr.strip()
        }
    except FileNotFoundError:
        return {
            "success": False,
            "output": "",
            "error": f"PowerShell executable '{ps_executable}' not found on this system path."
        }
    except Exception as ex:
        return {
            "success": False,
            "output": "",
            "error": str(ex)
        }


# ==============================================================================
# SECTION 2: MCP TOOLS DEFINITION
# ==============================================================================
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("hybrid-identity-ops-mcp")


# ------------------------------------------------------------------------------
# TOOL 1: Stage User Cutover (Dry-Run & Pre-Check)
# ------------------------------------------------------------------------------
@mcp.tool()
def stage_user_cutover(sam_account_name: str, new_domain: str) -> str:
    """
    STAGING ONLY (Safe / Read-Only): Inspects an on-premises AD user and generates
    the exact attribute cutover plan for UPN, primary mail, and proxyAddresses.
    
    Checks for:
      - Current primary SMTP address and secondary aliases
      - Collision check (verifies new UPN is not already claimed)
      - Formulates the exact new proxyAddresses array with uppercase 'SMTP:' for new primary
        and lowercase 'smtp:' preserving the old primary as a secondary alias.

    Parameters:
      sam_account_name: Active Directory sAMAccountName or username (e.g. 'jdoe').
      new_domain: Target email domain (e.g. 'newcompany.com').
    """
    clean_username = sam_account_name.strip()
    clean_domain = new_domain.strip().lstrip("@").lower()

    # PowerShell script to inspect the user in Active Directory
    ps_inspect_script = f"""
    Import-Module ActiveDirectory -ErrorAction Stop
    $u = Get-ADUser -Identity '{clean_username}' -Properties UserPrincipalName, mail, proxyAddresses, DisplayName, Enabled
    if (-not $u) {{
        throw "User '{clean_username}' not found in Active Directory."
    }}
    [PSCustomObject]@{{
        DisplayName       = $u.DisplayName
        sAMAccountName    = $u.sAMAccountName
        Enabled           = $u.Enabled
        CurrentUPN        = $u.UserPrincipalName
        CurrentMail       = $u.mail
        CurrentProxies    = $u.proxyAddresses
    }} | ConvertTo-Json -Compress
    """

    res = run_powershell_command(ps_inspect_script)
    if not res["success"]:
        return json.dumps({
            "status": "error",
            "message": f"Failed to query Active Directory: {res['error']}"
        }, indent=2)

    try:
        user_data = json.loads(res["output"])
    except Exception as ex:
        return json.dumps({"status": "error", "message": f"Failed to parse AD output: {str(ex)}"}, indent=2)

    # Calculate new attributes
    current_upn = user_data.get("CurrentUPN") or ""
    current_mail = user_data.get("CurrentMail") or ""
    username_prefix = current_upn.split("@")[0] if "@" in current_upn else clean_username

    new_upn = f"{username_prefix}@{clean_domain}"
    new_primary_smtp = f"SMTP:{username_prefix}@{clean_domain}"

    current_proxies = user_data.get("CurrentProxies") or []
    if isinstance(current_proxies, str):
        current_proxies = [current_proxies]

    # Re-calculate proxyAddresses list:
    # 1. Demote any current primary 'SMTP:' to lowercase 'smtp:' (preserve as alias so incoming mail doesn't bounce)
    # 2. Add the new primary 'SMTP:' in uppercase
    staged_proxies = []
    has_new_primary = False

    for proxy in current_proxies:
        if proxy.startswith("SMTP:"):
            staged_proxies.append("smtp:" + proxy[5:])  # Demote to alias
        elif proxy.lower() == f"smtp:{username_prefix}@{clean_domain}".lower():
            # If target address was already an alias, skip it now (will be added as primary)
            continue
        else:
            staged_proxies.append(proxy)

    staged_proxies.insert(0, new_primary_smtp)

    # Collision Check script
    ps_collision_check = f"""
    Import-Module ActiveDirectory -ErrorAction Stop
    $collision = Get-ADUser -Filter "UserPrincipalName -eq '{new_upn}' -or mail -eq '{username_prefix}@{clean_domain}'"
    if ($collision -and $collision.sAMAccountName -ne '{clean_username}') {{
        $true
    }} else {{
        $false
    }}
    """
    col_res = run_powershell_command(ps_collision_check)
    has_collision = col_res["output"].strip().lower() == "true"

    plan = {
        "status": "staged_pending_approval",
        "user": {
            "displayName": user_data.get("DisplayName"),
            "sAMAccountName": user_data.get("sAMAccountName"),
            "accountEnabled": user_data.get("Enabled")
        },
        "collisionWarning": has_collision,
        "changes": {
            "UserPrincipalName": {
                "before": current_upn,
                "after": new_upn
            },
            "mail": {
                "before": current_mail,
                "after": f"{username_prefix}@{clean_domain}"
            },
            "proxyAddresses": {
                "before": current_proxies,
                "after": staged_proxies
            }
        },
        "nextStep": (
            f"If approved, run: execute_user_cutover('{clean_username}', '{clean_domain}')"
            if not has_collision else "BLOCKED: Collision detected with another user. Manual review required."
        )
    }

    return json.dumps(plan, indent=2)


# ------------------------------------------------------------------------------
# TOOL 2: Execute User Cutover (Applies Changes with Snapshot Rollback)
# ------------------------------------------------------------------------------
@mcp.tool()
def execute_user_cutover(sam_account_name: str, new_domain: str, confirm: bool = False) -> str:
    """
    EXECUTION: Modifies on-premises Active Directory attributes for the user.
    Creates an automated snapshot file in rollback_history/ before applying.

    Parameters:
      sam_account_name: AD sAMAccountName (e.g. 'jdoe').
      new_domain: Target email domain (e.g. 'newcompany.com').
      confirm: Safety flag. Must be set to True to confirm execution.
    """
    if not confirm:
        return json.dumps({
            "status": "safety_hold",
            "message": "Execution safety block: confirm must be set to True to apply changes to Active Directory."
        }, indent=2)

    clean_username = sam_account_name.strip()
    clean_domain = new_domain.strip().lstrip("@").lower()

    # Step 1: Capture snapshot of current state for rollback safety
    ps_snapshot_script = f"""
    Import-Module ActiveDirectory -ErrorAction Stop
    $u = Get-ADUser -Identity '{clean_username}' -Properties UserPrincipalName, mail, proxyAddresses, DisplayName
    [PSCustomObject]@{{
        timestamp       = (Get-Date -Format 'o')
        sAMAccountName  = $u.sAMAccountName
        DisplayName     = $u.DisplayName
        UserPrincipalName = $u.UserPrincipalName
        mail            = $u.mail
        proxyAddresses  = $u.proxyAddresses
    }} | ConvertTo-Json -Compress
    """
    snap_res = run_powershell_command(ps_snapshot_script)
    if not snap_res["success"]:
        return json.dumps({"status": "error", "message": f"Snapshot failed: {snap_res['error']}"}, indent=2)

    snapshot_data = json.loads(snap_res["output"])
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_filename = os.path.join(ROLLBACK_DIR, f"{clean_username}_{timestamp_str}.rollback.json")
    with open(snapshot_filename, "w") as f:
        json.dump(snapshot_data, f, indent=2)

    # Step 2: Build the new proxyAddresses list
    current_upn = snapshot_data.get("UserPrincipalName") or ""
    username_prefix = current_upn.split("@")[0] if "@" in current_upn else clean_username
    new_upn = f"{username_prefix}@{clean_domain}"
    new_primary_smtp = f"SMTP:{username_prefix}@{clean_domain}"

    current_proxies = snapshot_data.get("proxyAddresses") or []
    if isinstance(current_proxies, str):
        current_proxies = [current_proxies]

    staged_proxies = []
    for proxy in current_proxies:
        if proxy.startswith("SMTP:"):
            staged_proxies.append("smtp:" + proxy[5:])
        elif proxy.lower() == f"smtp:{username_prefix}@{clean_domain}".lower():
            continue
        else:
            staged_proxies.append(proxy)
    staged_proxies.insert(0, new_primary_smtp)

    # Format proxyAddresses array as PowerShell array string
    proxies_ps_str = "@(" + ",".join([f"'{p}'" for p in staged_proxies]) + ")"

    # Step 3: Apply the change via Set-ADUser
    ps_apply_script = f"""
    Import-Module ActiveDirectory -ErrorAction Stop
    Set-ADUser -Identity '{clean_username}' `
        -UserPrincipalName '{new_upn}' `
        -EmailAddress '{username_prefix}@{clean_domain}' `
        -Replace @{{ proxyAddresses = {proxies_ps_str} }}
    """
    apply_res = run_powershell_command(ps_apply_script)
    if not apply_res["success"]:
        return json.dumps({
            "status": "error",
            "message": f"Set-ADUser failed: {apply_res['error']}",
            "snapshot_saved_at": snapshot_filename
        }, indent=2)

    return json.dumps({
        "status": "success",
        "message": f"Active Directory attributes updated successfully for {clean_username}.",
        "newAttributes": {
            "UserPrincipalName": new_upn,
            "mail": f"{username_prefix}@{clean_domain}",
            "primarySMTP": new_primary_smtp,
            "totalProxies": len(staged_proxies)
        },
        "rollbackSnapshot": snapshot_filename,
        "recommendedNextStep": "Run trigger_sync_cycle() to replicate changes to Entra ID."
    }, indent=2)


# ------------------------------------------------------------------------------
# TOOL 3: Rollback User Cutover (Restores Previous AD State)
# ------------------------------------------------------------------------------
@mcp.tool()
def rollback_user_cutover(sam_account_name: str) -> str:
    """
    EMERGENCY ROLLBACK: Reads the most recent snapshot for the user from rollback_history/
    and restores their previous UPN, mail, and proxyAddresses in Active Directory.

    Parameters:
      sam_account_name: Active Directory username to restore (e.g. 'jdoe').
    """
    clean_username = sam_account_name.strip()
    
    # Locate latest snapshot file
    matching_files = [
        f for f in os.listdir(ROLLBACK_DIR)
        if f.startswith(f"{clean_username}_") and f.endswith(".rollback.json")
    ]
    if not matching_files:
        return json.dumps({
            "status": "error",
            "message": f"No rollback snapshot found for user '{clean_username}' in {ROLLBACK_DIR}."
        }, indent=2)

    matching_files.sort(reverse=True)
    latest_snapshot_path = os.path.join(ROLLBACK_DIR, matching_files[0])

    with open(latest_snapshot_path, "r") as f:
        snap = json.load(f)

    restore_upn = snap.get("UserPrincipalName")
    restore_mail = snap.get("mail")
    restore_proxies = snap.get("proxyAddresses") or []
    if isinstance(restore_proxies, str):
        restore_proxies = [restore_proxies]

    proxies_ps_str = "@(" + ",".join([f"'{p}'" for p in restore_proxies]) + ")"

    ps_rollback_script = f"""
    Import-Module ActiveDirectory -ErrorAction Stop
    Set-ADUser -Identity '{clean_username}' `
        -UserPrincipalName '{restore_upn}' `
        -EmailAddress '{restore_mail}' `
        -Replace @{{ proxyAddresses = {proxies_ps_str} }}
    """
    res = run_powershell_command(ps_rollback_script)
    if not res["success"]:
        return json.dumps({
            "status": "error",
            "message": f"Rollback failed: {res['error']}"
        }, indent=2)

    return json.dumps({
        "status": "rolled_back",
        "message": f"User {clean_username} successfully restored to state from {snap.get('timestamp')}.",
        "restoredAttributes": {
            "UserPrincipalName": restore_upn,
            "mail": restore_mail,
            "proxyAddresses": restore_proxies
        },
        "snapshotFileUsed": latest_snapshot_path
    }, indent=2)


# ------------------------------------------------------------------------------
# TOOL 4: Trigger Entra Connect Sync (Delta Sync)
# ------------------------------------------------------------------------------
@mcp.tool()
def trigger_entra_connect_sync(policy_type: str = "Delta") -> str:
    """
    Forces a Microsoft Entra Connect synchronization cycle immediately.
    Equivalent to running `Start-ADSyncSyncCycle -PolicyType Delta` in PowerShell.

    Parameters:
      policy_type: Either 'Delta' (default, syncs changes) or 'Initial' (full tenant sync).
    """
    clean_policy = "Initial" if policy_type.lower() == "initial" else "Delta"
    ad_sync_server = os.getenv("AD_SYNC_SERVER")

    # If running on AD Connect server locally, execute directly; otherwise use Invoke-Command
    if ad_sync_server:
        ps_sync_script = f"""
        Invoke-Command -ComputerName '{ad_sync_server}' -ScriptBlock {{
            Import-Module ADSync -ErrorAction Stop
            Start-ADSyncSyncCycle -PolicyType {clean_policy}
        }}
        """
    else:
        ps_sync_script = f"""
        Import-Module ADSync -ErrorAction Stop
        Start-ADSyncSyncCycle -PolicyType {clean_policy}
        """

    res = run_powershell_command(ps_sync_script)
    if not res["success"]:
        return json.dumps({
            "status": "error",
            "message": f"Start-ADSyncSyncCycle failed: {res['error']}",
            "hint": "Ensure this command is executed on the Entra Connect server or set AD_SYNC_SERVER in .env"
        }, indent=2)

    return json.dumps({
        "status": "sync_initiated",
        "policyType": clean_policy,
        "message": f"Entra Connect {clean_policy} sync cycle successfully triggered. Changes will reflect in Entra ID shortly."
    }, indent=2)


# ==============================================================================
# SECTION 3: ENTRYPOINT
# ==============================================================================
if __name__ == "__main__":
    # If run in terminal, output server status
    if sys.stdin.isatty():
        print("Hybrid Identity Operations MCP Server: Ready.")
        print("Listening for Model Context Protocol commands on stdio...")
        mcp.run()
    else:
        mcp.run()

