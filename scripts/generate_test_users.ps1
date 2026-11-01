# Generate test users for Hybrid Identity Ops MCP Proof of Concept
# This script populates the default user container with test accounts
# to demonstrate the MCP server's cutover and management capabilities.

if (-not (Get-Module -ListAvailable ActiveDirectory)) {
    Write-Error "ActiveDirectory module is required. Please install RSAT."
    exit
}

$users = @(
    @{ SamAccountName = "jsmith"; DisplayName = "John Smith"; UPN = "jsmith@oldcorp.com"; Mail = "jsmith@oldcorp.com"; Proxies = "SMTP:jsmith@oldcorp.com" },
    @{ SamAccountName = "ajones"; DisplayName = "Alice Jones"; UPN = "ajones@oldcorp.com"; Mail = "ajones@oldcorp.com"; Proxies = "SMTP:ajones@oldcorp.com" },
    @{ SamAccountName = "rwilson"; DisplayName = "Robert Wilson"; UPN = "rwilson@oldcorp.com"; Mail = "rwilson@oldcorp.com"; Proxies = "SMTP:rwilson@oldcorp.com" },
    @{ SamAccountName = "mgarcia"; DisplayName = "Maria Garcia"; UPN = "mgarcia@oldcorp.com"; Mail = "mgarcia@oldcorp.com"; Proxies = "SMTP:mgarcia@oldcorp.com" },
    @{ SamAccountName = "lchen"; DisplayName = "Li Chen"; UPN = "lchen@oldcorp.com"; Mail = "lchen@oldcorp.com"; Proxies = "SMTP:lchen@oldcorp.com" }
)

$password = ConvertTo-SecureString "P@ssw0rd123!" -AsPlainText

foreach ($u in $users) {
    Write-Host "Creating user: $($u.SamAccountName)"
    try {
        New-ADUser -Name $u.DisplayName `
                    -SamAccountName $u.SamAccountName `
                    -UserPrincipalName $u.UPN `
                    -DisplayName $u.DisplayName `
                    -EmailAddress $u.Mail `
                    -Add @{proxyAddresses=$u.Proxies} `
                    -Enabled $true `
                    -AccountPassword $password `
                    -PasswordNeverExpires $true
        Write-Host "Successfully created $($u.SamAccountName)"
    }
    catch {
        Write-Error "Failed to create $($u.SamAccountName): $_"
    }
}
