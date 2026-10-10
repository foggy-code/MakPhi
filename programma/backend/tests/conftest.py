from __future__ import annotations

import ipaddress
import linecache
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx
import pytest
from fastapi.testclient import TestClient

from bioloom.app import create_app
from bioloom.config import Paths
from bioloom.core import cache as cache_module
from bioloom.core import jobs as jobs_module
from bioloom.engines import tools

DATA = Path(__file__).parent / "data"

# I TestClient mandano l'header Host "testserver", che senza accesso protetto il controllo dell'Host (core/guard)
# rifiuterebbe. Aggiunto qui, prima di ogni create_app, vale anche per le app create direttamente nei test; aggiunto e
# non solo predefinito, perché la variabile può esistere vuota (compose.yml) o con altri nomi. I test della guardia la
# tolgono o la cambiano con monkeypatch.
os.environ["BIOLOOM_ALLOWED_HOSTS"] = f"{os.environ.get('BIOLOOM_ALLOWED_HOSTS', '')} testserver".strip()

# Chiave MODELLER per i test (es. nel container: -e MODELLER_KEY=...), come la usa il server.
if os.environ.get("MODELLER_KEY"):
    jobs_module.set_subprocess_env("BIOLOOM_MODELLER_KEY", os.environ["MODELLER_KEY"])


@pytest.fixture(autouse=True)
def _restore_subprocess_env():
    """Le licenze attivate da un test non devono influenzare i successivi."""
    saved = dict(jobs_module._extra_env)
    yield
    jobs_module._extra_env.clear()
    jobs_module._extra_env.update(saved)


@pytest.fixture(autouse=True)
def _reset_compute_cache():
    """La cartella della cache dei calcoli configurata da un test (build_core) non vale per i successivi."""
    saved = cache_module.root()
    yield
    cache_module.configure(saved)


# ------------------------------------------------------------------------------------------ niente rete vera
NETWORK_HELP = ("I test senza il marcatore `network` non escono dal computer: usa httpx.MockTransport, un server su "
                "127.0.0.1 o un client finto; i test verso i servizi veri vanno marcati @pytest.mark.network (esclusi "
                "di default, si lanciano con `pytest -m network`).")
_LOCAL_NAMES = {"", "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def is_local_host(host: Any) -> bool:
    """Vero per gli indirizzi di questo computer: localhost, 127.0.0.0/8, ::1, l'indirizzo non specificato (0.0.0.0,
    ::) e i nomi *.localhost; falso per ogni altro nome o indirizzo."""
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    name = str(host).strip().strip("[]").lower().rstrip(".")
    if name in _LOCAL_NAMES or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def _is_ip(host: Any) -> bool:
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    try:
        ipaddress.ip_address(str(host).strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    return True


class _NetworkGuard:
    """Blocca le connessioni verso host non locali: client httpx con il trasporto vero (HTTPTransport, anche asincrono),
    risoluzione dei nomi (socket.getaddrinfo) e connessioni dei socket (anche smtplib, urllib...). MockTransport,
    TestClient e i server su 127.0.0.1 non passano di qui o sono locali. Ogni tentativo bloccato si registra, perché il
    codice può trasformare l'errore in un esito «servizio non raggiungibile» che il test accetterebbe."""

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._saved: list[tuple[object, str, object, bool]] = []

    def _blocked(self, what: str) -> str:
        self.attempts.append(what)
        return f"Connessione di rete reale bloccata nei test ({what}). {NETWORK_HELP}"

    def _patch(self, owner: object, name: str, value: object) -> None:
        self._saved.append((owner, name, getattr(owner, name), name in vars(owner)))
        setattr(owner, name, value)

    def install(self) -> None:
        guard = self
        real_getaddrinfo = socket.getaddrinfo
        real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
        real_handle = httpx.HTTPTransport.handle_request
        real_handle_async = httpx.AsyncHTTPTransport.handle_async_request

        def check_address(sock: socket.socket, address: Any) -> None:
            if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address:
                if not is_local_host(address[0]):
                    raise ConnectionRefusedError(guard._blocked(f"socket verso {address[0]}:{address[1]}"))

        def getaddrinfo(host, *args, **kwargs):
            # un indirizzo numerico non va in rete per risolversi: lo ferma la connessione
            if not is_local_host(host) and not _is_ip(host):
                raise socket.gaierror(socket.EAI_NONAME, guard._blocked(f"risoluzione del nome {host}"))
            return real_getaddrinfo(host, *args, **kwargs)

        def connect(sock, address):
            check_address(sock, address)
            return real_connect(sock, address)

        def connect_ex(sock, address):
            check_address(sock, address)
            return real_connect_ex(sock, address)

        def handle_request(transport, request):
            if not is_local_host(request.url.host):
                raise httpx.ConnectError(guard._blocked(f"httpx {request.method} {request.url}"), request=request)
            return real_handle(transport, request)

        async def handle_async_request(transport, request):
            if not is_local_host(request.url.host):
                raise httpx.ConnectError(guard._blocked(f"httpx {request.method} {request.url}"), request=request)
            return await real_handle_async(transport, request)

        self._patch(socket, "getaddrinfo", getaddrinfo)
        self._patch(socket.socket, "connect", connect)
        self._patch(socket.socket, "connect_ex", connect_ex)
        self._patch(httpx.HTTPTransport, "handle_request", handle_request)
        self._patch(httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request)

    def uninstall(self) -> None:
        for owner, name, value, own in reversed(self._saved):
            if own:
                setattr(owner, name, value)
            else:  # ereditato (socket.socket.connect viene da _socket.socket): si toglie la copia aggiunta
                delattr(owner, name)
        self._saved.clear()


@pytest.fixture(autouse=True)
def _no_real_network(request):
    """Nei test senza il marcatore `network` ogni connessione verso un host non locale fallisce subito con un messaggio
    chiaro, e il test fallisce anche se il codice ha trasformato l'errore in un esito accettabile. Non usa monkeypatch:
    un `monkeypatch.undo()` dentro un test non toglie il blocco."""
    if request.node.get_closest_marker("network") is not None:
        yield
        return
    guard = _NetworkGuard()
    guard.install()
    try:
        yield guard
    finally:
        guard.uninstall()
    if guard.attempts:
        shown = "; ".join(dict.fromkeys(guard.attempts))
        pytest.fail(f"Il test ha tentato connessioni di rete reali (bloccate): {shown}. {NETWORK_HELP}", pytrace=False)


# ------------------------------------------------------------------------------------------ attese senza pause fisse
def until(condition: Callable[[], Any], timeout: float = 30.0, interval: float = 0.02,
          what: str = "condizione") -> Any:
    """Attende che `condition()` dia un valore vero e lo restituisce; AssertionError dopo `timeout` secondi. Da usare
    al posto di una pausa fissa seguita da un'asserzione: sul computer lento si aspetta di più, su quello veloce
    meno."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError(f"{what} non raggiunta entro {timeout} s")
        time.sleep(interval)


_WAIT_CALLS = (".acquire(", ".wait(", ".wait_for(", ".join(")


def blocked_in(thread: threading.Thread, function: str) -> bool:
    """Vero se `thread` è fermo in un'attesa bloccante (lock, condizione, evento, join) chiamata direttamente da
    `function`: il frame Python più interno è su una riga con `.acquire(`, `.wait(`, `.wait_for(` o `.join(`, e il primo
    frame fuori dal modulo threading è `function`.

    È il segnale per le verifiche «non deve succedere nulla»: un thread fermo lì non può andare avanti finché il test
    non libera la risorsa, quindi l'asserzione non dipende da quanto si aspetta. Non vale per `with lock:` (l'attesa è
    in C, sulla riga del `with`): lì serve un `WatchedLock`."""
    frame = sys._current_frames().get(thread.ident) if thread.ident is not None else None
    if frame is None or not thread.is_alive():
        return False
    line = linecache.getline(frame.f_code.co_filename, frame.f_lineno)
    if not any(call in line for call in _WAIT_CALLS):
        return False
    while frame is not None and frame.f_code.co_filename == threading.__file__:
        frame = frame.f_back
    return frame is not None and frame.f_code.co_name == function


def dispatcher_settled(manager, pool: str) -> bool:
    """Ogni worker del pool (core/dispatch.Dispatcher) che non sta eseguendo un job è fermo nell'attesa della coda,
    in `Dispatcher._worker`: la coda è stata riesaminata (dopo la segnalazione di una risorsa occupata o l'arrivo di un
    job) e un job rimasto in coda non parte finché qualcosa non cambia (risorsa liberata, slot, capacità). È il segnale
    per verificare che un job resti in coda, al posto di una pausa fissa."""
    dispatcher = manager._pools[pool]
    with dispatcher._cond:
        threads = [t for t in dispatcher._threads if t.is_alive()]
        busy = dispatcher._busy
    return sum(blocked_in(t, "_worker") for t in threads) >= len(threads) - busy


class WatchedLock:
    """Lock che segnala chi lo trova occupato: `waiting` si imposta (e `waiters` riceve il nome del thread) quando un
    `acquire` bloccante deve aspettare. Sostituisce un lock del codice (es. quelli per id della libreria dei template)
    per verificare che un'operazione concorrente aspetti, senza pause fisse."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.waiting = threading.Event()
        self.waiters: list[str] = []

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if self._lock.acquire(False):
            return True
        if not blocking:
            return False
        self.waiters.append(threading.current_thread().name)
        self.waiting.set()
        return self._lock.acquire(True, timeout)

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()


HUMAN_UB = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"
YEAST_UB = "MQIFVKTLTGKTITLEVESSDTIDNVKSKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


@pytest.fixture
def ubq_path() -> Path:
    return DATA / "1UBQ.pdb"


@pytest.fixture
def client(tmp_path):
    app = create_app(Paths(tmp_path / "data"), frontend_dist=None)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client_with_ubq(client, ubq_path):
    with open(ubq_path, "rb") as fh:
        r = client.post("/api/templates/upload", files={"file": ("1UBQ.pdb", fh, "chemical/x-pdb")})
    assert r.status_code == 200, r.text
    return client


def ubq_variant(dest: Path, *, hetatm: Optional[dict[int, str]] = None, blank_chain: bool = False,
                drop: Iterable[int] = ()) -> Path:
    """Copia di 1UBQ modificata come i file reali: `hetatm` riscrive i residui indicati come HETATM con un altro nome
    (es. {1: "MSE"}: selenometionina, con SD → SE come nei file di RCSB; {20: "SEP"}), `blank_chain` svuota la colonna
    22 (catena senza identificativo, come nei file di dinamica molecolare), `drop` toglie dei residui (interruzione)."""
    hetatm = hetatm or {}
    dropped = set(drop)
    rows = []
    for ln in (DATA / "1UBQ.pdb").read_text().splitlines():
        if ln.startswith("ATOM") and int(ln[22:26]) in dropped:
            continue
        if ln.startswith("ATOM") and int(ln[22:26]) in hetatm:
            name = hetatm[int(ln[22:26])]
            ln = "HETATM" + ln[6:17] + name + ln[20:]
            if name == "MSE" and ln[12:16] == " SD ":
                ln = ln[:12] + "SE  " + ln[16:76] + "SE" + ln[78:]
        if blank_chain and ln.startswith(("ATOM", "HETATM", "TER")) and len(ln) > 21:
            ln = ln[:21] + " " + ln[22:]
        rows.append(ln)
    dest.write_text("\n".join(rows) + "\n")
    return dest


def wait_job(client, job_id: str, timeout: float = 300) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        r = client.get(f"/api/jobs/{job_id}")
        job = r.json()
        # una risposta d'errore si mostra com'è (codice e corpo), invece di un KeyError su "status"
        assert r.status_code == 200 and "status" in job, f"GET /api/jobs/{job_id}: {r.status_code} {r.text[:1000]}"
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} non terminato entro {timeout}s")


def modeller_available() -> bool:
    return tools.detect_modeller("")["available"]


requires_modeller = pytest.mark.skipif(not modeller_available(), reason="MODELLER non installato")
