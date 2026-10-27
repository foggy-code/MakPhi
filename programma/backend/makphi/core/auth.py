"""Accesso con chiave per l'uso da server (MAKPHI_AUTH=on).

In locale (porta pubblicata solo su 127.0.0.1) l'accesso è libero. Su un server la chiave viene da
MAKPHI_ACCESS_KEY (almeno MIN_KEY_LENGTH caratteri, altrimenti l'avvio si ferma) oppure è generata al primo avvio in
`data/access_key` e stampata nel log. Il login imposta un cookie HttpOnly firmato (HMAC) con scadenza; è accettato
anche `Authorization: Bearer <chiave>` per gli script.

Sessioni revocabili: il segreto della firma dipende dalla chiave e da un'"epoca" salvata in `data/session_epoch`
(intero, creato a 0). "Esci da tutti i dispositivi" (POST /api/auth/logout-all) incrementa l'epoca: tutti i cookie
emessi prima, compreso quello di chi lo chiede, non valgono più. Anche cambiare la chiave li invalida tutti. All'epoca 0
il segreto è quello delle versioni precedenti: aggiornare MakPhi non chiude le sessioni aperte. Un file dell'epoca
illeggibile o rovinato non riporta l'epoca a 0 (riattiverebbe le sessioni revocate): se ne sceglie una nuova a caso, e
tutte le sessioni si chiudono.

Tentativi di accesso (`LoginLimiter`): per indirizzo del client un tentativo alla volta (gli altri, in parallelo, sono
rifiutati con 429) e, dopo ogni chiave sbagliata, un'attesa che cresce con gli errori dell'ultimo quarto d'ora e vale
anche per le richieste in parallelo; gli indirizzi seguiti sono al più LOGIN_MAX_CLIENTS e scadono dopo
LOGIN_WINDOW secondi senza errori. Il freno vale per il login e per la chiave passata come Bearer a qualunque rotta
(`LoginLimiter.try_key`); un cookie di sessione valido non passa mai dal freno.

Indirizzo del client: quello della connessione. Dietro un reverse proxy (Caddy, nginx) è quello del proxy, uguale per
tutti, e il freno varrebbe per tutti insieme: chi sbaglia la chiave di continuo bloccherebbe l'accesso agli altri. Con
MAKPHI_TRUSTED_PROXIES (indirizzi o reti dei proxy) l'indirizzo viene da `X-Forwarded-For`, ma solo per le
connessioni che arrivano da quei proxy (`client_address`).
"""

from __future__ import annotations

import collections
import hashlib
import hmac
import ipaddress
import math
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .storage import atomic_write_text

COOKIE = "makphi_session"
LEGACY_COOKIE = "bioloom_session"  # nome usato da BioLoom (fino alla 4.4.0), ancora accettato
SESSION_DAYS = 30
EXEMPT = ("/api/health", "/api/auth/")
PROTECTED_PREFIXES = ("/api/", "/docs", "/redoc", "/openapi.json")
# Lunghezza minima di MAKPHI_ACCESS_KEY (la chiave generata e quelle create dagli avviatori sono di 24 caratteri)
MIN_KEY_LENGTH = 16
EPOCH_FILE = "session_epoch"
# Tentativi di accesso: finestra degli errori, attesa per errore e massima, indirizzi seguiti al più
LOGIN_WINDOW = 900.0
LOGIN_STEP = 0.5
LOGIN_MAX_DELAY = 10.0
LOGIN_MAX_CLIENTS = 4096


def _truthy(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in ("1", "on", "true", "yes", "si", "sì")


def server_mode() -> bool:
    """Modalità server (accesso con chiave): MAKPHI_AUTH=on. Vale anche per i comandi da riga di comando che girano
    nel container del server (verifica): usano le impostazioni dell'amministratore come il server (SettingsStore)."""
    return _truthy(os.environ.get("MAKPHI_AUTH"))


class AccessKeyError(RuntimeError):
    """Configurazione dell'accesso non valida (chiave troppo corta, proxy fidati scritti male): MakPhi non parte."""


class EpochError(OSError):
    """L'epoca delle sessioni non si è potuta salvare: le sessioni restano valide."""


def _parse_epoch(text: str) -> Optional[int]:
    """Epoca scritta nel file: un intero non negativo e nient'altro (spazi e a capo esclusi); None altrimenti."""
    text = text.strip()
    return int(text) if re.fullmatch(r"[0-9]{1,18}", text) else None


def _random_epoch() -> int:
    return secrets.randbelow(2 ** 31) + 1


@dataclass
class AuthConfig:
    enabled: bool
    key: str = ""
    generated: bool = False
    epoch: int = 0
    epoch_file: Optional[Path] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    @property
    def _secret(self) -> bytes:
        base = b"bioloom-session:" + self.key.encode()  # testo storico: i cookie emessi da BioLoom restano validi
        if self.epoch:  # all'epoca 0 il segreto delle versioni precedenti: i cookie già emessi restano validi
            base += b":epoch:" + str(self.epoch).encode()
        return hashlib.sha256(base).digest()

    def attach(self, data_dir: Path) -> None:
        """Lega l'epoca delle sessioni a `data_dir/session_epoch`: la legge, o crea il file a 0 se manca.

        Un file che c'è ma non si legge o non contiene un intero valido non vale 0: l'epoca 0 è il segreto delle
        versioni precedenti e riattiverebbe le sessioni revocate con logout-all. Si sceglie invece un'epoca nuova a caso
        (tutte le sessioni si chiudono) e la si salva. Un file non scrivibile non ferma l'avvio: l'epoca resta in
        memoria (logout-all darà errore se non riesce a salvarla)."""
        path = Path(data_dir) / EPOCH_FILE
        with self._lock:
            self.epoch_file = path
            try:
                text: Optional[str] = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self.epoch = 0
                self._save_epoch(path)
                return
            except OSError as exc:
                text, reason = None, f"illeggibile ({exc})"
            else:
                reason = f"non valida ({text.strip()[:40]!r})"
            value = _parse_epoch(text) if text is not None else None
            if value is not None:
                self.epoch = value
                return
            self.epoch = _random_epoch()
            print(f"[MakPhi] epoca delle sessioni in {path} {reason}: ne è stata scelta una nuova, quindi tutte le "
                  "sessioni aperte sono chiuse e nei browser va inserita di nuovo la chiave d'accesso.", flush=True)
            self._save_epoch(path)

    def _save_epoch(self, path: Path) -> None:
        try:
            atomic_write_text(path, f"{self.epoch}\n")
        except OSError as exc:
            print(f"[MakPhi] epoca delle sessioni non salvata in {path}: {exc}", flush=True)

    def revoke_all(self) -> int:
        """Invalida tutti i cookie di sessione emessi finora (logout-all): incrementa l'epoca e la salva prima di
        usarla, così dopo un riavvio vale ancora. EpochError se non si riesce a salvarla (nulla cambia)."""
        with self._lock:
            new = self.epoch + 1
            if self.epoch_file is not None:
                try:
                    atomic_write_text(self.epoch_file, f"{new}\n")
                except OSError as exc:
                    raise EpochError(f"{self.epoch_file}: {exc}") from exc
            self.epoch = new
            return new

    def check_key(self, candidate: str) -> bool:
        return bool(self.key) and hmac.compare_digest(candidate.strip().encode(), self.key.encode())

    def issue(self, now: Optional[float] = None) -> str:
        expires = int((now or time.time()) + SESSION_DAYS * 86400)
        sig = hmac.new(self._secret, str(expires).encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{sig}"

    def verify(self, token: str, now: Optional[float] = None) -> bool:
        try:
            expires_raw, sig = token.split(".", 1)
            expires = int(expires_raw)
        except (ValueError, AttributeError):
            return False
        expected = hmac.new(self._secret, str(expires).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and expires > (now or time.time())

    def session_valid(self, cookie: Optional[str]) -> bool:
        """Cookie di sessione valido. La chiave passata come Bearer invece si controlla solo attraverso il freno ai
        tentativi (api/auth.authenticate, LoginLimiter.try_key): qui nessun confronto della chiave senza freno."""
        return bool(cookie) and self.verify(cookie)

    def requires_auth(self, path: str) -> bool:
        """La richiesta a `path` richiede l'accesso: accesso protetto, API o documentazione dell'API, non esente
        (stato dell'accesso, login, salute). L'interfaccia (senza dati) è sempre servita."""
        return self.enabled and path.startswith(PROTECTED_PREFIXES) and not path.startswith(EXEMPT)


def bearer_key(authorization: Optional[str]) -> Optional[str]:
    """Chiave di `Authorization: Bearer <chiave>`; None se l'header manca o è di un altro tipo."""
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


def load(data_dir: Path) -> AuthConfig:
    if not server_mode():
        return AuthConfig(enabled=False)
    key = os.environ.get("MAKPHI_ACCESS_KEY", "").strip()
    if key:
        if len(key) < MIN_KEY_LENGTH:
            raise AccessKeyError(
                f"MAKPHI_ACCESS_KEY è troppo corta: {len(key)} caratteri, ne servono almeno {MIN_KEY_LENGTH} "
                "(una chiave corta si indovina per tentativi). Scrivi in server.env una chiave più lunga, per esempio "
                "di 24 lettere e cifre a caso come quelle create dall'avviatore (es. openssl rand -hex 12), oppure "
                "lascia vuota MAKPHI_ACCESS_KEY: MakPhi ne genera una in data/access_key e la stampa nel log. "
                "Poi riavvia MakPhi.")
        cfg = AuthConfig(enabled=True, key=key)
        cfg.attach(data_dir)
        return cfg
    path = data_dir / "access_key"
    try:
        key = path.read_text(encoding="utf-8").strip()
    except OSError:
        key = ""
    generated = False
    if len(key) < 8:
        key = secrets.token_urlsafe(18)
        path.write_text(key + "\n", encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        generated = True
    cfg = AuthConfig(enabled=True, key=key, generated=generated)
    cfg.attach(data_dir)
    return cfg


def shutdown_allowed() -> bool:
    return _truthy(os.environ.get("MAKPHI_ALLOW_SHUTDOWN"))


# ------------------------------------------------------------------------------------------ tentativi di accesso
@dataclass
class _Client:
    failures: list[float] = field(default_factory=list)
    busy: bool = False
    until: float = 0.0  # prima di questo istante un nuovo tentativo è rifiutato (attesa dopo l'ultimo errore)


class LoginLimiter:
    """Freno ai tentativi di accesso, per indirizzo del client.

    - Un tentativo alla volta: mentre uno è in corso (anche durante l'attesa dopo una chiave sbagliata) gli altri dallo
      stesso indirizzo sono rifiutati (`begin` restituisce l'attesa in secondi, la rotta risponde 429).
    - Attesa progressiva: dopo ogni chiave sbagliata `failure` restituisce LOGIN_STEP secondi per ogni errore degli
      ultimi LOGIN_WINDOW secondi (al più LOGIN_MAX_DELAY), che la rotta attende prima di rispondere; fino alla fine di
      quell'attesa ogni nuovo tentativo è rifiutato, anche se il client chiude la connessione prima.
    - Memoria limitata: gli indirizzi senza errori recenti e senza tentativi in corso si dimenticano, e oltre
      LOGIN_MAX_CLIENTS si dimenticano i meno recenti.
    """

    def __init__(self, *, window: float = LOGIN_WINDOW, step: float = LOGIN_STEP, max_delay: float = LOGIN_MAX_DELAY,
                 max_clients: int = LOGIN_MAX_CLIENTS, clock: Callable[[], float] = time.monotonic) -> None:
        self.window = window
        self.step = step
        self.max_delay = max_delay
        self.max_clients = max_clients
        self.clock = clock
        self._clients: collections.OrderedDict[str, _Client] = collections.OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._clients)

    def _prune(self, now: float, room: int = 0) -> None:
        """(Sotto il lock) dimentica gli indirizzi senza errori recenti, poi i meno recenti oltre il massimo (lasciando
        posto per `room` nuovi indirizzi)."""
        for name in list(self._clients):
            entry = self._clients[name]
            entry.failures = [t for t in entry.failures if now - t < self.window]
            if not entry.busy and not entry.failures and entry.until <= now:
                del self._clients[name]
        while len(self._clients) > max(0, self.max_clients - room):
            name = next((n for n, e in self._clients.items() if not e.busy), None)
            if name is None:
                break
            del self._clients[name]

    def begin(self, client: str) -> Optional[float]:
        """Inizio di un tentativo: None se può procedere (l'indirizzo resta occupato fino a `end`), altrimenti i secondi
        da attendere (un altro tentativo in corso o l'attesa dopo l'ultimo errore)."""
        with self._lock:
            now = self.clock()
            entry = self._clients.get(client)
            if entry is None:
                if len(self._clients) >= self.max_clients:
                    self._prune(now, room=1)
                entry = self._clients[client] = _Client()
            self._clients.move_to_end(client)
            if entry.busy:
                return max(1.0, entry.until - now)
            if entry.until > now:
                return entry.until - now
            entry.busy = True
            return None

    def failure(self, client: str) -> float:
        """Chiave sbagliata: registra l'errore e restituisce l'attesa da fare prima di rispondere."""
        with self._lock:
            now = self.clock()
            entry = self._clients.setdefault(client, _Client())
            entry.failures = [t for t in entry.failures if now - t < self.window] + [now]
            delay = min(self.max_delay, self.step * len(entry.failures))
            entry.until = now + delay
            return delay

    def try_key(self, client: str, verify: Callable[[], bool]) -> tuple[bool, Optional[float]]:
        """Chiave che arriva con una richiesta qualsiasi (`Authorization: Bearer`, gli script), senza un turno:

        - (False, secondi) se l'indirizzo è fermo (un login in corso o l'attesa dopo un errore): la chiave non si
          confronta nemmeno, la richiesta risponde 429. Così, come per il login, durante l'attesa nessuna chiave si
          prova, nemmeno quella giusta: altrimenti la risposta direbbe subito quale è giusta;
        - (False, None) se è sbagliata: l'errore conta come quelli del login, e fino alla fine dell'attesa che ne segue
          l'indirizzo è fermo (la richiesta risponde subito 401, l'attesa la fanno i tentativi successivi);
        - (True, None) se è giusta. Una chiave giusta non occupa l'indirizzo e non azzera gli errori: gli script possono
          fare richieste in parallelo, e non accorciano l'attesa di chi prova chiavi dallo stesso indirizzo.

        Controllo, confronto ed errore avvengono insieme sotto il lock: richieste in parallelo non confrontano più
        chiavi durante un'attesa."""
        with self._lock:
            now = self.clock()
            entry = self._clients.get(client)
            if entry is not None:
                if entry.busy:
                    return False, max(1.0, entry.until - now)
                if entry.until > now:
                    return False, entry.until - now
            if verify():
                return True, None
            if entry is None:
                if len(self._clients) >= self.max_clients:
                    self._prune(now, room=1)
                entry = self._clients[client] = _Client()
            self._clients.move_to_end(client)
            entry.failures = [t for t in entry.failures if now - t < self.window] + [now]
            entry.until = now + min(self.max_delay, self.step * len(entry.failures))
            return False, None

    def success(self, client: str) -> None:
        with self._lock:
            entry = self._clients.get(client)
            if entry is not None:
                entry.failures.clear()
                entry.until = 0.0

    def end(self, client: str) -> None:
        """Fine del tentativo (sempre, anche con un errore o una connessione chiusa)."""
        with self._lock:
            entry = self._clients.get(client)
            if entry is None:
                return
            entry.busy = False
            if not entry.failures and entry.until <= self.clock():
                del self._clients[client]


def wait_seconds(seconds: float) -> int:
    """Attesa da indicare nei messaggi: secondi interi, arrotondati per eccesso, almeno 1."""
    return max(1, math.ceil(seconds))


# ------------------------------------------------------------------------------------------ indirizzo del client
def trusted_proxies(value: Optional[str] = None) -> tuple:
    """Reti dei reverse proxy fidati da MAKPHI_TRUSTED_PROXIES (o da `value`): indirizzi o reti CIDR separati da
    virgole o spazi, es. "172.18.0.1" o "172.16.0.0/12, 10.88.0.1". Vuota = nessun proxy fidato. AccessKeyError (avvio
    fermato, con il motivo) se una voce non è un indirizzo: un errore di battitura non deve lasciare il freno ai
    tentativi diverso da come lo si crede."""
    raw = os.environ.get("MAKPHI_TRUSTED_PROXIES", "") if value is None else value
    out = []
    for item in re.split(r"[\s,;]+", raw.strip()):
        if not item:
            continue
        try:
            out.append(ipaddress.ip_network(item.strip("[]"), strict=False))
        except ValueError:
            raise AccessKeyError(
                f"MAKPHI_TRUSTED_PROXIES contiene «{item[:60]}», che non è un indirizzo IP né una rete (es. 172.18.0.1 "
                "o 172.16.0.0/12). Correggila in server.env, o lasciala vuota se MakPhi non è dietro un reverse proxy, "
                "poi riavvia MakPhi.") from None
    return tuple(out)


def _ip(text: str):
    """Indirizzo IP di una voce di X-Forwarded-For o dell'indirizzo della connessione ("1.2.3.4", "1.2.3.4:5678",
    "[::1]:80", "::1"), con gli IPv4 scritti come IPv6 ("::ffff:1.2.3.4") riportati a IPv4; None se non lo è."""
    text = text.strip().strip('"')
    if text.startswith("["):
        text = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _within(ip, trusted: tuple) -> bool:
    return any(ip in net for net in trusted)  # IPv4 in una rete IPv6 (o viceversa): falso, senza errori


def is_trusted(address: Optional[str], trusted: tuple) -> bool:
    ip = _ip(address or "")
    return ip is not None and _within(ip, trusted)


def client_address(peer: Optional[str], forwarded_for: Optional[str], trusted: tuple) -> str:
    """Indirizzo del client per il freno ai tentativi.

    Senza proxy fidati, o se la connessione non arriva da uno di loro, è l'indirizzo della connessione (`peer`):
    X-Forwarded-For lo può scrivere chiunque. Se arriva da un proxy fidato, è il primo indirizzo di X-Forwarded-For,
    partendo da destra, che non è a sua volta un proxy fidato: l'ultimo aggiunto è quello che il proxy ha visto
    collegarsi (Caddy lo scrive da sé, nginx con $proxy_add_x_forwarded_for lo aggiunge in fondo), quelli più a
    sinistra li può scrivere il client. Una voce che non è un indirizzo ferma la ricerca (proxy configurato male):
    vale l'ultimo proxy fidato raggiunto."""
    peer = peer or "?"
    if not trusted or not forwarded_for or not is_trusted(peer, trusted):
        return peer
    current = str(_ip(peer))
    for item in reversed(forwarded_for.split(",")):
        ip = _ip(item)
        if ip is None:
            break
        current = str(ip)
        if not _within(ip, trusted):
            break
    return current
