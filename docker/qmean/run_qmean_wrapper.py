"""Esegue run_qmean.py con il database UniClust30/UniRef30 scelto da BioLoom.

run_qmean.py accetta solo una cartella /uniclust30 con esattamente un database completo di tutti e 6 i
file (compresi gli HMM). Qui il prefisso arriva da BIOLOOM_UNICLUST_PREFIX: così funzionano qualunque
versione, i database senza HMM (HHblits li calcola dagli allineamenti) e più versioni sullo stesso disco.
Con BIOLOOM_HHBLITS_CPU HHblits usa più processori (run_qmean.py ne chiede 1): il risultato non cambia.
Va avviato con l'interprete di OpenStructure: `ost run_qmean_wrapper.py <argomenti di run_qmean.py>`.
"""

import importlib.util
import os
import sys

RUN_QMEAN = "/qmean/run_qmean.py"


def _more_cpu(cpu):
    from ost.bindings import hhblits3
    original = hhblits3.HHblits.BuildQueryMSA

    def build(self, nrdb, options={}, a3m_file=None, assign_ss=True):
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
    module._main()


if __name__ == "__main__":
    main()
