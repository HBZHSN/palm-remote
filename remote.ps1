[CmdletBinding()]
param(
    [ValidateSet('Install','Start','Stop','Restart','Uninstall','Info','Certificate','Run')]
    [string]$Action = 'Install',
    [string[]]$IP = @()
)
$ErrorActionPreference = 'Stop'
# Relaunch management commands with the same arguments after UAC consent.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and $Action -notin @('Info', 'Run')) {
    $scriptLiteral = "'" + $PSCommandPath.Replace("'", "''") + "'"
    $ipLiterals = @($IP | ForEach-Object { "'" + $_.Replace("'", "''") + "'" })
    $command = "& $scriptLiteral -Action '$Action'"
    if ($ipLiterals.Count) { $command += ' -IP @(' + ($ipLiterals -join ',') + ')' }
    $logDirectory = Join-Path $PSScriptRoot 'data'
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $logLiteral = "'" + (Join-Path $logDirectory 'setup.log').Replace("'", "''") + "'"
    $command = "Start-Transcript -LiteralPath $logLiteral -Force | Out-Null; try { $command; exit 0 } catch { Write-Error -ErrorRecord `$_ -ErrorAction Continue; exit 1 } finally { Stop-Transcript | Out-Null }"
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    try {
        $process = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', $encoded) -Wait -PassThru
    } catch {
        throw "Could not obtain administrator permission: $($_.Exception.Message)"
    }
    if ($process.ExitCode -ne 0) {
        throw "Management command failed. Check $(Join-Path $logDirectory 'setup.log')."
    }
    Write-Host 'Completed. Background service is independent of this window.'
    if ($Action -in @('Install', 'Start', 'Restart', 'Certificate')) {
        & $PSCommandPath -Action Info
    }
    return
}
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskName = 'PalmRemote-' + $env:USERNAME
$ruleName = 'PalmRemote-LAN-8765'
function Invoke-Python {
    param([string[]]$Arguments)
    & $pythonPath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python failed with exit code $LASTEXITCODE" }
}
function Update-Certificate {
    $certificateCode = @'
"""Create a private LAN CA and HTTPS leaf certificate; never publish private keys."""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
from pathlib import Path
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

ROOT = Path.cwd()


def addresses():
    result = {'127.0.0.1'}
    for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
        result.add(info[4][0])
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 80))
            result.add(sock.getsockname()[0])
    except OSError:
        pass
    return sorted(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ip', action='append', default=[], help='Additional LAN IP for certificate')
    parser.add_argument('--info', action='store_true', help='Show connection URLs only')
    args = parser.parse_args()
    data = ROOT / 'data'
    data.mkdir(exist_ok=True)
    ips = sorted(set(addresses() + [str(ipaddress.ip_address(ip)) for ip in args.ip]))
    if not args.info:
        now = datetime.now(timezone.utc)
        if (data / 'ca-key.pem').exists():
            ca_key = serialization.load_pem_private_key((data / 'ca-key.pem').read_bytes(), password=None)
            ca = x509.load_pem_x509_certificate((data / 'palm-ca.crt').read_bytes())
            try:
                ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
            except x509.ExtensionNotFound:
                ca = None
        else:
            ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            ca = None
        if ca is None:
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'Palm Remote CA - {socket.gethostname()}')])
            ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                  .not_valid_after(now + timedelta(days=3650))
                  .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                  .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                  .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
                  .sign(ca_key, hashes.SHA256()))
            (data / 'ca-key.pem').write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            (data / 'palm-ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(ca.subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.DNSName(socket.gethostname())]
                               + [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        (data / 'server-key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        (data / 'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        print('HTTPS certificate created. Transfer ONLY data/palm-ca.crt to your phone and trust it.')
    for ip in ips:
        address = ipaddress.ip_address(ip)
        if not address.is_loopback and not address.is_link_local:
            print('Phone URL: https://' + ip + ':8765')
    print('Local URL: https://localhost:8765')


if __name__ == '__main__':
    main()
'@
    $arguments = @('-')
    foreach ($address in $IP) { $arguments += @('--ip', $address) }
    $certificateCode | & $pythonPath @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Certificate generation failed.' }
}
function Show-Info {
    Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' } | ForEach-Object { Write-Host "Phone URL: https://$($_.IPAddress):8765" }
    Write-Host 'Local URL: https://localhost:8765'
    Write-Host 'Trust data/palm-ca.crt on your phone. Never transfer private keys.'
    Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Select-Object TaskName, State
}
function Stop-PalmRemote {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName
    }
    # Older PowerShell task wrappers can leave Python grandchildren running.
    # Match only this installation's exact interpreter and server module.
    $commandPattern = '^"?' + [regex]::Escape((Join-Path $projectRoot '.venv\Scripts\python')) + 'w?\.exe"?\s+-m\s+backend\.server(?:\s|$)'
    $processes = @(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $commandPattern })
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
    }
}
function Start-PalmRemote {
    Start-ScheduledTask -TaskName $taskName
    # Do not report success just because Task Scheduler accepted the request.
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 300
        $task = Get-ScheduledTask -TaskName $taskName
        $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
        $serverProcessIds = @(Get-CimInstance Win32_Process | Where-Object {
            $_.CommandLine -match ('^"?' + [regex]::Escape((Join-Path $projectRoot '.venv\Scripts\pythonw.exe')) + '"?\s+-m\s+backend\.server(?:\s|$)')
        } | Select-Object -ExpandProperty ProcessId)
        if ($task.State -eq 'Running' -and @($listener | Where-Object { $_.OwningProcess -in $serverProcessIds }).Count) { return }
    }
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    throw "Server did not start. Task result: $($info.LastTaskResult). Check data/server.log and port 8765."
}
Push-Location -LiteralPath $projectRoot
try {
    switch ($Action) {
        'Install' {
            if (-not (Test-Path -LiteralPath $pythonPath)) {
                if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
                else { & python -m venv .venv }
                if ($LASTEXITCODE -ne 0) { throw 'Python 3.10+ is required.' }
            }
            Invoke-Python -Arguments @('-m','pip','install','-r','requirements.txt')
            Stop-PalmRemote
            Update-Certificate
            # Manage everything from this PS1, but let Task Scheduler own the server directly.
            $taskAction = New-ScheduledTaskAction -Execute (Join-Path $projectRoot '.venv\Scripts\pythonw.exe') -Argument '-m backend.server' -WorkingDirectory $projectRoot
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
            $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable
            Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
            if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $ruleName }
            New-NetFirewallRule -Name $ruleName -DisplayName 'Palm Remote (private LAN only)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private -RemoteAddress LocalSubnet | Out-Null
            Start-PalmRemote
            Write-Host 'Installed, logon autostart configured, and background service running. You can close this window.'
            Show-Info
        }
        'Run' { Invoke-Python -Arguments @('-m','backend.server') }
        'Start' { Stop-PalmRemote; Start-PalmRemote; Show-Info }
        'Stop' { Stop-PalmRemote }
        'Restart' { Stop-PalmRemote; Start-PalmRemote }
        'Certificate' { Update-Certificate; if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) { Stop-PalmRemote; Start-PalmRemote } }
        'Info' { Show-Info }
        'Uninstall' {
            if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
                Stop-PalmRemote
                Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
            }
            if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) { Remove-NetFirewallRule -Name $ruleName }
            Write-Host 'Task and firewall rule removed. Application data and certificates preserved.'
        }
    }
} finally { Pop-Location }
