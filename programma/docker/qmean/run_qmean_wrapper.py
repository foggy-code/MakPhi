"""Esegue run_qmean.py con il database UniClust30/UniRef30 scelto da BioLoom.

run_qmean.py accetta solo una cartella /uniclust30 con esattamente un database completo di tutti e 6 i
file (compresi gli HMM). Qui il prefisso arriva da BIOLOOM_UNICLUST_PREFIX: così funzionano qualunque
versione, i database senza HMM (HHblits li calcola dagli allineamenti) e più versioni sullo stesso disco.
Con BIOLOOM_HHBLITS_CPU HHblits usa più processori (run_qmean.py ne chiede 1): il risultato non cambia.
Con BIOLOOM_PROFILE_CACHE (cartella del database in uso) il profilo HHblits di ogni sequenza si conserva e una
sequenza già vista non rifà la ricerca: run_qmean.py usa da sé un a3m già presente nella sua cartella di lavoro
(`<md5 della sequenza>/hhblits.a3m`), e il profilo dipende solo da sequenza e database, quindi il risultato è lo
stesso. Profili riusati e calcolati vanno in `$TMPDIR/profile_cache.json`.
Va avviato con l'interprete di OpenStructure: `ost run_qmean_wrapper.py <argomenti di run_qmean.py>`.
"""

import importlib.util
import json
import os
import shutil
import sys

RUN_QMEAN = "/qmean/run_qmean.py"


def _profile_cache(module, folder):
    from ost.bindings import hhblits3
    original = module._seqanno
    stats = {"reused": 0, "computed": 0}
    report = os.path.join(os.environ.get("TMPDIR") or ".", "profile_cache.json")

    def seqanno(target_seq, workdir, *args, **kwargs):
        name = target_seq.GetName()  # md5 della sequenza (run_qmean._get_seq_name)
        cached = os.path.join(folder, name + ".a3m")
        a3m = os.path.join(workdir, name, hhblits3.HHblits.OUTPUT_PREFIX + ".a3m")
        reused = False
        if os.path.isfile(cached) and not os.path.exists(a3m):
            try:
                os.makedirs(os.path.dirname(a3m), exist_ok=True)
                shutil.copyfile(cached, a3m)
                os.utime(cached, None)  # usato ora: la pulizia toglie prima i meno recenti
                reused = True
            except OSError:
                pass
        result = original(target_seq, workdir, *args, **kwargs)
        if reused:
            stats["reused"] += 1
        else:
            stats["computed"] += 1
            if os.path.isfile(a3m) and not os.path.exists(cached):
                try:  # scrittura atomica: un altro calcolo contemporaneo può salvare lo stesso profilo
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
    prefix = os.environ.get("BIOLOOM_UNICLUST_PREFIX", "")
    if not prefix or not os.path.exists(prefix + "_a3m.ffindex"):
        sys.stderr.write("BIOLOOM_UNICLUST_PREFIX non indica un database valido: %r\n" % prefix)
        sys.exit(2)
    cpu = int(os.environ.get("BIOLOOM_HHBLITS_CPU") or 1)
    if cpu > 1:
        _more_cpu(cpu)
    spec = importlib.util.spec_from_file_location("run_qmean", RUN_QMEAN)
    module = importlib.util.module_from_spec(spec)
    sys.argv[0] = RUN_QMEAN
    spec.loader.exec_module(module)
    module._get_uniclust30 = lambda: prefix
    folder = os.environ.get("BIOLOOM_PROFILE_CACHE", "")
    if folder:
        _profile_cache(module, folder)
    module._main()


if __name__ == "__main__":
    main()
