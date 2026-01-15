param(
  [string]$Version = "latest",
  [string]$InstallDir = "$env:LOCALAPPDATA\Programs\die-cli"
)

$repo = "leandrofariasldf/die-cli"
if ($Version -eq "latest") {
  $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest"
  $asset = $release.assets | Where-Object { $_.name -like "die-cli-*-win-x64.zip" } | Select-Object -First 1
  if (-not $asset) {
    Write-Host "No zip asset found on latest release."
    exit 1
  }
  $url = $asset.browser_download_url
} else {
  $url = "https://github.com/$repo/releases/download/v$Version/die-cli-$Version-win-x64.zip"
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$zip = Join-Path $env:TEMP "die-cli-$Version-win-x64.zip"
Invoke-WebRequest -Uri $url -OutFile $zip
Remove-Item -Recurse -Force (Join-Path $InstallDir "*") -ErrorAction SilentlyContinue
Expand-Archive -Force -Path $zip -DestinationPath $InstallDir
Remove-Item -Force $zip

$path = [Environment]::GetEnvironmentVariable("Path", "User")
if ($path -notmatch [regex]::Escape($InstallDir)) {
  [Environment]::SetEnvironmentVariable("Path", "$path;$InstallDir", "User")
}

Write-Host "Installed to $InstallDir"
Write-Host "Reopen the terminal to use 'die-cli'."
