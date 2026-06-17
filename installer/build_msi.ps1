<#
  CSV Viewer  MSI 빌드 스크립트
  ------------------------------------------------------------
  사용법 (PowerShell):
      .\installer\build_msi.ps1            # 기존 dist 로 MSI만 빌드
      .\installer\build_msi.ps1 -Rebuild   # PyInstaller로 앱부터 다시 빌드 후 MSI

  결과물:  installer\out\CSV Viewer Setup.msi
  필요 도구:  .NET SDK(dotnet) + WiX CLI(없으면 자동 설치)
#>
[CmdletBinding()]
param(
    [switch]$Rebuild   # 지정 시 PyInstaller 로 앱을 먼저 재빌드
)

$ErrorActionPreference = 'Stop'

$Root    = Split-Path $PSScriptRoot -Parent          # 프로젝트 루트
$Dist    = Join-Path $Root   'dist\CSV Viewer'       # PyInstaller onedir 산출물
$ResRoot = Join-Path $Root   'GUI\res'
$Wxs     = Join-Path $PSScriptRoot 'CSVViewer.wxs'
$OutDir  = Join-Path $PSScriptRoot 'out'
$Msi     = Join-Path $OutDir 'CSV Viewer Setup.msi'
$Python  = 'C:\Users\yslee\anaconda3\envs\sniff_env\python.exe'

# 1) (선택) PyInstaller 앱 재빌드
if ($Rebuild) {
    Write-Host '==> PyInstaller 앱 재빌드' -ForegroundColor Cyan
    & $Python -m PyInstaller --noconfirm (Join-Path $Root 'CSV_Viewer.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 빌드 실패' }
}

if (-not (Test-Path (Join-Path $Dist 'CSV Viewer.exe'))) {
    throw "앱 산출물이 없습니다: $Dist`n  → 먼저 PyInstaller 빌드를 하거나 -Rebuild 옵션으로 실행하세요."
}

# 2) WiX CLI 확보 (PATH → 사용자 tools 폴더 → 없으면 dotnet tool 설치)
$wixExe = $null
$wixCmd = Get-Command wix -ErrorAction SilentlyContinue
if ($wixCmd) {
    $wixExe = $wixCmd.Source
} else {
    $candidate = Join-Path $env:USERPROFILE '.dotnet\tools\wix.exe'
    if (Test-Path $candidate) {
        $wixExe = $candidate
    } else {
        Write-Host '==> WiX CLI 미설치 → dotnet tool 로 전역 설치 (v5)' -ForegroundColor Cyan
        # v5 고정: WiX 6/7 은 OSMF(상용 유지비) EULA 동의를 강제하는 게이트가 있다.
        # v5 는 동일 스키마(v4 namespace)에 그 게이트가 없어 그대로 빌드된다.
        dotnet tool install --global wix --version 5.0.2
        if ($LASTEXITCODE -ne 0) { throw 'WiX 설치 실패 (dotnet SDK 가 필요합니다)' }
        $wixExe = $candidate
    }
}
if (-not (Test-Path $wixExe)) { throw "wix 실행파일을 찾을 수 없습니다: $wixExe" }

# 3) MSI 빌드
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "==> WiX 빌드  ($wixExe)" -ForegroundColor Cyan
& $wixExe build $Wxs -arch x64 -d "DistDir=$Dist" -d "ResRoot=$ResRoot" -o $Msi
if ($LASTEXITCODE -ne 0) { throw 'WiX 빌드 실패' }

$size = [math]::Round((Get-Item $Msi).Length / 1MB, 1)
Write-Host ''
Write-Host "완료: $Msi  (${size} MB)" -ForegroundColor Green
Write-Host "설치(관리자):  msiexec /i `"$Msi`"" -ForegroundColor Gray
