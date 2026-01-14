param(
  [string]$Version = "latest",
  [string]$InstallDir = "$env:LOCALAPPDATA\Programs\die-cli"
)

$repo = "leandrofariasldf/die-cli"
if ($Version -eq "latest") {
  $url = "https://github.com/$repo/releases/latest/download/die-cli.exe"
} else {
  $url = "https://github.com/$repo/releases/download/v$Version/die-cli.exe"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$dest = Join-Path $InstallDir "die-cli.exe"
Invoke-WebRequest -Uri $url -OutFile $dest

$path = [Environment]::GetEnvironmentVariable("Path", "User")
if ($path -notmatch [regex]::Escape($InstallDir)) {
  [Environment]::SetEnvironmentVariable("Path", "$path;$InstallDir", "User")
}

Write-Host "Installed to $dest"
Write-Host "Reopen the terminal to use 'die-cli'."
