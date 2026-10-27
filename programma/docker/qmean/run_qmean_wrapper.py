"""Esegue run_qmean.py con il database UniClust30/UniRef30 scelto da MakPhi.

run_qmean.py accetta solo una cartella /uniclust30 con esattamente un database completo di tutti e 6 i
file (compresi gli HMM). Qui il prefisso arriva da MAKPHI_UNICLUST_PREFIX: così funzionano qualunque
versione, i database senza HMM (HHblits li calcola dagli allineamenti) e più versioni sullo stesso disco.
Con MAKPHI_HHBLITS_CPU HHblits usa più processori (run_qmean.py ne chiede 1): il risultato non cambia.
Con MAKPHI_PROFILE_CACHE (cartella del database in uso) il profilo HHblits di ogni sequenza si conserva e una
sequenza già vista non rifà la ricerca: run_qmean.py usa da sé un a3m già presente nella sua cartella di lavoro
(`<md5 della sequenza>/hhblits.a3m`), e il profilo dipende solo da sequenza e database, quindi il risultato è lo
stesso. Profili riusati e calcolati vanno in `$TMPDIR/profile_cache.json`.
Un solo HHblits per sequenza anche fra calcoli contemporanei: un blocco (flock) per sequenza in `<cartella>/.locks`
fa aspettare agli altri modelli della stessa sequenza il profilo del primo, che poi riusano. I blocchi sono
65536 file vuoti al massimo (le prime 4 cifre dell'md5): due sequenze diverse nello stesso file si aspettano a
vicenda, raramente e senza cambiare il risultato.
Va avviato con l'interprete di OpenStructure: `ost run_qmean_wrapper.py <argomenti di run_qmean.py>`.
"""

import fcntl
import importlib.util
import json
import os
import shutil
import sys

RUN_QMEAN = "/qmean/run_qmean.py"
LOCKS = ".locks"  # sottocartella dei blocchi per sequenza: la pulizia dei profili non la tocca


class _SequenceLock:
    """Blocco esclusivo fra processi per una sequenza (flock su `<cartella>/.locks/<md5[:4]>.lock`). Se il blocco
    non si può creare (cartella in sola lettura) si procede senza: al più si rifà un HHblits."""

    def __init__(self, folder, name):
        self.path = os.path.join(folder, LOCKS, name[:4] + ".lock")
        self.fh = None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self.fh = open(self.path, "a")
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        except OSError:
            self.close()
        return self

    def close(self):
        if self.fh is not None:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()
            self.fh = None

    def __exit__(self, *exc):
        self.close()


def _profile_cache(module, folder):
    from ost.bindings import hhblits3
    original = module._seqanno
    stats = {"reused": 0, "computed": 0}
    report = os.path.join(os.environ.get("TMPDIR") or ".", "profile_cache.json")

    def seqanno(target_seq, workdir, *args, **kwargs):
        name = target_seq.GetName()  # md5 della sequenza (run_qmean._get_seq_name)
        cached = os.path.join(folder, name + ".a3m")
        a3m = os.path.join(workdir, name, hhblits3.HHblits.OUTPUT_PREFIX + ".a3m")
        # il blocco resta preso fino al salvataggio del profilo: gli altri modelli della stessa sequenza aspettano
        # e poi lo riusano invece di rifare la ricerca
        with _SequenceLock(folder, name) as lock:
            reused = False
            if os.path.isfile(cached) and not os.path.exists(a3m):
                try:
                    os.makedirs(os.path.dirname(a3m), exist_ok=True)
                    shutil.copyfile(cached, a3m)
                    os.utime(cached, None)  # usato ora: la pulizia toglie prima i meno recenti
                    reused = True
                except OSError:
                    pass
            if reused:
                lock.close()  # profilo già pronto: nessuno deve aspettare il resto di questo calcolo
            result = original(target_seq, workdir, *args, **kwargs)
            if reused:
                stats["reused"] += 1
            else:
                stats["computed"] += 1
                if os.path.isfile(a3m) and not os.path.exists(cached):
                    try:  # scrittura atomica: chi legge non vede mai un profilo a metà
                        os.makedirs(folder, exist_ok=True)
                        tmp = "%s.%d.tmp" % (cached, os.getpid())
                        shutil.copyfile(a3m, tmp)
                        os.replace(tmp, cached)
                    except OSError:
                        pass
        with open(report, "w") as fh:
            json.dump(stats, fh)
        return result

    module._seqanno = seqanno


def _more_cpu(cpu):
    from ost.bindings import hhblits3
    original = hhblits3.HHblits.BuildQueryMSA

    def build(self, nrdb, options={}, a3m_file=None, assign_ss=True):  # noqa: B006 (firma di OpenStructure)
        merged = dict(options)
        merged.setdefault("cpu", cpu)
        return original(self, nrdb, merged, a3m_file, assign_ss)

    hhblits3.HHblits.BuildQueryMSA = build


def main():
    prefix = os.environ.get("MAKPHI_UNICLUST_PREFIX", "")
    if not prefix or not os.path.exists(prefix + "_a3m.ffindex"):
        sys.stderr.write("MAKPHI_UNICLUST_PREFIX non indica un database valido: %r\n" % prefix)
        sys.exit(2)
    cpu = int(os.environ.get("MAKPHI_HHBLITS_CPU") or 1)
    if cpu > 1:
        _more_cpu(cpu)
    spec = importlib.util.spec_from_file_location("run_qmean", RUN_QMEAN)
    module = importlib.util.module_from_spec(spec)
    sys.argv[0] = RUN_QMEAN
    spec.loader.exec_module(module)
    module._get_uniclust30 = lambda: prefix
    folder = os.environ.get("MAKPHI_PROFILE_CACHE", "")
    if folder:
        _profile_cache(module, folder)
    module._main()


if __name__ == "__main__":
    main()
