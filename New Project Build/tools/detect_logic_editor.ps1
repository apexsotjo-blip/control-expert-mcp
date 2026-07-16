# Detect the SCADAPack x70 Logic Editor's COM automation on a machine with
# RemoteConnect installed. Run in PowerShell on that machine:
#   powershell -ExecutionPolicy Bypass -File detect_logic_editor.ps1
# It writes LogicEditor_Detect_Report.txt next to itself - send that back.
#
# Background: the Logic Editor is an OEM Control Expert (UnitySoControl
# 16.2). Plain Control Expert registers the automation broker ProgID
# 'PSBroker.PServerBroker.1'; the OEM build likely registers the same or a
# renamed broker. If we find it, the DDT Mirror bridge can drive the Logic
# Editor directly (open .stu, import, build, save) via
#   set CE_MCP_BROKER_PROGID=<found progid>

$report = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "LogicEditor_Detect_Report.txt"
$out = New-Object System.Collections.Generic.List[string]
function Say($s) { $out.Add($s); Write-Host $s }

Say "=== Logic Editor COM detection, $(Get-Date) ==="
Say ""

# 1. ProgIDs that look like an automation broker / Unity OEM
Say "--- HKCR ProgIDs matching PSBroker/PServer/Unity/SoControl:"
$classesRoot = [Microsoft.Win32.RegistryKey]::OpenBaseKey('ClassesRoot', 'Default')
$names = $classesRoot.GetSubKeyNames()
$hits = $names | Where-Object {
    $_ -like '*PSBroker*' -or $_ -like '*PServer*' -or
    $_ -like '*UnityPro*' -or $_ -like '*SoControl*' -or
    $_ -like '*Unity.*' -or $_ -like '*LogicEditor*'
}
foreach ($h in $hits) {
    $clsid = ""
    try { $clsid = $classesRoot.OpenSubKey("$h\CLSID").GetValue("") } catch {}
    $server = ""
    if ($clsid) {
        foreach ($k in "CLSID\$clsid\LocalServer32", "WOW6432Node\CLSID\$clsid\LocalServer32") {
            try {
                $v = $classesRoot.OpenSubKey($k).GetValue("")
                if ($v) { $server = $v; break }
            } catch {}
        }
    }
    Say ("  {0}`n      CLSID: {1}`n      Server: {2}" -f $h, $clsid, $server)
}
if (-not $hits) { Say "  (none found)" }
Say ""

# 2. Schneider registry hives
Say "--- HKLM Schneider Electric keys:"
foreach ($root in 'HKLM:\SOFTWARE\Schneider Electric',
                  'HKLM:\SOFTWARE\WOW6432Node\Schneider Electric') {
    if (Test-Path $root) {
        Get-ChildItem $root -ErrorAction SilentlyContinue | ForEach-Object {
            Say ("  {0}" -f $_.Name)
            Get-ChildItem $_.PSPath -ErrorAction SilentlyContinue |
                Select-Object -First 6 | ForEach-Object { Say ("      {0}" -f $_.PSChildName) }
        }
    }
}
Say ""

# 3. Logic Editor executables on disk
Say "--- Logic Editor / UnitySoControl executables:"
foreach ($pf in "$env:ProgramFiles", "${env:ProgramFiles(x86)}") {
    if (-not $pf) { continue }
    Get-ChildItem $pf -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'Schneider|SCADAPack|RemoteConnect' } |
        ForEach-Object {
            Get-ChildItem $_.FullName -Recurse -Depth 3 -Include *.exe -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match 'Unity|SoControl|Logic|PServer|Broker' } |
                ForEach-Object { Say ("  {0}" -f $_.FullName) }
        }
}
Say ""
Say "=== done ==="
$out | Out-File -FilePath $report -Encoding utf8
Write-Host ""
Write-Host "Report written to: $report"
