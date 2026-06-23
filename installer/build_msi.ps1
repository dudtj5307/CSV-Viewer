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

# 버전 → 안정적(deterministic) GUID. 같은 버전이면 항상 같은 GUID, 버전이 다르면 다른 GUID.
# ProductCode 를 이 방식으로 만들어, '같은 버전=같은 ProductCode(재실행=제거 토글)',
# '다른 버전=다른 ProductCode(MajorUpgrade 로 업그레이드)' 를 동시에 만족시킨다.
# (Windows Installer 는 GUID 의 RFC 버전 비트를 안 따지므로 SHA1 해시→GUID 로 충분.)
function New-DeterministicGuid {
    param([string]$Namespace, [string]$Name)
    $sha1  = [System.Security.Cryptography.SHA1]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes("$Namespace|$Name")
    $hash  = $sha1.ComputeHash($bytes)
    $g = New-Object byte[] 16
    [Array]::Copy($hash, $g, 16)
    $g[6] = ($g[6] -band 0x0F) -bor 0x50   # version nibble = 5 (name-based)
    $g[8] = ($g[8] -band 0x3F) -bor 0x80   # variant bits
    return (New-Object System.Guid(,$g)).ToString().ToUpper()
}

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

# 3) wxs 에서 Version·UpgradeCode 를 읽어 ProductCode 를 버전별로 산출
#    (버전은 CSVViewer.wxs 의 Version="..." 한 곳이 유일한 출처. 여기서 그 값을 읽어 파생만 함.)
$wxsText = Get-Content -Raw -LiteralPath $Wxs
# 대소문자 구분(-cmatch): XML 선언의 소문자 version="1.0" 가 아니라 Package 의 Version="1.0.0.0" 을 잡는다.
if ($wxsText -cnotmatch 'Version="(\d+(?:\.\d+){1,3})"') { throw "CSVViewer.wxs 에서 Version 을 찾지 못했습니다." }
$Version = $Matches[1]
if ($wxsText -cnotmatch 'UpgradeCode="([0-9A-Fa-f-]{36})"') { throw "CSVViewer.wxs 에서 UpgradeCode 를 찾지 못했습니다." }
$UpgradeCode = $Matches[1]
# Windows Installer 의 버전 비교는 앞 3자리(major.minor.build)만 본다(4번째는 무시).
# → 같은 '3자리 버전'은 같은 ProductCode 가 되도록 3자리로 GUID 를 만든다(토글 정합).
$ver3 = ($Version -split '\.')[0..2] -join '.'
$ProductCode = '{' + (New-DeterministicGuid $UpgradeCode $ver3) + '}'

# 4) MSI 빌드
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "==> WiX 빌드  ($wixExe)" -ForegroundColor Cyan
Write-Host "    Version=$Version  ProductCode=$ProductCode" -ForegroundColor DarkGray
& $wixExe build $Wxs -arch x64 -d "DistDir=$Dist" -d "ResRoot=$ResRoot" -d "ProductCode=$ProductCode" -o $Msi
if ($LASTEXITCODE -ne 0) { throw 'WiX 빌드 실패' }

$size = [math]::Round((Get-Item $Msi).Length / 1MB, 1)
Write-Host ''
Write-Host "완료: $Msi  (${size} MB)" -ForegroundColor Green
Write-Host "설치(관리자):  msiexec /i `"$Msi`"" -ForegroundColor Gray
