param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$sourceRelativePath = 'docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md'
$sourcePath = Join-Path $repositoryRoot $sourceRelativePath

$chapterPaths = [ordered]@{
    '1'  = 'docs/product/01-프로젝트-개요-및-배경.md'
    '2'  = 'docs/product/02-목표-범위-및-성공-기준.md'
    '3'  = 'docs/architecture/03-기존-명세-대비-재설계-결정.md'
    '4'  = 'docs/product/04-사용자-및-시연-시나리오.md'
    '5'  = 'docs/architecture/05-전체-시스템-아키텍처.md'
    '6'  = 'docs/hardware/06-하드웨어-및-기구-설계.md'
    '7'  = 'docs/architecture/07-소프트웨어-기술-스택-및-실행-환경.md'
    '8'  = 'docs/jetson/08-ROS2-자율주행-및-탐사-설계.md'
    '9'  = 'docs/jetson/09-AI-인식-및-센서-융합.md'
    '10' = 'docs/jetson/10-영상-스트리밍-및-이벤트-녹화.md'
    '11' = 'docs/frontend/11-통합-관제-웹-시스템.md'
    '12' = 'docs/backend/12-통신-프로토콜-및-API.md'
    '13' = 'docs/backend/13-데이터베이스-시계열-S3-설계.md'
    '14' = 'docs/architecture/14-상태-머신-및-안전-정책.md'
    '15' = 'docs/operations/15-저장소-배포-CI-CD.md'
    '16' = 'docs/testing/16-테스트-및-검증-계획.md'
    '17' = 'docs/product/17-일정-및-역할-분배.md'
    '18' = 'docs/operations/18-위험-관리와-대체안.md'
    '19' = 'docs/testing/19-최종-시연-계획.md'
    '20' = 'docs/testing/20-완료-기준-및-KPI.md'
    '21' = 'docs/hardware/21-하드웨어-인터페이스-전원-배선-상세-설계.md'
    '22' = 'docs/jetson/22-센서-수집-시간-동기화-좌표계-상세-설계.md'
    '23' = 'docs/jetson/23-SLAM-위치-추정-미지-영역-탐사-상세-설계.md'
    '24' = 'docs/jetson/24-Nav2-경로-계획-장애물-회피-안전-속도-설계.md'
    '25' = 'docs/jetson/25-사람-탐지-추적-위치-추정-상세-설계.md'
    '26' = 'docs/architecture/26-Mission-Manager-임무-상태-머신-상세-설계.md'
    '27' = 'docs/backend/27-Spring-Boot-관제-백엔드-도메인-API-상세-설계.md'
    '28' = 'docs/frontend/28-Next.js-관제-화면-조이스틱-UX-상세-설계.md'
    '29' = 'docs/backend/29-PostgreSQL-TimescaleDB-S3-Outbox-상세-설계.md'
    '30' = 'docs/architecture/30-피해자-발견-접근-상호작용-오케스트레이션.md'
    '31' = 'docs/architecture/31-Jetson-Spring-Boot-관제-웹-통신-설계.md'
    '32' = 'docs/jetson/32-영상-스트리밍-링-버퍼-이벤트-녹화-상세-설계.md'
    '33' = 'docs/jetson/33-피해자-음성-상호작용-상세-설계.md'
    '34' = 'docs/hardware/34-STM32-저수준-주행-제어-안전-통신-설계.md'
    '35' = 'docs/hardware/35-센서-짐벌-엔코더-캘리브레이션-및-튜닝.md'
    '36' = 'docs/operations/36-보안-개인정보-데이터-보호-정책.md'
    '37' = 'docs/operations/37-운영-모니터링-장애-복구-설계.md'
    '38' = 'docs/testing/38-요구사항-추적표-최종-인수-시험.md'
    'A'  = 'docs/specifications/appendices/부록-A-기능-요구사항-상세.md'
    'B'  = 'docs/specifications/appendices/부록-B-비기능-안전-요구사항.md'
    'C'  = 'docs/specifications/appendices/부록-C-주요-ROS-토픽-액션.md'
    'D'  = 'docs/specifications/appendices/부록-D-환경-변수-예시.md'
    'E'  = 'docs/specifications/appendices/부록-E-초기-실행-순서.md'
    'F'  = 'docs/specifications/appendices/부록-F-주요-로그-확인-명령.md'
    'G'  = 'docs/specifications/appendices/부록-G-용어집.md'
    'H'  = 'docs/specifications/appendices/부록-H-최종-TBD-및-변경-관리.md'
    'I'  = 'docs/specifications/appendices/부록-I-최종-BOM-전력-예산-템플릿.md'
    'J'  = 'docs/specifications/appendices/부록-J-배선도-핀맵-확정표.md'
    'K'  = 'docs/specifications/appendices/부록-K-소프트웨어-기준선.md'
    'L'  = 'docs/specifications/appendices/부록-L-최종-파라미터-동결표.md'
    'REF' = 'docs/specifications/참고-자료.md'
}

if (-not (Test-Path -LiteralPath $sourcePath)) {
    throw "Integrated specification not found: $sourcePath"
}

$source = [System.IO.File]::ReadAllText($sourcePath)
$headingPattern = '(?m)^# (?:(?<chapter>\d+)\. |부록 (?<appendix>[A-L])\.|(?<references>참고 자료))'
$matches = [regex]::Matches($source, $headingPattern)

$sections = @{}
for ($index = 0; $index -lt $matches.Count; $index++) {
    $match = $matches[$index]
    $key = if ($match.Groups['chapter'].Success) {
        $match.Groups['chapter'].Value
    } elseif ($match.Groups['appendix'].Success) {
        $match.Groups['appendix'].Value
    } else {
        'REF'
    }

    $end = if ($index + 1 -lt $matches.Count) { $matches[$index + 1].Index } else { $source.Length }
    $sections[$key] = $source.Substring($match.Index, $end - $match.Index).TrimEnd() + "`n"
}

$missingSections = @($chapterPaths.Keys | Where-Object { -not $sections.ContainsKey($_) })
if ($missingSections.Count -gt 0) {
    throw "Missing sections in integrated specification: $($missingSections -join ', ')"
}

$generatedHeader = @"
<!--
  GENERATED FILE - DO NOT EDIT DIRECTLY.
  Source: docs/specifications/Sentinel_UGV_최종_통합_명세서_v1.0-rc1.md
  Generator: scripts/docs/split-integrated-spec.ps1
-->

> 기준 문서: Sentinel UGV 통합 명세서 v1.0-rc1 (2026-07-22). 변경은 전체본에 반영한 뒤 분할 스크립트를 실행합니다.

"@
$generatedHeader += "`n"

$differences = @()
$expectedPaths = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$chapterPaths.Values | ForEach-Object {
    [void]$expectedPaths.Add([System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $_)))
}

$staleGeneratedFiles = @(Get-ChildItem -LiteralPath (Join-Path $repositoryRoot 'docs') -Recurse -Filter '*.md' | Where-Object {
    $candidatePath = $_.FullName
    -not $expectedPaths.Contains($candidatePath) -and
    [System.IO.File]::ReadAllText($candidatePath) -match '^<!--\r?\n  GENERATED FILE'
})

if ($Check) {
    $differences += @($staleGeneratedFiles | ForEach-Object { "$($_.FullName) (stale generated file)" })
} else {
    $staleGeneratedFiles | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
}

foreach ($entry in $chapterPaths.GetEnumerator()) {
    $destinationPath = Join-Path $repositoryRoot $entry.Value
    $expected = $generatedHeader + $sections[$entry.Key]

    if ($Check) {
        if (-not (Test-Path -LiteralPath $destinationPath)) {
            $differences += "$($entry.Value) (missing)"
            continue
        }

        $actual = [System.IO.File]::ReadAllText($destinationPath)
        if ($actual -ne $expected) {
            $differences += "$($entry.Value) (out of date)"
        }
        continue
    }

    $destinationDirectory = Split-Path -Parent $destinationPath
    [System.IO.Directory]::CreateDirectory($destinationDirectory) | Out-Null
    [System.IO.File]::WriteAllText($destinationPath, $expected, [System.Text.UTF8Encoding]::new($false))
}

if ($Check -and $differences.Count -gt 0) {
    $differences | ForEach-Object { Write-Error $_ }
    exit 1
}

if ($Check) {
    Write-Output "Verified $($chapterPaths.Count) generated specification files."
} else {
    Write-Output "Generated $($chapterPaths.Count) specification files from $sourceRelativePath."
}
