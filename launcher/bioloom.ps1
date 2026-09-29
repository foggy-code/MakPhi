# Avviatore di BioLoom per Windows: avvia i container Docker, trova il disco con UniClust30,
# applica le licenze e apre il browser. Lo richiamano "Avvia BioLoom.bat" e "Arresta BioLoom.bat".
#
#   bioloom.ps1                       avvio con menu
#   bioloom.ps1 -Azione arresta       arresto
#   bioloom.ps1 -Azione prepara -Destinazione E:\BioLoom   copia portatile (chiavetta) con le immagini
#   bioloom.ps1 -UniClust E:\database   cartella con UniClust30/UniRef30 (qualunque versione; auto = ricerca automatica)
#   opzioni: -NoBrowser -NoMenu (avvio senza interazione, per script e test)
param(
    [ValidateSet("avvia", "arresta", "stato", "log", "verifica", "prepara")]
    [string]$Azione = "avvia",
    [string]$Destinazione = "",
    [switch]$NoBrowser,
    [switch]$NoMenu,
    [switch]$SenzaDati,
    [switch]$SenzaQmean,
    [string]$UniClust = $null
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Compose = Join-Path $PSScriptRoot "compose.yml"
$EnvFile = Join-Path $Root ".env.locale"
$DataDir = Join-Path $Root "data"
$ImagesDir = Join-Path $Root "immagini"
$Version = (Get-Content (Join-Path $PSScriptRoot "VERSION") -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $Version) { $Version = "4.1.0" }
$Version = $Version.Trim()
$script:UniclustChoice = $null
$script:ForceScan = $false
if ($PSBoundParameters.ContainsKey("UniClust")) {
    $script:UniclustChoice = $UniClust.Trim().Trim('"')
    if ($script:UniclustChoice -in @("auto", "automatica")) { $script:UniclustChoice = "" }  # "" si perde tra processi PowerShell
}

function Say([string]$text, [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }
function Title([string]$text) { Write-Host ""; Write-Host "  $text" -ForegroundColor Cyan }
function Fail([string]$text) {
    Write-Host ""
    Write-Host "  [!] $text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Invoke-Docker([string[]]$Arguments, [switch]$Quiet) {
    # Docker scrive i messaggi di avanzamento su stderr: in PowerShell 5.1 non vanno trattati come errori.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) {
            $out = & docker @Arguments 2>&1 | ForEach-Object { "$_" }
            return @{ Code = $LASTEXITCODE; Output = ($out -join "`n") }
        }
        & docker @Arguments 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
        return @{ Code = $LASTEXITCODE; Output = "" }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Compose([string[]]$Arguments, [switch]$Quiet) {
    $base = @("compose", "-f", $Compose, "--env-file", $EnvFile)
    return Invoke-Docker ($base + $Arguments) -Quiet:$Quiet
}

# ---------------------------------------------------------------------------------------- configurazione locale
function Read-EnvFile {
    $values = @{}
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content $EnvFile) {
            if ($line -match '^\s*([A-Z_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
        }
    }
    return $values
}

function Write-EnvFile($values) {
    $lines = @("# Generato dall'avviatore di BioLoom: configurazione di questo computer (non va copiata altrove).")
    foreach ($k in ($values.Keys | Sort-Object)) { $lines += "$k=$($values[$k])" }
    [IO.File]::WriteAllLines($EnvFile, $lines, (New-Object Text.UTF8Encoding $false))
}

# ---------------------------------------------------------------------------------------- Docker
function Ensure-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Say "  Docker non è installato su questo computer." Yellow
        Say "  BioLoom gira dentro Docker: installa Docker Desktop (gratuito per uso personale e accademico)," Yellow
        Say "  riavvia il computer se richiesto e poi fai di nuovo doppio clic su 'Avvia BioLoom'." Yellow
        if (-not $NoMenu) {
            $answer = Read-Host "  Aprire la pagina di download di Docker Desktop? (S/n)"
            if ($answer -notmatch '^[nN]') { Start-Process "https://www.docker.com/products/docker-desktop/" }
        }
        exit 1
    }
    $probe = Invoke-Docker @("info", "--format", "{{.ServerVersion}}") -Quiet
    if ($probe.Code -eq 0) { return }
    $desktop = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"),
        (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $desktop) { Fail "Docker è installato ma non è in esecuzione: avvialo e riprova." }
    Write-Host "  Avvio di Docker Desktop (può richiedere un minuto)" -ForegroundColor Yellow -NoNewline
    Start-Process $desktop | Out-Null
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 2
        Write-Host "." -NoNewline -ForegroundColor Yellow
        if ((Invoke-Docker @("info", "--format", "{{.ServerVersion}}") -Quiet).Code -eq 0) { Write-Host ""; return }
    }
    Write-Host ""
    Fail "Docker Desktop non ha risposto entro 3 minuti. Aprilo manualmente, attendi che sia pronto e riprova."
}

function Test-Image([string]$name) {
    return (Invoke-Docker @("image", "inspect", $name, "--format", "{{.Id}}") -Quiet).Code -eq 0
}

function Ensure-Image([string]$name, [string]$tarName, [string]$service) {
    if (Test-Image $name) { return $true }
    $tar = Join-Path $ImagesDir $tarName
    if (Test-Path $tar) {
        Say "  Caricamento dell'immagine $name dalla cartella 'immagini' (qualche minuto)..." Yellow
        if ((Invoke-Docker @("load", "-i", $tar)).Code -eq 0 -and (Test-Image $name)) { return $true }
        Say "  Caricamento non riuscito: provo a costruire l'immagine." Yellow
    }
    if (Test-Path (Join-Path $Root "docker\Dockerfile")) {
        Say "  Prima installazione: costruzione dell'immagine $name." Yellow
        Say "  Serve internet e richiede 10-20 minuti; le volte successive l'avvio è immediato." Yellow
        if ((Compose @("build", $service)).Code -eq 0) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------------------- UniClust30/UniRef30 e QMTL
# Qualunque versione: HH-suite 3 (<nome>_a3m.ffindex) o HH-suite 2 (<nome>_a3m_db.index), anche più versioni.
# Il worker QMEAN riconosce formati e versioni; qui basta trovare le cartelle e montare quella che le contiene.
function Get-UniclustNames([string]$dir) {
    $names = @()
    foreach ($pattern in @("*_a3m.ffindex", "*_a3m_db.index")) {
        foreach ($f in Get-ChildItem -Path (Join-Path $dir $pattern) -File -ErrorAction SilentlyContinue) {
            if ($f.Length -gt 0) { $names += ($f.Name -replace '_a3m(_db)?\.(ffindex|index)$', '') }
        }
    }
    return @($names | Select-Object -Unique)
}

function Test-Uniclust([string]$dir) {
    return [bool]($dir -and (Test-Path -LiteralPath $dir -PathType Container) -and (Get-UniclustNames $dir).Count -gt 0)
}

function Get-UniclustBelow([string]$root) {
    # cartelle con un database dentro $root, fino a 3 livelli (come il worker, che ne esplora 4)
    if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) { return @() }
    $all = @($root)
    $level = @($root)
    for ($depth = 0; $depth -lt 3; $depth++) {
        $next = @()
        foreach ($p in $level) {
            $next += @(Get-ChildItem -LiteralPath $p -Directory -ErrorAction SilentlyContinue | Where-Object { -not $_.Name.StartsWith(".") } | ForEach-Object { $_.FullName })
        }
        $all += $next
        $level = $next
    }
    return @($all | Where-Object { Test-Uniclust $_ })
}

function Get-VersionKey([string]$text) {
    if ($text -match '(19|20)(\d{2})[_\-.]?(\d{2})') { return "$($Matches[1])$($Matches[2])$($Matches[3])" }
    return "000000"
}

function Get-CommonParent([string[]]$paths) {
    $first = $paths[0].TrimEnd('\') -split '\\'
    $count = $first.Count
    foreach ($p in $paths) {
        $parts = $p.TrimEnd('\') -split '\\'
        $i = 0
        while ($i -lt [Math]::Min($count, $parts.Count) -and $first[$i] -ieq $parts[$i]) { $i++ }
        $count = $i
    }
    $common = ($first[0..([Math]::Max(0, $count - 1))] -join '\')
    if ($common -match '^[A-Za-z]:$') { $common += '\' }
    return $common
}

function Test-Qmtl([string]$dir) {
    return $dir -and (Test-Path -LiteralPath (Join-Path $dir "VERSION")) -and (Test-Path -LiteralPath (Join-Path $dir "indexer.dat"))
}

function Get-CandidateDirs {
    $skip = @('Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', '$Recycle.Bin', 'System Volume Information', 'Recovery', 'PerfLogs', 'AppData', 'node_modules')
    foreach ($drive in Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue) {
        if (-not $drive.Root -or -not (Test-Path $drive.Root)) { continue }
        $level1 = @(Get-ChildItem -LiteralPath $drive.Root -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $skip -notcontains $_.Name })
        foreach ($d in $level1) {
            $d.FullName
            foreach ($d2 in Get-ChildItem -LiteralPath $d.FullName -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $skip -notcontains $_.Name }) {
                $d2.FullName
                foreach ($d3 in Get-ChildItem -LiteralPath $d2.FullName -Directory -ErrorAction SilentlyContinue) { $d3.FullName }
            }
        }
    }
}

function Find-Databases($saved) {
    # Restituisce la cartella da montare (che contiene tutte le versioni trovate sullo stesso disco), i nomi
    # delle versioni, la QMTL e gli eventuali archivi non estratti.
    $result = @{ Uniclust = ""; Qmtl = ""; Names = @(); Archives = @(); Manual = ($saved["UNICLUST_MANUAL"] -eq "1") }
    $savedPath = $saved["UNICLUST_PATH"]
    if ($script:ForceScan -and -not $result.Manual) { $savedPath = "" }  # R: nuova ricerca sui dischi
    $below = Get-UniclustBelow $savedPath
    if ($below.Count) {
        $result.Uniclust = $savedPath
        $result.Names = @($below | ForEach-Object { Get-UniclustNames $_ } | Select-Object -Unique)
    } elseif ($result.Manual -and $savedPath) {
        Say "  La cartella indicata per UniClust30 ($savedPath) non è raggiungibile: cerco sui dischi." Yellow
    }
    if (Test-Qmtl $saved["QMTL_PATH"]) { $result.Qmtl = $saved["QMTL_PATH"] }
    if ($result.Uniclust -and ($result.Qmtl -or -not $saved["QMTL_PATH"])) { return $result }
    Say "  Ricerca di UniClust30/UniRef30 sui dischi collegati..." DarkGray
    $found = @()
    foreach ($dir in Get-CandidateDirs) {
        if (-not $result.Uniclust -and (Test-Uniclust $dir)) { $found += $dir }
        if (-not $result.Qmtl -and (Test-Qmtl $dir)) { $result.Qmtl = $dir }
        if (-not $result.Uniclust) {
            foreach ($a in Get-ChildItem -LiteralPath $dir -Filter "*.tar.gz" -File -ErrorAction SilentlyContinue) {
                if ($a.Name -match 'uniclust|uniref|hhsuite') { $result.Archives += $a.FullName }
            }
        }
    }
    if (-not $result.Uniclust -and $found.Count) {
        # più versioni: si monta la cartella comune a quelle del disco che ha la versione più recente
        $best = $found | Sort-Object { (Get-UniclustNames $_ | ForEach-Object { Get-VersionKey $_ } | Sort-Object -Descending | Select-Object -First 1) } -Descending | Select-Object -First 1
        $drive = [IO.Path]::GetPathRoot($best)
        $sameDrive = @($found | Where-Object { [IO.Path]::GetPathRoot($_) -ieq $drive })
        $result.Uniclust = if ($sameDrive.Count -gt 1) { Get-CommonParent $sameDrive } else { $best }
        $result.Names = @($sameDrive | ForEach-Object { Get-UniclustNames $_ } | Select-Object -Unique)
        $result.Manual = $false
    }
    return $result
}

function Read-UniclustPath {
    Say "  Indica la cartella che contiene UniClust30/UniRef30 (anche la cartella superiore, se ci sono più versioni)." White
    $path = (Read-Host "  Percorso (invio = ricerca automatica)").Trim().Trim('"')
    if (-not $path) { return "" }
    if (-not (Get-UniclustBelow $path).Count) {
        Say "  Nessun database HH-suite in $path (cerco file *_a3m.ffindex o *_a3m_db.index)." Yellow
        return $null
    }
    return $path
}

# ---------------------------------------------------------------------------------------- licenze e porta
function Get-ProsaMac {
    $lic = Join-Path $DataDir "licenses\prosa\LicenseDB"
    if (-not (Test-Path $lic)) { return "" }
    foreach ($line in Get-Content $lic) {
        $fields = $line.Trim() -split '\s+'
        if ($fields.Count -ge 4 -and $fields[2] -match '^[0-9A-Fa-f]{12}$') {
            $h = $fields[2].ToLower()
            return ($h.Substring(0, 2), $h.Substring(2, 2), $h.Substring(4, 2), $h.Substring(6, 2), $h.Substring(8, 2), $h.Substring(10, 2)) -join ":"
        }
    }
    return ""
}

function New-LocalMac {
    $bytes = New-Object byte[] 5
    (New-Object Random).NextBytes($bytes)
    return "02:" + (($bytes | ForEach-Object { $_.ToString("x2") }) -join ":")
}

function Test-PortFree([int]$port) {
    $listener = $null
    try {
        $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

function Test-Running {
    $ps = Compose @("ps", "--status", "running", "--services") -Quiet
    return ($ps.Output -split "`r?`n") -contains "bioloom"
}

function Wait-Healthy([int]$port) {
    for ($i = 0; $i -lt 120; $i++) {
        try {
            $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$port/api/health"
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

# ---------------------------------------------------------------------------------------- azioni
function Start-BioLoom {
    Title "BioLoom $Version"
    Ensure-Docker
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $probe = Join-Path $DataDir ".scrittura"
    try { Set-Content -Path $probe -Value "ok"; Remove-Item $probe -Force } catch { Fail "La cartella dati $DataDir non è scrivibile (chiavetta protetta da scrittura?)." }

    $saved = Read-EnvFile
    if ($script:UniclustChoice -ne $null) {
        # cartella indicata a mano (-UniClust o menu U): vuota = torna alla ricerca automatica
        $saved["UNICLUST_PATH"] = $script:UniclustChoice
        $saved["UNICLUST_MANUAL"] = if ($script:UniclustChoice) { "1" } else { "" }
    }
    $cfg = @{}
    $cfg["BIOLOOM_VERSION"] = $Version
    $dbs = Find-Databases $saved
    $mac = Get-ProsaMac
    if ($mac) { $cfg["BIOLOOM_MAC"] = $mac }
    elseif ($saved["BIOLOOM_MAC"] -and -not $saved["BIOLOOM_MAC_FROM_LICENSE"]) { $cfg["BIOLOOM_MAC"] = $saved["BIOLOOM_MAC"] }
    else { $cfg["BIOLOOM_MAC"] = New-LocalMac }
    if ($mac) { $cfg["BIOLOOM_MAC_FROM_LICENSE"] = "1" }

    $port = 8765
    if ($saved["BIOLOOM_PORT"]) { $port = [int]$saved["BIOLOOM_PORT"] }
    $current = $saved.Clone()
    $current["BIOLOOM_VERSION"] = $Version
    Write-EnvFile $current  # per interrogare lo stato dei container con la configurazione attuale
    $running = Test-Running
    if (-not $running) {
        while (-not (Test-PortFree $port) -and $port -lt 8800) { $port++ }
    }
    $cfg["BIOLOOM_PORT"] = "$port"
    if ($dbs.Uniclust) {
        $cfg["UNICLUST_PATH"] = $dbs.Uniclust
        if ($dbs.Manual) { $cfg["UNICLUST_MANUAL"] = "1" }
        $cfg["COMPOSE_PROFILES"] = "qmean"
        $cfg["BIOLOOM_QMEAN_URL"] = "http://qmean:8080"
        if ($dbs.Qmtl) { $cfg["QMTL_PATH"] = $dbs.Qmtl }
    }
    Write-EnvFile $cfg

    if (-not (Ensure-Image "bioloom:$Version" "bioloom-$Version.tar" "bioloom")) {
        Fail "Immagine BioLoom non disponibile: copia la cartella 'immagini' dalla chiavetta originale oppure collegati a internet."
    }
    if ($dbs.Uniclust -and -not (Ensure-Image "bioloom-qmean:$Version" "bioloom-qmean-$Version.tar" "qmean")) {
        Say "  Immagine QMEAN non disponibile: QMEAN locale resta spento." Yellow
        $cfg.Remove("COMPOSE_PROFILES"); $cfg.Remove("UNICLUST_PATH"); $cfg.Remove("QMTL_PATH"); $cfg.Remove("BIOLOOM_QMEAN_URL")
        Write-EnvFile $cfg
    }

    Say "  Avvio dei container..." DarkGray
    if (-not $cfg["COMPOSE_PROFILES"]) { Compose @("--profile", "qmean", "rm", "-sf", "qmean") -Quiet | Out-Null }
    if ((Compose @("up", "-d", "--remove-orphans")).Code -ne 0) { Fail "Docker non è riuscito ad avviare BioLoom (vedi i messaggi sopra)." }
    if (-not (Wait-Healthy $port)) { Fail "BioLoom non risponde. Log: 'Arresta BioLoom' e poi di nuovo 'Avvia BioLoom'; oppure docker compose logs bioloom." }

    $url = "http://127.0.0.1:$port"
    Title "BioLoom è pronto: $url"
    Say "  Dati:        $DataDir"
    if ($dbs.Uniclust) {
        $versions = if ($dbs.Names.Count) { ($dbs.Names -join ", ") } else { "?" }
        Say "  QMEAN:       locale, $versions in $($dbs.Uniclust)$(if ($dbs.Qmtl) { " + QMTL (QMEANDisCo)" })" Green
        if ($dbs.Names.Count -gt 1) { Say "               versione da usare: Impostazioni > Database (predefinita la più recente)" DarkGray }
    } elseif ($dbs.Archives.Count) {
        Say "  QMEAN:       trovato l'archivio $($dbs.Archives[0]) ma non è estratto: estrailo sul disco e usa R (Riavvia)" DarkYellow
    } else {
        Say "  QMEAN:       locale spento (nessun disco con UniClust30/UniRef30 collegato; U per indicare la cartella)" DarkYellow
    }
    Say "  ProSA:       $(if ($mac) { "licenza trovata, MAC applicato" } else { "nessuna licenza (si carica da Impostazioni > Licenze)" })"
    if (-not $NoBrowser) { Start-Process $url }
    if (-not $NoMenu) { Show-Menu $url }
}

function Show-Menu([string]$url) {
    while ($true) {
        Write-Host ""
        Say "  [A] Apri nel browser   [V] Verifica strumenti   [L] Log   [R] Riavvia (rileva di nuovo i dischi)" White
        Say "  [U] Indica la cartella di UniClust30/UniRef30   [S] Arresta BioLoom ed esci" White
        Say "  [C] Chiudi questa finestra (BioLoom resta attivo)" White
        $key = (Read-Host "  Scelta").Trim().ToUpper()
        switch ($key) {
            "A" { Start-Process $url }
            "V" { Compose @("exec", "bioloom", "python", "-m", "bioloom.selftest") | Out-Null }
            "L" { Compose @("logs", "--tail", "60") | Out-Null }
            "R" { $script:ForceScan = $true; Start-BioLoom; return }
            "U" {
                $choice = Read-UniclustPath
                if ($choice -ne $null) { $script:UniclustChoice = $choice; Start-BioLoom; return }
            }
            "S" { Stop-BioLoom; return }
            "C" { return }
        }
    }
}

function Stop-BioLoom {
    if (-not (Test-Path $EnvFile)) { Write-EnvFile @{ BIOLOOM_VERSION = $Version } }
    Say "  Arresto di BioLoom (i calcoli in corso vengono interrotti)..." Yellow
    Compose @("--profile", "qmean", "down") | Out-Null
    Say "  BioLoom è arrestato. I dati restano in $DataDir." Green
}

function Export-Portable {
    if (-not $Destinazione) {
        if ($NoMenu) { Fail "Indica la destinazione con -Destinazione." }
        $Destinazione = Read-Host "  Cartella di destinazione (es. E:\BioLoom sulla chiavetta)"
    }
    if (-not $Destinazione) { Fail "Nessuna destinazione." }
    Ensure-Docker
    New-Item -ItemType Directory -Force -Path $Destinazione | Out-Null
    $dest = (Resolve-Path $Destinazione).Path
    Title "Preparazione della copia portatile in $dest"
    $files = @("Avvia BioLoom.bat", "Arresta BioLoom.bat", "Avvia BioLoom.command", "Arresta BioLoom.command", "avvia-bioloom.sh",
               "bioloom.py", "README.md", "LEGGIMI.txt", ".dockerignore", ".gitattributes", ".gitignore")
    foreach ($f in $files) { if (Test-Path (Join-Path $Root $f)) { Copy-Item (Join-Path $Root $f) $dest -Force } }
    $dirs = @{ "launcher" = @(); "docker" = @(); "docs" = @(); "backend" = @("__pycache__", ".pytest_cache"); "frontend" = @("node_modules", "dist") }
    foreach ($d in $dirs.Keys) {
        $rcArgs = @((Join-Path $Root $d), (Join-Path $dest $d), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
        if ($dirs[$d].Count) { $rcArgs += "/XD"; $rcArgs += $dirs[$d] }
        robocopy @rcArgs | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "Copia di $d non riuscita." }
    }
    if (-not $SenzaDati -and (Test-Path $DataDir)) {
        Say "  Copia dei dati (progetti, risultati, licenze, database)..." DarkGray
        robocopy $DataDir (Join-Path $dest "data") /E /XD .scratch .localcache /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "Copia dei dati non riuscita." }
    }
    $imgDir = Join-Path $dest "immagini"
    New-Item -ItemType Directory -Force -Path $imgDir | Out-Null
    $images = @(, @("bioloom:$Version", "bioloom-$Version.tar", "bioloom"))
    if (-not $SenzaQmean) { $images += , @("bioloom-qmean:$Version", "bioloom-qmean-$Version.tar", "qmean") }
    foreach ($img in $images) {
        if (-not (Ensure-Image $img[0] $img[1] $img[2])) { Say "  Immagine $($img[0]) non disponibile: non inclusa." Yellow; continue }
        Say "  Salvataggio dell'immagine $($img[0]) (qualche minuto)..." DarkGray
        if ((Invoke-Docker @("save", "-o", (Join-Path $imgDir $img[1]), $img[0])).Code -ne 0) { Fail "Salvataggio di $($img[0]) non riuscito." }
    }
    Title "Copia portatile pronta: $dest"
    Say "  Sul computer di destinazione serve solo Docker Desktop: doppio clic su 'Avvia BioLoom'."
}

switch ($Azione) {
    "avvia" { Start-BioLoom }
    "arresta" { Stop-BioLoom }
    "stato" { if (-not (Test-Path $EnvFile)) { Write-EnvFile @{ BIOLOOM_VERSION = $Version } }; Compose @("--profile", "qmean", "ps") | Out-Null }
    "log" { Compose @("logs", "--tail", "200") | Out-Null }
    "verifica" { Compose @("exec", "bioloom", "python", "-m", "bioloom.selftest") | Out-Null }
    "prepara" { Export-Portable }
}
