# Avviatore di BioLoom per Windows: avvia i container Docker, trova il disco con UniClust30,
# applica le licenze e apre il browser. Lo richiamano "Avvia BioLoom.bat" e "Arresta BioLoom.bat".
#
#   bioloom.ps1                       avvio con menu
#   bioloom.ps1 -Azione arresta       arresto
#   bioloom.ps1 -Azione prepara -Destinazione E:\BioLoom   copia portatile (chiavetta) con le immagini
#   bioloom.ps1 -Azione aggiorna      porta le immagini al codice in programma\ (file in immagini\ o costruzione) e,
#                                     se BioLoom è attivo, lo riavvia
#   bioloom.ps1 -UniClust E:\database   cartella con UniClust30/UniRef30 (qualunque versione; auto = ricerca automatica)
#   opzioni: -NoBrowser -NoMenu (avvio senza interazione, per script e test)
#            -SenzaDati -SenzaQmean (copia portatile senza dati o senza l'immagine di QMEAN)
#            -ConLicenze  la copia portatile include chiavi di licenza, posta.env e chiave d'accesso (solo per sé)
#            -SpostaProsa  sposta su questo computer, senza chiedere, la licenza ProSA attiva su un altro
#   porta: la prima libera da 8765 a 8800 (si riparte dall'ultima usata); la variabile d'ambiente BIOLOOM_PORT ne chiede
#          una precisa (se è occupata ci si ferma)
param(
    [ValidateSet("avvia", "arresta", "stato", "log", "verifica", "prepara", "aggiorna")]
    [string]$Azione = "avvia",
    [string]$Destinazione = "",
    [switch]$NoBrowser,
    [switch]$NoMenu,
    [switch]$SenzaDati,
    [switch]$SenzaQmean,
    [switch]$ConLicenze,
    [switch]$SpostaProsa,
    [string]$UniClust = $null
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot   # programma\: il codice
$Base = Split-Path -Parent $Root           # cartella di BioLoom: avviatori, Guida, dati, immagini
$Compose = Join-Path $PSScriptRoot "compose.yml"
$EnvFile = Join-Path $Base ".env.locale"
$DataDir = Join-Path $Base "data"
$ImagesDir = Join-Path $Base "immagini"
# -LiteralPath qui e altrove: con -Path una cartella con le parentesi quadre nel nome (es. "Lab [2026]") verrebbe
# presa per un'espressione con caratteri jolly e i file non si troverebbero
$Version = (Get-Content -LiteralPath (Join-Path $PSScriptRoot "VERSION") -ErrorAction SilentlyContinue | Select-Object -First 1)   # unica fonte della versione
if ($Version) { $Version = $Version.Trim() }
# comando da suggerire nei messaggi: quello della Guida (più -NoProfile, come il .bat), con il percorso completo. Va sia nel
# Prompt dei comandi sia in PowerShell (la console predefinita di Terminale); "Avvia BioLoom.bat" seguito dagli
# argomenti funzionerebbe solo nel Prompt dei comandi
$LaunchCmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
$Impronta = Join-Path $PSScriptRoot "impronta.ps1"   # Get-BioLoomImpronta: impronta del codice in programma\
if (Test-Path -LiteralPath $Impronta) { . $Impronta }
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

if (-not $Version) { Fail "Manca il file programma\launcher\VERSION (versione di BioLoom): la cartella programma è incompleta, copiala di nuovo." }
# Le variabili d'ambiente prevalgono su --env-file: una BIOLOOM_VERSION vecchia in .env.locale non conta
$env:BIOLOOM_VERSION = $Version
# Porta chiesta con BIOLOOM_PORT. Non resta nell'ambiente: docker compose la preferirebbe alla porta che l'avviatore
# sceglie e scrive in .env.locale (e "aggiorna" non riavvierebbe BioLoom com'era)
$RequestedPort = "$env:BIOLOOM_PORT".Trim()
Remove-Item Env:BIOLOOM_PORT -ErrorAction SilentlyContinue

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
    if (Test-Path -LiteralPath $EnvFile) {
        foreach ($line in Get-Content -LiteralPath $EnvFile) {
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

function Ensure-EnvFile {  # .env.locale minimo, se manca: docker compose --env-file non parte senza il file
    if (-not (Test-Path -LiteralPath $EnvFile)) { Write-EnvFile @{ BIOLOOM_VERSION = $Version } }
}

# ---------------------------------------------------------------------------------------- Docker
function Ensure-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Say "  Docker non è installato su questo computer." Yellow
        Say "  BioLoom gira dentro Docker: installa Docker Desktop, riavvia il computer se richiesto e poi fai di nuovo" Yellow
        Say "  doppio clic su 'Avvia BioLoom'." Yellow
        # termini di Docker: abbonamento a pagamento per l'uso professionale negli enti pubblici, di qualunque dimensione,
        # e nelle organizzazioni con almeno 250 dipendenti o almeno 10 milioni di dollari di fatturato annuo (gratuito per
        # uso personale, didattico e nelle aziende piccole)
        Say "  Docker Desktop è gratuito per uso personale, didattico e nelle aziende piccole (meno di 250 dipendenti e meno" Yellow
        Say "  di 10 milioni di dollari di fatturato annuo); negli enti pubblici, anche piccoli, e nelle altre organizzazioni," Yellow
        Say "  se l'uso non è didattico, verifica la licenza (https://www.docker.com/pricing/). In alternativa un programma" Yellow
        Say "  che dia i comandi docker e docker compose (non provati con BioLoom): Rancher Desktop o Podman Desktop con la" Yellow
        Say "  compatibilità Docker." Yellow
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
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
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

$script:LoadedTar = ""   # ultimo file di immagini\ caricato: aggiornare l'immagine non lo ricarica

function Ensure-Image([string]$name, [string]$tarName, [string]$service) {
    if (Test-Image $name) { return $true }
    $tar = Join-Path $ImagesDir $tarName
    if (Test-Path -LiteralPath $tar) {
        Say "  Caricamento dell'immagine $name dalla cartella 'immagini' (qualche minuto)..." Yellow
        if ((Invoke-Docker @("load", "-i", $tar)).Code -eq 0 -and (Test-Image $name)) { $script:LoadedTar = $tar; return $true }
        Say "  Caricamento non riuscito: provo a costruire l'immagine." Yellow
    }
    if (Test-Path -LiteralPath (Join-Path $Root "docker\Dockerfile")) {
        Say "  Prima installazione: costruzione dell'immagine $name." Yellow
        Say "  Serve internet e richiede 10-20 minuti; le volte successive l'avvio è immediato." Yellow
        if ((Compose @("build", $service)).Code -eq 0) { return $true }
    }
    return $false
}

function Get-ImageId([string]$name) {  # identificativo dell'immagine, "" se manca
    $r = Invoke-Docker @("image", "inspect", $name, "--format", "{{.Id}}") -Quiet
    if ($r.Code -ne 0) { return "" }
    return "$(@($r.Output -split "`r?`n" | Where-Object { $_ -match '^sha256:[0-9a-f]+$' }) | Select-Object -Last 1)"
}

# ---------------------------------------------------------------------------------------- impronta del codice
# impronta.ps1 (stessa definizione di docker\impronta.sh) riassume in un hash i file di backend\, frontend\ e docker\
# che entrano nell'immagine e la build salva lo stesso hash in /app/IMPRONTA: se i due valori differiscono, il codice
# in programma\ è cambiato (anche a parità di versione) senza ricostruire l'immagine. Se non si può calcolare
# (impronta.ps1 assente o file illeggibili) il controllo si salta.
$script:CodeImpronta = $null
$script:CodeImprontaSet = $false

function Get-CodeImpronta {  # impronta del codice in programma\, calcolata una volta sola ($null se non si può)
    if (-not $script:CodeImprontaSet) {
        $script:CodeImprontaSet = $true
        if (Get-Command Get-BioLoomImpronta -ErrorAction SilentlyContinue) {
            try { $script:CodeImpronta = Get-BioLoomImpronta $Root } catch { $script:CodeImpronta = $null }
        }
        if ("$($script:CodeImpronta)" -cnotmatch '^[0-9a-f]{64}$') { $script:CodeImpronta = $null }
    }
    return $script:CodeImpronta
}

function Get-ImageImpronta([string]$name) {
    # impronta salvata nell'immagine: "" se l'immagine manca o non ha /app/IMPRONTA (immagini precedenti),
    # $null se Docker non ha potuto avviare il container (non si sa)
    if (-not (Test-Image $name)) { return "" }   # senza immagine "docker run" la cercherebbe su Docker Hub
    $r = Invoke-Docker @("run", "--rm", "--platform", "linux/amd64", "--entrypoint", "cat", $name, "/app/IMPRONTA") -Quiet
    if ($r.Code -eq 0) {
        # l'uscita contiene anche stderr (avvisi di Docker): conta solo la riga con l'impronta
        return "$(@($r.Output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -cmatch '^[0-9a-f]{64}$' }) | Select-Object -First 1)"
    }
    if ($r.Code -eq 1) { return "" }   # cat: file assente
    return $null
}

function Test-ImageOutdated([string]$name) {  # vero se l'immagine non corrisponde al codice in programma\ (falso se non si sa)
    $code = Get-CodeImpronta
    if (-not $code) { return $false }
    $image = Get-ImageImpronta $name
    if ($null -eq $image) { return $false }
    return $image -ne $code
}

function Test-ImageCurrent([string]$name) {  # vero se si sa che l'immagine corrisponde al codice in programma\
    $code = Get-CodeImpronta
    if (-not $code) { return $false }
    $image = Get-ImageImpronta $name
    return ($null -ne $image) -and ($image -eq $code)
}

function Get-VendorMissing {  # strumenti con licenza che una costruzione non includerebbe (pacchetti assenti in docker\vendor\)
    $missing = @()
    if (-not (Test-Path -LiteralPath (Join-Path $Root "docker\vendor\procheck\procheck.tar.gz") -PathType Leaf)) { $missing += "PROCHECK" }
    if (-not (Test-Path -LiteralPath (Join-Path $Root "docker\vendor\prosa\prosa2003.linux.tar.gz") -PathType Leaf)) { $missing += "ProSA" }
    return ($missing -join " e ")
}

$script:VendorWarned = $false
function Warn-Vendor {  # avviso, una volta sola, prima di una costruzione che perderebbe PROCHECK o ProSA; vero se mancano
    $missing = Get-VendorMissing
    if (-not $missing) { return $false }
    if (-not $script:VendorWarned) {
        $script:VendorWarned = $true
        Say "  La nuova immagine non avrà ${missing}: i pacchetti non sono in programma\docker\vendor\ (vedi il README lì)." Yellow
    }
    return $true
}

function Update-BioLoomImage {  # porta bioloom:<versione> al codice: prima il file in immagini\, poi la costruzione
    $img = "bioloom:$Version"
    $tar = Join-Path $ImagesDir "bioloom-$Version.tar"
    if ((Test-Path -LiteralPath $tar) -and $script:LoadedTar -ne $tar) {
        Say "  Caricamento dell'immagine $img dalla cartella 'immagini' (qualche minuto)..." Yellow
        if ((Invoke-Docker @("load", "-i", $tar)).Code -eq 0 -and (Test-Image $img)) {
            $script:LoadedTar = $tar
            if (-not (Test-ImageOutdated $img)) { Say "  Immagine $img aggiornata dalla cartella 'immagini'." Green; return $true }
            Say "  Anche l'immagine nella cartella 'immagini' viene da un codice diverso." Yellow
        } else {
            Say "  Caricamento non riuscito." Yellow
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Root "docker\Dockerfile"))) {
        Say "  L'immagine $img non corrisponde al codice in programma\ e non si può ricostruire (manca programma\docker\Dockerfile)." Yellow
        return $false
    }
    Say "  Costruzione dell'immagine $img dal codice in programma\ (serve internet; pochi minuti, 10-20 la prima volta)." Yellow
    [void](Warn-Vendor)
    if ((Compose @("build", "bioloom")).Code -ne 0) { Say "  Costruzione dell'immagine $img non riuscita (vedi i messaggi sopra)." Yellow; return $false }
    if (Test-ImageOutdated $img) { Say "  L'immagine $img costruita non corrisponde ancora al codice in programma\." Yellow; return $false }
    Say "  Immagine $img ricostruita dal codice in programma\." Green
    return $true
}

function Update-QmeanImage([string]$uniclust) {  # QMEAN si aggiorna se è in uso (immagine o UniClust configurato) o se c'è il suo file
    $img = "bioloom-qmean:$Version"
    $tar = Join-Path $ImagesDir "bioloom-qmean-$Version.tar"
    if (-not ((Test-Image $img) -or (Test-Path -LiteralPath $tar) -or $uniclust)) { return }
    if (Test-Path -LiteralPath $tar) {
        Say "  Caricamento dell'immagine $img dalla cartella 'immagini' (qualche minuto)..." Yellow
        if ((Invoke-Docker @("load", "-i", $tar)).Code -eq 0 -and (Test-Image $img)) { Say "  Immagine $img aggiornata dalla cartella 'immagini'." Green; return }
        Say "  Caricamento non riuscito: provo a costruire l'immagine." Yellow
    }
    if (Test-Path -LiteralPath (Join-Path $Root "docker\qmean\Dockerfile")) {
        Say "  Costruzione dell'immagine $img (serve internet)..." Yellow
        if ((Compose @("--profile", "qmean", "build", "qmean")).Code -eq 0) { Say "  Immagine $img aggiornata." Green; return }
    }
    Say "  Immagine $img non aggiornata: QMEAN locale resta con l'immagine che aveva (se c'era)." Yellow
}

function Check-Image {  # all'avvio, dopo Ensure-Image: immagine costruita da un codice diverso da quello in programma\
    if (-not (Test-ImageOutdated "bioloom:$Version")) { return }
    if (-not $NoMenu) {
        Say "  L'immagine di BioLoom non corrisponde al codice in programma\ (aggiornato senza ricostruire l'immagine)." Yellow
        # senza un file in immagini\ da caricare si ricostruisce: se così si perdono PROCHECK o ProSA, Invio vale "no"
        $tar = Join-Path $ImagesDir "bioloom-$Version.tar"
        $default = $true
        if (((-not (Test-Path -LiteralPath $tar -PathType Leaf)) -or $script:LoadedTar -eq $tar) -and (Warn-Vendor)) { $default = $false }
        $ask = if ($default) { "[S/n]" } else { "[s/N]" }
        $answer = Read-Host "  Aggiornarla ora? $ask"
        if ($null -eq $answer) { $answer = "n" }   # fine dell'input (nessuna tastiera): no
        $answer = $answer.Trim()
        if ($answer -match '^[sS]') { $yes = $true }
        elseif ($answer -match '^[nN]') { $yes = $false }
        else { $yes = $default }
        if ($yes -and (Update-BioLoomImage)) { return }
        Say "  Avvio con l'immagine esistente: per aggiornarla usa $LaunchCmd -Azione aggiorna" Yellow
        return
    }
    Say "  L'immagine di BioLoom non corrisponde al codice in programma\ (aggiornato senza ricostruire l'immagine):" Yellow
    Say "  per aggiornarla usa $LaunchCmd -Azione aggiorna. Avvio con l'immagine esistente." Yellow
}

function Test-StaleRunning {  # vero se un container di BioLoom in esecuzione usa un'immagine diversa da quella attuale
    foreach ($service in @("bioloom", "qmean")) {
        $ps = Compose @("--profile", "qmean", "ps", "-q", "--status", "running", $service) -Quiet
        $id = @($ps.Output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[0-9a-f]{12,64}$' }) | Select-Object -First 1
        if (-not $id) { continue }
        $img = if ($service -eq "qmean") { "bioloom-qmean:$Version" } else { "bioloom:$Version" }
        # immagine attuale assente (aggiornamento non riuscito): riavviare farebbe solo ritentare la costruzione a compose
        $want = Get-ImageId $img
        if (-not $want) { continue }
        $used = Invoke-Docker @("inspect", "--format", "{{.Image}}", $id) -Quiet
        $usedId = "$(@($used.Output -split "`r?`n" | Where-Object { $_ -match '^sha256:[0-9a-f]+$' }) | Select-Object -Last 1)"
        if ($usedId -ne $want) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------------------- UniClust30/UniRef30 e QMTL
# Qualunque versione: HH-suite 3 (<nome>_a3m.ffindex) o HH-suite 2 (<nome>_a3m_db.index), anche più versioni.
# Il worker QMEAN riconosce formati e versioni; qui basta trovare le cartelle e montare quella che le contiene.
function Get-UniclustNames([string]$dir) {
    $names = @()
    foreach ($pattern in @("*_a3m.ffindex", "*_a3m_db.index")) {
        foreach ($f in Get-ChildItem -LiteralPath $dir -Filter $pattern -File -ErrorAction SilentlyContinue) {
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
    # niente Join-Path: in PowerShell 5.1 fallisce se l'unità (disco scollegato) non esiste
    if (-not $dir -or -not (Test-Path -LiteralPath $dir -PathType Container)) { return $false }
    return (Test-Path -LiteralPath ([IO.Path]::Combine($dir, "VERSION"))) -and (Test-Path -LiteralPath ([IO.Path]::Combine($dir, "indexer.dat")))
}

function Get-CandidateDirs {
    # Tutte le unità collegate (qualunque lettera): 4 livelli di cartelle sui dischi dati ed esterni,
    # 3 sul disco di sistema (più lento da esplorare); i dischi esterni per primi.
    $skip = @('Windows', 'Program Files', 'Program Files (x86)', 'ProgramData', '$Recycle.Bin', 'System Volume Information', 'Recovery', 'PerfLogs', 'AppData', 'node_modules', '.git')
    $system = [IO.Path]::GetPathRoot($env:SystemRoot)
    $drives = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Root -and (Test-Path -LiteralPath $_.Root) } | ForEach-Object { $_.Root })
    $drives = @($drives | Sort-Object { if ($_ -ieq $system) { 1 } else { 0 } }, { $_ })
    foreach ($root in $drives) {
        $maxDepth = if ($root -ieq $system) { 3 } else { 4 }
        $level = @($root)
        for ($depth = 0; $depth -lt $maxDepth; $depth++) {
            $next = @()
            foreach ($p in $level) {
                $next += @(Get-ChildItem -LiteralPath $p -Directory -Force -ErrorAction SilentlyContinue |
                    Where-Object { $skip -notcontains $_.Name -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
                    ForEach-Object { $_.FullName })
            }
            $next
            $level = $next
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
    $path = Read-Host "  Percorso (invio = ricerca automatica)"
    if ($null -eq $path) { return $null }   # fine dell'input: nessuna scelta
    $path = $path.Trim().Trim('"')
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
    if (-not (Test-Path -LiteralPath $lic)) { return "" }
    foreach ($line in Get-Content -LiteralPath $lic) {
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

# La licenza ProSA vale per un computer alla volta: il primo computer che applica il MAC di LicenseDB la lega a sé
# in licenses\prosa\computer (una riga "<mac> <computer>" per licenza), che viaggia con la cartella dati; gli altri
# non applicano quel MAC finché la licenza non viene spostata (domanda o -SpostaProsa). Stessa formula degli
# avviatori bash e Podman: su questo Windows Git Bash ottiene lo stesso identificativo.
function Get-ComputerId {  # 16 cifre esadecimali che identificano questo computer ("" se non si ottiene)
    $raw = ""
    try { $raw = "$((Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Cryptography" -Name MachineGuid -ErrorAction Stop).MachineGuid)" } catch { }
    if (-not $raw.Trim()) {
        # PowerShell a 32 bit vede la vista a 32 bit del registro, dove MachineGuid non c'è
        try {
            $hklm = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, [Microsoft.Win32.RegistryView]::Registry64)
            $key = $hklm.OpenSubKey("SOFTWARE\Microsoft\Cryptography")
            if ($key) { $raw = "$($key.GetValue("MachineGuid"))"; $key.Close() }
            $hklm.Close()
        } catch { }
    }
    $raw = $raw.Trim().ToLowerInvariant()
    if (-not $raw) { return "" }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = $sha.ComputeHash((New-Object Text.UTF8Encoding $false).GetBytes("bioloom-computer:$raw")) } finally { $sha.Dispose() }
    return ([BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant().Substring(0, 16)
}

# Nel file il MAC si confronta normalizzato (minuscole, solo cifre esadecimali): una riga corretta a mano con le
# maiuscole o senza i due punti vale come quella scritta dall'avviatore.
function Get-MacKey([string]$text) { return ($text.ToLowerInvariant() -creplace '[^0-9a-f]', '') }

function Get-ProsaComputer([string]$file, [string]$mac) {  # computer (minuscolo) a cui è legata la licenza col MAC $mac ("" se nessuno)
    if (-not (Test-Path -LiteralPath $file)) { return "" }
    $key = Get-MacKey $mac
    foreach ($line in Get-Content -LiteralPath $file) {
        $fields = @($line.Trim() -split '\s+')
        if ((Get-MacKey $fields[0]) -ceq $key) {   # la prima riga della licenza decide, anche se non ha il computer
            if ($fields.Count -ge 2) { return $fields[1].ToLowerInvariant() }
            return ""
        }
    }
    return ""
}

function Set-ProsaComputer([string]$file, [string]$mac, [string]$id) {  # lega la licenza col MAC $mac al computer $id
    # Fine riga LF, come gli avviatori bash. Permessi lasciati come sono: la cartella dati passa da un computer
    # all'altro e un'ACL legata all'utente di questo impedirebbe agli altri di leggere il file.
    try {
        $lines = @()
        if (Test-Path -LiteralPath $file) {
            # tolte le righe della stessa licenza in qualunque forma (maiuscole, senza due punti): sostituite, non duplicate
            $key = Get-MacKey $mac
            $lines = @(Get-Content -LiteralPath $file | Where-Object { $_.Trim() -and (Get-MacKey (($_.Trim() -split '\s+')[0])) -cne $key })
        }
        $lines += "$mac $id"
        $tmp = "$file.tmp"
        [IO.File]::WriteAllText($tmp, (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
        Move-Item -LiteralPath $tmp -Destination $file -Force
        return $true
    } catch {
        return $false
    }
}

$script:ProsaState = ""   # licenza ProSA: "" (nessuna), attivata, questo, spostata, altro, senza-id

function Test-ProsaComputer([string]$mac) {  # vero se il MAC della licenza ProSA si può applicare su questo computer
    $file = Join-Path $DataDir "licenses\prosa\computer"
    $id = Get-ComputerId
    if (-not $id) { $script:ProsaState = "senza-id"; return $true }
    $bound = Get-ProsaComputer $file $mac
    if ($bound -eq $id) { $script:ProsaState = "questo"; return $true }
    if (-not $bound) {
        if (-not (Set-ProsaComputer $file $mac $id)) { Say "  ProSA: impossibile registrare il computer della licenza in $file." Yellow }
        $script:ProsaState = "attivata"; return $true
    }
    Say "  ProSA: la licenza è attiva su un altro computer (vale per un computer alla volta)." Yellow
    Say "  Per usarla qui chiedi una chiave per questo computer, oppure spostala qui (l'altro computer la perde)." Yellow
    $answer = ""
    if ($SpostaProsa) { $answer = "s" }
    elseif (-not $NoMenu) { $answer = Read-Host "  Spostare la licenza ProSA su questo computer? [s/N]" }
    else { Say "  Per spostarla qui rilancia con -SpostaProsa." Yellow }
    if ("$answer".Trim() -match '^[sS]') {
        if (Set-ProsaComputer $file $mac $id) { $script:ProsaState = "spostata"; return $true }
        Say "  ProSA: impossibile registrare lo spostamento in ${file}: licenza non spostata." Yellow
    }
    $script:ProsaState = "altro"
    return $false
}

function Test-PortFree([int]$port) {  # libera se su 127.0.0.1 nessuno risponde e se si può aprire
    # Prima una connessione: chi ascolta su tutte le interfacce (0.0.0.0) risponde subito, ma non sempre impedisce a
    # TcpListener di aprire 127.0.0.1. Su una porta chiusa Windows ritenta per circa 2 s: dopo 300 ms si passa oltre
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        if ($client.ConnectAsync([System.Net.IPAddress]::Loopback, $port).Wait(300) -and $client.Connected) { return $false }
    } catch {
    } finally {
        $client.Close()
    }
    # poi l'apertura, che trova anche le porte riservate da Windows (netsh interface ipv4 show excludedportrange)
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

function Get-ValidPort([string]$text) {  # porta TCP (da 1 a 65535) come intero; 0 se $text non lo è
    $p = 0
    $t = "$text".Trim()
    if ($t -match '^\d{1,5}$' -and [int]::TryParse($t, [ref]$p) -and $p -ge 1 -and $p -le 65535) { return $p }
    return 0
}

function Get-OwnPort([int]$assumed) {  # porta pubblicata dal BioLoom in esecuzione (0 se non è in esecuzione): per lui è
    # libera, compose ricrea il container. $assumed se compose non la dice
    if (-not (Test-Running)) { return 0 }
    $r = Compose @("port", "bioloom", "8765") -Quiet
    $line = @($r.Output -split "`r?`n" | Where-Object { $_ -match ':\d+\s*$' }) | Select-Object -Last 1
    if ($r.Code -eq 0 -and $line -match ':(\d+)\s*$') { return [int]$Matches[1] }
    return $assumed
}

function Find-FreePort([int]$first, [int]$own) {  # prima porta libera: $first (l'ultima usata), poi le seguenti fino a
    # 8800 e da 8765; $own (BioLoom in esecuzione) conta come libera. 0 se sono tutte occupate
    $list = @($first)
    if ($first -ge 8765 -and $first -le 8800) {
        for ($p = $first + 1; $p -le 8800; $p++) { $list += $p }
        for ($p = 8765; $p -lt $first; $p++) { $list += $p }
    } else {
        for ($p = 8765; $p -le 8800; $p++) { $list += $p }
    }
    foreach ($p in $list) { if ($p -eq $own -or (Test-PortFree $p)) { return $p } }
    return 0
}

# come vedere quali programmi usano le porte, per i messaggi
$PortHint = "netstat -ano | findstr LISTENING; il numero in fondo è il processo, in Gestione attività > Dettagli"

function Test-Running {
    $ps = Compose @("ps", "--status", "running", "--services") -Quiet
    return ($ps.Output -split "`r?`n") -contains "bioloom"
}

# ---------------------------------------------------------------------------------------- un BioLoom alla volta
# Le copie di BioLoom su uno stesso computer (per esempio quella installata e quella della chiavetta) hanno lo stesso
# progetto compose "bioloom", quindi gli stessi container e volumi: avviarne una ricreerebbe i container dell'altra con
# un'altra cartella dati. Il progetto non cambia nome da una copia all'altra (il volume cache con l'indice MMseqs2
# resterebbe orfano): prima di avviare si guarda da quale cartella è partito il BioLoom in esecuzione (etichetta
# working_dir che compose mette ai container: la cartella di compose.yml).
function Get-RunningContainerId {  # un container di BioLoom in esecuzione, prima quello di bioloom ("" se nessuno)
    foreach ($a in @(@("ps", "-q", "--status", "running", "bioloom"), @("--profile", "qmean", "ps", "-q", "--status", "running"))) {
        $ps = Compose $a -Quiet
        $id = @($ps.Output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[0-9a-f]{12,64}$' }) | Select-Object -First 1
        if ($id) { return $id }
    }
    return ""
}

function Test-SameDir([string]$a, [string]$b) {  # stessa cartella a meno di maiuscole e separatori (NTFS non li distingue)
    try {
        $fa = [IO.Path]::GetFullPath($a.Replace('/', '\')).TrimEnd('\')
        $fb = [IO.Path]::GetFullPath($b.Replace('/', '\')).TrimEnd('\')
    } catch { return $false }
    return $fa -ieq $fb
}

function Get-InspectJson([string]$id, [string]$field) {  # campo di docker inspect come oggetto ($null se non si legge).
    # {{json ...}} e non {{index ... "etichetta"}}: PowerShell 5.1 toglie le virgolette interne agli argomenti dei programmi
    $r = Invoke-Docker @("inspect", "--format", "{{json $field}}", $id) -Quiet
    if ($r.Code -ne 0) { return $null }
    $line = @($r.Output -split "`r?`n" | Where-Object { $_ -match '^\s*[\[{]' }) | Select-Object -First 1
    if (-not $line) { return $null }
    try { return ($line | ConvertFrom-Json) } catch { return $null }
}

function Get-OtherInstance {  # BioLoom in esecuzione partito da un'altra cartella: @{ Dir; Data }; $null se non è in
    # esecuzione, se è questa copia o se non si può sapere
    $id = Get-RunningContainerId
    if (-not $id) { return $null }
    $labels = Get-InspectJson $id ".Config.Labels"
    if (-not $labels) { return $null }
    $dir = "$($labels.'com.docker.compose.project.working_dir')"
    if (-not $dir -or (Test-SameDir $dir $PSScriptRoot)) { return $null }
    $data = "$(@(Get-InspectJson $id ".Mounts" | Where-Object { $_.Destination -eq "/data" } | ForEach-Object { $_.Source }) | Select-Object -First 1)"
    # Docker Desktop mostra i bind mount con il percorso della sua macchina virtuale (/run/desktop/mnt/host/d/...):
    # si riporta quello di Windows
    if ($data -match '^/(?:run/desktop/mnt/host|host_mnt|mnt/host)/([a-zA-Z])(/.*)?$') {
        $data = "$($Matches[1].ToUpper()):" + $(if ($Matches[2]) { $Matches[2] -replace '/', '\' } else { '\' })
    }
    return @{ Dir = ($dir.TrimEnd('\', '/') -replace '[\\/]programma[\\/]launcher$', ''); Data = $data }
}

function Confirm-SingleInstance([string]$what) {  # prima di avviare: il BioLoom partito da un'altra cartella si arresta
    # solo con il consenso (domanda; con -NoMenu ci si ferma). $what = cosa si annulla, per il messaggio
    $other = Get-OtherInstance
    if (-not $other) { return }
    $data = if ($other.Data) { $other.Data } else { "quella cartella" }
    Say "  È già in esecuzione BioLoom da un'altra cartella: $($other.Dir)" Yellow
    Say "  Le copie di BioLoom su questo computer usano gli stessi container: per continuare va arrestato" Yellow
    Say "  (i calcoli in corso vengono interrotti; i suoi dati restano in $data)." Yellow
    $answer = ""
    if (-not $NoMenu) { $answer = Read-Host "  Arrestarlo e continuare? [s/N]" }
    if ("$answer".Trim() -notmatch '^[sS]') {
        Fail "${what}: resta in esecuzione il BioLoom di $($other.Dir). Per usare questa copia arrestalo prima ($LaunchCmd -Azione arresta) e riprova."
    }
    Say "  Arresto del BioLoom partito da $($other.Dir)..." Yellow
    if ((Compose @("--profile", "qmean", "down", "--remove-orphans")).Code -ne 0) { Fail "Arresto non riuscito (vedi i messaggi sopra)." }
}

function Show-OtherInstance {  # stato, log, verifica: avviso se mostrano il BioLoom di un'altra cartella
    $other = Get-OtherInstance
    if ($other) { Say "  Il BioLoom in esecuzione è partito da un'altra cartella: $($other.Dir)" Yellow }
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
    try { Set-Content -LiteralPath $probe -Value "ok"; Remove-Item -LiteralPath $probe -Force } catch { Fail "La cartella dati $DataDir non è scrivibile (chiavetta protetta da scrittura?)." }
    Ensure-EnvFile
    Confirm-SingleInstance "Avvio annullato"

    $saved = Read-EnvFile
    if ($script:UniclustChoice -ne $null) {
        # cartella indicata a mano (-UniClust o menu U): vuota = torna alla ricerca automatica
        $saved["UNICLUST_PATH"] = $script:UniclustChoice
        $saved["UNICLUST_MANUAL"] = if ($script:UniclustChoice) { "1" } else { "" }
    }
    $cfg = @{}
    $cfg["BIOLOOM_VERSION"] = $Version
    try {
        $dbs = Find-Databases $saved
    } catch {
        # UniClust è facoltativo: un problema nella ricerca non deve impedire l'avvio di BioLoom
        Say "  Ricerca di UniClust30 non riuscita ($($_.Exception.Message)): QMEAN locale resta spento." Yellow
        $dbs = @{ Uniclust = ""; Qmtl = ""; Names = @(); Archives = @(); Manual = $false }
    }
    # MAC della licenza ProSA se vale su questo computer; altrimenti (nessuna licenza o licenza attiva su un altro
    # computer) il MAC locale di questo computer
    $mac = Get-ProsaMac
    $script:ProsaState = ""
    if ($mac -and (Test-ProsaComputer $mac)) { $cfg["BIOLOOM_MAC"] = $mac; $cfg["BIOLOOM_MAC_FROM_LICENSE"] = "1" }
    elseif ($saved["BIOLOOM_MAC"] -and -not $saved["BIOLOOM_MAC_FROM_LICENSE"]) { $cfg["BIOLOOM_MAC"] = $saved["BIOLOOM_MAC"] }
    else { $cfg["BIOLOOM_MAC"] = New-LocalMac }

    $port = Get-ValidPort $saved["BIOLOOM_PORT"]
    if (-not $port) { $port = 8765 }
    $current = $saved.Clone()
    $current["BIOLOOM_VERSION"] = $Version
    foreach ($k in @("UNICLUST_PATH", "QMTL_PATH", "COMPOSE_PROFILES", "BIOLOOM_QMEAN_URL")) { $current.Remove($k) }  # il disco può non esserci più
    Write-EnvFile $current  # per interrogare lo stato dei container con la configurazione attuale
    $own = Get-OwnPort $port
    if ($RequestedPort) {
        $port = Get-ValidPort $RequestedPort
        if (-not $port) { Fail "BIOLOOM_PORT=$RequestedPort non è una porta valida (un numero da 1 a 65535)." }
        if ($port -ne $own -and -not (Test-PortFree $port)) {
            Fail "La porta $port (BIOLOOM_PORT) è già usata da un altro programma: chiudilo (per vedere quale: $PortHint) oppure scegli un'altra porta con BIOLOOM_PORT."
        }
    } else {
        $free = Find-FreePort $port $own
        if (-not $free) {
            $also = if ($port -lt 8765 -or $port -gt 8800) { "la $port e " } else { "" }   # già nell'intervallo: non si ripete
            Fail ("Nessuna porta libera per BioLoom: ${also}le porte da 8765 a 8800 sono tutte usate da altri programmi. Chiudine uno " +
                  "(per vedere quali: $PortHint) oppure scegli una porta libera con BIOLOOM_PORT, per esempio in PowerShell " +
                  "`$env:BIOLOOM_PORT = 9000 (nel Prompt dei comandi: set BIOLOOM_PORT=9000) e poi $LaunchCmd")
        }
        $port = $free
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
    Check-Image
    if ($dbs.Uniclust -and -not (Ensure-Image "bioloom-qmean:$Version" "bioloom-qmean-$Version.tar" "qmean")) {
        Say "  Immagine QMEAN non disponibile: QMEAN locale resta spento." Yellow
        $cfg.Remove("COMPOSE_PROFILES"); $cfg.Remove("UNICLUST_PATH"); $cfg.Remove("QMTL_PATH"); $cfg.Remove("BIOLOOM_QMEAN_URL")
        Write-EnvFile $cfg
        $dbs.Uniclust = ""   # il riepilogo non deve dire "QMEAN: locale" (come UNICLUST="" nell'avviatore bash)
    }

    Say "  Avvio dei container..." DarkGray
    if (-not $cfg["COMPOSE_PROFILES"]) { Compose @("--profile", "qmean", "rm", "-sf", "qmean") -Quiet | Out-Null }
    if ((Compose @("up", "-d", "--remove-orphans")).Code -ne 0) { Fail "Docker non è riuscito ad avviare BioLoom (vedi i messaggi sopra)." }
    if (-not (Wait-Healthy $port)) { Fail "BioLoom non risponde: controlla con $LaunchCmd -Azione log" }

    $url = "http://127.0.0.1:$port"
    Title "BioLoom è pronto: $url"
    Say "  Dati:        $DataDir"
    if ($dbs.Uniclust -and $cfg["COMPOSE_PROFILES"]) {
        # Docker Desktop vede solo i dischi collegati quando è partito: se la cartella montata è vuota, va riavviato
        $seen = Compose @("exec", "-T", "qmean", "sh", "-c", "ls -A /uniclust_src | head -n 1") -Quiet
        if ($seen.Code -eq 0 -and -not $seen.Output.Trim()) {
            Say "  Docker non vede ancora il disco con UniClust30 (collegato dopo l'avvio di Docker Desktop)." Yellow
            Say "  Chiudi Docker Desktop dal suo menu (Quit), poi di nuovo 'Avvia BioLoom'." Yellow
        }
    }
    if ($dbs.Uniclust) {
        $versions = if ($dbs.Names.Count) { ($dbs.Names -join ", ") } else { "?" }
        Say "  QMEAN:       locale, $versions in $($dbs.Uniclust)$(if ($dbs.Qmtl) { " + QMTL (QMEANDisCo)" })" Green
        if ($dbs.Names.Count -gt 1) { Say "               versione da usare: Impostazioni > Database (predefinita la più recente)" DarkGray }
    } elseif ($dbs.Archives.Count) {
        Say "  QMEAN:       trovato l'archivio $($dbs.Archives[0]) ma non è estratto: estrailo sul disco e usa R (Riavvia)" DarkYellow
    } else {
        Say "  QMEAN:       locale spento (nessun disco con UniClust30/UniRef30 collegato; U per indicare la cartella)" DarkYellow
    }
    switch ($script:ProsaState) {
        "attivata" { Say "  ProSA:       licenza attivata su questo computer, MAC applicato" }
        "questo" { Say "  ProSA:       licenza di questo computer, MAC applicato" }
        "spostata" { Say "  ProSA:       licenza spostata su questo computer, MAC applicato" }
        "senza-id" { Say "  ProSA:       licenza trovata, MAC applicato (computer non identificato: nessun controllo)" }
        "altro" {
            $hint = if ($NoMenu) { "per spostarla qui: -SpostaProsa" } else { "R per spostarla qui" }
            Say "  ProSA:       licenza attiva su un altro computer, MAC non applicato ($hint)" DarkYellow
        }
        default { Say "  ProSA:       nessuna licenza (si carica da Impostazioni > Licenze)" }
    }
    if (-not $NoBrowser) { Start-Process $url }
    if (-not $NoMenu) { Show-Menu $url }
}

function Show-Menu([string]$url) {
    while ($true) {
        Write-Host ""
        Say "  [A] Apri nel browser   [V] Verifica strumenti   [L] Log   [R] Riavvia (rileva di nuovo i dischi)" White
        Say "  [U] Indica la cartella di UniClust30/UniRef30   [S] Arresta BioLoom ed esci" White
        Say "  [C] Chiudi questa finestra (BioLoom resta attivo)" White
        $key = Read-Host "  Scelta"
        if ($null -eq $key) { return }   # fine dell'input: come C
        switch ($key.Trim().ToUpper()) {
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
    Ensure-EnvFile
    $data = $DataDir
    # BioLoom partito da un'altra copia: si arresta lo stesso (i container sono gli stessi, "Arresta BioLoom" non chiede
    # e la cartella di quella copia può non esserci più, per esempio una chiavetta tolta), dicendo quale è
    $other = Get-OtherInstance
    if ($other) {
        Say "  Il BioLoom in esecuzione è partito da un'altra cartella: $($other.Dir)" Yellow
        $data = if ($other.Data) { $other.Data } else { $other.Dir }
    }
    Say "  Arresto di BioLoom (i calcoli in corso vengono interrotti)..." Yellow
    Compose @("--profile", "qmean", "down") | Out-Null
    Say "  BioLoom è arrestato. I dati restano in $data." Green
}

function Get-ImageIds { return "$(Get-ImageId "bioloom:$Version") $(Get-ImageId "bioloom-qmean:$Version")" }

function Update-BioLoom {  # azione aggiorna: immagini al codice in programma\, poi riavvio se BioLoom è attivo
    Ensure-Docker
    Ensure-EnvFile
    $saved = Read-EnvFile
    Title "Aggiornamento delle immagini di BioLoom $Version"
    $running = Test-Running
    $before = Get-ImageIds
    $ok = $true
    if (Test-ImageCurrent "bioloom:$Version") {
        Say "  L'immagine bioloom:$Version corrisponde già al codice in programma\." Green
    } elseif (-not (Update-BioLoomImage)) {
        $ok = $false
    }
    Update-QmeanImage $saved["UNICLUST_PATH"]
    if (-not $running) {
        if ((Get-ImageIds) -ne $before) { Say "  BioLoom non è in esecuzione: le immagini aggiornate verranno usate al prossimo avvio." }
    } elseif (-not (Test-StaleRunning)) {
        if ($ok) { Say "  BioLoom è in esecuzione e usa già le immagini attuali: nessun riavvio." }
        else { Say "  BioLoom resta in esecuzione con l'immagine che aveva: nessun riavvio." }
    } elseif (Get-OtherInstance) {
        # BioLoom di un'altra copia: arrestato (con il consenso) si avvia questa come dal desktop (porta, licenze,
        # UniClust), perché il suo .env.locale può non venire da un avvio
        Confirm-SingleInstance "Riavvio annullato"
        Start-BioLoom
    } else {
        # stesse impostazioni dell'ultimo avvio (.env.locale): compose ricrea i container con le immagini nuove
        Say "  Riavvio di BioLoom con le immagini aggiornate (i calcoli in corso vengono interrotti)..." Yellow
        if ((Compose @("up", "-d", "--remove-orphans")).Code -ne 0) { Fail "Docker non è riuscito a riavviare BioLoom (vedi i messaggi sopra)." }
        $port = Get-ValidPort $saved["BIOLOOM_PORT"]
        if (-not $port) { $port = 8765 }
        if (-not (Wait-Healthy $port)) { Fail "BioLoom non risponde: controlla con $LaunchCmd -Azione log" }
        Title "BioLoom è pronto: http://127.0.0.1:$port"
    }
    if (-not $ok) { Fail "L'immagine bioloom:$Version non è stata aggiornata (vedi i messaggi sopra)." }
}

function Find-Leftover([string[]]$paths) {  # primo file nei percorsi, esclusi README.md e i file nascosti o di sistema
    # (.DS_Store, ._README.md che macOS crea sulle chiavette exFAT, desktop.ini, Thumbs.db): resti di una copia precedente
    Get-ChildItem -LiteralPath $paths -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne "README.md" -and -not $_.Name.StartsWith(".") -and $_.Name -notin @("desktop.ini", "Thumbs.db") } |
        Select-Object -First 1
}

function Export-Portable {
    if (-not $Destinazione) {
        if ($NoMenu) { Fail "Indica la destinazione con -Destinazione." }
        $Destinazione = Read-Host "  Cartella di destinazione (es. E:\BioLoom sulla chiavetta)"
    }
    if (-not $Destinazione) { Fail "Nessuna destinazione." }
    Ensure-Docker
    New-Item -ItemType Directory -Force -Path $Destinazione | Out-Null
    $dest = (Resolve-Path -LiteralPath $Destinazione).Path
    if ($dest.TrimEnd('\') -ieq $Base.TrimEnd('\')) { Fail "La destinazione è la cartella di BioLoom stessa: scegline un'altra." }
    Title "Preparazione della copia portatile in $dest"
    Ensure-EnvFile
    # Prima le immagini: se quella di BioLoom non corrisponde al codice in programma\ la si aggiorna, e se non si può
    # la copia si ferma qui (una copia con immagine vecchia e codice nuovo non deve uscire)
    $imgDir = Join-Path $dest "immagini"
    New-Item -ItemType Directory -Force -Path $imgDir | Out-Null
    $images = @(, @("bioloom:$Version", "bioloom-$Version.tar", "bioloom"))
    if (-not $SenzaQmean) { $images += , @("bioloom-qmean:$Version", "bioloom-qmean-$Version.tar", "qmean") }
    foreach ($img in $images) {
        if (-not (Ensure-Image $img[0] $img[1] $img[2])) { Say "  Immagine $($img[0]) non disponibile: non inclusa." Yellow; continue }
        if ($img[2] -eq "bioloom" -and (Test-ImageOutdated $img[0])) {
            Say "  L'immagine $($img[0]) non corrisponde al codice in programma\: la aggiorno prima di salvarla." Yellow
            if (-not (Update-BioLoomImage)) { Fail "Immagine $($img[0]) non aggiornata: la copia avrebbe un'immagine diversa dal codice. Copia interrotta." }
        }
        Say "  Salvataggio dell'immagine $($img[0]) (qualche minuto)..." DarkGray
        if ((Invoke-Docker @("save", "-o", (Join-Path $imgDir $img[1]), $img[0])).Code -ne 0) { Fail "Salvataggio di $($img[0]) non riuscito." }
    }
    # LICENSE e THIRD_PARTY_NOTICES.md: licenza di BioLoom e avvisi (con l'offerta del sorgente) del software nelle immagini
    $files = @("Avvia BioLoom.bat", "Arresta BioLoom.bat", "Avvia BioLoom.command", "Arresta BioLoom.command", "avvia-bioloom.sh",
               "README.md", "LEGGIMI.txt", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitattributes", ".gitignore")
    foreach ($f in $files) { if (Test-Path -LiteralPath (Join-Path $Base $f)) { Copy-Item -LiteralPath (Join-Path $Base $f) -Destination $dest -Force } }
    # Pacchetti con licenza (PROCHECK, ProSA: vedi docker\vendor\README.md) mai nella copia. vendor\ resta con il
    # README e le sottocartelle vuote perché il Dockerfile la copia (COPY docker/vendor/prosa/ fallisce se manca):
    # senza pacchetti la build prosegue e BioLoom mostra quegli strumenti come "non installato".
    $vendor = Join-Path $Root "docker\vendor"
    $destVendor = Join-Path $dest "programma\docker\vendor"
    $dirs = @{ "Guida" = @(); "programma" = @("__pycache__", ".pytest_cache", "node_modules", "dist", $vendor) }
    foreach ($d in $dirs.Keys) {
        $rcArgs = @((Join-Path $Base $d), (Join-Path $dest $d), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
        if ($dirs[$d].Count) { $rcArgs += "/XD"; $rcArgs += $dirs[$d] }
        robocopy @rcArgs | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "Copia di $d non riuscita." }
    }
    foreach ($sub in @("procheck", "prosa")) { New-Item -ItemType Directory -Force -Path (Join-Path $destVendor $sub) | Out-Null }
    if (Test-Path -LiteralPath (Join-Path $vendor "README.md")) { Copy-Item -LiteralPath (Join-Path $vendor "README.md") -Destination $destVendor -Force }
    # Chiavi di licenza, posta.env (password SMTP) e chiave d'accesso del server: solo con -ConLicenze
    if (-not $SenzaDati -and (Test-Path -LiteralPath $DataDir)) {
        $rcArgs = @($DataDir, (Join-Path $dest "data"), "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/XD", ".scratch", ".localcache")
        if ($ConLicenze) {
            Say "  Copia dei dati (progetti, risultati, licenze, database)..." DarkGray
        } else {
            Say "  Copia dei dati (progetti, risultati, database; senza licenze e chiave d'accesso)..." DarkGray
            $rcArgs += @((Join-Path $DataDir "licenses"), "/XF", (Join-Path $DataDir "access_key"))
        }
        robocopy @rcArgs | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "Copia dei dati non riuscita." }
    }
    # resti di una copia fatta con una versione precedente, che copiava pacchetti e chiavi: si segnalano, non si cancellano
    $left = @()
    if (Find-Leftover @($destVendor)) { $left += "pacchetti con licenza in programma\docker\vendor" }
    if (-not $ConLicenze -and (Find-Leftover @((Join-Path $dest "data\licenses"), (Join-Path $dest "data\access_key")))) {
        $left += "licenze o chiave d'accesso in data"
    }
    Title "Copia portatile pronta: $dest"
    Say "  Sul computer di destinazione serve solo Docker (Docker Desktop o un programma con i comandi docker e docker compose):"
    Say "  doppio clic su 'Avvia BioLoom'."
    if ($SenzaDati -or -not (Test-Path -LiteralPath $DataDir)) {
        Say "  Dati e licenze non copiati: sul computer di destinazione le licenze si inseriscono da Impostazioni > Licenze." Yellow
    } elseif ($ConLicenze) {
        Say "  La copia contiene chiavi di licenza e password personali (data\licenses, data\access_key): non darla ad altri." Yellow
    } else {
        Say "  Licenze non copiate: sul computer di destinazione si inseriscono da Impostazioni > Licenze (MODELLER, ProSA)" Yellow
        Say "  e posta.env, se serve, va ricreato in data\licenses. Per includerle rifai la copia con -ConLicenze." Yellow
    }
    if ($left.Count) { Say "  Nella destinazione restano file di una copia precedente ($($left -join ', ')): cancellali se la copia va ad altri." Yellow }
    Say "  Le immagini in 'immagini' contengono MODELLER e, se installati, PROCHECK e ProSA II: la copia è per uso" Yellow
    Say "  personale (o del gruppo, se le licenze lo consentono) e non va redistribuita." Yellow
}

switch ($Azione) {
    "avvia" { Start-BioLoom }
    "arresta" { Stop-BioLoom }
    "stato" { Ensure-EnvFile; Show-OtherInstance; Compose @("--profile", "qmean", "ps") | Out-Null }
    "log" { Ensure-EnvFile; Show-OtherInstance; Compose @("logs", "--tail", "200") | Out-Null }
    "verifica" { Ensure-EnvFile; Show-OtherInstance; Compose @("exec", "bioloom", "python", "-m", "bioloom.selftest") | Out-Null }
    "prepara" { Export-Portable }
    "aggiorna" { Update-BioLoom }
}
